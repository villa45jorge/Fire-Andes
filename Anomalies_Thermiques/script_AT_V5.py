# -*- coding: utf-8 -*-
"""
Modified on 01/07/2026
Version 5.5.0
@author: jvilla

Changes v5.5.0
    Nettoye
Changes v5.4.1:
    [2c] pct_class12 root-cause fix (segunda correccion):
         nodata=None sobre array numpy tiene comportamiento indefinido entre
         versiones de rasterstats — devuelve count=None -> n_total=0 -> NaN.
         Solucion: una sola llamada stats=['count','nodata'] con nodata=nodata_class:
           count  = pixeles buffer con valor != nodata_class  (n12 = clase 12)
           nodata = pixeles buffer con valor == nodata_class  (n_other = otras clases)
           pct_class12 = n12 / (n12 + n_other) * 100
         Se añade all_touched=True para garantizar cobertura en buffers pequeños.
Changes v5.4.0:
    [AOI] AOI separada del analisis — actua solo en exportacion cartografica.
Changes v5.3.0:
    [AOI] Area of Interest added as global parameter.
Changes v5.2.0:
    [2b] pct_class12 fix: two separate zonal_stats calls (n12 / n_total * 100).
    [1]  Years outside MapBiomas range -> NaN.
    [3]  date_num removed from output.
    [4]  dem_mean removed.
"""

from pathlib import Path
import pandas as pd
import numpy as np
from rasterstats import zonal_stats
import geopandas as gpd
from shapely.geometry import box, shape
import os
from tqdm import tqdm
from datetime import timedelta
import time
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.crs import CRS
import gc
from sklearn.neighbors import BallTree
import networkx as nx

# --- Rutas -------------------------------------------------------------------
base_dir      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML")
data_dir      = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir    = base_dir / "3_output"
mapbiomas_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML/3_output")

# --- Area de Interes (AOI) — solo para exportacion cartografica --------------
# El analisis cubre siempre todo Peru. La AOI se usa unicamente para recortar
# los rasters (DEM, MapBiomas) y el shapefile/CSV resultado al final.
#
# AOI_PATH: Path a un .shp / .gpkg (varios features -> se unen automaticamente)
# AOI_BBOX: (W, S, E, N) en EPSG:4326
# Si ambos son None no se genera ninguna exportacion cartografica.
# Si los dos estan definidos, AOI_PATH tiene prioridad.
#
AOI_PATH = None                              # e.g., data_dir / 'mi_aoi.shp'
AOI_BBOX = (-73, -14, -72, -13)                              # e.g., (-77.5, -16.0, -73.0, -10.0)

# --- Parametros globales (analisis) ------------------------------------------
BUFFER_SIZE_M   = 500
BUFFER_SIZE_DEG = 0.005
ALT_THRESHOLD   = 2000
YEAR_MIN        = 2001
YEAR_MAX        = 2024
MAPBIOMAS_BANDS = YEAR_MAX - YEAR_MIN + 1   # 24

