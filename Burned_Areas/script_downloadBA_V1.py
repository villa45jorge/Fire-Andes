# -*- coding: utf-8 -*-
"""
Created on 03/07/2026

@author: villaramos

Version 1.1.0
"""
"""
Descarga MCD64A1 con grid de píxeles consistente
─────────────────────────────────────────────────
Fix principal: se calcula el número exacto de píxeles esperado para cada tile
y se fuerza con 'dimensions' en getDownloadURL(), en lugar de depender de 'scale'.
Esto garantiza que tiles de borde (con extent < tile_size) tengan el tamaño
correcto y sean consistentes con el DEM u otros rasters de referencia.
"""

import os
import math
import calendar
import numpy as np
import requests
import ee
from pathlib import Path


ee.Authenticate()
ee.Initialize(project="ee-villa45ramos")

modis_ba = ee.ImageCollection('MODIS/061/MCD64A1')

base_dir = Path("/media/villaramos/Donnees/MesProgrammes/MCD64A1")
output_dir = base_dir / "data/raw/Modis_BurnedAreas_v2"
Path(output_dir).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# CONSTANTE GLOBAL: metros por grado a escala 500m de MODIS
# ─────────────────────────────────────────────────────────────
# MODIS usa una grilla sinusoidal interna, pero al exportar en EPSG:4326
# GEE usa scale=500m → ~0.004491576420597609° por píxel (valor exacto de MODIS)
MODIS_DEG_PER_PIXEL = 500 / 111320  # ≈ 0.004492°  (aproximación suficiente)
# Si prefieres el valor exacto de la grilla MODIS:
# MODIS_DEG_PER_PIXEL = 0.004491576420597609


def compute_tile_dimensions(lat_min, lat_max, lon_min, lon_max):
    """
    Calcula las dimensiones exactas (width, height) en píxeles para un extent dado,
    usando la resolución de MODIS (~500m = 0.004492°/píx en EPSG:4326).
    
    Devuelve (width, height) como enteros, redondeando al píxel más cercano.
    """
    lat_range = lat_max - lat_min
    lon_range = lon_max - lon_min
    height = max(1, round(lat_range / MODIS_DEG_PER_PIXEL))
    width  = max(1, round(lon_range / MODIS_DEG_PER_PIXEL))
    return width, height


