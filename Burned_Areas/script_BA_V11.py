# -*- coding: utf-8 -*-
import os
# Cache GDAL moderada. En v9 cada worker abre 1 mosaico BA anual a la vez y,
# para MapBiomas, ventanas pequenas por tile. 512 MB/worker es suficiente.
os.environ.setdefault("GDAL_CACHEMAX", "512")
# Silenciar el ruido de libtiff (warnings cosmeticos de etiquetas TIFF).
os.environ.setdefault("CPL_LOG", os.devnull)
"""
Modified on 18/06/2026
Version 11.3.0   (prefijo de salida se mantiene "V11")
@author: jvilla

Base   : script_BA_V10.py (v10.1.0)
Logica : deteccion de eventos como v10.1.0, con los cambios de v11.x y, desde
         v11.3.0, nomenclatura de columnas ALINEADA con script_AT_V8.py.

============================== Changes v11.3.0 =========================
[AT-COLS]    Nomenclatura de columnas alineada con AT_V8 para que las salidas de
             ambos pipelines sean directamente comparables/concatenables:
               Elevation        -> dem_median
               geo_region       -> region_geo
               area_class12_ha  -> cl12_m2   (ahora en m2, no ha)
               area_class3_ha   -> cl3_m2    (ahora en m2, no ha)
             La capa de recortes usa 'area_clase_m2'. 'area_ha' (superficie del
             evento COMPLETO) se conserva con ese nombre (no tiene equivalente en
             AT, que trabaja con buffers fijos) y sigue en hectareas.

============================== Changes v11.2.0 =========================
[AT-ALIGN]   Config REAL tomada de script_AT_V8.py (inputs compartidos):
             - regiones: 'region-geografica.shp', campo 'nombre'.
             - zonas: REGION_1='Sierra' (clase 12) / REGION_2='Selva' (clase 3).
             - DEM y regiones se referencian al 1_input de AT (MCD14ML) por ruta
               absoluta, como ya se hacia con MAPBIOMAS_DIR, para reutilizar los
               MISMOS ficheros que AT sin duplicar.
[DEM-BUILD]  Se ADOPTA el patron de AT_V8 'ensure_dem_bruto': el DEM se
             materializa UNA vez desde los tiles crudos Copernicus GLO30
             (RAW_TILES_DIR) a un GeoTIFF tileado+comprimido (VRT -> Translate,
             escritura atomica, validacion de integridad opcional) y se cachea.
             Reemplaza el mosaico al-vuelo de v11.1.0. Si AT ya construyo
             'mosaico_peru_bruto.tif', BA lo reutiliza (cache compartida).
             process_ba_year vuelve a abrir un DEM unico y reproyecta al ROI
             (conservando la MEDIANA de cota por evento). Requiere osgeo/GDAL
             (igual que AT); en el cluster esta disponible.

============================== Changes v11.1.0 =========================
[DEM-TILES]  El DEM ya NO es un unico mosaico. Ahora son tiles crudos que
             cubren TODA el area de estudio (DEM_TILES_DIR / DEM_TILES_GLOB).
             Se mosaican al vuelo SOLO con rasterio: por cada grid del ROI se
             reproyectan las tiles que lo intersecan a ese grid (con cache por
             grid, como antes). No requiere VRT ni GDAL-CLI. Esto ademas da
             cobertura completa (tambien en selva), evitando NaN por cobertura.
[MEM-STREAM] merge_and_export escribe en STREAMING (un worker en memoria a la
             vez) -> GPKG 'eventos' + CSV. La exportacion cartografica del AOI
             ya NO recibe el GeoDataFrame completo: relee del GPKG solo la
             ventana (bbox) del AOI. Reduce el pico de RAM tras quitar el filtro
             de altitud (muchos mas eventos).

============================== Changes v11.0.0 =========================
[ALT-1]      Se ELIMINA el filtro de altitud (ELEV_THRESHOLD). Ya NO se
             descartan eventos por cota. El DEM se conserva SOLO como fuente
             del atributo de elevacion.
[ALT-2]      'Elevation' pasa de MEDIA a MEDIANA de la cota dentro de cada
             burned area (ndimage.labeled_comprehension + np.nanmedian, robusto
             a nodata del DEM). OJO: si el DEM no cubre el evento -> NaN.
[CLIM-1]     Se ELIMINA la zona climatica (assign_zone_clima / 'Zone_Clima').
[REG-1]      NUEVO input: shapefile de regiones geograficas del Peru (3 zonas).
             Cada evento se clasifica por su representative_point dentro de una
             region -> columna 'geo_region' (campo de atributo = REGION_FIELD).
[REG-2]      Solo se conservan DOS regiones (REGION_1, REGION_2 en la config).
             Los eventos de la tercera se descartan.
[MB-C3]      NUEVO input: tiles de la CLASE 3 (mismo directorio y naming que la
             clase 12: 'clase3_r{r}c{c}.tif'). get_mapbiomas_metadata() y
             calc_class_clip() se generalizan por (tiles_map, target_class).
[AREA-SPLIT] El area de clase 12 se mide SOLO en eventos de REGION_1 (sierra);
             el area de clase 3 SOLO en eventos de REGION_2 (selva). Columnas
             'area_class12_ha' y 'area_class3_ha' (NaN en la region contraria).
             La capa de recortes se UNIFICA con una columna 'clase'
             ('clase 12' / 'clase 3').
[VER]        Prefijo de salida "V10" -> "V11".

============================== Changes v10.1.0 =========================
[MB-1][SIMP] MapBiomas pasa a los tiles con CLASE 12 YA EXTRAIDA de AT V5:
             grilla 3x3 'clase12_r{r}c{c}.tif' en MCD14ML/3_output. Cada raster
             contiene SOLO clase 12 (valor de pixel = 12; fondo = nodata).
             => Se ELIMINA toda la maquinaria de tiles crudos:
                - discover_mapbiomas_tiles() (indexaba ~137k cabeceras)
                - _read_tile_footprint() y el cache mapbiomas_tiles_index.gpkg
                - _parse_tile_bounds_from_name()
                - MAPBIOMAS_REGIONS / MAPBIOMAS_INDEX_CACHE
             En su lugar: get_mapbiomas_metadata() (9 tiles, como AT V5).
[MB-2][SIMP] calc_class12_clip() ya NO reconstruye la clase 12 al vuelo desde
             tiles crudos. Vectoriza directamente la mascara (arr == 12) del
             tile pre-extraido y la interseca con el poligono BA. El anclaje de
             banda al ultimo anio (band_for_year) se conserva (= AT V5).
[MB-3]       'area_class12_ha' se sigue midiendo en EPSG:3857 (decision del
             usuario; ~6-12% de sesgo de area a estas latitudes, consistente
             con 'area_ha').
[NOTE]       SAMPLE_FRAC sigue en 0.05 (MODO TEST = solo 5% de eventos). Para
             produccion -> None. Si los resultados "no corresponden" en QGIS,
             revisar PRIMERO este knob: en test solo se exporta 1 de cada 20
             eventos.

   --- Correcciones de revision (misma 10.1.0, aun no corrida en produccion) ---
[FIX-3/4]    spatial_join_3attempts: intentos 2 y 3 ahora de-duplican el indice
             y asignan por INDICE (no por .values posicional). Evita crash/
             desalineacion si un centroide cae en poligonos solapados.
[FIX-5]      _preprocess_only avisa de cobertura MapBiomas PARCIAL listando los
             tiles ausentes (antes solo si faltaban los 9).
[FIX-6]      Raster BA del AOI: ya NO colapsa el mes con max(). Exporta un
             archivo por anio con SOLO las bandas-mes con eventos en el AOI
             ('{base}_aoi_ba_{anio}.tif', bandas 'ba_AAAA_MM'). Conserva la
             dimension mes y elimina el descarte silencioso de banda por forma.
[FIX-7]      _preprocess_only valida la existencia del shapefile GAUL en Paso 0
             (antes el fallo aparecia tarde, dentro del worker).
[FIX-8]      Si fallan workers, la salida se marca con sufijo '_PARCIAL' y se
             imprimen [ERROR] con los anios ausentes (paralelizacion por anio).
[FIX-13]     La capa 'clip_class12' incluye ahora 'area_class12_ha' (antes solo
             event_uid/year/month).
[FIX-16]     DEM reproyectado se CACHEA por worker con verificacion de grid
             (transform+forma+CRS): se reproyecta 1 vez si el grid se repite.
[DOC-12]     Documentada la semantica de area_class12_ha: NaN=fuera de cobertura,
             0.0=dentro de tile sin clase 12, >0=area real.
[DOC-18]     Documentado el limite de escala del merge (carga todo en memoria) y
             la alternativa en streaming para produccion.
[KEEP]       Revisados y dejados a proposito: SAMPLE_FRAC (test), filtro 'ba>0'
             (verificado vs doc MODIS y nodata=-9999), parseo de anio, append
             GPKG, area_ha del AOI (evento completo), log SLURM global,
             _label_by_doy (ya optimizado).

============================== Changes v10.0.0 =========================
[OUT-1]      Formatos de salida: SOLO GPKG + CSV. Se elimina todo SHP
             (truncaba campos a 10 chars y tope de 2 GB).
[OUT-2]      Eventos y recorte clase 12 se UNIFICAN en un unico GPKG con dos
             capas: '{base}.gpkg' -> layer 'eventos' + layer 'clip_class12'.
             CSV solo para 'eventos' ('{base}.csv'); el recorte es geometrico
             (sin CSV propio).
[OUT-3]      'area_class12_ha' queda SOLO en la capa de eventos. La capa
             'clip_class12' lleva nada mas event_uid, year, month + geometria.
[OUT-4][AOI] Se ELIMINA el raster MapBiomas clase 12 del AOI (la clase 12 ya
             esta como vector recortado en todo el ROI).
[OUT-5][AOI] Los 24 raster BA del AOI se COMPILAN en un unico raster de 24
             bandas (1 por anio): '{base}_aoi_ba_2001_2024.tif'. Cada banda
             colapsa los 12 meses con el maximo (valor = DOY del fuego del
             anio, 0 = sin quemar); descripcion 'ba_{anio}'. Resultados del
             AOI pasan a GPKG + CSV (sin SHP).

============================== Changes v9.3.0 ==========================
[REQ-5]      AGRUPAMIENTO DOBLE de eventos dentro del mes: ahora un evento
             exige mismo DOY (temporal) ADEMAS de vecindad de pixeles
             (espacial, conectividad 8). Antes solo se exigia vecindad sobre
             la mascara binaria (ba>0), por lo que pixeles contiguos con DOY
             distinto caian en el mismo evento. Implementado en _label_by_doy:
             se etiqueta por separado cada DOY presente en el mes y se juntan
             con ids globalmente unicos. Consecuencia: cada evento tiene un
             unico DOY, luego BurnDate es exactamente ese dia.
[REQ-6]      Se ELIMINAN los CSV de resumen agregado ({base}_summary_year.csv
             y {base}_summary_year_month.csv). Las salidas quedan a nivel de
             evento (no agregadas).

============================== Changes v9.2.0 ==========================
[REQ-1]      Se ELIMINAN las columnas de salida 'area_km2' y 'pct_class12'
             (en eventos y en los resumenes year/year-month). Se CONSERVA
             'area_class12_ha'.
[REQ-2][AOI] El raster MapBiomas del AOI ya NO se exporta con todas las
             bandas. Se queda SOLO con las 24 bandas de 2001-2024 (elegidas
             con band_for_year) y cada una se convierte en MASCARA BINARIA de
             la clase 12 (pixel == 12 -> 1, resto -> 0). Salida:
             {base}_aoi_mapbiomas_class12.tif (24 bandas, uint8).
[REQ-3][MB]  El conteo de clase 12 ya NO mezclaba bien nodata en el
             denominador (calc_mapbiomas_proportions contaba TODOS los pixeles
             del poligono, incluido nodata/0; MapBiomas reserva 0 = no
             observado). Se SUSTITUYE ese conteo por un RECORTE GEOMETRICO
             real: se vectoriza pixel == 12 en la ventana de cada evento (lo
             que excluye nodata por construccion) y se interseca con el
             poligono BA.
[REQ-4][OUT] Nuevo entregable ROI-wide: SHP/GPKG de los poligonos BA
             RECORTADOS a la clase 12 ({base}_clip_class12.shp/.gpkg), con
             columnas event_uid, year, month, areac12_ha. 'area_class12_ha'
             de cada evento se mide sobre ESA geometria recortada (EPSG:3857),
             no por pct*area. El SHP de BA mensuales (sin recortar) se mantiene
             igual que en v9.1.

============================== Changes v9.1.2 ==========================
[FIX-4][MB]  CAUSA REAL del pct_class12=NaN: rasterstats.zonal_stats ignora
             nodata=None y fuerza su default hardcodeado -999 (NodataWarning),
             que es incompatible con uint8 -> OverflowError, tumbaba el calculo.
             Se ELIMINA rasterstats: el conteo se hace con rasterio.features.
             rasterize + numpy (tot = pixeles del poligono, n12 = clase 12).
             Ahora pct_class12 y area_class12_ha se calculan correctamente.
[ADD]        Resumenes de area agregados por (year, month) y por (year):
             n_eventos, area_ha, area_km2, area_class12_ha, pct_class12.
             Salen como {base}_summary_year_month.csv y {base}_summary_year.csv.

============================== Changes v9.1.1 ==========================
[FIX-3][MB]  (insuficiente) intento de sanear nodata vs dtype; rasterstats lo
             ignoraba, ver FIX-4.
[ADD]        Resumenes de area agregados por (year, month) y por (year):
             n_eventos, area_ha, area_km2, area_class12_ha, pct_class12.
             Salen como {base}_summary_year_month.csv y {base}_summary_year.csv.

============================== Changes v9.1 ============================
[FIX-1][MB]  Footprints de tiles MapBiomas leidos de los BOUNDS REALES del
             raster (src.bounds + reproyeccion a WGS84), NO del nombre. Se
             cachean en 2_processed/mapbiomas_tiles_index.gpkg para no reabrir
             137k cabeceras en cada worker.
[FIX-2][AOI] Los recortes MapBiomas del AOI se FUSIONAN en un unico mosaico
             ({base}_aoi_mapbiomas.tif) en vez de 1 .tif por tile.
[DROP]       Se elimina la vectorizacion de clase 12 ({..}_class12.gpkg) y los
             GPKG intermedios por worker (w{k}.gpkg) se borran tras el merge.
[ADD]        Se exporta el SHP de TODOS los eventos BA (full extent).
[MOVE]       El GPKG final mergeado pasa de 4_test a 3_output.

============================== Changes v9 ==============================
Motivacion: correccion de los datos de entrada.
    - Los EVENTOS que se guardan salen ahora de MODIS BA (areas quemadas),
      no de los tiles de cobertura. MapBiomas y DEM pasan a ser FUENTES de
      atributos de cada evento BA.

[IN-1][BA]   MODIS BA = mosaicos anuales en 1_input/mosaics_BA.
             24 rasters (1 por anio, 2001-2024), cada uno con 12 BANDAS = los
             12 MESES del anio. El valor del pixel es el BurnDate (dia-del-anio,
             DOY); pixel > 0 = quemado ese mes. => el MES sale DIRECTO del
             indice de banda (1-12); el DOY se conserva como atributo BurnDate.

[IN-2][MB]   MapBiomas = tiles en MCD14ML/0_raw/biomas_peru_sol/{regiones}
             (la ruta que en v8 se asumia para BA). 1 banda por anio (anclada
             la ultima a YEAR_MAX). La clase objetivo es el VALOR DE PIXEL 12.
             Solo se consulta para calcular pct_class12 por evento.

[ARCH]       Paralelizacion por ANIO (24 archivos), no por tile. Cada worker
             toma un subconjunto de anios (round-robin), abre el mosaico del
             anio, reproyecta el DEM una vez a su grid y recorre los 12 meses.

[DEM]        Sigue siendo FILTRO (descarta eventos < ELEV_THRESHOLD m) y
             ademas atributo (Elevation media por evento).

[DROP]       Se elimina toda la maquinaria de "fuego partido entre tiles":
             touches_border, ba_id, union-find, reintegrate_ba_ids. Al leer
             cada banda completa (ventana = ROI) en memoria, un fuego es una
             unica componente conexa; no hay costura que soldar. El event_uid
             ("BA_{anio}_M{mes}_{label}") es el identificador definitivo.

[DROP]       Se elimina el grid 3x3 'mapbiomas_ba_grid' y band_for_year para BA
             (las bandas BA ya son meses). band_for_year se conserva SOLO para
             MapBiomas (bandas anuales).

[KEEP]       COUNTRY_FILTER (Peru), Zone_Clima, spatial_join_3attempts, areas
             en EPSG:3857, exportacion final GPKG + CSV, recorte AOI de capas.

[AOI][FIX]   El recorte AOI ahora es un CLIP geometrico real (gpd.clip) de los
             vectores, coherente con el recorte de rasters (crop=True). La
             vectorizacion de clase 12 se recorta tambien al poligono del AOI
             (no solo a su bbox) y se reproyecta por-tile antes de acumular.
             Se valida que el AOI quede dentro del ROI.

[PROD]       Por defecto se trabaja con TODOS los datos (SAMPLE_FRAC = None).
             SAMPLE_FRAC < 1 queda solo como knob opcional para tests.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
import rasterio.features
import rasterio.warp
import rasterio.windows
import geopandas as gpd
from shapely.geometry import box, shape, mapping
from shapely.ops import unary_union
from scipy import ndimage
from collections import defaultdict
from rasterio.features import rasterize
from rasterio.mask import mask as rio_mask
from rasterio.merge import merge as rio_merge
from rasterio.windows import Window, from_bounds
from rasterio.warp import Resampling
from rasterio.crs import CRS
import re
import math
import subprocess
import sys
import argparse
import time
import gc
import logging
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.getLogger("rasterio").setLevel(logging.ERROR)

# --- Rutas -------------------------------------------------------------------
base_dir      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD64A1")
data_dir      = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir    = base_dir / "3_output"
test_dir      = base_dir / "4_test"

# --- MODIS BA: mosaicos anuales (24 archivos, 12 bandas = meses) -------------
BA_MOSAIC_DIR = data_dir / "mosaics_BA"

# --- MapBiomas: tiles con CLASE YA EXTRAIDA (logica de AT V5) ----------------
# Grilla 3x3 'clase{K}_r{r}c{c}.tif'. Cada raster contiene SOLO la clase K
# (valor de pixel = K; fondo = nodata). 1 banda por anio, ancladas con la
# ULTIMA banda = YEAR_MAX (ver band_for_year).
# v11.0.0: ademas de la clase 12 (grasslands) se anade la clase 3 (forest),
# en el MISMO directorio y con el MISMO naming.
MAPBIOMAS_DIR   = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/"
                       "MCD14ML/3_output")
MAPBIOMAS_GRID  = 3


def _tiles_for_class(stem):
    """Grilla 3x3 de rutas '{stem}_r{r}c{c}.tif'."""
    return {(r, c): MAPBIOMAS_DIR / f"{stem}_r{r}c{c}.tif"
            for r in range(MAPBIOMAS_GRID) for c in range(MAPBIOMAS_GRID)}


MAPBIOMAS_TILES_C12 = _tiles_for_class("clase12")   # grasslands (clase 12)
MAPBIOMAS_TILES_C3  = _tiles_for_class("clase3")    # forest     (clase 3)

# --- Inputs COMPARTIDOS con el pipeline AT (anomalias termicas) --------------
# Regiones y DEM viven bajo MCD14ML/1_input (lado AT). Se referencian por ruta
# absoluta (igual que MAPBIOMAS_DIR) para reutilizar los MISMOS ficheros que AT
# sin duplicar. Ajusta si tu layout difiere; el usuario verifica antes de lanzar.
AT_INPUT_DIR = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/"
                    "MCD14ML/1_input")

# --- DEM bruto: construccion cache dentro del pipeline (patron de AT_V8) ------
# Los tiles crudos Copernicus GLO30 viven en el cluster. Se construye el mosaico
# UNA vez (si no existe) y se reusa. Es el MISMO fichero que usa AT, asi que si
# AT ya lo materializo, BA lo reutiliza directamente (cache compartida).
DEM_PATH      = AT_INPUT_DIR / "mosaico_peru_bruto.tif"   # DEM materializado
RAW_TILES_DIR = AT_INPUT_DIR / "copernicus_dem_andes"     # None si ya tienes el .tif
# bbox Peru + margen (lon_min, lat_min, lon_max, lat_max): cubre Sierra y Selva.
DEM_BBOX      = (-81.5, -18.6, -68.5, 0.2)
DEM_NODATA    = 0        # 0 = oceano/relleno (GLO30 via GEE no declara nodata)
DEM_REBUILD   = False    # True para forzar reconstruccion aunque exista
DEM_VALIDATE         = True    # valida integridad de tiles la 1a vez (pago unico)
DEM_VALIDATE_WORKERS = 8       # hilos para el checksum

# --- Area de Interes (AOI) — solo recorte de capas de ejemplo ----------------
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales -----------------------------------------------------
ROI_BBOX       = (-80.0, -20.0, -60.0, 1.0)
YEAR_MIN       = 2001
YEAR_MAX       = 2024
COUNTRIES_ADM0 = [178, 184, 185, 190, 207]
COUNTRY_FILTER = "Peru"

# --- Regiones geograficas del Peru (input compartido con AT) -----------------
# Shapefile que divide la zona de estudio en zonas. Cada evento se clasifica por
# su representative_point dentro de una region (campo de atributo = REGION_FIELD).
# Solo se conservan REGION_1 y REGION_2 (v11.2.0: valores REALES de AT_V8).
REGIONS_PATH = AT_INPUT_DIR / "region-geografica.shp"
REGION_FIELD = "nombre"
REGION_1     = "Sierra"   # grasslands -> area de clase 12
REGION_2     = "Selva"    # forest     -> area de clase 3
# Mapeo region -> clase objetivo. Usado en enrich_events.
REGION_CLASS = {REGION_1: 12, REGION_2: 3}

YEARS_RUN = list(range(YEAR_MIN, YEAR_MAX + 1))   # 2001-2024

# Mapeo banda<->anio SOLO para MapBiomas (bandas anuales). Ancla la ULTIMA
# banda a YEAR_MAX: tiles de 40 bandas (1985-2024) -> banda(2001)=17.
def band_for_year(year, n_bands):
    """Indice de banda (1-based) anclando la ULTIMA banda a YEAR_MAX."""
    return year - YEAR_MAX + n_bands

# Margen al recortar ventanas MapBiomas (cobertura de bordes de poligono).
MAPBIOMAS_EXTENT_BUFFER_DEG = 0.005

WGS84 = CRS.from_epsg(4326)

roi_geom = box(*ROI_BBOX)

# --- Submuestreo (solo tests) ------------------------------------------------
# SAMPLE_FRAC: fraccion de EVENTOS conservados por (anio, mes), muestreo
#   Bernoulli. Produccion: None (conservar todos los eventos).
#   OJO: 0.05 = modo TEST (5% de eventos). Para produccion poner None.
SAMPLE_FRAC = None
RANDOM_SEED = 42

# --- Workers -----------------------------------------------------------------
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# Orden de columnas de salida (v11.3.0: nomenclatura alineada con AT_V8).
# 'dem_median' es la MEDIANA de la cota del evento; cl12_m2/cl3_m2 en m2.
COL_ORDER = [
    "event_uid", "year", "month", "BurnDate", "dem_median", "region_geo",
    "cl12_m2", "cl3_m2", "ADM0_CODE", "gaul0_code",
    "gaul0_name", "area_ha", "geometry",
]


# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


def run_tag_of(sample_frac):
    """Sufijo de archivos. None/>=1 -> 'vf' (full). <1 -> 'test_e{NN}pct'."""
    return ("vf" if (sample_frac is None or sample_frac >= 1.0)
            else f"test_e{int(sample_frac * 100):02d}pct")


def _to_local_crs(geom, dst_crs):
    """Reproyecta un shapely geom de WGS84 al CRS dado (si difiere)."""
    if dst_crs is None or dst_crs == WGS84:
        return geom
    return (gpd.GeoDataFrame([0], geometry=[geom], crs=WGS84)
            .to_crs(dst_crs).geometry.iloc[0])


def _round_window(win, width, height):
    """Redondea una Window flotante a enteros y la acota al raster."""
    col_off = max(0, int(math.floor(win.col_off)))
    row_off = max(0, int(math.floor(win.row_off)))
    w = int(math.ceil(win.col_off + win.width)) - col_off
    h = int(math.ceil(win.row_off + win.height)) - row_off
    w = max(0, min(w, width - col_off))
    h = max(0, min(h, height - row_off))
    return Window(col_off, row_off, w, h)


# --- AOI ---------------------------------------------------------------------
def load_aoi():
    if AOI_PATH is not None:
        aoi_gdf  = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
        aoi_geom = aoi_gdf.geometry.union_all()
        print(f"  AOI cartografica : {Path(AOI_PATH).name} ({len(aoi_gdf)} feat.)")
    elif AOI_BBOX is not None:
        w, s, e, n = AOI_BBOX
        aoi_geom = box(w, s, e, n)
        print(f"  AOI cartografica : bbox W={w} S={s} E={e} N={n}")
    else:
        print("  AOI cartografica : None")
        return None
    # Aviso si el AOI se sale del ROI (el recorte de resultados saldria vacio).
    if not roi_geom.contains(aoi_geom):
        print("  [WARN] El AOI no esta totalmente dentro del ROI; las capas de "
              "ejemplo pueden quedar incompletas.")
    return aoi_geom


# --- Regiones geograficas (NUEVO v11.0.0) ------------------------------------
def load_regions(path, field, roi_geom):
    """Carga el shapefile de regiones, recorta al ROI y normaliza el campo de
    nombre a 'region_geo'. Se cargan TODAS las regiones (no solo las dos de
    interes) para clasificar correctamente y descartar la tercera despues."""
    reg = gpd.read_file(path)
    if field not in reg.columns:
        raise KeyError(f"Campo '{field}' no encontrado en {Path(path).name}. "
                       f"Columnas: {list(reg.columns)}")
    reg = reg[[field, "geometry"]].rename(columns={field: "region_geo"}).copy()
    reg = reg[reg.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    reg = reg.clip(roi_geom).to_crs("EPSG:4326")
    return reg.reset_index(drop=True)


def assign_geo_region(gdf, regions_gdf):
    """Clasifica cada evento por su representative_point dentro de una region.
    Devuelve un array de nombres de region (NaN si el punto no cae en ninguna).
    Un 'within' basta: representative_point cae dentro del BA (dentro del Peru)
    y las regiones particionan la zona de estudio."""
    pts = gpd.GeoDataFrame(
        geometry=gdf.geometry.representative_point(), crs=gdf.crs)
    j = pts.sjoin(
        regions_gdf[["region_geo", "geometry"]],
        how="left", predicate="within",
    ).drop(columns=["index_right"], errors="ignore")
    # Guard: un punto en frontera podria matchear >1 poligono -> primero.
    j = j[~j.index.duplicated(keep="first")]
    return j["region_geo"].reindex(gdf.index).values


# --- Carga / filtrado de paises ----------------------------------------------
def load_countries(path, adm0_codes, roi_geom):
    pays = gpd.read_file(path)
    pays = pays[pays["gaul0_code"].isin(adm0_codes)].copy()
    pays = pays[pays.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    pays = pays.clip(roi_geom).to_crs("EPSG:4326")
    return pays.reset_index(drop=True)


# --- Spatial join de paises (3 intentos) -------------------------------------
def spatial_join_3attempts(gdf, countries_gdf):
    if "ADM0_CODE" not in gdf.columns:
        gdf = gdf.copy()
        gdf["ADM0_CODE"] = np.nan

    joined = gdf.sjoin(
        countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
        how="left", predicate="intersects",
    ).drop(columns=["index_right"], errors="ignore")

    joined["_match"] = joined["gaul0_code"] == joined["ADM0_CODE"]
    gdf_out = (
        joined.sort_values("_match", ascending=False)
        .groupby(level=0).first()
        .reset_index(drop=True)
        .drop(columns=["_match"])
    )
    gdf_out = gpd.GeoDataFrame(gdf_out, geometry="geometry", crs=gdf.crs)
    del joined

    mask_nan = gdf_out["gaul0_name"].isna()
    if mask_nan.any():
        tmp = gdf_out[mask_nan].copy()
        tmp["geometry"] = tmp.geometry.centroid
        res = tmp[["geometry"]].sjoin(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
            how="left", predicate="within",
        ).drop(columns=["index_right"], errors="ignore")
        res = res[~res.index.duplicated(keep="first")]          # guard de-dup
        gdf_out.loc[res.index, "gaul0_code"] = res["gaul0_code"]  # por indice
        gdf_out.loc[res.index, "gaul0_name"] = res["gaul0_name"]

    mask_nan2 = gdf_out["gaul0_name"].isna()
    if mask_nan2.any():
        tmp2 = gdf_out[mask_nan2].copy()
        tmp2["geometry"] = tmp2.geometry.centroid
        res2 = tmp2[["geometry"]].sjoin_nearest(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]], how="left",
        ).drop(columns=["index_right"], errors="ignore")
        res2 = res2[~res2.index.duplicated(keep="first")]
        gdf_out.loc[res2.index, "gaul0_code"] = res2["gaul0_code"]  # por indice
        gdf_out.loc[res2.index, "gaul0_name"] = res2["gaul0_name"]

    return gdf_out


# --- Parseo de nombres -------------------------------------------------------
def _parse_year_from_name(path):
    """Anio (2001-2024) en el nombre del mosaico BA."""
    for m in re.finditer(r'(20\d{2})', path.stem):
        y = int(m.group(1))
        if YEAR_MIN <= y <= YEAR_MAX:
            return y
    return None


# --- Descubrimiento de fuentes -----------------------------------------------
def discover_ba_year_files():
    """Dict {anio: ruta} de los mosaicos BA anuales. No abre archivos."""
    files = sorted(BA_MOSAIC_DIR.glob("*.tif"))
    out = {}
    for p in files:
        y = _parse_year_from_name(p)
        if y is not None:
            out[y] = p
    # Fallback: si ningun nombre trae anio pero hay exactamente N archivos,
    # asignar por orden 2001..2024.
    if not out and len(files) == (YEAR_MAX - YEAR_MIN + 1):
        out = {YEAR_MIN + i: f for i, f in enumerate(files)}
        print("  [WARN] Anio no detectado en los nombres BA; asignado por orden.")
    return out


def get_mapbiomas_metadata(tiles_map):
    """Metadatos de una grilla 3x3 de tiles pre-extraidos (logica AT V5).

    v11.0.0: parametrizado por 'tiles_map' (clase 12 o clase 3). Cada tile
    contiene SOLO su clase (pixel == K; fondo = nodata), por lo que NO hay que
    reconstruir la clase al vuelo: basta vectorizar arr == K.

    Devuelve dict {(r,c): {bounds_geom(WGS84), crs, nodata, bands}}.
    """
    meta = {}
    for key, path in tiles_map.items():
        if not path.exists():
            print(f"  [WARN] Tile MapBiomas ausente: {path.name}")
            continue
        try:
            with rasterio.open(path) as src:
                b   = src.bounds
                crs = src.crs or WGS84
                if CRS.from_user_input(crs) != WGS84:
                    b = rasterio.warp.transform_bounds(crs, WGS84, *b,
                                                       densify_pts=21)
                meta[key] = {
                    "bounds_geom": box(*b),       # WGS84 (cruza con BA en WGS84)
                    "crs"        : src.crs,
                    "nodata"     : src.nodata,
                    "bands"      : src.count,      # para anclar ultima banda=YEAR_MAX
                }
        except Exception as e:
            print(f"  [WARN] tile MapBiomas ilegible {path.name}: {e}")
    return meta


# --- DEM bruto: construccion cache (patron de AT_V8, portado a BA) -----------
def _tile_ok(path):
    """(path, True/False). El Checksum de GDAL fuerza la lectura de TODOS los
    bloques del tile; un TIFF truncado/corrupto lanza excepcion y se marca
    invalido. Un dataset independiente por hilo (seguro en ThreadPoolExecutor)."""
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


def ensure_dem_bruto(raw_tiles_dir, dem_out, bbox, nodata=0, rebuild=False,
                     validate=True, max_workers=8):
    """Garantiza un DEM bruto materializado y devuelve su ruta (str).

    - Si 'dem_out' existe y not rebuild -> se reutiliza (cache; compartida con AT).
    - Si no existe -> se construye desde los tiles brutos de 'raw_tiles_dir':
        0) se VALIDA cada tile (Checksum); los corruptos se excluyen y registran
           en 'tiles_corruptos.txt' -> un tile roto no aborta todo;
        1) VRT transitorio recortado a 'bbox' con nodata explicito;
        2) gdal.Translate a un '.tmp.tif' tileado+comprimido y os.replace()
           atomico al nombre final (si falla, se limpian parciales).
    El .tif resultante es autocontenido y portable. Requiere osgeo/GDAL.
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
    dem_name_low = dem_out.name.lower()
    tiles = []
    for p in raw_tiles_dir.rglob("*.tif"):
        rel_parts = [x.lower() for x in p.relative_to(raw_tiles_dir).parts]
        if "output" in rel_parts:
            continue
        nm = p.name.lower()
        if nm.startswith("mosaico") or nm == dem_name_low:
            continue
        tiles.append(str(p))
    tiles.sort()
    if not tiles:
        raise FileNotFoundError(f"Sin tiles .tif en {raw_tiles_dir}")

    if validate:
        print(f"  [DEM] Validando integridad de {len(tiles)} tiles "
              f"({max_workers} hilos)...")
        bad = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for path, ok in ex.map(_tile_ok, tiles):
                if not ok:
                    bad.append(path)
        if bad:
            log = dem_out.parent / "tiles_corruptos.txt"
            log.write_text("\n".join(bad))
            print(f"  [DEM] {len(bad)} tile(s) CORRUPTO(S) excluido(s). "
                  f"Lista -> {log.name}")
            bad_set = set(bad)
            tiles = [t for t in tiles if t not in bad_set]
        else:
            print("  [DEM] Todos los tiles pasaron la validacion.")
        if not tiles:
            raise FileNotFoundError(
                "Todos los tiles fallaron la validacion; nada que mosaicar.")

    print(f"  [DEM] Construyendo desde {len(tiles)} tiles validos...")
    dem_out.parent.mkdir(parents=True, exist_ok=True)
    tmp_vrt = str(dem_out.with_suffix(".tmp.vrt"))
    tmp_tif = str(dem_out.with_suffix(".tmp.tif"))

    gdal.BuildVRT(
        tmp_vrt, tiles,
        options=gdal.BuildVRTOptions(
            outputBounds=list(bbox), srcNodata=nodata, VRTNodata=nodata,
            resampleAlg="nearest"))
    print(f"  [DEM] Materializando a {dem_out.name} (tiled+DEFLATE, streaming)...")
    try:
        gdal.Translate(
            tmp_tif, tmp_vrt,
            options=gdal.TranslateOptions(
                format="GTiff", noData=nodata,
                creationOptions=[
                    "COMPRESS=DEFLATE", "PREDICTOR=3", "ZLEVEL=6",
                    "TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512",
                    "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS"]))
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


