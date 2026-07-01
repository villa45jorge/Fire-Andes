# -*- coding: utf-8 -*-
"""
Modified on 04/03/2026
Version 4.4.0
@author: jvilla

Modifications: 
    -all dataset
    -Cluster 32Gb 

"""

from pathlib import Path
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
from sklearn.neighbors import BallTree
import networkx as nx

# Definir rutas
base_dir = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/MCD14ML")
data_dir = base_dir / "1_input"
processed_dir = base_dir / "2_processed"
output_dir = base_dir / "3_outputs"
test_dir = base_dir / "4_test"

'''CSV MODIS (raw)
    ↓ all data
    ↓ filtros temáticos + zona climática
    ↓ join espacial con países → excluye Brasil
    ↓ buffer 500m por punto
    ↓ zonal_stats DEM + WorldClim (420 tiles)
    ↓ filtro altitudinal > 2000m
    ↓ clustering espacio-temporal (1km / 15 días)
    ↓ primer evento por cluster
    → Shapefile final_stats
'''    
# --- Función utilitaria ---
def timer(label, start):
    elapsed = time.time() - start
    print(f"  ✓ {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()

def cluster_spatiotemporal(df, spatial_km, temporal_days):
    df['date'] = pd.to_datetime(df['acq_date'])

    df = df.sort_values('date').reset_index(drop=True)
    df['date_num'] = (df['date'] - df['date'].min()).dt.days
    
    # BallTree espacial (Haversine)
    coords_rad = np.radians(df[['latitude', 'longitude']].values)
    tree = BallTree(coords_rad, metric='haversine')
    
    radius_rad = spatial_km / 6371 
    
    # Para cada punto, encontrar vecinos espaciales
    indices = tree.query_radius(coords_rad, r=radius_rad)
    
    # Construir grafo: conectar solo si también son temporalmente cercanos
    G = nx.Graph()
    G.add_nodes_from(range(len(df)))
    
    dates = df['date_num'].values
    
    for i, neighbors in enumerate(indices):
        for j in neighbors:
            if j > i: 
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

    print("Starting work ...")
    #Read the CSV file
    t = time.time()
    df = pd.read_csv(file_path)
    print("df shape: ",df.shape)
    
    t = timer("Carga de datos", t)
    
    print("Dataframe Sample taille:",df.shape)
    
    countries = gpd.read_file(country_shape)
    countries = countries[['gaul0_name','geometry']]

    #Filter CSV file
    df=df.query('confidence >= 80 and ' '(`type` == 0 or `type` == 2) and '
    'latitude <= 1 and latitude >= -20 and ' 'longitude <= -60 and longitude >= -80')[['latitude', 'longitude', 'acq_date', 'acq_time','satellite','confidence', 'type']]

    #Add climate zone
    conditionlist = [
        (df["latitude"] >= -5) & (df["latitude"] <= 1),
        (df["latitude"] >= -8) & (df["latitude"] < -5),
        (df["latitude"] < -8)  & (df["latitude"] >= -20),
    ]
    choicelist = ["Zone_Equatorial", "Transition_Zone", "South_Zone"]
    df["Zone_Clima"] = np.select(conditionlist, choicelist, default="Not Specified")
    
    print("Filtering ok ...")
    print("Dataframe filtered taille:",df.shape)
    t = timer("Filtering DataFrame", t)
    
    gdf = gpd.GeoDataFrame(
        df, 
        geometry=gpd.points_from_xy(df.longitude, df.latitude),
        crs='EPSG:4326')
    
    #gdf = gdf.to_crs(countries.crs)
    
    gdf_country = gpd.sjoin(gdf, countries, how='left', predicate='within')
    
    #Filtering by Country
    gdf_country = gdf_country.query('gaul0_name != "Brazil"' )
    gdf_country['gaul0_name'] = gdf_country['gaul0_name'].replace('Bolivia (Plurinational State of)', 'Bolivia')
    
    BUFFER_SIZE_DEG = 0.005  # ~500m, captura exactamente el pixel de 1km (±500m desde el centroide)
        
    points_buffered = gdf_country.copy()
    points_buffered['geometry'] = gdf_country.geometry.to_crs('EPSG:3857').buffer(500).to_crs('EPSG:4326')
    
    print("Dataframe buffered taille:",points_buffered.shape)
    print("Buffering ok ...")
    t = timer("Buffering DataFrame", t)

    tile_size = 1
    x_min, y_min, x_max, y_max = -80, -20, -60, 1
    
    x_tiles = np.arange(x_min, x_max, tile_size)
    y_tiles = np.arange(y_min, y_max, tile_size)
    
    skipped_tiles = 0
    tiles = []
    for x in x_tiles:
        for y in y_tiles:
            tiles.append(box(x, y, x + tile_size, y + tile_size))
    
    print(f"N° de tiles ({tile_size}°x{tile_size}°): {len(tiles)}")
    
    all_results = []
    
    centroids = gdf_country.geometry
    
    with rasterio.open(DEM) as src, rasterio.open(WC) as src2:

        
        for t_idx, tile_geom in enumerate(tqdm(tiles, desc="Procesando tiles")):
    
            t_tile = time.time()
            
            points_in_tile = points_buffered[
                centroids.within(tile_geom)
            ].reset_index(drop=True)
            
            if len(points_in_tile) == 0:
                skipped_tiles += 1
                continue
            minx, miny, maxx, maxy = tile_geom.bounds
            tile_geom_expanded = box(
                minx - BUFFER_SIZE_DEG,
                miny - BUFFER_SIZE_DEG,
                maxx + BUFFER_SIZE_DEG,
                maxy + BUFFER_SIZE_DEG
            )
            try:
                out_image, out_transform = rio_mask(
                    src,
                    [tile_geom_expanded],
                    crop=True,
                    all_touched=True
                )
                
                out_image2, out_transform2 = rio_mask(
                    src2,
                    [tile_geom_expanded],
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
                stats=['majority'],
                prefix='wc_',
                nodata=-9999,
                geojson_out=False
            )
            
            
            stats_df_dem = gpd.GeoDataFrame(
                [f['properties'] for f in stats],
                geometry=[shape(f['geometry']) for f in stats],
                crs=points_buffered.crs
            )
            
            stats_df_wc = pd.DataFrame(stats1)
            
            # Reset index por seguridad antes de concatenar
            stats_df_dem = stats_df_dem.reset_index(drop=True)
            stats_df_wc  = stats_df_wc.reset_index(drop=True)
            
            tile_df = pd.concat([stats_df_dem, stats_df_wc], axis=1)
            all_results.append(tile_df)
    
            del out_image, out_image2, out_transform, out_transform2, points_in_tile, stats, stats1, tile_df
            gc.collect()

            print(f"  ✓ Tile {t_idx+1:02d}/{len(tiles)} "
                  f"— {time.time()-t_tile:.1f}s")
            
        t = timer("Zonal stats DEM", t)

    final_stats = pd.concat(all_results).reset_index(drop=True)
    gdf_final = gpd.GeoDataFrame(final_stats, geometry='geometry', crs=points_buffered.crs)    
    print("GeoDataframe Stats taille:",gdf_final.shape)
    #print("Stast ok ...")
    t = timer("Statistiaues done", t)

    umbral = 2000
    gdf_final = gdf_final[gdf_final['dem_median'] > umbral]
    
    print("Last Filter ok ...")

    gdf_cluster = cluster_spatiotemporal(pd.DataFrame(gdf_final), spatial_km=1.0, temporal_days=15)
    gdf_cluster = gdf_cluster.sort_values('date').groupby('cluster').first().reset_index()
    gdf_cluster = gdf_cluster.drop(columns=['geometry'])
    gdf_cluster ['geometry'] = gpd.points_from_xy(gdf_cluster['longitude'],gdf_cluster['latitude'])
    gdf_result = gpd.GeoDataFrame(gdf_cluster, geometry='geometry', crs='EPSG:4326')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    gdf_result.to_file(output_path)
    
    print("Work Done!")
    print(f"\n{'─'*40}")
    print(f"  TOTAL: {timedelta(seconds=int(time.time() - t_total))}")
    print(f"{'─'*40}")
    return gdf_result

  except Exception as e:
    print(f"An error occurred: {e}")
    
#Execution part  
gdf_result=filt_csv(
    data_dir / 'fire_archive_M-C61_706555.csv',
    data_dir / 'GAUL_2024_L1.shp',
    data_dir / 'mosaico_andes_DEM.tif',
    data_dir / 'mosaico_andes_WC.tif',
    output_dir / 'AnomaliesThermiques_V0.shp')