def download_tile(lat_min, lat_max, lon_min, lon_max, tile_name, output_dir, year, month):
    """
    Descarga una tile de MCD64A1 forzando las dimensiones exactas en píxeles.
    
    En lugar de usar 'scale' (que deja a GEE decidir el número de píxeles),
    usamos 'dimensions' (WxH explícito) para garantizar consistencia entre tiles.
    """
    region = ee.Geometry.Rectangle([
        float(lon_min), float(lat_min),
        float(lon_max), float(lat_max)
    ])

    start_date = f'{year}-{month:02d}-01'
    last_day   = calendar.monthrange(year, month)[1]
    end_date   = f'{year}-{month:02d}-{last_day}'

    collection = (modis_ba
                  .filterDate(start_date, end_date)
                  .filterBounds(region))

    if collection.size().getInfo() == 0:
        print(f"Sin datos: {tile_name}_{year}_{month:02d}")
        return None

    monthly_ba = collection.first().clip(region)

    output_file = os.path.join(output_dir, f'{tile_name}_{year}_{month:02d}.tif')
    if os.path.exists(output_file):
        print(f"Ya existe: {tile_name}_{year}_{month:02d}")
        return output_file

    # ── FIX CLAVE: calcular dimensiones exactas ──────────────────────────────
    width, height = compute_tile_dimensions(lat_min, lat_max, lon_min, lon_max)
    # ─────────────────────────────────────────────────────────────────────────

    try:
        url = monthly_ba.getDownloadURL({
            'bands':      ['BurnDate', 'Uncertainty', 'QA'],
            'crs':        'EPSG:4326',
            'region':     region,
            'format':     'GEO_TIFF',
            # 'scale': 500   ← REMOVIDO: causaba inconsistencia en tiles de borde
            'dimensions': f'{width}x{height}',  # ← FIX: dimensiones explícitas
        })

        response = requests.get(url, stream=True, timeout=300)

        if response.status_code == 200:
            with open(output_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            size_mb = os.path.getsize(output_file) / (1024 ** 2)
            print(f"  ✓ {tile_name}_{year}_{month:02d}: {size_mb:.1f} MB  [{width}x{height} px]")
            return output_file
        else:
            print(f"  ✗ HTTP {response.status_code}: {tile_name}")
            return None

    except Exception as e:
        print(f"  ✗ Error en {tile_name}: {str(e)[:80]}")
        return None


def download_region_with_fixed_tiles(region_name, bounds, output_dir, tile_size, year, month):
    """
    Descarga una región con tiles de tamaño fijo.
    Las tiles de borde tienen un extent menor, pero ahora sus dimensiones
    en píxeles son proporcionales y calculadas explícitamente → no hay mismatch.
    """
    lon_min, lat_min, lon_max, lat_max = bounds

    print(f"\n{'='*70}")
    print(f"REGIÓN: {region_name}  |  {year}-{month:02d}")
    print(f"{'='*70}")
    print(f"Bounds: Lat [{lat_min}, {lat_max}]  Lon [{lon_min}, {lon_max}]")

    lat_steps   = np.arange(lat_min, lat_max, tile_size)
    lon_steps   = np.arange(lon_min, lon_max, tile_size)
    total_tiles = len(lat_steps) * len(lon_steps)

    print(f"Grid: {len(lat_steps)} lat × {len(lon_steps)} lon = {total_tiles} tiles")

    region_dir = os.path.join(output_dir, region_name)
    Path(region_dir).mkdir(parents=True, exist_ok=True)

    downloaded, failed = [], []
    tile_num = 0

    for lat in lat_steps:
        for lon in lon_steps:
            tile_num += 1

            t_lat_min = round(float(lat), 6)
            t_lat_max = round(min(float(lat) + tile_size, lat_max), 6)
            t_lon_min = round(float(lon), 6)
            t_lon_max = round(min(float(lon) + tile_size, lon_max), 6)

            if (t_lat_max - t_lat_min) < 0.01 or (t_lon_max - t_lon_min) < 0.01:
                continue

            # Nombre con coordenadas reales (sin truncar con int())
            tile_name = (
                f"{region_name}_T{tile_num:04d}"
                f"_Lat{t_lat_min:+07.2f}to{t_lat_max:+07.2f}"
                f"_Lon{t_lon_min:+08.2f}to{t_lon_max:+08.2f}"
            )

            print(f"[{tile_num:3d}/{total_tiles}]", end=" ")
            result = download_tile(
                t_lat_min, t_lat_max, t_lon_min, t_lon_max,
                tile_name, region_dir, year, month
            )

            if result:
                downloaded.append(result)
            else:
                failed.append(tile_name)

    print(f"\n✓ Descargadas: {len(downloaded)}/{total_tiles}")
    if failed:
        print(f"✗ Fallidas: {len(failed)}")
    return downloaded, failed


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN PRINCIPAL
# ─────────────────────────────────────────────────────────────

regions = {
    'Colombia':      [-80, -4,  -73, 13],
    'Ecuador':       [-80, -5,  -70,  2],
    'Peru_Norte':    [-80, -10, -72,  0],
    'Peru_Centro':   [-80, -15, -72, -10],
    'Peru_Sur':      [-80, -19, -65, -15],
    'Bolivia_Norte': [-80, -13, -65,  -9],
    'Bolivia_Sur':   [-80, -23, -65, -13],
}

print("=" * 70)
print("DESCARGA MCD64A1 — REGIÓN ANDES (FIX: dimensiones explícitas)")
print("=" * 70)

respuesta = input("¿Continuar con la descarga? (s/n): ")
if respuesta.lower() != 's':
    print("Descarga cancelada")
    exit()

all_downloaded, all_failed = [], []
years  = range(2001, 2025)
months = range(1, 13)

for region_name, bounds in regions.items():
    for year in years:
        for month in months:
            downloaded, failed = download_region_with_fixed_tiles(
                region_name, bounds, output_dir,
                tile_size=2, year=year, month=month
            )
            all_downloaded.extend(downloaded)
            all_failed.extend(failed)

# ─────────────────────────────────────────────────────────────
# RESUMEN FINAL
# ─────────────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("RESUMEN FINAL")
print("=" * 70)
print(f"✓ Tiles descargadas: {len(all_downloaded)}")
print(f"✗ Tiles fallidas   : {len(all_failed)}")

if all_downloaded:
    total_gb = sum(os.path.getsize(f) for f in all_downloaded) / (1024 ** 3)
    print(f"Tamaño total: {total_gb:.2f} GB")
    print(f"Directorio : {output_dir}")

    print("\nDetalles por región:")
    for rname in regions:
        rdir = os.path.join(output_dir, rname)
        if os.path.exists(rdir):
            files = [f for f in os.listdir(rdir) if f.endswith('.tif')]
            if files:
                size_mb = sum(os.path.getsize(os.path.join(rdir, f)) for f in files) / (1024 ** 2)
                print(f"  {rname:20s}: {len(files):4d} tiles, {size_mb:9.1f} MB")

print("=" * 70)