MAPBIOMAS_TILES = {
    (r, c): mapbiomas_dir / f"clase12_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}

SPATIAL_KM    = 1.0
TEMPORAL_DAYS = 15
WGS84         = CRS.from_epsg(4326)

# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


# --- AOI: carga --------------------------------------------------------------
def load_aoi():
    """
    Carga la geometria AOI desde AOI_PATH o AOI_BBOX.
    Retorna None si ambos son None (sin exportacion cartografica).
    """
    if AOI_PATH is not None:
        aoi_gdf  = gpd.read_file(AOI_PATH).to_crs('EPSG:4326')
        aoi_geom = aoi_gdf.geometry.unary_union
        print(f"  AOI cartografica : {Path(AOI_PATH).name} "
              f"({len(aoi_gdf)} feature(s))")
        return aoi_geom

    if AOI_BBOX is not None:
        w, s, e, n = AOI_BBOX
        aoi_geom = box(w, s, e, n)
        print(f"  AOI cartografica : bbox W={w} S={s} E={e} N={n}")
        return aoi_geom

    print("  AOI cartografica : None — no se generan capas recortadas")
    return None


# --- Clustering espacio-temporal ---------------------------------------------
def cluster_spatiotemporal(df, spatial_km, temporal_days):
    """
    Agrupa detecciones en clusters via BallTree + grafo.
    date_num es variable local — no se escribe en el DataFrame.
    """
    df['date'] = pd.to_datetime(df['acq_date'])
    df = df.sort_values('date').reset_index(drop=True)

    date_nums  = (df['date'] - df['date'].min()).dt.days.values
    coords_rad = np.radians(df[['latitude', 'longitude']].values)
    tree       = BallTree(coords_rad, metric='haversine')
    radius_rad = spatial_km / 6371.0
    indices    = tree.query_radius(coords_rad, r=radius_rad)

    G = nx.Graph()
    G.add_nodes_from(range(len(df)))
    for i, neighbors in enumerate(indices):
        for j in neighbors:
            if j > i and abs(date_nums[i] - date_nums[j]) <= temporal_days:
                G.add_edge(i, j)

    clusters = np.full(len(df), -1, dtype=int)
    for cid, component in enumerate(nx.connected_components(G)):
        for idx in component:
            clusters[idx] = cid

    df['cluster'] = clusters
    return df


# --- Metadatos de tiles MapBiomas --------------------------------------------
def get_mapbiomas_metadata():
    """Pre-carga bounds, CRS y nodata de cada tile MapBiomas."""
    meta = {}
    for key, path in MAPBIOMAS_TILES.items():
        if not path.exists():
            print(f"  [WARN] Tile MapBiomas no encontrado: {path.name}")
            continue
        with rasterio.open(path) as src:
            meta[key] = {
                'bounds_geom': box(*src.bounds),
                'crs'        : src.crs,
                'nodata'     : src.nodata,
            }
    return meta


# --- Proporcion clase 12 por anio --------------------------------------------
def calc_mapbiomas_proportions(points_gdf, year, tile_expanded_geom, mb_meta):
    """
    Calcula pct_class12 [0.0-100.0] para cada buffer.

    Dos llamadas separadas a zonal_stats:
      Call 1  nodata=nodata_class  -> n12     (pixeles clase 12)
      Call 2  nodata=None          -> n_total (TODOS los pixeles del buffer)
      pct = n12 / n_total * 100

    Anios fuera de [2001-2024] retornan NaN.
    """
    if year < YEAR_MIN or year > YEAR_MAX:
        print(f"  [INFO] Anio {year} fuera del rango MapBiomas "
              f"({YEAR_MIN}-{YEAR_MAX}) -> pct_class12 = NaN")
        return np.full(len(points_gdf), np.nan)

    band_idx    = year - YEAR_MIN + 1
    n           = len(points_gdf)
    proportions = np.full(n, np.nan)

    for (row, col), meta in mb_meta.items():

        if not tile_expanded_geom.intersects(meta['bounds_geom']):
            continue

        tile_path    = MAPBIOMAS_TILES[(row, col)]
        nodata_class = meta['nodata'] if meta['nodata'] is not None else 0

        try:
            with rasterio.open(tile_path) as src:
                raster_crs = src.crs

                if raster_crs != WGS84:
                    mask_gdf = gpd.GeoDataFrame(
                        [0], geometry=[tile_expanded_geom], crs=WGS84
                    ).to_crs(raster_crs)
                    mask_geom     = mask_gdf.geometry.iloc[0]
                    points_reproj = points_gdf.to_crs(raster_crs)
                else:
                    mask_geom     = tile_expanded_geom
                    points_reproj = points_gdf

                out_image, out_transform = rio_mask(
                    src, [mask_geom], crop=True, all_touched=True,
                    indexes=[band_idx]
                )

                # Una sola llamada con dos estadisticas complementarias:
                #   count  = pixeles dentro del buffer con valor != nodata_class
                #            = pixeles clase 12  (n12)
                #   nodata = pixeles dentro del buffer con valor == nodata_class
                #            = pixeles de otras clases  (n_other)
                #   n_total = count + nodata  (sin usar nodata=None)
                #
                # Evita el comportamiento indefinido de nodata=None sobre arrays
                # numpy en distintas versiones de rasterstats.
                stats = zonal_stats(
                    points_reproj, out_image[0], affine=out_transform,
                    stats=['count', 'nodata'],
                    nodata=nodata_class,
                    all_touched=True
                )

                for i, s in enumerate(stats):
                    n12     = s.get('count') or 0
                    n_other = s.get('nodata') or 0
                    n_total = n12 + n_other
                    if n_total > 0:
                        v = round(float(n12) / float(n_total) * 100.0, 2)
                        proportions[i] = (
                            v if np.isnan(proportions[i])
                            else (proportions[i] + v) / 2.0
                        )

        except Exception as e:
            print(f"  [WARN] Error tile MB ({row},{col}) anio {year}: {e}")
            continue

    return proportions


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, mb_meta,
                              gdf_result, output_dir, base_name):
    """
    Recorta y guarda capas cartograficas limitadas a la AOI.
    El analisis no se ve afectado — esta funcion solo produce visualizaciones.

    Archivos generados (todos en output_dir con prefijo base_name):
      {base_name}_aoi_dem.tif              DEM recortado
      {base_name}_aoi_mapbiomas_rXcY.tif   Un archivo por tile que intersecta AOI
      {base_name}_aoi_results.shp          Puntos resultado dentro de la AOI
      {base_name}_aoi_results.csv          Idem en CSV

    Parameters
    ----------
    aoi_geom   : shapely.geometry en EPSG:4326
    dem_path   : Path al raster DEM original
    mb_meta    : dict de metadatos MapBiomas
    gdf_result : GeoDataFrame con los resultados finales del analisis
    output_dir : Path directorio de salida
    base_name  : str prefijo comun de archivos
    """
    if aoi_geom is None:
        return

    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'─'*58}")
    print("  Exportacion cartografica (AOI)")
    print(f"{'─'*58}")

    # -- DEM ------------------------------------------------------------------
    dem_out = output_dir / f'{base_name}_aoi_dem.tif'
    try:
        with rasterio.open(dem_path) as src:
            aoi_local = (
                gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                .to_crs(src.crs).geometry.iloc[0]
                if src.crs != WGS84 else aoi_geom
            )
            out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                       all_touched=True)
            out_meta = {**src.meta,
                        'driver': 'GTiff', 'compress': 'lzw',
                        'height': out_img.shape[1],
                        'width' : out_img.shape[2],
                        'transform': out_tr}
        with rasterio.open(dem_out, 'w', **out_meta) as dst:
            dst.write(out_img)
        print(f"  [OK] DEM              -> {dem_out.name}")
    except Exception as e:
        print(f"  [WARN] DEM clip error: {e}")

    # -- MapBiomas tiles ------------------------------------------------------
    n_mb = 0
    for (r, c), meta in mb_meta.items():
        if not aoi_geom.intersects(meta['bounds_geom']):
            continue
        mb_out = output_dir / f'{base_name}_aoi_mapbiomas_r{r}c{c}.tif'
        try:
            aoi_local = (
                gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                .to_crs(meta['crs']).geometry.iloc[0]
                if meta['crs'] != WGS84 else aoi_geom
            )
            with rasterio.open(MAPBIOMAS_TILES[(r, c)]) as src:
                out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                           all_touched=True)
                out_meta = {**src.meta,
                            'driver': 'GTiff', 'compress': 'lzw',
                            'height': out_img.shape[1],
                            'width' : out_img.shape[2],
                            'transform': out_tr}
            with rasterio.open(mb_out, 'w', **out_meta) as dst:
                dst.write(out_img)
            n_mb += 1
            print(f"  [OK] MapBiomas r{r}c{c}   -> {mb_out.name}")
        except Exception as e:
            print(f"  [WARN] MapBiomas r{r}c{c} clip error: {e}")

    # -- Shapefile y CSV resultado filtrado a AOI -----------------------------
    mask_aoi = gdf_result.geometry.within(aoi_geom)
    gdf_aoi  = gdf_result[mask_aoi].reset_index(drop=True)
    n_pts    = len(gdf_aoi)

    shp_aoi = output_dir / f'{base_name}_aoi_results.shp'
    csv_aoi = output_dir / f'{base_name}_aoi_results.csv'

    if n_pts > 0:
        gdf_aoi.to_file(shp_aoi)
        gdf_aoi.drop(columns=['geometry']).to_csv(csv_aoi, index=False,
                                                   encoding='utf-8-sig')
        print(f"  [OK] Resultados AOI   -> {shp_aoi.name} ({n_pts} puntos)")
    else:
        print("  [INFO] Ningun punto resultado cae dentro de la AOI.")

    timer(f"Exportacion cartografica (DEM + {n_mb} tiles MB + resultados)", t)