# --- Recorte geometrico de una clase por evento (BA ∩ claseK) ----------------
def calc_class_clip(polygons_gdf, year, mb_meta, tiles_map, target_class):
    """Para cada poligono BA (de un mismo 'year') devuelve:
        - clip_geoms: la geometria de 'target_class' contenida en el evento, es
          decir BA ∩ claseK, en WGS84 (o None).
        - area12_ha : superficie de esa geometria recortada, en ha (EPSG:3857).

    v11.0.0: parametrizado por (tiles_map, target_class) para reutilizarse con
    la clase 12 (grasslands) y la clase 3 (forest).

    Semantica de area12_ha (IMPORTANTE para analizar el CSV):
        - NaN  -> el evento NO cae dentro del rectangulo de NINGUN tile clase12.
                  Es decir, no hay cobertura MapBiomas para ese evento.
        - 0.0  -> el evento SI cae dentro del rectangulo de un tile, pero no hay
                  pixeles clase 12 bajo el. OJO: en el tile pre-extraido el fondo
                  es nodata y mezcla dos situaciones que el dato ya no distingue:
                  (a) terreno mapeado por MapBiomas pero de otra clase (no 12), y
                  (b) terreno genuinamente no mapeado. Ambas dan 0.0. En la
                  practica, como la grilla 3x3 se recorto al area de estudio y
                  MapBiomas cubre todo Peru, 0.0 significa casi siempre
                  "sin clase 12 aqui" (caso a), no "sin dato".
        - >0   -> area real de la interseccion BA ∩ clase12.
        Resumen: NaN = fuera de cobertura; 0.0 = dentro de cobertura sin clase 12.

    v10.1.0: los tiles ('clase12_r{r}c{c}.tif') YA contienen SOLO la clase 12
    (pixel == 12; fondo = nodata). Se vectoriza directamente arr == 12 en la
    ventana de cada evento y se interseca con el poligono BA. Un evento a
    caballo entre >1 tile acumula (union) las piezas de cada tile. El anclaje
    de banda al ultimo anio (band_for_year) es identico a AT V5.
    """
    n = len(polygons_gdf)
    clip_geoms = [None] * n
    area12 = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not mb_meta or year < YEAR_MIN or year > YEAR_MAX:
        return clip_geoms, area12

    geoms   = list(polygons_gdf.geometry.values)
    pieces  = defaultdict(list)   # eidx -> [geom clase12 en WGS84]
    covered = set()               # eidx con cobertura de algun tile valido

    for (r, c), meta in mb_meta.items():
        tile_geom = meta["bounds_geom"]                       # WGS84
        # Eventos que tocan este tile (9 tiles -> test directo, sin sjoin).
        eidx = [i for i in range(n)
                if geoms[i] is not None and not geoms[i].is_empty
                and geoms[i].intersects(tile_geom)]
        if not eidx:
            continue

        band_idx = band_for_year(year, meta["bands"])
        if band_idx < 1 or band_idx > meta["bands"]:
            print(f"  [WARN] band_idx={band_idx} fuera de rango "
                  f"(1-{meta['bands']}) tile ({r},{c}) anio {year}")
            continue

        tcrs = meta["crs"] or WGS84
        tile_path = tiles_map[(r, c)]
        try:
            with rasterio.open(tile_path) as src:
                for g in eidx:
                    geom   = geoms[g]
                    geom_r = (_to_local_crs(geom, tcrs)
                              if tcrs != WGS84 else geom)
                    minx, miny, maxx, maxy = geom_r.bounds
                    win = from_bounds(minx, miny, maxx, maxy,
                                      transform=src.transform)
                    # Pad de 2 px para no recortar bordes.
                    win = Window(win.col_off - 2, win.row_off - 2,
                                 win.width + 4, win.height + 4)
                    win = _round_window(win, src.width, src.height)
                    if win.width <= 0 or win.height <= 0:
                        continue
                    arr = src.read(band_idx, window=win)
                    tr  = src.window_transform(win)
                    covered.add(g)                       # cae sobre tile valido
                    is12 = (arr == target_class)         # clase K ya extraida
                    if not is12.any():
                        del arr, is12
                        continue
                    mask = is12.astype(np.uint8)
                    polys12 = [shape(gj) for gj, v in rasterio.features.shapes(
                        mask, mask=is12, transform=tr) if v == 1]
                    del arr, is12, mask
                    if not polys12:
                        continue
                    cls_geom = unary_union(polys12)
                    if tcrs != WGS84:
                        cls_geom = (gpd.GeoSeries([cls_geom], crs=tcrs)
                                    .to_crs(WGS84).iloc[0])
                    inter = cls_geom.intersection(geom)   # geom en WGS84
                    if (inter is not None) and (not inter.is_empty):
                        pieces[int(g)].append(inter)
        except Exception as e:
            print(f"  [WARN] {type(e).__name__} clase12 ({r},{c}) "
                  f"anio {year}: {e}")
        gc.collect()

    # Eventos cubiertos por algun tile -> default 0.0 (no NaN).
    for i in covered:
        area12[i] = 0.0

    # Consolidar piezas por evento y medir area en EPSG:3857 (m2, como AT_V8).
    if pieces:
        idxs   = list(pieces.keys())
        merged = [unary_union(pieces[i]) for i in idxs]
        gs = gpd.GeoSeries(merged, crs=WGS84).to_crs("EPSG:3857")
        ar = gs.area.values                     # m2 (v11.3.0: sin /10000)
        for k, i in enumerate(idxs):
            clip_geoms[i] = merged[k]
            area12[i]     = round(float(ar[k]), 2)

    return clip_geoms, area12


