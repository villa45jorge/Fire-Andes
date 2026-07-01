# -*- coding: utf-8 -*-
import os
# Aumentar cache GDAL para lectura eficiente de tiles MapBiomas (~138 GB por tile).
# GDAL lee bloques de ~256 KB del archivo; con 1 GB de cache reutiliza bloques
# recientes y reduce I/O. Debe setearse antes de que rasterio importe GDAL.
os.environ.setdefault("GDAL_CACHEMAX", "1024")
"""
Modified on 01/07/2026
Version 5.3.0
@author: jvilla

Base   : script_BA_V4.py  (MCD64A1 — Burned Areas raster)
Logica : script_AT_V5.py

Changes v5.2.0 — optimizacion de memoria (OOM en cluster):
    [B1] Una banda por mes: rasterio.mask.mask(indexes=[month]) dentro del
         loop mensual. Elimina ba_all (12 x ROI x 2 bytes) del heap.
         El archivo queda abierto durante el loop del ano; se abre/cierra
         una segunda vez para leer las bandas.
    [B2] Geometrias vectorizadas: rasterio.features.shapes(labeled_arr)
         una sola llamada por mes. Agrupa shapes por label en defaultdict
         → unary_union por evento. Elimina event_mask (N x ROI) del loop.
    [B3] CRS batch: gdf_month.to_crs("EPSG:3857") una llamada para todos
         los eventos del mes en lugar de gpd.GeoSeries().to_crs() por evento.
    [B4] Stats vectorizados: pandas groupby sobre arrays planos de pixeles
         validos → BurnDate (mode), Elevation (mean), ADM0_CODE (mode).
         No hay iteracion Python por evento para estadisticas.
    [B5] Escritura por ano: GPKG append mode al final de cada ano.
         del gdf_year + gc.collect() inmediato. Elimina all_yearly_gdfs y
         pd.concat final. Peak RAM = 1 ano de datos en memoria.
    [Refactor] extract_month_events(): nueva funcion con la logica B2-B4.
    [Refactor] spatial_join_3attempts(): extraida de run_pipeline.

Changes v5.1.0:
    [Sample]     SAMPLE_FRAC / RANDOM_SEED: submuestreo aleatorio de eventos
                 a nivel de evento (despues de ndimage.label, no a nivel pixel).

Changes v5.0.0:
    [MapBiomas]  WorldCover reemplazado por MapBiomas multi-anual.
    [AOI]        Area de Interes como parametro global.
    [Zone_Clima] Zona climatica asignada via representative_point().
    [Clustering] ndimage.label conservado.
    [Timer]      Convertido de context manager a funcion.
    [Salida]     GPKG (test_dir) + SHP + CSV (output_dir).
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
from rasterio.transform import array_bounds
from concurrent.futures import ProcessPoolExecutor, as_completed

# --- Rutas -------------------------------------------------------------------
base_dir      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD64A1")
data_dir      = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir    = base_dir / "3_output"
test_dir      = base_dir / "4_test"

# Tiles MapBiomas compartidos con el pipeline AT (mismos archivos de referencia)
mapbiomas_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML/3_output")

# --- Area de Interes (AOI) — solo para exportacion cartografica --------------
# El analisis cubre siempre todo el ROI. La AOI se usa unicamente para recortar
# los rasters (DEM, MapBiomas) y el shapefile/CSV resultado al final.
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales (analisis) ------------------------------------------
ROI_BBOX       = (-80.0, -20.0, -60.0,  1.0)
YEARS_TEST     = [2003, 2005, 2012, 2015, 2020, 2024]
ELEV_THRESHOLD = 2000
COUNTRIES_ADM0 = [178, 184, 185, 190, 207]

YEAR_MIN        = 2001
YEAR_MAX        = 2024
MAPBIOMAS_BANDS = YEAR_MAX - YEAR_MIN + 1   # 24

MAPBIOMAS_TILES = {
    (r, c): mapbiomas_dir / f"clase12_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}

# Tiles MapBiomas pre-procesados al grid BA (creados por preprocess_mapbiomas).
# Resolucion: igual al mosaico BA (3341x8017 px para el ROI actual).
# Tamano: ~640 MB por tile (vs 138 GB del tile original a 30m).
MB_BA_DIR = processed_dir / "mapbiomas_ba_grid"
MAPBIOMAS_BA_TILES = {
    (r, c): MB_BA_DIR / f"mapbiomas_ba_r{r}c{c}.tif"
    for r in range(3) for c in range(3)
}

WGS84 = CRS.from_epsg(4326)

roi_geom      = box(*ROI_BBOX)
roi_geom_list = [mapping(roi_geom)]

# --- Submuestreo para test de recursos del cluster ---------------------------
# Fraccion de eventos a procesar por mes (despues de ndimage.label).
# None = produccion completa.
SAMPLE_FRAC = 0.05
RANDOM_SEED = 42

# --- Numero de workers paralelos ---------------------------------------------
# Lee automaticamente el numero de cores asignados por SLURM (-c N).
# Si no hay variable SLURM, usa 3 como default conservador.
# Para el test: mantener en 3 (3 × ~3 GB = 9 GB + orquestador ~2 GB = 11 GB).
# Para produccion: subir a 6 con --mem=40G en el script SLURM.
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# --- Filtro de area minima ---------------------------------------------------
# Descarta eventos con menos de MIN_PIX pixeles ANTES de cualquier operacion
# Python. El 80-95% de las detecciones MODIS son pixeles aislados (ruido).
# A 500m: 1 px = 25 ha → MIN_PIX = 5 equivale a un umbral minimo de ~125 ha.
# Este filtro es el principal causante del OOM: sin el, ndimage.label produce
# ~2M eventos/mes y el groupby pandas abre ~4M objetos temporales Python.
# Con el filtro, N_events desciende a ~50K → groupby < 100 MB.
MIN_PIX = 5


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


# --- Preprocesamiento MapBiomas al grid BA (paso unico) ----------------------
def preprocess_mapbiomas(mb_meta, ba_transform, ba_crs, ba_shape):
    """
    Remuestrea los tiles MapBiomas originales (138 GB, 30m) al grid BA
    (500m, ~640 MB por tile) usando gdalwarp como subproceso.

    gdalwarp corre en su propio proceso: no consume RAM del pipeline principal.
    Idempotente: omite tiles que ya existan en MB_BA_DIR.
    El resultado queda en MB_BA_DIR para todos los runs siguientes.

    ba_transform : Affine del grid BA (de rasterio.mask.mask sobre BA file)
    ba_crs       : CRS del grid BA
    ba_shape     : (h, w) del grid BA
    """
    os.makedirs(MB_BA_DIR, exist_ok=True)

    left, bottom, right, top = array_bounds(ba_shape[0], ba_shape[1], ba_transform)

    pending = [
        (r, c) for (r, c) in mb_meta
        if not MAPBIOMAS_BA_TILES[(r, c)].exists()
    ]

    if not pending:
        n_ok = sum(1 for p in MAPBIOMAS_BA_TILES.values() if p.exists())
        print(f"  Tiles MB pre-procesados : {n_ok}/9 ya existen")
        return

    print(f"  Pre-procesando {len(pending)} tiles MapBiomas → grid BA")
    print(f"  (solo se ejecuta una vez — puede tardar varios minutos por tile)")

    for r, c in pending:
        src_path = MAPBIOMAS_TILES[(r, c)]
        out_path = MAPBIOMAS_BA_TILES[(r, c)]
        t = time.time()

        try:
            res = subprocess.run(
                [
                    "gdalwarp",
                    "-t_srs",    str(ba_crs),
                    "-te",       str(left), str(bottom), str(right), str(top),
                    "-ts",       str(ba_shape[1]), str(ba_shape[0]),
                    "-r",        "near",
                    "-ot",       "Byte",
                    "-co",       "COMPRESS=LZW",
                    "-co",       "TILED=YES",
                    "-co",       "BLOCKXSIZE=256",
                    "-co",       "BLOCKYSIZE=256",
                    "--config",  "GDAL_CACHEMAX", "1024",
                    str(src_path), str(out_path),
                ],
                capture_output=True, text=True
            )

            if res.returncode != 0:
                print(f"  [WARN] gdalwarp error r{r}c{c}: {res.stderr[:300]}")
                if out_path.exists():
                    out_path.unlink()
            else:
                mb_sz = round(out_path.stat().st_size / 1e6, 0)
                timer(f"r{r}c{c}: {mb_sz} MB", t)

        except FileNotFoundError:
            print("  [ERROR] gdalwarp no encontrado. Verificar instalacion de GDAL.")
            return

    n_ok = sum(1 for p in MAPBIOMAS_BA_TILES.values() if p.exists())
    print(f"  Tiles MB pre-procesados : {n_ok}/9 listos en {MB_BA_DIR.name}/")


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
                "bounds_geom": box(*src.bounds),
                "crs"        : src.crs,
                "nodata"     : src.nodata,
            }
    return meta


# --- Proporcion clase 12 por poligono y anio ---------------------------------
def calc_mapbiomas_proportions(polygons_gdf, year):
    """
    Lee los tiles pre-procesados al grid BA (~27 MB por banda) y calcula
    pct_class12 via zonal_stats.

    Los tiles son creados por preprocess_mapbiomas() en run_pipeline paso 5.
    Memoria por llamada: 27 MB (mb_at_ba) + 27 MB (class12_ba) + overhead zonal_stats.
    """
    if year < YEAR_MIN or year > YEAR_MAX:
        print(f"  [INFO] Anio {year} fuera del rango MapBiomas -> pct_class12 = NaN")
        return np.full(len(polygons_gdf), np.nan)

    band_idx    = year - YEAR_MIN + 1
    n           = len(polygons_gdf)
    proportions = np.full(n, np.nan)

    for (row, col), path in MAPBIOMAS_BA_TILES.items():
        if not path.exists():
            continue

        try:
            with rasterio.open(path) as src:
                mb_at_ba       = src.read(band_idx)   # uint8, ~27 MB
                tile_transform = src.transform

            # Mascara binaria: 1 = clase 12, 0 = todo lo demas
            class12_ba = (mb_at_ba == 12).view(np.uint8)
            del mb_at_ba

            stats_n12 = zonal_stats(
                polygons_gdf, class12_ba, affine=tile_transform,
                stats=["sum"], nodata=None
            )
            stats_total = zonal_stats(
                polygons_gdf, class12_ba, affine=tile_transform,
                stats=["count"], nodata=None
            )
            del class12_ba

            for i in range(n):
                n12     = stats_n12[i].get("sum") or 0
                n_total = stats_total[i].get("count") or 0
                if n_total > 0:
                    v = round(float(n12) / float(n_total) * 100.0, 2)
                    proportions[i] = v if np.isnan(proportions[i]) \
                                     else (proportions[i] + v) / 2.0

        except Exception as e:
            print(f"  [WARN] Error tile MB BA ({row},{col}) anio {year}: {e}")

        gc.collect()

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


# ── 4. Extraccion vectorizada de eventos (un mes) ──────────────────────────────
def extract_month_events(ba_data, labeled_arr, valid, dem_data, cntry_arr,
                          ba_transform, ba_crs, year, month, num_events):
    """
    v5.3.0 — Extraccion vectorizada con filtro MIN_PIX y sampling internos.

    Flujo:
      1. np.bincount → tamano de cada evento en pixeles (C-level, 0 bucles Python)
      2. MIN_PIX filter → descarta ruido antes de cualquier objeto Python
      3. SAMPLE_FRAC aplicado a los eventos VALIDOS (no al rango total 1..N)
      4. sampled_labeled array → shapes() itera solo sobre N_keep geometrias
         en lugar de los ~2M pixeles originales
      5. pandas groupby sobre N_keep grupos (tipicamente < 50K → < 100 MB)
      6. Batch area calculation: una sola llamada .to_crs()

    Retorna (GeoDataFrame en ba_crs, n_keep) o (None, 0).
    """
    # ── 1. Tamano de eventos via bincount (C-level, sin bucle) ─
    # event_sizes[i] = nº de pixeles del evento i (indice 0 = fondo)
    event_sizes = np.bincount(labeled_arr.ravel(), minlength=num_events + 1)

    # ── 2. MIN_PIX: filtrar eventos demasiado pequeños ─────────
    valid_ids = (np.where(event_sizes[1:] >= MIN_PIX)[0] + 1).astype(np.int32)

    if len(valid_ids) == 0:
        return None, 0

    # ── 3. SAMPLE_FRAC sobre eventos validos (no sobre el total) ─
    if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
        rng    = np.random.default_rng(seed=RANDOM_SEED + year * 100 + month)
        n_keep = max(1, int(len(valid_ids) * SAMPLE_FRAC))
        chosen = rng.choice(valid_ids, size=n_keep, replace=False)
        final_ids = np.sort(chosen).astype(np.int32)
    else:
        final_ids = valid_ids
        n_keep    = len(final_ids)

    print(f"    [{year}-{month:02d}] {num_events} eventos → "
          f"{len(valid_ids)} ≥{MIN_PIX}px → {n_keep} seleccionados")

    # ── 4. Arrays planos: solo pixeles de eventos seleccionados ─
    idx         = np.where(valid.ravel())[0]
    labels_flat = labeled_arr.ravel()[idx]
    keep        = np.isin(labels_flat, final_ids)

    if not keep.any():
        return None, n_keep

    labels_sub = labels_flat[keep]
    burn_sub   = ba_data.ravel()[idx][keep].astype(np.int32)
    dem_sub    = dem_data.ravel()[idx][keep].astype(np.float32)
    cntry_sub  = cntry_arr.ravel()[idx][keep].astype(np.int32)
    del idx, labels_flat, keep

    # ── 5. Stats vectorizados via pandas groupby (N_keep grupos) ─
    df  = pd.DataFrame({
        "label": labels_sub,
        "burn" : burn_sub,
        "dem"  : dem_sub,
        "cntry": cntry_sub,
    })
    del labels_sub, burn_sub, dem_sub, cntry_sub

    agg = (
        df.groupby("label", sort=False)
        .agg(
            BurnDate  = ("burn",  lambda x: int(x.mode().iloc[0])),
            Elevation = ("dem",   "mean"),
            ADM0_CODE = ("cntry", lambda x: int(x.mode().iloc[0])),
        )
        .reset_index()
    )
    del df

    # ── 6. Geometrias: shapes() solo sobre eventos seleccionados ─
    # Construir sampled_labeled: copia de labeled_arr con 0 en pixeles
    # no seleccionados. shapes() solo genera geometrias para N_keep eventos
    # en lugar de iterar sobre los ~2M eventos originales.
    sampled_labeled                  = np.zeros_like(labeled_arr)  # int32, 107 MB
    sampled_mask                     = np.isin(labeled_arr, final_ids)
    sampled_labeled[sampled_mask]    = labeled_arr[sampled_mask]
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

    # ── 7. Construir GeoDataFrame del mes ────────────────────
    records = []
    for _, row in agg.iterrows():
        eid   = int(row["label"])
        geoms = geom_by_label.get(eid)
        if not geoms:
            continue
        records.append({
            "geometry":  unary_union(geoms),
            "year":      year,
            "month":     month,
            "BurnDate":  int(row["BurnDate"]),
            "Elevation": round(float(row["Elevation"]), 1),
            "ADM0_CODE": int(row["ADM0_CODE"]),
        })
    del agg, geom_by_label

    if not records:
        return None, n_keep

    gdf_month = gpd.GeoDataFrame(records, crs=ba_crs)
    del records

    # ── 8. Area batch: una sola conversion CRS para el mes ───
    gdf_utm  = gdf_month.to_crs("EPSG:3857")
    areas_m2 = gdf_utm.geometry.area.values
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
                          countries_gdf, roi_geom_list, mb_meta, gpkg_path):
    """
    Procesa cada ano por separado y escribe al GPKG en modo append.
    Peak RAM = datos de un solo ano. No hay acumulacion entre anos.

    B1: lectura de una banda por mes.
    B2-B4: extract_month_events() vectorizado.
    B5: escritura por ano → del gdf_year → gc.collect().
    """
    col_order  = ["year", "month", "BurnDate", "Elevation", "Zone_Clima",
                  "pct_class12", "ADM0_CODE", "gaul0_code", "gaul0_name",
                  "area_ha", "area_km2", "geometry"]
    first_year = True

    for year, ba_path in ba_files_by_year.items():
        year_start = time.time()
        print(f"\n{'─'*60}")
        print(f"  Procesando ano {year}: {ba_path.name}")

        # ── Metadata: shape + transform (desde banda 1) ──────
        t = time.time()
        with rasterio.open(ba_path) as src:
            ba_ref, ba_transform = rasterio.mask.mask(
                src, roi_geom_list, crop=True, filled=True, nodata=0, indexes=[1]
            )
            ba_crs = src.crs
        h, w = ba_ref.shape[1], ba_ref.shape[2]
        del ba_ref
        t = timer(f"{year}: metadata BA ({h}x{w})", t)

        if elev_mask.shape != (h, w):
            print(f"  [WARN] Shape mismatch DEM {elev_mask.shape} vs BA {(h,w)}")
            continue

        # ── Rasterize paises: una sola vez por ano ────────────
        t = time.time()
        cntry_arr = rasterize_countries(
            countries_gdf.to_crs(ba_crs), (h, w), ba_transform
        )
        t = timer(f"{year}: rasterizacion paises", t)

        year_monthly_gdfs = []

        # E3: pre-alojar labeled_out como int32 UNA SOLA VEZ por ano.
        # ndimage.label escribe directamente en este array, eliminando la
        # conversion int64→int32 que de otro modo ocurre dentro del bucle.
        labeled_out = np.empty((h, w), dtype=np.int32)

        # ── B1: leer UNA banda por mes ────────────────────────
        with rasterio.open(ba_path) as src:
            for month in range(1, 13):
                ba_raw, _ = rasterio.mask.mask(
                    src, roi_geom_list, crop=True, filled=True, nodata=0,
                    indexes=[month]
                )
                ba_data = ba_raw[0].astype(np.int16)
                del ba_raw

                valid = (ba_data > 0) & elev_mask
                if not valid.any():
                    del ba_data, valid
                    continue

                # E3: label en labeled_out pre-alocado (sin conversion de tipo)
                t = time.time()
                structure = np.ones((3, 3), dtype=int)
                ndimage.label(valid, structure=structure, output=labeled_out)
                n_evt = int(labeled_out.max())
                t = timer(f"{year}-{month:02d}: etiquetado ({n_evt} eventos)", t)

                if n_evt == 0:
                    del ba_data, valid
                    continue

                # E2+sampling: MIN_PIX y SAMPLE_FRAC gestionados dentro de
                # extract_month_events (aplicados antes del groupby y shapes)
                t = time.time()
                gdf_month, n_kept = extract_month_events(
                    ba_data, labeled_out, valid, dem_data, cntry_arr,
                    ba_transform, ba_crs, year, month, n_evt
                )
                timer(f"{year}-{month:02d}: extraccion ({n_kept} eventos finales)", t)

                if gdf_month is not None:
                    year_monthly_gdfs.append(gdf_month.to_crs("EPSG:4326"))
                    del gdf_month

                del ba_data, valid
                gc.collect()

        del labeled_out

        # ── Fin loop meses — procesamiento a nivel de ano ─────
        if not year_monthly_gdfs:
            del cntry_arr
            gc.collect()
            timer(f"Ano {year} (sin eventos validos)", year_start)
            continue

        t = time.time()
        gdf_year = pd.concat(year_monthly_gdfs, ignore_index=True)
        del year_monthly_gdfs
        gc.collect()
        t = timer(f"{year}: concat mensual ({len(gdf_year)} eventos)", t)

        # MapBiomas: lee tiles pre-procesados al grid BA (~27 MB por banda)
        t = time.time()
        gdf_year["pct_class12"] = calc_mapbiomas_proportions(
            gdf_year, year
        )
        t = timer(f"{year}: MapBiomas pct_class12", t)

        # Spatial join + Zone_Clima
        t = time.time()
        gdf_year = spatial_join_3attempts(gdf_year, countries_gdf)
        gdf_year["Zone_Clima"] = assign_zone_clima(gdf_year)
        t = timer(f"{year}: spatial join + Zone_Clima", t)

        # Ordenar columnas
        cols     = [c for c in col_order if c in gdf_year.columns]
        gdf_year = gdf_year[cols]

        # ── B5: escribir al GPKG y liberar ────────────────────
        t = time.time()
        gpkg_mode = "w" if first_year else "a"
        gdf_year.to_file(gpkg_path, driver="GPKG", mode=gpkg_mode)
        first_year = False
        print(f"  [OK] GPKG ({gpkg_mode.upper()}) → {len(gdf_year)} eventos")
        timer(f"{year}: escritura GPKG", t)

        del gdf_year, cntry_arr
        gc.collect()
        timer(f"Ano {year} finalizado", year_start)

    print(f"\n{'═'*60}")


# ── Auxiliares del orquestador ─────────────────────────────────────────────────
def _preprocess_only():
    """
    Ejecuta el preprocesamiento MapBiomas sin correr el pipeline completo.
    Llamado por el orquestador ANTES de lanzar workers para evitar que
    N subprocesos llamen a gdalwarp sobre el mismo archivo simultaneamente.
    """
    t = time.time()
    print("  Paso 0: preprocesamiento MapBiomas (una sola vez)...")

    ba_files = {
        y: list((data_dir / "mosaics_BA").glob(f"*{y}*.tif"))[0]
        for y in YEARS_TEST
        if list((data_dir / "mosaics_BA").glob(f"*{y}*.tif"))
    }
    if not ba_files:
        print("  [ERROR] No se encontraron archivos BA para grid de referencia.")
        return False

    with rasterio.open(list(ba_files.values())[0]) as src:
        ba_ref, ba_tr = rasterio.mask.mask(
            src, roi_geom_list, crop=True, filled=True, nodata=0, indexes=[1]
        )
        ba_crs = src.crs
    ba_shape = (ba_ref.shape[1], ba_ref.shape[2])
    del ba_ref

    mb_meta = get_mapbiomas_metadata()
    preprocess_mapbiomas(mb_meta, ba_tr, ba_crs, ba_shape)
    timer("Preprocesamiento MapBiomas listo", t)
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
        (y, test_dir / f"BurnedAreas_MODIS_V5_{run_tag}_{y}.gpkg")
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
    gdf  = pd.concat(gdfs, ignore_index=True)
    del gdfs
    gc.collect()

    base     = f"BurnedAreas_MODIS_V5_{run_tag}"
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
    print(f"  INICIO - BurnedAreas_MODIS v5.4.0{year_label}")
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
    t = time.time()
    ba_files_by_year = {
        year: list((data_dir / "mosaics_BA").glob(f"*{year}*.tif"))[0]
        for year in YEARS_TEST
        if list((data_dir / "mosaics_BA").glob(f"*{year}*.tif"))
    }
    print(f"  Archivos BA encontrados: {list(ba_files_by_year.keys())}")
    t = timer("Busqueda archivos BA", t)
    if not ba_files_by_year:
        print("  [ERROR] No se encontraron archivos BA.")
        return None

    # -- 3. Grid de referencia ------------------------------------------------
    t = time.time()
    ref_ba_path = list(ba_files_by_year.values())[0]
    with rasterio.open(ref_ba_path) as src:
        ba_ref, ba_ref_transform = rasterio.mask.mask(
            src, roi_geom_list, crop=True, filled=True, nodata=0, indexes=[1]
        )
        ba_ref_crs = src.crs
    ref_shape = (ba_ref.shape[1], ba_ref.shape[2])
    del ba_ref
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

    # -- 5. MapBiomas: metadatos + preprocesamiento (solo primera vez) ---------
    t = time.time()
    mb_meta = get_mapbiomas_metadata()
    print(f"  Tiles MapBiomas originales : {len(mb_meta)}/9")
    t = timer("Metadatos MapBiomas", t)

    # preprocess_mapbiomas crea los tiles a resolucion BA (gdalwarp subproceso).
    # Primera ejecucion: varios minutos por tile; las siguientes son instantaneas.
    t = time.time()
    preprocess_mapbiomas(mb_meta, ba_ref_transform, ba_ref_crs, ref_shape)
    t = timer("Preprocesamiento MapBiomas (gdalwarp)", t)

    # -- Rutas de salida ------------------------------------------------------
    run_tag   = "full" if (SAMPLE_FRAC is None or SAMPLE_FRAC >= 1.0) \
                else f"test{int(SAMPLE_FRAC * 100):02d}pct"

    # En modo worker el GPKG incluye el ano para que N workers no colisionen
    if worker_mode:
        base_name = f"BurnedAreas_MODIS_V5_{run_tag}_{YEARS_TEST[0]}"
    else:
        base_name = f"BurnedAreas_MODIS_V5_{run_tag}"

    os.makedirs(output_dir, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = test_dir   / f"{base_name}.gpkg"
    shp_path  = output_dir / f"{base_name}.shp"
    csv_path  = output_dir / f"{base_name}.csv"

    # -- 6. Procesamiento: escribe al GPKG ano a ano (B5) ---------------------
    t = time.time()
    process_burned_areas(
        ba_files_by_year, dem_data, elev_mask,
        countries_gdf, roi_geom_list, mb_meta, gpkg_path
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
        description="BurnedAreas_MODIS pipeline v5.4.0"
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
        run_tag = "full" if (SAMPLE_FRAC is None or SAMPLE_FRAC >= 1.0) \
                  else f"test{int(SAMPLE_FRAC * 100):02d}pct"

        n_workers = min(len(YEARS_TEST), N_WORKERS)
        print(f"\n{'='*58}")
        print(f"  ORQUESTADOR — BurnedAreas_MODIS v5.4.0")
        print(f"  Anos    : {YEARS_TEST}")
        print(f"  Workers : {n_workers} en paralelo")
        print(f"  Modo    : {run_tag}")
        print(f"{'='*58}")

        # Paso 0: preprocesamiento MapBiomas (solo 1 vez, antes de los workers)
        # Si cada worker lo intentara en paralelo, N gdalwarp correran sobre el
        # mismo archivo destino simultaneamente → corrupcion de archivos.
        if not _preprocess_only():
            sys.exit(1)

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
            futures = {exe.submit(_run_worker, y): y for y in YEARS_TEST}
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