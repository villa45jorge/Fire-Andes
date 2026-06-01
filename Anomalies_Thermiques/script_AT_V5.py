# -*- coding: utf-8 -*-
"""
Modified on 28/05/2026
Version 5.0.0
@author: jvilla

Changes v5.0.0:
    - Country filter: Peru ONLY (after spatial join)
    - DEM zonal stats + altitude filter (>2000 m) applied BEFORE MapBiomas
    - WorldClim replaced by MapBiomas clase12 proportion:
        · 9 tiles (3x3 grid), each with 24 bands (2001-2024)
        · Band selected = year of the fire detection
        · Metric = (pixels == 12) / total valid pixels in buffer
    - Dual output: Shapefile + CSV
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
#test_dir    = base_dir / "4_test"

# MapBiomas tiles: directorio independiente (3_output, sin 's')
mapbiomas_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML/3_output")

# --- Parametros globales -----------------------------------------------------
BUFFER_SIZE_M   = 500        # metros - radio del buffer circular
BUFFER_SIZE_DEG = 0.005      # ~500 m en grados - expansion lateral de los tiles
ALT_THRESHOLD   = 2000       # metros - umbral de filtro altitudinal
YEAR_MIN        = 2001       # primera banda MapBiomas
YEAR_MAX        = 2024       # ultima banda MapBiomas
MAPBIOMAS_BANDS = YEAR_MAX - YEAR_MIN + 1   # 24 bandas
SPATIAL_KM      = 1.0        # radio de clustering espacial (km)
TEMPORAL_DAYS   = 15         # ventana temporal de clustering (dias)

# Grid 3x3 de tiles MapBiomas
MAPBIOMAS_TILES = {
    (r, c): mapbiomas_dir / f"clase12_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}

WGS84 = CRS.from_epsg(4326)

# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


def prop_class12(x):
    """
    Estadistica personalizada para rasterstats.

    Recibe un masked array (valores nodata ya enmascarados) y devuelve:
        proporcion = count(pixeles == 12) / count(pixeles validos)

    Si no hay pixeles validos en el buffer -> nan.
    """
    valid = x.compressed()   # elimina valores enmascarados (nodata)
    if len(valid) == 0:
        return np.nan
    return float(np.sum(valid == 12)) / float(len(valid))


def cluster_spatiotemporal(df, spatial_km, temporal_days):
    """
    Agrupa detecciones en clusters espacio-temporales.

    Algoritmo:
      1. BallTree Haversine para vecindad espacial
      2. Grafo: arista si vecino espacial Y diferencia temporal <= temporal_days
      3. Componentes conectados = clusters
    """
    df['date'] = pd.to_datetime(df['acq_date'])
    df = df.sort_values('date').reset_index(drop=True)
    df['date_num'] = (df['date'] - df['date'].min()).dt.days

    coords_rad = np.radians(df[['latitude', 'longitude']].values)
    tree = BallTree(coords_rad, metric='haversine')
    radius_rad = spatial_km / 6371.0

    indices = tree.query_radius(coords_rad, r=radius_rad)
    G = nx.Graph()
    G.add_nodes_from(range(len(df)))
    dates = df['date_num'].values

    for i, neighbors in enumerate(indices):
        for j in neighbors:
            if j > i and abs(dates[i] - dates[j]) <= temporal_days:
                G.add_edge(i, j)

    clusters = np.full(len(df), -1, dtype=int)
    for cid, component in enumerate(nx.connected_components(G)):
        for idx in component:
            clusters[idx] = cid

    df['cluster'] = clusters
    return df


# --- Metadatos de tiles MapBiomas --------------------------------------------
def get_mapbiomas_metadata():
    """
    Pre-carga bounds y CRS de cada tile MapBiomas para
    interseccion rapida durante el bucle principal.

    Returns
    -------
    dict {(row, col): {'bounds_geom': Polygon, 'crs': CRS, 'nodata': float}}
    """
    meta = {}
    for key, path in MAPBIOMAS_TILES.items():
        if not path.exists():
            print(f"  [WARN] Tile MapBiomas no encontrado: {path.name}")
            continue
        with rasterio.open(path) as src:
            meta[key] = {
                'bounds_geom': box(*src.bounds),
                'crs'        : src.crs,
                'nodata'     : src.nodata if src.nodata is not None else 0,
            }
    return meta


# --- Proporcion clase 12 por anio --------------------------------------------
def calc_mapbiomas_proportions(points_gdf, year, tile_expanded_geom, mb_meta):
    """
    Calcula la proporcion de clase 12 dentro de cada buffer para un anio dado.

    Logica:
      - Selecciona la banda MapBiomas = year - 2001 + 1  (1-indexed)
      - Para cada tile MB que se solape con tile_expanded_geom:
          * Enmascara el raster a la zona del tile de procesamiento
          * Llama a zonal_stats con la funcion prop_class12
          * Combina resultados (promedio simple en zonas de solape entre tiles MB)

    Parameters
    ----------
    points_gdf         : GeoDataFrame con geometrias buffer (EPSG:4326)
    year               : int - anio del fuego (determina la banda)
    tile_expanded_geom : shapely.Polygon - tile de procesamiento expandido
    mb_meta            : dict con metadatos de tiles MapBiomas

    Returns
    -------
    np.ndarray de float, longitud = len(points_gdf)
    """
    # Seleccion de banda: clamp al rango valido si el anio esta fuera
    band_idx = int(np.clip(year - YEAR_MIN + 1, 1, MAPBIOMAS_BANDS))
    n = len(points_gdf)
    proportions = np.full(n, np.nan)

    for (row, col), meta in mb_meta.items():

        # Saltar tiles que no se solapen con la zona de procesamiento
        if not tile_expanded_geom.intersects(meta['bounds_geom']):
            continue

        tile_path = MAPBIOMAS_TILES[(row, col)]
        try:
            with rasterio.open(tile_path) as src:
                raster_crs = src.crs
                nodata_val = meta['nodata']

                # Reproyectar geometrias si el raster no esta en EPSG:4326
                if raster_crs != WGS84:
                    mask_gdf = gpd.GeoDataFrame(
                        [0], geometry=[tile_expanded_geom], crs=WGS84
                    ).to_crs(raster_crs)
                    mask_geom     = mask_gdf.geometry.iloc[0]
                    points_reproj = points_gdf.to_crs(raster_crs)
                else:
                    mask_geom     = tile_expanded_geom
                    points_reproj = points_gdf

                # Recortar el raster a la zona del tile de procesamiento
                out_image, out_transform = rio_mask(
                    src,
                    [mask_geom],
                    crop=True,
                    all_touched=True,
                    indexes=[band_idx]   # solo la banda del anio correspondiente
                )

                # Zonal stats: proporcion de clase 12 por buffer
                stats = zonal_stats(
                    points_reproj,
                    out_image[0],
                    affine=out_transform,
                    stats=[],
                    add_stats={'prop12': prop_class12},
                    nodata=nodata_val
                )

                # Acumular resultados
                # (promedio simple en caso de solapamiento entre tiles MB)
                for i, s in enumerate(stats):
                    v = s.get('prop12')
                    if v is not None and not (isinstance(v, float) and np.isnan(v)):
                        v = float(v)
                        proportions[i] = (
                            v if np.isnan(proportions[i])
                            else (proportions[i] + v) / 2.0
                        )

        except Exception as e:
            print(f"  [WARN] Error tile MB ({row},{col}) anio {year}: {e}")
            continue

    return proportions


# --- Funcion principal -------------------------------------------------------
def filt_csv(file_path, country_shape, DEM, output_path):
    """
    Filtra y procesa detecciones MODIS - solo Peru, solo >2000 m.

    Flujo de datos:
      CSV MODIS
        -> filtros tematicos + zona climatica
        -> join espacial con paises -> conserva solo PERU
        -> buffer circular 500 m por punto
        -> por tile 1x1 grados:
            a. zonal_stats DEM (mean, median)
            b. filtro altitudinal dem_median > 2000 m
            c. MapBiomas clase12 -> proporcion por anio del fuego
        -> clustering espacio-temporal (1 km / 15 dias, primer evento)
        -> Shapefile + CSV

    Parameters
    ----------
    file_path     : Path - CSV MODIS de entrada
    country_shape : Path - shapefile GAUL paises
    DEM           : Path - raster DEM mosaico Andes
    output_path   : Path - ruta de salida (se generan .shp y .csv) 

    """
    try:
        t_total = time.time()
        print("=" * 58)
        print("  INICIO - AnomalíasTermicas_Peru v5.0.0")
        print("=" * 58)

        # -- 1. Carga y filtros tematicos -------------------------------------
        t = time.time()
        df = pd.read_csv(file_path)
        #df =df.sample(n=250000, random_state=54) #25% sample

        print(f"  Shape inicial CSV: {df.shape}")
        t = timer("Carga CSV", t)

        # Filtros de confianza, tipo, y bounding box Andes Tropicales
        df = df.query(
            'confidence >= 80 and '
            '(`type` == 0 or `type` == 2) and '
            'latitude  <=  1  and latitude  >= -20 and '
            'longitude <= -60 and longitude >= -80'
        )[['latitude', 'longitude', 'acq_date', 'acq_time',
           'satellite', 'confidence', 'type']]

        # Zona climatica latitudinal
        conditions = [
            (df["latitude"] >= -5) & (df["latitude"] <=  1),
            (df["latitude"] >= -8) & (df["latitude"] <  -5),
            (df["latitude"] <  -8) & (df["latitude"] >= -20),
        ]
        choices = ["Zone_Equatorial", "Transition_Zone", "South_Zone"]
        df["Zone_Clima"] = np.select(conditions, choices, default="Not Specified")
        print(f"  Shape tras filtros tematicos: {df.shape}")
        t = timer("Filtros tematicos", t)

        # -- 2. Join espacial -> solo PERU ------------------------------------
        countries = gpd.read_file(country_shape)[['gaul0_name', 'geometry']]

        gdf = gpd.GeoDataFrame(
            df,
            geometry=gpd.points_from_xy(df.longitude, df.latitude),
            crs='EPSG:4326'
        )
        gdf_join = gpd.sjoin(gdf, countries, how='left', predicate='within')

        # CAMBIO v5: filtrar solo PERU (antes excluia Brasil)
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
        # Guardar centroides originales para la interseccion con tiles
        centroids = gdf_peru.geometry.reset_index(drop=True)
        print(f"  Buffer {BUFFER_SIZE_M} m generado. Shape: {points_buffered.shape}")
        t = timer("Buffer 500 m", t)

        # -- 4. Pre-carga metadatos tiles MapBiomas ---------------------------
        mb_meta = get_mapbiomas_metadata()
        print(f"  Tiles MapBiomas disponibles: {len(mb_meta)}/9")

        # -- 5. Procesamiento por tiles ---------------------------------------
        tile_size = 1
        x_min, y_min, x_max, y_max = -80, -20, -60, 1
        tiles = [
            box(x, y, x + tile_size, y + tile_size)
            for x in np.arange(x_min, x_max, tile_size)
            for y in np.arange(y_min, y_max, tile_size)
        ]
        print(f"  Tiles de procesamiento ({tile_size}x{tile_size} grados): {len(tiles)}")

        all_results = []
        skipped = 0

        with rasterio.open(DEM) as dem_src:

            for t_idx, tile_geom in enumerate(tqdm(tiles, desc="Procesando tiles")):
                t_tile = time.time()

                # Seleccionar puntos cuyo centroide cae en este tile
                mask_tile = centroids.within(tile_geom)
                if mask_tile.sum() == 0:
                    skipped += 1
                    continue

                points_in_tile = points_buffered[mask_tile.values].reset_index(drop=True)

                # Expandir el tile para capturar buffers en los bordes
                minx, miny, maxx, maxy = tile_geom.bounds
                tile_exp = box(
                    minx - BUFFER_SIZE_DEG, miny - BUFFER_SIZE_DEG,
                    maxx + BUFFER_SIZE_DEG, maxy + BUFFER_SIZE_DEG
                )

                # -- a. DEM: zonal stats (mean + median) ---------------------
                # CAMBIO v5: DEM se procesa PRIMERO, antes de MapBiomas
                try:
                    dem_img, dem_tr = rio_mask(
                        dem_src, [tile_exp], crop=True, all_touched=True
                    )
                except Exception as e:
                    print(f"\n  [WARN] Tile {t_idx+1} error DEM mask: {e}")
                    skipped += 1
                    continue

                dem_stats = zonal_stats(
                    points_in_tile,
                    dem_img[0],
                    affine=dem_tr,
                    stats=['mean', 'median'],
                    prefix='dem_',
                    geojson_out=True,      # preserva todas las columnas originales
                    nodata=dem_src.nodata if dem_src.nodata is not None else -9999
                )

                # Reconstruir GeoDataFrame con propiedades + geometria buffer
                stats_df = gpd.GeoDataFrame(
                    [f['properties'] for f in dem_stats],
                    geometry=[shape(f['geometry']) for f in dem_stats],
                    crs=points_buffered.crs
                ).reset_index(drop=True)

                del dem_img, dem_tr, dem_stats
                gc.collect()

                # -- b. Filtro altitudinal: dem_median > 2000 m ---------------
                # CAMBIO v5: el filtro se aplica ANTES de MapBiomas
                stats_df = stats_df[
                    stats_df['dem_median'] > ALT_THRESHOLD
                ].reset_index(drop=True)

                if len(stats_df) == 0:
                    skipped += 1
                    continue

                print(f"\n  Tile {t_idx+1:03d}: {len(stats_df)} puntos "
                      f"tras filtro DEM (>{ALT_THRESHOLD} m)")

                # -- c. MapBiomas: proporcion clase 12 por anio del fuego -----
                # CAMBIO v5: reemplaza WorldClim; usa banda = anio del fuego
                stats_df['acq_date']     = pd.to_datetime(stats_df['acq_date'])
                stats_df['year']         = stats_df['acq_date'].dt.year.astype(int)
                stats_df['prop_class12'] = np.nan

                for yr in sorted(stats_df['year'].unique()):
                    mask_yr = stats_df['year'] == yr
                    sub_gdf = stats_df.loc[mask_yr].reset_index(drop=True)
                    props   = calc_mapbiomas_proportions(
                                  sub_gdf, yr, tile_exp, mb_meta
                              )
                    stats_df.loc[mask_yr, 'prop_class12'] = props

                all_results.append(stats_df.copy())

                del stats_df, points_in_tile
                gc.collect()

                print(f"  [OK] Tile {t_idx+1:03d}/{len(tiles)} "
                      f"- {time.time()-t_tile:.1f}s")

        t = timer("Procesamiento por tiles (DEM + MapBiomas)", t)
        print(f"  Tiles sin datos / saltados: {skipped}")

        if not all_results:
            print("  [WARN] Sin resultados. Verificar datos de entrada y parametros.")
            return None

        # -- 6. Clustering espacio-temporal -----------------------------------
        final_df  = pd.concat(all_results).reset_index(drop=True)
        final_gdf = gpd.GeoDataFrame(final_df, geometry='geometry', crs='EPSG:4326')
        print(f"  Shape antes del clustering: {final_gdf.shape}")

        gdf_cluster = cluster_spatiotemporal(
            pd.DataFrame(final_gdf), SPATIAL_KM, TEMPORAL_DAYS
        )
        # Primer evento de cada cluster (orden cronologico)
        gdf_cluster = (gdf_cluster
                       .sort_values('date')
                       .groupby('cluster')
                       .first()
                       .reset_index())
        gdf_cluster = gdf_cluster.drop(columns=['geometry'], errors='ignore')
        gdf_cluster['geometry'] = gpd.points_from_xy(
            gdf_cluster['longitude'], gdf_cluster['latitude']
        )
        gdf_result = gpd.GeoDataFrame(gdf_cluster, geometry='geometry', crs='EPSG:4326')
        t = timer("Clustering espacio-temporal", t)
        print(f"  Clusters unicos (primer evento): {len(gdf_result)}")

        # -- 7. Exportar: Shapefile + CSV -------------------------------------
        # CAMBIO v5: doble salida
        base_out = Path(str(output_path)).with_suffix('')
        shp_path = base_out.with_suffix('.shp')
        csv_path = base_out.with_suffix('.csv')

        os.makedirs(shp_path.parent, exist_ok=True)

        # Shapefile
        gdf_result.to_file(shp_path)
        print(f"  [OK] Shapefile -> {shp_path}")

        # CSV (sin columna geometry)
        df_out = gdf_result.drop(columns=['geometry'], errors='ignore')
        df_out.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"  [OK] CSV       -> {csv_path}")

        t = timer("Exportacion", t)
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
        output_path   = output_dir / 'AnomaliesThermiquesMB_Peru_V3.shp'
    )