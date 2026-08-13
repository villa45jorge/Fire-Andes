# -*- coding: utf-8 -*-
import os
# Cache GDAL moderada. En GFA cada worker abre 1 shapefile anual y, para
# MapBiomas/DEM, ventanas pequenas por evento. 512 MB/worker es suficiente.
os.environ.setdefault("GDAL_CACHEMAX", "512")
# Silenciar el ruido de libtiff (warnings cosmeticos de etiquetas TIFF).
os.environ.setdefault("CPL_LOG", os.devnull)
"""
Version 1.0.0  (prefijo de salida "GFA_V1")
@author: jvilla

======================= Cambios GFA_V1 (respecto a GFA_V0) =======================
[ECO-1]  ZONIFICACION POR ECORREGIONES. Se reemplaza 'region-geografica.shp'
         (campo 'nombre', Sierra/Selva) por el GPKG 'Ecoreg_peru_VF.gpkg'
         (campo 'ECO_NAME'). La capa se autodetecta si el GPKG trae una sola
         (ver load_ecoregions(); portado de AT_V9). El GPKG cuelga de
         GFA_BASE/1_input/Ecorregions_2017_Peru/, NO del CLUSTER_ROOT.
[ECO-2]  SIN SELECCION DE VALORES. Desaparece el filtro isin([Sierra,Selva]):
         ahora se clasifican y CONSERVAN TODOS los perimetros de TODAS las
         ecorregiones. Cada evento recibe un ECO_NAME UNICO por su
         representative_point. Los perimetros en Peru fuera de toda ecorregion
         se conservan con ECO_NAME='sin_region'.
[ECO-3]  DOS CLASES PARA TODOS. Se elimina el mapeo region->clase. Cada evento
         recibe SIEMPRE las DOS areas MapBiomas:
             cl12 = area de clase 12 (Grassland) dentro del poligono
             cl3  = area de clase 3  (Forest)    dentro del poligono
         calculadas sobre el POLIGONO COMPLETO (independientes del ECO_NAME).
[ECO-4]  UNIDADES km2 + nombres cl12/cl3 (antes cl12_m2/cl3_m2 en m2). El
         area se mide en EPSG:3857 y se divide por 1e6. La capa 'clip_clases'
         emite HASTA 2 filas por evento (clase 12 y clase 3) con 'area_clase_km2'.
[ECO-5]  INVERSION DE SEMANTICA NaN (igual que AT_V9/MOD-12): cl12=NaN (o cl3)
         significa AHORA, EXCLUSIVAMENTE, 'sin cobertura de raster MapBiomas'.
         En V0, cl12=NaN podia leerse como 'el evento es Selva'. Cualquier
         analisis aguas abajo que usara ese NaN como proxy de region debe
         revisarse.
[ECO-6]  Prefijo de salida "GFA_V0" -> "GFA_V1".

Base   : script_BA_V11.py (v11.3.0)
Logica : IGUAL que BA_V11 en enriquecimiento (pais, region geografica, DEM,
         MapBiomas clase 12/3, areas, salidas GPKG+CSV), pero la FUENTE de
         eventos cambia por completo: ya no se detectan desde el raster MODIS
         BA (MCD64A1); ahora cada evento es un POLIGONO de un shapefile GFA.

======================= Cambios GFA_V1 (respecto a BA_V11) =======================
[SRC-1]  FUENTE DE EVENTOS = shapefiles de perimetros GFA, uno por anio:
         '{PERIM_DIR}/GFA_v20250411_perimeters_{anio}.shp'. Cada FILA es un
         evento (poligono de area quemada). CRS declarado: EPSG:4326 (WGS84).
[SRC-2]  La fecha del evento sale de la columna 'start_date' (formato YYYY-MM-DD).
         De ella se derivan 'year' y 'month' (para igualar el analisis de
         MCD64A1, que trabajaba a nivel anio-mes). GFA NO trae DOY, asi que la
         columna 'BurnDate' de BA_V11 DESAPARECE; en su lugar se guarda
         'start_date' (fecha completa) en la salida.
[DROP-1] Se ELIMINA toda la deteccion de eventos por raster: lectura de bandas
         (meses), _label_by_doy, _extract_month_events, componentes conexas,
         cache de DEM reproyectado por grid y el uso de scipy.ndimage.
[DEM-1]  'dem_median' ya NO se calcula por labeled_comprehension sobre un DEM
         reproyectado a la grilla del raster. Ahora es una MEDIANA ZONAL por
         poligono: se lee la ventana del DEM que cubre el evento, se rasteriza
         el poligono y se toma np.median ignorando nodata del DEM. Si el DEM no
         cubre el evento -> NaN (misma semantica que antes).
[AOI-1]  save_cartographic_layers ya NO exporta rasters BA recortados al AOI
         (no hay raster de origen). Conserva el recorte del DEM y el GPKG+CSV de
         los eventos que caen en el AOI.
[COLS-NATIVE] Se CONSERVAN todas las columnas nativas del shapefile GFA. En la
         salida se emiten las columnas estandar (COL_ORDER) primero, luego las
         nativas y 'geometry' al final (_order_columns). Si una columna nativa
         choca con un nombre generado por el pipeline (p.ej. 'year'), la nativa
         se renombra con sufijo '_gfa'. La 'start_date' nativa no se altera.
[VER]    Prefijo de salida "V11" -> "GFA_V1".

--- KEEP (identico a BA_V11) ------------------------------------------------------
  - Filtro de pais (Peru) via spatial_join_3attempts + COUNTRY_FILTER.
  - Region geografica por representative_point (Sierra=clase12 / Selva=clase3).
  - MapBiomas: tiles pre-extraidos clase 12/3, band_for_year anclando la ULTIMA
    banda a YEAR_MAX (2024). calc_class_clip sin cambios.
  - DEM bruto materializado una vez (ensure_dem_bruto), cache compartida con AT.
  - area_ha del evento completo en EPSG:3857 (misma decision que BA_V11; ver
    nota [MEJORA-AREA] mas abajo si se quiere area equivalente).
  - Salidas: GPKG (capas 'eventos' + 'clip_clases') + CSV de eventos, streaming
    en el merge, paralelizacion por anio (round-robin), sufijo _PARCIAL si fallan
    workers.

--- DECISIONES (resueltas) --------------------------------------------------------
  [D1] Rutas: GFA_BASE queda como PLACEHOLDER a editar con la ruta EXACTA en el
       cluster. DEM, MapBiomas, regiones y GAUL vuelven al cluster (CLUSTER_ROOT),
       identicos a BA_V11.
  [D2] 'start_date' en formato YYYY-MM-DD (parseo con format explicito + fallback).
  [D3] Sin BurnDate: GFA no trae DOY; se trabaja con start_date. event_uid =
       'GFA_{anio_fichero}_{idx:06d}' (anio = particion/nombre del fichero); las
       columnas year/month vienen de start_date. Suelen coincidir; se avisa si no.
  [D4] Periodo de estudio 2001-2024 (YEAR_MIN=2001, YEAR_MAX=2024). Eventos fuera
       de ese rango se descartan (avisando) para que band_for_year sea valido.

  [MEJORA-AREA] area_ha / areas de clase se miden en EPSG:3857 (~6-12% de sesgo
       a estas latitudes). Para area verdadera conviene un CRS equivalente
       (p.ej. ESRI:102033 South America Albers). Se deja 3857 por comparabilidad
       con el pipeline AT; cambiar solo si se decide romper esa comparabilidad.
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
from shapely.geometry import box, shape
from shapely.ops import unary_union
from collections import defaultdict
from rasterio.features import rasterize
from rasterio.mask import mask as rio_mask
from rasterio.windows import Window, from_bounds
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
# GFA: producto de perimetros. #### EDITAR ####: pon aqui la ruta EXACTA del GFA
# en el cluster. El resto de subrutas (1_input, 3_output, ...) cuelga de esta.
GFA_BASE      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/GFA")   # <<< EDITAR
data_dir      = GFA_BASE / "1_input"
processed_dir = GFA_BASE / "2_processed"
output_dir    = GFA_BASE / "3_output"
test_dir      = GFA_BASE / "4_test"

# --- GFA: shapefiles de perimetros (1 por anio; cada fila = 1 evento) --------
PERIM_DIR        = data_dir / "SHP_perimeters"
PERIM_GLOB       = "GFA_*_perimeters_*.shp"   # fallback a "*.shp" si no matchea
START_DATE_FIELD = "start_date"               # columna con la fecha (YYYY-MM-DD)

# --- Inputs COMPARTIDOS con AT/BA (en el cluster; identicos a BA_V11) --------
# DEM, MapBiomas y regiones viven bajo MCD14ML; GAUL bajo MCD64A1. Se referencian
# por ruta absoluta para reutilizar los MISMOS ficheros que AT/BA sin duplicar.
CLUSTER_ROOT  = Path("/home/villaramosj/scratch_villaramosj/test_phd/data")
MCD14ML_INPUT = CLUSTER_ROOT / "MCD14ML" / "1_input"
MAPBIOMAS_DIR = CLUSTER_ROOT / "MCD14ML" / "3_output"
MAPBIOMAS_GRID = 3


def _tiles_for_class(stem):
    """Grilla 3x3 de rutas '{stem}_r{r}c{c}.tif'."""
    return {(r, c): MAPBIOMAS_DIR / f"{stem}_r{r}c{c}.tif"
            for r in range(MAPBIOMAS_GRID) for c in range(MAPBIOMAS_GRID)}


MAPBIOMAS_TILES_C12 = _tiles_for_class("clase12")   # grasslands (clase 12)
MAPBIOMAS_TILES_C3  = _tiles_for_class("clase3")    # forest     (clase 3)

# --- DEM bruto (patron AT_V8; cache compartida) [D1: VERIFICAR] --------------
DEM_PATH      = MCD14ML_INPUT / "mosaico_peru_bruto.tif"   # DEM materializado
RAW_TILES_DIR = MCD14ML_INPUT / "copernicus_dem_andes"     # None si ya tienes el .tif
DEM_BBOX      = (-81.5, -18.6, -68.5, 0.2)   # lon_min, lat_min, lon_max, lat_max
DEM_NODATA    = 0        # 0 = oceano/relleno (GLO30 via GEE no declara nodata)
DEM_REBUILD   = False
DEM_VALIDATE         = True
DEM_VALIDATE_WORKERS = 8

# --- Paises (GAUL) -----------------------------------------------------------
GAUL_PATH = CLUSTER_ROOT / "MCD64A1" / "1_input" / "GAUL_2024_L1.shp"

# --- Ecorregiones del Peru [ECO-1] (RESOLVE Ecoregions 2017 recortado) -------
# GPKG bajo GFA_BASE/1_input (NO en el CLUSTER_ROOT compartido). Campo ECO_NAME.
# ECO_LAYER=None -> autodeteccion; si el GPKG es multicapa, load_ecoregions aborta
# y lista las capas (evita el fallo silencioso de leer la 1a capa sin avisar).
ECO_PATH  = data_dir / "Ecorregions_2017_Peru" / "Ecoreg_peru_VF.gpkg"
ECO_FIELD = "ECO_NAME"
ECO_LAYER = None
SIN_REGION = "sin_region"   # [ECO-2] eventos en Peru fuera de toda ecorregion

# [ECO-3] Ambas clases MapBiomas para TODOS los eventos (ya no hay mapeo zona->clase).
CLASS_1 = 12   # cl12 = Grassland
CLASS_2 = 3    # cl3  = Forest

# --- Area de Interes (AOI) — solo recorte de capas de ejemplo ----------------
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales -----------------------------------------------------
ROI_BBOX       = (-80.0, -20.0, -60.0, 1.0)
YEAR_MIN       = 2001
YEAR_MAX       = 2024     # [D4] ancla de banda MapBiomas, NO el ultimo anio GFA
COUNTRIES_ADM0 = [178, 184, 185, 190, 207]
COUNTRY_FILTER = "Peru"

YEARS_RUN = list(range(YEAR_MIN, YEAR_MAX + 1))


def band_for_year(year, n_bands):
    """Indice de banda MapBiomas (1-based) anclando la ULTIMA banda a YEAR_MAX."""
    return year - YEAR_MAX + n_bands


MAPBIOMAS_EXTENT_BUFFER_DEG = 0.005
WGS84 = CRS.from_epsg(4326)
roi_geom = box(*ROI_BBOX)

# --- Submuestreo (solo tests) ------------------------------------------------
# SAMPLE_FRAC: fraccion de EVENTOS conservados por anio (muestreo Bernoulli).
#   Produccion: None. <1 = modo TEST.
SAMPLE_FRAC = None
RANDOM_SEED = 42

# --- Workers -----------------------------------------------------------------
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# Orden de columnas de salida. La fecha del evento es 'start_date' (YYYY-MM-DD);
# GFA no trae DOY (BurnDate), asi que esa columna desaparece respecto a BA_V11.
# Las columnas NATIVAS del shapefile se CONSERVAN: se emiten despues de estas
# (ver _order_columns) y antes de 'geometry'.
COL_ORDER = [
    "event_uid", "year", "month", "start_date", "dem_median",
    "ECO_NAME", "cl12", "cl3", "ADM0_CODE", "gaul0_code",
    "gaul0_name", "area_ha", "geometry",
]

# Nombres que el pipeline GENERA. Si el shapefile nativo trae alguno de estos
# (salvo 'start_date', que ES nativa), la columna nativa se renombra con sufijo
# '_gfa' para no perder el dato ni pisar la columna calculada.
RESERVED_GENERATED = {
    "event_uid", "year", "month", "dem_median", "ECO_NAME",
    "cl12", "cl3", "ADM0_CODE", "gaul0_code", "gaul0_name", "area_ha",
    "index_right", "_match",
}


def _order_columns(gdf):
    """Devuelve las columnas ordenadas: primero las estandar de COL_ORDER que
    existan, luego las NATIVAS/extra (orden estable de aparicion) y 'geometry'
    al final. No descarta ninguna columna."""
    std = [c for c in COL_ORDER if c in gdf.columns and c != "geometry"]
    extra = [c for c in gdf.columns
             if c not in std and c != "geometry"]
    tail = ["geometry"] if "geometry" in gdf.columns else []
    return std + extra + tail


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


def _fix_geometry(geom):
    """Repara geometrias invalidas (autointersecciones) sin cambiar el area
    de forma significativa. Devuelve None si no se puede reparar."""
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid:
        return geom
    try:
        from shapely.validation import make_valid
        g = make_valid(geom)
    except Exception:
        g = geom.buffer(0)
    return g if (g is not None and not g.is_empty) else None


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
    if not roi_geom.contains(aoi_geom):
        print("  [WARN] El AOI no esta totalmente dentro del ROI; las capas de "
              "ejemplo pueden quedar incompletas.")
    return aoi_geom


# --- [ECO-1] Ecorregiones: carga (portado de AT_V9) --------------------------
def load_ecoregions(eco_path, layer=ECO_LAYER, field=ECO_FIELD):
    """Carga el GPKG de ecorregiones, reproyecta a EPSG:4326 y devuelve un
    GeoDataFrame con [field, 'geometry'].

    - layer=None -> autodetecta. Si el GPKG tiene mas de una capa, aborta y las
      lista (un GPKG multicapa leido sin 'layer' devuelve la 1a SIN avisar:
      fallo silencioso que preferimos convertir en error).
    - Repara geometrias invalidas (RESOLVE Ecoregions trae self-intersections):
      make_valid con fallback a buffer(0).
    - NO se recorta al ROI: el representative_point de cada perimetro ya cae en
      Peru; recortar poligonos de ecorregion solo generaria slivers de borde.
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

    eco = eco[[field, "geometry"]].to_crs("EPSG:4326")
    eco = eco[eco.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()

    n_bad = int((~eco.geometry.is_valid).sum())
    if n_bad:
        print(f"  [WARN] {n_bad} geometria(s) invalida(s) -> reparando")
        try:
            eco["geometry"] = eco.geometry.make_valid()
        except AttributeError:
            eco["geometry"] = eco.geometry.buffer(0)

    n_null = int(eco[field].isna().sum())
    if n_null:
        print(f"  [WARN] {n_null} poligono(s) con {field} nulo -> los eventos "
              f"que caigan ahi quedaran como '{SIN_REGION}'")

    print(f"  Ecorregiones distintas en la capa: {eco[field].nunique()}")
    return eco.reset_index(drop=True)


def assign_ecoregion(gdf, eco_gdf, field=ECO_FIELD):
    """[ECO-2] Asigna ECO_NAME UNICO a cada evento por su representative_point.

    Los eventos cuyo punto no cae en ninguna ecorregion (o en un poligono con
    ECO_NAME nulo) quedan como SIN_REGION -> se CONSERVAN (no se descartan).
    Si el punto cae en frontera entre poligonos, se toma el primero.
    """
    pts = gpd.GeoDataFrame(
        geometry=gdf.geometry.representative_point(), crs=gdf.crs)
    j = pts.sjoin(
        eco_gdf[[field, "geometry"]],
        how="left", predicate="within",
    ).drop(columns=["index_right"], errors="ignore")
    j = j[~j.index.duplicated(keep="first")]
    vals = j[field].reindex(gdf.index)
    return vals.fillna(SIN_REGION).values


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
        res = res[~res.index.duplicated(keep="first")]
        gdf_out.loc[res.index, "gaul0_code"] = res["gaul0_code"]
        gdf_out.loc[res.index, "gaul0_name"] = res["gaul0_name"]

    mask_nan2 = gdf_out["gaul0_name"].isna()
    if mask_nan2.any():
        tmp2 = gdf_out[mask_nan2].copy()
        tmp2["geometry"] = tmp2.geometry.centroid
        res2 = tmp2[["geometry"]].sjoin_nearest(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]], how="left",
        ).drop(columns=["index_right"], errors="ignore")
        res2 = res2[~res2.index.duplicated(keep="first")]
        gdf_out.loc[res2.index, "gaul0_code"] = res2["gaul0_code"]
        gdf_out.loc[res2.index, "gaul0_name"] = res2["gaul0_name"]

    return gdf_out


