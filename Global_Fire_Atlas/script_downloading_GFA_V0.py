# -*- coding: utf-8 -*-
"""
Modified on 20/03/2026
Version 0.0.0
@author: jvilla

"""

import requests
from pathlib import Path
from tqdm import tqdm  # pip install tqdm

def download_file(url: str, dest: Path):
    """Descarga con streaming y barra de progreso."""
    response = requests.get(url, stream=True)
    total = int(response.headers.get("content-length", 0))
    
    with open(dest, "wb") as f, tqdm(
        desc=dest.name,
        total=total,
        unit="B",
        unit_scale=True,
    ) as bar:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            bar.update(len(chunk))

RECORD_ID = "14065246"
OUTPUT_DIR = Path("/media/villaramos/Donnees/MesProgrammes/global_fire_atlas")
OUTPUT_DIR.mkdir(exist_ok=True)

# Obtener lista de archivos
record = requests.get(f"https://zenodo.org/api/records/{RECORD_ID}").json()

# Elegir cuáles descargar (evita los 7.7 GB de golpe)
FILES_TO_DOWNLOAD = [
    "GeoTIFF_Qdeg_monthly_summaries.zip",  # el más pequeño: 155 MB
    "SHP_ignitions.zip",
]

for file_info in record["files"]:
    if file_info["key"] in FILES_TO_DOWNLOAD:
        url = file_info["links"]["self"]
        dest = OUTPUT_DIR / file_info["key"]
        
        if dest.exists():
            print(f"Ya existe: {dest.name}, saltando...")
            continue
        
        print(f"Descargando {file_info['key']}...")
        download_file(url, dest)

print("Descarga completa.")