# --- Mediana ignorando NaN (para la cota por evento) -------------------------
def _nanmedian_safe(values):
    """Mediana de 'values' ignorando NaN; NaN si no queda ningun valido.
    Evita el RuntimeWarning 'All-NaN slice' de np.nanmedian a gran escala."""
    v = values[~np.isnan(values)]
    return float(np.median(v)) if v.size else np.nan


# --- Etiquetado por DOY + conectividad (v9.3.0) ------------------------------
def _label_by_doy(valid, ba_data, structure):
    """Etiqueta componentes conexas IMPONIENDO mismo DOY ademas de vecindad.

    Para cada DOY presente en los pixeles validos del mes se etiquetan por
    separado sus componentes (con la conectividad de 'structure') y se
    desplazan los ids para que sean globalmente unicos (1..n_total contiguos).
    Asi dos pixeles vecinos con DOY distinto quedan en eventos distintos."""
    labeled = np.zeros(valid.shape, dtype=np.int32)
    n_total = 0
    for d in np.unique(ba_data[valid]):            # DOY > 0 presentes en el mes
        m   = valid & (ba_data == d)
        lab = np.zeros(valid.shape, dtype=np.int32)
        ndimage.label(m, structure=structure, output=lab)
        n = int(lab.max())
        if n == 0:
            continue
        sel = lab > 0
        labeled[sel] = lab[sel] + n_total          # ids globalmente unicos
        n_total += n
    return labeled, n_total