# --- Parseo del anio del nombre del shapefile --------------------------------
def _parse_year_from_name(path):
    """Anio de PARTICION del shapefile GFA. Se ancla al patron 'perimeters_AAAA'
    para NO confundirse con el AAAA de la version (p.ej. 'v20250411')."""
    m = re.search(r'perimeters[_-](\d{4})', path.stem, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Fallback: ultimo grupo de 4 digitos plausible como anio.
    for g in reversed(re.findall(r'(\d{4})', path.stem)):
        y = int(g)
        if 1900 <= y <= 2100:
            return y
    return None


# --- Descubrimiento de fuentes -----------------------------------------------
def discover_perimeter_year_files():
    """Dict {anio: ruta} de los shapefiles de perimetros GFA. No abre archivos."""
    files = sorted(PERIM_DIR.glob(PERIM_GLOB))
    if not files:
        files = sorted(PERIM_DIR.glob("*.shp"))
    out = {}
    for p in files:
        y = _parse_year_from_name(p)
        if y is not None:
            out[y] = p
    if not out and files:
        out = {YEAR_MIN + i: f for i, f in enumerate(files)}
        print("  [WARN] Anio no detectado en los nombres GFA; asignado por orden.")
    return out


def get_mapbiomas_metadata(tiles_map):
    """Metadatos de una grilla 3x3 de tiles pre-extraidos (logica AT V5).
    Devuelve dict {(r,c): {bounds_geom(WGS84), crs, nodata, bands}}."""
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
                    "bounds_geom": box(*b),
                    "crs"        : src.crs,
                    "nodata"     : src.nodata,
                    "bands"      : src.count,
                }
        except Exception as e:
            print(f"  [WARN] tile MapBiomas ilegible {path.name}: {e}")
    return meta


