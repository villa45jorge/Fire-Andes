# -*- coding: utf-8 -*-
"""
Modified on 30/06/2026
Version 7.0.0
@author: jvilla

Changes v7.0.0 (sobre v6.0.0):
    [MOD-5] Filtro a DOS zonas geograficas. Tras clasificar por 'region_geo' se
        conservan SOLO los puntos de region1 y region2 (configurables en __main__,
        ejemplos 'region1' / 'region2'). El resto (incl. 'sin_region') se descarta.
    [MOD-6] Nueva entrada: rasters de clase 3 (clase3_r{r}c{c}.tif) en el mismo
        directorio que la clase 12. Misma estructura (1 clase por raster, banda
        final = YEAR_MAX). AJUSTA el prefijo del archivo si difiere.
    [MOD-7] Calculo de area diferenciado por zona:
        - region1 -> area de clase 12 (columna 'cl12_m2')
        - region2 -> area de clase 3  (columna 'cl3_m2')
        Cada punto recibe SOLO una de las dos areas; la otra queda NaN.
    [refactor] get_mapbiomas_metadata() y calc_mapbiomas_area() ahora reciben el
        diccionario de tiles, para reutilizarse con clase 12 y clase 3. La
        exportacion cartografica AOI se extiende a ambas clases.

--- Historico ----------------------------------------------------------------
Changes v6.0.0 (sobre v5.4.2):
    [MOD-4] Buffer CUADRADO (pixel MODIS 1000 m) via shapely.box.
    [MOD-1] Sin filtro de altitud; dem_median como atributo; se descartan solo
        los buffers sin cobertura DEM (dem_median NaN).
    [MOD-2] Eliminado el bloque de zonas climaticas (Zone_Clima).
    [MOD-3] Clasificacion por region geografica (sjoin 'within' sobre el punto)
        -> columna 'region_geo'; fuera de zona = 'sin_region'.
    [fix] load_aoi: unary_union -> union_all.
Changes v5.4.2:
    [2] Acumulacion entre tiles (suma de areas, no promedio de %).
    [3] Salida en AREA (m2); columna cl12_m2 (ha = cl12_m2/10000).
    [4] Anclaje de banda a YEAR_MAX: band_idx = n_bandas - (YEAR_MAX - year).
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
AOI_PATH = None                              # e.g., data_dir / 'mi_aoi.shp'
AOI_BBOX = (-73, -14, -72, -13)              # conservado (genera capas recortadas)

# --- Parametros globales (analisis) ------------------------------------------
# [MOD-4] BUFFER_SIZE_M = SEMILADO del cuadrado (m). Lado = 2*500 = 1000 m (pixel MODIS).
BUFFER_SIZE_M   = 500
# Margen para expandir cada tile: la esquina del cuadrado se aleja sqrt(2)*lado/2.
BUFFER_SIZE_DEG = (BUFFER_SIZE_M * np.sqrt(2) * 1.05) / 111320.0
YEAR_MIN        = 2001
YEAR_MAX        = 2024
MAPBIOMAS_BANDS = YEAR_MAX - YEAR_MIN + 1   # 24

# [MOD-3] Campo del shapefile de regiones con el nombre de la zona.
REGION_FIELD = 'nombre'

# [MOD-6] Tiles MapBiomas: clase 12 y (nuevo) clase 3, en el mismo directorio.
# Si el prefijo real de la clase 3 difiere, ajustalo aqui.
MAPBIOMAS_TILES_C12 = {
    (r, c): mapbiomas_dir / f"clase12_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}
MAPBIOMAS_TILES_C3 = {
    (r, c): mapbiomas_dir / f"clase3_r{r}c{c}.tif"
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
    """Carga la geometria AOI desde AOI_PATH o AOI_BBOX (None si ambos None)."""
    if AOI_PATH is not None:
        aoi_gdf  = gpd.read_file(AOI_PATH).to_crs('EPSG:4326')
        aoi_geom = aoi_gdf.geometry.union_all()
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
    """Agrupa detecciones en clusters via BallTree + grafo."""
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
def get_mapbiomas_metadata(tiles_dict):
    """Pre-carga bounds, CRS, nodata y nro. de bandas de cada tile del set dado."""
    meta = {}
    for key, path in tiles_dict.items():
        if not path.exists():
            print(f"  [WARN] Tile MapBiomas no encontrado: {path.name}")
            continue
        with rasterio.open(path) as src:
            meta[key] = {
                'bounds_geom': box(*src.bounds),
                'crs'        : src.crs,
                'nodata'     : src.nodata,
                'bands'      : src.count,
            }
    return meta


# --- Area de una clase MapBiomas por anio ------------------------------------
def calc_mapbiomas_area(points_gdf, year, tile_expanded_geom, mb_meta,
                        tiles_dict, class_label='clase'):
    """
    Calcula el AREA [m2] de la clase contenida en 'tiles_dict' dentro de cada
    buffer. El raster contiene SOLO pixeles de esa clase (resto = fondo/nodata),
    asi que en zonal_stats:  count = pixeles de la clase ; nodata = fondo.

    Reutilizable para clase 12 o clase 3 segun el 'tiles_dict' / 'mb_meta' que
    se le pase.

    [2] Acumula el area entre tiles a caballo (no promedia).
    [4] Banda anclada al final: band_idx = n_bandas - (YEAR_MAX - year).
    Anios fuera de [YEAR_MIN, YEAR_MAX] -> NaN.
    Buffers con cobertura pero sin la clase -> 0.0.
    """
    n = len(points_gdf)
    if year < YEAR_MIN or year > YEAR_MAX:
        print(f"  [INFO] Anio {year} fuera del rango MapBiomas "
              f"({YEAR_MIN}-{YEAR_MAX}) -> {class_label} = NaN")
        return np.full(n, np.nan)

    bnds    = points_gdf.geometry.bounds
    lat_arr = (bnds['miny'].values + bnds['maxy'].values) / 2.0

    area_acc = np.zeros(n)
    seen     = np.zeros(n, dtype=bool)

    for (row, col), meta in mb_meta.items():

        if not tile_expanded_geom.intersects(meta['bounds_geom']):
            continue

        band_idx = meta['bands'] - (YEAR_MAX - year)
        if band_idx < 1 or band_idx > meta['bands']:
            print(f"  [WARN] band_idx={band_idx} fuera de rango "
                  f"(1-{meta['bands']}) tile ({row},{col}) anio {year} [{class_label}]")
            continue

        tile_path    = tiles_dict[(row, col)]
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

            px = abs(out_transform.a)
            py = abs(out_transform.e)
            if raster_crs is not None and raster_crs.is_geographic:
                m_per_deg_lat = 111320.0
                m_per_deg_lon = 111320.0 * np.cos(np.radians(lat_arr))
                pix_area = (px * m_per_deg_lon) * (py * m_per_deg_lat)
            else:
                pix_area = np.full(n, px * py)

            stats = zonal_stats(
                points_reproj, out_image[0], affine=out_transform,
                stats=['count', 'nodata'],
                nodata=nodata_class,
                all_touched=True
            )

            for i, s in enumerate(stats):
                n_cls = s.get('count')  or 0
                other = s.get('nodata') or 0
                if (n_cls + other) > 0:
                    area_acc[i] += n_cls * pix_area[i]
                    seen[i] = True

        except Exception as e:
            print(f"  [WARN] Error tile MB ({row},{col}) anio {year} "
                  f"[{class_label}]: {e}")
            continue

    areas = np.full(n, np.nan)
    areas[seen] = np.round(area_acc[seen], 2)
    return areas


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, mb_layers,
                             gdf_result, output_dir, base_name):
    """
    Recorta y guarda capas cartograficas limitadas a la AOI (solo visualizacion).

    mb_layers : dict {label: (tiles_dict, meta_dict)}  -> p.ej. {'c12': (...),
                'c3': (...)}. Se genera un .tif por tile que intersecta la AOI,
                con nombre {base_name}_aoi_mapbiomas_{label}_rXcY.tif.
    """
    if aoi_geom is None:
        return

    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'-'*58}")
    print("  Exportacion cartografica (AOI)")
    print(f"{'-'*58}")

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

    # -- MapBiomas tiles (clase 12 y clase 3) ---------------------------------
    n_mb = 0
    for label, (tiles_dict, meta_dict) in mb_layers.items():
        for (r, c), meta in meta_dict.items():
            if not aoi_geom.intersects(meta['bounds_geom']):
                continue
            mb_out = output_dir / f'{base_name}_aoi_mapbiomas_{label}_r{r}c{c}.tif'
            try:
                aoi_local = (
                    gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                    .to_crs(meta['crs']).geometry.iloc[0]
                    if meta['crs'] != WGS84 else aoi_geom
                )
                with rasterio.open(tiles_dict[(r, c)]) as src:
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
                print(f"  [OK] MapBiomas {label} r{r}c{c} -> {mb_out.name}")
            except Exception as e:
                print(f"  [WARN] MapBiomas {label} r{r}c{c} clip error: {e}")

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
def filt_csv(file_path, country_shape, DEM, regions_shape,
             region1, region2, output_path):
    """
    Procesa detecciones MODIS — Peru, dos zonas geograficas.

    region1 / region2 : nombres (str) de las dos zonas de 'nombre' a conservar.
        - region1 -> se calcula area de clase 12 (cl12_m2)
        - region2 -> se calcula area de clase 3  (cl3_m2)

    Columnas de salida:
      latitude, longitude, acq_date, acq_time, satellite, confidence,
      type, region_geo, gaul0_name, dem_median, year, cl12_m2, cl3_m2,
      cluster, date, geometry

    cl12_m2 : area de clase 12 (m2) en el buffer — solo puntos de region1 (NaN en region2).
    cl3_m2  : area de clase 3  (m2) en el buffer — solo puntos de region2 (NaN en region1).
              Hectareas = area / 10000.
    """
    try:
        t_total = time.time()
        print("=" * 58)
        print("  INICIO - AnomaliasTermicas_Peru v7.0.0")
        print(f"  Zonas conservadas: region1='{region1}' (clase12) | "
              f"region2='{region2}' (clase3)")
        print("=" * 58)

        # -- 0. Cargar AOI ----------------------------------------------------
        aoi_geom = load_aoi()

        # -- 1. Carga y filtros tematicos -------------------------------------
        t = time.time()
        df = pd.read_csv(file_path)
        df = df.sample(n=100000, random_state=54)  # 10% sample para test
        print(f"  Shape inicial CSV: {df.shape}")
        t = timer("Carga CSV", t)

        df = df.query(
            'confidence >= 80 and '
            '(`type` == 0 or `type` == 2) and '
            'latitude  <=  1  and latitude  >= -20 and '
            'longitude <= -60 and longitude >= -80'
        )[['latitude', 'longitude', 'acq_date', 'acq_time',
           'satellite', 'confidence', 'type']]
        print(f"  Shape tras filtros tematicos: {df.shape}")
        t = timer("Filtros tematicos", t)

        # -- 2. Join espacial -> Peru -----------------------------------------
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

        # -- 2b. [MOD-3] Clasificacion por region geografica (sobre el PUNTO) --
        regions = (gpd.read_file(regions_shape)[[REGION_FIELD, 'geometry']]
                   .to_crs('EPSG:4326'))
        gdf_peru = gpd.sjoin(gdf_peru, regions, how='left', predicate='within')
        gdf_peru = gdf_peru[~gdf_peru.index.duplicated(keep='first')]
        gdf_peru['region_geo'] = gdf_peru[REGION_FIELD].fillna('sin_region')
        _drop = ['index_right'] + ([REGION_FIELD]
                                   if REGION_FIELD != 'region_geo' else [])
        gdf_peru = (gdf_peru.drop(columns=_drop, errors='ignore')
                    .reset_index(drop=True))

        print("  Reparto por region geografica (todas las zonas):")
        for reg, cnt in gdf_peru['region_geo'].value_counts().items():
            print(f"    - {reg}: {cnt}")

        # -- 2c. [MOD-5] Filtro a SOLO dos zonas (region1, region2) -----------
        gdf_peru = (gdf_peru[gdf_peru['region_geo'].isin([region1, region2])]
                    .reset_index(drop=True))
        n1 = int((gdf_peru['region_geo'] == region1).sum())
        n2 = int((gdf_peru['region_geo'] == region2).sum())
        print(f"  Tras filtro a 2 zonas -> {len(gdf_peru)} puntos "
              f"(region1='{region1}': {n1} | region2='{region2}': {n2})")
        if len(gdf_peru) == 0:
            print("  [WARN] Ninguna de las dos zonas tiene puntos. "
                  "Revisa region1/region2 vs los valores reales de "
                  f"'{REGION_FIELD}'.")
            return None
        t = timer("Clasificacion + filtro a 2 zonas", t)

        # -- 3. [MOD-4] Buffer CUADRADO 1000 m (pixel MODIS) ------------------
        points_buffered = gdf_peru.copy()
        pts_3857 = gdf_peru.geometry.to_crs('EPSG:3857')
        squares = pts_3857.apply(
            lambda p: box(p.x - BUFFER_SIZE_M, p.y - BUFFER_SIZE_M,
                          p.x + BUFFER_SIZE_M, p.y + BUFFER_SIZE_M)
        )
        points_buffered['geometry'] = (
            gpd.GeoSeries(squares, crs='EPSG:3857').to_crs('EPSG:4326')
        )
        centroids = gdf_peru.geometry.reset_index(drop=True)
        print(f"  Buffer cuadrado {2*BUFFER_SIZE_M} m de lado generado. "
              f"Shape: {points_buffered.shape}")
        t = timer("Buffer cuadrado 1000 m", t)

        # -- 4. Pre-carga metadatos tiles MapBiomas (clase 12 y clase 3) ------
        mb_meta_c12 = get_mapbiomas_metadata(MAPBIOMAS_TILES_C12)
        mb_meta_c3  = get_mapbiomas_metadata(MAPBIOMAS_TILES_C3)
        print(f"  Tiles disponibles: clase12={len(mb_meta_c12)}/9 | "
              f"clase3={len(mb_meta_c3)}/9")

        # -- 5. Procesamiento por tiles ---------------------------------------
        tile_size = 1
        tiles = [
            box(x, y, x + tile_size, y + tile_size)
            for x in np.arange(-80, -60, tile_size)
            for y in np.arange(-20,   1, tile_size)
        ]
        print(f"  Tiles de procesamiento ({tile_size}x{tile_size} grados): {len(tiles)}")

        # region -> (columna destino, meta, tiles)
        region_calc = {
            region1: ('cl12_m2', mb_meta_c12, MAPBIOMAS_TILES_C12, 'clase12'),
            region2: ('cl3_m2',  mb_meta_c3,  MAPBIOMAS_TILES_C3,  'clase3'),
        }

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

                # -- b. [MOD-1] Solo se descartan buffers sin cobertura DEM ----
                stats_df = stats_df[
                    stats_df['dem_median'].notna()
                ].reset_index(drop=True)

                if len(stats_df) == 0:
                    skipped += 1
                    continue

                print(f"\n  Tile {t_idx+1:03d}: {len(stats_df)} puntos "
                      f"con DEM valido (NaN eliminados)")

                # -- c. [MOD-7] Area por zona: region1->clase12, region2->clase3
                stats_df['acq_date'] = pd.to_datetime(stats_df['acq_date'])
                stats_df['year']     = stats_df['acq_date'].dt.year.astype(int)
                stats_df['cl12_m2']  = np.nan
                stats_df['cl3_m2']   = np.nan

                for yr in sorted(stats_df['year'].unique()):
                    for reg, (col, meta_c, tiles_c, lbl) in region_calc.items():
                        mask = ((stats_df['year'] == yr) &
                                (stats_df['region_geo'] == reg))
                        if not mask.any():
                            continue
                        sub_gdf = stats_df.loc[mask].reset_index(drop=True)
                        area = calc_mapbiomas_area(
                            sub_gdf, yr, tile_exp, meta_c, tiles_c, lbl
                        )
                        stats_df.loc[mask, col] = area

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

        # -- 7. Exportar resultados completos ---------------------------------
        base_out  = Path(str(output_path)).with_suffix('')
        base_name = base_out.stem
        shp_path  = base_out.with_suffix('.shp')
        csv_path  = base_out.with_suffix('.csv')

        os.makedirs(shp_path.parent, exist_ok=True)

        gdf_result.to_file(shp_path)
        print(f"  [OK] Shapefile -> {shp_path.name}")
        gdf_result.drop(columns=['geometry']).to_csv(
            csv_path, index=False, encoding='utf-8-sig'
        )
        print(f"  [OK] CSV       -> {csv_path.name}")
        t = timer("Exportacion resultados", t)

        # -- 8. Exportacion cartografica recortada a AOI ----------------------
        save_cartographic_layers(
            aoi_geom   = aoi_geom,
            dem_path   = DEM,
            mb_layers  = {'c12': (MAPBIOMAS_TILES_C12, mb_meta_c12),
                          'c3' : (MAPBIOMAS_TILES_C3,  mb_meta_c3)},
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
        regions_shape = data_dir / 'region-geografica.shp',
        region1       = "Sierra",   
        region2       = "Selva",   
        output_path   = output_dir / 'AnomaliesThermiquesMB_Peru_V7_test10p.shp'
    )
