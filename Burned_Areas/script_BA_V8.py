# -*- coding: utf-8 -*-
import os
# Cache GDAL moderada: en v8 cada worker abre 1 tile a la vez (~25 MB/tile),
# no hay VRT gigante, así que 512 MB por worker es suficiente.
os.environ.setdefault("GDAL_CACHEMAX", "512")
# Silenciar el ruido de libtiff ("TIFFReadDirectory: Sum of Photometric...").
# Es un warning cosmético de etiquetas TIFF; con 137K tiles inundaría el .err.
os.environ.setdefault("CPL_LOG", os.devnull)
"""
Modified on 13/06/2026
Version 8.0.1
@author: jvilla

Base   : script_BA_V8.py
Logica : reescritura — paralelizacion por ESPACIO (worker = grupo de tiles).

============================== Changes v8.0.1 ==============================
Motivacion (tests 5/10/25% daban cifras y tiempos identicos):
    - El muestreo de eventos no tenia efecto: los tiles son diminutos
      (~137K tiles → pocos km), asi que cada tile-año tiene 1-3 eventos.
      Con n_keep = max(1, int(n_evt*FRAC)) el int() redondeaba a 0 y el
      max(1,...) lo subia a 1 → SIEMPRE se guardaba 1 evento por tile-año,
      diese igual 5/10/25%. Resultado: 281.9K eventos en los tres tests.
    - Aunque el muestreo funcionase, el tiempo no bajaria: el coste manda
      en el I/O (abrir/leer los 137.180 tiles + reproyectar el DEM por
      tile), que NO depende de SAMPLE_FRAC.

Cambios:
    [1][FIX] _extract_tile_events: muestreo de eventos por BERNOULLI
             (cada evento se conserva con prob = SAMPLE_FRAC, sin el suelo
             artificial "minimo 1"). Las cifras ya escalan con el %.
    [2][TEST] run_worker: nuevo TILE_SAMPLE_FRAC. Submuestrea los TILES
             asignados al worker → reduce el I/O real → el tiempo si baja
             al bajar el %. Es el unico parametro que afecta al tiempo.
             Para PRODUCCION dejar TILE_SAMPLE_FRAC = None o 1.0.
    [3][MEM] FLUSH_EVERY: 100_000 → 30_000. Antes nunca se disparaba
             (buffer maximo ~75K/worker) y todo se volcaba al final, con
             pico de RAM ~16 GB. Ahora el volcado por lotes funciona.
    [4][RNG] Semilla del muestreo por SeedSequence([SEED, year, tidx]) en
             vez de suma aritmetica (evita colisiones si tidx es grande).
    [5][NAME] El sufijo de los archivos lo marca ahora TILE_SAMPLE_FRAC
             (no SAMPLE_FRAC): 'test_t05pct' / 'test_t10pct' / ... → cada
             test de tiles tiene nombre propio y no se sobrescriben.
             Produccion (TILE_SAMPLE_FRAC None/1.0) sigue siendo 'vf'.

================================ Changes v8 ================================
Motivacion (fallos de v7.2):
    - El VRT de 137.180 tiles x 40 bandas (~5,5 M de fuentes) generaba un XML
      de ~1 GB que GDAL no podia reabrir ("Input file too large to be opened").
    - El fallback raw paralelizaba por AÑO: cada worker reabria los 137K tiles
      por año → 137K x 24 = 3,3 M de aperturas → TIMEOUT + OUT_OF_MEMORY.

Cambio de fondo:
    [ARCH]   Paralelizacion por ESPACIO, no por año. Los tiles guardan los 40
             años en sus 40 bandas, asi que la unidad natural del dato es el
             TILE. Cada worker recibe un grupo espacial de tiles y procesa los
             40 años de cada tile ANTES de cerrarlo. Aperturas totales: 137.180
             (una por tile) en lugar de 3,3 M.
    [DROP]   Eliminados: VRT (ba_raw_tiles_mosaic.vrt), modo mosaic, modo raw
             año-paralelo, _ensure_ba_tiles_vrt, _process_vrt_year,
             _process_raw_tiles_for_year, rasterize_countries, _ndimage_mode,
             extract_month_events, load_elevation_mask (grid global).
    [DEM]    El DEM se reproyecta UNA vez por tile (no por año). Si el tile
             entero esta por debajo de ELEV_THRESHOLD se descarta sin leer
             ninguna banda (la mayoria de tiles amazonicos/costeros se saltan).
    [ID]     Doble identificador (peticion del usuario):
             - event_uid = "{region}_T{idx}_{year}_{label}": inmutable, asignado
               al detectar cada componente conexa. Lleva el flag touches_border.
             - ba_id: objeto fisico reconstruido en merge_and_export. Por año,
               los eventos con touches_border=True se agrupan por contiguidad
               (self-sjoin + union-find, escalable) → un ba_id por objeto. Une
               las mitades de un fuego partido entre tiles/workers. Nunca fusiona
               años distintos. Los eventos interiores conservan ba_id=event_uid.
    [MB]     calc_mapbiomas_proportions: PROC_TILE_DEG auto-ajustado a la
             resolucion REAL de los tiles MapBiomas (detectada en runtime), con
             tope de ~2000x2000 px por lectura → evita OOM si MB esta a 10 m.
    [AOI]    Vectorizacion de MapBiomas clase 12 SOLO dentro del AOI (zona de
             muestreo), en save_cartographic_layers. El pct_class12 del analisis
             sigue calculandose por raster (zonal_stats) en TODA la extension.
    [OUT]    Produccion: GPKG (+ CSV). El SHP se ELIMINA del flujo full-extent
             (a 10 m se superaria el limite de 2 GB del formato Shapefile con
             millones de poligonos). El SHP solo se usa para los recortes AOI.
    [AREA]   Areas en EPSG:3857 (Pseudo-Mercator), por decision del usuario:
             menos exacto pero mas rapido. Distorsion ~7% a 15°S.

    [BAND]   Indice banda<->año por ANCLAJE de la ultima banda a 2024
             (band_for_year): tiles BA de 40 bandas → banda(2001)=17; tiles
             MapBiomas de 24 bandas → banda(2001)=1. Corrige el bug v8.0.0 que
             usaba la convencion BA (1985) tambien para MapBiomas.

Conservado de v7:
    COUNTRY_FILTER, Zone_Clima, spatial_join_3attempts, filtro elevacion
    >= 2000 m, SAMPLE_FRAC.
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
from rasterstats import zonal_stats
from rasterio.mask import mask as rio_mask
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

# Bajar el nivel del logger de rasterio para no propagar warnings de GDAL.
logging.getLogger("rasterio").setLevel(logging.ERROR)

# --- Rutas -------------------------------------------------------------------
base_dir      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD64A1")
data_dir      = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir    = base_dir / "3_output"
test_dir      = base_dir / "4_test"

mapbiomas_dir = processed_dir / "mapbiomas_ba_grid"
DEM_PATH      = processed_dir / "mosaico_andes_DEM_COG.tif"

# --- Tiles raw BA ------------------------------------------------------------
BA_RAW_DIR = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/"
                  "MCD14ML/0_raw/biomas_peru_sol")
BA_REGIONS = ["Peru_Norte", "Peru_Centro", "Peru_Sur"]

# --- Area de Interes (AOI) — solo cartografia / vectorizacion de muestra -----
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales -----------------------------------------------------
ROI_BBOX       = (-80.0, -20.0, -60.0, 1.0)
YEARS_TEST     = list(range(2001, 2025))   # 2001-2024 (24 años)
ELEV_THRESHOLD = 2000
COUNTRIES_ADM0 = [178, 184, 185, 190, 207]
COUNTRY_FILTER = "Peru"

# Mapeo banda<->año por ANCLAJE de la ULTIMA banda al año mas reciente (YEAR_MAX).
# Robusto frente a rasters con distinto nº de bandas, SIEMPRE que su ultima
# banda sea YEAR_MAX:
#   - Tiles BA        : 40 bandas (1985-2024) -> banda(2001)=17, banda(2024)=40
#   - Tiles MapBiomas : 24 bandas (2001-2024) -> banda(2001)=1,  banda(2024)=24
YEAR_MIN = 2001
YEAR_MAX = 2024

YEARS_RUN = [y for y in YEARS_TEST if YEAR_MIN <= y <= YEAR_MAX]


def band_for_year(year, n_bands):
    """Indice de banda (1-based) anclando la ULTIMA banda a YEAR_MAX."""
    return year - YEAR_MAX + n_bands

# Tiles MapBiomas (9 tiles 3x3) — lectura raster, sin vectorizar (salvo AOI).
MAPBIOMAS_TILES = {
    (r, c): mapbiomas_dir / f"mapbiomas_ba_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}

# Margen al recortar ventanas MapBiomas (cobertura de bordes de poligono).
MAPBIOMAS_EXTENT_BUFFER_DEG = 0.005
# Tope de pixeles por lectura MapBiomas (para fijar PROC_TILE_DEG en runtime).
MAPBIOMAS_MAX_WINDOW_PX = 2000

# Buffer (grados) para soldar las mitades de un fuego partido en el borde de un
# tile. Diminuto: solo cierra coincidencias de punto-flotante en la costura
# (gap=0). Muy por debajo de 1 pixel (~0.00009°) → no fusiona fuegos distintos.
REINTEGRATION_BUFFER_DEG = 1e-7

WGS84 = CRS.from_epsg(4326)

roi_geom      = box(*ROI_BBOX)
roi_geom_list = [mapping(roi_geom)]

# --- Submuestreo (tests de recursos) -----------------------------------------
# SAMPLE_FRAC: fraccion de EVENTOS conservados por tile-año (muestreo Bernoulli,
#   v8.0.1). Afecta al numero de eventos del resultado, NO al tiempo (el coste
#   manda en el I/O de los tiles). Produccion: None o 1.0.
SAMPLE_FRAC = 0.25
# TILE_SAMPLE_FRAC: fraccion de TILES leidos por cada worker (v8.0.1). Es el
#   parametro que SI reduce el tiempo, porque recorta el I/O real (menos
#   archivos abiertos/leidos). Util para tests de recursos/escalabilidad.
#   Produccion: None o 1.0 (leer todos los tiles).
TILE_SAMPLE_FRAC = 0.1
RANDOM_SEED = 42

# --- Workers -----------------------------------------------------------------
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# Tamaño del buffer de eventos antes de enriquecer+volcar al GPKG del worker.
# v8.0.1: 100_000 → 30_000. Antes nunca se alcanzaba (buffer max ~75K/worker)
# y todo se volcaba en un unico flush al final (pico RAM ~16 GB). Con 30K el
# volcado incremental funciona y baja la memoria pico.
FLUSH_EVERY = 30_000

# Orden de columnas
COL_ORDER = [
    "event_uid", "year", "month", "BurnDate", "Elevation", "Zone_Clima",
    "pct_class12", "area_class12_ha", "ADM0_CODE", "gaul0_code", "gaul0_name",
    "area_ha", "area_km2", "touches_border", "geometry",
]
COL_ORDER_FINAL = (
    ["event_uid", "ba_id"]
    + [c for c in COL_ORDER if c != "event_uid"]
)


# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


def run_tag_of(tile_frac):
    """Sufijo de los archivos de salida.

    v8.0.1: lo marca TILE_SAMPLE_FRAC (el parametro real de los tests de
    recursos), NO SAMPLE_FRAC. Asi cada test de tiles produce un nombre
    distinto y no se sobrescriben.
      - None o >= 1.0           -> 'vf'           (full / produccion)
      - 0.05 / 0.10 / 0.25 ...  -> 'test_t05pct' / 'test_t10pct' / ...
    """
    return ("vf" if (tile_frac is None or tile_frac >= 1.0)
            else f"test_t{int(tile_frac * 100):02d}pct")


# --- AOI ---------------------------------------------------------------------
def load_aoi():
    if AOI_PATH is not None:
        aoi_gdf  = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
        aoi_geom = aoi_gdf.geometry.union_all()
        print(f"  AOI cartografica : {Path(AOI_PATH).name} ({len(aoi_gdf)} feat.)")
        return aoi_geom
    if AOI_BBOX is not None:
        w, s, e, n = AOI_BBOX
        print(f"  AOI cartografica : bbox W={w} S={s} E={e} N={n}")
        return box(w, s, e, n)
    print("  AOI cartografica : None")
    return None


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


# --- Mes desde DOY (vectorizado) ---------------------------------------------
def doy_year_to_month(doy_arr, year_arr):
    """BurnDate interpretado como dia-del-año (DOY). doy<=0 → mes 0."""
    doy_arr  = np.asarray(doy_arr)
    year_arr = np.asarray(year_arr)
    out      = np.zeros(len(doy_arr), dtype=np.int16)
    valid    = (doy_arr > 0) & (doy_arr <= 366)
    if valid.any():
        base = pd.to_datetime(
            pd.Series(year_arr[valid]).astype(str) + "-01-01"
        )
        dt = base.values + pd.to_timedelta(doy_arr[valid] - 1, unit="D")
        out[valid] = pd.DatetimeIndex(dt).month.astype(np.int16)
    return out


# --- Metadatos MapBiomas -----------------------------------------------------
def get_mapbiomas_metadata():
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
                "n_bands"    : src.count,
                "res"        : abs(src.transform.a),   # v8: grados/pixel
            }
    return meta


def mapbiomas_proc_tile_deg(mb_meta):
    """PROC_TILE_DEG ajustado a la resolucion real de MapBiomas.

    window_px = PROC_TILE_DEG / res  ≤  MAPBIOMAS_MAX_WINDOW_PX.
    A 30 m → ~0.54°; a 10 m → ~0.18°. Acotado a [0.1°, 2.0°].
    """
    res_vals = [m["res"] for m in mb_meta.values() if m.get("res")]
    res = min(res_vals) if res_vals else 0.000269
    deg = MAPBIOMAS_MAX_WINDOW_PX * res
    deg = max(0.1, min(2.0, deg))
    return round(deg, 3)


# --- Proporcion clase 12 por poligono y año (lectura raster) -----------------
def calc_mapbiomas_proportions(polygons_gdf, year, mb_meta, proc_deg):
    if year < YEAR_MIN or year > YEAR_MAX:
        return np.full(len(polygons_gdf), np.nan)

    n          = len(polygons_gdf)
    _sum_n12   = np.zeros(n, dtype=np.float64)
    _sum_total = np.zeros(n, dtype=np.float64)

    if len(mb_meta) == 0:
        return np.full(n, np.nan)

    # El indice de banda MapBiomas se calcula POR TILE (band_for_year), ya que
    # los tiles MapBiomas (24 bandas, 2001-2024) usan otra convencion que los
    # tiles BA (40 bandas). Validacion: la ultima banda debe ser >= year.
    max_nb = max(m["n_bands"] for m in mb_meta.values())
    if band_for_year(year, max_nb) < 1:
        print(f"  [ERROR] año {year} fuera de rango de bandas MapBiomas "
              f"(max_nb={max_nb}).")
        return np.full(n, np.nan)

    repr_pts = polygons_gdf.geometry.representative_point()
    minx, miny, maxx, maxy = polygons_gdf.total_bounds
    xs = np.arange(np.floor(minx), np.ceil(maxx), proc_deg)
    ys = np.arange(np.floor(miny), np.ceil(maxy), proc_deg)

    for xt in xs:
        for yt in ys:
            proc_tile = box(xt, yt, xt + proc_deg, yt + proc_deg)
            mask_tile = repr_pts.within(proc_tile)
            if not mask_tile.any():
                continue

            idx_global = np.where(mask_tile.values)[0]
            polys_sub  = polygons_gdf.iloc[idx_global].reset_index(drop=True)

            proc_tile_exp = box(
                xt - MAPBIOMAS_EXTENT_BUFFER_DEG,
                yt - MAPBIOMAS_EXTENT_BUFFER_DEG,
                xt + proc_deg + MAPBIOMAS_EXTENT_BUFFER_DEG,
                yt + proc_deg + MAPBIOMAS_EXTENT_BUFFER_DEG,
            )

            for (row, col), meta in mb_meta.items():
                if not proc_tile_exp.intersects(meta["bounds_geom"]):
                    continue
                band_idx = band_for_year(year, meta["n_bands"])
                if band_idx < 1 or band_idx > meta["n_bands"]:
                    continue

                tile_path    = MAPBIOMAS_TILES[(row, col)]
                nodata_class = meta["nodata"] if meta["nodata"] is not None else 0
                try:
                    with rasterio.open(tile_path) as src:
                        raster_crs = src.crs
                        if raster_crs != WGS84:
                            exp_reproj = (gpd.GeoDataFrame(
                                [0], geometry=[proc_tile_exp], crs=WGS84
                            ).to_crs(raster_crs).geometry.iloc[0])
                            polys_reproj = polys_sub.to_crs(raster_crs)
                        else:
                            exp_reproj   = proc_tile_exp
                            polys_reproj = polys_sub

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
                        nodata=nodata_class, all_touched=True,
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
                except Exception as e:
                    print(f"  [WARN] {type(e).__name__} MB tile ({row},{col}) "
                          f"celda({xt:.0f},{yt:.0f}) año {year}: {e}")
            gc.collect()

    proportions = np.full(n, np.nan)
    valid = _sum_total > 0
    proportions[valid] = np.round(_sum_n12[valid] / _sum_total[valid] * 100.0, 2)
    return proportions


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
    # groupby().first() puede devolver un DataFrame plano (pierde geometria/CRS).
    # Re-envolver como GeoDataFrame garantiza .geometry / .to_crs aguas abajo.
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
        # sjoin_nearest puede duplicar; conservar el primero por indice
        res2 = res2[~res2.index.duplicated(keep="first")]
        gdf_out.loc[mask_nan2, "gaul0_code"] = res2["gaul0_code"].values
        gdf_out.loc[mask_nan2, "gaul0_name"] = res2["gaul0_name"].values

    return gdf_out


# --- Parseo de nombre de tile ------------------------------------------------
def _parse_tile_bounds_from_name(path):
    """Pattern: ...Lat{a}to{b}_Lon{c}to{d}.tif (precision ENTERA de grado)."""
    m = re.search(r'Lat([+-]?\d+)to([+-]?\d+)_Lon([+-]?\d+)to([+-]?\d+)',
                  path.stem)
    if not m:
        return None
    lat1, lat2 = int(m[1]), int(m[2])
    lon1, lon2 = int(m[3]), int(m[4])
    return (min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))


def _parse_tile_index_from_name(path):
    m = re.search(r'_T(\d+)', path.stem)
    return int(m[1]) if m else 0


# --- Descubrimiento de tiles BA ----------------------------------------------
def discover_ba_tiles():
    """Lista de tiles BA que intersectan el ROI. No abre ningun archivo."""
    rx0, ry0, rx1, ry1 = ROI_BBOX
    tiles = []
    for region in BA_REGIONS:
        rd = BA_RAW_DIR / region
        if not rd.exists():
            print(f"  [WARN] region ausente: {region}")
            continue
        for p in sorted(rd.glob("*.tif")):
            b = _parse_tile_bounds_from_name(p)
            if b is not None:
                t_minx, t_miny, t_maxx, t_maxy = b
                if (t_maxx <= rx0 or t_minx >= rx1 or
                        t_maxy <= ry0 or t_miny >= ry1):
                    continue
            else:
                t_minx = t_miny = t_maxx = t_maxy = 999
            tiles.append({
                "region": region, "path": p,
                "tile_idx": _parse_tile_index_from_name(p),
                "minx": t_minx, "miny": t_miny,
                "maxx": t_maxx, "maxy": t_maxy,
            })
    return tiles


# --- Extraccion de eventos de UN tile (todos los años) -----------------------
def _extract_tile_events(tile, dem_src):
    """Abre el tile una vez, reproyecta el DEM una vez, recorre los 40 años.

    Devuelve una lista de records (dicts) en WGS84. Vacia si el tile no tiene
    pixeles quemados sobre ELEV_THRESHOLD en ningun año.
    """
    region    = tile["region"]
    tidx      = tile["tile_idx"]
    path      = tile["path"]
    structure = np.ones((3, 3), dtype=int)
    records   = []

    try:
        src = rasterio.open(path)
    except Exception as e:
        print(f"  [WARN] {type(e).__name__} {path.name}: {e}")
        return records

    with src:
        n_bands        = src.count
        tile_transform = src.transform
        tile_crs       = src.crs
        H, W           = src.height, src.width

        # DEM al grid del tile (una sola vez para los 40 años)
        dem_tile = np.full((H, W), np.nan, dtype=np.float32)
        try:
            rasterio.warp.reproject(
                source=rasterio.band(dem_src, 1), destination=dem_tile,
                src_transform=dem_src.transform, src_crs=dem_src.crs,
                dst_transform=tile_transform, dst_crs=tile_crs,
                resampling=rasterio.warp.Resampling.bilinear,
                src_nodata=dem_src.nodata, dst_nodata=np.nan,
            )
        except Exception:
            pass

        elev_ok = dem_tile >= ELEV_THRESHOLD     # NaN >= thr → False
        if not elev_ok.any():
            return records                       # tile entero bajo el umbral

        for year in YEARS_RUN:
            band_idx = band_for_year(year, n_bands)
            if band_idx < 1 or band_idx > n_bands:
                continue
            try:
                ba_data = src.read(band_idx)
            except Exception:
                continue

            valid = (ba_data > 0) & elev_ok
            if not valid.any():
                del ba_data, valid
                continue

            labeled = np.zeros((H, W), dtype=np.int32)
            ndimage.label(valid, structure=structure, output=labeled)
            n_evt = int(labeled.max())
            del valid
            if n_evt == 0:
                del ba_data, labeled
                continue

            # Labels que tocan el borde del tile (candidatos a partirse)
            border = (set(np.unique(labeled[0]))  | set(np.unique(labeled[-1])) |
                      set(np.unique(labeled[:, 0])) | set(np.unique(labeled[:, -1])))
            border.discard(0)

            # SAMPLE_FRAC (v8.0.1): muestreo BERNOULLI — cada evento se conserva
            # con probabilidad = SAMPLE_FRAC, sin el suelo "minimo 1" que antes
            # aplastaba todo a 1 evento/tile-año (los tiles son diminutos, 1-3
            # eventos cada uno). Semilla robusta por SeedSequence (no colisiona
            # aunque tidx sea grande).
            all_ids = np.arange(1, n_evt + 1, dtype=np.int32)
            if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
                rng  = np.random.default_rng(
                    np.random.SeedSequence([RANDOM_SEED, year, tidx]))
                keep = rng.random(n_evt) < SAMPLE_FRAC
                ids  = all_ids[keep]
                if ids.size == 0:            # este tile-año no aporta eventos
                    del ba_data, labeled
                    continue
                sampled = np.where(np.isin(labeled, ids), labeled, 0).astype(
                    np.int32)
            else:
                ids     = all_ids
                sampled = labeled

            # Stats vectorizadas (nivel C)
            BurnDate  = np.round(
                ndimage.median(ba_data.astype(np.float32), labeled, ids)
            ).astype(np.int32)
            Elevation = np.asarray(
                ndimage.mean(dem_tile, labeled, ids), dtype=np.float32)

            # Geometrias
            geom_by_label = defaultdict(list)
            for gd, lv in rasterio.features.shapes(
                sampled, mask=(sampled > 0).astype(np.uint8),
                transform=tile_transform,
            ):
                geom_by_label[int(lv)].append(shape(gd))

            id_to_idx = {int(e): i for i, e in enumerate(ids)}
            for eid, geoms in geom_by_label.items():
                i = id_to_idx.get(eid)
                if i is None:
                    continue
                geom = geoms[0] if len(geoms) == 1 else unary_union(geoms)
                records.append({
                    "geometry"      : geom,
                    "year"          : year,
                    "BurnDate"      : int(BurnDate[i]),
                    "Elevation"     : round(float(Elevation[i]), 1),
                    "event_uid"     : f"{region}_T{tidx}_{year}_{eid}",
                    "touches_border": bool(eid in border),
                })

            del ba_data, labeled, geom_by_label, BurnDate, Elevation, id_to_idx
            if sampled is not None:
                del sampled
        del dem_tile

    # Defensa: si el tile no estuviera en WGS84, reproyectar geometrias
    if records and tile_crs is not None and tile_crs != WGS84:
        g = gpd.GeoDataFrame(records, crs=tile_crs).to_crs(WGS84)
        records = g.to_dict("records")
    return records


# --- Enriquecimiento de un lote de eventos -----------------------------------
def enrich_events(records, countries_gdf, mb_meta, proc_deg):
    """sjoin + Zone_Clima + COUNTRY_FILTER + month + area + MapBiomas."""
    if not records:
        return None

    gdf = gpd.GeoDataFrame(records, crs=WGS84)
    gdf["Zone_Clima"] = assign_zone_clima(gdf)
    gdf = spatial_join_3attempts(gdf, countries_gdf)
    gdf["ADM0_CODE"] = gdf["gaul0_code"]

    if COUNTRY_FILTER:
        gdf = gdf[gdf["gaul0_name"] == COUNTRY_FILTER].reset_index(drop=True)
    if len(gdf) == 0:
        return gdf

    gdf["month"] = doy_year_to_month(gdf["BurnDate"].values, gdf["year"].values)

    # Area en EPSG:3857 (decision del usuario)
    utm = gdf.to_crs("EPSG:3857")
    a   = utm.geometry.area.values
    del utm
    gdf["area_ha"]  = np.round(a / 10_000, 2)
    gdf["area_km2"] = np.round(a / 1_000_000, 4)

    # MapBiomas: por año (la banda depende del año)
    gdf["pct_class12"] = np.nan
    for yr in sorted(gdf["year"].unique()):
        sub = gdf[gdf["year"] == yr].reset_index()   # 'index' = idx original
        pct = calc_mapbiomas_proportions(sub, int(yr), mb_meta, proc_deg)
        gdf.loc[sub["index"].values, "pct_class12"] = pct

    gdf["area_class12_ha"] = (
        gdf["pct_class12"] / 100 * gdf["area_ha"]
    ).round(2)

    # Conservar NaN (sin dato MB) o area distinta de 0
    gdf = gdf[
        gdf["area_class12_ha"].isna() | (gdf["area_class12_ha"] != 0)
    ].reset_index(drop=True)
    return gdf


def _flush_buffer(buf, countries_gdf, mb_meta, proc_deg, gpkg_path, first_write):
    gdf = enrich_events(buf, countries_gdf, mb_meta, proc_deg)
    if gdf is None or len(gdf) == 0:
        return first_write, 0
    cols = [c for c in COL_ORDER if c in gdf.columns]
    gdf  = gdf[cols]
    mode = "w" if first_write else "a"
    gdf.to_file(gpkg_path, driver="GPKG", mode=mode)
    n = len(gdf)
    del gdf
    gc.collect()
    return False, n


# --- Procesamiento del grupo de tiles de un worker ---------------------------
def process_tile_group(tiles, countries_gdf, mb_meta, dem_path,
                       gpkg_path, worker_id):
    proc_deg    = mapbiomas_proc_tile_deg(mb_meta)
    print(f"  [w{worker_id}] {len(tiles)} tiles | MapBiomas PROC_TILE_DEG="
          f"{proc_deg}° | flush cada {FLUSH_EVERY} eventos")

    buf         = []
    first_write = True
    n_written   = 0
    t0          = time.time()

    with rasterio.open(dem_path) as dem_src:
        for i, tile in enumerate(tiles, 1):
            recs = _extract_tile_events(tile, dem_src)
            if recs:
                buf.extend(recs)
            if len(buf) >= FLUSH_EVERY:
                first_write, w = _flush_buffer(
                    buf, countries_gdf, mb_meta, proc_deg,
                    gpkg_path, first_write)
                n_written += w
                buf = []
            if i % 2000 == 0:
                el = timedelta(seconds=int(time.time() - t0))
                print(f"  [w{worker_id}] {i}/{len(tiles)} tiles | "
                      f"buffer={len(buf)} | escritos={n_written} | {el}")
                gc.collect()

    if buf:
        first_write, w = _flush_buffer(
            buf, countries_gdf, mb_meta, proc_deg, gpkg_path, first_write)
        n_written += w

    print(f"  [w{worker_id}] FIN: {n_written} eventos → {gpkg_path.name} "
          f"({timedelta(seconds=int(time.time() - t0))})")
    return n_written


# --- Reintegracion de ba_id --------------------------------------------------
def _union_find_labels(n, pairs):
    """Componentes conexas. pairs: iterable de (i, j) con 0<=i,j<n."""
    parent = list(range(n))

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:      # compresion de camino
            parent[x], x = root, parent[x]
        return root

    for i, j in pairs:
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[ri] = rj

    comp, out = {}, np.empty(n, dtype=np.int64)
    for x in range(n):
        r = find(x)
        if r not in comp:
            comp[r] = len(comp)
        out[x] = comp[r]
    return out, len(comp)


def reintegrate_ba_ids(gdf):
    """Asigna ba_id. Los eventos interiores conservan ba_id=event_uid; los que
    tocan borde se fusionan por contiguidad geometrica DENTRO del mismo año.

    Implementacion escalable: en lugar de unary_union de cientos de miles de
    poligonos (superlineal), se hace un self-sjoin (STRtree) para hallar pares
    de eventos que se tocan y union-find para etiquetar componentes conexas.
    """
    gdf = gdf.copy()
    gdf["ba_id"] = gdf["event_uid"]

    if "touches_border" not in gdf.columns:
        return gdf
    bmask  = gdf["touches_border"].fillna(False).astype(bool)
    border = gdf[bmask]
    if len(border) == 0:
        print("  ba_id: 0 eventos en borde — sin reintegracion necesaria")
        return gdf

    new_ids   = {}
    n_objects = 0
    for yr, grp in border.groupby("year"):
        b    = grp.reset_index(drop=True)
        uids = b["event_uid"].values
        n    = len(b)
        # Buffer diminuto: cierra la costura entre mitades adyacentes de tiles.
        gb = gpd.GeoDataFrame(
            geometry=b.geometry.buffer(REINTEGRATION_BUFFER_DEG), crs=gdf.crs
        ).reset_index(drop=True)
        pairs_df = gpd.sjoin(gb, gb, predicate="intersects")
        pairs    = zip(pairs_df.index.values, pairs_df["index_right"].values)
        labels, k = _union_find_labels(n, pairs)
        n_objects += k
        for i in range(n):
            new_ids[uids[i]] = f"BA_{int(yr)}_{int(labels[i])}"
        del gb, pairs_df

    gdf["ba_id"] = gdf["event_uid"].map(lambda u: new_ids.get(u, u))
    print(f"  ba_id: {len(border)} eventos en borde → {n_objects} objetos "
          f"fisicos reintegrados")
    return gdf


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, mb_meta,
                             gdf_result, output_dir, base_name):
    if aoi_geom is None:
        return
    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'-'*58}\n  Exportacion cartografica (AOI)\n{'-'*58}")

    # DEM recortado al AOI
    dem_out = output_dir / f"{base_name}_aoi_dem.tif"
    try:
        with rasterio.open(dem_path) as src:
            aoi_local = (gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                         .to_crs(src.crs).geometry.iloc[0]
                         if src.crs != WGS84 else aoi_geom)
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

    # MapBiomas: raster recortado + vectorizacion de clase 12 (solo AOI)
    class12_polys = []
    n_mb = 0
    for (r, c), meta in mb_meta.items():
        if not aoi_geom.intersects(meta["bounds_geom"]):
            continue
        crs_t = meta["crs"]
        aoi_local = (gpd.GeoDataFrame([0], geometry=[aoi_geom], crs=WGS84)
                     .to_crs(crs_t).geometry.iloc[0]
                     if crs_t != WGS84 else aoi_geom)

        # Raster recortado (todas las bandas)
        mb_out = output_dir / f"{base_name}_aoi_mapbiomas_r{r}c{c}.tif"
        try:
            with rasterio.open(MAPBIOMAS_TILES[(r, c)]) as src:
                out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                           all_touched=True)
                out_meta = {**src.meta, "driver": "GTiff", "compress": "lzw",
                            "height": out_img.shape[1],
                            "width": out_img.shape[2], "transform": out_tr}
            with rasterio.open(mb_out, "w", **out_meta) as dst:
                dst.write(out_img)
            n_mb += 1
            print(f"  [OK] MapBiomas r{r}c{c}   -> {mb_out.name}")
        except Exception as e:
            print(f"  [WARN] MapBiomas r{r}c{c} clip: {e}")

        # Vectorizacion clase 12 por año (ventana acotada al AOI)
        try:
            with rasterio.open(MAPBIOMAS_TILES[(r, c)]) as src:
                win = rasterio.windows.from_bounds(
                    *aoi_local.bounds, transform=src.transform
                ).intersection(
                    rasterio.windows.Window(0, 0, src.width, src.height))
                if win.width <= 0 or win.height <= 0:
                    continue
                win_tr = src.window_transform(win)
                for year in YEARS_RUN:
                    bidx = band_for_year(year, src.count)
                    if bidx < 1 or bidx > src.count:
                        continue
                    arr  = src.read(bidx, window=win)
                    mask = (arr == 12).astype(np.uint8)
                    if not mask.any():
                        continue
                    for gd, _v in rasterio.features.shapes(
                            mask, mask=mask, transform=win_tr):
                        class12_polys.append({"geometry": shape(gd),
                                              "year": year})
        except Exception as e:
            print(f"  [WARN] vectorizacion clase12 r{r}c{c}: {e}")

    if class12_polys:
        c12 = gpd.GeoDataFrame(class12_polys, crs=meta["crs"])
        if c12.crs != WGS84:
            c12 = c12.to_crs(WGS84)
        c12_out = output_dir / f"{base_name}_aoi_mapbiomas_class12.gpkg"
        c12.to_file(c12_out, driver="GPKG")
        print(f"  [OK] Clase 12 (vector) -> {c12_out.name} "
              f"({len(c12)} poligonos)")

    # Resultados dentro del AOI (SHP + CSV — pequeño)
    mask_aoi = gdf_result.geometry.intersects(aoi_geom)
    gdf_aoi  = gdf_result[mask_aoi].reset_index(drop=True)
    if len(gdf_aoi) > 0:
        shp_aoi = output_dir / f"{base_name}_aoi_results.shp"
        csv_aoi = output_dir / f"{base_name}_aoi_results.csv"
        gdf_aoi.to_file(shp_aoi)
        gdf_aoi.drop(columns=["geometry"]).to_csv(
            csv_aoi, index=False, encoding="utf-8-sig")
        print(f"  [OK] Resultados AOI   -> {shp_aoi.name} ({len(gdf_aoi)} pol.)")
    else:
        print("  [INFO] Ningun resultado intersecta el AOI.")
    timer(f"Exportacion cartografica ({n_mb} tiles MB)", t)


# --- Validacion previa -------------------------------------------------------
def _preprocess_only():
    t = time.time()
    print("  Paso 0: validando tiles MapBiomas...")
    mb_meta = get_mapbiomas_metadata()
    print(f"  Tiles MapBiomas disponibles: {len(mb_meta)}/9")
    if len(mb_meta) == 0:
        print("  [ERROR] Ningun tile MapBiomas. Revisar mapbiomas_dir.")
        return False
    nbset = {m["n_bands"] for m in mb_meta.values()}
    need  = YEAR_MAX - YEAR_MIN + 1   # 24 años (2001-2024)
    if any(nb < need for nb in nbset):
        print(f"  [WARN] algun tile MapBiomas tiene < {need} bandas {nbset}; "
              f"no cubre 2001-2024. Se ancla la ultima banda a {YEAR_MAX}.")
    else:
        print(f"  MapBiomas n_bands={nbset} (ultima banda anclada a {YEAR_MAX}: "
              f"banda(2001)={ {nb: band_for_year(2001, nb) for nb in nbset} }).")
    print(f"  MapBiomas PROC_TILE_DEG (auto): "
          f"{mapbiomas_proc_tile_deg(mb_meta)}°")
    timer("Validacion MapBiomas", t)
    return True


# --- Merge + ba_id + exportacion final ---------------------------------------
def merge_and_export(run_tag, n_workers):
    t = time.time()
    print(f"\n{'-'*58}\n  Merge de resultados de workers\n{'-'*58}")

    worker_paths = [test_dir / f"BurnedAreas_MODIS_V8_{run_tag}_w{k}.gpkg"
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

    # Reintegracion de objetos fisicos partidos en bordes de tile
    t2  = time.time()
    gdf = reintegrate_ba_ids(gdf)
    timer("Reintegracion ba_id", t2)

    cols = [c for c in COL_ORDER_FINAL if c in gdf.columns]
    gdf  = gdf[cols]

    base     = f"BurnedAreas_MODIS_V8_{run_tag}"
    gpkg_out = test_dir   / f"{base}.gpkg"
    csv_out  = output_dir / f"{base}.csv"
    os.makedirs(output_dir, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    gdf.to_file(gpkg_out, driver="GPKG")
    gdf.drop(columns=["geometry"]).to_csv(
        csv_out, index=False, encoding="utf-8-sig")
    print(f"  [OK] GPKG → {gpkg_out.name}")
    print(f"  [OK] CSV  → {csv_out.name}")
    print(f"  [INFO] SHP omitido en full-extent (limite 2 GB). Ver capas AOI.")
    timer(f"Merge ({len(gdf)} features)", t)
    return gdf


# --- Worker ------------------------------------------------------------------
def run_worker(worker_id, n_workers):
    t0 = time.time()
    run_tag = run_tag_of(TILE_SAMPLE_FRAC)
    print(f"\n{'='*58}\n  WORKER {worker_id}/{n_workers} — BurnedAreas v8")
    _ev   = f"{int(SAMPLE_FRAC*100)}%" if (SAMPLE_FRAC and SAMPLE_FRAC < 1.0) else "100%"
    _ti   = f"{int(TILE_SAMPLE_FRAC*100)}%" if (TILE_SAMPLE_FRAC and TILE_SAMPLE_FRAC < 1.0) else "100%"
    _modo = "PRODUCCION" if run_tag == "vf" else f"TEST (tiles={_ti}, eventos={_ev})"
    print(f"  MODO: {_modo}")
    print(f"{'='*58}")

    all_tiles = discover_ba_tiles()
    # Orden espacial → franjas contiguas, repartidas round-robin entre workers.
    # Cada worker recibe varias franjas dispersas por todo el ROI, asi se
    # equilibra la carga de fuegos (que se concentra en los Andes) en vez de
    # caer toda en un solo worker. Dentro de cada franja los tiles siguen
    # contiguos (buena localidad para la lectura de MapBiomas).
    all_tiles.sort(key=lambda t: (t["miny"], t["minx"], t["region"],
                                  t["tile_idx"]))
    STRIPE_FACTOR = 8
    n_stripes  = n_workers * STRIPE_FACTOR
    stripe_sz  = math.ceil(len(all_tiles) / n_stripes)
    my = []
    for s in range(worker_id, n_stripes, n_workers):
        my += all_tiles[s * stripe_sz:(s + 1) * stripe_sz]
    print(f"  Tiles totales en ROI: {len(all_tiles)} | este worker: {len(my)} "
          f"({STRIPE_FACTOR} franjas)")

    # v8.0.1: submuestreo de TILES (recorta el I/O real → reduce el tiempo).
    # Determinista por worker. Produccion: TILE_SAMPLE_FRAC None o >= 1.0.
    if TILE_SAMPLE_FRAC is not None and TILE_SAMPLE_FRAC < 1.0 and my:
        rng = np.random.default_rng(
            np.random.SeedSequence([RANDOM_SEED, worker_id]))
        k   = max(1, int(len(my) * TILE_SAMPLE_FRAC))
        sel = np.sort(rng.choice(len(my), size=k, replace=False))
        my  = [my[i] for i in sel]
        print(f"  [w{worker_id}] TILE_SAMPLE_FRAC={TILE_SAMPLE_FRAC} → "
              f"{len(my)} tiles leidos")

    if not my:
        print("  [INFO] Sin tiles asignados.")
        return

    countries_gdf = load_countries(
        data_dir / "GAUL_2024_L1.shp", COUNTRIES_ADM0, roi_geom)
    mb_meta = get_mapbiomas_metadata()

    gpkg_path = test_dir / f"BurnedAreas_MODIS_V8_{run_tag}_w{worker_id}.gpkg"
    test_dir.mkdir(parents=True, exist_ok=True)
    if gpkg_path.exists():
        gpkg_path.unlink()   # evitar append sobre una corrida anterior

    process_tile_group(my, countries_gdf, mb_meta, DEM_PATH,
                       gpkg_path, worker_id)
    print(f"  TOTAL worker {worker_id}: "
          f"{timedelta(seconds=int(time.time() - t0))}")


# --- Orquestador -------------------------------------------------------------
def run_orchestrator():
    t0      = time.time()
    run_tag = run_tag_of(TILE_SAMPLE_FRAC)
    n_workers = N_WORKERS

    print(f"\n{'='*58}")
    print(f"  ORQUESTADOR — BurnedAreas_MODIS v8 (paralelo por espacio)")
    print(f"  Anos    : {YEARS_RUN}")
    print(f"  Workers : {n_workers}")
    print(f"  Modo    : {run_tag}")
    print(f"{'='*58}")

    if not _preprocess_only():
        sys.exit(1)

    all_tiles = discover_ba_tiles()
    if not all_tiles:
        print("  [ERROR] Ningun tile BA en el ROI. Abortando.")
        sys.exit(1)
    by_region = {r: sum(1 for t in all_tiles if t["region"] == r)
                 for r in BA_REGIONS}
    print(f"  Tiles BA en ROI: {len(all_tiles)}  {by_region}")
    print(f"  Reparto: ~{math.ceil(len(all_tiles)/n_workers)} tiles/worker")

    def _launch(k):
        r = subprocess.run(
            [sys.executable, __file__,
             "--worker-id", str(k), "--n-workers", str(n_workers)],
            text=True, capture_output=True)
        if r.stdout:
            print("\n".join(r.stdout.strip().splitlines()[-8:]))
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
            mb_meta  = get_mapbiomas_metadata()
            save_cartographic_layers(
                aoi_geom, DEM_PATH, mb_meta, gdf, output_dir,
                f"BurnedAreas_MODIS_V8_{run_tag}")
            cols_show = [c for c in ["event_uid", "ba_id", "year", "month",
                                     "BurnDate", "Elevation", "Zone_Clima",
                                     "pct_class12", "area_ha", "gaul0_name"]
                         if c in gdf.columns]
            print(gdf[cols_show].head())

    timer(f"TOTAL orquestador ({len(ok)}/{n_workers} workers OK)", t0)


# --- Ejecucion ---------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BurnedAreas_MODIS pipeline v8")
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