# --- Extraccion de eventos de UNA banda (un mes) -----------------------------
def _extract_month_events(ba_data, dem_tile, transform,
                          year, month, structure):
    """Componentes conexas quemadas (>0) con el MISMO DOY (v9.3.0): la
    temporalidad (dia) condiciona el grupo.
    v11.0.0: SIN filtro de altitud; la cota solo entra como atributo (mediana).
    """
    valid = (ba_data > 0)
    if not valid.any():
        return []

    # Agrupamiento doble: temporal (mismo DOY) + espacial (conectividad).
    labeled, n_evt = _label_by_doy(valid, ba_data, structure)
    if n_evt == 0:
        return []

    ids = np.arange(1, n_evt + 1, dtype=np.int32)
    if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
        rng  = np.random.default_rng(
            np.random.SeedSequence([RANDOM_SEED, year, month]))
        keep = rng.random(n_evt) < SAMPLE_FRAC
        ids  = ids[keep]
        if ids.size == 0:
            return []
        sampled = np.where(np.isin(labeled, ids), labeled, 0).astype(np.int32)
    else:
        sampled = labeled

    # BurnDate = DOY del evento. Como ahora cada componente tiene un unico DOY,
    # la mediana devuelve ese mismo valor.
    BurnDate  = np.round(
        ndimage.median(ba_data.astype(np.float32), labeled, ids)
    ).astype(np.int32)
    # Elevation = MEDIANA de la cota dentro del evento (v11.0.0). Se ignora el
    # nodata del DEM (NaN) por evento. Si TODO el evento cae sobre nodata -> NaN
    # (sin cobertura DEM), sin emitir warning de 'all-NaN slice'.
    Elevation = np.asarray(
        ndimage.labeled_comprehension(
            dem_tile, labeled, ids, _nanmedian_safe, np.float32, np.nan),
        dtype=np.float32)

    geom_by_label = defaultdict(list)
    for gd, lv in rasterio.features.shapes(
        sampled, mask=(sampled > 0).astype(np.uint8), transform=transform,
    ):
        geom_by_label[int(lv)].append(shape(gd))

    id_to_idx = {int(e): i for i, e in enumerate(ids)}
    recs = []
    for eid, geoms in geom_by_label.items():
        i = id_to_idx.get(eid)
        if i is None:
            continue
        geom = geoms[0] if len(geoms) == 1 else unary_union(geoms)
        recs.append({
            "geometry"  : geom,
            "year"      : year,
            "month"     : month,
            "BurnDate"  : int(BurnDate[i]),
            "dem_median": round(float(Elevation[i]), 1),
            "event_uid" : f"BA_{year}_M{month:02d}_{eid}",
        })
    return recs


