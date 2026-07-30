# -*- coding: utf-8 -*-
import os
# Cache GDAL moderada. En v9 cada worker abre 1 mosaico BA anual a la vez y,
# para MapBiomas, ventanas pequenas por tile. 512 MB/worker es suficiente.
os.environ.setdefault("GDAL_CACHEMAX", "512")
# Silenciar el ruido de libtiff (warnings cosmeticos de etiquetas TIFF).
os.environ.setdefault("CPL_LOG", os.devnull)
"""
Modified on 18/06/2026
Version 9.2.0   (prefijo de salida se mantiene "V9")
@author: jvilla

Base   : script_BA_V9.py (v9.1.2)
Logica : igual que v9.1.2; cambia el tratamiento de la clase 12 (geometrico)
         y las salidas.

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

# --- MapBiomas: tiles con bandas anuales (clase 12 = valor de pixel 12) ------
MAPBIOMAS_DIR     = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/"
                         "MCD14ML/0_raw/biomas_peru_sol")
MAPBIOMAS_REGIONS = ["Peru_Norte", "Peru_Centro", "Peru_Sur"]
MAPBIOMAS_CLASS   = 12
# Indice cacheado de footprints REALES de tiles (se construye 1 vez).
MAPBIOMAS_INDEX_CACHE = processed_dir / "mapbiomas_tiles_index.gpkg"

DEM_PATH = processed_dir / "mosaico_andes_DEM_COG.tif"

# --- Area de Interes (AOI) — solo recorte de capas de ejemplo ----------------
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales -----------------------------------------------------
ROI_BBOX       = (-80.0, -20.0, -60.0, 1.0)
YEAR_MIN       = 2001
YEAR_MAX       = 2024
ELEV_THRESHOLD = 2000
COUNTRIES_ADM0 = [178, 184, 185, 190, 207]
COUNTRY_FILTER = "Peru"

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
SAMPLE_FRAC = 0.05
RANDOM_SEED = 42

# --- Workers -----------------------------------------------------------------
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# Orden de columnas de salida (v9.2.0: sin 'area_km2' ni 'pct_class12')
COL_ORDER = [
    "event_uid", "year", "month", "BurnDate", "Elevation", "Zone_Clima",
    "area_class12_ha", "ADM0_CODE", "gaul0_code", "gaul0_name",
    "area_ha", "geometry",
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


# --- Zona climatica ----------------------------------------------------------
def assign_zone_clima(gdf):
    rep_lat = gdf.geometry.representative_point().y
    conditions = [
        (rep_lat >= -5) & (rep_lat <= 1),
        (rep_lat >= -8) & (rep_lat < -5),
        (rep_lat < -8) & (rep_lat >= -20),
    ]
    choices = ["Zone_Equatorial", "Transition_Zone", "South_Zone"]
    return np.select(conditions, choices, default="Not_Specified")


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
        gdf_out.loc[mask_nan, "gaul0_code"] = res["gaul0_code"].values
        gdf_out.loc[mask_nan, "gaul0_name"] = res["gaul0_name"].values

    mask_nan2 = gdf_out["gaul0_name"].isna()
    if mask_nan2.any():
        tmp2 = gdf_out[mask_nan2].copy()
        tmp2["geometry"] = tmp2.geometry.centroid
        res2 = tmp2[["geometry"]].sjoin_nearest(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]], how="left",
        ).drop(columns=["index_right"], errors="ignore")
        res2 = res2[~res2.index.duplicated(keep="first")]
        gdf_out.loc[mask_nan2, "gaul0_code"] = res2["gaul0_code"].values
        gdf_out.loc[mask_nan2, "gaul0_name"] = res2["gaul0_name"].values

    return gdf_out


# --- Parseo de nombres -------------------------------------------------------
def _parse_tile_bounds_from_name(path):
    """Pattern MapBiomas: ...Lat{a}to{b}_Lon{c}to{d}.tif (grados enteros)."""
    m = re.search(r'Lat([+-]?\d+)to([+-]?\d+)_Lon([+-]?\d+)to([+-]?\d+)',
                  path.stem)
    if not m:
        return None
    lat1, lat2 = int(m[1]), int(m[2])
    lon1, lon2 = int(m[3]), int(m[4])
    return (min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))


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


def _read_tile_footprint(path):
    """Footprint REAL de un tile (bounds del raster -> WGS84). Robusto frente a
    cualquier convencion de nombre. Devuelve dict {path, geometry} o None."""
    try:
        with rasterio.open(path) as src:
            b   = src.bounds
            crs = src.crs
        if crs is not None and CRS.from_user_input(crs) != WGS84:
            b = rasterio.warp.transform_bounds(crs, WGS84, *b, densify_pts=21)
        if not (b[2] > b[0] and b[3] > b[1]):
            return None
        return {"path": str(path), "geometry": box(*b)}
    except Exception as e:
        print(f"  [WARN] bounds ilegibles {Path(path).name}: {e}")
        return None


def discover_mapbiomas_tiles(use_cache=True, rebuild=False):
    """GeoDataFrame {path, geometry} de tiles MapBiomas que intersectan el ROI.

    v9.1: los footprints salen de los BOUNDS REALES del raster, no del nombre.
    Para no reabrir ~137k cabeceras en cada worker, el indice COMPLETO se cachea
    en MAPBIOMAS_INDEX_CACHE y se reutiliza; el filtrado por ROI se hace en
    memoria sobre ese cache."""
    # 1) Cache existente -> filtrar por ROI y devolver.
    if use_cache and not rebuild and MAPBIOMAS_INDEX_CACHE.exists():
        try:
            idx = gpd.read_file(MAPBIOMAS_INDEX_CACHE)
            if len(idx):
                idx = idx[idx.intersects(roi_geom)].reset_index(drop=True)
            return idx
        except Exception as e:
            print(f"  [WARN] cache de tiles ilegible ({e}); reconstruyo.")

    # 2) Reconstruir: escanear .tif de las regiones y leer bounds en paralelo.
    paths = []
    for region in MAPBIOMAS_REGIONS:
        rd = MAPBIOMAS_DIR / region
        if not rd.exists():
            print(f"  [WARN] region MapBiomas ausente: {region}")
            continue
        paths.extend(sorted(rd.glob("*.tif")))
    if not paths:
        return gpd.GeoDataFrame({"path": []}, geometry=[], crs="EPSG:4326")

    print(f"  Indexando footprints reales de {len(paths)} tiles MapBiomas...")
    rows, n_read = [], 0
    with ThreadPoolExecutor(max_workers=min(16, len(paths))) as exe:
        for r in exe.map(_read_tile_footprint, paths):
            n_read += 1
            if r is not None:
                rows.append(r)
            if n_read % 20000 == 0:
                print(f"    ... {n_read}/{len(paths)} leidos")
    full = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    # 3) Guardar cache COMPLETO (sin filtrar) para futuras corridas/workers.
    try:
        processed_dir.mkdir(parents=True, exist_ok=True)
        if MAPBIOMAS_INDEX_CACHE.exists():
            MAPBIOMAS_INDEX_CACHE.unlink()
        full.to_file(MAPBIOMAS_INDEX_CACHE, driver="GPKG")
        print(f"  [OK] indice de tiles cacheado -> {MAPBIOMAS_INDEX_CACHE.name} "
              f"({len(full)} tiles validos)")
    except Exception as e:
        print(f"  [WARN] no pude cachear el indice de tiles: {e}")

    return full[full.intersects(roi_geom)].reset_index(drop=True)


# --- Recorte geometrico clase 12 por evento (BA ∩ clase 12) ------------------
def calc_class12_clip(polygons_gdf, year, mb_tiles):
    """Para cada poligono BA (de un mismo 'year') devuelve:
        - clip_geoms: la geometria de clase 12 (pixel == MAPBIOMAS_CLASS)
          contenida en el evento, es decir BA ∩ clase12, en WGS84 (o None).
        - area12_ha: superficie de esa geometria recortada, en ha (EPSG:3857).
                     0.0 si el evento esta cubierto por tiles pero no tiene
                     clase 12; NaN si ningun tile MapBiomas lo cubre.

    v9.2.0: reemplaza el conteo pct (calc_mapbiomas_proportions). Se vectoriza
    pixel == 12 SOLO en la ventana de cada evento, lo que excluye nodata por
    construccion (nodata != 12), y se interseca con el poligono BA. Un evento
    que cae en >1 tile acumula (union) las piezas de cada tile.
    """
    n = len(polygons_gdf)
    clip_geoms = [None] * n
    area12 = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or len(mb_tiles) == 0 or year < YEAR_MIN or year > YEAR_MAX:
        return clip_geoms, area12

    ev = gpd.GeoDataFrame(
        {"eidx": np.arange(n)},
        geometry=polygons_gdf.geometry.values, crs=polygons_gdf.crs)
    pairs = gpd.sjoin(ev, mb_tiles[["path", "geometry"]],
                      predicate="intersects")
    if pairs.empty:
        return clip_geoms, area12

    # Eventos cubiertos por al menos un tile -> default 0.0 (no NaN).
    covered = np.unique(pairs["eidx"].values)
    area12[covered] = 0.0

    pieces = defaultdict(list)   # eidx -> [geom clase12 en WGS84]

    for tile_path, grp in pairs.groupby("path"):
        eidx = grp["eidx"].values
        try:
            with rasterio.open(tile_path) as src:
                nb   = src.count
                bidx = band_for_year(year, nb)
                if bidx < 1 or bidx > nb:
                    continue
                tcrs = src.crs or WGS84
                for g in eidx:
                    geom = polygons_gdf.geometry.iloc[g]
                    if geom is None or geom.is_empty:
                        continue
                    geom_r = (_to_local_crs(geom, tcrs)
                              if tcrs != WGS84 else geom)
                    minx, miny, maxx, maxy = geom_r.bounds
                    win = from_bounds(minx, miny, maxx, maxy,
                                      transform=src.transform)
                    # Pad de 2 px (CRS-agnostico) para no recortar bordes.
                    win = Window(win.col_off - 2, win.row_off - 2,
                                 win.width + 4, win.height + 4)
                    win = _round_window(win, src.width, src.height)
                    if win.width <= 0 or win.height <= 0:
                        continue
                    arr = src.read(bidx, window=win)
                    tr  = src.window_transform(win)
                    is12 = (arr == MAPBIOMAS_CLASS).astype(np.uint8)
                    if not is12.any():
                        del arr, is12
                        continue
                    polys12 = [shape(gj) for gj, v in rasterio.features.shapes(
                        is12, mask=is12.astype(bool), transform=tr) if v == 1]
                    del arr, is12
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
            print(f"  [WARN] {type(e).__name__} MB {Path(tile_path).name} "
                  f"anio {year}: {e}")
        gc.collect()

    # Consolidar piezas por evento y medir area en EPSG:3857 (ha).
    if pieces:
        idxs   = list(pieces.keys())
        merged = [unary_union(pieces[i]) for i in idxs]
        gs = gpd.GeoSeries(merged, crs=WGS84).to_crs("EPSG:3857")
        ar = gs.area.values / 10_000.0
        for k, i in enumerate(idxs):
            clip_geoms[i] = merged[k]
            area12[i]     = round(float(ar[k]), 2)

    return clip_geoms, area12


# --- Extraccion de eventos de UNA banda (un mes) -----------------------------
def _extract_month_events(ba_data, elev_ok, dem_tile, transform,
                          year, month, structure):
    """Componentes conexas quemadas (>0) y sobre el umbral de elevacion."""
    valid = (ba_data > 0) & elev_ok
    if not valid.any():
        return []

    labeled = np.zeros(valid.shape, dtype=np.int32)
    ndimage.label(valid, structure=structure, output=labeled)
    n_evt = int(labeled.max())
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

    # BurnDate = mediana del DOY; Elevation = media del DEM (por componente)
    BurnDate  = np.round(
        ndimage.median(ba_data.astype(np.float32), labeled, ids)
    ).astype(np.int32)
    Elevation = np.asarray(
        ndimage.mean(dem_tile, labeled, ids), dtype=np.float32)

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
            "geometry" : geom,
            "year"     : year,
            "month"    : month,
            "BurnDate" : int(BurnDate[i]),
            "Elevation": round(float(Elevation[i]), 1),
            "event_uid": f"BA_{year}_M{month:02d}_{eid}",
        })
    return recs


# --- Enriquecimiento de los eventos de un anio -------------------------------
def enrich_events(records, countries_gdf, mb_tiles, year):
    """sjoin pais + Zone_Clima + COUNTRY_FILTER + area + recorte clase12.

    Devuelve (gdf, gdf_clip):
      - gdf      : eventos BA enriquecidos (incluye area_class12_ha).
      - gdf_clip : poligonos BA ∩ clase12 (event_uid, year, month, areac12_ha)
                   o None si ningun evento toca clase 12.
    """
    if not records:
        return None, None

    gdf = gpd.GeoDataFrame(records, crs=WGS84)
    gdf["Zone_Clima"] = assign_zone_clima(gdf)
    gdf = spatial_join_3attempts(gdf, countries_gdf)
    gdf["ADM0_CODE"] = gdf["gaul0_code"]

    if COUNTRY_FILTER:
        gdf = gdf[gdf["gaul0_name"] == COUNTRY_FILTER].reset_index(drop=True)
    if len(gdf) == 0:
        return gdf, None

    # Area en EPSG:3857 (decision del usuario). v9.2.0: ya NO se calcula area_km2.
    m = gdf.to_crs("EPSG:3857")
    a = m.geometry.area.values
    del m
    gdf["area_ha"] = np.round(a / 10_000, 2)

    # MapBiomas clase 12: recorte geometrico real (BA ∩ clase12).
    clip_geoms, area12 = calc_class12_clip(gdf, year, mb_tiles)
    gdf["area_class12_ha"] = np.round(area12, 2)

    # Capa de recortes (solo eventos con geometria clase12 no vacia).
    keep = [i for i in range(len(gdf))
            if clip_geoms[i] is not None and not clip_geoms[i].is_empty]
    if keep:
        gdf_clip = gpd.GeoDataFrame(
            {
                "event_uid":  gdf["event_uid"].values[keep],
                "year":       gdf["year"].values[keep],
                "month":      gdf["month"].values[keep],
                "areac12_ha": np.round(area12[keep], 2),
            },
            geometry=[clip_geoms[i] for i in keep], crs=WGS84)
    else:
        gdf_clip = None

    return gdf, gdf_clip


# --- Procesamiento de un anio (un mosaico BA, 12 meses) ----------------------
def process_ba_year(year, ba_path, countries_gdf, mb_tiles, dem_path,
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

        dem_tile = np.full((H, W), np.nan, dtype=np.float32)
        try:
            rasterio.warp.reproject(
                source=rasterio.band(dem_src, 1), destination=dem_tile,
                src_transform=dem_src.transform, src_crs=dem_src.crs,
                dst_transform=transform, dst_crs=(src_crs or WGS84),
                resampling=Resampling.bilinear,
                src_nodata=dem_src.nodata, dst_nodata=np.nan,
            )
        except Exception as e:
            print(f"  [WARN] anio {year}: reproyeccion DEM fallida: {e}")

        elev_ok = dem_tile >= ELEV_THRESHOLD       # NaN >= thr -> False
        if not elev_ok.any():
            print(f"  [INFO] anio {year}: nada sobre {ELEV_THRESHOLD} m en ROI.")
            return fw_main, fw_clip

        n_months = min(12, n_bands)
        for month in range(1, n_months + 1):
            try:
                ba = src.read(month, window=win)
            except Exception:
                continue
            recs = _extract_month_events(
                ba, elev_ok, dem_tile, transform, year, month, structure)
            if recs:
                recs_year.extend(recs)
            del ba
        del dem_tile

    # Defensa: si el mosaico no estuviera en WGS84, reproyectar geometrias
    if recs_year and src_crs is not None and src_crs != WGS84:
        g = gpd.GeoDataFrame(recs_year, crs=src_crs).to_crs(WGS84)
        recs_year = g.to_dict("records")

    gdf, gdf_clip = enrich_events(recs_year, countries_gdf, mb_tiles, year)
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

    print(f"  [OK] anio {year}: {len(gdf)} eventos (+{n_clip} recortes clase12) "
          f"-> {gpkg_path.name} ({timedelta(seconds=int(time.time() - t0))})")
    del gdf, gdf_clip
    gc.collect()
    return fw_main, fw_clip


# --- Worker (subconjunto de anios) -------------------------------------------
def run_worker(worker_id, n_workers):
    t0 = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)
    print(f"\n{'='*58}\n  WORKER {worker_id}/{n_workers} — BurnedAreas v9")
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
    mb_tiles = discover_mapbiomas_tiles()
    print(f"  Tiles MapBiomas en ROI: {len(mb_tiles)}")

    # Intermedios por worker (scratch): merge_and_export los borra al terminar.
    gpkg_path = test_dir / f"BurnedAreas_MODIS_V9_{run_tag}_w{worker_id}.gpkg"
    clip_path = test_dir / f"BurnedAreas_MODIS_V9_{run_tag}_w{worker_id}_clip12.gpkg"
    test_dir.mkdir(parents=True, exist_ok=True)
    for p in (gpkg_path, clip_path):
        if p.exists():
            p.unlink()

    fw_main, fw_clip = True, True
    for yr in my_years:
        fw_main, fw_clip = process_ba_year(
            yr, year_files[yr], countries_gdf, mb_tiles, DEM_PATH,
            gpkg_path, clip_path, fw_main, fw_clip)

    print(f"  TOTAL worker {worker_id}: "
          f"{timedelta(seconds=int(time.time() - t0))}")


# --- Merge + exportacion final -----------------------------------------------
def merge_and_export(run_tag, n_workers):
    t = time.time()
    print(f"\n{'-'*58}\n  Merge de resultados de workers\n{'-'*58}")

    worker_paths = [test_dir / f"BurnedAreas_MODIS_V9_{run_tag}_w{k}.gpkg"
                    for k in range(n_workers)]
    existing = [p for p in worker_paths if p.exists()]
    if not existing:
        print("  [WARN] No hay GPKGs de workers para merge.")
        return None
    missing = [p.name for p in worker_paths if not p.exists()]
    if missing:
        print(f"  [INFO] Workers sin GPKG (vacios o fallidos): {missing}")

    print(f"  Cargando {len(existing)} GPKGs...")
    gdfs = [gpd.read_file(p) for p in existing]
    gdf  = gpd.GeoDataFrame(
        pd.concat(gdfs, ignore_index=True), geometry="geometry",
        crs="EPSG:4326")
    del gdfs
    gc.collect()
    print(f"  Total eventos: {len(gdf)}")

    cols = [c for c in COL_ORDER if c in gdf.columns]
    gdf  = gdf[cols]

    base     = f"BurnedAreas_MODIS_V9_{run_tag}"
    os.makedirs(output_dir, exist_ok=True)
    gpkg_out = output_dir / f"{base}.gpkg"     # entregable final -> 3_output
    csv_out  = output_dir / f"{base}.csv"
    shp_out  = output_dir / f"{base}.shp"      # SHP de TODOS los eventos BA

    gdf.to_file(gpkg_out, driver="GPKG")
    gdf.drop(columns=["geometry"]).to_csv(
        csv_out, index=False, encoding="utf-8-sig")
    print(f"  [OK] GPKG -> {gpkg_out.name}")
    print(f"  [OK] CSV  -> {csv_out.name}")

    # --- Resumen de areas agregadas por (year, month) y por (year) -----------
    # v9.2.0: sin area_km2 ni pct_class12. Solo n_eventos, area_ha y, si existe,
    # area_class12_ha (suma del area recortada a clase 12).
    if {"year", "month", "area_ha"}.issubset(gdf.columns):
        has_c12 = "area_class12_ha" in gdf.columns
        agg = {"event_uid": "count", "area_ha": "sum"}
        if has_c12:
            agg["area_class12_ha"] = "sum"

        ym = (gdf.groupby(["year", "month"]).agg(agg)
              .rename(columns={"event_uid": "n_eventos"}).reset_index())
        yr = (gdf.groupby(["year"]).agg(agg)
              .rename(columns={"event_uid": "n_eventos"}).reset_index())
        for tbl in (ym, yr):
            tbl["area_ha"] = tbl["area_ha"].round(2)
            if has_c12:
                tbl["area_class12_ha"] = tbl["area_class12_ha"].round(2)

        ym_out = output_dir / f"{base}_summary_year_month.csv"
        yr_out = output_dir / f"{base}_summary_year.csv"
        ym.to_csv(ym_out, index=False, encoding="utf-8-sig")
        yr.to_csv(yr_out, index=False, encoding="utf-8-sig")
        print(f"  [OK] Resumen year-month -> {ym_out.name} ({len(ym)} filas)")
        print(f"  [OK] Resumen year       -> {yr_out.name} ({len(yr)} filas)")

    # SHP full-extent. El formato trunca nombres de campo a 10 chars y tiene
    # limite de 2 GB; capturamos el fallo en vez de abortar.
    try:
        gdf.to_file(shp_out)
        print(f"  [OK] SHP  -> {shp_out.name} "
              f"(nombres de campo truncados a 10 chars)")
    except Exception as e:
        print(f"  [WARN] SHP full-extent no escrito (posible limite 2 GB): {e}")
        print(f"         Usa el GPKG {gpkg_out.name} (sin limite de tamano).")

    # --- Merge de los recortes BA ∩ clase 12 (ROI-wide) ----------------------
    clip_worker_paths = [
        test_dir / f"BurnedAreas_MODIS_V9_{run_tag}_w{k}_clip12.gpkg"
        for k in range(n_workers)]
    clip_existing = [p for p in clip_worker_paths if p.exists()]
    if clip_existing:
        print(f"  Cargando {len(clip_existing)} GPKGs de recortes clase12...")
        gdfs_c = [gpd.read_file(p) for p in clip_existing]
        gdf_c  = gpd.GeoDataFrame(
            pd.concat(gdfs_c, ignore_index=True), geometry="geometry",
            crs="EPSG:4326")
        del gdfs_c
        gc.collect()
        clip_gpkg = output_dir / f"{base}_clip_class12.gpkg"
        clip_csv  = output_dir / f"{base}_clip_class12.csv"
        clip_shp  = output_dir / f"{base}_clip_class12.shp"
        gdf_c.to_file(clip_gpkg, driver="GPKG")
        gdf_c.drop(columns=["geometry"]).to_csv(
            clip_csv, index=False, encoding="utf-8-sig")
        print(f"  [OK] BA recortado clase12 -> {clip_gpkg.name} "
              f"({len(gdf_c)} pol.)")
        try:
            gdf_c.to_file(clip_shp)
            print(f"  [OK] BA recortado clase12 -> {clip_shp.name}")
        except Exception as e:
            print(f"  [WARN] SHP recorte clase12 no escrito: {e}; usa el GPKG.")
        del gdf_c
        gc.collect()
    else:
        print("  [INFO] Sin recortes BA-clase12 que fusionar.")

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

    timer(f"Merge ({len(gdf)} features)", t)
    return gdf


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, mb_tiles, ba_year_files,
                             gdf_result, output_dir, base_name):
    if aoi_geom is None:
        print("  [INFO] AOI None: sin capas de ejemplo.")
        return
    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'-'*58}\n  Exportacion cartografica (AOI)\n{'-'*58}")

    # 1) DEM recortado al AOI
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

    # 2) MODIS BA recortado al AOI (un raster por anio, 12 bandas)
    n_ba = 0
    for yr, p in sorted(ba_year_files.items()):
        ba_out = output_dir / f"{base_name}_aoi_ba_{yr}.tif"
        try:
            with rasterio.open(p) as src:
                aoi_local = _to_local_crs(aoi_geom, src.crs)
                out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                           all_touched=True)
                out_meta = {**src.meta, "driver": "GTiff", "compress": "lzw",
                            "height": out_img.shape[1],
                            "width": out_img.shape[2], "transform": out_tr}
            with rasterio.open(ba_out, "w", **out_meta) as dst:
                dst.write(out_img)
            n_ba += 1
        except Exception as e:
            print(f"  [WARN] BA {yr} clip: {e}")
    print(f"  [OK] MODIS BA         -> {n_ba} rasters anuales recortados")

    # 3) MapBiomas recortado al AOI -> UN SOLO raster BINARIO de clase 12.
    #    v9.2.0: ya NO se exporta el mosaico con todas las bandas (1985-2024).
    #    Se conservan SOLO las 24 bandas de 2001-2024 (band_for_year) y cada
    #    una se convierte en mascara binaria de la clase 12 (pixel==12 -> 1).
    inter = mb_tiles[mb_tiles.intersects(aoi_geom)] if len(mb_tiles) else mb_tiles
    mb_paths = [Path(r["path"]) for _, r in inter.iterrows()]
    if mb_paths:
        opened = []
        try:
            for p in mb_paths:
                try:
                    opened.append(rasterio.open(p))
                except Exception as e:
                    print(f"  [WARN] no abre {p.name}: {e}")
            if opened:
                crs0 = opened[0].crs
                if any(s.crs != crs0 for s in opened):
                    print("  [WARN] tiles MapBiomas con CRS mixto; uso el del "
                          "primero. Puede haber huecos en el mosaico.")
                aoi_local = _to_local_crs(aoi_geom, crs0)
                # bounds del AOI en el CRS de los tiles -> recorte + fusion
                mosaic, out_tr = rio_merge(opened, bounds=aoi_local.bounds)
                meta = opened[0].meta.copy()

                nb = mosaic.shape[0]
                # Solo las bandas 2001-2024 (ancladas con band_for_year).
                year_bands = [(y, band_for_year(y, nb)) for y in YEARS_RUN]
                year_bands = [(y, b) for (y, b) in year_bands if 1 <= b <= nb]
                if not year_bands:
                    print("  [WARN] El mosaico MapBiomas AOI no tiene bandas "
                          "2001-2024; no se exporta clase12.")
                else:
                    # Cada banda -> mascara BINARIA de la clase 12.
                    sel = np.stack(
                        [(mosaic[b - 1] == MAPBIOMAS_CLASS).astype(np.uint8)
                         for (_y, b) in year_bands], axis=0)
                    meta.update(driver="GTiff", compress="lzw",
                                dtype="uint8", nodata=None,
                                count=sel.shape[0], height=sel.shape[1],
                                width=sel.shape[2], transform=out_tr)
                    mb_out = (output_dir /
                              f"{base_name}_aoi_mapbiomas_class12.tif")
                    with rasterio.open(mb_out, "w", **meta) as dst:
                        dst.write(sel)
                        for i, (y, _b) in enumerate(year_bands, start=1):
                            dst.set_band_description(i, f"class12_{y}")
                    print(f"  [OK] MapBiomas clase12-> {mb_out.name} "
                          f"({sel.shape[0]} bandas 2001-2024, "
                          f"{len(opened)} tiles; 1=clase12, 0=resto)")
                    del sel
                del mosaic
        except Exception as e:
            print(f"  [WARN] fusion MapBiomas: {e}")
        finally:
            for s in opened:
                try:
                    s.close()
                except Exception:
                    pass
    else:
        print("  [INFO] Ningun tile MapBiomas intersecta el AOI.")

    # 4) Resultados dentro del AOI (CLIP geometrico real) -> SHP + CSV
    gdf_aoi = gpd.clip(gdf_result, aoi_geom).reset_index(drop=True)
    if len(gdf_aoi) > 0:
        shp_aoi = output_dir / f"{base_name}_aoi_results.shp"
        csv_aoi = output_dir / f"{base_name}_aoi_results.csv"
        gdf_aoi.to_file(shp_aoi)
        gdf_aoi.drop(columns=["geometry"]).to_csv(
            csv_aoi, index=False, encoding="utf-8-sig")
        print(f"  [OK] Resultados AOI   -> {shp_aoi.name} ({len(gdf_aoi)} pol.)")
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

    mbt = discover_mapbiomas_tiles()
    print(f"  MapBiomas: {len(mbt)} tiles intersectan el ROI")
    if len(mbt) == 0:
        print("  [WARN] Sin tiles MapBiomas en ROI; area_class12_ha sera NaN.")

    if not DEM_PATH.exists():
        print(f"  [ERROR] DEM no encontrado: {DEM_PATH}")
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
    print(f"  ORQUESTADOR — BurnedAreas_MODIS v9 (paralelo por anio)")
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

    if ok:
        gdf = merge_and_export(run_tag, n_workers)
        if gdf is not None and len(gdf):
            aoi_geom = load_aoi()
            mb_tiles = discover_mapbiomas_tiles()
            save_cartographic_layers(
                aoi_geom, DEM_PATH, mb_tiles, year_files, gdf, output_dir,
                f"BurnedAreas_MODIS_V9_{run_tag}")
            cols_show = [c for c in ["event_uid", "year", "month", "BurnDate",
                                     "Elevation", "Zone_Clima",
                                     "area_class12_ha", "area_ha", "gaul0_name"]
                         if c in gdf.columns]
            print(gdf[cols_show].head())

    timer(f"TOTAL orquestador ({len(ok)}/{n_workers} workers OK)", t0)


# --- Ejecucion ---------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BurnedAreas_MODIS pipeline v9")
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