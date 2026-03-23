# -*- coding: utf-8 -*-
"""
Modified on 20/03/2026
Version 1.0.0
@author: jvilla

MODIFICATIONS:
    -base_dir to change
    -PROCCESS TIME


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

# Definir rutas
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


# ── configuración ──────────────────────────────────────────────────────────────
ROI_BBOX        = (-72.0, -17.0, -68.0, -13.0)
YEARS_TEST      = list(range(2000,2010))
ELEV_THRESHOLD  = 2000
COUNTRIES_ADM0  = [178, 184, 185, 190, 207]

#DIR_MCD64   = Path("data/MCD64A1")          # un GeoTIFF/HDF por año o por mes
#PATH_DEM    = Path("data/dem/srtm_30m.tif")
#PATH_WC     = Path("data/worldcover/ESA_WC_10m.tif")
#PATH_PAYS   = Path("data/countries/gaul_countries.shp")
#PATH_OUTPUT = Path("output/burned_areas_final.gpkg")

roi_geom = box(*ROI_BBOX)
roi_geom_list = [mapping(roi_geom)]

# ── 1. Carga y filtrado del shapefile de países ────────────────────────────────
def load_countries(path, adm0_codes, roi_geom):
    """Equivalente a country() en GEE."""
    pays = gpd.read_file(path)
    pays = pays[pays["gaul0_code"].isin(adm0_codes)].copy()
    # quedarse solo con Polygon (descartar MultiPoint, etc.)
    pays = pays[pays.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    pays = pays.clip(roi_geom)
    pays = pays.to_crs("EPSG:4326")
    return pays.reset_index(drop=True)




# ── 2. Máscara de elevación ────────────────────────────────────────────────────
def load_elevation_mask(dem_path, roi_geom_list, threshold=2000):
    """Equivalente a elevation() en GEE. Retorna máscara booleana + metadata."""
    with rasterio.open(dem_path) as src:
        dem_data, dem_transform = rasterio.mask.mask(
            src, roi_geom_list, crop=True, filled=True, nodata=-9999
        )
        dem_meta = src.meta.copy()
    dem_data = dem_data[0].astype(float)
    dem_data[dem_data == -9999] = np.nan
    elev_mask = dem_data >= threshold   # True donde elevación >= 2000 m
    dem_meta.update({"height": dem_data.shape[0], "width": dem_data.shape[1],
                     "transform": dem_transform, "dtype": "float32"})
    return dem_data, elev_mask, dem_meta


# ── 3. Carga WorldCover ────────────────────────────────────────────────────────
def load_worldcover(wc_path, roi_geom_list, target_shape, target_transform):
    """
    Extrae WorldCover dentro del ROI y lo remuestrea
    a la resolución del BA (equivalente al addBands de GEE).
    """
    with rasterio.open(wc_path) as src:
        wc_data, _ = rasterio.mask.mask(
            src, roi_geom_list, crop=True, filled=True, nodata=0
        )
    wc_data = wc_data[0].astype(np.uint8)
    # remuestrear a la forma objetivo del BA
    wc_resampled = np.empty(target_shape, dtype=np.uint8)
    rasterio.warp.reproject(
        source=wc_data,
        destination=wc_resampled,
        src_transform=target_transform,      # se ajusta al llamar la función
        src_crs="EPSG:4326",
        dst_transform=target_transform,
        dst_crs="EPSG:4326",
        resampling=rasterio.warp.Resampling.nearest
    )
    return wc_resampled

# ── 4. Rasterizar países (equivalente a reduceToImage) ─────────────────────────
def rasterize_countries(countries_gdf, target_shape, target_transform, target_crs):
    """Convierte el GeoDataFrame de países en un raster de gaul0_code."""
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
def process_burned_areas(ba_files_by_year, dem_data, elev_mask, wc_path,
                          countries_gdf, roi_geom_list):
    all_features = []

    for year, ba_path in ba_files_by_year.items():
        print(f"  Procesando año {year}: {ba_path.name}")
        with rasterio.open(ba_path) as src:
            ba_data, ba_transform = rasterio.mask.mask(
                src, roi_geom_list, crop=True, filled=True, nodata=0
            )
            ba_crs = src.crs
            ba_meta = src.meta.copy()

        ba_data = ba_data[0].astype(np.int16)
        h, w = ba_data.shape
        ba_meta.update({"height": h, "width": w, "transform": ba_transform})

        # ── resample DEM/máscara si es necesario ──────────────────────────────
        if elev_mask.shape != (h, w):
            from skimage.transform import resize as sk_resize
            elev_mask_r = sk_resize(elev_mask.astype(float), (h, w), order=0).astype(bool)
            dem_r = sk_resize(dem_data, (h, w), order=1)
        else:
            elev_mask_r = elev_mask
            dem_r = dem_data

        # ── píxeles quemados válidos ───────────────────────────────────────────
        valid = (ba_data > 0) & elev_mask_r
        if not valid.any():
            print(f"    Sin áreas quemadas válidas en {year}")
            continue

        # ── WorldCover y países a resolución BA ───────────────────────────────
        wc_arr   = load_worldcover(wc_path, roi_geom_list, (h, w), ba_transform)
        cntry_arr = rasterize_countries(
            countries_gdf.to_crs(ba_crs), (h, w), ba_transform, ba_crs
        )

        # ── etiquetar eventos conectados (cada blob = un evento) ──────────────
        structure = np.ones((3, 3), dtype=int)   # conectividad 8-vecinos
        labeled_arr, num_events = ndimage.label(valid, structure=structure)
        print(f"    Eventos detectados: {num_events}")

        # ── zonal stats por evento ─────────────────────────────────────────────
        for event_id in range(1, num_events + 1):
            event_mask = labeled_arr == event_id          # píxeles del evento

            # BurnDate: moda (día más frecuente dentro del evento)
            burn_vals  = ba_data[event_mask]
            burn_date  = int(Counter(burn_vals.tolist()).most_common(1)[0][0])

            # Elevation: media de los píxeles válidos del evento
            dem_vals   = dem_r[event_mask]
            dem_vals   = dem_vals[~np.isnan(dem_vals)]
            elev_mean  = float(np.mean(dem_vals)) if len(dem_vals) > 0 else np.nan

            # WorldCover: moda (clase más frecuente dentro del evento)
            wc_vals    = wc_arr[event_mask]
            wc_mode    = int(Counter(wc_vals.tolist()).most_common(1)[0][0])

            # País: moda del raster de países
            cntry_vals = cntry_arr[event_mask]
            cntry_mode = int(Counter(cntry_vals.tolist()).most_common(1)[0][0])

            # Geometría: vectorizar solo los píxeles de este evento
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

            # unir todos los polígonos del evento en uno solo
            from shapely.ops import unary_union
            geom_union = unary_union(geom_list)
            
            geom_utm = gpd.GeoSeries([geom_union], crs=ba_crs).to_crs("EPSG:3857")  # ajusta zona UTM
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
                "area_ha":    area_ha,       # ← nuevo
                "area_km2":   area_km2,      # ← nuevo
            })

    if not all_features:
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame(all_features, crs=ba_crs)

    gdf = gdf.sjoin(
        countries_gdf[["gaul0_code", "gaul0_name", "geometry"]],
        how="left", predicate="within"
    ).drop(columns=["index_right"], errors="ignore")

    return gdf

# ── 6. Orquestador principal ───────────────────────────────────────────────────
def run_pipeline():

    print("Cargando países...")
    countries_gdf = load_countries(data_dir / 'GAUL_2024_L1.shp', 
                                   COUNTRIES_ADM0, roi_geom)
    
    print("Cargando elevación...")
    dem_data, elev_mask, dem_meta = load_elevation_mask(data_dir / 'mosaico_andes_DEM.tif', 
                                                        roi_geom_list, ELEV_THRESHOLD)
    
    # mapear archivos BA disponibles a los años del test
    ba_files_by_year = {
        year: list((data_dir/'mosaics_BA').glob(f"*{year}*.tif"))[0]
        for year in YEARS_TEST
        if list((data_dir/'mosaics_BA').glob(f"*{year}*.tif"))
    }

    print(f"Archivos BA encontrados: {list(ba_files_by_year.keys())}")    
    
    print("Procesando áreas quemadas...")
    gdf_final = process_burned_areas(
        ba_files_by_year, dem_data, elev_mask,
        data_dir / 'mosaico_andes_WC.tif', 
        countries_gdf, roi_geom_list
    )
    
    if gdf_final.empty:
        print("Sin resultados. Verifica los datos de entrada.")
        return

    print(f"Guardando {len(gdf_final)} features en {test_dir}...")
    test_dir.mkdir(parents=True, exist_ok=True)
    gdf_final.to_file(test_dir / "burned_areas_final_1.gpkg", driver="GPKG")
    print("¡Listo!")
    return gdf_final

    print("WOrk DONe!")

if __name__ == "__main__":
    gdf = run_pipeline()
    if gdf is not None:
        print(gdf[["year", "BurnDate", "Elevation", "WorldCover", "area_ha", "area_km2"]].head())
