# -*- coding: utf-8 -*-
"""
Modified on 03/07/2026
Version 8.1.2
@author: jvilla

Changes v8.1.2 (sobre v8.1.1):
    [FIX-D] ensure_dem_bruto: VALIDACION de integridad de los tiles brutos antes
        de mosaicar. Se calcula el Checksum de cada tile (fuerza la lectura de
        todos sus bloques internos -> detecta TIFF truncados/corruptos como el
        que abortaba la corrida). Los tiles danados se EXCLUYEN y se registran en
        'tiles_corruptos.txt' para re-descarga; un solo tile roto ya no tumba el
        mosaico completo. Validacion paralela (ThreadPoolExecutor) y opcional via
        DEM_VALIDATE / DEM_VALIDATE_WORKERS.
    [FIX-E] ensure_dem_bruto: materializacion ATOMICA. gdal.Translate escribe a
        un '.tmp.tif' y solo al terminar con exito se hace os.replace() al nombre
        final (rename atomico en el mismo filesystem). Si Translate falla a
        medias, se limpian los parciales y NO queda un .tif incompleto que el
        cache ('dem_out.exists()') reutilizaria silenciosamente en la siguiente
        corrida (bug latente de v8.1.0/8.1.1).

Changes v8.1.1 (sobre v8.1.0):
    [FIX-A] Clustering: el representante de cada cluster ahora es la FILA real
        del primer evento (idxmin de 'date'), no groupby.first() — que tomaba
        el primer no-nulo POR COLUMNA y mezclaba cl12_m2/cl3_m2 y region_geo de
        puntos distintos en clusters que cruzan Sierra/Selva.
    [FIX-B] ensure_dem_bruto: la exclusion de 'output'/'mosaico' se evalua sobre
        la ruta RELATIVA a RAW_TILES_DIR y el nombre de archivo (antes: subcadena
        sobre la ruta absoluta -> podia excluir TODOS los tiles si una carpeta
        madre contenia esas palabras).
    [FIX-C] Submuestreo de test convertido en flag SAMPLE_N (None=produccion),
        con guarda contra ValueError si SAMPLE_N > filas del CSV.

Changes v8.1.0 (sobre v8.0.0):
    [MOD-9] Construccion del DEM bruto INTEGRADA en el pipeline (Python/GDAL),
        patron cache "construir-si-no-existe": si el mosaico .tif no existe, se
        arma desde los tiles brutos (RAW_TILES_DIR) via VRT transitorio +
        gdal.Translate (streaming, sin cargar todo en RAM) y se MATERIALIZA a
        GeoTIFF tileado/comprimido. El .tif es autocontenido -> portable entre
        local y cluster (a diferencia de un VRT, que guarda rutas y se rompe al
        moverlo). Config: RAW_TILES_DIR, DEM_BBOX, DEM_NODATA=0, DEM_REBUILD.
        Ver ensure_dem_bruto(). Sustituye al build_raw_dem_vrt.sh externo.

Changes v8.0.0 (sobre v7.0.0):
    [MOD-8] DEM BRUTO (sin filtro de altitud). La zonificacion Sierra/Selva la
        define EXCLUSIVAMENTE el shapefile de regiones (region_geo). El DEM
        filtrado a >2000 m eliminaba silenciosamente la Selva (tierras bajas
        <2000 m -> NoData -> descartadas por MOD-1). Ahora 'DEM' debe apuntar a
        un mosaico/VRT bruto (ver build_raw_dem_vrt.sh).
    [MOD-1b] Semantica de MOD-1 aclarada: con DEM bruto, descartar por
        'dem_median' NaN equivale a descartar SOLO buffers fuera de la cobertura
        espacial real del DEM (bordes/oceano), no por altitud. Se contabiliza
        cuantos puntos elimina para control.
    [CHK] Verificacion post-proceso: dem_median (count/min/max) por region_geo,
        para confirmar que la Selva sobrevive (min << 2000).
    [robustez] nodata y CRS del DEM se leen del propio archivo; sin supuestos.

--- Historico ----------------------------------------------------------------
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

# --- DEM bruto: construccion cache dentro del pipeline (v8.1.0) --------------
# [MOD-9] Los tiles brutos Copernicus GLO30 viven en el CLUSTER. La V8
#   construye el mosaico UNA sola vez (si no existe) y lo reusa. Se materializa
#   a GeoTIFF (portable, sin rutas externas -> no se rompe al mover como un VRT).
RAW_TILES_DIR = data_dir / "copernicus_dem_andes"   # None si ya tienes el .tif
# bbox Peru + margen (lon_min, lat_min, lon_max, lat_max): cubre Sierra y Selva
# (oriente ~-68.5, norte hasta lat ~0). Verificado en etapa de inspeccion.
DEM_BBOX      = (-81.5, -18.6, -68.5, 0.2)
DEM_NODATA    = 0        # 0 = oceano/relleno (GLO30 via GEE no declara nodata)
DEM_REBUILD   = False    # True para forzar reconstruccion aunque exista
# [FIX-D] Validacion de integridad de tiles antes de mosaicar (caza truncados).
#   True = recomendado la 1a vez (pago unico; el .tif queda cacheado). Pon False
#   si ya confias en la integridad de los tiles y quieres ahorrar la relectura.
DEM_VALIDATE         = True
DEM_VALIDATE_WORKERS = 8   # hilos para el checksum (abre datasets independientes)

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

# [FIX-C] Submuestreo para pruebas. None = usar TODO el CSV (produccion).
#   Entero = nro. de filas a muestrear (con guarda si excede el total).
SAMPLE_N      = None
#SAMPLE_SEED   = 54

# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()
    
def rss_gib():
    # ru_maxrss en Linux está en KiB
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


# --- [FIX-D] Validacion de integridad de un tile -----------------------------
def _tile_ok(path):
    """
    Devuelve (path, True/False). El Checksum de GDAL fuerza la lectura de TODOS
    los bloques internos del tile, de modo que un TIFF truncado/corrupto (p.ej.
    'TIFFFillTile: got N bytes, expected M') lanza excepcion y se marca invalido.
    Se abre un dataset independiente por hilo (seguro en ThreadPoolExecutor).
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
           os.replace() atomico al nombre final. Si falla, se limpian parciales
           y NO queda un .tif incompleto que el cache reutilice.
    El .tif resultante es autocontenido: portable entre local y cluster.

    raw_tiles_dir : dir con los tiles .tif (recursivo). Puede ser None si ya
                    tienes 'dem_out' (entonces solo se valida su existencia).
    bbox          : (lon_min, lat_min, lon_max, lat_max).
    nodata        : valor nodata a fijar (0 para GLO30 via GEE).
    validate      : [FIX-D] activar/desactivar la validacion por checksum.
    max_workers   : hilos para la validacion.
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
    # [FIX-B] Excluir la subcarpeta 'output/' y mosaicos previos SIN mirar la
    #   ruta absoluta (una carpeta madre podria contener 'output'/'mosaico').
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
    #    PREDICTOR=3 -> optimo para float (GLO30 es Float32).
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
        # No dejar parciales que el cache reutilice en la proxima corrida.
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
        print("  INICIO - AnomaliasTermicas_Peru v8.1.2")
        print(f"  Zonas conservadas: region1='{region1}' (clase12) | "
              f"region2='{region2}' (clase3)")
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
        mod1_dropped = 0   # [MOD-1b] buffers sin cobertura espacial DEM

        with rasterio.open(DEM) as dem_src:
            _dem_nd = dem_src.nodata
            print(f"  DEM abierto: {Path(str(DEM)).name} | CRS={dem_src.crs} | "
                  f"nodata={_dem_nd} | dtype={dem_src.dtypes[0]}")
            if _dem_nd is None:
                print("  [WARN] El DEM no declara nodata; se usa -9999 como "
                      "fallback en zonal_stats (sin efecto si no hay ese valor).")


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

                # -- b. [MOD-1/MOD-1b] Descartar SOLO buffers sin cobertura DEM
                #    (con DEM bruto esto ya no es un filtro de altitud, sino de
                #     cobertura espacial real: bordes/oceano/huecos del mosaico).
                _n_before = len(stats_df)
                stats_df = stats_df[
                    stats_df['dem_median'].notna()
                ].reset_index(drop=True)
                mod1_dropped += _n_before - len(stats_df)

                if len(stats_df) == 0:
                    skipped += 1
                    continue

                print(f"\n  Tile {t_idx+1:03d}: {len(stats_df)} puntos "
                      f"con cobertura DEM ({_n_before - len(stats_df)} sin cobertura)")

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
        print(f"  [MOD-1b] Puntos descartados por falta de cobertura DEM: "
              f"{mod1_dropped}")

        if not all_results:
            print("  [WARN] Sin resultados.")
            return None

        print(f"  [MEM] tras procesamiento tiles: {rss_gib():.2f} GiB")

        # -- 5b. [CHK] Verificacion: dem_median por region (confirmar Selva) ---
        final_df  = pd.concat(all_results).reset_index(drop=True)
        print("\n  [CHK] dem_median por region_geo (Selva debe tener min << 2000):")
        _chk = (final_df.groupby('region_geo')['dem_median']
                .agg(['count', 'min', 'median', 'max']))
        for reg, row in _chk.iterrows():
            print(f"    - {reg:12s}: n={int(row['count']):6d} | "
                  f"min={row['min']:.0f} | med={row['median']:.0f} | "
                  f"max={row['max']:.0f}")
        if region2 in _chk.index and _chk.loc[region2, 'count'] == 0:
            print(f"  [WARN] '{region2}' (Selva) sigue sin puntos: revisa que el "
                  f"DEM sea BRUTO y cubra la Selva.")

        # -- 6. Clustering espacio-temporal -----------------------------------
        final_gdf = gpd.GeoDataFrame(final_df, geometry='geometry', crs='EPSG:4326')
        print(f"  Shape antes del clustering: {final_gdf.shape}")

        gdf_cluster = cluster_spatiotemporal(
            pd.DataFrame(final_gdf), SPATIAL_KM, TEMPORAL_DAYS
        )
        # [FIX-A] Representante = FILA real del primer evento (idxmin de fecha).
        #   Evita el mezclado por-columna de groupby.first() en clusters que
        #   cruzan Sierra/Selva (cl12_m2/cl3_m2/region_geo de puntos distintos).
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
        # [MOD-9] DEM bruto materializado (se construye 1a vez desde RAW_TILES_DIR
        #   si no existe; portable local<->cluster). Ver ensure_dem_bruto().
        DEM           = data_dir / 'mosaico_peru_bruto.tif',
        regions_shape = data_dir / 'region-geografica.shp',
        region1       = "Sierra",
        region2       = "Selva",
        output_path   = output_dir / 'AnomaliesThermiquesMB_Peru_V8.shp'
    )