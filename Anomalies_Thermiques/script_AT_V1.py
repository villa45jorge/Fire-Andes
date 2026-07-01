# -*- coding: utf-8 -*-
"""
Modified on 01/07/2026
Version 2.1.0
@author: jvilla
"""

import pandas as pd
import numpy as np
from rasterstats import zonal_stats
import geopandas as gpd
from shapely.geometry import box
import os
from tqdm import tqdm
from datetime import timedelta
import time
import rasterio
from rasterio.mask import mask as rio_mask
import gc
from shapely.geometry import shape

# --- Función utilitaria ---
def timer(label, start):
    elapsed = time.time() - start
    print(f"  ✓ {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()  # retorna nuevo start para el siguiente paso

def filt_csv(file_path,country_shape,DEM,WC,output_path):
  """
  This function takes a CSV file and filter
  - by confidence (>80% High Level Confidence)
  - type of thermal anomalie detected (0 = presumed vegetation fire, 2 =other static land source)
  - latitude's limite in the region of interest (Tropical Andes)  -20 (20°S) et 1 (1°N) (Segura,xxxx)
  - longitude's limite in the region of interest (Tropical Andes) -80 (80°W) et -60 (60°W) (Segura,xxxx)
  - select only the columns needed
  #, add a new column with the name of the country,
  #and add a new column with the name of the climate zone.

  Parameters:
  -------------
  file_path: str
      Path to the CSV file to filter.

  Returns:
  -------------
  filtered_df: pd.DataFrame
    Filtered DataFrame.

  """
  try:
      
    t_total = time.time()
    print("Starting work ...")
    #Read the CSV file
    t = time.time()
    df = pd.read_csv(file_path)
    
    df =df.sample(n=1000, random_state=69)
    
    t = timer("Carga de datos", t)
    
    print("Dataframe taille:",df.shape)
    
    countries = gpd.read_file(country_shape)
    countries = countries[['gaul0_name','geometry']]

    #Filter CSV file
    filt_df=df.query('confidence >= 80 and ' '(`type` == 0 or `type` == 2) and '
    'latitude <= 1 and latitude >= -20 and ' 'longitude <= -60 and longitude >= -80')[['latitude', 'longitude', 'acq_date', 'confidence', 'type']]

    #Add climate zone
    conditionlist = [
      (filt_df["latitude"] >= -5) & (filt_df["latitude"] ),
      (filt_df["latitude"] >= -8) & (filt_df["latitude"] < -5),
      (filt_df["latitude"] <= -8) & (filt_df["latitude"] ),
    ]
    choicelist = ["Zone_Equatorial", "Transition_Zone", "South_Zone"]
    filt_df["Zone_Clima"] = np.select(conditionlist, choicelist, default="Not Specified")
    print("Filtering ok ...")
    print("Dataframe filtered taille:",filt_df.shape)
    t = timer("Filtering DataFrame", t)
    
    gdf = gpd.GeoDataFrame(
        filt_df, 
        geometry=gpd.points_from_xy(filt_df.longitude, filt_df.latitude),
        crs='EPSG:4326')
    #gdf.to_file("output.shp")
    
    gdf = gdf.to_crs(countries.crs)
    
    points_with_country = gpd.sjoin(gdf, countries, how='left', predicate='within')

     # Crear buffers cuadrados de 1°x1° directamente
    def create_square_buffer(point, size=0.5):
        """Crea un cuadrado de (size*2)° x (size*2)° alrededor del punto"""
        x, y = point.x, point.y
        return box(x - size, y - size, x + size, y + size)  
        
    points_buffered = points_with_country.copy()
    points_buffered['geometry'] = points_with_country.geometry.apply(lambda p: create_square_buffer(p, 0.5))
  
    print("Dataframe buffered taille:",points_buffered.shape)
    print("Buffering ok ...")
    t = timer("Buffering DataFrame", t)

    tile_size = 1  # grados
    x_min, y_min, x_max, y_max = -80, -20, -60, 1
    
    x_tiles = np.arange(x_min, x_max, tile_size)
    y_tiles = np.arange(y_min, y_max, tile_size)
    
    #result_chunks = []
    skipped_tiles = 0
    #chunk_size = 50_000  # ajustar según RAM disponible
    tiles = []
    for x in x_tiles:
        for y in y_tiles:
            tiles.append(box(x, y, x + tile_size, y + tile_size))
    
    print(f"N° de tiles ({tile_size}°x{tile_size}°): {len(tiles)}")
    
    all_results = []
    
    with rasterio.open(DEM) as src, rasterio.open(WC) as src2:
        
        for t_idx, tile_geom in enumerate(tqdm(tiles, desc="Procesando tiles")):
    
            t_tile = time.time()
            
            points_in_tile = points_buffered[
                points_buffered.intersects(tile_geom)
            ].reset_index(drop=True)
            
            if len(points_in_tile) == 0:
                skipped_tiles += 1
                continue
            try:
                out_image, out_transform = rio_mask(
                    src,
                    [tile_geom],
                    crop=True,
                    all_touched=True
                )

                out_image2, out_transform2 = rio_mask(
                    src2,
                    [tile_geom],
                    crop=True,
                    all_touched=True
                )
    
            except Exception as e:
                print(f"\n  ⚠ Tile {t_idx+1} error al recortar DEM: {e}")
                continue    

            # Extraer estadísticas del raster
            stats = zonal_stats(
                points_in_tile,
                out_image[0],
                affine=out_transform,
                stats=['mean', 'median'],
                prefix='dem_',
                geojson_out=True,
                nodata=src.nodata or -9999
            )
            
            
            
            stats1 = zonal_stats(
                points_in_tile,
                out_image2[0],
                affine=out_transform2, 
                stats=['majority'],    # majority = moda
                prefix='wc_',
                nodata=-9999,
                geojson_out=False
            )

            stats_df_dem = gpd.GeoDataFrame(
                [f['properties'] for f in stats],
                geometry=[shape(f['geometry']) for f in stats],  # reconstruir geometry
                crs=points_buffered.crs
            )
            
            stats_df_wc = pd.DataFrame(stats1)
            
            # Reset index por seguridad antes de concatenar
            stats_df_dem = stats_df_dem.reset_index(drop=True)
            stats_df_wc  = stats_df_wc.reset_index(drop=True)
            
            tile_df = pd.concat([stats_df_dem, stats_df_wc], axis=1)
            all_results.append(tile_df)
    
            del out_image, out_image2, out_transform, out_transform2, points_in_tile, stats, stats1
            gc.collect()

            print(f"  ✓ Tile {t_idx+1:02d}/{len(tiles)} "
                  f"— {time.time()-t_tile:.1f}s")
            
        t = timer("Zonal stats DEM", t)

    #features = [f for f in result_chunks]  # liste de dicts GeoJSON Feature
    final_stats = pd.concat(all_results).reset_index(drop=True)
    gdf_final = gpd.GeoDataFrame(final_stats, geometry='geometry', crs=points_buffered.crs)    
    print("GeoDataframe Stats 1:",gdf_final.shape)
    print("Stast 1 part ok ...")
    t = timer("Stats Part one", t)

    umbral = 2000
    gdf_final_filtrado = gdf_final[gdf_final['dem_median'] > umbral]
    
    print("Last Filter ok ...")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    gdf_final_filtrado.to_file(output_path)
    
    print("Work Done!")
    print(f"\n{'─'*40}")
    print(f"  TOTAL: {timedelta(seconds=int(time.time() - t_total))}")
    print(f"{'─'*40}")
    return gdf_final_filtrado

  except Exception as e:
    print(f"An error occurred: {e}")
    
    
gdf_final_filtrado=filt_csv('D:/MesProgrammes/MCD14ML/fire_archive_M-C61_706555.csv',
                       'D:/MesProgrammes/Limits_countries/GAUL_2024_L1.shp',
                       'D:/MesProgrammes/MCD14ML/copernicus_dem_andes/output/mosaico_andes_filtrado.tif',
                       'D:/MesProgrammes/MCD14ML/copernicus_wc_andes/output/mosaico_andes_filtrado.tif',
                       'D:/MesProgrammes/MCD14ML/output/AnomaliesThermiques.shp')