# -*- coding: utf-8 -*-
import os
# Cache GDAL moderada. En GFA cada worker abre 1 shapefile anual y, para
# MapBiomas/DEM, ventanas pequenas por evento. 512 MB/worker es suficiente.
os.environ.setdefault("GDAL_CACHEMAX", "512")
# Silenciar el ruido de libtiff (warnings cosmeticos de etiquetas TIFF).
os.environ.setdefault("CPL_LOG", os.devnull)
"""
Version 4.0.0  (prefijo de salida "GFA_V4")
@author: jvilla

===================== Cambios GFA_V4 (respecto a GFA_V3) =====================
[MOD-ECO-1] DESCOMPOSICION POR ECORREGION. Se abandona la asignacion por
     'representative_point' como UNICA verdad. Ahora cada evento se INTERSECTA
     con todas las ecorregiones que toca y se mide el area geodesica de cada
     trozo. Consecuencias:
       - capa 'eventos' (1 fila/evento, cardinalidad SIN CAMBIOS):
           ECO_NAME       = ecorregion DOMINANTE POR AREA (antes: por punto)
           ECO_NAME_pt    = etiqueta antigua por representative_point (auditoria)
           n_eco          = nº de ecorregiones reales que intersecta
           ha_dom         = area del trozo dominante (ha)
           eco_resid_ha   = area no cubierta por ninguna ecorregion (ha)
       - capa NUEVA 'eventos_eco' (formato LARGO, 1 fila por evento-ecorregion):
           event_uid, year, month, ECO_NAME, es_dominante, area_ha_eco,
           cl12_eco_km2, cl3_eco_km2, cl_x_eco_km2, geometry(trozo)
     MOTIVO: un perimetro a caballo de una frontera aportaba el 100 % de su
     superficie a un solo lado. El error NO es aleatorio: (a) la probabilidad
     de cruzar frontera escala con el perimetro mientras el peso escala con el
     area, luego se concentra en los eventos GRANDES; (b) las fronteras RESOLVE
     son gradientes ecologicos abruptos (puna/yungas, yungas/bosque humedo),
     justo donde la atribucion binaria es maximamente erronea.
[MOD-ECO-2] MapBiomas POR TROZO, sin lecturas raster adicionales.
     cl12_eco_km2 / cl3_eco_km2 NO se reparten proporcionalmente (eso asumiria cobertura uniforme
     dentro del evento y aplanaria justo la señal buscada: el trozo en puna es
     mayoritariamente clase 12 y el trozo en yungas clase 3). Se calculan
     intersectando la geometria de recorte YA obtenida a nivel de evento
     (clip_geom_12 / clip_geom_3) con cada trozo de ecorregion. Por
     asociatividad de la interseccion el resultado es identico a re-rasterizar
     por trozo, con coste I/O CERO. Se verifica sum(cl12_eco_km2) == cl12_km2.
[MOD-ECO-3] SLIVERS. predicate='intersects' es cierto con contacto de medida
     nula, asi que un trozo cuenta como ecorregion REAL solo si supera A LA VEZ
     ECO_MIN_FRAC (fraccion del evento) y ECO_MIN_HA (absoluto). Lo descartado
     no se pierde: cae en eco_resid_ha, de modo que el balance
     sum(area_ha_eco) + eco_resid_ha == area_ha se cumple siempre.
[MOD-ECO-4] VIA RAPIDA. Un sjoin 'within' resuelve los eventos enteramente
     contenidos en una ecorregion (la mayoria): su unico trozo ES el evento, no
     se calcula ninguna interseccion. Solo el resto paga geometria par a par.
[MOD-ECO-5] SE ELIMINAN LAS FRACCIONES (f12, f3, y las f*_eco / eco_frac que
     iban a existir en la capa larga). Solo se conservan AREAS. Motivo: desde
     [MOD-AREA-1] las areas son geodesicas y por tanto INSESGADAS, con lo que
     desaparece la razon que justificaba f12/f3 en [MOD-AREA-5] (eran
     insensibles al CRS y protegian del sesgo de Mercator). Toda fraccion es
     derivable aguas abajo con una division; almacenarla solo duplica el dato,
     multiplica los NaN y obliga a mantener dos convenciones de unidad.
     AVISO: f12 y f3 YA NO EXISTEN en la salida. Codigo posterior que las lea
     debe calcular cl12_km2 / (area_ha/100).
[MOD-ECO-6] TERCERA CLASE cl_x_km2 = "OTROS / SIN DATO", POR DIFERENCIA. Cierra
     el balance de cobertura dentro de cada poligono:
           cl_x_km2 = area_ha/100 - cl12_km2 - cl3_km2
     y en la capa larga
           cl_x_eco_km2 = area_ha_eco/100 - cl12_eco_km2 - cl3_eco_km2.
     NOMENCLATURA: la etiqueta correcta es "otros / sin dato", NO "otros". La
     columna NO es una categoria ecologica medida: mezcla dos cosas de
     naturaleza distinta e indistinguibles en el residuo:
       (i)  superficie que MapBiomas SI clasifico, pero como algo que no es la
            clase 12 ni la 3 (agricultura, agua, urbano, suelo desnudo, ...);
       (ii) superficie con NODATA, donde MapBiomas no afirma nada.
     Separarlas exigiria los tiles del resto de clases o una mascara de validez;
     este pipeline no lee ninguna de las dos. Usar siempre "otros / sin dato" en
     tablas y figuras, para que no se lea como una cobertura observada.
     Advertencias adicionales, porque cl_x_km2 NO es simetrica de cl12/cl3
     (aquellas se MIDEN, esta se INFIERE y absorbe todo el error del pipeline):
       (a) NaN se PROPAGA: cl_x_km2 es NaN si cl12_km2 O cl3_km2 lo son. Los
           tiles C12 y C3 son ficheros DISTINTOS, asi que un evento puede tener
           cobertura de una clase y no de la otra; en ese caso el residuo es
           indeterminado y NO debe rellenarse con area_ha (seria convertir
           "no se" en "todo es otros").
       (b) cl_x_km2 PUEDE SALIR NEGATIVA. Sustituye al chequeo [CHK-AREA] (a):
           cl_x_km2 < 0 es exactamente el caso cl12+cl3 > area_ha. NO se recorta
           a cero a proposito, para que el error quede visible en el dato y no
           solo en el log. Si es sistematico, sospechar que los tiles C12 y C3
           estan en rejillas distintas y se solapan en los bordes.
       (c) Se calcula con el area SIN redondear, no con area_ha (redondeada a 2
           decimales): redondear antes de restar inyectaria ruido gratuito en la
           unica columna que ya acumula todos los demas errores.
[MOD-ECO-7] UNIDAD EN EL NOMBRE. cl12 -> cl12_km2, cl3 -> cl3_km2, y analogos
     en la capa larga (cl12_eco_km2, cl3_eco_km2, cl_x_eco_km2). Recupera la
     convencion que [ECO-4] habia abandonado al pasar de m2 a km2 (cl12_m2 ->
     cl12). Las UNIDADES NO CAMBIAN: siguen siendo km2, identicas a la revision
     anterior; solo cambia el nombre. Con esto TODA columna de magnitud lleva su
     unidad: area_ha, ha_dom, eco_resid_ha, area_ha_eco, area_clase_km2,
     cl12_km2, cl3_km2, cl_x_km2.
     AVISO: rompe cualquier consulta que lea 'cl12' o 'cl3' por nombre.
[VER]    Prefijo de salida "GFA_V3" -> "GFA_V4" (constante VERSION_TAG).
         El salto de version es OBLIGATORIO, no cosmetico: V4 rompe el esquema
         de V3 en tres frentes, uno de ellos SILENCIOSO.
           - ruidoso : f12 / f3 desaparecen ([MOD-ECO-5]).
           - ruidoso : cl12 / cl3 -> cl12_km2 / cl3_km2 ([MOD-ECO-7]).
           - SILENCIOSO: 'ECO_NAME' cambia de SIGNIFICADO ([MOD-ECO-1]): antes
             era la ecorregion del representative_point, ahora la DOMINANTE POR
             AREA. Una consulta V3 sobre una salida V4 no falla: devuelve otra
             cosa. La etiqueta antigua se conserva en ECO_NAME_pt para poder
             cuantificar el trasvase.
[CHK-ECO] Por anio se imprime: distribucion de n_eco, % de eventos y % de AREA
     multi-ecorregion, area en la ecorregion NO dominante (cota inferior del
     area antes mal atribuida), eventos que CAMBIAN de etiqueta respecto a
     ECO_NAME_pt, y el balance del residuo. Un residuo NEGATIVO delata SOLAPES
     en la capa RESOLVE tras make_valid (hoy invisibles, porque un punto cae
     como mucho en un poligono). audit_eco_summary() re-tabula todo eso sobre
     una salida ya generada, con --audit-eco.
     AVISO: las areas POR ECORREGION no son comparables con las de V3. Las
     areas POR EVENTO ('area_ha') NO cambian entre V3 y V4.

======================= Cambios GFA_V3 (respecto a GFA_V2) =======================
[MOD-AREA-1] AREA GEODESICA. Toda medicion de area abandona EPSG:3857 y pasa a
     integracion sobre el elipsoide WGS84 (pyproj.Geod). Afecta a:
       - 'area_ha' del evento          (enrich_events)
       - 'cl12_km2' / 'cl3_km2'        (calc_class_clip)
       - 'cl_x_km2' otros / sin dato   (por diferencia, [MOD-ECO-6])
       - 'area_clase_km2'              (capa clip_clases)
     MOTIVO: Web Mercator es CONFORME, no equivalente. El factor de escala
     areal es sec^2(lat) x 1.0067, de modo que el area medida en 3857 esta
     inflada de forma DETERMINISTA y creciente hacia el sur.
     DIAGNOSTICO sobre la salida V2 (23 620 eventos, 18 ecorregiones):
       Purus varzea      (lat -2.4) : +0.85 %
       Ucayali moist f.  (lat -8.1) : +2.73 %
       Peruvian Yungas   (lat -11.3): +4.85 %
       Central And. puna (lat -15.3): +8.12 %
       TOTAL: 29 623 km2 (3857) vs 28 403 km2 (real) -> +4.30 % (+1 220 km2)
     El sesgo observado se ajusta al predicho por sec^2(lat) con r = 0.9986 y
     residuo maximo 0.46 pp (los residuos son todos positivos por convexidad:
     desigualdad de Jensen; los mayores estan en las ecorregiones de mayor
     dispersion latitudinal — Sechura desert, Peruvian Yungas, sin_region).
     IMPACTO: las CUOTAS porcentuales apenas se mueven (max +-0.33 pp), pero
       (a) las areas absolutas caian un 4.3 % de media;
       (b) los contrastes Andes/Amazonia estaban inflados +2.6 a +5.3 %
           (p.ej. Central Andean puna / Iquitos varzea: 0.3973 -> 0.3772);
       (c) HABIA UNA INVERSION DE RANKING: en V2 'Central Andean wet puna'
           figuraba 3a por area y 'Iquitos varzea' 4a; con area real el orden
           se invierte (12.85 % vs 12.81 % de cuota). Cualquier afirmacion
           sobre la 3a/4a ecorregion mas afectada derivada de V2 es incorrecta.
     Las areas de V2 NO son comparables con las de V3. Cierra [MEJORA-AREA].
[MOD-AREA-2] Se descarta ESRI:102033 (sugerido en la nota [MEJORA-AREA] de V2):
     su definicion real es '+proj=aea +ellps=aust_SA' sobre datum SAD69, lo que
     obliga a una transformacion de datum cuyo resultado depende de las rejillas
     PROJ instaladas en el nodo. Benchmark sobre 23 600 poligonos: Geod es a la
     vez EXACTO y mas rapido (1.48 s vs 1.79 s de un Albers/WGS84 a medida y
     9.19 s vs 10.70 s con poligonos de 20 000 vertices), asi que no hay
     contrapartida de rendimiento. Si se quisiera un CRS proyectado, el unico
     admisible es AREA_CRS_ALT (Albers a medida sobre WGS84, ver config).
[MOD-AREA-3] ORIENTACION DE ANILLOS (trampa de pyproj). Geod devuelve area CON
     SIGNO segun la orientacion del anillo: si se aplica abs() al total, los
     HUECOS se SUMAN en vez de restarse. Medido: poligono de 121.2878 km2 con
     hueco de 43.6636 km2 -> 164.9514 km2 sin orientar (error +112 %) frente a
     77.6242 km2 correcto. area_m2_geod() fuerza orient(sign=1.0) en el
     exterior y en cada parte de un MultiPolygon antes de medir. Critico aqui:
     los perimetros GFA tienen islas sin quemar y las uniones de recortes
     MapBiomas todavia mas.
[MOD-AREA-4] CENTROID_CRS se MANTIENE en EPSG:3857 (decision deliberada, no
     olvido). El centroide en Mercator se desplaza 0.03 m (evento de 5 km) a
     16 m (evento de 50 km, lat -18) respecto al geografico: despreciable
     frente a la incertidumbre de la frontera GAUL. Ademas el 'within' es
     autoconsistente porque centroide y poligono de Peru se transforman los
     dos. Cambiarlo alteraria el conjunto de eventos retenidos por [FIX-PAIS]
     sin ganancia. Se corrige el comentario '# coherente con area_ha', que ya
     no lo es.
[MOD-AREA-5] (DEROGADO por [MOD-ECO-5]: f12/f3 ya no se emiten.)
     NUEVAS COLUMNAS f12 / f3 = fraccion del evento cubierta por la
     clase MapBiomas 12 / 3, en [0, 1]. Son INSENSIBLES al CRS (numerador y
     denominador comparten el factor de escala, que se cancela: residuo
     estimado <0.11 % incluso a lat -18 con la clase concentrada en un
     extremo). Son por tanto la magnitud a preferir para comparar
     ecorregiones. NaN si cl12/cl3 es NaN (sin cobertura MapBiomas) o si
     area_ha <= 0.
[CHK-AREA] Dos verificaciones nuevas:
     (a) en enrich_events, aviso si cl12 + cl3 > area_ha (imposible: son
         clases MapBiomas disjuntas recortadas al mismo poligono; si salta,
         hay un problema de geometria o de unidades);
     (b) audit_area_bias(gpkg): funcion post-hoc, opcional, que recalcula el
         area en EPSG:3857 sobre la salida y tabula el sesgo por ECO_NAME
         contra el predicho por sec^2(lat). Sirve para reproducir el
         diagnostico de [MOD-AREA-1] en cualquier corrida futura. NO se
         ejecuta en el pipeline; se invoca con --audit-area.
[VER]    Prefijo de salida "GFA_V2" -> "GFA_V3".

======================= Correccion GFA_V2.1 =======================
[FIX-PAIS] El filtro de pais de BA_V11 (spatial_join_3attempts) asignaba
     'Peru' a poligonos que no estaban en Peru. Tres fallos acumulados:
       (a) predicate='intersects': bastaba rozar la frontera;
       (b) groupby(level=0).first() con ADM0_CODE=NaN -> '_match' siempre
           False, el desempate entre paises era el orden de filas del GAUL;
       (c) sjoin_nearest SIN max_distance: todo evento sin match recibia el
           pais mas cercano. Como Brasil no estaba en COUNTRIES_ADM0, los
           perimetros del oeste brasileño (leidos por el ROI hasta -60E)
           caian todos en 'Peru'.
     Diagnostico sobre la salida V1 (27202 eventos): 5034 con centroide
     fuera de Peru (18.5% de los eventos, 10.5% del area = 3382/32075 km2).
     Reparto: 5003 por (c) [Brasil], 27 por (b) [Colombia 18, Bolivia 7,
     Ecuador 2], 31 fronterizos por (a), 3 con centroide en el mar.
     Sesgo NO uniforme en el tiempo (27 eventos en 2009 vs 959 en 2022):
     cualquier tendencia temporal derivada de V1 esta invalidada.
     SOLUCION: filter_by_country_centroid(). Criterio unico y auditable:
     centroide (EPSG:3857) DENTRO del poligono Peru disuelto, predicate
     'within', SIN fallback. Fuera -> descartado, incluidos los 3 costeros.
[FIX-ROI]  ROI_BBOX -80.0 -> -82.0: el borde oeste cortaba Tumbes/Piura
     (Peru llega a -81.33). Con el filtro estricto eso pasaba de producir
     una etiqueta rara a perder eventos reales.
[FIX-CLIP] load_countries ya NO hace .clip(roi_geom) (mutilaba el poligono
     de Peru); filtra por interseccion tras reproyectar a EPSG:4326.
[FIX-ORDEN] El filtro de pais y el submuestreo se ejecutan en
     process_gfa_year ANTES de add_dem_median: el ~18% descartado ya no
     paga mediana zonal ni MapBiomas. enrich_events pierde el parametro
     'countries_gdf' y el bloque SAMPLE_FRAC.
     OJO: el submuestreo opera ahora sobre el subconjunto peruano, asi que
     las corridas de test NO son comparables con las de V1 aun con la
     misma semilla (cambia len(gdf) y con el todo el vector Bernoulli).
[FIX-GUARD] _preprocess_only valida que COUNTRY_FILTER este entre los
     paises cargados y avisa si el borde oeste no alcanza -81.33.

======================= Cambios GFA_V1 (respecto a GFA_V0) =======================
[ECO-1]  ZONIFICACION POR ECORREGIONES. Se reemplaza 'region-geografica.shp'
         (campo 'nombre', Sierra/Selva) por el GPKG 'Ecoreg_peru_VF.gpkg'
         (campo 'ECO_NAME'). La capa se autodetecta si el GPKG trae una sola
         (ver load_ecoregions(); portado de AT_V9). El GPKG cuelga de
         GFA_BASE/1_input/Ecorregions_2017_Peru/, NO del CLUSTER_ROOT.
[ECO-2]  SIN SELECCION DE VALORES. Desaparece el filtro isin([Sierra,Selva]):
         ahora se clasifican y CONSERVAN TODOS los perimetros de TODAS las
         ecorregiones. Cada evento recibe un ECO_NAME UNICO por su
         representative_point. Los perimetros en Peru fuera de toda ecorregion
         se conservan con ECO_NAME='sin_region'.
[ECO-3]  DOS CLASES PARA TODOS. Se elimina el mapeo region->clase. Cada evento
         recibe SIEMPRE las DOS areas MapBiomas:
             cl12_km2 = area de clase 12 (Grassland) en el poligono
             cl3_km2  = area de clase 3  (Forest)    en el poligono
             cl_x_km2 = resto por diferencia: 'otros / sin dato'
         calculadas sobre el POLIGONO COMPLETO (independientes del ECO_NAME).
[ECO-4]  UNIDADES km2 + nombres cl12/cl3 (antes cl12_m2/cl3_m2 en m2). El
         area se mide en EPSG:3857 y se divide por 1e6. La capa 'clip_clases'
         emite HASTA 2 filas por evento (clase 12 y clase 3) con 'area_clase_km2'.
[ECO-5]  INVERSION DE SEMANTICA NaN (igual que AT_V9/MOD-12): cl12=NaN (o cl3)
         significa AHORA, EXCLUSIVAMENTE, 'sin cobertura de raster MapBiomas'.
         En V0, cl12=NaN podia leerse como 'el evento es Selva'. Cualquier
         analisis aguas abajo que usara ese NaN como proxy de region debe
         revisarse.
[ECO-6]  Prefijo de salida "GFA_V0" -> "GFA_V1".

Base   : script_BA_V11.py (v11.3.0)
Logica : IGUAL que BA_V11 en enriquecimiento (pais, region geografica, DEM,
         MapBiomas clase 12/3, areas, salidas GPKG+CSV), pero la FUENTE de
         eventos cambia por completo: ya no se detectan desde el raster MODIS
         BA (MCD64A1); ahora cada evento es un POLIGONO de un shapefile GFA.

======================= Cambios GFA_V2 (respecto a BA_V11) =======================
[SRC-1]  FUENTE DE EVENTOS = shapefiles de perimetros GFA, uno por anio:
         '{PERIM_DIR}/GFA_v20250411_perimeters_{anio}.shp'. Cada FILA es un
         evento (poligono de area quemada). CRS declarado: EPSG:4326 (WGS84).
[SRC-2]  La fecha del evento sale de la columna 'start_date' (formato YYYY-MM-DD).
         De ella se derivan 'year' y 'month' (para igualar el analisis de
         MCD64A1, que trabajaba a nivel anio-mes). GFA NO trae DOY, asi que la
         columna 'BurnDate' de BA_V11 DESAPARECE; en su lugar se guarda
         'start_date' (fecha completa) en la salida.
[DROP-1] Se ELIMINA toda la deteccion de eventos por raster: lectura de bandas
         (meses), _label_by_doy, _extract_month_events, componentes conexas,
         cache de DEM reproyectado por grid y el uso de scipy.ndimage.
[DEM-1]  'dem_median' ya NO se calcula por labeled_comprehension sobre un DEM
         reproyectado a la grilla del raster. Ahora es una MEDIANA ZONAL por
         poligono: se lee la ventana del DEM que cubre el evento, se rasteriza
         el poligono y se toma np.median ignorando nodata del DEM. Si el DEM no
         cubre el evento -> NaN (misma semantica que antes).
[AOI-1]  save_cartographic_layers ya NO exporta rasters BA recortados al AOI
         (no hay raster de origen). Conserva el recorte del DEM y el GPKG+CSV de
         los eventos que caen en el AOI.
[COLS-NATIVE] Se CONSERVAN todas las columnas nativas del shapefile GFA. En la
         salida se emiten las columnas estandar (COL_ORDER) primero, luego las
         nativas y 'geometry' al final (_order_columns). Si una columna nativa
         choca con un nombre generado por el pipeline (p.ej. 'year'), la nativa
         se renombra con sufijo '_gfa'. La 'start_date' nativa no se altera.
[VER]    Prefijo de salida "V11" -> "GFA_V1".

--- KEEP (identico a BA_V11) ------------------------------------------------------
  - MapBiomas: tiles pre-extraidos clase 12/3, band_for_year anclando la ULTIMA
    banda a YEAR_MAX (2024). calc_class_clip sin cambios.
  - DEM bruto materializado una vez (ensure_dem_bruto), cache compartida con AT.
  - area_ha es del evento COMPLETO (no recortado al AOI). En V3 se mide de forma
    geodesica, ya NO en EPSG:3857 (ver [MOD-AREA-1]).
  - Salidas: GPKG (capas 'eventos' + 'clip_clases') + CSV de eventos, streaming
    en el merge, paralelizacion por anio (round-robin), sufijo _PARCIAL si fallan
    workers.

--- DECISIONES (resueltas) --------------------------------------------------------
  [D1] Rutas: GFA_BASE queda como PLACEHOLDER a editar con la ruta EXACTA en el
       cluster. DEM, MapBiomas, regiones y GAUL vuelven al cluster (CLUSTER_ROOT),
       identicos a BA_V11.
  [D2] 'start_date' en formato YYYY-MM-DD (parseo con format explicito + fallback).
  [D3] Sin BurnDate: GFA no trae DOY; se trabaja con start_date. event_uid =
       'GFA_{anio_fichero}_{idx:06d}' (anio = particion/nombre del fichero); las
       columnas year/month vienen de start_date. Suelen coincidir; se avisa si no.
  [D4] Periodo de estudio 2001-2024 (YEAR_MIN=2001, YEAR_MAX=2024). Eventos fuera
       de ese rango se descartan (avisando) para que band_for_year sea valido.

  [MEJORA-AREA] RESUELTA en V3 -> ver [MOD-AREA-1..5]. Nota historica: V2 dejaba
       3857 "por comparabilidad con el pipeline AT". Esa justificacion habia
       caducado: AT_V9.1.0 ([MOD-16]) ya habia abandonado 3857 por conforme y
       pasado a construccion geodesica. Mantener 3857 en GFA ROMPIA la
       comparabilidad en vez de conservarla.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
import rasterio.mask
import rasterio.features
import rasterio.warp
import rasterio.windows
import geopandas as gpd
from shapely.geometry import box, shape, MultiPolygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from pyproj import Geod
from collections import defaultdict
from rasterio.features import rasterize
from rasterio.mask import mask as rio_mask
from rasterio.windows import Window, from_bounds
from rasterio.crs import CRS
import re
import math
import subprocess
import sys
import argparse
import time
import gc
import logging
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.getLogger("rasterio").setLevel(logging.ERROR)

# --- Rutas -------------------------------------------------------------------
# GFA: producto de perimetros. #### EDITAR ####: pon aqui la ruta EXACTA del GFA
# en el cluster. El resto de subrutas (1_input, 3_output, ...) cuelga de esta.
GFA_BASE      = Path("/home/villaramosj/scratch_villaramosj/test_phd/data/GFA")   # <<< EDITAR
data_dir      = GFA_BASE / "1_input"
processed_dir = GFA_BASE / "2_processed"
output_dir    = GFA_BASE / "3_output"
test_dir      = GFA_BASE / "4_test"

# --- GFA: shapefiles de perimetros (1 por anio; cada fila = 1 evento) --------
PERIM_DIR        = data_dir / "SHP_perimeters"
PERIM_GLOB       = "GFA_*_perimeters_*.shp"   # fallback a "*.shp" si no matchea
START_DATE_FIELD = "start_date"               # columna con la fecha (YYYY-MM-DD)

# --- Inputs COMPARTIDOS con AT/BA (en el cluster; identicos a BA_V11) --------
# DEM, MapBiomas y regiones viven bajo MCD14ML; GAUL bajo MCD64A1. Se referencian
# por ruta absoluta para reutilizar los MISMOS ficheros que AT/BA sin duplicar.
CLUSTER_ROOT  = Path("/home/villaramosj/scratch_villaramosj/test_phd/data")
MCD14ML_INPUT = CLUSTER_ROOT / "MCD14ML" / "1_input"
MAPBIOMAS_DIR = CLUSTER_ROOT / "MCD14ML" / "3_output"
MAPBIOMAS_GRID = 3


def _tiles_for_class(stem):
    """Grilla 3x3 de rutas '{stem}_r{r}c{c}.tif'."""
    return {(r, c): MAPBIOMAS_DIR / f"{stem}_r{r}c{c}.tif"
            for r in range(MAPBIOMAS_GRID) for c in range(MAPBIOMAS_GRID)}


MAPBIOMAS_TILES_C12 = _tiles_for_class("clase12")   # grasslands (clase 12)
MAPBIOMAS_TILES_C3  = _tiles_for_class("clase3")    # forest     (clase 3)

# --- DEM bruto (patron AT_V8; cache compartida) [D1: VERIFICAR] --------------
DEM_PATH      = MCD14ML_INPUT / "mosaico_peru_bruto.tif"   # DEM materializado
RAW_TILES_DIR = MCD14ML_INPUT / "copernicus_dem_andes"     # None si ya tienes el .tif
DEM_BBOX      = (-81.5, -18.6, -68.5, 0.2)   # lon_min, lat_min, lon_max, lat_max
DEM_NODATA    = 0        # 0 = oceano/relleno (GLO30 via GEE no declara nodata)
DEM_REBUILD   = False
DEM_VALIDATE         = True
DEM_VALIDATE_WORKERS = 8

# --- Paises (GAUL) -----------------------------------------------------------
GAUL_PATH = CLUSTER_ROOT / "MCD64A1" / "1_input" / "GAUL_2024_L1.shp"

# --- Ecorregiones del Peru [ECO-1] (RESOLVE Ecoregions 2017 recortado) -------
# GPKG bajo GFA_BASE/1_input (NO en el CLUSTER_ROOT compartido). Campo ECO_NAME.
# ECO_LAYER=None -> autodeteccion; si el GPKG es multicapa, load_ecoregions aborta
# y lista las capas (evita el fallo silencioso de leer la 1a capa sin avisar).
ECO_PATH  = data_dir / "Ecorregions_2017_Peru" / "Ecoreg_peru_VF.gpkg"
ECO_FIELD = "ECO_NAME"
ECO_LAYER = None
SIN_REGION = "sin_region"   # [ECO-2] eventos en Peru fuera de toda ecorregion

# --- [MOD-ECO-1] Descomposicion evento -> ecorregiones -----------------------
# Prefijo de TODOS los ficheros de salida. UNICO punto de verdad: cambiarlo
# aqui basta para separar por completo una version de la siguiente.
VERSION_TAG = "GFA_V4"
# Nombre de la capa larga (1 fila por par evento-ecorregion).
ECO_LONG_LAYER = "eventos_eco"
# [MOD-ECO-3] Umbrales de sliver. Un trozo cuenta como ecorregion REAL solo si
# supera AMBOS. Sin esto, rozar una frontera inflaria n_eco y haria creer que el
# problema es mayor de lo que es. Lo descartado va a eco_resid_ha (no se pierde).
ECO_MIN_FRAC = 1e-3     # 0.1 % del area del evento
ECO_MIN_HA   = 0.5      # y ademas 0.5 ha en absoluto

# [ECO-3] Ambas clases MapBiomas para TODOS los eventos (ya no hay mapeo zona->clase).
CLASS_1 = 12   # cl12 = Grassland
CLASS_2 = 3    # cl3  = Forest

# --- Area de Interes (AOI) — solo recorte de capas de ejemplo ----------------
AOI_PATH = None
AOI_BBOX = (-73, -14, -72, -13)

# --- Parametros globales -----------------------------------------------------
ROI_BBOX = (-82.0, -20.0, -60.0, 1.0)   # era -80.0: cortaba Tumbes/Piura
YEAR_MIN       = 2001
YEAR_MAX       = 2024     # [D4] ancla de banda MapBiomas, NO el ultimo anio GFA
COUNTRIES_ADM0 = [207]                   # solo Peru: el centroide no necesita vecinos
COUNTRY_FILTER = "Peru"

# --- CRS del filtro de pais --------------------------------------------------
# [MOD-AREA-4] Se MANTIENE 3857 a proposito. El centroide en Mercator se
# desplaza <=16 m (evento de 50 km a lat -18) y el 'within' es autoconsistente
# porque centroide y poligono de Peru se transforman los dos. Cambiarlo
# alteraria el conjunto retenido por [FIX-PAIS] sin ganancia.
# NOTA: ya NO es "coherente con area_ha" — area_ha es geodesica desde V3.
CENTROID_CRS = "EPSG:3857"

# --- Medicion de area: GEODESICA sobre WGS84 ([MOD-AREA-1/2/3]) --------------
# Elipsoide WGS84, que es el datum en que YA estan los datos (GFA se declara
# EPSG:4326 y el pipeline normaliza a WGS84 en load_perimeters_year). Cero
# reproyecciones, cero ambiguedad de datum, exacto por construccion.
_GEOD = Geod(ellps="WGS84")

# CRS equivalente alternativo, SOLO por si se necesitara una version vectorizada
# (no se usa en el pipeline). Albers a medida sobre WGS84 con paralelos estandar
# ajustados a Peru; insesgado (p99 de desviacion vs Geod: 0.004 %). NO usar
# ESRI:102033: es SAD69 / elipsoide aust_SA, ver [MOD-AREA-2].
AREA_CRS_ALT = ("+proj=aea +lat_1=-5 +lat_2=-17 +lat_0=-10 +lon_0=-75 "
                "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
# CRS heredado de V2, conservado SOLO para audit_area_bias() ([CHK-AREA] b).
AREA_CRS_LEGACY = "EPSG:3857"


def area_m2_geod(geom):
    """Area elipsoidal WGS84 de una geometria en lon/lat, en m2.

    [MOD-AREA-3] Geod.geometry_area_perimeter devuelve area CON SIGNO segun la
    orientacion de cada anillo. Sin orientar, abs() del total SUMA los huecos
    en vez de restarlos (medido: 164.9514 km2 en vez de 77.6242 km2, +112 %).
    orient(sign=1.0) fuerza exterior CCW / huecos CW, con lo que la suma con
    signo cancela correctamente. Verificado tambien en MultiPolygon con huecos.

    Devuelve 0.0 para None, vacias o tipos no poligonales.
    """
    if geom is None or geom.is_empty:
        return 0.0
    gt = geom.geom_type
    if gt == "Polygon":
        geom = orient(geom, 1.0)
    elif gt == "MultiPolygon":
        geom = MultiPolygon([orient(g, 1.0) for g in geom.geoms
                             if (g is not None) and (not g.is_empty)])
        if geom.is_empty:
            return 0.0
    elif gt == "GeometryCollection":
        # Puede aparecer tras .intersection(): sumar solo las partes poligonales.
        return float(sum(area_m2_geod(g) for g in geom.geoms
                         if g.geom_type in ("Polygon", "MultiPolygon")))
    else:
        return 0.0
    return abs(_GEOD.geometry_area_perimeter(geom)[0])


def area_m2_geod_series(geoms):
    """area_m2_geod aplicada a un iterable de geometrias -> np.ndarray float64."""
    return np.fromiter((area_m2_geod(g) for g in geoms),
                       dtype=np.float64, count=len(geoms))

YEARS_RUN = list(range(YEAR_MIN, YEAR_MAX + 1))


def band_for_year(year, n_bands):
    """Indice de banda MapBiomas (1-based) anclando la ULTIMA banda a YEAR_MAX."""
    return year - YEAR_MAX + n_bands


MAPBIOMAS_EXTENT_BUFFER_DEG = 0.005
WGS84 = CRS.from_epsg(4326)
roi_geom = box(*ROI_BBOX)

# --- Submuestreo (solo tests) ------------------------------------------------
# SAMPLE_FRAC: fraccion de EVENTOS conservados por anio (muestreo Bernoulli).
#   Produccion: None. <1 = modo TEST.
SAMPLE_FRAC = None
RANDOM_SEED = 42

# --- Workers -----------------------------------------------------------------
N_WORKERS = int(os.environ.get("SLURM_CPUS_PER_TASK", 3))

# Orden de columnas de salida. La fecha del evento es 'start_date' (YYYY-MM-DD);
# GFA no trae DOY (BurnDate), asi que esa columna desaparece respecto a BA_V11.
# Las columnas NATIVAS del shapefile se CONSERVAN: se emiten despues de estas
# (ver _order_columns) y antes de 'geometry'.
COL_ORDER = [
    "event_uid", "year", "month", "start_date", "dem_median",
    "ECO_NAME", "ECO_NAME_pt", "n_eco", "ha_dom", "eco_resid_ha",
    "cl12_km2", "cl3_km2", "cl_x_km2", "ADM0_CODE", "gaul0_code",
    "gaul0_name", "area_ha", "geometry",
]

# [MOD-ECO-1] Orden de la capa larga 'eventos_eco'.
COL_ORDER_ECO = [
    "event_uid", "year", "month", "ECO_NAME", "es_dominante",
    "area_ha_eco", "cl12_eco_km2", "cl3_eco_km2", "cl_x_eco_km2",
    "geometry",
]

# Nombres que el pipeline GENERA. Si el shapefile nativo trae alguno de estos
# (salvo 'start_date', que ES nativa), la columna nativa se renombra con sufijo
# '_gfa' para no perder el dato ni pisar la columna calculada.
RESERVED_GENERATED = {
    "event_uid", "year", "month", "dem_median", "ECO_NAME",
    "ECO_NAME_pt", "n_eco", "ha_dom", "eco_resid_ha",
    "cl12_km2", "cl3_km2", "cl_x_km2", "ADM0_CODE", "gaul0_code", "gaul0_name",
    "area_ha", "index_right", "_match",
}


def _order_columns(gdf):
    """Devuelve las columnas ordenadas: primero las estandar de COL_ORDER que
    existan, luego las NATIVAS/extra (orden estable de aparicion) y 'geometry'
    al final. No descarta ninguna columna."""
    std = [c for c in COL_ORDER if c in gdf.columns and c != "geometry"]
    extra = [c for c in gdf.columns
             if c not in std and c != "geometry"]
    tail = ["geometry"] if "geometry" in gdf.columns else []
    return std + extra + tail


# --- Utilidades --------------------------------------------------------------
def timer(label, start):
    elapsed = time.time() - start
    print(f"  [OK] {label}: {timedelta(seconds=int(elapsed))} ({elapsed:.2f}s)")
    return time.time()


def run_tag_of(sample_frac):
    """Sufijo de archivos. None/>=1 -> 'vf' (full). <1 -> 'test_e{NN}pct'."""
    return ("vf" if (sample_frac is None or sample_frac >= 1.0)
            else f"test_e{int(sample_frac * 100):02d}pct")


def _to_local_crs(geom, dst_crs):
    """Reproyecta un shapely geom de WGS84 al CRS dado (si difiere)."""
    if dst_crs is None or dst_crs == WGS84:
        return geom
    return (gpd.GeoDataFrame([0], geometry=[geom], crs=WGS84)
            .to_crs(dst_crs).geometry.iloc[0])


def _round_window(win, width, height):
    """Redondea una Window flotante a enteros y la acota al raster."""
    col_off = max(0, int(math.floor(win.col_off)))
    row_off = max(0, int(math.floor(win.row_off)))
    w = int(math.ceil(win.col_off + win.width)) - col_off
    h = int(math.ceil(win.row_off + win.height)) - row_off
    w = max(0, min(w, width - col_off))
    h = max(0, min(h, height - row_off))
    return Window(col_off, row_off, w, h)


def _fix_geometry(geom):
    """Repara geometrias invalidas (autointersecciones) sin cambiar el area
    de forma significativa. Devuelve None si no se puede reparar."""
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid:
        return geom
    try:
        from shapely.validation import make_valid
        g = make_valid(geom)
    except Exception:
        g = geom.buffer(0)
    return g if (g is not None and not g.is_empty) else None


# --- AOI ---------------------------------------------------------------------
def load_aoi():
    if AOI_PATH is not None:
        aoi_gdf  = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
        aoi_geom = aoi_gdf.geometry.union_all()
        print(f"  AOI cartografica : {Path(AOI_PATH).name} ({len(aoi_gdf)} feat.)")
    elif AOI_BBOX is not None:
        w, s, e, n = AOI_BBOX
        aoi_geom = box(w, s, e, n)
        print(f"  AOI cartografica : bbox W={w} S={s} E={e} N={n}")
    else:
        print("  AOI cartografica : None")
        return None
    if not roi_geom.contains(aoi_geom):
        print("  [WARN] El AOI no esta totalmente dentro del ROI; las capas de "
              "ejemplo pueden quedar incompletas.")
    return aoi_geom


# --- [ECO-1] Ecorregiones: carga (portado de AT_V9) --------------------------
def load_ecoregions(eco_path, layer=ECO_LAYER, field=ECO_FIELD):
    """Carga el GPKG de ecorregiones, reproyecta a EPSG:4326 y devuelve un
    GeoDataFrame con [field, 'geometry'].

    - layer=None -> autodetecta. Si el GPKG tiene mas de una capa, aborta y las
      lista (un GPKG multicapa leido sin 'layer' devuelve la 1a SIN avisar:
      fallo silencioso que preferimos convertir en error).
    - Repara geometrias invalidas (RESOLVE Ecoregions trae self-intersections):
      make_valid con fallback a buffer(0).
    - NO se recorta al ROI: el representative_point de cada perimetro ya cae en
      Peru; recortar poligonos de ecorregion solo generaria slivers de borde.
    """
    eco_path = Path(eco_path)
    if not eco_path.exists():
        raise FileNotFoundError(f"No existe el GPKG de ecorregiones: {eco_path}")

    if layer is None:
        try:
            import fiona
            layers = fiona.listlayers(str(eco_path))
        except Exception:
            from pyogrio import list_layers
            layers = [str(x[0]) for x in list_layers(str(eco_path))]
        if len(layers) == 0:
            raise ValueError(f"El GPKG {eco_path.name} no contiene capas.")
        if len(layers) > 1:
            raise ValueError(
                f"El GPKG {eco_path.name} contiene {len(layers)} capas "
                f"{layers}. Fija ECO_LAYER explicitamente.")
        layer = layers[0]

    eco = gpd.read_file(eco_path, layer=layer)
    print(f"  Ecorregiones     : {eco_path.name} | capa='{layer}' | "
          f"{len(eco)} poligono(s) | CRS={eco.crs}")

    if field not in eco.columns:
        raise KeyError(
            f"El campo '{field}' no existe en la capa '{layer}'. "
            f"Columnas disponibles: {list(eco.columns)}")

    eco = eco[[field, "geometry"]].to_crs("EPSG:4326")
    eco = eco[eco.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()

    n_bad = int((~eco.geometry.is_valid).sum())
    if n_bad:
        print(f"  [WARN] {n_bad} geometria(s) invalida(s) -> reparando")
        try:
            eco["geometry"] = eco.geometry.make_valid()
        except AttributeError:
            eco["geometry"] = eco.geometry.buffer(0)

    n_null = int(eco[field].isna().sum())
    if n_null:
        print(f"  [WARN] {n_null} poligono(s) con {field} nulo -> los eventos "
              f"que caigan ahi quedaran como '{SIN_REGION}'")

    print(f"  Ecorregiones distintas en la capa: {eco[field].nunique()}")
    return eco.reset_index(drop=True)


def assign_ecoregion(gdf, eco_gdf, field=ECO_FIELD):
    """[ECO-2] Asigna ECO_NAME UNICO a cada evento por su representative_point.

    Los eventos cuyo punto no cae en ninguna ecorregion (o en un poligono con
    ECO_NAME nulo) quedan como SIN_REGION -> se CONSERVAN (no se descartan).
    Si el punto cae en frontera entre poligonos, se toma el primero.
    """
    pts = gpd.GeoDataFrame(
        geometry=gdf.geometry.representative_point(), crs=gdf.crs)
    j = pts.sjoin(
        eco_gdf[[field, "geometry"]],
        how="left", predicate="within",
    ).drop(columns=["index_right"], errors="ignore")
    j = j[~j.index.duplicated(keep="first")]
    vals = j[field].reindex(gdf.index)
    return vals.fillna(SIN_REGION).values


# --- [MOD-ECO-1] Descomposicion evento -> ecorregiones -----------------------
def decompose_ecoregion(gdf, eco_gdf, area_ha_evt, field=ECO_FIELD,
                        min_frac=ECO_MIN_FRAC, min_ha=ECO_MIN_HA):
    """Parte cada evento en trozos, uno por ecorregion que intersecta.

    `gdf`         : GeoDataFrame de eventos en WGS84, indice 0..n-1.
    `area_ha_evt` : array de areas geodesicas por evento (ha), ya calculadas.

    Devuelve (resumen, piezas):
      resumen : DataFrame 0..n-1 con eco_dom, n_eco, ha_dom, eco_resid_ha.
                [MOD-ECO-5] solo AREAS: la fraccion dominante es ha_dom/area_ha.
      piezas  : lista de listas; piezas[i] = [(eco_name, geom, area_ha), ...]
                ORDENADA de mayor a menor area. Incluye, si procede, un trozo
                SIN_REGION con el resto no cubierto, de modo que
                sum(area_ha) + 0 == area_ha_evt[i] por construccion.

    [MOD-ECO-4] Los eventos enteramente contenidos ('within') se resuelven por
    el indice espacial y no calculan ninguna interseccion.
    """
    n = len(gdf)
    piezas = [[] for _ in range(n)]
    resumen = pd.DataFrame({
        "eco_dom":      np.full(n, SIN_REGION, dtype=object),
        "n_eco":        np.zeros(n, dtype=np.int32),
        "ha_dom":       np.zeros(n, dtype=np.float64),
        "eco_resid_ha": np.asarray(area_ha_evt, dtype=np.float64).copy(),
    })
    if n == 0 or eco_gdf is None or len(eco_gdf) == 0:
        return resumen, piezas

    eco_gdf = eco_gdf.reset_index(drop=True)
    ev_geom = list(gdf.geometry.values)
    ec_geom = list(eco_gdf.geometry.values)
    ec_name = eco_gdf[field].fillna(SIN_REGION).astype(str).values

    base = gpd.GeoDataFrame(geometry=ev_geom, crs=WGS84)   # indice 0..n-1
    eco_min = eco_gdf[[field, "geometry"]]

    # --- Via rapida: evento ENTERAMENTE dentro de una ecorregion ------------
    w = base.sjoin(eco_min, how="inner", predicate="within")
    w = w[~w.index.duplicated(keep="first")]     # por si la capa tiene solapes
    dentro = set(int(i) for i in w.index)
    for i in w.index:
        i = int(i)
        nm = w.at[i, field]
        nm = str(nm) if pd.notna(nm) else SIN_REGION
        piezas[i].append((nm, ev_geom[i], float(area_ha_evt[i])))

    # --- Resto: interseccion par a par --------------------------------------
    resto = base.loc[~base.index.isin(dentro)]
    n_inter = 0
    if len(resto):
        pares = resto.sjoin(eco_min, how="inner", predicate="intersects")
        for ie, ic in zip(pares.index.to_numpy(), pares["index_right"].to_numpy()):
            ie, ic = int(ie), int(ic)
            g_ev = ev_geom[ie]
            if g_ev is None or g_ev.is_empty:
                continue
            try:
                inter = g_ev.intersection(ec_geom[ic])
            except Exception:
                try:
                    inter = g_ev.buffer(0).intersection(ec_geom[ic])
                except Exception:
                    continue
            n_inter += 1
            if inter is None or inter.is_empty:
                continue
            ha = area_m2_geod(inter) / 1e4        # 0.0 si es linea o punto
            if ha > 0.0:
                piezas[ie].append((ec_name[ic], inter, ha))

    # --- Consolidacion por evento -------------------------------------------
    for i in range(n):
        a_tot = float(area_ha_evt[i])
        if not piezas[i]:
            # Evento fuera de TODA ecorregion: un unico trozo SIN_REGION que
            # es el evento entero. Sin esto su area desapareceria de la capa
            # larga y el balance por ecorregion no cerraria.
            if a_tot > 0:
                piezas[i] = [(SIN_REGION, ev_geom[i], a_tot)]
            continue

        # Un evento puede tocar 2 poligonos de la MISMA ecorregion (multipartes).
        acc = {}
        for nm, g, ha in piezas[i]:
            if nm in acc:
                acc[nm] = (unary_union([acc[nm][0], g]), acc[nm][1] + ha)
            else:
                acc[nm] = (g, ha)

        # [MOD-ECO-3] Filtro de slivers: hay que superar frac Y absoluto.
        lim_ha = max(min_ha, min_frac * a_tot) if a_tot > 0 else min_ha
        keep = [(nm, g, ha) for nm, (g, ha) in acc.items() if ha >= lim_ha]
        keep.sort(key=lambda t: t[2], reverse=True)

        cub = float(sum(t[2] for t in keep))
        resid = a_tot - cub
        piezas[i] = keep
        resumen.at[i, "n_eco"] = len(keep)
        if keep:
            resumen.at[i, "eco_dom"] = keep[0][0]
            resumen.at[i, "ha_dom"]  = keep[0][2]
        resumen.at[i, "eco_resid_ha"] = resid

        # Trozo SIN_REGION explicito: cierra la particion del evento.
        if a_tot > 0 and resid >= lim_ha:
            try:
                if keep:
                    rest_g = ev_geom[i].difference(
                        unary_union([t[1] for t in keep]))
                else:
                    rest_g = ev_geom[i]
                if rest_g is not None and not rest_g.is_empty:
                    piezas[i].append((SIN_REGION, rest_g, resid))
            except Exception:
                pass   # sin geometria de residuo; eco_resid_ha ya lo registra

    print(f"  [MOD-ECO-1] descomposicion: {len(dentro)}/{n} eventos contenidos "
          f"(via rapida) | {n_inter} intersecciones calculadas")
    return resumen, piezas


# --- Carga / filtrado de paises ----------------------------------------------
def load_countries(path, adm0_codes, roi_geom):
    pays = gpd.read_file(path)
    pays = pays[pays["gaul0_code"].isin(adm0_codes)].copy()
    pays = pays[pays.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    pays = pays.to_crs("EPSG:4326")          # reproyectar ANTES de comparar
    pays = pays[pays.intersects(roi_geom)]   # roi_geom esta en EPSG:4326
    return pays.reset_index(drop=True)

# --- filet by country  -------------------------------------
def filter_by_country_centroid(gdf, countries_gdf, country_name=COUNTRY_FILTER):
    """Conserva SOLO los eventos cuyo CENTROIDE cae dentro de `country_name`.
    Sin fallback por proximidad ni por interseccion: fuera -> se descarta.
    Sustituye a spatial_join_3attempts (V1), cuyo sjoin_nearest sin max_distance
    asignaba 'Peru' a 5003 poligonos brasileños (18.5% del dataset)."""
    if gdf is None or len(gdf) == 0:
        return gdf

    tgt = countries_gdf[countries_gdf["gaul0_name"] == country_name]
    if tgt.empty:
        raise ValueError(f"'{country_name}' ausente de countries_gdf. "
                         f"Revisa COUNTRIES_ADM0 / GAUL_PATH.")
    code = tgt["gaul0_code"].iloc[0]
    pais = gpd.GeoDataFrame(
        geometry=[tgt.geometry.union_all()], crs=tgt.crs).to_crs(CENTROID_CRS)

    cent = gpd.GeoDataFrame(
        geometry=gdf.to_crs(CENTROID_CRS).geometry.centroid,
        index=gdf.index, crs=CENTROID_CRS)
    dentro = cent.sjoin(pais[["geometry"]], how="inner",
                        predicate="within").index.unique()

    n0 = len(gdf)
    out = gdf.loc[gdf.index.isin(dentro)].copy()
    out["gaul0_code"] = code
    out["gaul0_name"] = country_name
    out["ADM0_CODE"]  = code
    print(f"  [FILTRO PAIS] centroide en {country_name}: "
          f"{len(out)}/{n0} ({n0 - len(out)} descartados)")
    return out.reset_index(drop=True)

# --- Parseo del anio del nombre del shapefile --------------------------------
def _parse_year_from_name(path):
    """Anio de PARTICION del shapefile GFA. Se ancla al patron 'perimeters_AAAA'
    para NO confundirse con el AAAA de la version (p.ej. 'v20250411')."""
    m = re.search(r'perimeters[_-](\d{4})', path.stem, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Fallback: ultimo grupo de 4 digitos plausible como anio.
    for g in reversed(re.findall(r'(\d{4})', path.stem)):
        y = int(g)
        if 1900 <= y <= 2100:
            return y
    return None


# --- Descubrimiento de fuentes -----------------------------------------------
def discover_perimeter_year_files():
    """Dict {anio: ruta} de los shapefiles de perimetros GFA. No abre archivos."""
    files = sorted(PERIM_DIR.glob(PERIM_GLOB))
    if not files:
        files = sorted(PERIM_DIR.glob("*.shp"))
    out = {}
    for p in files:
        y = _parse_year_from_name(p)
        if y is not None:
            out[y] = p
    if not out and files:
        out = {YEAR_MIN + i: f for i, f in enumerate(files)}
        print("  [WARN] Anio no detectado en los nombres GFA; asignado por orden.")
    return out


def get_mapbiomas_metadata(tiles_map):
    """Metadatos de una grilla 3x3 de tiles pre-extraidos (logica AT V5).
    Devuelve dict {(r,c): {bounds_geom(WGS84), crs, nodata, bands}}."""
    meta = {}
    for key, path in tiles_map.items():
        if not path.exists():
            print(f"  [WARN] Tile MapBiomas ausente: {path.name}")
            continue
        try:
            with rasterio.open(path) as src:
                b   = src.bounds
                crs = src.crs or WGS84
                if CRS.from_user_input(crs) != WGS84:
                    b = rasterio.warp.transform_bounds(crs, WGS84, *b,
                                                       densify_pts=21)
                meta[key] = {
                    "bounds_geom": box(*b),
                    "crs"        : src.crs,
                    "nodata"     : src.nodata,
                    "bands"      : src.count,
                }
        except Exception as e:
            print(f"  [WARN] tile MapBiomas ilegible {path.name}: {e}")
    return meta


# --- DEM bruto: construccion cache (patron de AT_V8) -------------------------
def _tile_ok(path):
    """(path, True/False). Checksum de GDAL fuerza la lectura de TODOS los
    bloques; un TIFF truncado lanza excepcion y se marca invalido."""
    from osgeo import gdal
    gdal.UseExceptions()
    ds = None
    try:
        ds = gdal.Open(path)
        if ds is None:
            return path, False
        for b in range(1, ds.RasterCount + 1):
            ds.GetRasterBand(b).Checksum()
        return path, True
    except Exception:
        return path, False
    finally:
        ds = None


def ensure_dem_bruto(raw_tiles_dir, dem_out, bbox, nodata=0, rebuild=False,
                     validate=True, max_workers=8):
    """Garantiza un DEM bruto materializado y devuelve su ruta (str)."""
    dem_out = Path(dem_out)
    if dem_out.exists() and not rebuild:
        print(f"  [DEM] Reutilizando existente: {dem_out.name}")
        return str(dem_out)
    if dem_out.exists() and rebuild:
        print(f"  [DEM] rebuild=True -> regenerando {dem_out.name}")

    if raw_tiles_dir is None:
        raise FileNotFoundError(
            f"No existe {dem_out} y RAW_TILES_DIR=None: no hay de donde construir "
            f"el DEM. Provee el .tif o define RAW_TILES_DIR.")

    try:
        from osgeo import gdal
    except ImportError as e:
        raise ImportError(
            "Se requieren los bindings de GDAL (osgeo) para construir el DEM. "
            "En conda: conda install -c conda-forge gdal") from e
    gdal.UseExceptions()

    raw_tiles_dir = Path(raw_tiles_dir)
    dem_name_low = dem_out.name.lower()
    tiles = []
    for p in raw_tiles_dir.rglob("*.tif"):
        rel_parts = [x.lower() for x in p.relative_to(raw_tiles_dir).parts]
        if "output" in rel_parts:
            continue
        nm = p.name.lower()
        if nm.startswith("mosaico") or nm == dem_name_low:
            continue
        tiles.append(str(p))
    tiles.sort()
    if not tiles:
        raise FileNotFoundError(f"Sin tiles .tif en {raw_tiles_dir}")

    if validate:
        print(f"  [DEM] Validando integridad de {len(tiles)} tiles "
              f"({max_workers} hilos)...")
        bad = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for path, ok in ex.map(_tile_ok, tiles):
                if not ok:
                    bad.append(path)
        if bad:
            log = dem_out.parent / "tiles_corruptos.txt"
            log.write_text("\n".join(bad))
            print(f"  [DEM] {len(bad)} tile(s) CORRUPTO(S) excluido(s). "
                  f"Lista -> {log.name}")
            bad_set = set(bad)
            tiles = [t for t in tiles if t not in bad_set]
        else:
            print("  [DEM] Todos los tiles pasaron la validacion.")
        if not tiles:
            raise FileNotFoundError(
                "Todos los tiles fallaron la validacion; nada que mosaicar.")

    print(f"  [DEM] Construyendo desde {len(tiles)} tiles validos...")
    dem_out.parent.mkdir(parents=True, exist_ok=True)
    tmp_vrt = str(dem_out.with_suffix(".tmp.vrt"))
    tmp_tif = str(dem_out.with_suffix(".tmp.tif"))

    gdal.BuildVRT(
        tmp_vrt, tiles,
        options=gdal.BuildVRTOptions(
            outputBounds=list(bbox), srcNodata=nodata, VRTNodata=nodata,
            resampleAlg="nearest"))
    print(f"  [DEM] Materializando a {dem_out.name} (tiled+DEFLATE, streaming)...")
    try:
        gdal.Translate(
            tmp_tif, tmp_vrt,
            options=gdal.TranslateOptions(
                format="GTiff", noData=nodata,
                creationOptions=[
                    "COMPRESS=DEFLATE", "PREDICTOR=3", "ZLEVEL=6",
                    "TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512",
                    "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS"]))
        os.replace(tmp_tif, dem_out)
    except Exception:
        for f in (tmp_tif, tmp_vrt):
            try:
                os.remove(f)
            except OSError:
                pass
        raise
    finally:
        try:
            os.remove(tmp_vrt)
        except OSError:
            pass

    print(f"  [DEM] Listo: {dem_out}")
    return str(dem_out)


# --- Mediana ignorando NaN ---------------------------------------------------
def _nanmedian_safe(values):
    """Mediana de 'values' ignorando NaN; NaN si no queda ningun valido."""
    v = values[~np.isnan(values)]
    return float(np.median(v)) if v.size else np.nan


# --- DEM: mediana zonal por poligono (NUEVO en GFA_V2) -----------------------
def add_dem_median(gdf, dem_path, field="dem_median"):
    """Anade la MEDIANA de cota del DEM dentro de cada poligono.

    Para cada evento: se lee la ventana del DEM que lo cubre, se rasteriza el
    poligono (all_touched) y se toma la mediana de los pixeles bajo la mascara,
    ignorando el nodata del DEM. Si el DEM no cubre el evento -> NaN.
    Reemplaza el labeled_comprehension sobre el DEM reproyectado de BA_V11.
    """
    out = np.full(len(gdf), np.nan, dtype=np.float64)
    if len(gdf) == 0:
        gdf[field] = out
        return gdf

    with rasterio.open(dem_path) as dem:
        dcrs = dem.crs or WGS84
        nod  = dem.nodata
        geoms = (gdf.geometry.values if CRS.from_user_input(dcrs) == WGS84
                 else gdf.to_crs(dcrs).geometry.values)
        for i, geom in enumerate(geoms):
            if geom is None or geom.is_empty:
                continue
            minx, miny, maxx, maxy = geom.bounds
            win = from_bounds(minx, miny, maxx, maxy, transform=dem.transform)
            win = Window(win.col_off - 1, win.row_off - 1,
                         win.width + 2, win.height + 2)
            win = _round_window(win, dem.width, dem.height)
            if win.width <= 0 or win.height <= 0:
                continue
            try:
                arr = dem.read(1, window=win).astype(np.float32)
            except Exception:
                continue
            tr = dem.window_transform(win)
            mask = rasterize(
                [(geom, 1)], out_shape=(int(win.height), int(win.width)),
                transform=tr, fill=0, all_touched=True, dtype="uint8").astype(bool)
            if not mask.any():
                continue
            vals = arr[mask]
            if nod is not None:
                vals = vals[vals != nod]
            vals = vals[~np.isnan(vals)]
            if vals.size:
                out[i] = round(float(np.median(vals)), 1)
    gdf[field] = out
    return gdf


# --- Recorte geometrico de una clase por evento (BA ∩ claseK) ----------------
def calc_class_clip(polygons_gdf, year, mb_meta, tiles_map, target_class):
    """Para cada poligono devuelve (clip_geoms, area_km2).

    Semantica de area [ECO-4] (en km2; GEODESICA sobre WGS84 desde V3,
    antes EPSG:3857 -> ver [MOD-AREA-1]):
        NaN -> el evento NO cae en NINGUN tile de la clase (sin cobertura).
        0.0 -> cae en un tile pero sin pixeles de la clase objetivo.
        >0  -> area de la interseccion evento ∩ claseK, en km2.
    """
    n = len(polygons_gdf)
    clip_geoms = [None] * n
    area12 = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not mb_meta or year < YEAR_MIN or year > YEAR_MAX:
        return clip_geoms, area12

    geoms   = list(polygons_gdf.geometry.values)
    pieces  = defaultdict(list)
    covered = set()

    for (r, c), meta in mb_meta.items():
        tile_geom = meta["bounds_geom"]
        eidx = [i for i in range(n)
                if geoms[i] is not None and not geoms[i].is_empty
                and geoms[i].intersects(tile_geom)]
        if not eidx:
            continue

        band_idx = band_for_year(year, meta["bands"])
        if band_idx < 1 or band_idx > meta["bands"]:
            print(f"  [WARN] band_idx={band_idx} fuera de rango "
                  f"(1-{meta['bands']}) tile ({r},{c}) anio {year}")
            continue

        tcrs = meta["crs"] or WGS84
        tile_path = tiles_map[(r, c)]
        try:
            with rasterio.open(tile_path) as src:
                for g in eidx:
                    geom   = geoms[g]
                    geom_r = (_to_local_crs(geom, tcrs)
                              if tcrs != WGS84 else geom)
                    minx, miny, maxx, maxy = geom_r.bounds
                    win = from_bounds(minx, miny, maxx, maxy,
                                      transform=src.transform)
                    win = Window(win.col_off - 2, win.row_off - 2,
                                 win.width + 4, win.height + 4)
                    win = _round_window(win, src.width, src.height)
                    if win.width <= 0 or win.height <= 0:
                        continue
                    arr = src.read(band_idx, window=win)
                    tr  = src.window_transform(win)
                    covered.add(g)
                    is12 = (arr == target_class)
                    if not is12.any():
                        del arr, is12
                        continue
                    mask = is12.astype(np.uint8)
                    polys12 = [shape(gj) for gj, v in rasterio.features.shapes(
                        mask, mask=is12, transform=tr) if v == 1]
                    del arr, is12, mask
                    if not polys12:
                        continue
                    cls_geom = unary_union(polys12)
                    if tcrs != WGS84:
                        cls_geom = (gpd.GeoSeries([cls_geom], crs=tcrs)
                                    .to_crs(WGS84).iloc[0])
                    inter = cls_geom.intersection(geom)
                    if (inter is not None) and (not inter.is_empty):
                        pieces[int(g)].append(inter)
        except Exception as e:
            print(f"  [WARN] {type(e).__name__} clase{target_class} ({r},{c}) "
                  f"anio {year}: {e}")
        gc.collect()

    for i in covered:
        area12[i] = 0.0

    if pieces:
        idxs   = list(pieces.keys())
        merged = [unary_union(pieces[i]) for i in idxs]
        # [MOD-AREA-1] Area GEODESICA de la union de recortes (antes: 3857).
        #   'merged' ya esta en WGS84 (cls_geom se reproyecta arriba si el tile
        #   no lo estaba), asi que se mide directamente sobre el elipsoide.
        #   [MOD-AREA-3] area_m2_geod orienta los anillos: estas uniones de
        #   poligonos rasterizados a 30 m son las que mas huecos generan.
        ar = area_m2_geod_series(merged)          # m2
        for k, i in enumerate(idxs):
            clip_geoms[i] = merged[k]
            area12[i]     = round(float(ar[k]) / 1e6, 6)   # [ECO-4] -> km2

    return clip_geoms, area12


# --- Carga de perimetros GFA de UN anio (reemplaza la deteccion por raster) --
def load_perimeters_year(shp_path, file_year):
    """Lee un shapefile GFA y devuelve un GeoDataFrame de eventos en WGS84.

    Se CONSERVAN todas las columnas nativas del shapefile. Se AÑADEN:
    event_uid, year, month (derivadas de start_date). La 'start_date' nativa se
    mantiene sin modificar. Orden: estandar -> nativas -> geometry.
    - Lee SOLO los perimetros que intersectan el bbox del ROI (memoria acotada).
    - year/month se derivan de START_DATE_FIELD (formato YYYY-MM-DD).
    - Repara geometrias invalidas; descarta filas sin fecha valida o fuera del
      rango [YEAR_MIN, YEAR_MAX] (para que band_for_year de MapBiomas sea valido).
    """
    try:
        gdf = gpd.read_file(shp_path, bbox=ROI_BBOX)
    except Exception:
        # Fallback: leer completo y filtrar despues (si el driver no soporta bbox).
        gdf = gpd.read_file(shp_path)
        gdf = gdf[gdf.intersects(roi_geom)].copy()
    if gdf is None or len(gdf) == 0:
        return None

    # CRS: el usuario confirma EPSG:4326. Si no viene declarado, se asigna;
    # si viene otro, se reproyecta.
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    elif CRS.from_user_input(gdf.crs) != WGS84:
        gdf = gdf.to_crs(WGS84)

    if START_DATE_FIELD not in gdf.columns:
        raise KeyError(
            f"Columna de fecha '{START_DATE_FIELD}' no encontrada en "
            f"{Path(shp_path).name}. Columnas: {list(gdf.columns)}")

    # Guard de colisiones: si una columna NATIVA se llama igual que una columna
    # que el pipeline GENERA (year, month, event_uid, area_ha, ...), se renombra
    # la nativa con sufijo '_gfa' para conservar el dato sin pisar el calculado.
    clashes = [c for c in gdf.columns if c in RESERVED_GENERATED]
    if clashes:
        ren = {c: f"{c}_gfa" for c in clashes}
        # Evitar que el nuevo nombre tambien exista ya.
        ren = {k: v for k, v in ren.items() if v not in gdf.columns}
        if ren:
            gdf = gdf.rename(columns=ren)
            print(f"  [INFO] {Path(shp_path).name}: columnas nativas renombradas "
                  f"para no colisionar con las calculadas: {ren}")

    # Formato esperado YYYY-MM-DD; el fallback recupera valores con hora u otro
    # formato sin romper (los irrecuperables quedan NaT y se descartan).
    dt = pd.to_datetime(gdf[START_DATE_FIELD], format="%Y-%m-%d",
                        errors="coerce")
    miss = dt.isna()
    if miss.any():
        dt.loc[miss] = pd.to_datetime(
            gdf.loc[miss, START_DATE_FIELD], errors="coerce")
    n_bad = int(dt.isna().sum())
    if n_bad:
        print(f"  [WARN] {Path(shp_path).name}: {n_bad} fila(s) con "
              f"'{START_DATE_FIELD}' no parseable -> descartadas.")
    ok = dt.notna()
    gdf, dt = gdf.loc[ok].copy(), dt.loc[ok]

    # year/month se DERIVAN de la fecha, pero la columna 'start_date' NATIVA se
    # conserva TAL CUAL (no se reescribe), para no alterar el dato original.
    gdf["year"]  = dt.dt.year.astype(int)
    gdf["month"] = dt.dt.month.astype(int)

    # Geometria valida y de tipo poligonal.
    gdf["geometry"] = gdf.geometry.apply(_fix_geometry)
    gdf = gdf[gdf.geometry.notna()]
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    gdf = gdf[~gdf.geometry.is_empty]

    # Rango de anios (validez de banda MapBiomas).
    in_range = gdf["year"].between(YEAR_MIN, YEAR_MAX)
    n_out = int((~in_range).sum())
    if n_out:
        print(f"  [WARN] {Path(shp_path).name}: {n_out} evento(s) fuera de "
              f"[{YEAR_MIN},{YEAR_MAX}] -> descartados.")
    gdf = gdf[in_range].reset_index(drop=True)
    if len(gdf) == 0:
        return None

    # Aviso si el anio de start_date no coincide con el anio del fichero.
    n_mismatch = int((gdf["year"] != file_year).sum())
    if n_mismatch:
        print(f"  [INFO] {Path(shp_path).name}: {n_mismatch} evento(s) con "
              f"year(start_date) != {file_year} (anio del fichero). Se usa "
              f"year(start_date) en las columnas.")

    # event_uid unico y trazable al fichero de origen.
    gdf["event_uid"] = [f"GFA_{file_year}_{i:06d}" for i in range(len(gdf))]

    # Se CONSERVAN todas las columnas nativas del shapefile; solo se reordenan
    # (estandar primero, nativas despues, geometry al final).
    return gdf[_order_columns(gdf)].copy()


# --- Enriquecimiento de los eventos de un anio -------------------------------
def enrich_events(gdf, eco_gdf, mb_meta_c12, mb_meta_c3, year):
    """
    [FIX-PAIS] Ya NO filtra pais ni submuestrea: ambos pasos ocurren en
    process_gfa_year, antes del DEM. Aqui solo ecorregion + area + MapBiomas.
    Las columnas gaul0_code/gaul0_name/ADM0_CODE llegan ya rellenadas por
    filter_by_country_centroid.

    sjoin pais + COUNTRY_FILTER + ecorregion + area + DOS clases MapBiomas.

    [ECO-2/ECO-3] Ya NO se filtra por region: se conservan TODOS los eventos de
    Peru (todas las ecorregiones + 'sin_region'), y cada uno recibe SIEMPRE
    cl12_km2 y cl3_km2 calculadas sobre el poligono completo.

    Recibe un GeoDataFrame de eventos (ya con event_uid/year/month/start_date/
    dem_median). Devuelve (gdf, gdf_clip). gdf_clip lleva HASTA 2 filas por
    evento (clase 12 y clase 3).
    """
    if gdf is None or len(gdf) == 0:
        return None, None

    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=WGS84).copy()
    gdf = gdf.reset_index(drop=True)   # decompose_ecoregion indexa por posicion

    # [MOD-AREA-1] Area GEODESICA (elipsoide WGS84), ya NO en EPSG:3857.
    #   V2: gdf.to_crs("EPSG:3857").geometry.area -> inflada sec^2(lat)*1.0067,
    #   es decir +0.85 % en Purus varzea y +8.1 % en Central Andean puna.
    #   Se calcula ANTES de la ecorregion porque la descomposicion la necesita.
    area_ha_evt = area_m2_geod_series(gdf.geometry.values) / 10_000
    gdf["area_ha"] = np.round(area_ha_evt, 2)

    # [MOD-ECO-1] Descomposicion en trozos por ecorregion. ECO_NAME pasa a ser
    #   la DOMINANTE POR AREA; la etiqueta antigua (por representative_point) se
    #   conserva en ECO_NAME_pt para poder auditar el trasvase.
    gdf["ECO_NAME_pt"] = assign_ecoregion(gdf, eco_gdf)
    resumen_eco, piezas_eco = decompose_ecoregion(gdf, eco_gdf, area_ha_evt)
    gdf["ECO_NAME"]     = resumen_eco["eco_dom"].values
    gdf["n_eco"]        = resumen_eco["n_eco"].values
    gdf["ha_dom"]       = np.round(resumen_eco["ha_dom"].values, 2)
    gdf["eco_resid_ha"] = np.round(resumen_eco["eco_resid_ha"].values, 2)

    n = len(gdf)
    gdf["cl12_km2"] = np.nan
    gdf["cl3_km2"]  = np.nan
    # Geometria de recorte por clase (para la capa clip_clases): 2 slots/evento.
    clip_geom_12 = [None] * n
    clip_geom_3  = [None] * n

    # [ECO-3] AMBAS clases para TODOS los eventos (ya no hay mapeo zona->clase).
    #   (columna destino, clase objetivo, tiles, meta, slot de geometria de clip)
    plan = (
        ("cl12_km2", CLASS_1, MAPBIOMAS_TILES_C12, mb_meta_c12, clip_geom_12),
        ("cl3_km2",  CLASS_2, MAPBIOMAS_TILES_C3,  mb_meta_c3,  clip_geom_3),
    )
    for col, target_class, tiles_map, mb_meta, clip_slot in plan:
        if not mb_meta:
            continue
        geoms_c, area_c = calc_class_clip(gdf, year, mb_meta,
                                          tiles_map, target_class)
        gdf[col] = area_c                     # km2 (NaN=sin cobertura, 0=sin pixeles)
        for i in range(n):
            g = geoms_c[i]
            if g is not None and not g.is_empty:
                clip_slot[i] = g

    gdf["cl12_km2"] = np.round(gdf["cl12_km2"].astype(float), 6)
    gdf["cl3_km2"]  = np.round(gdf["cl3_km2"].astype(float), 6)

    # [MOD-ECO-6] Tercera clase "OTROS / SIN DATO" por DIFERENCIA (km2).
    #   NO es una cobertura observada: mezcla lo que MapBiomas clasifico como
    #   otra cosa con lo que dejo en nodata. Etiquetarla siempre asi.
    #   Se usa el area SIN redondear (area_ha_evt) para no arrastrar el redondeo
    #   a 2 decimales de area_ha al residuo.
    #   [MOD-ECO-6] (b) NaN se propaga: si NO hay cobertura MapBiomas de una de
    #   las dos clases (tiles C12 y C3 son ficheros distintos), el residuo es
    #   indeterminado. np.nan + x = nan hace el trabajo sin enmascarar nada.
    area_km2 = area_ha_evt / 100.0
    gdf["cl_x_km2"] = np.round(
        area_km2 - gdf["cl12_km2"].astype(float).values
                 - gdf["cl3_km2"].astype(float).values, 6)

    # [MOD-ECO-6] (b) Sustituye a [CHK-AREA] (a):
    #   cl_x_km2 < 0  <=>  cl12_km2 + cl3_km2 > area.
    #   NO se recorta a cero: el error debe quedar visible en el dato.
    #   Tolerancia 0.5 % del area: la rasterizacion MapBiomas a 30 m puede
    #   desbordar marginalmente el borde del poligono.
    with np.errstate(invalid="ignore"):
        bad = gdf["cl_x_km2"].values < -(area_km2 * 0.005)
    n_bad = int(np.count_nonzero(np.nan_to_num(bad, nan=False)))
    if n_bad:
        peor = float(np.nanmin(gdf["cl_x_km2"].values[bad] /
                               np.where(area_km2[bad] > 0, area_km2[bad], np.nan)))
        print(f"  [CHK-ECO] {n_bad}/{n} evento(s) con cl_x_km2 NEGATIVA "
              f"(cl12_km2+cl3_km2 > area_ha; peor caso {100.0*peor:.2f} % del area). "
              f"Revisar geometrias.")
    n_nan = int(np.count_nonzero(np.isnan(gdf["cl_x_km2"].values)))
    if n_nan:
        print(f"  [CHK-ECO] {n_nan}/{n} evento(s) con cl_x = NaN "
              f"(sin cobertura MapBiomas de al menos una clase)")

    # [ECO-4] clip_clases: HASTA 2 filas por evento (una por clase con geometria).
    rows_uid, rows_year, rows_month = [], [], []
    rows_class, rows_area, rows_geom = [], [], []
    for label, target_class, clip_slot, col in (
            ("clase 12", CLASS_1, clip_geom_12, "cl12_km2"),
            ("clase 3",  CLASS_2, clip_geom_3,  "cl3_km2")):
        for i in range(n):
            g = clip_slot[i]
            if g is None or g.is_empty:
                continue
            rows_uid.append(gdf["event_uid"].values[i])
            rows_year.append(gdf["year"].values[i])
            rows_month.append(gdf["month"].values[i])
            rows_class.append(label)
            rows_area.append(gdf[col].values[i])
            rows_geom.append(g)

    if rows_geom:
        gdf_clip = gpd.GeoDataFrame(
            {
                "event_uid":      rows_uid,
                "year":           rows_year,
                "month":          rows_month,
                "clase":          rows_class,
                "area_clase_km2": rows_area,
            },
            geometry=rows_geom, crs=WGS84)
    else:
        gdf_clip = None

    # [MOD-ECO-1/2] Capa LARGA: 1 fila por par evento-ecorregion -------------
    gdf_eco = _build_eco_long(gdf, piezas_eco, clip_geom_12, clip_geom_3)
    _report_eco(gdf, year)

    return gdf, gdf_clip, gdf_eco


def _build_eco_long(gdf, piezas, clip_geom_12, clip_geom_3):
    """[MOD-ECO-2] Capa larga con MapBiomas POR TROZO.

    cl12_eco NO es un reparto proporcional de cl12 (eso asumiria cobertura
    uniforme dentro del evento y aplanaria justo el contraste que se quiere
    medir). Se obtiene intersectando la geometria de recorte del evento con el
    trozo de ecorregion: por asociatividad de la interseccion,
        (evento ∩ clase12) ∩ eco  ==  evento ∩ (clase12 ∩ eco),
    de modo que el resultado es identico a re-rasterizar por trozo, con CERO
    lecturas raster adicionales.

    Semantica NaN heredada de [ECO-5]: si cl12 del evento es NaN (sin cobertura
    MapBiomas), cl12_eco es NaN en todos sus trozos.
    """
    n = len(gdf)
    uid  = gdf["event_uid"].values
    yr   = gdf["year"].values
    mo   = gdf["month"].values
    dom  = gdf["ECO_NAME"].values
    cl12 = gdf["cl12_km2"].astype(float).values
    cl3  = gdf["cl3_km2"].astype(float).values
    n_eco = gdf["n_eco"].values

    r_uid, r_yr, r_mo, r_nm, r_dom = [], [], [], [], []
    r_ha, r_c12, r_c3, r_geom = [], [], [], []

    for i in range(n):
        for nm, g, ha in piezas[i]:
            if g is None or getattr(g, "is_empty", True):
                continue
            # Via rapida SOLO si el trozo ES el evento completo (un unico trozo
            # y no es el residuo). Con n_eco<=1 NO basta: un evento mitad en una
            # ecorregion y mitad fuera tiene n_eco=1 pero DOS trozos, y atribuir
            # el cl12 entero al trozo de ecorregion seria un error del 100 %.
            unico = (len(piezas[i]) == 1) and (nm != SIN_REGION)
            for src_cl, slot, dest in ((cl12[i], clip_geom_12, r_c12),
                                       (cl3[i],  clip_geom_3,  r_c3)):
                if np.isnan(src_cl):
                    dest.append(np.nan)              # sin cobertura MapBiomas
                elif unico:
                    dest.append(float(src_cl))       # el trozo ES el evento
                else:
                    cg = slot[i]
                    if cg is None or cg.is_empty:
                        dest.append(0.0)
                    else:
                        try:
                            dest.append(round(
                                area_m2_geod(cg.intersection(g)) / 1e6, 6))
                        except Exception:
                            dest.append(np.nan)
            r_uid.append(uid[i]); r_yr.append(yr[i]); r_mo.append(mo[i])
            r_nm.append(nm); r_dom.append(bool(nm == dom[i]))
            r_ha.append(round(float(ha), 2)); r_geom.append(g)

    if not r_geom:
        return None

    out = gpd.GeoDataFrame(
        {
            "event_uid":   r_uid,
            "year":        r_yr,
            "month":       r_mo,
            "ECO_NAME":    r_nm,
            "es_dominante": r_dom,
            "area_ha_eco": r_ha,
            "cl12_eco_km2":    r_c12,
            "cl3_eco_km2":     r_c3,
        },
        geometry=r_geom, crs=WGS84)

    # [MOD-ECO-6] "Otros / sin dato" por diferencia, dentro de CADA trozo.
    out["cl_x_eco_km2"] = np.round(
        out["area_ha_eco"].astype(float).values / 100.0
        - out["cl12_eco_km2"].astype(float).values
        - out["cl3_eco_km2"].astype(float).values, 6)

    # [CHK-ECO] Balance MapBiomas: la suma de los trozos debe reproducir cl12.
    #   OJO: solo se cumple si el evento quedo enteramente particionado. Los
    #   slivers descartados por [MOD-ECO-3] no generan fila, asi que su clase
    #   se pierde de la suma; de ahi la tolerancia relativa de 0.5 %.
    chk = out.groupby("event_uid")[["cl12_eco_km2", "cl3_eco_km2"]].sum(min_count=1)
    ref = (gdf.set_index("event_uid")[["cl12_km2", "cl3_km2"]]
              .reindex(chk.index).astype(float))
    for a, b in (("cl12_eco_km2", "cl12_km2"), ("cl3_eco_km2", "cl3_km2")):
        d = np.abs(chk[a].values - ref[b].values)
        tol = np.maximum(1e-6, 0.005 * np.nan_to_num(ref[b].values))
        nb = int(np.count_nonzero(d > tol))
        if nb:
            print(f"  [CHK-ECO] {nb} evento(s) con sum({a}) != {b} "
                  f"(desv. max {np.nanmax(d):.6f} km2). Revisar geometrias.")

    return out[[c for c in COL_ORDER_ECO if c in out.columns]]


def _report_eco(gdf, year):
    """[CHK-ECO] Diagnostico por anio del impacto de la descomposicion."""
    n = len(gdf)
    ha = gdf["area_ha"].astype(float).values
    ha_tot = float(np.nansum(ha))
    if n == 0 or ha_tot <= 0:
        return
    m = gdf["n_eco"].values >= 2
    ha_dom = np.nan_to_num(gdf["ha_dom"].astype(float).values, nan=0.0)
    ha_no_dom = float(np.nansum(ha[m] - ha_dom[m]))
    dif = (gdf["ECO_NAME"].astype(str).values !=
           gdf["ECO_NAME_pt"].astype(str).values)
    resid = float(np.nansum(gdf["eco_resid_ha"].values))
    neg = int(np.count_nonzero(gdf["eco_resid_ha"].values < -0.01))

    print(f"  [CHK-ECO] anio {year}: multi-ecorregion "
          f"{int(m.sum())}/{n} eventos ({100.0*m.sum()/n:.2f} %), "
          f"{100.0*float(np.nansum(ha[m]))/ha_tot:.2f} % del area")
    print(f"  [CHK-ECO]   area en ecorregion NO dominante: "
          f"{ha_no_dom/100:,.1f} km2 ({100.0*ha_no_dom/ha_tot:.2f} % del total)")
    print(f"  [CHK-ECO]   cambian de etiqueta vs ECO_NAME_pt: "
          f"{int(dif.sum())} ({100.0*dif.sum()/n:.2f} %), "
          f"area {100.0*float(np.nansum(ha[dif]))/ha_tot:.2f} %")
    print(f"  [CHK-ECO]   residuo fuera de toda ecorregion: "
          f"{resid/100:,.1f} km2 ({100.0*resid/ha_tot:.2f} %)"
          + (f" | [WARN] {neg} residuo(s) NEGATIVO(s) -> SOLAPE en RESOLVE"
             if neg else ""))


# --- Procesamiento de un anio (un shapefile GFA) -----------------------------
def process_gfa_year(year, shp_path, countries_gdf, eco_gdf,
                     mb_meta_c12, mb_meta_c3, dem_path,
                     gpkg_path, clip_gpkg_path, fw_main, fw_clip,
                     eco_gpkg_path=None, fw_eco=True):
    t0 = time.time()

    gdf0 = load_perimeters_year(shp_path, year)
    if gdf0 is None or len(gdf0) == 0:
        print(f"  [INFO] anio {year}: 0 perimetros en el ROI "
              f"({timedelta(seconds=int(time.time() - t0))}).")
        return fw_main, fw_clip, fw_eco

    # [FIX-PAIS] Filtro de pais ANTES del DEM/MapBiomas: los eventos cuyo
    # centroide cae fuera de Peru se descartan aqui y no pagan coste zonal.
    gdf0 = filter_by_country_centroid(gdf0, countries_gdf, COUNTRY_FILTER)
    if len(gdf0) == 0:
        print(f"  [INFO] anio {year}: 0 eventos con centroide en "
              f"{COUNTRY_FILTER} ({timedelta(seconds=int(time.time() - t0))}).")
        return fw_main, fw_clip, fw_eco

    # Submuestreo opcional (tests): tambien antes del DEM.
    if SAMPLE_FRAC is not None and SAMPLE_FRAC < 1.0:
        rng  = np.random.default_rng(np.random.SeedSequence([RANDOM_SEED, year]))
        keep = rng.random(len(gdf0)) < SAMPLE_FRAC
        gdf0 = gdf0.loc[keep].reset_index(drop=True)
        print(f"  [TEST] submuestreo {SAMPLE_FRAC}: {len(gdf0)} eventos")
        if len(gdf0) == 0:
            return fw_main, fw_clip, fw_eco, fw_eco

    # Mediana de cota por poligono (mediana zonal desde el DEM).
    gdf0 = add_dem_median(gdf0, dem_path)

    gdf, gdf_clip, gdf_eco = enrich_events(
        gdf0, eco_gdf, mb_meta_c12, mb_meta_c3, year)

    if gdf is None or len(gdf) == 0:
        print(f"  [INFO] anio {year}: 0 eventos tras enriquecer "
              f"({timedelta(seconds=int(time.time() - t0))}).")
        return fw_main, fw_clip, fw_eco

    # Se conservan TODAS las columnas (nativas incluidas); solo se reordenan.
    gdf = gdf[_order_columns(gdf)]
    gdf.to_file(gpkg_path, driver="GPKG", mode="w" if fw_main else "a")
    fw_main = False

    n_clip = 0
    if gdf_clip is not None and len(gdf_clip):
        gdf_clip.to_file(clip_gpkg_path, driver="GPKG",
                         mode="w" if fw_clip else "a")
        fw_clip = False
        n_clip = len(gdf_clip)

    # [MOD-ECO-1] Capa larga evento-ecorregion.
    n_eco_rows = 0
    if eco_gpkg_path is not None and gdf_eco is not None and len(gdf_eco):
        gdf_eco.to_file(eco_gpkg_path, driver="GPKG",
                        mode="w" if fw_eco else "a")
        fw_eco = False
        n_eco_rows = len(gdf_eco)

    print(f"  [OK] anio {year}: {len(gdf)} eventos (+{n_clip} recortes clase12/3, "
          f"+{n_eco_rows} trozos eco) "
          f"-> {gpkg_path.name} ({timedelta(seconds=int(time.time() - t0))})")
    del gdf, gdf_clip, gdf_eco, gdf0
    gc.collect()
    return fw_main, fw_clip, fw_eco


# --- Worker (subconjunto de anios) -------------------------------------------
def run_worker(worker_id, n_workers):
    t0 = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)
    print(f"\n{'='*58}\n  WORKER {worker_id}/{n_workers} — {VERSION_TAG}")
    _modo = "PRODUCCION" if run_tag == "vf" else f"TEST (eventos={SAMPLE_FRAC})"
    print(f"  MODO: {_modo}\n{'='*58}")

    year_files = discover_perimeter_year_files()
    years = sorted(year_files)
    my_years = years[worker_id::n_workers]
    print(f"  Anios totales: {len(years)} | este worker: {my_years}")
    if not my_years:
        print("  [INFO] Sin anios asignados.")
        return

    countries_gdf = load_countries(GAUL_PATH, COUNTRIES_ADM0, roi_geom)
    eco_gdf = load_ecoregions(ECO_PATH, layer=ECO_LAYER, field=ECO_FIELD)
    print(f"  Ecorregiones cargadas: {eco_gdf[ECO_FIELD].nunique()} clases "
          f"(TODAS conservadas; fuera de poligono -> '{SIN_REGION}')")
    mb_meta_c12 = get_mapbiomas_metadata(MAPBIOMAS_TILES_C12)
    mb_meta_c3  = get_mapbiomas_metadata(MAPBIOMAS_TILES_C3)
    print(f"  Tiles clase12: {len(mb_meta_c12)}/{len(MAPBIOMAS_TILES_C12)} | "
          f"clase3: {len(mb_meta_c3)}/{len(MAPBIOMAS_TILES_C3)}")
    dem_path = ensure_dem_bruto(RAW_TILES_DIR, DEM_PATH, DEM_BBOX,
                                nodata=DEM_NODATA, rebuild=False, validate=False)

    gpkg_path = test_dir / f"{VERSION_TAG}_{run_tag}_w{worker_id}.gpkg"
    clip_path = test_dir / f"{VERSION_TAG}_{run_tag}_w{worker_id}_clip.gpkg"
    eco_path  = test_dir / f"{VERSION_TAG}_{run_tag}_w{worker_id}_eco.gpkg"
    test_dir.mkdir(parents=True, exist_ok=True)
    for p in (gpkg_path, clip_path, eco_path):
        if p.exists():
            p.unlink()

    fw_main, fw_clip, fw_eco = True, True, True
    for yr in my_years:
        fw_main, fw_clip, fw_eco = process_gfa_year(
            yr, year_files[yr], countries_gdf, eco_gdf,
            mb_meta_c12, mb_meta_c3, dem_path,
            gpkg_path, clip_path, fw_main, fw_clip,
            eco_gpkg_path=eco_path, fw_eco=fw_eco)

    print(f"  TOTAL worker {worker_id}: "
          f"{timedelta(seconds=int(time.time() - t0))}")


# --- Merge + exportacion final -----------------------------------------------
def merge_and_export(run_tag, n_workers, base_name=None):
    t = time.time()
    print(f"\n{'-'*58}\n  Merge de resultados de workers\n{'-'*58}")

    worker_paths = [test_dir / f"{VERSION_TAG}_{run_tag}_w{k}.gpkg"
                    for k in range(n_workers)]
    existing = [p for p in worker_paths if p.exists()]
    if not existing:
        print("  [WARN] No hay GPKGs de workers para merge.")
        return None
    missing = [p.name for p in worker_paths if not p.exists()]
    if missing:
        print(f"  [INFO] Workers sin GPKG (vacios o fallidos): {missing}")

    base     = base_name or f"{VERSION_TAG}_{run_tag}"
    os.makedirs(output_dir, exist_ok=True)
    gpkg_out = output_dir / f"{base}.gpkg"
    csv_out  = output_dir / f"{base}.csv"
    for p in (gpkg_out, csv_out):
        if p.exists():
            p.unlink()

    print(f"  Escribiendo {len(existing)} GPKGs en streaming...")
    total, first = 0, True
    for p in existing:
        g = gpd.read_file(p)
        g = g[_order_columns(g)]   # conserva columnas nativas; solo reordena
        g.to_file(gpkg_out, layer="eventos", driver="GPKG",
                  mode="w" if first else "a")
        g.drop(columns=["geometry"]).to_csv(
            csv_out, mode="w" if first else "a",
            header=first, index=False, encoding="utf-8-sig")
        total += len(g)
        first = False
        del g
        gc.collect()
    print(f"  Total eventos: {total}")
    print(f"  [OK] GPKG (layer eventos) -> {gpkg_out.name}")
    print(f"  [OK] CSV  (eventos)       -> {csv_out.name}")

    clip_worker_paths = [
        test_dir / f"{VERSION_TAG}_{run_tag}_w{k}_clip.gpkg"
        for k in range(n_workers)]
    clip_existing = [p for p in clip_worker_paths if p.exists()]
    if clip_existing:
        print(f"  Escribiendo {len(clip_existing)} GPKGs de recortes...")
        total_c = 0
        for p in clip_existing:
            gc_ = gpd.read_file(p)
            gc_.to_file(gpkg_out, layer="clip_clases", driver="GPKG", mode="a")
            total_c += len(gc_)
            del gc_
            gc.collect()
        print(f"  [OK] GPKG (layer clip_clases) -> {gpkg_out.name} "
              f"({total_c} pol.)")
    else:
        print("  [INFO] Sin recortes evento-clase (12/3) que anadir.")

    # [MOD-ECO-1] Capa larga evento-ecorregion (GPKG + CSV propio).
    eco_worker_paths = [
        test_dir / f"{VERSION_TAG}_{run_tag}_w{k}_eco.gpkg"
        for k in range(n_workers)]
    eco_existing = [p for p in eco_worker_paths if p.exists()]
    if eco_existing:
        csv_eco = output_dir / f"{base}_{ECO_LONG_LAYER}.csv"
        if csv_eco.exists():
            csv_eco.unlink()
        print(f"  Escribiendo {len(eco_existing)} GPKGs de trozos eco...")
        total_e, first_e = 0, True
        for p in eco_existing:
            ge = gpd.read_file(p)
            ge.to_file(gpkg_out, layer=ECO_LONG_LAYER, driver="GPKG",
                       mode="w" if first_e else "a")
            ge.drop(columns=["geometry"]).to_csv(
                csv_eco, mode="w" if first_e else "a",
                header=first_e, index=False, encoding="utf-8-sig")
            total_e += len(ge)
            first_e = False
            del ge
            gc.collect()
        print(f"  [OK] GPKG (layer {ECO_LONG_LAYER}) -> {gpkg_out.name} "
              f"({total_e} filas)")
        print(f"  [OK] CSV  ({ECO_LONG_LAYER})       -> {csv_eco.name}")
    else:
        print("  [INFO] Sin trozos evento-ecorregion que anadir.")

    removed = 0
    for p in existing + clip_existing + eco_existing:
        try:
            p.unlink()
            removed += 1
        except Exception as e:
            print(f"  [WARN] no pude borrar {p.name}: {e}")
    print(f"  [OK] Limpieza: {removed} GPKG de workers eliminados")

    timer(f"Merge ({total} features)", t)
    return gpkg_out


# --- Exportacion cartografica (AOI) ------------------------------------------
def save_cartographic_layers(aoi_geom, dem_path, gdf_result, output_dir, base_name):
    """Recorte de capas de ejemplo al AOI. En GFA_V4 NO hay raster de origen que
    recortar: solo se exporta el DEM del AOI y los eventos (poligonos) que caen
    dentro, como GPKG + CSV."""
    if aoi_geom is None:
        print("  [INFO] AOI None: sin capas de ejemplo.")
        return
    os.makedirs(output_dir, exist_ok=True)
    t = time.time()
    print(f"\n{'-'*58}\n  Exportacion cartografica (AOI)\n{'-'*58}")

    # 1) DEM recortado al AOI.
    dem_out = output_dir / f"{base_name}_aoi_dem.tif"
    try:
        with rasterio.open(dem_path) as src:
            aoi_local = _to_local_crs(aoi_geom, src.crs)
            out_img, out_tr = rio_mask(src, [aoi_local], crop=True,
                                       all_touched=True)
            out_meta = {**src.meta, "driver": "GTiff", "compress": "lzw",
                        "height": out_img.shape[1], "width": out_img.shape[2],
                        "transform": out_tr}
        with rasterio.open(dem_out, "w", **out_meta) as dst:
            dst.write(out_img)
        print(f"  [OK] DEM              -> {dem_out.name}")
    except Exception as e:
        print(f"  [WARN] DEM clip: {e}")

    # 2) Eventos dentro del AOI -> GPKG + CSV.
    if gdf_result is None or len(gdf_result) == 0:
        print("  [INFO] Sin eventos en el AOI; no se exportan capas vectoriales.")
        timer("Exportacion cartografica (AOI)", t)
        return
    gdf_aoi = gpd.clip(gdf_result, aoi_geom).reset_index(drop=True)
    if len(gdf_aoi) > 0:
        gpkg_aoi = output_dir / f"{base_name}_aoi_results.gpkg"
        csv_aoi  = output_dir / f"{base_name}_aoi_results.csv"
        gdf_aoi.to_file(gpkg_aoi, layer="eventos_aoi", driver="GPKG")
        gdf_aoi.drop(columns=["geometry"]).to_csv(
            csv_aoi, index=False, encoding="utf-8-sig")
        print(f"  [OK] Resultados AOI   -> {gpkg_aoi.name} ({len(gdf_aoi)} pol.)")
        print("  [NOTE] area_ha es del evento COMPLETO (no recortado al AOI) "
              "y GEODESICA sobre WGS84 ([MOD-AREA-1]).")
    else:
        print("  [INFO] Ningun resultado intersecta el AOI.")
    timer("Exportacion cartografica (AOI)", t)


# --- Validacion previa -------------------------------------------------------
def _preprocess_only():
    t = time.time()
    print("  Paso 0: validando fuentes de entrada...")

    yf = discover_perimeter_year_files()
    print(f"  GFA perimetros: {len(yf)} shapefiles anuales {sorted(yf)}")
    if not yf:
        print(f"  [ERROR] Ningun shapefile GFA en {PERIM_DIR}.")
        return False

    # Peek: la columna de fecha existe en el primer fichero.
    first_shp = yf[sorted(yf)[0]]
    try:
        head = gpd.read_file(first_shp, rows=1)
        if START_DATE_FIELD not in head.columns:
            print(f"  [ERROR] '{START_DATE_FIELD}' no esta en {first_shp.name}. "
                  f"Columnas: {list(head.columns)}")
            return False
        print(f"  [INFO] Columna de fecha '{START_DATE_FIELD}' OK "
              f"(ejemplo: {head[START_DATE_FIELD].iloc[0]!r}).")
    except Exception as e:
        print(f"  [ERROR] No pude leer {first_shp.name}: {e}")
        return False

    for cls, tiles_map, col in (("clase12", MAPBIOMAS_TILES_C12, "cl12_km2"),
                                ("clase3",  MAPBIOMAS_TILES_C3,  "cl3_km2")):
        mbm = get_mapbiomas_metadata(tiles_map)
        print(f"  Tiles {cls}: {len(mbm)}/{len(tiles_map)} disponibles")
        if len(mbm) == 0:
            print(f"  [WARN] Sin tiles {cls}; {col} sera NaN.")
        else:
            faltan = [f"r{r}c{c}" for (r, c) in tiles_map if (r, c) not in mbm]
            if faltan:
                print(f"  [WARN] Cobertura {cls} PARCIAL: faltan {len(faltan)} "
                      f"tiles -> {faltan}. Los eventos en esa zona tendran "
                      f"{col}=NaN.")
            else:
                print(f"  [INFO] Cobertura {cls} completa (9/9 tiles).")

    try:
        ensure_dem_bruto(RAW_TILES_DIR, DEM_PATH, DEM_BBOX,
                         nodata=DEM_NODATA, rebuild=DEM_REBUILD,
                         validate=DEM_VALIDATE, max_workers=DEM_VALIDATE_WORKERS)
    except Exception as e:
        print(f"  [ERROR] No pude asegurar el DEM: {e}")
        return False

    if not Path(GAUL_PATH).exists():
            print(f"  [ERROR] Shapefile de paises no encontrado: {GAUL_PATH}")
            return False

    # [FIX-PAIS] El filtro por centroide descarta todo lo que no caiga en
    # COUNTRY_FILTER. Si ese pais no esta entre los cargados, la corrida
    # produciria 0 eventos tras horas de computo: se aborta aqui.
    try:
        _pays  = load_countries(GAUL_PATH, COUNTRIES_ADM0, roi_geom)
        _names = sorted(_pays["gaul0_name"].unique())
        print(f"  Paises cargados ({len(_names)}): {_names}")
        if COUNTRY_FILTER and COUNTRY_FILTER not in _names:
            print(f"  [ERROR] '{COUNTRY_FILTER}' ausente de los paises "
                  f"cargados. Revisa COUNTRIES_ADM0={COUNTRIES_ADM0} y "
                  f"ROI_BBOX={ROI_BBOX}.")
            return False
        # Sanidad geometrica: el pais debe cubrir su bbox esperado.
        _tgt = _pays[_pays["gaul0_name"] == COUNTRY_FILTER]
        _b   = _tgt.total_bounds   # (minx, miny, maxx, maxy) en EPSG:4326
        print(f"  [INFO] bbox de {COUNTRY_FILTER}: "
              f"({_b[0]:.3f}, {_b[1]:.3f}, {_b[2]:.3f}, {_b[3]:.3f})")
        if _b[0] > -81.2:
            print(f"  [WARN] El borde OESTE de {COUNTRY_FILTER} ({_b[0]:.3f}) "
                  f"no alcanza -81.33 (Punta Parinas). El poligono esta "
                  f"recortado: se perderan eventos en Tumbes/Piura.")
        del _pays, _tgt
    except Exception as e:
        print(f"  [ERROR] No pude cargar/validar los paises: {e}")
        return False

    if not Path(ECO_PATH).exists():
        print(f"  [ERROR] GPKG de ecorregiones no encontrado: {ECO_PATH}")
        return False
    try:
        _eco = load_ecoregions(ECO_PATH, layer=ECO_LAYER, field=ECO_FIELD)
        _vals = sorted(_eco[ECO_FIELD].dropna().unique())
        print(f"  Ecorregiones ({len(_vals)}): {_vals}")
        print(f"  [INFO] Sin seleccion de valores: se clasifican y conservan "
              f"TODAS. Fuera de poligono -> '{SIN_REGION}'.")
    except Exception as e:
        print(f"  [ERROR] No pude leer el GPKG de ecorregiones: {e}")
        return False

    timer("Validacion de fuentes", t)
    return True


# --- Orquestador -------------------------------------------------------------
def run_orchestrator():
    t0      = time.time()
    run_tag = run_tag_of(SAMPLE_FRAC)

    if not _preprocess_only():
        sys.exit(1)

    year_files = discover_perimeter_year_files()
    years = sorted(year_files)
    n_workers = max(1, min(N_WORKERS, len(years)))

    print(f"\n{'='*58}")
    print(f"  ORQUESTADOR — {VERSION_TAG} (paralelo por anio)")
    print(f"  Anios   : {years}")
    print(f"  Workers : {n_workers}")
    print(f"  Modo    : {run_tag}")
    print(f"{'='*58}")

    def _launch(k):
        r = subprocess.run(
            [sys.executable, __file__,
             "--worker-id", str(k), "--n-workers", str(n_workers)],
            text=True, capture_output=True)
        if r.stdout:
            print("\n".join(r.stdout.strip().splitlines()[-10:]))
        if r.returncode != 0:
            print(f"\n  [FAIL] worker {k}:\n{r.stderr[-600:]}")
        return k, r.returncode == 0

    ok, fail = [], []
    with ThreadPoolExecutor(max_workers=n_workers) as exe:
        futures = {exe.submit(_launch, k): k for k in range(n_workers)}
        for f in as_completed(futures):
            k, good = f.result()
            (ok if good else fail).append(k)
            print(f"  {'[OK]  ' if good else '[FAIL]'} worker {k} terminado")

    print(f"\n  Workers OK: {sorted(ok)} | FAIL: {sorted(fail)}")

    missing_years = sorted({y for k in fail for y in years[k::n_workers]})
    partial = len(fail) > 0
    base_name = (f"{VERSION_TAG}_{run_tag}" + ("_PARCIAL" if partial else ""))
    if partial:
        print(f"\n  [ERROR] Corrida PARCIAL: fallaron {len(fail)} worker(s) "
              f"-> {sorted(fail)}")
        print(f"  [ERROR] Anios AUSENTES del dataset final: {missing_years}")
        print(f"  [ERROR] La salida se marca con sufijo '_PARCIAL'.")

    if ok:
        gpkg_out = merge_and_export(run_tag, n_workers, base_name)
        if gpkg_out is not None:
            aoi_geom = load_aoi()
            gdf_aoi_src = None
            if aoi_geom is not None:
                try:
                    gdf_aoi_src = gpd.read_file(
                        gpkg_out, layer="eventos",
                        bbox=tuple(aoi_geom.bounds))
                except Exception as e:
                    print(f"  [WARN] no pude leer el subset AOI del GPKG: {e}")
            save_cartographic_layers(
                aoi_geom, DEM_PATH, gdf_aoi_src, output_dir, base_name)
            cols_show = ["event_uid", "year", "month", "start_date",
                         "dem_median", "ECO_NAME", "n_eco", "ha_dom",
                         "cl12_km2", "cl3_km2", "cl_x_km2", "area_ha", "gaul0_name"]
            try:
                head = gpd.read_file(gpkg_out, layer="eventos", rows=5)
                print(head[[c for c in cols_show if c in head.columns]])
            except Exception as e:
                print(f"  [INFO] no pude imprimir head: {e}")

    timer(f"TOTAL orquestador ({len(ok)}/{n_workers} workers OK)", t0)


# --- Ejecucion ---------------------------------------------------------------
# --- [CHK-AREA] (b) Auditoria post-hoc del sesgo de proyeccion ---------------
# Constantes WGS84 (solo para el modelo teorico del sesgo de la auditoria).
_WGS84_A  = 6378137.0
_WGS84_F  = 1.0 / 298.257223563
_WGS84_E2 = 2.0 * _WGS84_F - _WGS84_F * _WGS84_F


def _mercator_area_scale(lat_deg):
    """Factor de escala AREAL de EPSG:3857 respecto al elipsoide WGS84.

    Derivacion: en 3857, x = a*lam e y = a*ln(tan(pi/4 + phi/2)), luego el
    elemento de area proyectado es a^2*sec(phi) dphi dlam. El elemento real es
    M(phi)*N(phi)*cos(phi) dphi dlam con los radios de curvatura meridional y
    normal del elipsoide. El cociente queda:

        s(phi) = sec^2(phi) * (1 - e2*sin^2(phi))^2 / (1 - e2)

    El segundo factor es el desajuste esfera/elipsoide: vale 1/(1-e2) = 1.006739
    en el ecuador, y es la razon de que el sesgo NO sea 0 % en lat 0 sino
    +0.67 %. Validado contra medicion directa: coincide a 4 decimales en todo
    el rango peruano (0 a -18.35).
    """
    ph = np.radians(np.asarray(lat_deg, dtype=np.float64))
    s2 = np.sin(ph) ** 2
    return (1.0 / np.cos(ph) ** 2) * (1.0 - _WGS84_E2 * s2) ** 2 / (1.0 - _WGS84_E2)


def audit_area_bias(gpkg_path, layer="eventos"):
    """Recalcula el area en AREA_CRS_LEGACY (EPSG:3857) sobre una salida ya
    generada y tabula el sesgo por ECO_NAME contra el predicho por sec^2(lat).

    Reproduce el diagnostico de [MOD-AREA-1]. Sobre la salida de V3 el sesgo
    'obs' debe coincidir con 'pred' (r ~ 0.999) y la columna 'area_ha' debe ser
    la geodesica; sirve para verificar que el cambio esta activo y para
    cuantificar cuanto sobreestimaba V2 en cualquier corrida.

    No forma parte del pipeline: se invoca con --audit-area <ruta.gpkg>.
    """
    g = gpd.read_file(gpkg_path, layer=layer)
    if g.crs is None:
        g = g.set_crs(WGS84)
    g = g.to_crs(WGS84)

    g["area_ha_geod"] = area_m2_geod_series(g.geometry.values) / 1e4
    g["area_ha_3857"] = g.to_crs(AREA_CRS_LEGACY).geometry.area.values / 1e4
    g["lat"] = g.geometry.representative_point().y
    g["sesgo_pct"] = (g["area_ha_3857"] / g["area_ha_geod"] - 1.0) * 100.0
    g["sesgo_pred"] = (_mercator_area_scale(g["lat"].values) - 1.0) * 100.0

    key = "ECO_NAME" if "ECO_NAME" in g.columns else None
    print(f"\n  [CHK-AREA] auditoria de {Path(gpkg_path).name} "
          f"(capa '{layer}', n={len(g)})")
    if key:
        t = (g.groupby(key)
               .agg(n=("sesgo_pct", "size"),
                    lat=("lat", "mean"),
                    sesgo_obs=("sesgo_pct", "mean"),
                    sesgo_pred=("sesgo_pred", "mean"),
                    ha_3857=("area_ha_3857", "sum"),
                    ha_geod=("area_ha_geod", "sum"))
               .sort_values("lat"))
        t["resid_pp"] = t["sesgo_obs"] - t["sesgo_pred"]
        t["dif_km2"] = (t["ha_3857"] - t["ha_geod"]) / 100.0
        print(t.round(4).to_string())

    h37, hg = g["area_ha_3857"].sum(), g["area_ha_geod"].sum()
    print(f"\n  TOTAL 3857 = {h37/100:,.0f} km2 | geodesico = {hg/100:,.0f} km2 "
          f"| sobreestimacion = {(h37/hg - 1)*100:.2f} % "
          f"({(h37-hg)/100:,.0f} km2)")
    ok = np.isfinite(g["sesgo_pct"]) & np.isfinite(g["sesgo_pred"])
    if ok.sum() > 2:
        r = float(np.corrcoef(g.loc[ok, "sesgo_pct"], g.loc[ok, "sesgo_pred"])[0, 1])
        print(f"  correlacion obs~pred r = {r:.4f}")

    # Verificacion de que 'area_ha' almacenada es la geodesica (no la de 3857).
    if "area_ha" in g.columns:
        d_geod = float(np.nanmedian(np.abs(g["area_ha"] / g["area_ha_geod"] - 1)))
        d_3857 = float(np.nanmedian(np.abs(g["area_ha"] / g["area_ha_3857"] - 1)))
        cual = "GEODESICA (V3, correcto)" if d_geod < d_3857 else \
               "EPSG:3857 (V2 o anterior; area INFLADA)"
        print(f"  'area_ha' almacenada corresponde a: {cual} "
              f"(desv. mediana geod={d_geod:.2e}, 3857={d_3857:.2e})")
    return g


def audit_eco_summary(gpkg_path, layer="eventos"):
    """[CHK-ECO] Re-tabula el impacto de [MOD-ECO-1] sobre una salida ya
    generada. Solo lee atributos (sin geometria), asi que es casi instantaneo.

    Se invoca con --audit-eco <ruta.gpkg>.
    """
    g = gpd.read_file(gpkg_path, layer=layer, ignore_geometry=True)
    need = {"n_eco", "ha_dom", "eco_resid_ha", "ECO_NAME", "area_ha"}
    falta = need - set(g.columns)
    if falta:
        print(f"  [ERROR] al GPKG le faltan columnas de [MOD-ECO-1]: "
              f"{sorted(falta)}. ¿Es una salida anterior a la revision ECO?")
        return None

    ha = g["area_ha"].astype(float).values
    ha_tot = float(np.nansum(ha))
    print(f"\n  [CHK-ECO] auditoria de {Path(gpkg_path).name} "
          f"(capa '{layer}', n={len(g)}, {ha_tot/100:,.0f} km2)")

    d = (g.groupby("n_eco")
           .agg(eventos=("area_ha", "size"),
                km2=("area_ha", lambda s: s.sum() / 100.0)))
    d["pct_eventos"] = 100.0 * d["eventos"] / len(g)
    d["pct_area"] = 100.0 * d["km2"] / (ha_tot / 100.0)
    print("\n  --- Distribucion de n_eco ---")
    print(d.round(3).to_string())

    m = g["n_eco"].values >= 2
    ha_no_dom = float(np.nansum(
        ha[m] - np.nan_to_num(g["ha_dom"].astype(float).values[m], nan=0.0)))
    print(f"\n  MULTI-ECORREGION: {int(m.sum()):,} eventos "
          f"({100.0*m.sum()/len(g):.2f} %), "
          f"{100.0*float(np.nansum(ha[m]))/ha_tot:.2f} % del area")
    print(f"  AREA en ecorregion NO dominante: {ha_no_dom/100:,.0f} km2 "
          f"({100.0*ha_no_dom/ha_tot:.2f} %)")

    if "ECO_NAME_pt" in g.columns:
        dif = g["ECO_NAME"].astype(str) != g["ECO_NAME_pt"].astype(str)
        print(f"\n  --- Trasvase punto -> dominante ---")
        print(f"  eventos que cambian: {int(dif.sum()):,} "
              f"({100.0*dif.sum()/len(g):.2f} %) | area implicada "
              f"{100.0*float(np.nansum(ha[dif.values]))/ha_tot:.2f} %")
        if dif.any():
            tr = (g.loc[dif].groupby(["ECO_NAME_pt", "ECO_NAME"])["area_ha"]
                    .agg(n="size", km2=lambda s: s.sum() / 100.0)
                    .sort_values("km2", ascending=False))
            print(tr.head(20).round(2).to_string())

    r = g["eco_resid_ha"].astype(float).values
    print(f"\n  --- Residuo fuera de toda ecorregion ---")
    print(f"  total {np.nansum(r)/100:,.0f} km2 "
          f"({100.0*np.nansum(r)/ha_tot:.2f} %) | "
          f"eventos con residuo NEGATIVO (solape RESOLVE): "
          f"{int(np.count_nonzero(r < -0.01)):,}")

    try:
        e = gpd.read_file(gpkg_path, layer=ECO_LONG_LAYER, ignore_geometry=True)
        t = (e.groupby("ECO_NAME")
               .agg(filas=("area_ha_eco", "size"),
                    km2=("area_ha_eco", lambda s: s.sum() / 100.0),
                    cl12=("cl12_eco_km2", "sum"), cl3=("cl3_eco_km2", "sum"),
                    cl_x=("cl_x_eco_km2", "sum"))
               .sort_values("km2", ascending=False))
        t["pct"] = 100.0 * t["km2"] / t["km2"].sum()
        print(f"\n  --- Area por ecorregion segun '{ECO_LONG_LAYER}' "
              f"(reparto real) ---")
        print(t.round(3).to_string())
    except Exception as ex:
        print(f"  [INFO] sin capa '{ECO_LONG_LAYER}': {ex}")
    return g


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GFA perimeters pipeline V4")
    parser.add_argument("--worker-id", type=int, default=None,
                        help="ID del worker (modo subproceso)")
    parser.add_argument("--n-workers", type=int, default=None,
                        help="Numero total de workers")
    parser.add_argument("--audit-area", type=str, default=None, metavar="GPKG",
                        help="[CHK-AREA] audita el sesgo de proyeccion de un "
                             "GPKG ya generado y sale (no ejecuta el pipeline)")
    parser.add_argument("--audit-eco", type=str, default=None, metavar="GPKG",
                        help="[CHK-ECO] tabula el impacto de [MOD-ECO-1] sobre "
                             "un GPKG ya generado y sale")
    args = parser.parse_args()

    if args.audit_eco:
        audit_eco_summary(args.audit_eco)
    elif args.audit_area:
        audit_area_bias(args.audit_area)
    elif args.worker_id is not None:
        nw = args.n_workers or N_WORKERS
        run_worker(args.worker_id, nw)
    else:
        run_orchestrator()
