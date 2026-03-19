import rasterio
#from rasterio.merge import merge
from rasterio.mask import mask
#from rasterio.io import MemoryFile
import numpy as np
import os
from shapely.geometry import box
from pathlib import Path
import subprocess

# Configuración
#input_root = "D:/MesProgrammes/MCD14ML/copernicus_wc_andes"
#output_dir = "D:/MesProgrammes/MCD14ML/copernicus_wc_andes/output"


base_dir = Path("/media/villaramos/Donnees/MesProgrammes/MCD14ML")
data_dir = base_dir / "data/raw/copernicus_wc_andes_v2"
output_dir = base_dir / "data/raw/copernicus_wc_andes_v2/output"
os.makedirs(output_dir, exist_ok=True)
# Zona de interés (lon_min, lat_min, lon_max, lat_max)
#bbox = (-73, -14, -71, -12)
bbox = (-80, -20, -60, 1)

geometria = [box(*bbox)]

#umbral = 2000
rasters_procesados = []
vacios = 0
fuera_zona = 0

print("Procesando rasters...")

for carpeta, subcarpetas, archivos in os.walk(data_dir,output_dir):
    
    if str(output_dir) in str(carpeta):
        continue
    
    for archivo in archivos:
        if not archivo.endswith(".tif"):
            continue
        
        ruta = os.path.join(carpeta, archivo)
        
        try:
            with rasterio.open(ruta) as src:
                # Verificar intersección con bbox
                raster_bounds = src.bounds
                
                if not (raster_bounds.right > bbox[0] and 
                        raster_bounds.left < bbox[2] and
                        raster_bounds.top > bbox[1] and 
                        raster_bounds.bottom < bbox[3]):
                    fuera_zona += 1
                    continue
                
                # Recortar al bbox
                data_recortada, transform_recortada = mask(src, geometria, crop=True)
                data = data_recortada[0].astype(float)
                nodata = src.nodata
                
                # Enmascarar nodata
                if nodata is not None:
                    data = np.where(data == nodata, np.nan, data)
                
                # Verificar si hay píxeles > umbral
                #if np.nanmax(data) > umbral:
                    # FILTRAR: convertir a nodata los píxeles <= umbral
                    #data_filtrado = np.where(data > umbral, data, np.nan)
                    
                    # Guardar raster temporal filtrado
                    meta = src.meta.copy()
                    meta.update({
                        "height": data.shape[0],
                        "width": data.shape[1],
                        "transform": transform_recortada,
                        "nodata": -9999,
                        "dtype": 'float32'
                    })
                    
                    # Convertir NaN a nodata value para guardar
                    data_filtrado = np.where(np.isnan(data), -9999, data)
                    
                    temp_path = os.path.join(output_dir, f"temp_{archivo}")
                    with rasterio.open(temp_path, "w", **meta) as dest:
                        dest.write(data_filtrado, 1)
                    
                    rasters_procesados.append(temp_path)
                    print(f"✓ Procesado: {archivo}")
                else:
                    vacios += 1
                    
        except Exception as e:
            print(f"⚠ Error en {archivo}: {e}")

print(f"\nRasters procesados: {len(rasters_procesados)}")
#print(f"Descartados (sin píxeles > {umbral}m): {vacios}")
print(f"Fuera de zona: {fuera_zona}")

vrt_path = str(output_dir / "mosaico_temp.vrt")
output_mosaico = str(output_dir / "mosaico_andes_filtrado.tif")

print(f"Construyendo VRT con {len(rasters_procesados)} rasters...")
subprocess.run([
    "gdalbuildvrt",
    "-vrtnodata", "-9999",
    vrt_path,
    *[str(r) for r in rasters_procesados]
], check=True)

print("Convirtiendo a GeoTIFF...")
subprocess.run([
    "gdal_translate",
    "-of", "GTiff",
    "-co", "COMPRESS=LZW",
    "-co", "TILED=YES",
    "-co", "BIGTIFF=YES",
    "-a_nodata", "-9999",
    vrt_path, output_mosaico
], check=True)

print(f"✓ Mosaico guardado: {output_mosaico}")

os.remove(vrt_path)

print("Limpiando archivos temporales...")
for temp_file in rasters_procesados:
    os.remove(temp_file)
print("✓ Proceso completado")