# --- DEM bruto: construccion cache (patron de AT_V8) -------------------------
def _tile_ok(path):
    """(path, True/False). Checksum de GDAL fuerza la lectura de TODOS los
    bloques; un TIFF truncado lanza excepcion y se marca invalido."""
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
    """Garantiza un DEM bruto materializado y devuelve su ruta (str)."""
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
        os.replace(tmp_tif, dem_out)
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


# --- Mediana ignorando NaN ---------------------------------------------------
def _nanmedian_safe(values):
    """Mediana de 'values' ignorando NaN; NaN si no queda ningun valido."""
    v = values[~np.isnan(values)]
    return float(np.median(v)) if v.size else np.nan


# --- DEM: mediana zonal por poligono (NUEVO en GFA_V1) -----------------------
def add_dem_median(gdf, dem_path, field="dem_median"):
    """Anade la MEDIANA de cota del DEM dentro de cada poligono.

    Para cada evento: se lee la ventana del DEM que lo cubre, se rasteriza el
    poligono (all_touched) y se toma la mediana de los pixeles bajo la mascara,
    ignorando el nodata del DEM. Si el DEM no cubre el evento -> NaN.
    Reemplaza el labeled_comprehension sobre el DEM reproyectado de BA_V11.
    """
    out = np.full(len(gdf), np.nan, dtype=np.float64)
    if len(gdf) == 0:
        gdf[field] = out
        return gdf

    with rasterio.open(dem_path) as dem:
        dcrs = dem.crs or WGS84
        nod  = dem.nodata
        geoms = (gdf.geometry.values if CRS.from_user_input(dcrs) == WGS84
                 else gdf.to_crs(dcrs).geometry.values)
        for i, geom in enumerate(geoms):
            if geom is None or geom.is_empty:
                continue
            minx, miny, maxx, maxy = geom.bounds
            win = from_bounds(minx, miny, maxx, maxy, transform=dem.transform)
            win = Window(win.col_off - 1, win.row_off - 1,
                         win.width + 2, win.height + 2)
            win = _round_window(win, dem.width, dem.height)
            if win.width <= 0 or win.height <= 0:
                continue
            try:
                arr = dem.read(1, window=win).astype(np.float32)
            except Exception:
                continue
            tr = dem.window_transform(win)
            mask = rasterize(
                [(geom, 1)], out_shape=(int(win.height), int(win.width)),
                transform=tr, fill=0, all_touched=True, dtype="uint8").astype(bool)
            if not mask.any():
                continue
            vals = arr[mask]
            if nod is not None:
                vals = vals[vals != nod]
            vals = vals[~np.isnan(vals)]
            if vals.size:
                out[i] = round(float(np.median(vals)), 1)
    gdf[field] = out
    return gdf


