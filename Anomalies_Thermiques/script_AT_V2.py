# -*- coding: utf-8 -*-
"""
Modified on 17/02/2026
Version 2.0.0
@author: jvilla
"""

import pandas as pd
import numpy as np
#import matplotlib.pyplot as plt
from rasterstats import zonal_stats
#from scipy import stats as sp_stats
import geopandas as gpd
from shapely.geometry import box
import os
from tqdm import tqdm
from datetime import timedelta
import time
#import math
import rasterio
from rasterio.mask import mask as rio_mask
import gc
from shapely.geometry import shape

from sklearn.neighbors import BallTree
import networkx as nx
#import numpy as np
#import pandas as pd

# --- Función utilitaria ---
def timer(label, start):
    elapsed = time.time() - start
    print(f"  ✓ {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()  # retorna nuevo start para el siguiente paso

def cluster_spatiotemporal(df, spatial_km, temporal_days):
    df['date'] = pd.to_datetime(df['acq_date'])

    df = df.sort_values('date').reset_index(drop=True)
    df['date_num'] = (df['date'] - df['date'].min()).dt.days
    
    # BallTree espacial (Haversine)
    coords_rad = np.radians(df[['latitude', 'longitude']].values)
    tree = BallTree(coords_rad, metric='haversine')
    
    radius_rad = spatial_km / 6371  # km → radianes
    
    # Para cada punto, encontrar vecinos espaciales
    indices = tree.query_radius(coords_rad, r=radius_rad)
    
    # Construir grafo: conectar solo si también son temporalmente cercanos
    G = nx.Graph()
    G.add_nodes_from(range(len(df)))
    
    dates = df['date_num'].values
    
    for i, neighbors in enumerate(indices):
        for j in neighbors:
            if j > i:  # evitar duplicados
                if abs(dates[i] - dates[j]) <= temporal_days:
                    G.add_edge(i, j)
    
    # Componentes conectados = clusters
    clusters = np.zeros(len(df), dtype=int) - 1
    for cluster_id, component in enumerate(nx.connected_components(G)):
        for idx in component:
            clusters[idx] = cluster_id
    
    df['cluster'] = clusters
    return df

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
    #Get the file path
    #file_name = Path(file_path).stem
    print("Starting work ...")
    #Read the CSV file
    t = time.time()
    df = pd.read_csv(file_path)
    print("df shape: ",df.shape)
    #breakpoint()

    #df =df.sample(n=100000, random_state=45)
    df =df.sample(n=1000, random_state=69)
    
    t = timer("Carga de datos", t)
    
    print("Dataframe Sample taille:",df.shape)
    
    countries = gpd.read_file(country_shape)
    countries = countries[['gaul0_name','geometry']]

    #Filter CSV file
    filt_df=df.query('confidence >= 80 and ' '(`type` == 0 or `type` == 2) and '
    'latitude <= 1 and latitude >= -20 and ' 'longitude <= -60 and longitude >= -80')[['latitude', 'longitude', 'acq_date', 'acq_time','satellite','confidence', 'type']]

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


    #Random Test
    #filt_df=filt_df.sample(n=100000, random_state=45)
    
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

    #points_sorted = points_buffered.copy()
    #points_sorted['_lat'] = points_sorted.geometry.centroid.y
    #points_sorted['_lon'] = points_sorted.geometry.centroid.x
    #points_sorted = points_sorted.sort_values(['_lat', '_lon']).reset_index(drop=True)
    #points_sorted = points_sorted.drop(columns=['_lat', '_lon'])
    
    #print("Puntos ordenados espacialmente ✓")
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
    
    
    #total_filas = len(points_sorted)  # ~900,000
    #chunk_size  = 10_000
    #n_chunks    = math.ceil(total_filas / chunk_size)
    #n_chunks = math.ceil(len(points_buffered) / chunk_size)
    #print(f"Total filas  : {total_filas:,}")
    #print(f"Chunk size   : {chunk_size:,}")
    #print(f"N° de chunks : {n_chunks}")
    all_results = []
    
    #indices = range(0, len(points_sorted), chunk_size)
    with rasterio.open(DEM) as src, rasterio.open(WC) as src2:
        #total_area  = (x_max - x_min) * (y_max - y_min)  # grados²
        #tile_area   = tile_size ** 2
        #tile_size_gb = 9.54 * (tile_area / total_area)
        #print(f"Tamaño aprox por tile: {tile_size_gb:.2f} GB")
        
        for t_idx, tile_geom in enumerate(tqdm(tiles, desc="Procesando tiles")):
    
            t_tile = time.time()
            
            
            
            #chunk = points_sorted.iloc[start : start + chunk_size].reset_index(drop=True)
            CRS_PROJ = "EPSG:3857"  # UTM zona 19S — ajusta según tu área exacta
            
            centroids = points_buffered.geometry.to_crs(CRS_PROJ).centroid.to_crs(points_buffered.crs)
            
            points_in_tile = points_buffered[
                centroids.within(tile_geom)
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
                #recorte_mb = out_image.nbytes / 1e6
                
                out_image2, out_transform2 = rio_mask(
                    src2,
                    [tile_geom],
                    crop=True,
                    all_touched=True
                )
                #recorte_mb2 = out_image2.nbytes / 1e6
    
            except Exception as e:
                print(f"\n  ⚠ Tile {t_idx+1} error al recortar DEM: {e}")
                continue    
            
            # Extent del chunk actual
            #bounds  = chunk.total_bounds  # (minx, miny, maxx, maxy)
            #bbox    = box(*bounds)
            
            # Leer solo la porción del DEM que cubre este chunk
            #out_image, out_transform = rio_mask(src, [bbox], crop=True)
            #dem_chunk  = out_image[0]
    
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
                #categorical=True,      # trata los pixeles como categorías
                stats=['majority'],    # majority = moda
                prefix='wc_',
                nodata=-9999,
                geojson_out=False
            )
            
            
            #result_chunks.extend(stats)
            #result_chunks.extend(stats1)
            
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
                  #f"— DEM recorte: {recorte_mb:.0f} MB "
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

    df_result = cluster_spatiotemporal(gdf_final_filtrado, spatial_km=1.0, temporal_days=15)
    df_oldest = df_result.sort_values('date').groupby('cluster').first().reset_index()


    
    
    #print("GeoDataframe Stats 2:",result.shape)
    #print("Stast 2 part ok ...")
    #t = timer("Stats Part two", t)


    
    #fig, ax = plt.subplots(figsize=(15, 12))
    #countries.plot(ax=ax, color='lightgray', edgecolor='black', linewidth=0.8)
    #gdf.plot(ax=ax, color='red', markersize=5, alpha=0.6)
    # Zoom en Andes
    #ax.set_xlim(-82, -63)
    #ax.set_ylim(-23, 13)
    #plt.title("Países")
    #plt.show()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df_result.to_file(output_path)
    
    
    print("Work Done!")
    print(f"\n{'─'*40}")
    print(f"  TOTAL: {timedelta(seconds=int(time.time() - t_total))}")
    print(f"{'─'*40}")
    return df,df_result,gdf,final_stats,gdf_final_filtrado,df_oldest,points_buffered


  except Exception as e:
    print(f"An error occurred: {e}")
    


    
df,df_result,gdf,final_stats,gdf_final_filtrado,df_oldest,points_buffered=filt_csv('D:/MesProgrammes/MCD14ML/fire_archive_M-C61_706555.csv',
                       'D:/MesProgrammes/Limits_countries/GAUL_2024_L1.shp',
                       'D:/MesProgrammes/MCD14ML/copernicus_dem_andes/output/mosaico_andes_filtrado.tif',
                       'D:/MesProgrammes/MCD14ML/copernicus_wc_andes/output/mosaico_andes_filtrado.tif',
                       'D:/MesProgrammes/MCD14ML/output/AnomaliesThermiques.shp')