# --- Enriquecimiento de los eventos de un anio -------------------------------
def enrich_events(records, countries_gdf, regions_gdf,
                  mb_meta_c12, mb_meta_c3, year):
    """sjoin pais + COUNTRY_FILTER + region geografica + area + recorte por clase.

    Flujo:
      1) sjoin pais + filtro COUNTRY_FILTER (Peru).
      2) region geografica por representative_point ('region_geo'); se conservan
         SOLO REGION_1 y REGION_2.
      3) area_ha del evento completo (EPSG:3857, hectareas).
      4) area por clase (m2), cada una restringida a su region:
           REGION_1 (Sierra) -> clase 12 ; REGION_2 (Selva) -> clase 3.
         'cl12_m2' y 'cl3_m2' quedan NaN en la region contraria.

    Devuelve (gdf, gdf_clip):
      - gdf      : eventos BA enriquecidos.
      - gdf_clip : recortes BA ∩ claseK con columna 'clase' ('clase 12'/'clase 3')
                   o None si ningun evento intersecta su clase.
    """
    if not records:
        return None, None

    gdf = gpd.GeoDataFrame(records, crs=WGS84)
    gdf = spatial_join_3attempts(gdf, countries_gdf)
    gdf["ADM0_CODE"] = gdf["gaul0_code"]

    if COUNTRY_FILTER:
        gdf = gdf[gdf["gaul0_name"] == COUNTRY_FILTER].reset_index(drop=True)
    if len(gdf) == 0:
        return gdf, None

    # Region geografica; conservar solo REGION_1 / REGION_2.
    gdf["region_geo"] = assign_geo_region(gdf, regions_gdf)
    gdf = gdf[gdf["region_geo"].isin([REGION_1, REGION_2])].reset_index(drop=True)
    if len(gdf) == 0:
        return gdf, None

    # Area del evento completo en EPSG:3857 (ha; footprint del evento).
    m = gdf.to_crs("EPSG:3857")
    gdf["area_ha"] = np.round(m.geometry.area.values / 10_000, 2)
    del m

    # Area por clase (m2), restringida a su region. Se calcula sobre el
    # SUBCONJUNTO de cada region y se reasigna por INDICE real del gdf.
    n = len(gdf)
    gdf["cl12_m2"] = np.nan
    gdf["cl3_m2"]  = np.nan
    clip_geoms = [None] * n     # geometria del recorte por evento (o None)
    clip_class = [None] * n     # 'clase 12' / 'clase 3'

    plan = (
        (REGION_1, 12, MAPBIOMAS_TILES_C12, mb_meta_c12, "cl12_m2"),
        (REGION_2,  3, MAPBIOMAS_TILES_C3,  mb_meta_c3,  "cl3_m2"),
    )
    for region, target_class, tiles_map, mb_meta, col in plan:
        sub_idx = gdf.index[gdf["region_geo"] == region].tolist()
        if not sub_idx or not mb_meta:
            continue
        sub = gdf.loc[sub_idx].reset_index(drop=True)
        geoms_c, area_c = calc_class_clip(sub, year, mb_meta,
                                          tiles_map, target_class)
        for pos, gi in enumerate(sub_idx):          # pos en sub -> gi en gdf
            gdf.at[gi, col] = area_c[pos]
            g = geoms_c[pos]
            if g is not None and not g.is_empty:
                clip_geoms[gi] = g
                clip_class[gi] = f"clase {target_class}"

    gdf["cl12_m2"] = np.round(gdf["cl12_m2"].astype(float), 2)
    gdf["cl3_m2"]  = np.round(gdf["cl3_m2"].astype(float), 2)

    # Capa unica de recortes con columna 'clase' (area de la interseccion, m2).
    keep = [i for i in range(n) if clip_geoms[i] is not None]
    if keep:
        area_clip = [
            gdf["cl12_m2"].values[i] if clip_class[i] == "clase 12"
            else gdf["cl3_m2"].values[i]
            for i in keep
        ]
        gdf_clip = gpd.GeoDataFrame(
            {
                "event_uid":     gdf["event_uid"].values[keep],
                "year":          gdf["year"].values[keep],
                "month":         gdf["month"].values[keep],
                "clase":         [clip_class[i] for i in keep],
                "area_clase_m2": area_clip,
            },
            geometry=[clip_geoms[i] for i in keep], crs=WGS84)
    else:
        gdf_clip = None

    return gdf, gdf_clip


