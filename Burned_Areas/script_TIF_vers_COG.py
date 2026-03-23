# -*- coding: utf-8 -*-
"""
Modified on 23/03/2026
Version 0.0.0
@author: jvilla



"""

from osgeo import gdal
from pathlib import Path
import time

gdal.UseExceptions()

# Definir rutas
base_dir = Path("/media/villaramos/Donnees/MesProgrammes/data/MCD64A1")
data_dir = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir = base_dir / "3_output"
test_dir = base_dir / "4_test"


files_to_convert = [
    (data_dir / "mosaico_andes_DEM.tif", processed_dir  / "mosaico_andes_DEM_COG.tif"),
    (data_dir / "mosaico_andes_WC.tif",  processed_dir  / "mosaico_andes_WC_COG.tif"),
]

for src_path, dst_path in files_to_convert:
    print(f"\n{'═'*60}")
    print(f"  Convirtiendo: {src_path.name} → {dst_path.name}")

    # ── info antes ────────────────────────────────────────────
    ds = gdal.Open(str(src_path))
    band = ds.GetRasterBand(1)
    block_x, block_y = band.GetBlockSize()
    size_x, size_y   = ds.RasterXSize, ds.RasterYSize
    ram_gb = size_x * size_y * 4 / 1e9
    print(f"  📐 Size     : {size_x} x {size_y}")
    print(f"  📦 Block    : {block_x} x {block_y}  {'⚠️  strip layout' if block_y == 1 else '✅ tiled'}")
    print(f"  🧠 RAM bruta: ~{ram_gb:.1f} GB")
    ds = None

    # ── conversión a COG ──────────────────────────────────────
    start = time.perf_counter()

    translate_options = gdal.TranslateOptions(
        format       = "COG",
        creationOptions = [
            "COMPRESS=LZW",
            "BLOCKSIZE=512",
            "BIGTIFF=YES",
        ]
    )

    gdal.Translate(
        destName  = str(dst_path),
        srcDS     = str(src_path),
        options   = translate_options,
        callback  = gdal.TermProgress_nocb,   # barra de progreso en consola
    )

    elapsed = time.perf_counter() - start
    print(f"  ⏱  Conversión completada en {elapsed:.1f}s")

    # ── verificación ──────────────────────────────────────────
    ds = gdal.Open(str(dst_path))
    band = ds.GetRasterBand(1)
    block_x, block_y = band.GetBlockSize()
    size_x, size_y   = ds.RasterXSize, ds.RasterYSize
    file_gb = dst_path.stat().st_size / 1e9
    print(f"  ✅ Block nuevo : {block_x} x {block_y}  {'✅ tiled' if block_x == 512 else '⚠️ revisar'}")
    print(f"  💾 Tamaño disco: {file_gb:.2f} GB")
    ds = None

print(f"\n{'═'*60}")
print("✅ Conversiones completadas. Actualiza las rutas en el pipeline:")
print(f"   mosaico_andes_DEM_COG.tif")
print(f"   mosaico_andes_WC_COG.tif")