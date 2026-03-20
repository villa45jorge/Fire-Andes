# -*- coding: utf-8 -*-
"""
Created on Sat Feb 28 12:11:28 2026

@author: villaramos

Version 0.0.0
Issue: Nettoyer et commenter
    
"""

import ee
import requests
import os
from pathlib import Path
import math
import numpy as np
import calendar

ee.Authenticate()

ee.Initialize(project="ee-villa45ramos")

modis_ba = ee.ImageCollection('MODIS/061/MCD64A1')

base_dir = Path("/media/villaramos/Donnees/MesProgrammes/MCD64A1")
output_dir = base_dir / "data/raw/Modis_BurnedAreas_v1"

Path(output_dir).mkdir(parents=True, exist_ok=True)


def download_tile(lat_min, lat_max, lon_min, lon_max, tile_name, output_dir, year, month, scale=500):
    """Descarga una tile con fecha específica"""
    
    region = ee.Geometry.Rectangle([
        float(round(lon_min, 6)), float(round(lat_min, 6)),
        float(round(lon_max, 6)), float(round(lat_max, 6))
    ])
    
    # Filtrar por mes específico
    start_date = f'{year}-{month:02d}-01'
    last_day = calendar.monthrange(year, month)[1]
    end_date = f'{year}-{month:02d}-{last_day}'
        
    collection = (modis_ba
                  .filterDate(start_date, end_date)
                  .filterBounds(region))
    
    # Skip if no image available for this period/region
    if collection.size().getInfo() == 0:
        print(f"  ⏭️  Sin datos: {tile_name}_{year}_{month:02d}")
        return None
    
    monthly_ba = collection.first().clip(region)
        
    output_file = os.path.join(output_dir, f'{tile_name}_{year}_{month:02d}.tif')
    
    if os.path.exists(output_file):
        print(f"  ⏭️  Ya existe: {tile_name}_{year}_{month:02d}")
        return output_file
    
    try:
        url = monthly_ba.getDownloadURL({
            'bands': ['BurnDate', 'Uncertainty', 'QA'],  # BurnDate = día juliano del año
            'scale': scale,
            'crs': 'EPSG:4326',
            'region': region,
            'format': 'GEO_TIFF'
        })
        
        response = requests.get(url, stream=True, timeout=300)
        
        if response.status_code == 200:
            with open(output_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            size_mb = os.path.getsize(output_file) / (1024**2)
            print(f"  ✓ {tile_name}_{year}_{month:02d}: {size_mb:.1f} MB")
            return output_file
        else:
            print(f"  ✗ HTTP {response.status_code}")
            return None
            
    except Exception as e:
        print(f"  ✗ {str(e)[:80]}")
        return None
    
def download_region_with_fixed_tiles(region_name, bounds, output_dir, tile_size, scale, year, month):
    """
    Descarga una región con tiles de tamaño FIJO
    
    Args:
        tile_size: Tamaño de cada tile en grados (1.0° = ~111km)
                   1.0° a 30m = ~3700 píxeles = ~15-40 MB por tile
    """
    lon_min, lat_min, lon_max, lat_max = bounds
    
    print(f"\n{'='*70}")
    print(f"REGIÓN: {region_name}")
    print(f"{'='*70}")
    print(f"Bounds: Lat {lat_min} a {lat_max}, Lon {lon_min} a {lon_max}")
    
    # Calcular cuántas tiles necesitamos
    lat_tiles = math.ceil((lat_max - lat_min) / tile_size)
    lon_tiles = math.ceil((lon_max - lon_min) / tile_size)
    total_tiles = lat_tiles * lon_tiles
    
    print(f"Tamaño de tile: {tile_size}° x {tile_size}°")
    print(f"Grid: {lat_tiles} lat x {lon_tiles} lon = {total_tiles} tiles totales")
    print()
    
    # Crear subdirectorio para esta región
    region_dir = os.path.join(output_dir, region_name)
    Path(region_dir).mkdir(parents=True, exist_ok=True)
    
    # Generar y descargar tiles
    downloaded = []
    failed = []
    tile_num = 0
    
    lat_steps = np.arange(lat_min, lat_max, tile_size)
    lon_steps = np.arange(lon_min, lon_max, tile_size)
    
    for lat in lat_steps:
        for lon in lon_steps:
            tile_num += 1 
            t_lat_min = lat
            t_lat_max = min(lat + tile_size, lat_max)
            t_lon_min = lon
            t_lon_max = min(lon + tile_size, lon_max)
    
            # Skip degenerate tiles
            if (t_lat_max - t_lat_min) < 0.01 or (t_lon_max - t_lon_min) < 0.01:
                continue
            
            # Nombre descriptivo
            tile_name = f'{region_name}_T{tile_num:04d}_Lat{int(t_lat_min):+04d}to{int(t_lat_max):+04d}_Lon{int(t_lon_min):+04d}to{int(t_lon_max):+04d}'
            
            print(f"[{tile_num:3d}/{total_tiles}]", end=" ")
            result = download_tile(t_lat_min, t_lat_max, t_lon_min, t_lon_max, 
                          tile_name, region_dir, scale=scale, year=year, month=month)
            
            if result:
                downloaded.append(result)
            else:
                failed.append(tile_name)
            
            lon += tile_size
        lat += tile_size
    
    # Resumen de esta región
    print(f"\n{'='*70}")
    print(f"RESUMEN - {region_name}")
    print(f"{'='*70}")
    print(f"✓ Tiles descargadas: {len(downloaded)}/{total_tiles}")
    
    if downloaded:
        total_size = sum(os.path.getsize(f) for f in downloaded) / (1024**2)
        print(f"💾 Tamaño total: {total_size:.1f} MB ({total_size/1024:.2f} GB)")
    
    if failed:
        print(f"✗ Tiles fallidas: {len(failed)}")
    
    return downloaded, failed

# ============================================================================
# CONFIGURACIÓN PRINCIPAL
# ============================================================================



# Regiones a descargar
regions = {
    'Colombia': [-80, -4, -73, 13],
    'Ecuador': [-80, -5, -70, 2],
    'Peru_Norte': [-80, -10, -72, 0],
    'Peru_Centro': [-80, -15, -72, -10],
    'Peru_Sur': [-80, -19, -65, -15],
    'Bolivia_Norte': [-80, -13, -65, -9],
    'Bolivia_Sur': [-80, -23, -65, -13]
}

print("="*70)
print("DESCARGA MODIS BURNED AREAS - REGIÓN ANDES")
print("MODO: TILES FIJAS DE 10°x10°")
print("="*70)
#print("\nNOTA: Tiles de 0.5° ≈ (111km × 111km ≈ 15-40 MB)/2 cada una")
#print("Total estimado: ~200-300 tiles, ~5-10 GB\n")

# Preguntar si continuar
respuesta = input("¿Continuar con la descarga? (s/n): ")
if respuesta.lower() != 's':
    print("Descarga cancelada")
    exit()

# Descargar cada región
all_downloaded = []
all_failed = []

years = range(2001, 2025)
months = range(1, 13)

for region_name, bounds in regions.items():
    for year in years:
        for month in months:
            downloaded, failed = download_region_with_fixed_tiles(
                region_name, bounds, output_dir,
                tile_size=2, scale=500,
                year=year, month=month  # pasar al download_tile
            )
    
            all_downloaded.extend(downloaded)
            all_failed.extend(failed)

# Resumen final
print("\n\n" + "="*70)
print("RESUMEN FINAL - TODAS LAS REGIONES")
print("="*70)
print(f"Regiones procesadas: {len(regions)}")
print(f"✓ Tiles descargadas totales: {len(all_downloaded)}")
print(f"✗ Tiles fallidas totales: {len(all_failed)}")

if all_downloaded:
    total_size_gb = sum(os.path.getsize(f) for f in all_downloaded) / (1024**3)
    print(f"💾 Tamaño total: {total_size_gb:.2f} GB")
    print(f"📁 Directorio base: {output_dir}")
    
    # Resumen por región
    print(f"\nDetalles por región:")
    for region_name in regions.keys():
        region_dir = os.path.join(output_dir, region_name)
        if os.path.exists(region_dir):
            region_files = [f for f in os.listdir(region_dir) if f.endswith('.tif')]
            if region_files:
                region_size = sum(os.path.getsize(os.path.join(region_dir, f)) 
                                for f in region_files) / (1024**2)
                print(f"  {region_name:20s}: {len(region_files):3d} tiles, {region_size:8.1f} MB")

if all_failed:
    print(f"\n⚠️  Si hay tiles fallidas, prueba reducir tile_size°")

print("="*70)