# --- Procesamiento de un anio (un mosaico BA, 12 meses) ----------------------
# Cache del DEM reproyectado, a nivel de PROCESO (= por worker). Clave = grid
# de destino (transform + forma + CRS). Si los mosaicos comparten grid entre
# anios (lo habitual), el DEM se reproyecta UNA sola vez y se reutiliza; si un
# anio tuviera otro grid, la clave no coincide y se reproyecta de nuevo. Asi la
# optimizacion es segura aunque cambie la resolucion/extension entre anios.
_DEM_CACHE = {}


def process_ba_year(year, ba_path, countries_gdf, regions_gdf,
                    mb_meta_c12, mb_meta_c3, dem_path,
                    gpkg_path, clip_gpkg_path, fw_main, fw_clip):
    structure = np.ones((3, 3), dtype=int)
    t0 = time.time()
    recs_year = []

    with rasterio.open(ba_path) as src, rasterio.open(dem_path) as dem_src:
        src_crs = src.crs
        n_bands = src.count

        # Ventana = ROI (acota memoria). DEM se reproyecta a este grid 1 vez.
        roi_local = _to_local_crs(roi_geom, src_crs)
        win = from_bounds(*roi_local.bounds, transform=src.transform)
        win = win.intersection(Window(0, 0, src.width, src.height))
        win = _round_window(win, src.width, src.height)
        if win.width <= 0 or win.height <= 0:
            print(f"  [WARN] anio {year}: el ROI no intersecta el mosaico.")
            return fw_main, fw_clip

        transform = src.window_transform(win)
        H, W = int(win.height), int(win.width)
        dst_crs = (src_crs or WGS84)

        # Caché por grid de destino: reproyectar el DEM (unico .tif) solo si no
        # esta cacheado para este mismo (transform, forma, CRS). El nodata del
        # DEM (0 = oceano/relleno GLO30) se convierte a NaN.
        grid_key = (transform.to_gdal(), H, W, str(dst_crs))
        dem_tile = _DEM_CACHE.get(grid_key)
        if dem_tile is None:
            dem_tile = np.full((H, W), np.nan, dtype=np.float32)
            try:
                rasterio.warp.reproject(
                    source=rasterio.band(dem_src, 1), destination=dem_tile,
                    src_transform=dem_src.transform, src_crs=dem_src.crs,
                    dst_transform=transform, dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                    src_nodata=dem_src.nodata, dst_nodata=np.nan,
                )
                _DEM_CACHE[grid_key] = dem_tile    # cachear solo si reproyecto OK
            except Exception as e:
                print(f"  [WARN] anio {year}: reproyeccion DEM fallida: {e}")

        # v11.0.0: SIN filtro de altitud. El DEM (dem_tile) se conserva solo
        # como fuente de la MEDIANA de cota por evento. Si la reproyeccion fallo,
        # dem_tile queda todo-NaN y las medianas saldran NaN (sin cobertura).
        n_months = min(12, n_bands)
        for month in range(1, n_months + 1):
            try:
                ba = src.read(month, window=win)
            except Exception:
                continue
            recs = _extract_month_events(
                ba, dem_tile, transform, year, month, structure)
            if recs:
                recs_year.extend(recs)
            del ba

    # Defensa: si el mosaico no estuviera en WGS84, reproyectar geometrias
    if recs_year and src_crs is not None and src_crs != WGS84:
        g = gpd.GeoDataFrame(recs_year, crs=src_crs).to_crs(WGS84)
        recs_year = g.to_dict("records")

    gdf, gdf_clip = enrich_events(recs_year, countries_gdf, regions_gdf,
                                  mb_meta_c12, mb_meta_c3, year)
    if gdf is None or len(gdf) == 0:
        print(f"  [INFO] anio {year}: 0 eventos tras enriquecer "
              f"({timedelta(seconds=int(time.time() - t0))}).")
        return fw_main, fw_clip

    cols = [c for c in COL_ORDER if c in gdf.columns]
    gdf  = gdf[cols]
    gdf.to_file(gpkg_path, driver="GPKG", mode="w" if fw_main else "a")
    fw_main = False

    n_clip = 0
    if gdf_clip is not None and len(gdf_clip):
        gdf_clip.to_file(clip_gpkg_path, driver="GPKG",
                         mode="w" if fw_clip else "a")
        fw_clip = False
        n_clip = len(gdf_clip)

    print(f"  [OK] anio {year}: {len(gdf)} eventos (+{n_clip} recortes clase12/3) "
          f"-> {gpkg_path.name} ({timedelta(seconds=int(time.time() - t0))})")
    del gdf, gdf_clip
    gc.collect()
    return fw_main, fw_clip


