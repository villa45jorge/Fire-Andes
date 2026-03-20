#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modified on 06/03/2026
Version 0.0.0
@author: jvilla
"""

"""
build_annual_rasters.py

Etapa 1: Por cada tile (lat x lon), agrupa los 12 TIFs mensuales y crea
         un raster multibanda (1 banda por mes, orden enero→diciembre).

Etapa 2: Por cada año, mosaica todos los tiles en un único raster anual.

Estructura esperada de entrada:
  BASE_DIR/
    <Region>/
      <Subregion>/
        <Region>_<Sub>_<Tile>_<Lat>_<Lon>_<YYYY>_<MM>.tif
        ...

Salida:
  OUTPUT_DIR/
    tiles/   → un .tif multibanda por (tile, año)
    annual/  → un .tif mosaico por año
    
    
@Issues/Problems:
    QA pixel (band3)
    Uncertain pixel (band2)
    How to put in the pipeline? (read documentation)
"""


from pathlib import Path
import re
from collections import defaultdict
import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_bounds


#Compte Earth Engine
#ee.Authenticate()
#ee.Initialize(project='ee-villa45ramos')

# ── CONFIGURACIÓN ────────────────────────────────────────────────────────────
BASE_DIR   = Path("/media/villaramos/Donnees/MesProgrammes/MCD64A1/data/raw/Modis_BurnedAreas_v1")   # ← cambia esto
OUTPUT_DIR = Path("/media/villaramos/Donnees/MesProgrammes/MCD64A1/data/raw/Modis_BurnedAreas_v1/output")      # ← cambia esto

MONTHS = list(range(1, 13))              # 01–12
NODATA = -9999
# ─────────────────────────────────────────────────────────────────────────────


def parse_filename(path: Path):
    """
    Extrae (tile_key, year, month) del nombre de archivo.
    tile_key = todo excepto año y mes → identifica un tile único.
    Ejemplo:
      Bolivia_Norte_T0016_Lat-011to-009_Lon-066to-065_2023_04.tif
      → tile_key = 'Bolivia_Norte_T0016_Lat-011to-009_Lon-066to-065'
        year=2023, month=4
    """
    stem = path.stem
    # El patrón espera _YYYY_MM al final
    m = re.match(r"^(.+?)_(\d{4})_(\d{2})$", stem)
    if not m:
        return None, None, None
    tile_key = m.group(1)
    year     = int(m.group(2))
    month    = int(m.group(3))
    return tile_key, year, month


def collect_files(base_dir: Path):
    """
    Recorre recursivamente base_dir y devuelve un dict:
      { (tile_key, year): { month: Path } }
    """
    catalog = defaultdict(dict)
    for tif in sorted(base_dir.rglob("*.tif")):
        tile_key, year, month = parse_filename(tif)
        if tile_key is None:
            print(f"  [skip] nombre no reconocido: {tif.name}")
            continue
        catalog[(tile_key, year)][month] = tif
    return catalog


# ── ETAPA 1: raster multibanda por (tile, año) ───────────────────────────────

def build_tile_annual(tile_key, year, month_files, out_dir):
    """
    Crea un GeoTIFF con 12 bandas (una por mes).
    Banda i → mes i (1=enero … 12=diciembre).
    Si un mes falta, la banda se rellena con NODATA.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{tile_key}_{year}_annual_test0.tif"

    if out_path.exists():
        print(f"  [existe] {out_path.name}")
        return out_path

    # Tomar metadatos de cualquier archivo disponible
    ref_path = next(iter(month_files.values()))
    with rasterio.open(ref_path) as src:
        profile = src.profile.copy()
        height, width = src.height, src.width
        transform = src.transform
        crs       = src.crs

    profile.update(
        count  = 12,
        dtype  = "float32",
        nodata = NODATA,
        compress = "lzw",
    )

    with rasterio.open(out_path, "w", **profile) as dst:
        for month in MONTHS:
            band_idx = month   # rasterio usa 1-indexed
            if month in month_files:
                with rasterio.open(month_files[month]) as src:
                    data = src.read(1).astype("float32")
                    # Propagar nodata del origen
                    if src.nodata is not None:
                        data[data == src.nodata] = NODATA
            else:
                print(f"    [falta mes {month:02d}] rellenando con NODATA")
                data = np.full((height, width), NODATA, dtype="float32")

            dst.write(data, band_idx)
            dst.update_tags(band_idx, month=f"{year}-{month:02d}")

    print(f"  ✓ tile anual → {out_path.name}")
    return out_path

# ── ETAPA 2: mosaico anual con todos los tiles ───────────────────────────────

def build_annual_mosaic(year, tile_paths, out_dir):
    """
    Mosaica todos los tiles de un mismo año en un único raster.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"mosaic_{year}_annual_test0.tif"

    if out_path.exists():
        print(f"  [existe] {out_path.name}")
        return out_path

    datasets = [rasterio.open(p) for p in tile_paths]
    try:
        mosaic, transform = merge(datasets, nodata=NODATA, method="first")
    finally:
        for ds in datasets:
            ds.close()

    profile = datasets[0].profile.copy()
    profile.update(
        height    = mosaic.shape[1],
        width     = mosaic.shape[2],
        transform = transform,
        count     = 12,
        dtype     = "float32",
        nodata    = NODATA,
        compress  = "lzw",
        tiled     = True,
        blockxsize = 512,
        blockysize = 512,
    )

    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mosaic)
        for month in MONTHS:
            dst.update_tags(month, month=f"{year}-{month:02d}")

    print(f"  ✓ mosaico anual → {out_path.name}")
    return out_path

# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    tiles_out  = OUTPUT_DIR / "tiles"
    annual_out = OUTPUT_DIR / "annual"

    print(f"\n{'='*60}")
    print(f"  Directorio base : {BASE_DIR}")
    print(f"  Salida tiles    : {tiles_out}")
    print(f"  Salida mosaicos : {annual_out}")
    print(f"{'='*60}\n")

    # ── Inventario ──────────────────────────────────────────────────────────
    print("▶ Inventariando archivos...")
    catalog = collect_files(BASE_DIR)
    if not catalog:
        print("  No se encontraron archivos TIF con el patrón esperado.")
        return

    years = sorted({y for (_, y) in catalog.keys()})
    tiles = sorted({t for (t, _) in catalog.keys()})
    print(f"  {len(catalog)} combinaciones (tile × año)")
    print(f"  Años   : {years}")
    print(f"  Tiles  : {len(tiles)}\n")

    # ── Etapa 1 ─────────────────────────────────────────────────────────────
    print("▶ ETAPA 1 — Construyendo rasters multibanda por tile × año...")
    tile_annual_paths = defaultdict(list)   # year → [Path, ...]

    for (tile_key, year), month_files in sorted(catalog.items()):
        print(f"  [{tile_key}] {year}  ({len(month_files)}/12 meses)")
        path = build_tile_annual(tile_key, year, month_files, tiles_out)
        tile_annual_paths[year].append(path)

    # ── Etapa 2 ─────────────────────────────────────────────────────────────
    print("\n▶ ETAPA 2 — Construyendo mosaicos anuales...")
    for year in sorted(tile_annual_paths.keys()):
        paths = tile_annual_paths[year]
        print(f"  {year}: {len(paths)} tile(s)")
        build_annual_mosaic(year, paths, annual_out)

    print("\n✅ Proceso completado.")


if __name__ == "__main__":
    main()