# --- Recorte geometrico de una clase por evento (BA ∩ claseK) ----------------
def calc_class_clip(polygons_gdf, year, mb_meta, tiles_map, target_class):
    """Para cada poligono devuelve (clip_geoms, area_m2). Identico a BA_V11.

    Semantica de area [ECO-4] (en km2, EPSG:3857):
        NaN -> el evento NO cae en NINGUN tile de la clase (sin cobertura).
        0.0 -> cae en un tile pero sin pixeles de la clase objetivo.
        >0  -> area real de la interseccion evento ∩ claseK, en km2 (EPSG:3857).
    """
    n = len(polygons_gdf)
    clip_geoms = [None] * n
    area12 = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not mb_meta or year < YEAR_MIN or year > YEAR_MAX:
        return clip_geoms, area12

    geoms   = list(polygons_gdf.geometry.values)
    pieces  = defaultdict(list)
    covered = set()

    for (r, c), meta in mb_meta.items():
        tile_geom = meta["bounds_geom"]
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
                    win = Window(win.col_off - 2, win.row_off - 2,
                                 win.width + 4, win.height + 4)
                    win = _round_window(win, src.width, src.height)
                    if win.width <= 0 or win.height <= 0:
                        continue
                    arr = src.read(band_idx, window=win)
                    tr  = src.window_transform(win)
                    covered.add(g)
                    is12 = (arr == target_class)
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
                    inter = cls_geom.intersection(geom)
                    if (inter is not None) and (not inter.is_empty):
                        pieces[int(g)].append(inter)
        except Exception as e:
            print(f"  [WARN] {type(e).__name__} clase{target_class} ({r},{c}) "
                  f"anio {year}: {e}")
        gc.collect()

    for i in covered:
        area12[i] = 0.0

    if pieces:
        idxs   = list(pieces.keys())
        merged = [unary_union(pieces[i]) for i in idxs]
        gs = gpd.GeoSeries(merged, crs=WGS84).to_crs("EPSG:3857")
        ar = gs.area.values                      # m2
        for k, i in enumerate(idxs):
            clip_geoms[i] = merged[k]
            area12[i]     = round(float(ar[k]) / 1e6, 6)   # [ECO-4] -> km2

    return clip_geoms, area12


