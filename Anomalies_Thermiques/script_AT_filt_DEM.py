import rasterio
from rasterio.merge import merge
from rasterio.mask import mask
import numpy as np
import os
from shapely.geometry import box

# Configuración
input_root = "D:/MesProgrammes/MCD14ML/copernicus_dem_andes"
output_dir = "D:/MesProgrammes/MCD14ML/copernicus_dem_andes/output"
os.makedirs(output_dir, exist_ok=True)

# Zona de interés (lon_min, lat_min, lon_max, lat_max)
#bbox = (-73, -14, -71, -12)
bbox = (-80, -20, -60, 1)

geometria = [box(*bbox)]

umbral = 2000
rasters_procesados = []
vacios = 0
fuera_zona = 0

print("Procesando rasters...")

for carpeta, subcarpetas, archivos in os.walk(input_root):
    
    if output_dir in carpeta:
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
                if np.nanmax(data) > umbral:
                    # FILTRAR: convertir a nodata los píxeles <= umbral
                    data_filtrado = np.where(data > umbral, data, np.nan)
                    
                    # Guardar raster temporal filtrado
                    meta = src.meta.copy()
                    meta.update({
                        "height": data_filtrado.shape[0],
                        "width": data_filtrado.shape[1],
                        "transform": transform_recortada,
                        "nodata": -9999,
                        "dtype": 'float32'
                    })
                    
                    # Convertir NaN a nodata value para guardar
                    data_filtrado = np.where(np.isnan(data_filtrado), -9999, data_filtrado)
                    
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
print(f"Descartados (sin píxeles > {umbral}m): {vacios}")
print(f"Fuera de zona: {fuera_zona}")

# FUSIONAR todos los rasters filtrados
if rasters_procesados:
    print("\nFusionando rasters...")
    
    try:
        # Abrir todos los rasters
        src_files = [rasterio.open(ruta) for ruta in rasters_procesados]
        
        # Fusionar con merge
        mosaico, transform = merge(src_files, nodata=-9999)
        
        # Cerrar archivos
        for src in src_files:
            src.close()
        
        # Guardar mosaico final
        meta = src_files[0].meta.copy()
        meta.update({
            "driver": "GTiff",
            "height": mosaico.shape[1],
            "width": mosaico.shape[2],
            "transform": transform,
            "compress": "lzw"
        })
        
        output_mosaico = os.path.join(output_dir, "mosaico_andes_filtrado.tif")
        with rasterio.open(output_mosaico, "w", **meta) as dest:
            dest.write(mosaico)
        
        print(f"✓ Mosaico guardado: {output_mosaico}")
        
        # Limpiar archivos temporales
        print("\nLimpiando archivos temporales...")
        for temp_file in rasters_procesados:
            os.remove(temp_file)
        
        print("✓ Proceso completado")
        
    except Exception as e:
        print(f"⚠ Error al fusionar: {e}")
else:
    print("\n⚠ No hay rasters para fusionar")