# --- Worker (subconjunto de anios) -------------------------------------------
def run_worker(worker_id, n_workers):
    t0 = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)
    print(f"\n{'='*58}\n  WORKER {worker_id}/{n_workers} — BurnedAreas v11")
    _modo = "PRODUCCION" if run_tag == "vf" else f"TEST (eventos={SAMPLE_FRAC})"
    print(f"  MODO: {_modo}\n{'='*58}")

    year_files = discover_ba_year_files()
    years = sorted(year_files)
    my_years = years[worker_id::n_workers]
    print(f"  Anios totales: {len(years)} | este worker: {my_years}")
    if not my_years:
        print("  [INFO] Sin anios asignados.")
        return

    countries_gdf = load_countries(
        data_dir / "GAUL_2024_L1.shp", COUNTRIES_ADM0, roi_geom)
    regions_gdf = load_regions(REGIONS_PATH, REGION_FIELD, roi_geom)
    print(f"  Regiones cargadas: {sorted(regions_gdf['region_geo'].unique())} "
          f"(se conservan: {REGION_1}, {REGION_2})")
    mb_meta_c12 = get_mapbiomas_metadata(MAPBIOMAS_TILES_C12)
    mb_meta_c3  = get_mapbiomas_metadata(MAPBIOMAS_TILES_C3)
    print(f"  Tiles clase12: {len(mb_meta_c12)}/{len(MAPBIOMAS_TILES_C12)} | "
          f"clase3: {len(mb_meta_c3)}/{len(MAPBIOMAS_TILES_C3)}")
    # DEM: el orquestador ya lo construyo/valido en el Paso 0. Aqui basta con
    # obtener la ruta (cache hit si existe; validate=False para no revalidar).
    dem_path = ensure_dem_bruto(RAW_TILES_DIR, DEM_PATH, DEM_BBOX,
                                nodata=DEM_NODATA, rebuild=False, validate=False)

    # Intermedios por worker (scratch): merge_and_export los borra al terminar.
    gpkg_path = test_dir / f"BurnedAreas_MODIS_V11_{run_tag}_w{worker_id}.gpkg"
    clip_path = test_dir / f"BurnedAreas_MODIS_V11_{run_tag}_w{worker_id}_clip.gpkg"
    test_dir.mkdir(parents=True, exist_ok=True)
    for p in (gpkg_path, clip_path):
        if p.exists():
            p.unlink()

    fw_main, fw_clip = True, True
    for yr in my_years:
        fw_main, fw_clip = process_ba_year(
            yr, year_files[yr], countries_gdf, regions_gdf,
            mb_meta_c12, mb_meta_c3, dem_path,
            gpkg_path, clip_path, fw_main, fw_clip)

    print(f"  TOTAL worker {worker_id}: "
          f"{timedelta(seconds=int(time.time() - t0))}")


# --- Merge + exportacion final -----------------------------------------------
def merge_and_export(run_tag, n_workers, base_name=None):
    t = time.time()
    print(f"\n{'-'*58}\n  Merge de resultados de workers\n{'-'*58}")

    worker_paths = [test_dir / f"BurnedAreas_MODIS_V11_{run_tag}_w{k}.gpkg"
                    for k in range(n_workers)]
    existing = [p for p in worker_paths if p.exists()]
    if not existing:
        print("  [WARN] No hay GPKGs de workers para merge.")
        return None
    missing = [p.name for p in worker_paths if not p.exists()]
    if missing:
        print(f"  [INFO] Workers sin GPKG (vacios o fallidos): {missing}")

    base     = base_name or f"BurnedAreas_MODIS_V11_{run_tag}"
    os.makedirs(output_dir, exist_ok=True)
    gpkg_out = output_dir / f"{base}.gpkg"     # GPKG unico con 2 capas
    csv_out  = output_dir / f"{base}.csv"      # CSV solo de eventos
    for p in (gpkg_out, csv_out):              # evitar append sobre corridas viejas
        if p.exists():
            p.unlink()

    # [MEM-STREAM v11.1.0] Se procesa 1 GPKG de worker a la vez: se lee, se
    # escribe (append) a la capa 'eventos' y al CSV, y se libera. Nunca se
    # mantiene el dataset completo en memoria. Coste: no devolvemos el
    # GeoDataFrame completo (el AOI se relee del GPKG por bbox mas abajo).
    print(f"  Escribiendo {len(existing)} GPKGs en streaming...")
    total, first = 0, True
    for p in existing:
        g = gpd.read_file(p)
        g = g[[c for c in COL_ORDER if c in g.columns]]
        g.to_file(gpkg_out, layer="eventos", driver="GPKG",
                  mode="w" if first else "a")
        g.drop(columns=["geometry"]).to_csv(
            csv_out, mode="w" if first else "a",
            header=first, index=False, encoding="utf-8-sig")
        total += len(g)
        first = False
        del g
        gc.collect()
    print(f"  Total eventos: {total}")
    print(f"  [OK] GPKG (layer eventos) -> {gpkg_out.name}")
    print(f"  [OK] CSV  (eventos)       -> {csv_out.name}")

    # --- Capa 2: recortes BA dentro de su clase (ROI-wide), en streaming -----
    # Una sola capa 'clip_clases' con columna 'clase' ('clase 12'/'clase 3').
    clip_worker_paths = [
        test_dir / f"BurnedAreas_MODIS_V11_{run_tag}_w{k}_clip.gpkg"
        for k in range(n_workers)]
    clip_existing = [p for p in clip_worker_paths if p.exists()]
    if clip_existing:
        print(f"  Escribiendo {len(clip_existing)} GPKGs de recortes...")
        total_c = 0
        for p in clip_existing:
            gc_ = gpd.read_file(p)
            # 'clip_clases' se anade al GPKG existente (mode='a' preserva
            # 'eventos'); OGR crea la capa en el primer append.
            gc_.to_file(gpkg_out, layer="clip_clases", driver="GPKG", mode="a")
            total_c += len(gc_)
            del gc_
            gc.collect()
        print(f"  [OK] GPKG (layer clip_clases) -> {gpkg_out.name} "
              f"({total_c} pol.)")
    else:
        print("  [INFO] Sin recortes BA-clase (12/3) que anadir.")

    # Limpieza: los GPKG intermedios por worker (eventos + recortes) dejan de
    # ser salida.
    removed = 0
    for p in existing + clip_existing:
        try:
            p.unlink()
            removed += 1
        except Exception as e:
            print(f"  [WARN] no pude borrar {p.name}: {e}")
    print(f"  [OK] Limpieza: {removed} GPKG de workers eliminados")

    timer(f"Merge ({total} features)", t)
    return gpkg_out


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, ba_year_files,
                             gdf_result, output_dir, base_name):
    if aoi_geom is None:
        print("  [INFO] AOI None: sin capas de ejemplo.")
        return
    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'-'*58}\n  Exportacion cartografica (AOI)\n{'-'*58}")

    # 1) DEM recortado al AOI (fichero unico ya materializado por ensure_dem_bruto).
    dem_out = output_dir / f"{base_name}_aoi_dem.tif"
    try:
        with rasterio.open(dem_path) as src:
            aoi_local = _to_local_crs(aoi_geom, src.crs)
            out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                       all_touched=True)
            out_meta = {**src.meta, "driver": "GTiff", "compress": "lzw",
                        "height": out_img.shape[1], "width": out_img.shape[2],
                        "transform": out_tr}
        with rasterio.open(dem_out, "w", **out_meta) as dst:
            dst.write(out_img)
        print(f"  [OK] DEM              -> {dem_out.name}")
    except Exception as e:
        print(f"  [WARN] DEM clip: {e}")

    # Eventos recortados al AOI (para el raster BA y la capa de resultados).
    # gdf_result ya viene pre-filtrado por bbox del AOI (o None).
    if gdf_result is None or len(gdf_result) == 0:
        print("  [INFO] Sin eventos en el AOI; no se exportan capas vectoriales.")
        timer("Exportacion cartografica (AOI)", t)
        return
    gdf_aoi = gpd.clip(gdf_result, aoi_geom).reset_index(drop=True)

    # 2) MODIS BA recortado al AOI -> un raster multibanda POR ANIO con SOLO los
    #    meses que tienen eventos en el AOI (Correccion 6, opcion C). Conserva la
    #    dimension mes (NO se colapsa con max). Cada banda se describe 'ba_AAAA_MM'.
    #    Un archivo por anio garantiza grid identico entre sus bandas (misma
    #    fuente) y evita problemas de alineacion entre anios.
    if {"year", "month"}.issubset(gdf_aoi.columns) and len(gdf_aoi) > 0:
        ym = (gdf_aoi[["year", "month"]].dropna().astype(int)
              .drop_duplicates())
        exported = []
        for yr in sorted(ym["year"].unique()):
            months_yr = sorted(ym.loc[ym["year"] == yr, "month"].unique())
            p = ba_year_files.get(int(yr))
            if p is None:
                print(f"  [WARN] BA {yr}: sin mosaico para ese anio; se omite.")
                continue
            try:
                with rasterio.open(p) as src:
                    aoi_local = _to_local_crs(aoi_geom, src.crs)
                    out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                               all_touched=True)
                    meta = src.meta.copy()
                n_b = out_img.shape[0]
                sel = [int(m) for m in months_yr if 1 <= m <= n_b]
                if not sel:
                    continue
                stack = out_img[[m - 1 for m in sel], :, :]   # bandas-mes
                meta.update(driver="GTiff", compress="lzw",
                            count=stack.shape[0], height=stack.shape[1],
                            width=stack.shape[2], transform=out_tr)
                ba_out = output_dir / f"{base_name}_aoi_ba_{yr}.tif"
                with rasterio.open(ba_out, "w", **meta) as dst:
                    dst.write(stack)
                    for i, m in enumerate(sel, start=1):
                        dst.set_band_description(i, f"ba_{yr}_{m:02d}")
                exported.append((int(yr), sel))
                print(f"  [OK] MODIS BA {yr}    -> {ba_out.name} "
                      f"(meses con eventos: {sel})")
                del stack, out_img
            except Exception as e:
                print(f"  [WARN] BA {yr} clip: {e}")
        if not exported:
            print("  [INFO] Sin (anio,mes) con eventos en el AOI para BA.")
    else:
        print("  [INFO] Sin eventos en el AOI; no se exporta raster BA.")

    # 3) Resultados dentro del AOI -> GPKG + CSV
    if len(gdf_aoi) > 0:
        gpkg_aoi = output_dir / f"{base_name}_aoi_results.gpkg"
        csv_aoi  = output_dir / f"{base_name}_aoi_results.csv"
        gdf_aoi.to_file(gpkg_aoi, layer="eventos_aoi", driver="GPKG")
        gdf_aoi.drop(columns=["geometry"]).to_csv(
            csv_aoi, index=False, encoding="utf-8-sig")
        print(f"  [OK] Resultados AOI   -> {gpkg_aoi.name} ({len(gdf_aoi)} pol.)")
        print("  [NOTE] area_ha es del evento COMPLETO (no recortado al AOI).")
    else:
        print("  [INFO] Ningun resultado intersecta el AOI.")
    timer("Exportacion cartografica (AOI)", t)


