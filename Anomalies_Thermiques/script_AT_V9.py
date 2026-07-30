# -*- coding: utf-8 -*-
"""
Modified on 17/07/2026
Version 9.0.0
@author: jvilla

Changes v9.0.0 (sobre v8.1.2):
    [MOD-10] ZONIFICACION POR ECORREGIONES. El shapefile 'region-geografica.shp'
        (campo 'nombre', valores Costa/Sierra/Selva) se sustituye por el
        geopackage 'Ecoreg_peru_VF.gpkg', campo 'ECO_NAME'. La capa se
        autodetecta si el GPKG contiene una sola (ver load_ecoregions()).
    [MOD-11] SIN SELECCION DE VALORES. Se elimina el filtro MOD-5 (v7) que
        conservaba unicamente region1/region2: ahora se clasifican y conservan
        TODAS las anomalias termicas de TODAS las ecorregiones. Los puntos en
        Peru que no caen en ningun poligono (slivers de borde GAUL/GPKG) se
        conservan con ECO_NAME='sin_region'. Los parametros region1/region2
        desaparecen de la firma de filt_csv() y de __main__.
    [MOD-12] AMBAS CLASES PARA TODOS LOS PUNTOS. Se elimina el mapeo
        region->clase de MOD-7. Cada punto recibe ahora las DOS areas:
            cl12 = area de MapBiomas clase 12 (Grassland) en el buffer
            cl3  = area de MapBiomas clase 3  (Forest)    en el buffer
        Ninguna de las dos es NaN por construccion: NaN pasa a significar
        EXCLUSIVAMENTE 'sin cobertura de raster MapBiomas'. OJO: esto INVIERTE
        la semantica de v7/v8, donde cl12_m2=NaN significaba 'el punto es de
        region2'. Cualquier analisis aguas abajo que usara el NaN como proxy de
        region debe revisarse.
    [MOD-13] UNIDADES EN km2 y renombrado de columnas: cl12_m2 -> cl12,
        cl3_m2 -> cl3, ambas en km2 (antes m2). Techo teorico por buffer:
        1.0 km2 (cuadrado de 1000 m de lado) y cl12 + cl3 <= 1.0.
        Redondeo a 6 decimales (= 1 m2).
    [MOD-14] DEM: 0 deja de tratarse como nodata AL LEER (opcion C). El mosaico
        sigue declarando nodata=0 (no hay que reconstruirlo, DEM_REBUILD=False),
        pero zonal_stats recibe DEM_STATS_NODATA=-9999, de modo que la cota 0 m
        vuelve a ser una altitud REAL. Motivo: al abrir el analisis a todas las
        ecorregiones entran las costeras (Sechura, manglares de Tumbes, desierto
        costero) cuyos buffers estan a nivel del mar; con nodata=0 todos sus
        pixeles se ignoraban -> dem_median=NaN -> MOD-1 los borraba en silencio.
        MOD-1 dejaba de ser un filtro de cobertura para convertirse en un filtro
        de altitud encubierto, sesgando justo lo que v9 quiere cubrir.
        EFECTO LATERAL ASUMIDO: rio_mask rellena con 0 las zonas del recorte
        fuera del DEM, asi que un buffer realmente fuera del mosaico ya no da
        NaN sino dem_median=0 (altitud falsa). Se considera inocuo porque
        DEM_BBOX cubre todo Peru con margen y el paso 2 recorta a GAUL Peru; el
        [CHK] por ECO_NAME lo delataria (una ecorregion de montana con min=0).
    [MOD-1c] MOD-1 degradado a flag: DROP_NO_DEM (default False). dem_median es
        ya solo un ATRIBUTO descriptivo — la zonificacion viene del GPKG y las
        areas de MapBiomas, ninguna depende del DEM. Los puntos sin cobertura se
        CONTABILIZAN y se conservan con dem_median=NaN en vez de borrarse.
    [MOD-15] Salida en GPKG + CSV (antes SHP + CSV). Motivo: los valores de
        ECO_NAME llevan tildes ("Paramo", "Yungas peruanos", "Bosques secos del
        Maranon") y el .dbf del shapefile escribe en Latin-1 por defecto ->
        mojibake. GPKG es UTF-8 nativo y no trunca nombres de campo a 10 chars.
    [CHK-2] La verificacion post-proceso de dem_median se agrupa por ECO_NAME
        (antes por region_geo, con WARN codificado a 'Selva'), e incluye el
        reparto de puntos y la cobertura MapBiomas por ecorregion.

--- Historico ----------------------------------------------------------------
Changes v8.1.2 (sobre v8.1.1):
    [FIX-D] ensure_dem_bruto: VALIDACION de integridad de los tiles brutos antes
        de mosaicar (Checksum -> detecta TIFF truncados). Los tiles danados se
        EXCLUYEN y se registran en 'tiles_corruptos.txt'.
    [FIX-E] ensure_dem_bruto: materializacion ATOMICA via '.tmp.tif' +
        os.replace(), para no dejar parciales que el cache reutilice.
Changes v8.1.1 (sobre v8.1.0):
    [FIX-A] Clustering: representante = FILA real del primer evento (idxmin de
        'date'), no groupby.first().
    [FIX-B] ensure_dem_bruto: exclusion de 'output'/'mosaico' sobre la ruta
        RELATIVA a RAW_TILES_DIR.
    [FIX-C] Submuestreo de test convertido en flag SAMPLE_N.
Changes v8.1.0 (sobre v8.0.0):
    [MOD-9] Construccion del DEM bruto INTEGRADA en el pipeline (cache
        "construir-si-no-existe"): VRT transitorio + gdal.Translate a GeoTIFF
        tileado/comprimido, autocontenido y portable. Ver ensure_dem_bruto().
Changes v8.0.0 (sobre v7.0.0):
    [MOD-8] DEM BRUTO (sin filtro de altitud).
    [MOD-1b] Semantica de MOD-1 aclarada: descartar por dem_median NaN equivale
        a descartar buffers fuera de la cobertura espacial del DEM.
Changes v7.0.0 (sobre v6.0.0):
    [MOD-5] Filtro a DOS zonas geograficas.        (ELIMINADO en v9 -> MOD-11)
    [MOD-6] Nueva entrada: rasters de clase 3.
    [MOD-7] Calculo de area diferenciado por zona. (ELIMINADO en v9 -> MOD-12)
    [refactor] get_mapbiomas_metadata() y calc_mapbiomas_area() reciben el
        diccionario de tiles, para reutilizarse con clase 12 y clase 3.
Changes v6.0.0 (sobre v5.4.2):
    [MOD-4] Buffer CUADRADO (pixel MODIS 1000 m) via shapely.box.
    [MOD-1] Sin filtro de altitud; dem_median como atributo.
    [MOD-2] Eliminado el bloque de zonas climaticas (Zone_Clima).
    [MOD-3] Clasificacion por region geografica.   (SUSTITUIDO en v9 -> MOD-10)
Changes v5.4.2:
    [2] Acumulacion entre tiles (suma de areas, no promedio de %).
    [3] Salida en AREA.                            (m2 -> km2 en v9, MOD-13)
    [4] Anclaje de banda a YEAR_MAX.
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
from concurrent.futures import ThreadPoolExecutor   # [FIX-D] validacion paralela
from sklearn.neighbors import BallTree
import networkx as nx
import resource

# --- Rutas -------------------------------------------------------------------
base_dir      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML")
data_dir      = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir    = base_dir / "3_output"
mapbiomas_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML/3_output")

# [MOD-10] Ecorregiones (RESOLVE Ecoregions 2017 recortado a Peru).
eco_dir  = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/GFA/1_input/"
                "Ecorregions_2017_Peru")
ECO_PATH  = eco_dir / "Ecoreg_peru_VF.gpkg"
ECO_FIELD = "ECO_NAME"    # columna con el nombre de la ecorregion
ECO_LAYER = None          # None = autodetectar (falla si el GPKG tiene >1 capa)

# --- DEM bruto: construccion cache dentro del pipeline (v8.1.0) --------------
RAW_TILES_DIR = data_dir / "copernicus_dem_andes"   # None si ya tienes el .tif
# bbox Peru + margen (lon_min, lat_min, lon_max, lat_max): cubre todo el pais.
DEM_BBOX      = (-81.5, -18.6, -68.5, 0.2)
DEM_NODATA    = 0        # valor DECLARADO en el mosaico (GLO30 via GEE)
DEM_REBUILD   = False    # True para forzar reconstruccion aunque exista
DEM_VALIDATE         = True
DEM_VALIDATE_WORKERS = 8

# [MOD-14] Nodata usado AL LEER el DEM en zonal_stats. Centinela imposible en un
#   DEM real -> el 0 se interpreta como cota 0 m (playa, manglar, desierto
#   costero) y NO como 'sin dato'. No requiere reconstruir el mosaico: el .tif
#   sigue declarando nodata=DEM_NODATA, simplemente lo ignoramos en la lectura.
DEM_STATS_NODATA = -9999

# [MOD-1c] Si True, se BORRAN los puntos con dem_median NaN (comportamiento v8).
#   False (recomendado v9): se conservan con dem_median=NaN y solo se cuentan.
#   dem_median es un atributo descriptivo; ni ECO_NAME ni cl12/cl3 dependen del DEM.
DROP_NO_DEM = False

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

# [MOD-6] Tiles MapBiomas: clase 12 (Grassland) y clase 3 (Forest).
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

# [FIX-C] Submuestreo para pruebas. None = usar TODO el CSV (produccion).
SAMPLE_N      = None
SAMPLE_SEED   = 54


# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


def rss_gib():
    # ru_maxrss en Linux esta en KiB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2)


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


# --- [MOD-10] Ecorregiones: carga --------------------------------------------
def load_ecoregions(eco_path, layer=None, field=ECO_FIELD):
    """
    Carga el GPKG de ecorregiones, reproyecta a EPSG:4326 y devuelve un
    GeoDataFrame con [field, 'geometry'].

    - layer=None -> autodetecta. Si el GPKG contiene mas de una capa, aborta y
      lista las disponibles (un GPKG multicapa leido sin 'layer' devuelve la
      primera SIN avisar: fallo silencioso que preferimos convertir en error).
    - Repara geometrias invalidas (RESOLVE Ecoregions trae self-intersections
      en varios poligonos): make_valid, con fallback a buffer(0).
    """
    eco_path = Path(eco_path)
    if not eco_path.exists():
        raise FileNotFoundError(f"No existe el GPKG de ecorregiones: {eco_path}")

    if layer is None:
        try:
            import fiona
            layers = fiona.listlayers(str(eco_path))
        except Exception:
            from pyogrio import list_layers
            layers = [str(x[0]) for x in list_layers(str(eco_path))]
        if len(layers) == 0:
            raise ValueError(f"El GPKG {eco_path.name} no contiene capas.")
        if len(layers) > 1:
            raise ValueError(
                f"El GPKG {eco_path.name} contiene {len(layers)} capas "
                f"{layers}. Fija ECO_LAYER explicitamente.")
        layer = layers[0]

    eco = gpd.read_file(eco_path, layer=layer)
    print(f"  Ecorregiones     : {eco_path.name} | capa='{layer}' | "
          f"{len(eco)} poligono(s) | CRS={eco.crs}")

    if field not in eco.columns:
        raise KeyError(
            f"El campo '{field}' no existe en la capa '{layer}'. "
            f"Columnas disponibles: {list(eco.columns)}")

    eco = eco[[field, 'geometry']].to_crs('EPSG:4326')

    n_bad = int((~eco.geometry.is_valid).sum())
    if n_bad:
        print(f"  [WARN] {n_bad} geometria(s) invalida(s) -> reparando")
        try:
            eco['geometry'] = eco.geometry.make_valid()
        except AttributeError:
            eco['geometry'] = eco.geometry.buffer(0)

    n_null = int(eco[field].isna().sum())
    if n_null:
        print(f"  [WARN] {n_null} poligono(s) con {field} nulo -> los puntos "
              f"que caigan ahi quedaran como 'sin_region'")

    print(f"  Ecorregiones distintas en la capa: {eco[field].nunique()}")
    return eco


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
    Calcula el AREA [km2] de la clase contenida en 'tiles_dict' dentro de cada
    buffer. El raster contiene SOLO pixeles de esa clase (resto = fondo/nodata),
    asi que en zonal_stats:  count = pixeles de la clase ; nodata = fondo.

    Reutilizable para clase 12 (Grassland) o clase 3 (Forest) segun el
    'tiles_dict' / 'mb_meta' que se le pase.

    [2]      Acumula el area entre tiles a caballo (no promedia).
    [4]      Banda anclada al final: band_idx = n_bandas - (YEAR_MAX - year).
    [MOD-13] Devuelve km2 (el calculo interno sigue en m2; /1e6 al final).
             Techo por buffer de 1000 m de lado: 1.0 km2.
    Anios fuera de [YEAR_MIN, YEAR_MAX]  -> NaN.
    Buffers sin cobertura de raster      -> NaN.
    Buffers con cobertura pero sin clase -> 0.0.
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
    # [MOD-13] m2 -> km2. 6 decimales = 1 m2 (un pixel de 30 m ~ 0.0009 km2).
    areas[seen] = np.round(area_acc[seen] / 1e6, 6)
    return areas


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, mb_layers,
                             gdf_result, output_dir, base_name):
    """
    Recorta y guarda capas cartograficas limitadas a la AOI (solo visualizacion).

    mb_layers : dict {label: (tiles_dict, meta_dict)}  -> p.ej. {'c12': (...),
                'c3': (...)}. Se genera un .tif por tile que intersecta la AOI,
                con nombre {base_name}_aoi_mapbiomas_{label}_rXcY.tif.
    [MOD-15] Los resultados recortados salen en GPKG + CSV (no SHP).
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

    # -- GPKG y CSV resultado filtrado a AOI ----------------------------------
    mask_aoi = gdf_result.geometry.within(aoi_geom)
    gdf_aoi  = gdf_result[mask_aoi].reset_index(drop=True)
    n_pts    = len(gdf_aoi)

    gpkg_aoi = output_dir / f'{base_name}_aoi_results.gpkg'
    csv_aoi  = output_dir / f'{base_name}_aoi_results.csv'

    if n_pts > 0:
        gdf_aoi.to_file(gpkg_aoi, layer=f'{base_name}_aoi', driver='GPKG')
        gdf_aoi.drop(columns=['geometry']).to_csv(csv_aoi, index=False,
                                                  encoding='utf-8-sig')
        print(f"  [OK] Resultados AOI   -> {gpkg_aoi.name} ({n_pts} puntos)")
    else:
        print("  [INFO] Ningun punto resultado cae dentro de la AOI.")

    timer(f"Exportacion cartografica (DEM + {n_mb} tiles MB + resultados)", t)