# --- Carga de perimetros GFA de UN anio (reemplaza la deteccion por raster) --
def load_perimeters_year(shp_path, file_year):
    """Lee un shapefile GFA y devuelve un GeoDataFrame de eventos en WGS84.

    Se CONSERVAN todas las columnas nativas del shapefile. Se AÑADEN:
    event_uid, year, month (derivadas de start_date). La 'start_date' nativa se
    mantiene sin modificar. Orden: estandar -> nativas -> geometry.
    - Lee SOLO los perimetros que intersectan el bbox del ROI (memoria acotada).
    - year/month se derivan de START_DATE_FIELD (formato YYYY-MM-DD).
    - Repara geometrias invalidas; descarta filas sin fecha valida o fuera del
      rango [YEAR_MIN, YEAR_MAX] (para que band_for_year de MapBiomas sea valido).
    """
    try:
        gdf = gpd.read_file(shp_path, bbox=ROI_BBOX)
    except Exception:
        # Fallback: leer completo y filtrar despues (si el driver no soporta bbox).
        gdf = gpd.read_file(shp_path)
        gdf = gdf[gdf.intersects(roi_geom)].copy()
    if gdf is None or len(gdf) == 0:
        return None

    # CRS: el usuario confirma EPSG:4326. Si no viene declarado, se asigna;
    # si viene otro, se reproyecta.
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    elif CRS.from_user_input(gdf.crs) != WGS84:
        gdf = gdf.to_crs(WGS84)

    if START_DATE_FIELD not in gdf.columns:
        raise KeyError(
            f"Columna de fecha '{START_DATE_FIELD}' no encontrada en "
            f"{Path(shp_path).name}. Columnas: {list(gdf.columns)}")

    # Guard de colisiones: si una columna NATIVA se llama igual que una columna
    # que el pipeline GENERA (year, month, event_uid, area_ha, ...), se renombra
    # la nativa con sufijo '_gfa' para conservar el dato sin pisar el calculado.
    clashes = [c for c in gdf.columns if c in RESERVED_GENERATED]
    if clashes:
        ren = {c: f"{c}_gfa" for c in clashes}
        # Evitar que el nuevo nombre tambien exista ya.
        ren = {k: v for k, v in ren.items() if v not in gdf.columns}
        if ren:
            gdf = gdf.rename(columns=ren)
            print(f"  [INFO] {Path(shp_path).name}: columnas nativas renombradas "
                  f"para no colisionar con las calculadas: {ren}")

    # Formato esperado YYYY-MM-DD; el fallback recupera valores con hora u otro
    # formato sin romper (los irrecuperables quedan NaT y se descartan).
    dt = pd.to_datetime(gdf[START_DATE_FIELD], format="%Y-%m-%d",
                        errors="coerce")
    miss = dt.isna()
    if miss.any():
        dt.loc[miss] = pd.to_datetime(
            gdf.loc[miss, START_DATE_FIELD], errors="coerce")
    n_bad = int(dt.isna().sum())
    if n_bad:
        print(f"  [WARN] {Path(shp_path).name}: {n_bad} fila(s) con "
              f"'{START_DATE_FIELD}' no parseable -> descartadas.")
    ok = dt.notna()
    gdf, dt = gdf.loc[ok].copy(), dt.loc[ok]

    # year/month se DERIVAN de la fecha, pero la columna 'start_date' NATIVA se
    # conserva TAL CUAL (no se reescribe), para no alterar el dato original.
    gdf["year"]  = dt.dt.year.astype(int)
    gdf["month"] = dt.dt.month.astype(int)

    # Geometria valida y de tipo poligonal.
    gdf["geometry"] = gdf.geometry.apply(_fix_geometry)
    gdf = gdf[gdf.geometry.notna()]
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    gdf = gdf[~gdf.geometry.is_empty]

    # Rango de anios (validez de banda MapBiomas).
    in_range = gdf["year"].between(YEAR_MIN, YEAR_MAX)
    n_out = int((~in_range).sum())
    if n_out:
        print(f"  [WARN] {Path(shp_path).name}: {n_out} evento(s) fuera de "
              f"[{YEAR_MIN},{YEAR_MAX}] -> descartados.")
    gdf = gdf[in_range].reset_index(drop=True)
    if len(gdf) == 0:
        return None

    # Aviso si el anio de start_date no coincide con el anio del fichero.
    n_mismatch = int((gdf["year"] != file_year).sum())
    if n_mismatch:
        print(f"  [INFO] {Path(shp_path).name}: {n_mismatch} evento(s) con "
              f"year(start_date) != {file_year} (anio del fichero). Se usa "
              f"year(start_date) en las columnas.")

    # event_uid unico y trazable al fichero de origen.
    gdf["event_uid"] = [f"GFA_{file_year}_{i:06d}" for i in range(len(gdf))]

    # Se CONSERVAN todas las columnas nativas del shapefile; solo se reordenan
    # (estandar primero, nativas despues, geometry al final).
    return gdf[_order_columns(gdf)].copy()