# --- Validacion previa -------------------------------------------------------
def _preprocess_only():
    t = time.time()
    print("  Paso 0: validando fuentes de entrada...")

    yf = discover_ba_year_files()
    print(f"  MODIS BA: {len(yf)} mosaicos anuales {sorted(yf)}")
    if not yf:
        print(f"  [ERROR] Ningun mosaico BA en {BA_MOSAIC_DIR}.")
        return False

    for cls, tiles_map, col in (("clase12", MAPBIOMAS_TILES_C12, "cl12_m2"),
                                ("clase3",  MAPBIOMAS_TILES_C3,  "cl3_m2")):
        mbm = get_mapbiomas_metadata(tiles_map)
        print(f"  Tiles {cls}: {len(mbm)}/{len(tiles_map)} disponibles")
        if len(mbm) == 0:
            print(f"  [WARN] Sin tiles {cls}; {col} sera NaN.")
        else:
            faltan = [f"r{r}c{c}" for (r, c) in tiles_map if (r, c) not in mbm]
            if faltan:
                print(f"  [WARN] Cobertura {cls} PARCIAL: faltan {len(faltan)} "
                      f"tiles -> {faltan}. Los eventos en esa zona tendran "
                      f"{col}=NaN.")
            else:
                print(f"  [INFO] Cobertura {cls} completa (9/9 tiles).")

    # DEM bruto: construir-si-no-existe (cache; compartido con AT). Se hace UNA
    # vez aqui, antes de lanzar workers, con validacion de integridad de tiles.
    try:
        ensure_dem_bruto(RAW_TILES_DIR, DEM_PATH, DEM_BBOX,
                         nodata=DEM_NODATA, rebuild=DEM_REBUILD,
                         validate=DEM_VALIDATE, max_workers=DEM_VALIDATE_WORKERS)
    except Exception as e:
        print(f"  [ERROR] No pude asegurar el DEM: {e}")
        return False

    gaul_path = data_dir / "GAUL_2024_L1.shp"
    if not gaul_path.exists():
        print(f"  [ERROR] Shapefile de paises no encontrado: {gaul_path}")
        return False

    if not Path(REGIONS_PATH).exists():
        print(f"  [ERROR] Shapefile de regiones no encontrado: {REGIONS_PATH}")
        return False
    try:
        _rg = load_regions(REGIONS_PATH, REGION_FIELD, roi_geom)
        _vals = set(_rg["region_geo"].unique())
        print(f"  Regiones: {sorted(_vals)} (se conservan: {REGION_1}, {REGION_2})")
        faltan_reg = [r for r in (REGION_1, REGION_2) if r not in _vals]
        if faltan_reg:
            print(f"  [WARN] Estas regiones NO existen en el shapefile "
                  f"(revisa REGION_1/REGION_2 y el campo '{REGION_FIELD}'): "
                  f"{faltan_reg}. Sus eventos quedaran vacios.")
    except Exception as e:
        print(f"  [ERROR] No pude leer el shapefile de regiones: {e}")
        return False

    timer("Validacion de fuentes", t)
    return True


# --- Orquestador -------------------------------------------------------------
def run_orchestrator():
    t0      = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)

    if not _preprocess_only():
        sys.exit(1)

    year_files = discover_ba_year_files()
    years = sorted(year_files)
    n_workers = max(1, min(N_WORKERS, len(years)))

    print(f"\n{'='*58}")
    print(f"  ORQUESTADOR — BurnedAreas_MODIS v11 (paralelo por anio)")
    print(f"  Anios   : {years}")
    print(f"  Workers : {n_workers}")
    print(f"  Modo    : {run_tag}")
    print(f"{'='*58}")

    def _launch(k):
        r = subprocess.run(
            [sys.executable, __file__,
             "--worker-id", str(k), "--n-workers", str(n_workers)],
            text=True, capture_output=True)
        if r.stdout:
            print("\n".join(r.stdout.strip().splitlines()[-10:]))
        if r.returncode != 0:
            print(f"\n  [FAIL] worker {k}:\n{r.stderr[-600:]}")
        return k, r.returncode == 0

    ok, fail = [], []
    with ThreadPoolExecutor(max_workers=n_workers) as exe:
        futures = {exe.submit(_launch, k): k for k in range(n_workers)}
        for f in as_completed(futures):
            k, good = f.result()
            (ok if good else fail).append(k)
            print(f"  {'[OK]  ' if good else '[FAIL]'} worker {k} terminado")

    print(f"\n  Workers OK: {sorted(ok)} | FAIL: {sorted(fail)}")

    # Corrida parcial: si fallaron workers, faltan anios ENTEROS (round-robin).
    missing_years = sorted({y for k in fail for y in years[k::n_workers]})
    partial = len(fail) > 0
    base_name = (f"BurnedAreas_MODIS_V11_{run_tag}"
                 + ("_PARCIAL" if partial else ""))
    if partial:
        print(f"\n  [ERROR] Corrida PARCIAL: fallaron {len(fail)} worker(s) "
              f"-> {sorted(fail)}")
        print(f"  [ERROR] Anios AUSENTES del dataset final: {missing_years}")
        print(f"  [ERROR] La salida se marca con sufijo '_PARCIAL' para que no "
              f"se confunda con un dataset completo.")

    if ok:
        gpkg_out = merge_and_export(run_tag, n_workers, base_name)
        if gpkg_out is not None:
            aoi_geom = load_aoi()
            # AOI: se relee del GPKG SOLO la ventana (bbox) del AOI -> memoria
            # acotada (no se carga el dataset completo).
            gdf_aoi_src = None
            if aoi_geom is not None:
                try:
                    gdf_aoi_src = gpd.read_file(
                        gpkg_out, layer="eventos",
                        bbox=tuple(aoi_geom.bounds))
                except Exception as e:
                    print(f"  [WARN] no pude leer el subset AOI del GPKG: {e}")
            save_cartographic_layers(
                aoi_geom, DEM_PATH, year_files, gdf_aoi_src,
                output_dir, base_name)
            # Head para el log (solo unas filas, sin cargar todo).
            cols_show = ["event_uid", "year", "month", "BurnDate", "dem_median",
                         "region_geo", "cl12_m2", "cl3_m2",
                         "area_ha", "gaul0_name"]
            try:
                head = gpd.read_file(gpkg_out, layer="eventos", rows=5)
                print(head[[c for c in cols_show if c in head.columns]])
            except Exception as e:
                print(f"  [INFO] no pude imprimir head: {e}")

    timer(f"TOTAL orquestador ({len(ok)}/{n_workers} workers OK)", t0)


# --- Ejecucion ---------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BurnedAreas_MODIS pipeline v11")
    parser.add_argument("--worker-id", type=int, default=None,
                        help="ID del worker (modo subproceso)")
    parser.add_argument("--n-workers", type=int, default=None,
                        help="Numero total de workers")
    args = parser.parse_args()

    if args.worker_id is not None:
        nw = args.n_workers or N_WORKERS
        run_worker(args.worker_id, nw)
    else:
        run_orchestrator()