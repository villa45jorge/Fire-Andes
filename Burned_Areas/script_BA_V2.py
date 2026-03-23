# -*- coding: utf-8 -*-
"""
Modified on 20/03/2026
Version 3.0.0
@author: jvilla


MODIFICATIONS:




"""

import os
import numpy as np
import rasterio
import rasterio.mask
import rasterio.features
import geopandas as gpd
import pandas as pd
from shapely.geometry import box, shape, mapping
from pathlib import Path
import rasterio.warp
from scipy import ndimage
from collections import Counter
import time
from contextlib import contextmanager

# Definir rutas
base_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD64A1")
data_dir = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir = base_dir / "3_output"
test_dir = base_dir / "4_test"

'''BA MODIS  RASTER(raw-ANNUEL)
    ↓ muestra (subset de zone d'etude', plusieurs années)
    ↓ extration de type de wordcover (raster) a l'interiuer du Burned area'
    ↓ extration de elevation (raster) a l'interiuer du Burned area
    ↓ extration de pays (shapefile) a qui contient le Burned area
    → Shapefile final_stats
'''   


@contextmanager
def timer(label):
    """Context manager para medir tiempos de ejecución."""
    start = time.perf_counter()
    print(f"  ⏱  [{label}] iniciando...")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        print(f"  ✅ [{label}] completado en {elapsed:.2f}s")

ROI_BBOX        = (-74.0, -19.0, -66.0, -11.0)
YEARS_TEST      = [2003,2005,2012,2015,2020, 2024]
ELEV_THRESHOLD  = 2000
COUNTRIES_ADM0  = [178, 184, 185, 190, 207]

roi_geom = box(*ROI_BBOX)
roi_geom_list = [mapping(roi_geom)]