# --- [FIX-D] Validacion de integridad de un tile -----------------------------
def _tile_ok(path):
    """
    Devuelve (path, True/False). El Checksum de GDAL fuerza la lectura de TODOS
    los bloques internos del tile, de modo que un TIFF truncado/corrupto lanza
    excepcion y se marca invalido. Un dataset independiente por hilo.
    """
    from osgeo import gdal
    gdal.UseExceptions()
    ds = None
    try:
        ds = gdal.Open(path)
        if ds is None:
            return path, False
        for b in range(1, ds.RasterCount + 1):
            ds.GetRasterBand(b).Checksum()
        return path, True
    except Exception:
        return path, False
    finally:
        ds = None


# --- Construccion cache del DEM bruto ----------------------------------------
def ensure_dem_bruto(raw_tiles_dir, dem_out, bbox, nodata=0, rebuild=False,
                     validate=True, max_workers=8):
    """
    [MOD-9 / FIX-D,E] Garantiza un DEM bruto materializado y lo devuelve (str).

    - Si 'dem_out' existe y not rebuild -> se reutiliza (cache).
    - Si no existe -> se construye desde los tiles brutos de 'raw_tiles_dir':
        0) [FIX-D] se VALIDA cada tile (Checksum); los corruptos se excluyen y
           registran en 'tiles_corruptos.txt' -> un tile roto no aborta todo.
        1) VRT transitorio (rutas validas en ESTA maquina) recortado a 'bbox';
        2) [FIX-E] gdal.Translate a un '.tmp.tif' tileado+comprimido y, al exito,
           os.replace() atomico al nombre final.
    El .tif resultante es autocontenido: portable entre local y cluster.

    NOTA v9 [MOD-14]: 'nodata' es el valor que se DECLARA en el .tif. La lectura
    en filt_csv() usa DEM_STATS_NODATA (-9999) y por tanto trata el 0 como cota
    real. No hace falta reconstruir el mosaico por este cambio.
    """
    dem_out = Path(dem_out)
    if dem_out.exists() and not rebuild:
        print(f"  [DEM] Reutilizando existente: {dem_out.name}")
        return str(dem_out)
    if dem_out.exists() and rebuild:
        print(f"  [DEM] rebuild=True -> regenerando {dem_out.name}")

    if raw_tiles_dir is None:
        raise FileNotFoundError(
            f"No existe {dem_out} y RAW_TILES_DIR=None: no hay de donde construir "
            f"el DEM. Provee el .tif o define RAW_TILES_DIR.")

    try:
        from osgeo import gdal
    except ImportError as e:
        raise ImportError(
            "Se requieren los bindings de GDAL (osgeo) para construir el DEM. "
            "En conda: conda install -c conda-forge gdal") from e

    gdal.UseExceptions()

    raw_tiles_dir = Path(raw_tiles_dir)
    # [FIX-B] Excluir 'output/' y mosaicos previos SIN mirar la ruta absoluta.
    dem_name_low = dem_out.name.lower()
    tiles = []
    for p in raw_tiles_dir.rglob('*.tif'):
        rel_parts = [x.lower() for x in p.relative_to(raw_tiles_dir).parts]
        if 'output' in rel_parts:
            continue
        nm = p.name.lower()
        if nm.startswith('mosaico') or nm == dem_name_low:
            continue
        tiles.append(str(p))
    tiles.sort()
    if not tiles:
        raise FileNotFoundError(f"Sin tiles .tif en {raw_tiles_dir}")

    # -- [FIX-D] Validacion de integridad (pago unico; el .tif queda cacheado) --
    if validate:
        print(f"  [DEM] Validando integridad de {len(tiles)} tiles "
              f"({max_workers} hilos)...")
        bad = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for path, ok in tqdm(ex.map(_tile_ok, tiles), total=len(tiles),
                                 desc="Validando tiles"):
                if not ok:
                    bad.append(path)
        if bad:
            log = dem_out.parent / 'tiles_corruptos.txt'
            log.write_text('\n'.join(bad))
            print(f"  [DEM] {len(bad)} tile(s) CORRUPTO(S) excluido(s). "
                  f"Lista -> {log.name} (re-descargalos para cobertura completa)")
            bad_set = set(bad)
            tiles = [t for t in tiles if t not in bad_set]
        else:
            print("  [DEM] Todos los tiles pasaron la validacion.")
        if not tiles:
            raise FileNotFoundError(
                "Todos los tiles fallaron la validacion; nada que mosaicar.")

    print(f"  [DEM] Construyendo desde {len(tiles)} tiles validos...")

    dem_out.parent.mkdir(parents=True, exist_ok=True)
    tmp_vrt = str(dem_out.with_suffix('.tmp.vrt'))
    tmp_tif = str(dem_out.with_suffix('.tmp.tif'))   # [FIX-E]

    # 1) VRT transitorio recortado al bbox, con nodata explicito
    gdal.BuildVRT(
        tmp_vrt, tiles,
        options=gdal.BuildVRTOptions(
            outputBounds=list(bbox),      # [xmin, ymin, xmax, ymax]
            srcNodata=nodata, VRTNodata=nodata,
            resampleAlg='nearest',
        )
    )

    # 2) [FIX-E] Materializar a un .tmp.tif y renombrar ATOMICO al final.
    print(f"  [DEM] Materializando a {dem_out.name} (tiled+DEFLATE, streaming)...")
    try:
        gdal.Translate(
            tmp_tif, tmp_vrt,
            options=gdal.TranslateOptions(
                format='GTiff', noData=nodata,
                creationOptions=[
                    'COMPRESS=DEFLATE', 'PREDICTOR=3', 'ZLEVEL=6',
                    'TILED=YES', 'BLOCKXSIZE=512', 'BLOCKYSIZE=512',
                    'BIGTIFF=IF_SAFER', 'NUM_THREADS=ALL_CPUS',
                ],
            )
        )
        os.replace(tmp_tif, dem_out)   # rename atomico (mismo filesystem)
    except Exception:
        for f in (tmp_tif, tmp_vrt):
            try:
                os.remove(f)
            except OSError:
                pass
        raise
    finally:
        try:
            os.remove(tmp_vrt)
        except OSError:
            pass

    print(f"  [DEM] Listo: {dem_out}")
    return str(dem_out)


