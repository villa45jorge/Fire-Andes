# -*- coding: utf-8 -*-
"""
Modified on 27/03/2026
Version 4.0.0
@author: jvilla


MODIFICATIONS:
    -test performing cluster calculs
    -all data but 1 year



"""

#import os
import numpy as np
import rasterio
import rasterio.mask
import rasterio.features
import geopandas as gpd
#import pandas as pd
from shapely.geometry import box, shape, mapping
from pathlib import Path
import rasterio.warp
from scipy import ndimage
from collections import Counter
import time
from contextlib import contextmanager

# Definir rutas
#base_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD64A1")
base_dir = Path("/media/villaramos/Donnees/MesProgrammes/data/MCD64A1")
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

ROI_BBOX        = (-80.0, -20.0, -60.0, 1.0)
YEARS_TEST      = [2012]
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
def load_elevation_mask(dem_path, target_shape, target_transform, target_crs, threshold=2000):
    """
    Reprojecta el DEM directamente al grid del BA usando GDAL,
    sin cargar el raster completo en memoria.
    """
    with timer("load_elevation_mask: reproject al grid BA"):
        dem_r = np.empty(target_shape, dtype=np.float32)

        with rasterio.open(dem_path) as src:
            rasterio.warp.reproject(
                source=rasterio.band(src, 1),   # ← lectura lazy por GDAL
                destination=dem_r,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target_transform,
                dst_crs=target_crs,
                resampling=rasterio.warp.Resampling.bilinear,
                src_nodata=src.nodata,
                dst_nodata=np.nan
            )

    with timer("load_elevation_mask: cálculo máscara"):
        elev_mask = dem_r >= threshold   # NaN >= 2000 → False ✓

    return dem_r, elev_mask
# ── 3. Carga WorldCover ────────────────────────────────────────────────────────
def load_worldcover(wc_path, target_shape, target_transform, target_crs):
    with timer("load_worldcover: reproject al grid BA"):
        wc_r = np.empty(target_shape, dtype=np.uint8)

        with rasterio.open(wc_path) as src:
            rasterio.warp.reproject(
                source=rasterio.band(src, 1),
                destination=wc_r,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target_transform,
                dst_crs=target_crs,
                resampling=rasterio.warp.Resampling.nearest,
                src_nodata=src.nodata,
                dst_nodata=0
            )
    return wc_r


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
                          wc_data,
                          countries_gdf, roi_geom_list):
    all_features = []
    pipeline_start = time.perf_counter()

    for year, ba_path in ba_files_by_year.items():
        year_start = time.perf_counter()
        print(f"\n{'─'*60}")
        print(f"  📅 Procesando año {year}: {ba_path.name}")

        # ── Leer TODAS las bandas de una vez ──────────────────────
        with timer(f"{year}: lectura BA (12 bandas)"):
            with rasterio.open(ba_path) as src:
                ba_all, ba_transform = rasterio.mask.mask(
                    src, roi_geom_list, crop=True, filled=True, nodata=0
                )
                ba_crs  = src.crs
                ba_meta = src.meta.copy()
            # ba_all shape: (12, h, w)
            h, w = ba_all.shape[1], ba_all.shape[2]
            ba_meta.update({"height": h, "width": w, "transform": ba_transform})

        # ── Validación shape DEM ───────────────────────────────────
        if elev_mask.shape != (h, w):
            print(f"    ⚠️  Shape mismatch DEM {elev_mask.shape} vs BA {(h,w)}")
            continue

        # ── Iterar por mes (banda) ─────────────────────────────────
        for month in range(1, 13):
            ba_data = ba_all[month - 1].astype(np.int16)  # banda del mes

            valid = (ba_data > 0) & elev_mask
            if not valid.any():
                continue  # sin quemas este mes, pasar al siguiente

            print(f"    📆 Mes {month:02d} — píxeles válidos: {valid.sum()}")

            wc_arr    = wc_data
            dem_r     = dem_data

            with timer(f"{year}-{month:02d}: rasterización países"):
                cntry_arr = rasterize_countries(
                    countries_gdf.to_crs(ba_crs), (h, w), ba_transform, ba_crs
                )

            with timer(f"{year}-{month:02d}: etiquetado eventos"):
                structure   = np.ones((3, 3), dtype=int)
                labeled_arr, num_events = ndimage.label(valid, structure=structure)
            print(f"    🔥 Mes {month:02d} — eventos detectados: {num_events}")

            with timer(f"{year}-{month:02d}: zonal stats ({num_events} eventos)"):
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
                        "month":      month,        # ← nuevo
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

    # ── 1. Buscar archivos BA primero ──────────────────────────────
    with timer("búsqueda archivos BA"):
        ba_files_by_year = {
            year: list((data_dir/'mosaics_BA').glob(f"*{year}*.tif"))[0]
            for year in YEARS_TEST
            if list((data_dir/'mosaics_BA').glob(f"*{year}*.tif"))
        }
    print(f"  📂 Archivos BA encontrados: {list(ba_files_by_year.keys())}")

    # ── 2. Leer grid de referencia del primer BA ───────────────────
    with timer("lectura grid de referencia BA"):
        ref_ba_path = list(ba_files_by_year.values())[0]
        with rasterio.open(ref_ba_path) as src:
            ba_ref_data, ba_ref_transform = rasterio.mask.mask(
                src, roi_geom_list, crop=True, filled=True, nodata=0
            )
            ba_ref_crs = src.crs
        ref_shape = (ba_ref_data.shape[1], ba_ref_data.shape[2])
        print(f"  📐 Grid de referencia: shape={ref_shape}, crs={ba_ref_crs}")

    # ── 3. DEM y WorldCover ya al tamaño del BA ────────────────────
    with timer("carga elevación"):
        dem_data, elev_mask = load_elevation_mask(
            processed_dir / 'mosaico_andes_DEM_COG.tif',
            ref_shape,
            ba_ref_transform,
            ba_ref_crs,
            ELEV_THRESHOLD
        )

    with timer("carga WorldCover"):
        wc_data = load_worldcover(
            processed_dir / 'mosaico_andes_WC_COG.tif',
            ref_shape,
            ba_ref_transform,
            ba_ref_crs
        )

    # ── 4. Procesamiento ───────────────────────────────────────────
    with timer("procesamiento áreas quemadas"):
        gdf_final = process_burned_areas(
            ba_files_by_year, dem_data, elev_mask,
            wc_data,                    # ← sin wc_transform
            countries_gdf, roi_geom_list
        )

    if gdf_final.empty:
        print("⚠️  Sin resultados. Verifica los datos de entrada.")
        return

    with timer("escritura GeoPackage"):
        test_dir.mkdir(parents=True, exist_ok=True)
        gdf_final.to_file(test_dir / "burned_areas_final_2.gpkg", driver="GPKG")

    total = time.perf_counter() - pipeline_start
    print(f"\n{'═'*60}")
    print(f"✅ Pipeline completo — {len(gdf_final)} features guardados")
    print(f"🕐 Tiempo total pipeline: {total:.2f}s")
    print(f"{'═'*60}\n")
    return gdf_final


if __name__ == "__main__":
    gdf = run_pipeline()
    if gdf is not None:
        print(gdf[["year", "month", "BurnDate", "Elevation", "WorldCover", "area_ha", "area_km2"]].head())