# --- Enriquecimiento de los eventos de un anio -------------------------------
def enrich_events(gdf, countries_gdf, eco_gdf,
                  mb_meta_c12, mb_meta_c3, year):
    """sjoin pais + COUNTRY_FILTER + ecorregion + area + DOS clases MapBiomas.

    [ECO-2/ECO-3] Ya NO se filtra por region: se conservan TODOS los eventos de
    Peru (todas las ecorregiones + 'sin_region'), y cada uno recibe SIEMPRE cl12
    y cl3 (km2) calculadas sobre el poligono completo.

    Recibe un GeoDataFrame de eventos (ya con event_uid/year/month/start_date/
    dem_median). Devuelve (gdf, gdf_clip). gdf_clip lleva HASTA 2 filas por
    evento (clase 12 y clase 3).
    """
    if gdf is None or len(gdf) == 0:
        return None, None

    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=WGS84).copy()

    # Submuestreo opcional (tests): Bernoulli reproducible por (anio).
    if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0 and len(gdf) > 0:
        rng  = np.random.default_rng(np.random.SeedSequence([RANDOM_SEED, year]))
        keep = rng.random(len(gdf)) < SAMPLE_FRAC
        gdf  = gdf.loc[keep].reset_index(drop=True)
        if len(gdf) == 0:
            return gdf, None

    gdf = spatial_join_3attempts(gdf, countries_gdf)
    gdf["ADM0_CODE"] = gdf["gaul0_code"]

    if COUNTRY_FILTER:
        gdf = gdf[gdf["gaul0_name"] == COUNTRY_FILTER].reset_index(drop=True)
    if len(gdf) == 0:
        return gdf, None

    # [ECO-2] ECO_NAME unico por representative_point; SIN filtro de valores.
    #   Los eventos fuera de toda ecorregion quedan como 'sin_region' (se conservan).
    gdf["ECO_NAME"] = assign_ecoregion(gdf, eco_gdf)

    m = gdf.to_crs("EPSG:3857")
    gdf["area_ha"] = np.round(m.geometry.area.values / 10_000, 2)
    del m

    n = len(gdf)
    gdf["cl12"] = np.nan
    gdf["cl3"]  = np.nan
    # Geometria de recorte por clase (para la capa clip_clases): 2 slots/evento.
    clip_geom_12 = [None] * n
    clip_geom_3  = [None] * n

    # [ECO-3] AMBAS clases para TODOS los eventos (ya no hay mapeo zona->clase).
    #   (columna destino, clase objetivo, tiles, meta, slot de geometria de clip)
    plan = (
        ("cl12", CLASS_1, MAPBIOMAS_TILES_C12, mb_meta_c12, clip_geom_12),
        ("cl3",  CLASS_2, MAPBIOMAS_TILES_C3,  mb_meta_c3,  clip_geom_3),
    )
    for col, target_class, tiles_map, mb_meta, clip_slot in plan:
        if not mb_meta:
            continue
        geoms_c, area_c = calc_class_clip(gdf, year, mb_meta,
                                          tiles_map, target_class)
        gdf[col] = area_c                     # km2 (NaN=sin cobertura, 0=sin pixeles)
        for i in range(n):
            g = geoms_c[i]
            if g is not None and not g.is_empty:
                clip_slot[i] = g

    gdf["cl12"] = np.round(gdf["cl12"].astype(float), 6)
    gdf["cl3"]  = np.round(gdf["cl3"].astype(float), 6)

    # [ECO-4] clip_clases: HASTA 2 filas por evento (una por clase con geometria).
    rows_uid, rows_year, rows_month = [], [], []
    rows_class, rows_area, rows_geom = [], [], []
    for label, target_class, clip_slot, col in (
            ("clase 12", CLASS_1, clip_geom_12, "cl12"),
            ("clase 3",  CLASS_2, clip_geom_3,  "cl3")):
        for i in range(n):
            g = clip_slot[i]
            if g is None or g.is_empty:
                continue
            rows_uid.append(gdf["event_uid"].values[i])
            rows_year.append(gdf["year"].values[i])
            rows_month.append(gdf["month"].values[i])
            rows_class.append(label)
            rows_area.append(gdf[col].values[i])
            rows_geom.append(g)

    if rows_geom:
        gdf_clip = gpd.GeoDataFrame(
            {
                "event_uid":      rows_uid,
                "year":           rows_year,
                "month":          rows_month,
                "clase":          rows_class,
                "area_clase_km2": rows_area,
            },
            geometry=rows_geom, crs=WGS84)
    else:
        gdf_clip = None

    return gdf, gdf_clip