def filt_csv(file_path, country_shape, DEM, eco_path, output_path,
             eco_layer=ECO_LAYER):
    """
    Procesa detecciones MODIS — Peru, TODAS las ecorregiones.

    [MOD-10/11] Cada punto se clasifica por la ecorregion que lo contiene
        (ECO_NAME del GPKG). No hay seleccion de valores: se conservan todas.
        Los puntos en Peru fuera de cualquier poligono -> 'sin_region'.
    [MOD-12] Cada punto recibe las DOS areas (no una u otra segun la zona).

    Columnas de salida:
      latitude, longitude, acq_date, acq_time, satellite, confidence, type,
      gaul0_name, ECO_NAME, dem_median, year, cl12, cl3, cluster, date, geometry

    ECO_NAME   : nombre de la ecorregion (valor unico por punto).
    cl12 [km2] : area de MapBiomas clase 12 (Grassland) dentro del buffer.
    cl3  [km2] : area de MapBiomas clase 3  (Forest)    dentro del buffer.
                 Techo: 1.0 km2 cada una; cl12 + cl3 <= 1.0. ha = km2 * 100.
                 NaN = sin cobertura de raster MapBiomas (NO 'otra region',
                 al contrario que en v7/v8). 0.0 = cubierto, sin esa clase.
    dem_median : atributo descriptivo (m). NaN posible si DROP_NO_DEM=False.
    """
    try:
        t_total = time.time()
        print("=" * 58)
        print("  INICIO - AnomaliasTermicas_Peru v9.0.0")
        print("  Zonificacion: TODAS las ecorregiones (ECO_NAME, sin filtro)")
        print("  Areas       : cl12 (clase 12 Grassland) + cl3 (clase 3 Forest)")
        print("                para TODOS los puntos, en km2")
        print("=" * 58)

        # -- [MOD-9] Asegurar DEM bruto (construir-si-no-existe, cache) --------
        DEM = ensure_dem_bruto(RAW_TILES_DIR, DEM, DEM_BBOX,
                               nodata=DEM_NODATA, rebuild=DEM_REBUILD,
                               validate=DEM_VALIDATE,
                               max_workers=DEM_VALIDATE_WORKERS)

        # -- 0. Cargar AOI ----------------------------------------------------
        aoi_geom = load_aoi()

        # -- 1. Carga y filtros tematicos -------------------------------------
        t = time.time()
        df = pd.read_csv(file_path)
        _n0 = len(df)
        if SAMPLE_N is not None:
            df = df.sample(n=min(SAMPLE_N, _n0), random_state=SAMPLE_SEED)
            print(f"  [TEST] Submuestreo activo: {len(df)}/{_n0} filas "
                  f"(SAMPLE_N={SAMPLE_N}). Pon SAMPLE_N=None para produccion.")
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

        print(f"  [MEM] tras carga CSV: {rss_gib():.2f} GiB")

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

        # -- 2b. [MOD-10/11] Clasificacion por ECORREGION (sobre el PUNTO) -----
        eco = load_ecoregions(eco_path, layer=eco_layer, field=ECO_FIELD)

        gdf_peru = gpd.sjoin(gdf_peru, eco, how='left', predicate='within')
        # Un punto sobre la frontera compartida de dos poligonos puede duplicar
        # la fila; ECO_NAME debe ser UNICO por punto -> nos quedamos con la 1a.
        _n_dup  = int(gdf_peru.index.duplicated().sum())
        gdf_peru = gdf_peru[~gdf_peru.index.duplicated(keep='first')]
        if _n_dup:
            print(f"  [INFO] {_n_dup} punto(s) en frontera entre ecorregiones "
                  f"-> se conserva la primera coincidencia")

        # [MOD-11] 'sin_region' se CONSERVA (slivers de borde GAUL/GPKG).
        gdf_peru[ECO_FIELD] = gdf_peru[ECO_FIELD].fillna('sin_region')
        gdf_peru = (gdf_peru.drop(columns=['index_right'], errors='ignore')
                    .reset_index(drop=True))

        _rep = gdf_peru[ECO_FIELD].value_counts()
        print(f"  Reparto por ecorregion ({len(_rep)} clases, TODAS conservadas):")
        for reg, cnt in _rep.items():
            print(f"    - {reg}: {cnt}")
        _n_sr = int((gdf_peru[ECO_FIELD] == 'sin_region').sum())
        if _n_sr:
            print(f"  [INFO] {_n_sr} punto(s) en Peru fuera de toda ecorregion "
                  f"-> ECO_NAME='sin_region' (conservados)")

        if len(gdf_peru) == 0:
            print("  [WARN] Sin puntos tras la clasificacion.")
            return None
        t = timer("Clasificacion por ecorregion", t)

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

        # [MOD-12] Ambas clases para TODOS los puntos (ya no hay mapeo por zona).
        #   (columna destino, meta, tiles, etiqueta de log)
        CLASS_CALC = [
            ('cl12', mb_meta_c12, MAPBIOMAS_TILES_C12, 'clase12_grassland'),
            ('cl3',  mb_meta_c3,  MAPBIOMAS_TILES_C3,  'clase3_forest'),
        ]

        all_results  = []
        skipped      = 0
        mod1_nodem   = 0   # [MOD-1c] buffers sin cobertura espacial DEM
        mod1_dropped = 0   # solo > 0 si DROP_NO_DEM=True

        with rasterio.open(DEM) as dem_src:
            _dem_nd = dem_src.nodata
            print(f"  DEM abierto: {Path(str(DEM)).name} | CRS={dem_src.crs} | "
                  f"nodata declarado={_dem_nd} | dtype={dem_src.dtypes[0]}")
            # [MOD-14] Se IGNORA el nodata declarado a proposito.
            print(f"  [MOD-14] zonal_stats usa nodata={DEM_STATS_NODATA}: la cota "
                  f"0 m se trata como altitud REAL (no como 'sin dato').")
            print(f"  [MOD-1c] DROP_NO_DEM={DROP_NO_DEM} -> los puntos sin "
                  f"cobertura DEM {'SE BORRAN' if DROP_NO_DEM else 'se conservan (dem_median=NaN)'}")

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

                # [MOD-14] nodata centinela -> el 0 cuenta como cota real.
                dem_stats = zonal_stats(
                    points_in_tile, dem_img[0], affine=dem_tr,
                    stats=['median'], prefix='dem_', geojson_out=True,
                    nodata=DEM_STATS_NODATA
                )

                stats_df = gpd.GeoDataFrame(
                    [f['properties'] for f in dem_stats],
                    geometry=[shape(f['geometry']) for f in dem_stats],
                    crs=points_buffered.crs
                ).reset_index(drop=True)

                del dem_img, dem_tr, dem_stats
                gc.collect()

                # -- b. [MOD-1c] dem_median como ATRIBUTO (no filtro) ---------
                _n_before = len(stats_df)
                _no_dem   = stats_df['dem_median'].isna()
                _n_nodem  = int(_no_dem.sum())
                mod1_nodem += _n_nodem

                if DROP_NO_DEM and _n_nodem:
                    stats_df = stats_df[~_no_dem].reset_index(drop=True)
                    mod1_dropped += _n_nodem

                if len(stats_df) == 0:
                    skipped += 1
                    continue

                print(f"\n  Tile {t_idx+1:03d}: {len(stats_df)} puntos "
                      f"({_n_nodem} sin cobertura DEM"
                      f"{' -> borrados' if DROP_NO_DEM else ' -> dem_median=NaN'})")

                # -- c. [MOD-12] AMBAS clases para todos los puntos -----------
                stats_df['acq_date'] = pd.to_datetime(stats_df['acq_date'])
                stats_df['year']     = stats_df['acq_date'].dt.year.astype(int)
                stats_df['cl12']     = np.nan
                stats_df['cl3']      = np.nan

                for yr in sorted(stats_df['year'].unique()):
                    mask_yr = (stats_df['year'] == yr)
                    if not mask_yr.any():
                        continue
                    sub_gdf = stats_df.loc[mask_yr].reset_index(drop=True)
                    for col, meta_c, tiles_c, lbl in CLASS_CALC:
                        area = calc_mapbiomas_area(
                            sub_gdf, yr, tile_exp, meta_c, tiles_c, lbl
                        )
                        stats_df.loc[mask_yr, col] = area

                all_results.append(stats_df.copy())
                del stats_df, points_in_tile
                gc.collect()

                print(f"  [OK] Tile {t_idx+1:03d}/{len(tiles)} "
                      f"- {time.time()-t_tile:.1f}s")

        t = timer("Procesamiento por tiles (DEM + MapBiomas x2)", t)
        print(f"  Tiles sin datos / saltados: {skipped}")
        print(f"  [MOD-1c] Puntos sin cobertura DEM: {mod1_nodem} "
              f"(borrados: {mod1_dropped})")

        if not all_results:
            print("  [WARN] Sin resultados.")
            return None

        print(f"  [MEM] tras procesamiento tiles: {rss_gib():.2f} GiB")

        # -- 5b. [CHK-2] Verificacion por ECO_NAME ----------------------------
        final_df = pd.concat(all_results).reset_index(drop=True)

        print("\n  [CHK-2] dem_median y cobertura MapBiomas por ECO_NAME:")
        print(f"    {'ECO_NAME':38s} {'n':>7s} {'dem_min':>8s} {'dem_med':>8s} "
              f"{'dem_max':>8s} {'%cl12':>6s} {'%cl3':>6s}")
        for reg, g in final_df.groupby(ECO_FIELD):
            n     = len(g)
            dmin  = g['dem_median'].min()
            dmed  = g['dem_median'].median()
            dmax  = g['dem_median'].max()
            p12   = 100.0 * g['cl12'].notna().mean()
            p3    = 100.0 * g['cl3'].notna().mean()
            _f = lambda v: f"{v:8.0f}" if pd.notna(v) else f"{'NaN':>8s}"
            print(f"    {str(reg)[:38]:38s} {n:7d} {_f(dmin)} {_f(dmed)} "
                  f"{_f(dmax)} {p12:5.1f}% {p3:5.1f}%")

        # Red de seguridad [MOD-14]: una ecorregion de montana con dem_min=0
        # delata un hueco en el mosaico (rio_mask rellena con 0 fuera del DEM).
        _nan_dem = int(final_df['dem_median'].isna().sum())
        if _nan_dem:
            print(f"  [INFO] {_nan_dem} punto(s) con dem_median=NaN "
                  f"(conservados; ver DROP_NO_DEM)")
        _nan_mb = int((final_df['cl12'].isna() | final_df['cl3'].isna()).sum())
        if _nan_mb:
            print(f"  [WARN] {_nan_mb} punto(s) sin cobertura MapBiomas en al "
                  f"menos una clase (cl12/cl3 = NaN)")

        # -- 6. Clustering espacio-temporal -----------------------------------
        final_gdf = gpd.GeoDataFrame(final_df, geometry='geometry', crs='EPSG:4326')
        print(f"  Shape antes del clustering: {final_gdf.shape}")

        gdf_cluster = cluster_spatiotemporal(
            pd.DataFrame(final_gdf), SPATIAL_KM, TEMPORAL_DAYS
        )
        # [FIX-A] Representante = FILA real del primer evento (idxmin de fecha).
        #   NOTA v9: un cluster puede cruzar dos ecorregiones; el ECO_NAME del
        #   cluster es el del PRIMER evento en el tiempo (decision asumida).
        first_idx   = gdf_cluster.groupby('cluster')['date'].idxmin()
        gdf_cluster = gdf_cluster.loc[first_idx].reset_index(drop=True)
        gdf_cluster = gdf_cluster.drop(columns=['geometry'], errors='ignore')
        gdf_cluster['geometry'] = gpd.points_from_xy(
            gdf_cluster['longitude'], gdf_cluster['latitude']
        )
        gdf_result = gpd.GeoDataFrame(gdf_cluster, geometry='geometry',
                                      crs='EPSG:4326')
        t = timer("Clustering espacio-temporal", t)
        print(f"  Clusters unicos (primer evento): {len(gdf_result)}")

        print(f"  [MEM] tras clustering: {rss_gib():.2f} GiB")

        # -- 6b. Orden de columnas --------------------------------------------
        _order = ['latitude', 'longitude', 'acq_date', 'acq_time', 'satellite',
                  'confidence', 'type', 'gaul0_name', ECO_FIELD, 'dem_median',
                  'year', 'cl12', 'cl3', 'cluster', 'date', 'geometry']
        _cols = [c for c in _order if c in gdf_result.columns]
        _cols += [c for c in gdf_result.columns if c not in _cols]
        gdf_result = gdf_result[_cols]

        # -- 7. [MOD-15] Exportar resultados (GPKG + CSV) ---------------------
        base_out  = Path(str(output_path)).with_suffix('')
        base_name = base_out.stem
        gpkg_path = base_out.with_suffix('.gpkg')
        csv_path  = base_out.with_suffix('.csv')

        os.makedirs(gpkg_path.parent, exist_ok=True)

        gdf_result.to_file(gpkg_path, layer=base_name, driver='GPKG')
        print(f"  [OK] GeoPackage -> {gpkg_path.name} (capa '{base_name}')")
        gdf_result.drop(columns=['geometry']).to_csv(
            csv_path, index=False, encoding='utf-8-sig'
        )
        print(f"  [OK] CSV        -> {csv_path.name}")
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
        print(f"  ECORREGIONES EN LA SALIDA: {gdf_result[ECO_FIELD].nunique()}")
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
        # [MOD-9] DEM bruto materializado (se construye 1a vez desde RAW_TILES_DIR
        #   si no existe; portable local<->cluster). Ver ensure_dem_bruto().
        DEM           = data_dir / 'mosaico_peru_bruto.tif',
        # [MOD-10] Ecorregiones: TODAS, sin seleccion de valores.
        eco_path      = ECO_PATH,
        eco_layer     = ECO_LAYER,
        output_path   = output_dir / 'AnomaliesThermiquesMB_Peru_V9.gpkg'
    )