# --- Funcion principal -------------------------------------------------------
def filt_csv(file_path, country_shape, DEM, output_path):
    """
    Filtra y procesa detecciones MODIS — todo Peru, solo >2000 m.
    La AOI (AOI_PATH / AOI_BBOX) no afecta al analisis: se usa unicamente
    en el paso final de exportacion cartografica.

    Columnas de salida:
      latitude, longitude, acq_date, acq_time, satellite, confidence,
      type, Zone_Clima, gaul0_name, dem_median, year, pct_class12,
      cluster, date, geometry
    """
    try:
        t_total = time.time()
        print("=" * 58)
        print("  INICIO - AnomalíasTermicas_Peru v5.4.1")
        print("=" * 58)

        # -- 0. Cargar AOI (solo para exportacion final) ----------------------
        aoi_geom = load_aoi()

        # -- 1. Carga y filtros tematicos -------------------------------------
        t = time.time()
        df = pd.read_csv(file_path)
        #df = df.sample(n=250000, random_state=54)  # 25% sample para test
        print(f"  Shape inicial CSV: {df.shape}")
        t = timer("Carga CSV", t)

        # Bbox fijo Andes Tropicales — no depende de la AOI
        df = df.query(
            'confidence >= 80 and '
            '(`type` == 0 or `type` == 2) and '
            'latitude  <=  1  and latitude  >= -20 and '
            'longitude <= -60 and longitude >= -80'
        )[['latitude', 'longitude', 'acq_date', 'acq_time',
           'satellite', 'confidence', 'type']]

        conditions = [
            (df["latitude"] >= -5) & (df["latitude"] <=  1),
            (df["latitude"] >= -8) & (df["latitude"] <  -5),
            (df["latitude"] <  -8) & (df["latitude"] >= -20),
        ]
        choices = ["Zone_Equatorial", "Transition_Zone", "South_Zone"]
        df["Zone_Clima"] = np.select(conditions, choices, default="Not_Specified")
        print(f"  Shape tras filtros tematicos: {df.shape}")
        t = timer("Filtros tematicos", t)

        # -- 2. Join espacial -> todo Peru (sin filtro AOI) -------------------
        countries = gpd.read_file(country_shape)[['gaul0_name', 'geometry']]

        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df.longitude, df.latitude),
            crs='EPSG:4326'
        )
        gdf_join = gpd.sjoin(gdf, countries, how='left', predicate='within')

        gdf_peru = (gdf_join[gdf_join['gaul0_name'] == 'Peru']
                    .drop(columns=['index_right'], errors='ignore')
                    .reset_index(drop=True))
        print(f"  Shape tras filtro Peru: {gdf_peru.shape}")
        t = timer("Join espacial + filtro Peru", t)

        # -- 3. Buffer circular 500 m -----------------------------------------
        points_buffered = gdf_peru.copy()
        points_buffered['geometry'] = (
            gdf_peru.geometry
            .to_crs('EPSG:3857')
            .buffer(BUFFER_SIZE_M)
            .to_crs('EPSG:4326')
        )
        centroids = gdf_peru.geometry.reset_index(drop=True)
        print(f"  Buffer {BUFFER_SIZE_M} m generado. Shape: {points_buffered.shape}")
        t = timer("Buffer 500 m", t)

        # -- 4. Pre-carga metadatos tiles MapBiomas ---------------------------
        mb_meta = get_mapbiomas_metadata()
        print(f"  Tiles MapBiomas disponibles: {len(mb_meta)}/9")

        # -- 5. Procesamiento por tiles (grid completo de los Andes) ----------
        tile_size = 1
        tiles = [
            box(x, y, x + tile_size, y + tile_size)
            for x in np.arange(-80, -60, tile_size)
            for y in np.arange(-20,   1, tile_size)
        ]
        print(f"  Tiles de procesamiento ({tile_size}x{tile_size} grados): {len(tiles)}")

        all_results = []
        skipped = 0

        with rasterio.open(DEM) as dem_src:

            for t_idx, tile_geom in enumerate(tqdm(tiles, desc="Procesando tiles")):
                t_tile = time.time()

                mask_tile = centroids.within(tile_geom)
                if mask_tile.sum() == 0:
                    skipped += 1
                    continue

                points_in_tile = points_buffered[mask_tile.values].reset_index(drop=True)

                minx, miny, maxx, maxy = tile_geom.bounds
                tile_exp = box(
                    minx - BUFFER_SIZE_DEG, miny - BUFFER_SIZE_DEG,
                    maxx + BUFFER_SIZE_DEG, maxy + BUFFER_SIZE_DEG
                )

                # -- a. DEM: solo median --------------------------------------
                try:
                    dem_img, dem_tr = rio_mask(
                        dem_src, [tile_exp], crop=True, all_touched=True
                    )
                except Exception as e:
                    print(f"\n  [WARN] Tile {t_idx+1} error DEM mask: {e}")
                    skipped += 1
                    continue

                dem_stats = zonal_stats(
                    points_in_tile, dem_img[0], affine=dem_tr,
                    stats=['median'], prefix='dem_', geojson_out=True,
                    nodata=dem_src.nodata if dem_src.nodata is not None else -9999
                )

                stats_df = gpd.GeoDataFrame(
                    [f['properties'] for f in dem_stats],
                    geometry=[shape(f['geometry']) for f in dem_stats],
                    crs=points_buffered.crs
                ).reset_index(drop=True)

                del dem_img, dem_tr, dem_stats
                gc.collect()

                # -- b. Filtro altitudinal ------------------------------------
                stats_df = stats_df[
                    stats_df['dem_median'] > ALT_THRESHOLD
                ].reset_index(drop=True)

                if len(stats_df) == 0:
                    skipped += 1
                    continue

                print(f"\n  Tile {t_idx+1:03d}: {len(stats_df)} puntos "
                      f"tras filtro DEM (>{ALT_THRESHOLD} m)")

                # -- c. MapBiomas: pct_class12 por anio -----------------------
                stats_df['acq_date']    = pd.to_datetime(stats_df['acq_date'])
                stats_df['year']        = stats_df['acq_date'].dt.year.astype(int)
                stats_df['pct_class12'] = np.nan

                for yr in sorted(stats_df['year'].unique()):
                    mask_yr = stats_df['year'] == yr
                    sub_gdf = stats_df.loc[mask_yr].reset_index(drop=True)
                    props   = calc_mapbiomas_proportions(
                                  sub_gdf, yr, tile_exp, mb_meta
                              )
                    stats_df.loc[mask_yr, 'pct_class12'] = props

                all_results.append(stats_df.copy())
                del stats_df, points_in_tile
                gc.collect()

                print(f"  [OK] Tile {t_idx+1:03d}/{len(tiles)} "
                      f"- {time.time()-t_tile:.1f}s")

        t = timer("Procesamiento por tiles (DEM + MapBiomas)", t)
        print(f"  Tiles sin datos / saltados: {skipped}")

        if not all_results:
            print("  [WARN] Sin resultados.")
            return None

        # -- 6. Clustering espacio-temporal -----------------------------------
        final_df  = pd.concat(all_results).reset_index(drop=True)
        final_gdf = gpd.GeoDataFrame(final_df, geometry='geometry', crs='EPSG:4326')
        print(f"  Shape antes del clustering: {final_gdf.shape}")

        gdf_cluster = cluster_spatiotemporal(
            pd.DataFrame(final_gdf), SPATIAL_KM, TEMPORAL_DAYS
        )
        gdf_cluster = (gdf_cluster
                       .sort_values('date')
                       .groupby('cluster')
                       .first()
                       .reset_index())
        gdf_cluster = gdf_cluster.drop(columns=['geometry'], errors='ignore')
        gdf_cluster['geometry'] = gpd.points_from_xy(
            gdf_cluster['longitude'], gdf_cluster['latitude']
        )
        gdf_result = gpd.GeoDataFrame(gdf_cluster, geometry='geometry',
                                      crs='EPSG:4326')
        t = timer("Clustering espacio-temporal", t)
        print(f"  Clusters unicos (primer evento): {len(gdf_result)}")

        # -- 7. Exportar resultados completos (todo Peru) ---------------------
        base_out  = Path(str(output_path)).with_suffix('')
        base_name = base_out.stem
        shp_path  = base_out.with_suffix('.shp')
        csv_path  = base_out.with_suffix('.csv')

        os.makedirs(shp_path.parent, exist_ok=True)

        gdf_result.to_file(shp_path)
        print(f"  [OK] Shapefile (Peru completo) -> {shp_path.name}")

        gdf_result.drop(columns=['geometry']).to_csv(
            csv_path, index=False, encoding='utf-8-sig'
        )
        print(f"  [OK] CSV       (Peru completo) -> {csv_path.name}")
        t = timer("Exportacion resultados", t)

        # -- 8. Exportacion cartografica recortada a AOI ----------------------
        # El analisis no se ve afectado — este paso es exclusivamente visual
        save_cartographic_layers(
            aoi_geom   = aoi_geom,
            dem_path   = DEM,
            mb_meta    = mb_meta,
            gdf_result = gdf_result,
            output_dir = output_dir,
            base_name  = base_name,
        )

        print(f"\n{'='*58}")
        print(f"  TOTAL : {timedelta(seconds=int(time.time() - t_total))}")
        print(f"  FILAS : {len(gdf_result)}")
        print(f"{'='*58}")
        return gdf_result

    except Exception as e:
        import traceback
        print(f"\n  [ERROR] {e}")
        traceback.print_exc()
        return None


# --- Ejecucion ---------------------------------------------------------------
if __name__ == "__main__":
    gdf_result = filt_csv(
        file_path     = data_dir / 'fire_archive_M-C61_706555.csv',
        country_shape = data_dir / 'GAUL_2024_L1.shp',
        DEM           = data_dir / 'mosaico_andes_DEM.tif',
        output_path   = output_dir / 'AnomaliesThermiquesMB_Peru_V5.shp'
    )