# --- Procesamiento de un anio (un shapefile GFA) -----------------------------
def process_gfa_year(year, shp_path, countries_gdf, eco_gdf,
                     mb_meta_c12, mb_meta_c3, dem_path,
                     gpkg_path, clip_gpkg_path, fw_main, fw_clip):
    t0 = time.time()

    gdf0 = load_perimeters_year(shp_path, year)
    if gdf0 is None or len(gdf0) == 0:
        print(f"  [INFO] anio {year}: 0 perimetros en el ROI "
              f"({timedelta(seconds=int(time.time() - t0))}).")
        return fw_main, fw_clip

    # Mediana de cota por poligono (mediana zonal desde el DEM).
    gdf0 = add_dem_median(gdf0, dem_path)

    gdf, gdf_clip = enrich_events(gdf0, countries_gdf, eco_gdf,
                                  mb_meta_c12, mb_meta_c3, year)
    if gdf is None or len(gdf) == 0:
        print(f"  [INFO] anio {year}: 0 eventos tras enriquecer "
              f"({timedelta(seconds=int(time.time() - t0))}).")
        return fw_main, fw_clip

    # Se conservan TODAS las columnas (nativas incluidas); solo se reordenan.
    gdf = gdf[_order_columns(gdf)]
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
    del gdf, gdf_clip, gdf0
    gc.collect()
    return fw_main, fw_clip


# --- Worker (subconjunto de anios) -------------------------------------------
def run_worker(worker_id, n_workers):
    t0 = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)
    print(f"\n{'='*58}\n  WORKER {worker_id}/{n_workers} — GFA_V1")
    _modo = "PRODUCCION" if run_tag == "vf" else f"TEST (eventos={SAMPLE_FRAC})"
    print(f"  MODO: {_modo}\n{'='*58}")

    year_files = discover_perimeter_year_files()
    years = sorted(year_files)
    my_years = years[worker_id::n_workers]
    print(f"  Anios totales: {len(years)} | este worker: {my_years}")
    if not my_years:
        print("  [INFO] Sin anios asignados.")
        return

    countries_gdf = load_countries(GAUL_PATH, COUNTRIES_ADM0, roi_geom)
    eco_gdf = load_ecoregions(ECO_PATH, layer=ECO_LAYER, field=ECO_FIELD)
    print(f"  Ecorregiones cargadas: {eco_gdf[ECO_FIELD].nunique()} clases "
          f"(TODAS conservadas; fuera de poligono -> '{SIN_REGION}')")
    mb_meta_c12 = get_mapbiomas_metadata(MAPBIOMAS_TILES_C12)
    mb_meta_c3  = get_mapbiomas_metadata(MAPBIOMAS_TILES_C3)
    print(f"  Tiles clase12: {len(mb_meta_c12)}/{len(MAPBIOMAS_TILES_C12)} | "
          f"clase3: {len(mb_meta_c3)}/{len(MAPBIOMAS_TILES_C3)}")
    dem_path = ensure_dem_bruto(RAW_TILES_DIR, DEM_PATH, DEM_BBOX,
                                nodata=DEM_NODATA, rebuild=False, validate=False)

    gpkg_path = test_dir / f"GFA_V1_{run_tag}_w{worker_id}.gpkg"
    clip_path = test_dir / f"GFA_V1_{run_tag}_w{worker_id}_clip.gpkg"
    test_dir.mkdir(parents=True, exist_ok=True)
    for p in (gpkg_path, clip_path):
        if p.exists():
            p.unlink()

    fw_main, fw_clip = True, True
    for yr in my_years:
        fw_main, fw_clip = process_gfa_year(
            yr, year_files[yr], countries_gdf, eco_gdf,
            mb_meta_c12, mb_meta_c3, dem_path,
            gpkg_path, clip_path, fw_main, fw_clip)

    print(f"  TOTAL worker {worker_id}: "
          f"{timedelta(seconds=int(time.time() - t0))}")