# ── 1. Carga y filtrado del shapefile de países ────────────────────────────────
def load_countries(path, adm0_codes, roi_geom):
    with timer("load_countries: lectura shapefile"):
        pays = gpd.read_file(path)

    with timer("load_countries: filtrado y clip"):
        pays = pays[pays["gaul0_code"].isin(adm0_codes)].copy()
        pays = pays[pays.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        pays = pays.clip(roi_geom)
        pays = pays.to_crs("EPSG:4326")

    return pays.reset_index(drop=True)


# ── 2. Máscara de elevación ────────────────────────────────────────────────────
def load_elevation_mask(dem_path, roi_bbox, threshold=2000):
    """
    Usa windowed reading para leer solo los píxeles del ROI.
    roi_bbox: (xmin, ymin, xmax, ymax)
    """
    from rasterio.windows import from_bounds

    with timer("load_elevation_mask: lectura con window"):
        with rasterio.open(dem_path) as src:
            window = from_bounds(
                left      = roi_bbox[0],
                bottom    = roi_bbox[1],
                right     = roi_bbox[2],
                top       = roi_bbox[3],
                transform = src.transform
            )
            dem_data     = src.read(1, window=window).astype(float)
            dem_transform = src.window_transform(window)
            dem_meta     = src.meta.copy()
            dem_meta.update({
                "height"    : dem_data.shape[0],
                "width"     : dem_data.shape[1],
                "transform" : dem_transform,
                "dtype"     : "float32"
            })

    with timer("load_elevation_mask: cálculo máscara"):
        dem_data[dem_data == src.nodata] = np.nan
        elev_mask = dem_data >= threshold

    return dem_data, elev_mask, dem_meta

# ── 3. Carga WorldCover ────────────────────────────────────────────────────────
def load_worldcover(wc_path, roi_geom_list, roi_bbox):
    """
    Usa windowed reading para leer solo los píxeles del ROI,
    sin cargar el raster completo en memoria.
    roi_bbox: (xmin, ymin, xmax, ymax)
    """
    from rasterio.windows import from_bounds

    with timer("load_worldcover: lectura con window"):
        with rasterio.open(wc_path) as src:
            window = from_bounds(
                left   = roi_bbox[0],
                bottom = roi_bbox[1],
                right  = roi_bbox[2],
                top    = roi_bbox[3],
                transform = src.transform
            )
            wc_data     = src.read(1, window=window).astype(np.uint8)
            wc_transform = src.window_transform(window)

    return wc_data, wc_transform


def resample_worldcover(wc_data, wc_src_transform, target_shape, target_transform):
    """Reproyecta el array ya cargado a la resolución del BA."""
    with timer("load_worldcover: reproyección"):
        wc_resampled = np.empty(target_shape, dtype=np.uint8)
        rasterio.warp.reproject(
            source=wc_data,
            destination=wc_resampled,
            src_transform=wc_src_transform,   # ← transform ORIGINAL del WC
            src_crs="EPSG:4326",
            dst_transform=target_transform,
            dst_crs="EPSG:4326",
            resampling=rasterio.warp.Resampling.nearest
        )
    return wc_resampled


# ── 4. Rasterizar países ───────────────────────────────────────────────────────
def rasterize_countries(countries_gdf, target_shape, target_transform, target_crs):
    with timer("rasterize_countries"):
        shapes_iter = (
            (mapping(geom), int(code))
            for geom, code in zip(countries_gdf.geometry, countries_gdf["gaul0_code"])
        )
        country_raster = rasterio.features.rasterize(
            shapes=shapes_iter,
            out_shape=target_shape,
            transform=target_transform,
            fill=0,
            dtype=np.int32
        )
    return country_raster


# ── 5. Procesamiento principal por año ────────────────────────────────────────
def process_burned_areas(ba_files_by_year, dem_data, elev_mask,
                          wc_data, wc_transform,          # ← array ya cargado
                          countries_gdf, roi_geom_list):
    all_features = []
    _dem_cache = {}   # {(h, w): (elev_mask_r, dem_r)}
    
    pipeline_start = time.perf_counter()

    for year, ba_path in ba_files_by_year.items():
        year_start = time.perf_counter()
        print(f"\n{'─'*60}")
        print(f"  📅 Procesando año {year}: {ba_path.name}")
        
        
        with timer(f"{year}: lectura BA"):
            with rasterio.open(ba_path) as src:
                ba_data, ba_transform = rasterio.mask.mask(
                    src, roi_geom_list, crop=True, filled=True, nodata=0
                )
                ba_crs = src.crs
                ba_meta = src.meta.copy()

            ba_data = ba_data[0].astype(np.int16)
            h, w = ba_data.shape
            ba_meta.update({"height": h, "width": w, "transform": ba_transform})

        h, w = ba_data.shape
        with timer(f"{year}: resample DEM"):
                    if (h, w) not in _dem_cache:
                        print(f"    → DEM resample necesario para shape {(h,w)}")
                        if elev_mask.shape != (h, w):
                            from skimage.transform import resize as sk_resize
                            elev_mask_r = sk_resize(elev_mask.astype(float), (h, w), order=0).astype(bool)
                            dem_r = sk_resize(dem_data, (h, w), order=1)
                        else:
                            elev_mask_r = elev_mask
                            dem_r = dem_data
                        _dem_cache[(h, w)] = (elev_mask_r, dem_r)
                    else:
                        print(f"    → DEM resample desde caché para shape {(h,w)}")
                        elev_mask_r, dem_r = _dem_cache[(h, w)]

        valid = (ba_data > 0) & elev_mask_r
        if not valid.any():
            print(f"    ⚠️  Sin áreas quemadas válidas en {year}")
            continue

        with timer(f"{year}: carga WorldCover"):
            wc_arr = resample_worldcover(wc_data, wc_transform, (h, w), ba_transform)

        with timer(f"{year}: rasterización países"):
            cntry_arr = rasterize_countries(
                countries_gdf.to_crs(ba_crs), (h, w), ba_transform, ba_crs
            )

        with timer(f"{year}: etiquetado eventos (label)"):
            structure = np.ones((3, 3), dtype=int)
            labeled_arr, num_events = ndimage.label(valid, structure=structure)
        print(f"    🔥 Eventos detectados: {num_events}")

        with timer(f"{year}: zonal stats ({num_events} eventos)"):
            for event_id in range(1, num_events + 1):
                event_mask = labeled_arr == event_id

                burn_vals  = ba_data[event_mask]
                burn_date  = int(Counter(burn_vals.tolist()).most_common(1)[0][0])

                dem_vals   = dem_r[event_mask]
                dem_vals   = dem_vals[~np.isnan(dem_vals)]
                elev_mean  = float(np.mean(dem_vals)) if len(dem_vals) > 0 else np.nan

                wc_vals    = wc_arr[event_mask]
                wc_mode    = int(Counter(wc_vals.tolist()).most_common(1)[0][0])

                cntry_vals = cntry_arr[event_mask]
                cntry_mode = int(Counter(cntry_vals.tolist()).most_common(1)[0][0])

                geom_list = [
                    shape(g)
                    for g, v in rasterio.features.shapes(
                        event_mask.astype(np.uint8),
                        mask=event_mask.astype(np.uint8),
                        transform=ba_transform
                    )
                    if v == 1
                ]
                if not geom_list:
                    continue

                from shapely.ops import unary_union
                geom_union = unary_union(geom_list)

                geom_utm = gpd.GeoSeries([geom_union], crs=ba_crs).to_crs("EPSG:3857")
                area_m2  = float(geom_utm.area.iloc[0])
                area_ha  = round(area_m2 / 10_000, 2)
                area_km2 = round(area_m2 / 1_000_000, 4)

                all_features.append({
                    "geometry":   geom_union,
                    "year":       year,
                    "BurnDate":   burn_date,
                    "Elevation":  round(elev_mean, 1),
                    "WorldCover": wc_mode,
                    "ADM0_CODE":  cntry_mode,
                    "area_ha":    area_ha,
                    "area_km2":   area_km2,
                })

        year_elapsed = time.perf_counter() - year_start
        print(f"  🏁 Año {year} finalizado en {year_elapsed:.2f}s")

    print(f"\n{'═'*60}")

    if not all_features:
        return gpd.GeoDataFrame()

    with timer("spatial join países"):
        gdf = gpd.GeoDataFrame(all_features, crs=ba_crs)
        gdf = gdf.sjoin(
            countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
            how="left", predicate="within"
        ).drop(columns=["index_right"], errors="ignore")

    total_elapsed = time.perf_counter() - pipeline_start
    print(f"  🕐 Tiempo total process_burned_areas: {total_elapsed:.2f}s")
    return gdf


# ── 6. Orquestador principal ───────────────────────────────────────────────────
def run_pipeline():
    pipeline_start = time.perf_counter()
    print(f"\n{'═'*60}")
    print("🚀 Iniciando pipeline")
    print(f"{'═'*60}")

    with timer("carga países"):
        countries_gdf = load_countries(data_dir / 'GAUL_2024_L1.shp',
                                       COUNTRIES_ADM0, roi_geom)

    with timer("carga elevación"):
        dem_data, elev_mask, dem_meta = load_elevation_mask(
            processed_dir / 'mosaico_andes_DEM_COG.tif', ROI_BBOX, ELEV_THRESHOLD 
        )

    with timer("búsqueda archivos BA"):
        ba_files_by_year = {
            year: list((data_dir/'mosaics_BA').glob(f"*{year}*.tif"))[0]
            for year in YEARS_TEST
            if list((data_dir/'mosaics_BA').glob(f"*{year}*.tif"))
        }
    print(f"  📂 Archivos BA encontrados: {list(ba_files_by_year.keys())}")

    with timer("carga WorldCover"):
        wc_data, wc_transform = load_worldcover(
            processed_dir / 'mosaico_andes_WC_COG.tif',
            roi_geom_list,
            roi_bbox=ROI_BBOX       # ← (-74, -19, -66, -11) para T1
        )

    with timer("procesamiento áreas quemadas"):
        gdf_final = process_burned_areas(
            ba_files_by_year, dem_data, elev_mask,
            wc_data, wc_transform,                     # ← pasar array
            countries_gdf, roi_geom_list
        )

    if gdf_final.empty:
        print("⚠️  Sin resultados. Verifica los datos de entrada.")
        return

    with timer("escritura GeoPackage"):
        test_dir.mkdir(parents=True, exist_ok=True)
        gdf_final.to_file(test_dir / "burned_areas_final_0.gpkg", driver="GPKG")

    total = time.perf_counter() - pipeline_start
    print(f"\n{'═'*60}")
    print(f"✅ Pipeline completo — {len(gdf_final)} features guardados")
    print(f"🕐 Tiempo total pipeline: {total:.2f}s")
    print(f"{'═'*60}\n")
    return gdf_final


if __name__ == "__main__":
    gdf = run_pipeline()
    if gdf is not None:
        print(gdf[["year", "BurnDate", "Elevation", "WorldCover", "area_ha", "area_km2"]].head())