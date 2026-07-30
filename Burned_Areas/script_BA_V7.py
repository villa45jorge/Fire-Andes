# -*- coding: utf-8 -*-
import os
# Aumentar cache GDAL para lectura eficiente de tiles MapBiomas (~138 GB por tile).
# GDAL lee bloques de ~256 KB del archivo; con 1 GB de cache reutiliza bloques
# recientes y reduce I/O. Debe setearse antes de que rasterio importe GDAL.
os.environ.setdefault("GDAL_CACHEMAX", "512")   # [v7.1] era 1024 → 6 workers × 1 GB = 6 GB innecesario
"""
Modified on 10/06/2026
Version 7.2.4
@author: jvilla

Base   : script_BA_V4.py  (MCD64A1 — Burned Areas raster)
Logica : script_AT_V5.py

Changes v7.2:
    [PATH]     mapbiomas_dir corregido: era MCD14ML/3_output/clase12_r*
               (tiles del pipeline AT), ahora apunta a los tiles propios BA en
               MCD64A1/2_processed/mapbiomas_ba_grid/mapbiomas_ba_r{r}c{c}.tif
    [BAND]     BAND_YEAR_MIN = 1985 (origen real de la banda 1 en los tiles AT y
               MapBiomas — 40 bandas, 1985-2024). Era YEAR_MIN=2001, lo que
               desplazaba band_idx 16 posiciones: año 2001 leía banda 1 (=1985),
               año 2024 leía banda 24 (=2008). Toda la columna pct_class12 era
               incorrecta. YEAR_MIN se conserva como filtro del periodo de estudio.
    [SOURCE]   discover_ba_years(): nueva función que detecta fuentes BA en orden
               de prioridad: (1) tiles raw multi-banda en 0_raw/{región}/ siguiendo
               la estructura del pipeline AT; (2) mosaicos pre-procesados en
               1_input/mosaics_BA/ como fallback. Reemplaza el glob directo en
               run_pipeline y en el orquestador paso 0b.
    [SOURCE]   _merge_raw_tiles_for_year(): fusiona los tiles raw de todas las
               regiones para un año dado usando rasterio.merge (sin cargar todo
               en RAM — clip previo a roi_bounds). Devuelve un array 2D equivalente
               a una banda de mosaico → process_burned_areas sin cambios de firma.
    [SOURCE]   process_burned_areas: acepta dict {year: ('mosaic',Path) |
               ('raw',[paths])}. En modo 'raw' fusiona tiles y procesa como una
               sola pasada anual (month derivado de BurnDate DOY). En modo 'mosaic'
               comportamiento previo sin cambios.

Changes v7.1.1:
    [BANDFIX]  get_mapbiomas_metadata: guarda src.count (n_bands) por tile.
    [BANDFIX]  calc_mapbiomas_proportions valida band_idx < n_bands.
    [GC]       gc.collect() movido fuera del loop más interno.
    [LOG]      except Exception: añade type(e).__name__.
    [COMMENT]  Bloque duplicado eliminado. SAMPLE_FRAC comentario corregido.

Changes v7.1.0:
    [OOM FIX]  calc_mapbiomas_proportions: bucle 2°×2° de tiles de proceso.
               V7.0 generaba extent_geom del ROI completo → rio_mask de 5.5 GB.
               V7.1 itera tiles de PROC_TILE_DEG grados: out_image acotado a ~55 MB.
               Lectura con rasterio.windows.from_bounds en lugar de rio_mask.
               Asignacion por representative_point (sin doble conteo en bordes).
    [REORDER]  process_burned_areas: sjoin + COUNTRY_FILTER movidos ANTES de
               calc_mapbiomas. Reduce poligonos MB de ~100K (ROI) a ~8K (Peru)
               y limita el extent a las fronteras del pais filtrado.
    [CACHE]    GDAL_CACHEMAX 1024 → 512 MB. Ahorro: ~3 GB con 6 workers.

Changes v7.0.0:
    [MAPBIOMAS] Lógica MapBiomas adoptada de script_AT_V5 (lectura directa
                de tiles originales MCD14ML/3_output sin preprocesamiento):
                - Elimina preprocess_mapbiomas() / gdalwarp / tiles BA-grid
                - rio_mask recorta el tile original al extent del lote mensual
                  → lee solo unos MB de los 138 GB por tile
                - Una sola llamada zonal_stats(stats=['count','nodata'],
                  nodata=nodata_class, all_touched=True) — patrón AT_V5 v5.4.1
                - CRS reprojection explícita si tile != WGS84
                - Conserva FIX v6: acumulación ponderada multi-tile
    [SIMPLIFY]  _preprocess_only() reducido a validación de tiles existentes.
    [REMOVE]    MB_BA_DIR, MAPBIOMAS_BA_TILES, preprocess_mapbiomas(),
                array_bounds import.

Changes v6.1.1-vf:
    [PROD]  SAMPLE_FRAC = None: dataset 100%, sin submuestreo.
    [VF]    Salidas renombradas a "vf" (version final).
    [SLURM] N_WORKERS autodetectado: -c 6 → 6 workers en paralelo.

Changes v6.1.0:
    [NO_MIN_PIX]   Eliminado filtro MIN_PIX: se procesan TODOS los eventos
                   (~2M/mes en produccion). pd.groupby sustituido por stats
                   scipy.ndimage (nivel C), eliminando el OOM por objetos Python.
    [STREAMING]    process_burned_areas escribe MES a MES al GPKG en lugar de
                   acumular todos los meses del año en RAM. Peak RAM = 1 mes.
                   MapBiomas, sjoin y filtros se aplican dentro del bucle mensual.
    [NDIMAGE_MODE] Nueva funcion _ndimage_mode: moda de pais via ndimage.sum
                   iterando sobre COUNTRIES_ADM0 (5 paises) en C-level.

Changes v6.0.0:
    [Peru]         Filtrado de salida a Peru: COUNTRY_FILTER = "Peru".
                   Los paises vecinos siguen cargandose para rasterizacion
                   correcta de bordes; el filtro se aplica tras el spatial join.
    [class12_area] Nueva columna area_class12_ha: area de grassland MapBiomas
                   (clase 12) contenida en cada poligono quemado.
                   area_class12_ha = (pct_class12 / 100) * area_ha

Changes v5.4.0:
    [Pool]     ProcessPoolExecutor: un subproceso por ano (modo worker).
               Orquestador lanza N_WORKERS trabajos paralelos y hace merge.
    [N_WORKERS] Configurable via SLURM_CPUS_PER_TASK o fallback a 3.

Changes v5.3.0:
    [E2]  MIN_PIX filter: np.bincount antes del groupby elimina eventos ruido.
          Reduce N_events de ~2M a ~50K por mes → groupby manejable.
    [E3]  ndimage.label(output=labeled_out): pre-alocado int32, sin copia.
    [MapBiomas] preprocess_mapbiomas: gdalwarp subprocess genera tiles al grid
                BA (~640 MB) una sola vez; calc_mapbiomas_proportions lee 27 MB.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
import rasterio.features
import rasterio.warp
import geopandas as gpd
from shapely.geometry import box, shape, mapping
from shapely.ops import unary_union
from scipy import ndimage
from collections import Counter, defaultdict
from rasterstats import zonal_stats
from rasterio.mask import mask as rio_mask
from rasterio.crs import CRS
import os
import subprocess
import sys
import argparse
import time
import gc
from datetime import timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

# --- Rutas -------------------------------------------------------------------
base_dir      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD64A1")
data_dir      = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir    = base_dir / "3_output"
test_dir      = base_dir / "4_test"

# [v7.2] Tiles MapBiomas propios del pipeline BA.
# v7.1 usaba MCD14ML/3_output/clase12_r* (tiles AT) — incorrecto.
mapbiomas_dir = processed_dir / "mapbiomas_ba_grid"

# --- Area de Interes (AOI) — solo para exportacion cartografica --------------
# El analisis cubre siempre todo el ROI. La AOI se usa unicamente para recortar
# los rasters (DEM, MapBiomas) y el shapefile/CSV resultado al final.
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales (analisis) ------------------------------------------
ROI_BBOX       = (-80.0, -20.0, -60.0,  1.0)
YEARS_TEST     = list(range(2001, 2025))   # vf — periodo completo 2001-2024 (24 años)
ELEV_THRESHOLD = 2000
COUNTRIES_ADM0 = [178, 184, 185, 190, 207]

# Filtro de pais para la salida (v6).
COUNTRY_FILTER = "Peru"

# [v7.2] BAND_YEAR_MIN = 1985: origen real de la banda 1 en los tiles raw.
# Los tiles tienen 40 bandas (1985-2024); banda 17 = 2001, banda 40 = 2024.
# YEAR_MIN/YEAR_MAX siguen siendo el filtro del periodo de estudio.
BAND_YEAR_MIN   = 1985
BAND_YEAR_MAX   = 2024
YEAR_MIN        = 2001
YEAR_MAX        = 2024
MAPBIOMAS_BANDS = BAND_YEAR_MAX - BAND_YEAR_MIN + 1   # 40

# [v7.2] Tiles MapBiomas BA con nomenclatura corregida
MAPBIOMAS_TILES = {
    (r, c): mapbiomas_dir / f"mapbiomas_ba_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}

# [v7.2] Fuentes de datos BA — en orden de prioridad:
#   1. VRT (v7.2.2)  : BA_VRT_PATH — mosaic virtual de todos los tiles raw.
#      Se construye una sola vez (~5-10 min) y se reutiliza para todos los años.
#      Elimina el cuello de botella de 137K opens secuenciales que causaba TIMEOUT.
#   2. Raw tiles      : BA_RAW_DIR/{región}/*.tif — fallback si VRT no disponible.
#   3. Mosaicos       : BA_MOSAIC_DIR/ — fallback legacy (6 años disponibles).
BA_RAW_DIR    = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML/0_raw/biomas_peru_sol")
BA_MOSAIC_DIR = data_dir / "mosaics_BA"
BA_REGIONS    = ["Peru_Norte", "Peru_Centro", "Peru_Sur"]
BA_VRT_PATH   = processed_dir / "ba_raw_tiles_mosaic.vrt"   # construido una sola vez

# Tamaño de ventana para procesamiento del VRT BA.
# 0.1° × 0.1° a ~10m resolución ≈ 1111×1111 px ≈ 1.2 MB por ventana.
# (vs PROC_TILE_DEG=2.0° que se usa para MapBiomas a ~500m resolución)
BA_PROC_CELL  = 0.1

# Margen espacial alrededor del extent de polígonos al recortar tiles (v7).
# Igual al BUFFER_SIZE_DEG de script_AT_V5 — garantiza cobertura en bordes.
MAPBIOMAS_EXTENT_BUFFER_DEG = 0.005
# Tamaño de la baldosa de proceso en calc_mapbiomas_proportions.
# 2° × 111000/30 ≈ 7400 px → out_image ~55 MB (vs 5.5 GB del ROI completo).
PROC_TILE_DEG = 2.0

WGS84 = CRS.from_epsg(4326)

roi_geom      = box(*ROI_BBOX)
roi_geom_list = [mapping(roi_geom)]

# --- Submuestreo (solo para tests de recursos del cluster) -------------------
# None = produccion completa (100% de eventos).
# Para tests: SAMPLE_FRAC = 0.05 / 0.10 / 0.25
SAMPLE_FRAC = 0.05   # test — 5% de eventos (produccion: None o 1.0)
RANDOM_SEED = 42     # reservado (reproducibilidad si se reactiva SAMPLE_FRAC)

# --- Numero de workers paralelos ---------------------------------------------
# Lee automaticamente el numero de cores asignados por SLURM (-c N).
# Config vf recomendada: -c 6 --mem=32G
#   → 6 workers × ~4 GB + orquestador ~2 GB = ~26 GB pico
#   → 24 años / 6 workers = 4 batches paralelos (~2h estimado)
# Fallback local: 3 workers (sin SLURM_CPUS_PER_TASK en entorno interactivo).
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# --- Filtro de area minima ---------------------------------------------------
# MIN_PIX eliminado en v6.1: se procesan todos los eventos (~2M/mes).
# La escalabilidad se logra sustituyendo pd.groupby por scipy.ndimage stats
# (nivel C) y con escritura mensual directa (streaming) al GPKG.
# Ver _ndimage_mode() y process_burned_areas() para los detalles.


# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


# --- AOI: carga --------------------------------------------------------------
def load_aoi():
    """Carga la geometria AOI desde AOI_PATH o AOI_BBOX."""
    if AOI_PATH is not None:
        aoi_gdf  = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
        aoi_geom = aoi_gdf.geometry.unary_union
        print(f"  AOI cartografica : {Path(AOI_PATH).name} ({len(aoi_gdf)} feature(s))")
        return aoi_geom
    if AOI_BBOX is not None:
        w, s, e, n = AOI_BBOX
        print(f"  AOI cartografica : bbox W={w} S={s} E={e} N={n}")
        return box(w, s, e, n)
    print("  AOI cartografica : None — no se generan capas recortadas")
    return None


# --- Zona climatica ----------------------------------------------------------
def assign_zone_clima(gdf):
    """
    Asigna zona climatica usando representative_point() de cada poligono.
    Garantia de punto interior al poligono. Requiere gdf en EPSG:4326.
    """
    rep_lat    = gdf.geometry.representative_point().y
    conditions = [
        (rep_lat >= -5) & (rep_lat <=  1),
        (rep_lat >= -8) & (rep_lat <  -5),
        (rep_lat <  -8) & (rep_lat >= -20),
    ]
    choices = ["Zone_Equatorial", "Transition_Zone", "South_Zone"]
    return np.select(conditions, choices, default="Not_Specified")




# --- Metadatos de tiles MapBiomas --------------------------------------------
def get_mapbiomas_metadata():
    """Pre-carga bounds, CRS, nodata y número de bandas de cada tile MapBiomas.

    n_bands se usa en calc_mapbiomas_proportions para validar band_idx antes
    de intentar src.read(), evitando BandIndexError silenciosos.
    """
    meta = {}
    for key, path in MAPBIOMAS_TILES.items():
        if not path.exists():
            print(f"  [WARN] Tile MapBiomas no encontrado: {path.name}")
            continue
        with rasterio.open(path) as src:
            meta[key] = {
                "bounds_geom": box(*src.bounds),
                "crs"        : src.crs,
                "nodata"     : src.nodata,
                "n_bands"    : src.count,   # [v7.1.1] necesario para validar band_idx
            }
    return meta


# --- Proporcion clase 12 por poligono y anio ---------------------------------
def calc_mapbiomas_proportions(polygons_gdf, year, mb_meta):
    """
    v7.1.1 — Validación de band_idx y gc.collect fuera del loop interno.

    V7.1.0: bucle 2°×2° de tiles de proceso para acotar RAM.
    V7.1.1:
      - band_idx se valida contra meta["n_bands"] antes de src.read().
        Si el tile tiene menos bandas que band_idx se emite [WARN] explícito
        en lugar de propagar un BandIndexError silencioso (→ NaN).
      - MAPBIOMAS_BANDS se contrasta contra n_bands al inicio para detectar
        desalineación de YEAR_MIN entre este script y los tiles AT.
      - gc.collect() movido fuera del loop de tiles (era innermost → 270×/año).
      - except Exception añade type(e).__name__ para trazabilidad en cluster.

    1. Asignación por representative_point → cada polígono en exactamente
       una celda (sin doble conteo en bordes)
    2. rasterio.windows.from_bounds → lectura de ventana rectangular
       sin crear array de máscara (menos overhead que rio_mask)
    3. out_image por celda: ~7400×7400 px × 1 B = ~55 MB
    4. Una sola zonal_stats(stats=['count','nodata'], all_touched=True)
       con acumulación ponderada multi-tile (FIX v6 conservado)
    """
    if year < YEAR_MIN or year > YEAR_MAX:
        print(f"  [INFO] Año {year} fuera del rango MapBiomas → pct_class12 = NaN")
        return np.full(len(polygons_gdf), np.nan)

    # [v7.2] band_idx usa BAND_YEAR_MIN=1985, no YEAR_MIN=2001.
    # Tiles con 40 bandas (1985-2024): banda 1=1985, banda 17=2001, banda 40=2024.
    # Con YEAR_MIN=2001 (v7.1): band 1 para 2001 → leía año 1985. Incorrecto.
    band_idx = year - BAND_YEAR_MIN + 1
    n        = len(polygons_gdf)
    _sum_n12   = np.zeros(n, dtype=np.float64)
    _sum_total = np.zeros(n, dtype=np.float64)

    if len(mb_meta) == 0:
        print(f"  [ERROR] calc_mapbiomas: ningun tile disponible")
        return np.full(n, np.nan)

    # Verificar cobertura de bandas (n_bands esperado = MAPBIOMAS_BANDS = 40)
    n_bands_set = {m["n_bands"] for m in mb_meta.values()}
    if n_bands_set and not any(nb >= band_idx for nb in n_bands_set):
        print(f"  [ERROR] band_idx={band_idx} (año {year}) excede n_bands "
              f"de todos los tiles {n_bands_set}. "
              f"Verificar BAND_YEAR_MIN ({BAND_YEAR_MIN}) vs tiles.")
        return np.full(n, np.nan)
    if n_bands_set and MAPBIOMAS_BANDS not in n_bands_set:
        print(f"  [WARN] MAPBIOMAS_BANDS={MAPBIOMAS_BANDS} != n_bands tiles "
              f"{n_bands_set}. Verificar BAND_YEAR_MIN.")

    print(f"  MapBiomas: {len(mb_meta)} tiles, banda {band_idx} (año {year})")

    # Puntos representativos para asignación sin doble conteo
    repr_pts = polygons_gdf.geometry.representative_point()

    # Cuadrícula de celdas de proceso sobre el extent de los polígonos
    minx, miny, maxx, maxy = polygons_gdf.total_bounds
    xs = np.arange(np.floor(minx), np.ceil(maxx), PROC_TILE_DEG)
    ys = np.arange(np.floor(miny), np.ceil(maxy), PROC_TILE_DEG)

    n_reads = 0
    for xt in xs:
        for yt in ys:
            proc_tile = box(xt, yt, xt + PROC_TILE_DEG, yt + PROC_TILE_DEG)

            # Asignar polígonos por representative_point (evita doble conteo)
            mask_tile = repr_pts.within(proc_tile)
            if not mask_tile.any():
                continue

            idx_global = np.where(mask_tile.values)[0]
            polys_sub  = polygons_gdf.iloc[idx_global].reset_index(drop=True)

            # Ventana expandida para cobertura de bordes de polígonos
            proc_tile_exp = box(
                xt - MAPBIOMAS_EXTENT_BUFFER_DEG,
                yt - MAPBIOMAS_EXTENT_BUFFER_DEG,
                xt + PROC_TILE_DEG + MAPBIOMAS_EXTENT_BUFFER_DEG,
                yt + PROC_TILE_DEG + MAPBIOMAS_EXTENT_BUFFER_DEG
            )

            for (row, col), meta in mb_meta.items():
                if not proc_tile_exp.intersects(meta["bounds_geom"]):
                    continue

                # [v7.1.1] Validar band_idx antes de abrir el archivo.
                # Evita BandIndexError silencioso que en v7.1.0 era tragado
                # por except Exception → pct_class12 = NaN sin trazabilidad.
                if band_idx > meta["n_bands"]:
                    print(f"  [WARN] Tile ({row},{col}): band_idx={band_idx} "
                          f"> n_bands={meta['n_bands']} — año {year} sin cobertura")
                    continue

                tile_path    = MAPBIOMAS_TILES[(row, col)]
                nodata_class = meta["nodata"] if meta["nodata"] is not None else 0

                try:
                    with rasterio.open(tile_path) as src:
                        raster_crs = src.crs

                        if raster_crs != WGS84:
                            exp_reproj   = (gpd.GeoDataFrame(
                                [0], geometry=[proc_tile_exp], crs=WGS84
                            ).to_crs(raster_crs).geometry.iloc[0])
                            polys_reproj = polys_sub.to_crs(raster_crs)
                        else:
                            exp_reproj   = proc_tile_exp
                            polys_reproj = polys_sub

                        # Lectura por ventana: ~55 MB (vs 5.5 GB con extent ROI)
                        win = rasterio.windows.from_bounds(
                            *exp_reproj.bounds, transform=src.transform
                        ).intersection(
                            rasterio.windows.Window(0, 0, src.width, src.height)
                        )
                        if win.width <= 0 or win.height <= 0:
                            continue

                        out_image     = src.read(band_idx, window=win)
                        out_transform = src.window_transform(win)

                    stats = zonal_stats(
                        polys_reproj, out_image, affine=out_transform,
                        stats=["count", "nodata"],
                        nodata=nodata_class, all_touched=True
                    )
                    del out_image

                    for local_i, s in enumerate(stats):
                        n12     = s.get("count") or 0
                        n_other = s.get("nodata") or 0
                        n_total = n12 + n_other
                        if n_total > 0:
                            g = idx_global[local_i]
                            _sum_n12[g]   += float(n12)
                            _sum_total[g] += float(n_total)

                    del stats
                    n_reads += 1

                except Exception as e:
                    # [v7.1.1] type(e).__name__ distingue BandIndexError /
                    # MemoryError / RasterioIOError en logs del cluster.
                    print(f"  [WARN] {type(e).__name__} tile ({row},{col}) "
                          f"celda({xt:.0f},{yt:.0f}) año {year}: {e}")

            # [v7.1.1] gc.collect() movido fuera del loop de tiles.
            # En v7.1.0 se ejecutaba por cada (tile × celda) ≈ 270 veces/año.
            # Una sola llamada al salir de la celda es suficiente.
            gc.collect()

    print(f"  MapBiomas: {n_reads} lecturas de ventana realizadas")
    proportions = np.full(n, np.nan)
    valid_mask  = _sum_total > 0
    proportions[valid_mask] = np.round(
        _sum_n12[valid_mask] / _sum_total[valid_mask] * 100.0, 2
    )
    return proportions



# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, mb_meta,
                              gdf_result, output_dir, base_name):
    """Recorta y guarda capas cartograficas limitadas a la AOI."""
    if aoi_geom is None:
        return
    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'─'*58}")
    print("  Exportacion cartografica (AOI)")
    print(f"{'─'*58}")

    dem_out = output_dir / f"{base_name}_aoi_dem.tif"
    try:
        with rasterio.open(dem_path) as src:
            aoi_local = (
                gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                .to_crs(src.crs).geometry.iloc[0]
                if src.crs != WGS84 else aoi_geom
            )
            out_img, out_tr = rio_mask(src, [aoi_local], crop=True, all_touched=True)
            out_meta = {**src.meta, "driver": "GTiff", "compress": "lzw",
                        "height": out_img.shape[1], "width": out_img.shape[2],
                        "transform": out_tr}
        with rasterio.open(dem_out, "w", **out_meta) as dst:
            dst.write(out_img)
        print(f"  [OK] DEM              -> {dem_out.name}")
    except Exception as e:
        print(f"  [WARN] DEM clip error: {e}")

    n_mb = 0
    for (r, c), meta in mb_meta.items():
        if not aoi_geom.intersects(meta["bounds_geom"]):
            continue
        mb_out = output_dir / f"{base_name}_aoi_mapbiomas_r{r}c{c}.tif"
        try:
            aoi_local = (
                gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                .to_crs(meta["crs"]).geometry.iloc[0]
                if meta["crs"] != WGS84 else aoi_geom
            )
            with rasterio.open(MAPBIOMAS_TILES[(r, c)]) as src:
                out_img, out_tr = rio_mask(src, [aoi_local], crop=True, all_touched=True)
                out_meta = {**src.meta, "driver": "GTiff", "compress": "lzw",
                            "height": out_img.shape[1], "width": out_img.shape[2],
                            "transform": out_tr}
            with rasterio.open(mb_out, "w", **out_meta) as dst:
                dst.write(out_img)
            n_mb += 1
            print(f"  [OK] MapBiomas r{r}c{c}   -> {mb_out.name}")
        except Exception as e:
            print(f"  [WARN] MapBiomas r{r}c{c} clip error: {e}")

    mask_aoi = gdf_result.geometry.intersects(aoi_geom)
    gdf_aoi  = gdf_result[mask_aoi].reset_index(drop=True)
    n_feats  = len(gdf_aoi)
    shp_aoi  = output_dir / f"{base_name}_aoi_results.shp"
    csv_aoi  = output_dir / f"{base_name}_aoi_results.csv"
    if n_feats > 0:
        gdf_aoi.to_file(shp_aoi)
        gdf_aoi.drop(columns=["geometry"]).to_csv(csv_aoi, index=False,
                                                    encoding="utf-8-sig")
        print(f"  [OK] Resultados AOI   -> {shp_aoi.name} ({n_feats} poligonos)")
    else:
        print("  [INFO] Ningun poligono resultado intersecta la AOI.")
    timer(f"Exportacion cartografica (DEM + {n_mb} tiles MB + resultados)", t)


# ── 1. Carga y filtrado del shapefile de paises ────────────────────────────────
def load_countries(path, adm0_codes, roi_geom):
    t    = time.time()
    pays = gpd.read_file(path)
    t    = timer("load_countries: lectura shapefile", t)
    pays = pays[pays["gaul0_code"].isin(adm0_codes)].copy()
    pays = pays[pays.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    pays = pays.clip(roi_geom).to_crs("EPSG:4326")
    timer("load_countries: filtrado y clip", t)
    return pays.reset_index(drop=True)


# ── 2. Mascara de elevacion ────────────────────────────────────────────────────
def load_elevation_mask(dem_path, target_shape, target_transform, target_crs,
                        threshold=2000):
    """Reprojecta el DEM al grid del BA sin cargar el raster completo."""
    t     = time.time()
    dem_r = np.empty(target_shape, dtype=np.float32)
    with rasterio.open(dem_path) as src:
        rasterio.warp.reproject(
            source=rasterio.band(src, 1),
            destination=dem_r,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=target_transform,
            dst_crs=target_crs,
            resampling=rasterio.warp.Resampling.bilinear,
            src_nodata=src.nodata,
            dst_nodata=np.nan
        )
    t         = timer("load_elevation_mask: reproject al grid BA", t)
    elev_mask = dem_r >= threshold   # NaN >= 2000 → False ✓
    timer("load_elevation_mask: calculo mascara", t)
    return dem_r, elev_mask


# ── 3. Rasterizar paises ───────────────────────────────────────────────────────
def rasterize_countries(countries_gdf, target_shape, target_transform):
    """countries_gdf debe estar ya en el CRS del BA."""
    shapes_iter = (
        (mapping(geom), int(code))
        for geom, code in zip(countries_gdf.geometry, countries_gdf["gaul0_code"])
    )
    return rasterio.features.rasterize(
        shapes=shapes_iter,
        out_shape=target_shape,
        transform=target_transform,
        fill=0,
        dtype=np.int32
    )


# ── 3b. Helper: moda via ndimage.sum ──────────────────────────────────────────
def _ndimage_mode(arr, labeled_arr, index, possible_values):
    """
    Calcula el valor modal de `arr` para cada label en `index`.
    Itera sobre `possible_values` (lista corta) con ndimage.sum a nivel C.
    Evita pd.groupby y sus objetos Python temporales por evento.

    arr             : array 2D de enteros (e.g. cntry_arr)
    labeled_arr     : array 2D de etiquetas (salida de ndimage.label)
    index           : 1D int32 array de IDs de eventos a consultar
    possible_values : lista de valores enteros posibles en arr
    Retorna         : 1D int32 array de misma longitud que index
    """
    n          = len(index)
    best_count = np.zeros(n, dtype=np.float64)
    best_val   = np.zeros(n, dtype=np.int32)
    for val in possible_values:
        counts = np.asarray(
            ndimage.sum((arr == val).view(np.uint8), labeled_arr, index),
            dtype=np.float64
        )
        better             = counts > best_count
        best_count[better] = counts[better]
        best_val[better]   = val
    return best_val


# ── 4. Extraccion vectorizada de eventos (un mes) ──────────────────────────────
def extract_month_events(ba_data, labeled_arr, valid, dem_data, cntry_arr,
                          ba_transform, ba_crs, year, month, num_events):
    """
    v6.1 — Sin filtro MIN_PIX: escala a ~2M eventos/mes en produccion.

    Reemplaza pd.groupby (OOM a 2M grupos, ~4M objetos Python temporales)
    por scipy.ndimage stats vectorizadas (nivel C, RAM constante):

      1. SAMPLE_FRAC sobre TODOS los eventos (sin preseleccion MIN_PIX)
      2. ndimage.median  → BurnDate  (proxy de moda; en BA/MODIS un evento
                           tipicamente tiene 1-2 fechas → median == mode)
      3. ndimage.mean    → Elevation
      4. _ndimage_mode   → ADM0_CODE (itera 5 paises a nivel C)
      5. shapes() sobre sampled_labeled → solo N_keep geometrias
      6. union + GeoDataFrame + area batch (igual que v5.3)

    Peak RAM por mes ≈ 4 × labeled_arr (107 MB) + stats arrays (<50 MB),
    frente a 1-4 GB de objetos Python del groupby anterior.
    Retorna (GeoDataFrame en ba_crs, n_keep) o (None, 0).
    """
    if num_events == 0:
        return None, 0

    all_ids = np.arange(1, num_events + 1, dtype=np.int32)

    # ── SAMPLE_FRAC (modo test) ─────────────────────────────────────────
    if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
        rng       = np.random.default_rng(seed=RANDOM_SEED + year * 100 + month)
        n_keep    = max(1, int(num_events * SAMPLE_FRAC))
        final_ids = np.sort(
            rng.choice(all_ids, size=n_keep, replace=False)
        ).astype(np.int32)
    else:
        final_ids = all_ids
        n_keep    = num_events

    print(f"    [{year}-{month:02d}] {num_events} eventos totales → {n_keep} procesados")

    # ── 1. Stats vectorizadas a nivel C (sin pd.groupby) ───────────────
    # ndimage opera sobre labeled_arr completo en un solo paso; no crea
    # ningun objeto Python por evento. Peak RAM: 1-2 arrays extra (h × w).
    BurnDate  = np.round(
        ndimage.median(ba_data.astype(np.float32), labeled_arr, final_ids)
    ).astype(np.int32)

    Elevation = np.asarray(
        ndimage.mean(dem_data.astype(np.float32), labeled_arr, final_ids),
        dtype=np.float32
    )

    ADM0_CODE = _ndimage_mode(cntry_arr, labeled_arr, final_ids, COUNTRIES_ADM0)

    # ── 2. Geometrias: shapes() sobre eventos seleccionados ─────────────
    sampled_labeled               = np.zeros_like(labeled_arr)   # int32, 107 MB
    sampled_mask                  = np.isin(labeled_arr, final_ids)
    sampled_labeled[sampled_mask] = labeled_arr[sampled_mask]
    del sampled_mask

    geom_by_label = defaultdict(list)
    for geom_dict, label_val in rasterio.features.shapes(
        sampled_labeled,
        mask=(sampled_labeled > 0).astype(np.uint8),
        transform=ba_transform
    ):
        geom_by_label[int(label_val)].append(shape(geom_dict))
    del sampled_labeled

    if not geom_by_label:
        return None, n_keep

    # ── 3. Construir GeoDataFrame ────────────────────────────────────────
    # Lookup O(1): label ID → indice en los arrays de stats.
    id_to_idx = {int(eid): i for i, eid in enumerate(final_ids)}

    records = []
    for eid, geoms in geom_by_label.items():
        i = id_to_idx.get(eid)
        if i is None:
            continue
        # Evitar unary_union innecesario en el caso dominante (pixel aislado)
        geom = geoms[0] if len(geoms) == 1 else unary_union(geoms)
        records.append({
            "geometry":  geom,
            "year":      year,
            "month":     month,
            "BurnDate":  int(BurnDate[i]),
            "Elevation": round(float(Elevation[i]), 1),
            "ADM0_CODE": int(ADM0_CODE[i]),
        })
    del geom_by_label, BurnDate, Elevation, ADM0_CODE, id_to_idx

    if not records:
        return None, n_keep

    gdf_month = gpd.GeoDataFrame(records, crs=ba_crs)
    del records

    # ── 4. Area batch ────────────────────────────────────────────────────
    gdf_utm   = gdf_month.to_crs("EPSG:3857")
    areas_m2  = gdf_utm.geometry.area.values
    del gdf_utm
    gdf_month["area_ha"]  = np.round(areas_m2 / 10_000, 2)
    gdf_month["area_km2"] = np.round(areas_m2 / 1_000_000, 4)

    return gdf_month, n_keep


# ── 5. Spatial join de paises (3 intentos) ────────────────────────────────────
def spatial_join_3attempts(gdf, countries_gdf):
    """
    Spatial join en 3 intentos: intersects → centroide within → nearest.
    Resuelve duplicados priorizando filas donde gaul0_code == ADM0_CODE.
    """
    joined = gdf.sjoin(
        countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
        how="left", predicate="intersects"
    ).drop(columns=["index_right"], errors="ignore")

    # Resolver duplicados: conservar la fila que concuerda con ADM0_CODE
    joined["_match"] = joined["gaul0_code"] == joined["ADM0_CODE"]
    gdf_out = (
        joined
        .sort_values("_match", ascending=False)
        .groupby(level=0)
        .first()
        .reset_index(drop=True)
        .drop(columns=["_match"])
    )
    del joined

    # Intento 2: centroide within para NaN restantes
    mask_nan = gdf_out["gaul0_name"].isna()
    if mask_nan.any():
        print(f"  [WARN] {mask_nan.sum()} features sin pais — centroides")
        tmp = gdf_out[mask_nan].copy()
        tmp["geometry"] = tmp.geometry.centroid
        res = tmp[["geometry"]].sjoin(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
            how="left", predicate="within"
        ).drop(columns=["index_right"], errors="ignore")
        gdf_out.loc[mask_nan, "gaul0_code"] = res["gaul0_code"].values
        gdf_out.loc[mask_nan, "gaul0_name"] = res["gaul0_name"].values

    # Intento 3: nearest neighbor
    mask_nan2 = gdf_out["gaul0_name"].isna()
    if mask_nan2.any():
        print(f"  [WARN] {mask_nan2.sum()} features sin pais — nearest")
        tmp2 = gdf_out[mask_nan2].copy()
        tmp2["geometry"] = tmp2.geometry.centroid
        res2 = tmp2[["geometry"]].sjoin_nearest(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
            how="left"
        ).drop(columns=["index_right"], errors="ignore")
        gdf_out.loc[mask_nan2, "gaul0_code"] = res2["gaul0_code"].values
        gdf_out.loc[mask_nan2, "gaul0_name"] = res2["gaul0_name"].values

    print(f"  NaN tras 3 intentos: {gdf_out['gaul0_name'].isna().sum()}")
    return gdf_out


# ── 6. Procesamiento principal por ano ─────────────────────────────────────────
def process_burned_areas(ba_files_by_year, dem_data, elev_mask,
                          countries_gdf, roi_geom_list, mb_meta, gpkg_path,
                          dem_path=None):
    """
    v7.2.1 — Dos modos según la fuente detectada por discover_ba_years():

    Modo 'mosaic' (fallback, comportamiento v7.1 sin cambios):
        Itera 12 bandas mensuales del mosaico.  Usa dem_data/elev_mask
        pre-computados al grid del mosaico.  Escribe al GPKG mes a mes.

    Modo 'raw' (v7.2.1):
        Llama a _process_raw_tiles_for_year() que procesa cada tile
        de forma independiente (1 fd abierto a la vez, ~4 MB RAM/tile).
        Devuelve un GeoDataFrame anual con eventos ya filtrados por
        elevación.  El resto del pipeline (sjoin, MapBiomas, GPKG) es
        idéntico al modo mosaic.
    """
    col_order   = ["year", "month", "BurnDate", "Elevation", "Zone_Clima",
                   "pct_class12", "area_class12_ha",
                   "ADM0_CODE", "gaul0_code", "gaul0_name",
                   "area_ha", "area_km2", "geometry"]
    first_write = True

    _dem_path = dem_path or (processed_dir / "mosaico_andes_DEM_COG.tif")

    def _doy_to_month(doy, yr):
        try:
            if doy <= 0:
                return 0
            return (pd.Timestamp(year=yr, month=1, day=1)
                    + pd.Timedelta(days=int(doy) - 1)).month
        except Exception:
            return 0

    for year, ba_source in ba_files_by_year.items():
        source_type, source_data = ba_source
        year_start   = time.time()
        year_written = 0
        print(f"\n{'─'*60}")
        src_label = (source_data.name if source_type in ("mosaic", "vrt")
                     else f"{len(source_data)} tiles raw")
        print(f"  Procesando ano {year} [{source_type}]: {src_label}")

        # ══════════════════════════════════════════════════════════════
        # MODO VRT — ventanas sobre VRT multi-banda (v7.2.2, prioritario)
        # ══════════════════════════════════════════════════════════════
        if source_type == "vrt":
            t = time.time()
            gdf_year = _process_vrt_year(year, source_data, _dem_path)
            timer(f"{year}: extraccion VRT (ventanas {BA_PROC_CELL}°)", t)

            if gdf_year is None or len(gdf_year) == 0:
                print(f"  [WARN] Sin eventos VRT para año {year}")
                timer(f"Ano {year} finalizado (sin datos)", year_start)
                continue

            gdf_year["Zone_Clima"] = assign_zone_clima(gdf_year)
            t = time.time()
            gdf_year = spatial_join_3attempts(gdf_year, countries_gdf)
            timer(f"{year}: sjoin países", t)

            if COUNTRY_FILTER:
                n_antes  = len(gdf_year)
                gdf_year = gdf_year[
                    gdf_year["gaul0_name"] == COUNTRY_FILTER
                ].reset_index(drop=True)
                print(f"  [FILTER] {COUNTRY_FILTER}: "
                      f"{len(gdf_year)}/{n_antes} retenidos")

            if len(gdf_year) == 0:
                del gdf_year
                timer(f"Ano {year} finalizado (sin datos Peru)", year_start)
                continue

            gdf_year["month"] = gdf_year["BurnDate"].apply(
                lambda d: _doy_to_month(d, year)
            )
            if "ADM0_CODE" not in gdf_year.columns:
                gdf_year["ADM0_CODE"] = gdf_year.get("gaul0_code", np.nan)

            t = time.time()
            gdf_year["pct_class12"] = calc_mapbiomas_proportions(
                gdf_year, year, mb_meta
            )
            timer(f"{year}: MapBiomas pct_class12", t)

            gdf_year["area_class12_ha"] = (
                gdf_year["pct_class12"] / 100 * gdf_year["area_ha"]
            ).round(2)

            n_c12    = len(gdf_year)
            gdf_year = gdf_year[
                gdf_year["area_class12_ha"].isna() |
                (gdf_year["area_class12_ha"] != 0)
            ].reset_index(drop=True)
            n_filt = n_c12 - len(gdf_year)
            if n_filt > 0:
                print(f"  [FILTER] area_class12_ha!=0: "
                      f"{len(gdf_year)}/{n_c12} retenidos")

            if len(gdf_year) == 0:
                del gdf_year
                continue

            cols     = [c for c in col_order if c in gdf_year.columns]
            gdf_year = gdf_year[cols]

            t         = time.time()
            gpkg_mode = "w" if first_write else "a"
            gdf_year.to_file(gpkg_path, driver="GPKG", mode=gpkg_mode)
            first_write  = False
            year_written = len(gdf_year)
            print(f"  [OK] GPKG ({gpkg_mode.upper()}) "
                  f"{year}-anual → {year_written} eventos")
            timer(f"{year}: escritura GPKG", t)
            del gdf_year
            gc.collect()

        # ══════════════════════════════════════════════════════════════
        # MODO RAW — tile-by-tile (fallback, v7.2.1)
        # ══════════════════════════════════════════════════════════════
        elif source_type == "raw":
            t = time.time()
            gdf_year = _process_raw_tiles_for_year(year, source_data, _dem_path)
            timer(f"{year}: extraccion tile-by-tile", t)

            if gdf_year is None or len(gdf_year) == 0:
                print(f"  [WARN] Sin eventos raw para año {year}")
                timer(f"Ano {year} finalizado (sin datos)", year_start)
                continue

            # Zone_Clima + sjoin países
            gdf_year["Zone_Clima"] = assign_zone_clima(gdf_year)
            t = time.time()
            gdf_year = spatial_join_3attempts(gdf_year, countries_gdf)
            timer(f"{year}: sjoin países", t)

            # COUNTRY_FILTER
            if COUNTRY_FILTER:
                n_antes  = len(gdf_year)
                gdf_year = gdf_year[
                    gdf_year["gaul0_name"] == COUNTRY_FILTER
                ].reset_index(drop=True)
                print(f"  [FILTER] {COUNTRY_FILTER}: "
                      f"{len(gdf_year)}/{n_antes} retenidos")

            if len(gdf_year) == 0:
                del gdf_year
                timer(f"Ano {year} finalizado (sin datos Peru)", year_start)
                continue

            # month desde BurnDate (DOY)
            gdf_year["month"] = gdf_year["BurnDate"].apply(
                lambda d: _doy_to_month(d, year)
            )
            # ADM0_CODE desde gaul0_code (equivalente en modo raw)
            if "ADM0_CODE" not in gdf_year.columns:
                gdf_year["ADM0_CODE"] = gdf_year.get("gaul0_code", np.nan)

            # MapBiomas
            t = time.time()
            gdf_year["pct_class12"] = calc_mapbiomas_proportions(
                gdf_year, year, mb_meta
            )
            timer(f"{year}: MapBiomas pct_class12", t)

            gdf_year["area_class12_ha"] = (
                gdf_year["pct_class12"] / 100 * gdf_year["area_ha"]
            ).round(2)

            # Filtro area_class12_ha != 0
            n_c12    = len(gdf_year)
            gdf_year = gdf_year[
                gdf_year["area_class12_ha"].isna() |
                (gdf_year["area_class12_ha"] != 0)
            ].reset_index(drop=True)
            n_filt = n_c12 - len(gdf_year)
            if n_filt > 0:
                print(f"  [FILTER] area_class12_ha!=0: "
                      f"{len(gdf_year)}/{n_c12} retenidos")

            if len(gdf_year) == 0:
                del gdf_year
                continue

            cols     = [c for c in col_order if c in gdf_year.columns]
            gdf_year = gdf_year[cols]

            t         = time.time()
            gpkg_mode = "w" if first_write else "a"
            gdf_year.to_file(gpkg_path, driver="GPKG", mode=gpkg_mode)
            first_write   = False
            year_written  = len(gdf_year)
            print(f"  [OK] GPKG ({gpkg_mode.upper()}) "
                  f"{year}-anual → {year_written} eventos")
            timer(f"{year}: escritura GPKG", t)

            del gdf_year
            gc.collect()

        # ══════════════════════════════════════════════════════════════
        # MODO MOSAIC — loop mensual (comportamiento v7.1 intacto)
        # ══════════════════════════════════════════════════════════════
        else:
            t = time.time()
            with rasterio.open(source_data) as src:
                ba_ref, ba_transform = rasterio.mask.mask(
                    src, roi_geom_list, crop=True, filled=True,
                    nodata=0, indexes=[1]
                )
                ba_crs = src.crs
            h, w = ba_ref.shape[1], ba_ref.shape[2]
            del ba_ref
            timer(f"{year}: metadata BA ({h}x{w})", t)

            if elev_mask.shape != (h, w):
                print(f"  [WARN] Shape mismatch DEM {elev_mask.shape} "
                      f"vs BA {(h, w)}")
                continue

            t = time.time()
            cntry_arr = rasterize_countries(
                countries_gdf.to_crs(ba_crs), (h, w), ba_transform
            )
            timer(f"{year}: rasterizacion paises", t)

            labeled_out = np.empty((h, w), dtype=np.int32)

            with rasterio.open(source_data) as src:
                for month in range(1, 13):
                    ba_raw, _ = rasterio.mask.mask(
                        src, roi_geom_list, crop=True, filled=True,
                        nodata=0, indexes=[month]
                    )
                    ba_data = ba_raw[0].astype(np.int16)
                    del ba_raw

                    valid = (ba_data > 0) & elev_mask
                    if not valid.any():
                        del ba_data, valid
                        continue

                    t = time.time()
                    ndimage.label(valid, structure=np.ones((3, 3), dtype=int),
                                  output=labeled_out)
                    n_evt = int(labeled_out.max())
                    timer(f"{year}-{month:02d}: etiquetado ({n_evt} eventos)", t)

                    if n_evt == 0:
                        del ba_data, valid
                        continue

                    t = time.time()
                    gdf_month, n_kept = extract_month_events(
                        ba_data, labeled_out, valid, dem_data, cntry_arr,
                        ba_transform, ba_crs, year, month, n_evt
                    )
                    timer(f"{year}-{month:02d}: extraccion ({n_kept})", t)
                    del ba_data, valid
                    gc.collect()

                    if gdf_month is None:
                        continue

                    gdf_month = gdf_month.to_crs("EPSG:4326")
                    t = time.time()
                    gdf_month = spatial_join_3attempts(gdf_month, countries_gdf)
                    gdf_month["Zone_Clima"] = assign_zone_clima(gdf_month)
                    timer(f"{year}-{month:02d}: sjoin + Zone_Clima", t)

                    if COUNTRY_FILTER:
                        n_antes   = len(gdf_month)
                        gdf_month = gdf_month[
                            gdf_month["gaul0_name"] == COUNTRY_FILTER
                        ].reset_index(drop=True)
                        print(f"  [FILTER] {COUNTRY_FILTER}: "
                              f"{len(gdf_month)}/{n_antes} retenidos")

                    if len(gdf_month) == 0:
                        del gdf_month
                        continue

                    t = time.time()
                    gdf_month["pct_class12"] = calc_mapbiomas_proportions(
                        gdf_month, year, mb_meta
                    )
                    timer(f"{year}-{month:02d}: MapBiomas", t)

                    gdf_month["area_class12_ha"] = (
                        gdf_month["pct_class12"] / 100 * gdf_month["area_ha"]
                    ).round(2)

                    n_c12     = len(gdf_month)
                    gdf_month = gdf_month[
                        gdf_month["area_class12_ha"].isna() |
                        (gdf_month["area_class12_ha"] != 0)
                    ].reset_index(drop=True)
                    if n_c12 - len(gdf_month) > 0:
                        print(f"  [FILTER] area_class12_ha!=0: "
                              f"{len(gdf_month)}/{n_c12}")

                    if len(gdf_month) == 0:
                        del gdf_month
                        continue

                    cols      = [c for c in col_order if c in gdf_month.columns]
                    gdf_month = gdf_month[cols]

                    t         = time.time()
                    gpkg_mode = "w" if first_write else "a"
                    gdf_month.to_file(gpkg_path, driver="GPKG", mode=gpkg_mode)
                    first_write   = False
                    year_written += len(gdf_month)
                    print(f"  [OK] GPKG ({gpkg_mode.upper()}) "
                          f"{year}-{month:02d} → {len(gdf_month)} eventos")
                    timer(f"{year}-{month:02d}: escritura GPKG", t)

                    del gdf_month
                    gc.collect()

            del labeled_out, cntry_arr
            gc.collect()

        print(f"  {year}: {year_written} eventos escritos")
        timer(f"Ano {year} finalizado", year_start)

    print(f"\n{'═'*60}")


# ── Auxiliares del orquestador ─────────────────────────────────────────────────
def discover_ba_years(years):
    """
    v7.2.2 — Detecta fuentes BA en orden de prioridad:

    1. VRT  : BA_VRT_PATH (construido una sola vez por _ensure_ba_tiles_vrt).
              Un solo archivo open por año, acceso por ventana → sin TIMEOUT.
    2. Raw  : BA_RAW_DIR/{región}/*.tif (fallback — lento: 137K opens/año).
    3. Mosaic: BA_MOSAIC_DIR/mosaic_{year}_*.tif (fallback legacy, 6 años).
    """
    sources = {}

    # --- Recolectar tiles raw (necesario para VRT y para fallback raw) --------
    raw_tiles_all = []
    for region in BA_REGIONS:
        rd = BA_RAW_DIR / region
        if rd.exists():
            raw_tiles_all.extend(sorted(rd.glob("*.tif")))

    # --- Prioridad 1: VRT -----------------------------------------------------
    if raw_tiles_all:
        vrt_path = _ensure_ba_tiles_vrt(raw_tiles_all)
        if vrt_path is not None:
            try:
                with rasterio.open(vrt_path) as src:
                    n_bands = src.count
                for yr in years:
                    if yr < YEAR_MIN or yr > YEAR_MAX:
                        continue
                    b = yr - BAND_YEAR_MIN + 1
                    if 1 <= b <= n_bands:
                        sources[yr] = ("vrt", vrt_path)
            except Exception as e:
                print(f"  [WARN] No se pudo leer VRT: {e}")

    # --- Prioridad 2: raw tile-by-tile (fallback lento) -----------------------
    if raw_tiles_all:
        raw_band_count = 0
        if not sources:   # solo si VRT falló
            try:
                with rasterio.open(raw_tiles_all[0]) as src:
                    raw_band_count = src.count
                print(f"  BA raw tiles: {len(raw_tiles_all)} tiles en "
                      f"{sum(1 for r in BA_REGIONS if (BA_RAW_DIR/r).exists())} regiones "
                      f"({raw_band_count} bandas / tile)")
            except Exception as e:
                print(f"  [WARN] No se pudo leer tiles raw BA: {e}")

            for yr in years:
                if yr in sources or yr < YEAR_MIN or yr > YEAR_MAX:
                    continue
                b = yr - BAND_YEAR_MIN + 1
                if raw_tiles_all and 1 <= b <= raw_band_count:
                    sources[yr] = ("raw", raw_tiles_all)

    # --- Prioridad 3: mosaicos (fallback legacy) ------------------------------
    for yr in years:
        if yr in sources or yr < YEAR_MIN or yr > YEAR_MAX:
            continue
        if BA_MOSAIC_DIR.exists():
            matches = sorted(BA_MOSAIC_DIR.glob(f"*{yr}*.tif"))
            if matches:
                sources[yr] = ("mosaic", matches[0])

    n_vrt    = sum(1 for v in sources.values() if v[0] == "vrt")
    n_raw    = sum(1 for v in sources.values() if v[0] == "raw")
    n_mosaic = sum(1 for v in sources.values() if v[0] == "mosaic")
    print(f"  Anos BA disponibles: {sorted(sources.keys())} "
          f"(vrt={n_vrt}, raw={n_raw}, mosaic={n_mosaic})")
    return sources


def _ensure_ba_tiles_vrt(raw_tile_paths):
    """
    v7.2.4 — Valida el VRT existente con XML (sin rasterio.open).

    v7.2.2/7.2.3 abrían el VRT con rasterio.open() para verificar resolución
    y bandas → si el VRT está a 10m (demasiado grande), GDAL lanza
    CPLE_AppDefinedError antes de entrar al except Exception, crasheando el
    orquestador.

    v7.2.4: parsea el XML del VRT directamente (xml.etree) para leer
    GeoTransform y contar VRTRasterBand sin invocar GDAL. Solo se llama a
    rasterio.open() una vez, sobre el VRT NUEVO recién construido (que ya tiene
    la resolución correcta gracias a -tr).

    Flujo:
    1. Si existe VRT: leer GeoTransform y bandas desde XML.
       - Correcto (bandas=40, res≈500m) → retornar Path sin rebuild.
       - Incorrecto (res=10m u otro) → eliminar y continuar.
    2. Construir nuevo VRT con gdalbuildvrt -tr res_x res_y -r average.
    3. Validar VRT construido nuevamente por XML (sin GDAL).
    4. Retornar Path o None.
    """
    import subprocess
    import xml.etree.ElementTree as ET

    vrt_path = BA_VRT_PATH

    # ── Resolución objetivo desde mosaicos existentes ─────────────────────────
    res_x = res_y = 0.004491576420598   # ~500m (MODIS), valor por defecto
    if BA_MOSAIC_DIR.exists():
        for m in sorted(BA_MOSAIC_DIR.glob("*.tif")):
            try:
                with rasterio.open(m) as src:
                    res_x = abs(src.transform.a)
                    res_y = abs(src.transform.e)
                break
            except Exception:
                continue

    # ── Helper: inspeccionar VRT por XML ─────────────────────────────────────
    def _vrt_xml_info(path):
        """Devuelve (n_bandas, res_px) o (0, 0) si no parseable."""
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            gt   = root.findtext("GeoTransform", "")
            n_b  = len(root.findall("VRTRasterBand"))
            if not gt:
                return 0, 0.0
            vals = [float(v.strip()) for v in gt.split(",")]
            return n_b, abs(vals[1])   # n_bandas, ancho_pixel
        except Exception:
            return 0, 0.0

    # ── 1. Reutilizar si correcto ─────────────────────────────────────────────
    if vrt_path.exists():
        n_b, vrt_res = _vrt_xml_info(vrt_path)
        tol = res_x * 0.02            # 2% de tolerancia en resolución
        if n_b == MAPBIOMAS_BANDS and abs(vrt_res - res_x) < tol:
            print(f"  VRT BA: reutilizando {vrt_path.name} "
                  f"({n_b} bandas, res={vrt_res:.6f}°)")
            return vrt_path
        else:
            print(f"  VRT BA existente: {n_b} bandas / res={vrt_res:.8f}° "
                  f"(esperado {MAPBIOMAS_BANDS} bandas / res~{res_x:.6f}°) "
                  f"— eliminando y reconstruyendo")
            vrt_path.unlink(missing_ok=True)

    # ── 2. Construir nuevo VRT ────────────────────────────────────────────────
    list_file = vrt_path.parent / "ba_raw_tiles_list.txt"
    try:
        with open(list_file, "w") as fh:
            for p in raw_tile_paths:
                fh.write(f"{p}\n")

        cmd = [
            "gdalbuildvrt",
            "-tr", str(res_x), str(res_y),
            "-r",  "average",
            "-overwrite",
            str(vrt_path),
            "--optfile", str(list_file)
        ]
        print(f"  Construyendo VRT BA: {len(raw_tile_paths)} tiles → "
              f"res {res_x:.6f}° (~500m) — puede tardar 5-25 min (solo una vez)...")
        t0     = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=2400)
        elapsed = time.time() - t0

        if result.returncode != 0:
            print(f"  [ERROR] gdalbuildvrt ({elapsed:.0f}s): "
                  f"{result.stderr[:400]}")
            return None

        # ── 3. Validar por XML (sin GDAL open) ───────────────────────────────
        n_b, vrt_res = _vrt_xml_info(vrt_path)
        print(f"  [OK] VRT BA en {elapsed:.0f}s → {vrt_path.name} "
              f"({n_b} bandas, res={vrt_res:.6f}°)")

        if n_b == 0:
            print("  [WARN] No se pudo validar VRT por XML — usando igualmente")
        return vrt_path

    except subprocess.TimeoutExpired:
        print("  [ERROR] gdalbuildvrt superó el timeout (2400s)")
        return None
    except FileNotFoundError:
        print("  [ERROR] gdalbuildvrt no encontrado en PATH. "
              "Verifica que GDAL esté activo en el entorno conda.")
        return None
    finally:
        if list_file.exists():
            list_file.unlink()


def _process_vrt_year(year, vrt_path, dem_path):
    """
    v7.2.2 — Extrae eventos de un año desde el VRT multi-banda.

    Lectura por ventanas BA_PROC_CELL × BA_PROC_CELL (0.1°):
    - ~1111×1111 px × 1 B ≈ 1.2 MB por ventana (manejable)
    - ROI 20°×21° → ~42 000 ventanas; la mayoría sin datos → skip inmediato
    - DEM se reprojecta una vez por ventana con data (~4 MB)
    - ndimage.label + shapes solo en ventanas con píxeles válidos

    1 descriptor de archivo abierto a la vez (VRT + DEM) vs 137K en v7.2.1.

    Returns GeoDataFrame (WGS84) o None.
    """
    band_idx = year - BAND_YEAR_MIN + 1
    roi_minx, roi_miny, roi_maxx, roi_maxy = ROI_BBOX
    structure  = np.ones((3, 3), dtype=int)
    xs = np.arange(np.floor(roi_minx), np.ceil(roi_maxx), BA_PROC_CELL)
    ys = np.arange(np.floor(roi_miny), np.ceil(roi_maxy), BA_PROC_CELL)

    all_gdfs  = []
    n_cells   = 0   # ventanas con al menos un píxel válido
    n_events  = 0

    with rasterio.open(vrt_path) as ba_src, \
         rasterio.open(dem_path) as dem_src:

        vrt_crs = ba_src.crs
        vrt_win_full = rasterio.windows.Window(0, 0, ba_src.width, ba_src.height)

        for xt in xs:
            for yt in ys:
                cell_bounds = (xt, yt,
                               xt + BA_PROC_CELL, yt + BA_PROC_CELL)

                # Ventana sobre el VRT
                try:
                    win = rasterio.windows.from_bounds(
                        *cell_bounds, transform=ba_src.transform
                    ).intersection(vrt_win_full)
                except Exception:
                    continue
                if win.width <= 0 or win.height <= 0:
                    continue

                # Leer banda del año
                try:
                    ba_data = ba_src.read(band_idx, window=win)
                except Exception:
                    continue

                if not (ba_data > 0).any():
                    del ba_data
                    continue

                cell_transform = ba_src.window_transform(win)
                cell_shape     = ba_data.shape

                # DEM para esta ventana
                dem_tile = np.full(cell_shape, np.nan, dtype=np.float32)
                try:
                    rasterio.warp.reproject(
                        source        = rasterio.band(dem_src, 1),
                        destination   = dem_tile,
                        src_transform = dem_src.transform,
                        src_crs       = dem_src.crs,
                        dst_transform = cell_transform,
                        dst_crs       = vrt_crs,
                        resampling    = rasterio.warp.Resampling.bilinear,
                        src_nodata    = dem_src.nodata,
                        dst_nodata    = np.nan
                    )
                except Exception:
                    pass  # continuar sin filtro altitudinal si DEM falla

                valid = (ba_data > 0) & (dem_tile >= ELEV_THRESHOLD)
                del dem_tile

                if not valid.any():
                    del ba_data, valid
                    continue

                n_cells += 1

                # Etiquetar
                labeled = np.zeros(cell_shape, dtype=np.int32)
                ndimage.label(valid, structure=structure, output=labeled)
                n_evt = int(labeled.max())
                del valid

                if n_evt == 0:
                    del ba_data, labeled
                    continue

                # SAMPLE_FRAC
                all_ids = np.arange(1, n_evt + 1, dtype=np.int32)
                if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
                    rng    = np.random.default_rng(
                        seed=RANDOM_SEED + year * 100000
                             + int(xt * 1000) + int(yt * 10)
                    )
                    n_keep = max(1, int(n_evt * SAMPLE_FRAC))
                    ids    = np.sort(
                        rng.choice(all_ids, size=n_keep, replace=False)
                    ).astype(np.int32)
                else:
                    ids = all_ids

                # Stats vectorizadas
                BurnDate  = np.round(
                    ndimage.median(ba_data.astype(np.float32), labeled, ids)
                ).astype(np.int32)

                # Geometrías (solo eventos seleccionados)
                sampled          = np.zeros(cell_shape, dtype=np.int32)
                sampled[np.isin(labeled, ids)] = labeled[np.isin(labeled, ids)]
                del ba_data, labeled

                geom_by_label = defaultdict(list)
                for geom_dict, lv in rasterio.features.shapes(
                    sampled,
                    mask=(sampled > 0).astype(np.uint8),
                    transform=cell_transform
                ):
                    geom_by_label[int(lv)].append(shape(geom_dict))
                del sampled

                id_to_idx = {int(eid): i for i, eid in enumerate(ids)}
                records   = []
                for eid, geoms in geom_by_label.items():
                    i = id_to_idx.get(eid)
                    if i is None:
                        continue
                    geom = (geoms[0] if len(geoms) == 1
                            else unary_union(geoms))
                    records.append({
                        "geometry" : geom,
                        "year"     : year,
                        "BurnDate" : int(BurnDate[i]),
                    })
                del geom_by_label, BurnDate, id_to_idx

                if not records:
                    del records
                    continue

                cell_gdf = gpd.GeoDataFrame(records, crs=vrt_crs)
                del records
                if vrt_crs != WGS84:
                    cell_gdf = cell_gdf.to_crs(WGS84)

                utm      = cell_gdf.to_crs("EPSG:3857")
                areas_m2 = utm.geometry.area.values
                del utm
                cell_gdf["area_ha"]  = np.round(areas_m2 / 10_000,   2)
                cell_gdf["area_km2"] = np.round(areas_m2 / 1_000_000, 4)

                n_events += len(cell_gdf)
                all_gdfs.append(cell_gdf)
                del cell_gdf
                gc.collect()

    print(f"  BA VRT año {year}: {n_cells} celdas activas → {n_events} eventos")

    if not all_gdfs:
        return None

    gdf = gpd.GeoDataFrame(
        pd.concat(all_gdfs, ignore_index=True),
        geometry="geometry", crs=WGS84
    )
    del all_gdfs
    gc.collect()
    return gdf


def _parse_tile_bounds_from_name(path):
    """
    Extrae bounds (minx, miny, maxx, maxy) del nombre del tile SIN abrir el archivo.
    Pattern: ...Lat{lat1}to{lat2}_Lon{lon1}to{lon2}.tif
    e.g.:  Peru_Norte_T0001_Lat-010to-009_Lon-080to-079.tif
    Returns (minx, miny, maxx, maxy) o None si el nombre no matchea.
    """
    import re
    m = re.search(r'Lat([+-]?\d+)to([+-]?\d+)_Lon([+-]?\d+)to([+-]?\d+)',
                  path.stem)
    if not m:
        return None
    lat1, lat2 = int(m[1]), int(m[2])
    lon1, lon2 = int(m[3]), int(m[4])
    return (min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))


def _process_raw_tiles_for_year(year, raw_tile_paths, dem_path):
    """
    v7.2.1 — Procesa tiles raw BA uno por uno, SIN merge global.

    El enfoque anterior (_merge_raw_tiles_for_year + rasterio.merge) fallaba con
    137K tiles porque:
      - Too many open files : rasterio.merge abría todos los descriptores a la vez
      - OUT_OF_MEMORY      : el array fusionado (~10m resolución × ROI completo)
                             habría requerido ~200 GB de RAM

    Este enfoque abre UNO a la vez:

    Por cada tile:
      1. Pre-filtro espacial por bounds en el nombre → sin abrir ningún archivo
      2. Abrir tile, leer banda (year - BAND_YEAR_MIN + 1), cerrar inmediatamente
      3. Reproyectar DEM al grid del tile (tile ~0.3 MP → rápido, in-place)
      4. Máscara válida = (ba > 0) & (dem >= ELEV_THRESHOLD)
      5. Si ningún píxel válido → skip inmediato
      6. ndimage.label → n eventos
      7. SAMPLE_FRAC por tile (seed determinista: RANDOM_SEED + year*1000 + tile_idx)
      8. ndimage.median(BurnDate) + ndimage.mean(Elevation) + shapes()
      9. GeoDataFrame del tile → acumular

    RAM pico por tile: ~3 × 557 × 558 × 4 B ≈ 4 MB (vs 200 GB del merge).
    File descriptors: máximo 1 abierto a la vez (vs 137K simultáneos).

    Returns GeoDataFrame en WGS84 o None si no hay eventos en el ROI.
    """
    band_idx = year - BAND_YEAR_MIN + 1
    roi_minx, roi_miny, roi_maxx, roi_maxy = ROI_BBOX
    structure = np.ones((3, 3), dtype=int)

    all_gdfs   = []
    n_roi      = 0   # tiles que pasan el pre-filtro de ROI
    n_events   = 0   # total eventos acumulados

    with rasterio.open(dem_path) as dem_src:

        for tile_idx, tile_path in enumerate(raw_tile_paths):

            # 1. Pre-filtro por nombre — 0 file descriptors
            bounds = _parse_tile_bounds_from_name(tile_path)
            if bounds is not None:
                t_minx, t_miny, t_maxx, t_maxy = bounds
                if (t_maxx <= roi_minx or t_minx >= roi_maxx or
                        t_maxy <= roi_miny or t_miny >= roi_maxy):
                    continue
            n_roi += 1

            # 2. Leer banda (abrir + cerrar de inmediato)
            try:
                with rasterio.open(tile_path) as src:
                    if band_idx > src.count:
                        continue
                    ba_data        = src.read(band_idx)
                    tile_transform = src.transform
                    tile_crs       = src.crs
                    tile_shape     = ba_data.shape
            except Exception as e:
                print(f"  [WARN] {type(e).__name__} {tile_path.name}: {e}")
                continue

            # Salida rápida si no hay datos quemados
            if not (ba_data > 0).any():
                del ba_data
                continue

            # 3. Reproyectar DEM al grid del tile
            dem_tile = np.full(tile_shape, np.nan, dtype=np.float32)
            try:
                rasterio.warp.reproject(
                    source        = rasterio.band(dem_src, 1),
                    destination   = dem_tile,
                    src_transform = dem_src.transform,
                    src_crs       = dem_src.crs,
                    dst_transform = tile_transform,
                    dst_crs       = tile_crs,
                    resampling    = rasterio.warp.Resampling.bilinear,
                    src_nodata    = dem_src.nodata,
                    dst_nodata    = np.nan
                )
            except Exception:
                pass  # Si DEM falla, procesar sin filtro altitudinal

            # 4. Máscara válida (NaN >= threshold → False ✓)
            valid = (ba_data > 0) & (dem_tile >= ELEV_THRESHOLD)
            if not valid.any():
                del ba_data, dem_tile, valid
                continue

            # 5. Etiquetado
            labeled = np.zeros(tile_shape, dtype=np.int32)
            ndimage.label(valid, structure=structure, output=labeled)
            n_evt = int(labeled.max())
            del valid

            if n_evt == 0:
                del ba_data, dem_tile, labeled
                continue

            # 6. SAMPLE_FRAC por tile
            all_ids = np.arange(1, n_evt + 1, dtype=np.int32)
            if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
                rng    = np.random.default_rng(
                    seed=RANDOM_SEED + year * 10000 + tile_idx
                )
                n_keep = max(1, int(n_evt * SAMPLE_FRAC))
                ids    = np.sort(
                    rng.choice(all_ids, size=n_keep, replace=False)
                ).astype(np.int32)
            else:
                ids    = all_ids
                n_keep = n_evt

            # 7. Stats vectorizadas (nivel C)
            BurnDate  = np.round(
                ndimage.median(ba_data.astype(np.float32), labeled, ids)
            ).astype(np.int32)
            Elevation = np.asarray(
                ndimage.mean(dem_tile.astype(np.float32), labeled, ids),
                dtype=np.float32
            )
            del ba_data, dem_tile

            # 8. Geometrías (solo eventos seleccionados)
            sampled               = np.zeros(tile_shape, dtype=np.int32)
            mask_s                = np.isin(labeled, ids)
            sampled[mask_s]       = labeled[mask_s]
            del labeled, mask_s

            geom_by_label = defaultdict(list)
            for geom_dict, lv in rasterio.features.shapes(
                sampled,
                mask=(sampled > 0).astype(np.uint8),
                transform=tile_transform
            ):
                geom_by_label[int(lv)].append(shape(geom_dict))
            del sampled

            id_to_idx = {int(eid): i for i, eid in enumerate(ids)}
            records   = []
            for eid, geoms in geom_by_label.items():
                i = id_to_idx.get(eid)
                if i is None:
                    continue
                geom = geoms[0] if len(geoms) == 1 else unary_union(geoms)
                records.append({
                    "geometry" : geom,
                    "year"     : year,
                    "BurnDate" : int(BurnDate[i]),
                    "Elevation": round(float(Elevation[i]), 1),
                })
            del geom_by_label, BurnDate, Elevation, id_to_idx

            if not records:
                del records
                continue

            tile_gdf = gpd.GeoDataFrame(records, crs=tile_crs)
            del records
            if tile_crs != WGS84:
                tile_gdf = tile_gdf.to_crs(WGS84)

            # Área en batch
            utm      = tile_gdf.to_crs("EPSG:3857")
            areas_m2 = utm.geometry.area.values
            del utm
            tile_gdf["area_ha"]  = np.round(areas_m2 / 10_000,   2)
            tile_gdf["area_km2"] = np.round(areas_m2 / 1_000_000, 4)

            n_events += len(tile_gdf)
            all_gdfs.append(tile_gdf)
            del tile_gdf
            gc.collect()

    print(f"  BA raw año {year}: {n_roi} tiles en ROI → {n_events} eventos")

    if not all_gdfs:
        return None

    gdf = gpd.GeoDataFrame(
        pd.concat(all_gdfs, ignore_index=True),
        geometry="geometry", crs=WGS84
    )
    del all_gdfs
    gc.collect()
    return gdf


def _preprocess_only():
    """
    v7 — Sin gdalwarp: valida que los tiles MapBiomas originales existen.
    El orquestador lo llama antes del pool como check de integridad.
    """
    t = time.time()
    print("  Paso 0: validando tiles MapBiomas...")
    mb_meta = get_mapbiomas_metadata()
    n_ok = len(mb_meta)
    print(f"  Tiles MapBiomas disponibles: {n_ok}/9")
    if n_ok == 0:
        print("  [ERROR] Ningun tile MapBiomas encontrado. Verificar mapbiomas_dir.")
        return False
    timer("Validacion MapBiomas", t)
    return True


def merge_and_export(run_tag):
    """
    Combina los GPKGs anuales generados por los workers en el resultado final.
    Exporta GPKG consolidado + SHP + CSV.
    """
    t = time.time()
    print(f"\n{'─'*58}")
    print("  Merge de resultados anuales")
    print(f"{'─'*58}")

    year_paths = [
        (y, test_dir / f"BurnedAreas_MODIS_V7_1_{run_tag}_{y}.gpkg")
        for y in YEARS_TEST
    ]
    existing = [(y, p) for y, p in year_paths if p.exists()]

    if not existing:
        print("  [WARN] No se encontraron GPKGs anuales para merge.")
        return None

    missing = [y for y, p in year_paths if not p.exists()]
    if missing:
        print(f"  [WARN] Anos sin GPKG (fallaron): {missing}")

    print(f"  Cargando {len(existing)} GPKGs...")
    gdfs = [gpd.read_file(p) for _, p in existing]
    # [FIX v6] pd.concat puede perder el CRS al combinar GeoDataFrames
    # (mismo problema documentado en process_burned_areas). Envolver en
    # gpd.GeoDataFrame() explicito garantiza que .to_file() y .to_crs()
    # funcionen correctamente en el GDF consolidado.
    gdf = gpd.GeoDataFrame(
        pd.concat(gdfs, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326"
    )
    del gdfs
    gc.collect()

    base     = f"BurnedAreas_MODIS_V7_{run_tag}"   # [FIX v6] era V5 por error
    gpkg_out = test_dir   / f"{base}.gpkg"
    shp_out  = output_dir / f"{base}.shp"
    csv_out  = output_dir / f"{base}.csv"
    os.makedirs(output_dir, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    gdf.to_file(gpkg_out, driver="GPKG")
    gdf.to_file(shp_out)
    gdf.drop(columns=["geometry"]).to_csv(csv_out, index=False, encoding="utf-8-sig")

    print(f"  [OK] GPKG → {gpkg_out.name}")
    print(f"  [OK] SHP  → {shp_out.name}")
    print(f"  [OK] CSV  → {csv_out.name}")
    timer(f"Merge ({len(gdf)} features totales)", t)
    return gdf


# ── 7. Orquestador principal ───────────────────────────────────────────────────
def run_pipeline(worker_mode=False):
    """
    worker_mode=False : pipeline completo (1 proceso, todos los anos en YEARS_TEST).
    worker_mode=True  : procesa solo YEARS_TEST[0], escribe GPKG con sufijo de ano,
                        omite SHP/CSV y exportacion cartografica (el orquestador
                        se encarga del merge y exportacion final).
    """
    t_total = time.time()
    year_label = f" — año {YEARS_TEST[0]}" if worker_mode else ""
    print(f"\n{'='*58}")
    print(f"  INICIO - BurnedAreas_MODIS v7{year_label}")
    if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
        print(f"  MODO     : TEST {SAMPLE_FRAC*100:.0f}% eventos/mes "
              f"(seed base {RANDOM_SEED})")
    else:
        print("  MODO     : PRODUCCION (datos completos)")
    if worker_mode:
        print("  EJECUCION: WORKER (subprocess)")
    print(f"{'='*58}")

    # -- 0. AOI ---------------------------------------------------------------
    aoi_geom = load_aoi()

    # -- 1. Paises ------------------------------------------------------------
    t = time.time()
    countries_gdf = load_countries(
        data_dir / "GAUL_2024_L1.shp", COUNTRIES_ADM0, roi_geom
    )
    t = timer("Carga paises", t)

    # -- 2. Archivos BA -------------------------------------------------------
    # [v7.2] discover_ba_years: raw tiles (0_raw/{región}/) con fallback a mosaicos.
    t = time.time()
    ba_files_by_year = discover_ba_years(YEARS_TEST)
    t = timer("Busqueda archivos BA", t)
    if not ba_files_by_year:
        print("  [ERROR] No se encontraron archivos BA.")
        if worker_mode:
            sys.exit(1)
        return None

    # -- 3. Grid de referencia ------------------------------------------------
    t = time.time()
    ref_year, ref_source = next(iter(ba_files_by_year.items()))
    ref_type, ref_data   = ref_source

    if ref_type == "mosaic":
        with rasterio.open(ref_data) as src:
            ba_ref, ba_ref_transform = rasterio.mask.mask(
                src, roi_geom_list, crop=True, filled=True, nodata=0, indexes=[1]
            )
            ba_ref_crs = src.crs
        ref_shape = (ba_ref.shape[1], ba_ref.shape[2])
        del ba_ref
    else:  # raw — obtener grid desde el primer tile del ROI sin merge
        # Solo se necesita para referenciar el DEM; _process_raw_tiles_for_year
        # reprojecta el DEM tile por tile, así que este grid es solo orientativo.
        # Usamos el primer tile que intersecta el ROI.
        roi_minx, roi_miny, roi_maxx, roi_maxy = ROI_BBOX
        ba_ref_transform = ba_ref_crs = ref_shape = None
        for tp in ref_data:
            b = _parse_tile_bounds_from_name(tp)
            if b is not None:
                tx0, ty0, tx1, ty1 = b
                if tx1 <= roi_minx or tx0 >= roi_maxx or ty1 <= roi_miny or ty0 >= roi_maxy:
                    continue
            try:
                with rasterio.open(tp) as src:
                    ba_ref_transform = src.transform
                    ba_ref_crs       = src.crs
                    ref_shape        = (src.height, src.width)
                break
            except Exception:
                continue
        if ref_shape is None:
            print("  [ERROR] Sin tiles raw en ROI para grid de referencia.")
            if worker_mode:
                sys.exit(1)
            return None

    gc.collect()
    print(f"  Grid de referencia: shape={ref_shape}, crs={ba_ref_crs}")
    t = timer("Grid de referencia BA", t)

    # -- 4. DEM ---------------------------------------------------------------
    t = time.time()
    dem_data, elev_mask = load_elevation_mask(
        processed_dir / "mosaico_andes_DEM_COG.tif",
        ref_shape, ba_ref_transform, ba_ref_crs, ELEV_THRESHOLD
    )
    t = timer("Carga elevacion", t)

    # -- 5. MapBiomas: metadatos de tiles originales (v7: sin preprocesamiento) --
    # Los tiles MCD14ML/3_output se leen directamente con recorte espacial
    # en calc_mapbiomas_proportions (rio_mask sobre ventana del mes).
    t = time.time()
    mb_meta = get_mapbiomas_metadata()
    print(f"  Tiles MapBiomas originales : {len(mb_meta)}/9")
    t = timer("Metadatos MapBiomas", t)

    # -- Rutas de salida ------------------------------------------------------
    run_tag   = "vf" if (SAMPLE_FRAC is None or SAMPLE_FRAC >= 1.0) \
                else f"test{int(SAMPLE_FRAC * 100):02d}pct"

    # En modo worker el GPKG incluye el ano para que N workers no colisionen
    if worker_mode:
        base_name = f"BurnedAreas_MODIS_V7_1_{run_tag}_{YEARS_TEST[0]}"
    else:
        base_name = f"BurnedAreas_MODIS_V7_1_{run_tag}"

    os.makedirs(output_dir, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = test_dir   / f"{base_name}.gpkg"
    shp_path  = output_dir / f"{base_name}.shp"
    csv_path  = output_dir / f"{base_name}.csv"

    # -- 6. Procesamiento: escribe al GPKG ano a ano (B5) ---------------------
    t = time.time()
    process_burned_areas(
        ba_files_by_year, dem_data, elev_mask,
        countries_gdf, roi_geom_list, mb_meta, gpkg_path,
        dem_path=processed_dir / "mosaico_andes_DEM_COG.tif"
    )
    t = timer("Procesamiento completo BA", t)

    del dem_data, elev_mask
    gc.collect()

    if not gpkg_path.exists():
        print("  [WARN] GPKG no generado. Verifica los datos de entrada.")
        return None

    # En modo worker el merge y la exportacion final los maneja el orquestador
    if worker_mode:
        print(f"\n  [WORKER] Año {YEARS_TEST[0]} completado → {gpkg_path.name}")
        print(f"  TOTAL worker : {timedelta(seconds=int(time.time() - t_total))}")
        return None

    # -- 7. Leer GPKG para exportar SHP + CSV (sin pd.concat) ----------------
    # BPeak FIX: se lee desde disco en lugar de acumular en memoria
    t = time.time()
    gdf_final = gpd.read_file(gpkg_path)
    t = timer("Lectura GPKG final", t)

    t = time.time()
    gdf_final.to_file(shp_path)
    print(f"  [OK] Shapefile  -> {shp_path.name}")
    gdf_final.drop(columns=["geometry"]).to_csv(
        csv_path, index=False, encoding="utf-8-sig"
    )
    print(f"  [OK] CSV        -> {csv_path.name}")
    t = timer("Exportacion SHP + CSV", t)

    # -- 8. Exportacion cartografica (AOI) ------------------------------------
    save_cartographic_layers(
        aoi_geom   = aoi_geom,
        dem_path   = processed_dir / "mosaico_andes_DEM_COG.tif",
        mb_meta    = mb_meta,
        gdf_result = gdf_final,
        output_dir = output_dir,
        base_name  = base_name,
    )

    print(f"\n{'='*58}")
    print(f"  TOTAL    : {timedelta(seconds=int(time.time() - t_total))}")
    print(f"  FEATURES : {len(gdf_final)}")
    print(f"{'='*58}")
    return gdf_final


# --- Ejecucion ---------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BurnedAreas_MODIS pipeline v7"
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Procesar solo este ano (modo worker, llamado por el orquestador)"
    )
    args = parser.parse_args()

    # ── MODO WORKER ─────────────────────────────────────────────────────────────
    # Llamado por el orquestador via subprocess: procesa un solo ano.
    if args.year is not None:
        if args.year not in YEARS_TEST:
            print(f"  [ERROR] Ano {args.year} no esta en YEARS_TEST: {YEARS_TEST}")
            sys.exit(1)
        YEARS_TEST = [args.year]
        run_pipeline(worker_mode=True)

    # ── MODO ORQUESTADOR ─────────────────────────────────────────────────────────
    # Llamado sin argumentos: coordina workers + merge.
    else:
        t0 = time.time()
        run_tag = "vf" if (SAMPLE_FRAC is None or SAMPLE_FRAC >= 1.0) \
                  else f"test{int(SAMPLE_FRAC * 100):02d}pct"

        n_workers = min(len(YEARS_TEST), N_WORKERS)
        print(f"\n{'='*58}")
        print(f"  ORQUESTADOR — BurnedAreas_MODIS v7")
        print(f"  Anos    : {YEARS_TEST}")
        print(f"  Workers : {n_workers} en paralelo")
        print(f"  Modo    : {run_tag}")
        print(f"{'='*58}")

        # Paso 0: preprocesamiento MapBiomas (solo 1 vez, antes de los workers)
        # Si cada worker lo intentara en paralelo, N gdalwarp correran sobre el
        # mismo archivo destino simultaneamente → corrupcion de archivos.
        if not _preprocess_only():
            sys.exit(1)

        # Paso 0b: validación previa de fuentes BA
        # [v7.2] discover_ba_years: raw tiles primero, mosaicos como fallback.
        ba_sources   = discover_ba_years(YEARS_TEST)
        years_ok     = sorted(ba_sources.keys())
        years_miss   = [y for y in YEARS_TEST if y not in ba_sources]

        if years_miss:
            print(f"  [WARN] {len(years_miss)} años sin archivo BA — se omiten:")
            print(f"         {years_miss}")
        if not years_ok:
            print("  [ERROR] Ningun archivo BA encontrado. Abortando.")
            sys.exit(1)

        YEARS_TEST_RUN = years_ok   # años con datos reales
        n_workers = min(len(YEARS_TEST_RUN), N_WORKERS)
        print(f"  Anos con datos : {YEARS_TEST_RUN} ({len(YEARS_TEST_RUN)} años)")
        print(f"  Workers activos: {n_workers}")

        # Paso 1: un worker por ano, en paralelo
        def _run_worker(year):
            r = subprocess.run(
                [sys.executable, __file__, "--year", str(year)],
                text=True, capture_output=True
            )
            if r.stdout:
                # Mostrar las ultimas lineas del worker para seguimiento
                lines = r.stdout.strip().splitlines()
                print("\n".join(lines[-6:]))
            if r.returncode != 0:
                print(f"\n  [FAIL] año {year}:\n{r.stderr[-400:]}")
            return year, r.returncode == 0

        ok_years, fail_years = [], []

        with ProcessPoolExecutor(max_workers=n_workers) as exe:
            futures = {exe.submit(_run_worker, y): y for y in YEARS_TEST_RUN}
            for f in as_completed(futures):
                year, ok = f.result()
                (ok_years if ok else fail_years).append(year)
                print(f"  {'[OK]  ' if ok else '[FAIL]'} año {year} terminado")

        print(f"\n  Workers finalizados — OK: {sorted(ok_years)} "
              f"| FAIL: {sorted(fail_years)}")

        # Paso 2: merge de los GPKGs anuales + exportacion final
        if ok_years:
            gdf_final = merge_and_export(run_tag)
            if gdf_final is not None:
                print(gdf_final[["year", "month", "BurnDate", "Elevation",
                                  "Zone_Clima", "pct_class12",
                                  "area_ha", "gaul0_name"]].head())

        timer(f"TOTAL orquestador ({len(ok_years)}/{len(YEARS_TEST)} anos OK)", t0)