# --- Merge + exportacion final -----------------------------------------------
def merge_and_export(run_tag, n_workers, base_name=None):
    t = time.time()
    print(f"\n{'-'*58}\n  Merge de resultados de workers\n{'-'*58}")

    worker_paths = [test_dir / f"GFA_V1_{run_tag}_w{k}.gpkg"
                    for k in range(n_workers)]
    existing = [p for p in worker_paths if p.exists()]
    if not existing:
        print("  [WARN] No hay GPKGs de workers para merge.")
        return None
    missing = [p.name for p in worker_paths if not p.exists()]
    if missing:
        print(f"  [INFO] Workers sin GPKG (vacios o fallidos): {missing}")

    base     = base_name or f"GFA_V1_{run_tag}"
    os.makedirs(output_dir, exist_ok=True)
    gpkg_out = output_dir / f"{base}.gpkg"
    csv_out  = output_dir / f"{base}.csv"
    for p in (gpkg_out, csv_out):
        if p.exists():
            p.unlink()

    print(f"  Escribiendo {len(existing)} GPKGs en streaming...")
    total, first = 0, True
    for p in existing:
        g = gpd.read_file(p)
        g = g[_order_columns(g)]   # conserva columnas nativas; solo reordena
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

    clip_worker_paths = [
        test_dir / f"GFA_V1_{run_tag}_w{k}_clip.gpkg"
        for k in range(n_workers)]
    clip_existing = [p for p in clip_worker_paths if p.exists()]
    if clip_existing:
        print(f"  Escribiendo {len(clip_existing)} GPKGs de recortes...")
        total_c = 0
        for p in clip_existing:
            gc_ = gpd.read_file(p)
            gc_.to_file(gpkg_out, layer="clip_clases", driver="GPKG", mode="a")
            total_c += len(gc_)
            del gc_
            gc.collect()
        print(f"  [OK] GPKG (layer clip_clases) -> {gpkg_out.name} "
              f"({total_c} pol.)")
    else:
        print("  [INFO] Sin recortes evento-clase (12/3) que anadir.")

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
def save_cartographic_layers(aoi_geom, dem_path, gdf_result, output_dir, base_name):
    """Recorte de capas de ejemplo al AOI. En GFA_V1 NO hay raster de origen que
    recortar: solo se exporta el DEM del AOI y los eventos (poligonos) que caen
    dentro, como GPKG + CSV."""
    if aoi_geom is None:
        print("  [INFO] AOI None: sin capas de ejemplo.")
        return
    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'-'*58}\n  Exportacion cartografica (AOI)\n{'-'*58}")

    # 1) DEM recortado al AOI.
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

    # 2) Eventos dentro del AOI -> GPKG + CSV.
    if gdf_result is None or len(gdf_result) == 0:
        print("  [INFO] Sin eventos en el AOI; no se exportan capas vectoriales.")
        timer("Exportacion cartografica (AOI)", t)
        return
    gdf_aoi = gpd.clip(gdf_result, aoi_geom).reset_index(drop=True)
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

    yf = discover_perimeter_year_files()
    print(f"  GFA perimetros: {len(yf)} shapefiles anuales {sorted(yf)}")
    if not yf:
        print(f"  [ERROR] Ningun shapefile GFA en {PERIM_DIR}.")
        return False

    # Peek: la columna de fecha existe en el primer fichero.
    first_shp = yf[sorted(yf)[0]]
    try:
        head = gpd.read_file(first_shp, rows=1)
        if START_DATE_FIELD not in head.columns:
            print(f"  [ERROR] '{START_DATE_FIELD}' no esta en {first_shp.name}. "
                  f"Columnas: {list(head.columns)}")
            return False
        print(f"  [INFO] Columna de fecha '{START_DATE_FIELD}' OK "
              f"(ejemplo: {head[START_DATE_FIELD].iloc[0]!r}).")
    except Exception as e:
        print(f"  [ERROR] No pude leer {first_shp.name}: {e}")
        return False

    for cls, tiles_map, col in (("clase12", MAPBIOMAS_TILES_C12, "cl12"),
                                ("clase3",  MAPBIOMAS_TILES_C3,  "cl3")):
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

    try:
        ensure_dem_bruto(RAW_TILES_DIR, DEM_PATH, DEM_BBOX,
                         nodata=DEM_NODATA, rebuild=DEM_REBUILD,
                         validate=DEM_VALIDATE, max_workers=DEM_VALIDATE_WORKERS)
    except Exception as e:
        print(f"  [ERROR] No pude asegurar el DEM: {e}")
        return False

    if not Path(GAUL_PATH).exists():
        print(f"  [ERROR] Shapefile de paises no encontrado: {GAUL_PATH}")
        return False

    if not Path(ECO_PATH).exists():
        print(f"  [ERROR] GPKG de ecorregiones no encontrado: {ECO_PATH}")
        return False
    try:
        _eco = load_ecoregions(ECO_PATH, layer=ECO_LAYER, field=ECO_FIELD)
        _vals = sorted(_eco[ECO_FIELD].dropna().unique())
        print(f"  Ecorregiones ({len(_vals)}): {_vals}")
        print(f"  [INFO] Sin seleccion de valores: se clasifican y conservan "
              f"TODAS. Fuera de poligono -> '{SIN_REGION}'.")
    except Exception as e:
        print(f"  [ERROR] No pude leer el GPKG de ecorregiones: {e}")
        return False

    timer("Validacion de fuentes", t)
    return True


# --- Orquestador -------------------------------------------------------------
def run_orchestrator():
    t0      = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)

    if not _preprocess_only():
        sys.exit(1)

    year_files = discover_perimeter_year_files()
    years = sorted(year_files)
    n_workers = max(1, min(N_WORKERS, len(years)))

    print(f"\n{'='*58}")
    print(f"  ORQUESTADOR — GFA_V1 (paralelo por anio)")
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

    missing_years = sorted({y for k in fail for y in years[k::n_workers]})
    partial = len(fail) > 0
    base_name = (f"GFA_V1_{run_tag}" + ("_PARCIAL" if partial else ""))
    if partial:
        print(f"\n  [ERROR] Corrida PARCIAL: fallaron {len(fail)} worker(s) "
              f"-> {sorted(fail)}")
        print(f"  [ERROR] Anios AUSENTES del dataset final: {missing_years}")
        print(f"  [ERROR] La salida se marca con sufijo '_PARCIAL'.")

    if ok:
        gpkg_out = merge_and_export(run_tag, n_workers, base_name)
        if gpkg_out is not None:
            aoi_geom = load_aoi()
            gdf_aoi_src = None
            if aoi_geom is not None:
                try:
                    gdf_aoi_src = gpd.read_file(
                        gpkg_out, layer="eventos",
                        bbox=tuple(aoi_geom.bounds))
                except Exception as e:
                    print(f"  [WARN] no pude leer el subset AOI del GPKG: {e}")
            save_cartographic_layers(
                aoi_geom, DEM_PATH, gdf_aoi_src, output_dir, base_name)
            cols_show = ["event_uid", "year", "month", "start_date",
                         "dem_median", "ECO_NAME", "cl12", "cl3",
                         "area_ha", "gaul0_name"]
            try:
                head = gpd.read_file(gpkg_out, layer="eventos", rows=5)
                print(head[[c for c in cols_show if c in head.columns]])
            except Exception as e:
                print(f"  [INFO] no pude imprimir head: {e}")

    timer(f"TOTAL orquestador ({len(ok)}/{n_workers} workers OK)", t0)


# --- Ejecucion ---------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GFA perimeters pipeline V0")
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
