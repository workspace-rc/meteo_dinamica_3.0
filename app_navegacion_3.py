import requests
import streamlit as st
import pydeck as pdk
import pandas as pd
import numpy as np
from shapely.geometry import LineString
from datetime import datetime, time, timedelta
import os
import io
import re

def obtener_bandera_pais(lon, sentido_viaje, aduana_procesada, es_cruce_frontera):
    """
    Retorna la bandera del país de manera lógica.
    Evita saltos de bandera por coordenadas GPS intermedias utilizando el estado de la aduana.
    """
    if not es_cruce_frontera:
        # Si no es un viaje internacional, definimos por la frontera física general
        return "🇦🇷" if lon > -71.8654 else "🇨🇱"
    
    if sentido_viaje == "ARG-CHI":
        # De Argentina a Chile: eres Argentina hasta que pasas y procesas la aduana
        return "🇨🇱" if aduana_procesada else "🇦🇷"
        
    elif sentido_viaje == "CHI-ARG":
        # De Chile a Argentina: eres Chile hasta que pasas y procesas la aduana
        return "🇦🇷" if aduana_procesada else "🇨🇱"
        
    return "🇨🇱"   

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Navegador Meteorológico Chile", layout="wide")

# --- 2. CONSULTA API: OPEN-METEO (Clima + Elevación) ---
def consultar_datos(lat, lon, fecha_str):
    """
    Consulta previsión meteorológica y elevación para un punto.
    """
    # 2.1 Consulta de Clima
    url_clima = "https://api.open-meteo.com/v1/forecast"
    params_clima = {
        "latitude":   round(lat, 4),
        "longitude":  round(lon, 4),
        "start_date": fecha_str,
        "end_date":   fecha_str,
        "hourly": [
            "temperature_2m", "precipitation_probability", "rain",
            "cloud_cover", "windspeed_10m", "windgusts_10m",
            "surface_pressure", "freezing_level_height",
        ],
        "daily":    ["sunrise", "sunset"],
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    }
    
    # 2.2 Consulta de Elevación (API sugerida)
    url_topo = "https://api.open-meteo.com/v1/elevation"
    params_topo = {"latitude": lat, "longitude": lon}

    try:
        r_clima = requests.get(url_clima, params=params_clima, timeout=10).json()
        r_topo = requests.get(url_topo, params=params_topo, timeout=10).json()
        
        if 'hourly' not in r_clima: return None
        
        # Combinamos los datos
        r_clima['elevation_msnm'] = r_topo.get('elevation', [0])[0]
        return r_clima
    except:
        return None

# --- 3. INTERFAZ (SIDEBAR) ---
st.sidebar.header("⚙️ Parámetros de Travesía")

# --- SELECTOR DE ARCHIVOS ÚNICO (Con ordenamiento numérico) ---
archivos_disponibles = [f for f in os.listdir('.') if f.endswith(".csv") or f.endswith(".gpx")]

# Ordenamiento natural (01, 02, 10...) de tu código original
import re
archivos_disponibles.sort(key=lambda f: int(re.search(r'\d+', f).group()) if re.search(r'\d+', f) else 0)

# Este es el único y definitivo selector de archivos de ruta
CSV_RUTA = st.sidebar.selectbox(
    "Archivo de ruta:", 
    options=archivos_disponibles,
    key="csv_ruta_seleccionada"
) if archivos_disponibles else None

# Parámetros temporales y de velocidad
fecha_dt = st.sidebar.date_input("Fecha del tramo", value=datetime.now())
FECHA_TRAMO = fecha_dt.strftime("%Y-%m-%d")
HORA_SALIDA = st.sidebar.number_input("Hora de salida (0-23)", min_value=0, max_value=23, value=9, step=1)
HORA_FORMATEADA = f"{int(HORA_SALIDA):02d}:00"
VEL_PROMEDIO = st.sidebar.slider("Velocidad promedio (km/h)", 20, 120, 50, step=10)
DIST_SUBTRAMO = st.sidebar.slider("Resolución: Chequeo cada (km)", 0, 100, 20, step=10)

# Parámetros de Frontera
st.sidebar.subheader("⏳ Parámetros de Frontera")
st.sidebar.slider("Demora en Aduana (horas)", 0.0, 5.0, 1.0, step=0.5, key="demora_aduana_key")
DEMORA_ADUANA = st.session_state.demora_aduana_key

# Lógica dinámica para el Transbordador (Se activa solo si se detecta Hua Hum)
es_hua_hum = False
tiene_reserva = False
hora_zarpe = None

if CSV_RUTA:
    nombre_archivo_limpio = CSV_RUTA.lower().replace(" ", "").replace("_", "").replace("-", "")
    if "huahum" in nombre_archivo_limpio:
        es_hua_hum = True

if es_hua_hum:
    st.sidebar.markdown("---")
    st.sidebar.subheader("🚢 Transbordador Pirehueico")
    tiene_reserva = st.sidebar.checkbox("¿Tiene reserva de ferri?", value=True)
    
    if tiene_reserva:
        # 1. Generamos las opciones de hora como simples cadenas de texto
        opciones_horas_str = [f"{str(i).zfill(2)}:00" for i in range(24)]
        
        # 2. Selector en el sidebar utilizando strings planos
        hora_seleccionada_str = st.sidebar.selectbox(
            "🚢 Selecciona la hora de salida del Ferri:",
            options=opciones_horas_str,
            index=12  # Por defecto preselecciona las 12:00
        )
        
        # 3. Guardamos la hora seleccionada como un objeto de tiempo de forma ultra aislada
        import sys
        modulo_datetime = sys.modules.get('datetime')
        if modulo_datetime is None:
            import datetime as modulo_datetime
            
        partes = [int(p) for p in hora_seleccionada_str.split(":")]
        HORA_ZARPE_FERRI = modulo_datetime.time(partes[0], partes[1])
    else:
        import sys
        modulo_datetime = sys.modules.get('datetime')
        if modulo_datetime is None:
            import datetime as modulo_datetime
        HORA_ZARPE_FERRI = modulo_datetime.time(0, 0)

# --- 4. LÓGICA PRINCIPAL ---
def main():
    st.title("🛰️ Navegador Meteorológico Táctico")
    
    # Reparación del error CU: Definición completa de la variable
    contenedor_estado = st.empty()

    if CSV_RUTA and st.sidebar.button("🚀 Iniciar Análisis", use_container_width=True):
        contenedor_estado = st.empty()
        contenedor_estado.info(f"⏳ Procesando ruta: {CSV_RUTA}...")
        
        try:
            import re
            import zipfile
            import io

            # --- 1. LECTURA DEL ARCHIVO (Soporta KMZ, KML, GPX, CSV) ---
            if CSV_RUTA.endswith(".kmz"):
                with zipfile.ZipFile(CSV_RUTA, 'r') as z:
                    kml_name = [f for f in z.namelist() if f.endswith('.kml')][0]
                    with z.open(kml_name) as f:
                        contenido = f.read().decode('utf-8')
            else:
                with open(CSV_RUTA, 'r', encoding='utf-8') as f:
                    contenido = f.read()

            # --- 2. EXTRACCIÓN DE COORDENADAS ---
            puntos_ruta = []
            
            # Caso KML (Google Earth)
            if "<coordinates>" in contenido:
                coord_bloques = re.findall(r'<coordinates>(.*?)</coordinates>', contenido, re.DOTALL)
                all_coords = " ".join(coord_bloques).replace('\n', ' ').strip().split()
                for entry in all_coords:
                    parts = entry.split(',')
                    if len(parts) >= 2:
                        puntos_ruta.append({'X': float(parts[0]), 'Y': float(parts[1])})
            
            # Caso GPX (OsmAnd) - FILTRADO PARA EVITAR EL SALTO AL INICIO
            # Buscamos SOLO etiquetas <trkpt>, ignorando los <wpt> (Waypoints)
            elif '<trkpt' in contenido:
                # Esta regex es más estricta: solo captura puntos dentro de tracks
                segmentos = re.findall(r'<trkpt.*?lat="([-?0-9.]+)" lon="([-?0-9.]+)".*?>', contenido, re.DOTALL)
                for la, lo in segmentos:
                    puntos_ruta.append({'X': float(lo), 'Y': float(la)})
            
            # Caso CSV convencional
            else:
                df_temp = pd.read_csv(io.StringIO(contenido))
                cols = {c.lower(): c for c in df_temp.columns}
                col_x = next((cols[p] for p in ['x', 'lon', 'longitude', 'lng'] if p in cols), None)
                col_y = next((cols[p] for p in ['y', 'lat', 'latitude'] if p in cols), None)
                if col_x and col_y:
                    for _, row in df_temp.iterrows():
                        puntos_ruta.append({'X': float(row[col_x]), 'Y': float(row[col_y])})

            # --- 3. VALIDACIÓN Y LIMPIEZA ---
            if not puntos_ruta:
                st.error("No se encontraron puntos de coordenadas válidos.")
                st.stop()

            df_ruta = pd.DataFrame(puntos_ruta)
            df_ruta = df_ruta.dropna(subset=['X', 'Y'])
            # Eliminar duplicados de posición consecutivos
            df_ruta = df_ruta[(df_ruta[['X', 'Y']].shift() != df_ruta[['X', 'Y']]).any(axis=1)]

            # =========================================================================
            # --- TABLA DE REFERENCIA DE PASOS FRONTERIZOS ---
            # =========================================================================
            PASOS_FRONTERA = {
                "pehuenche": {"nombre": "Paso Pehuenche", "lon_frontera": -70.43},
                "pichachen": {"nombre": "Paso Pichachén", "lon_frontera": -71.14},
                "pinohachado": {"nombre": "Paso Pino Hachado", "lon_frontera": -71.08},
                "pino": {"nombre": "Paso Pino Hachado", "lon_frontera": -71.08},
                "hachado": {"nombre": "Paso Pino Hachado", "lon_frontera": -71.08},
                "mamuil": {"nombre": "Paso Mamuil Malal", "lon_frontera": -71.40},
                "malal": {"nombre": "Paso Mamuil Malal", "lon_frontera": -71.40},
                "carirrine": {"nombre": "Paso Carirriñe", "lon_frontera": -71.67},
                "huahum": {"nombre": "Paso Hua Hum", "lon_frontera": -71.69},
                "samore": {"nombre": "Paso Cardenal Samoré", "lon_frontera": -71.82},
                "cardenal": {"nombre": "Paso Cardenal Samoré", "lon_frontera": -71.82}
            }

            # 1. Limpieza del nombre del archivo para buscar coincidencias
            nombre_archivo_limpio = CSV_RUTA.lower().replace(" ", "").replace("_", "").replace("-", "")
            paso_detectado = None
            lon_frontera = None

            for clave, info in PASOS_FRONTERA.items():
                if clave in nombre_archivo_limpio:
                    paso_detectado = info["nombre"]
                    lon_frontera = info["lon_frontera"]
                    break

            # 2. Determinar geográficamente el origen y destino
            lon_origen = df_ruta.iloc[0]['X']
            lon_destino = df_ruta.iloc[-1]['X']

            # 3. Identificar el Sentido y si es un cruce real de esa frontera
            es_cruce_frontera = False
            sentido_viaje = "interno" # por defecto

            if lon_frontera is not None:
                # Comprobamos si el viaje efectivamente cruza la línea de la frontera detectada
                if (lon_origen > lon_frontera and lon_destino < lon_frontera):
                    es_cruce_frontera = True
                    sentido_viaje = "ARG-CHI"  # De Este a Oeste (Ida a Chile)
                elif (lon_origen < lon_frontera and lon_destino > lon_frontera):
                    es_cruce_frontera = True
                    sentido_viaje = "CHI-ARG"  # De Oeste a Este (Vuelta a Argentina)

            # Informar al usuario en la interfaz del análisis detectado
            if es_cruce_frontera:
                st.sidebar.success(f"🗺️ Cruce detectado: {paso_detectado} ({sentido_viaje})")
            else:
                st.sidebar.info("🚗 Trayecto interno (Sin cruce de frontera activa)")
            # =========================================================================
            
            # --- CÁLCULO DE GEOMETRÍA ---
            puntos = list(zip(df_ruta['X'], df_ruta['Y']))
            linea = LineString(puntos)
            distancia_total_km = linea.length * 111.1
            num_subtramos = int(distancia_total_km // DIST_SUBTRAMO) + 1
            
            # --- DEFINICIÓN HORARIO INICIO ---
            from zoneinfo import ZoneInfo
            hora_inicio = datetime.strptime(f"{FECHA_TRAMO} {HORA_FORMATEADA}", "%Y-%m-%d %H:%M")

            # --- Aviso dinámico de HEA y HES ---
            if es_cruce_frontera:
                # Encontrar el kilómetro real del hito fronterizo
                km_frontera = 0.0
                for i in range(num_subtramos):
                    pos = (i * DIST_SUBTRAMO) / distancia_total_km
                    punto = linea.interpolate(min(pos, 1.0), normalized=True)
                    if (sentido_viaje == "ARG-CHI" and punto.x <= lon_frontera) or \
                       (sentido_viaje == "CHI-ARG" and punto.x >= lon_frontera):
                        km_frontera = round(i * DIST_SUBTRAMO, 1)
                        break
                
                # Calcular las horas estimadas
                horas_hasta_frontera = km_frontera / VEL_PROMEDIO
                dt_hea = hora_inicio + timedelta(hours=horas_hasta_frontera)
                
                bandera_origen = "🇦🇷" if sentido_viaje == "ARG-CHI" else "🇨🇱"
                bandera_destino = "🇨🇱" if sentido_viaje == "ARG-CHI" else "🇦🇷"
                
                if sentido_viaje == "ARG-CHI":
                    dt_hes = dt_hea + timedelta(hours=DEMORA_ADUANA) - timedelta(hours=1)
                else:
                    dt_hes = dt_hea + timedelta(hours=DEMORA_ADUANA) + timedelta(hours=1)
                
                hea_str = dt_hea.strftime("%H:%M")
                hes_str = dt_hes.strftime("%H:%M")
                
                # Mostrar la alerta limpia en la app
                st.warning(
                    f"⚠️ **Frontera: {paso_detectado}** (Km {km_frontera}) | "
                    f"**HEA:** {hea_str} {bandera_origen} | "
                    f"**Espera:** {DEMORA_ADUANA}h | "
                    f"**HES:** {hes_str} {bandera_destino}"
                )
            
            # =========================================================================
            # 4. BUCLE FOR DEL TRAYECTO (BIMODAL CON ALERTA DE ANTICIPACIÓN)
            # =========================================================================
            resultados = []
            barra_progreso = st.progress(0)
            
            aduana_procesada = False
            ferri_procesado = False
            demora_acumulada = 0.0

            # Límites geográficos del Lago Pirehueico
            LON_PIREHUEICO = -71.69
            LON_FUY = -71.89

            # Variables para guardar el cálculo de margen y usarlo fuera del bucle
            margen_embarque_minutos = None
            hora_llegada_puerto_registro = None
            nombre_puerto_registro = ""

            for i in range(num_subtramos):
                barra_progreso.progress((i + 1) / num_subtramos)
                
                pos = (i * DIST_SUBTRAMO) / distancia_total_km
                punto = linea.interpolate(min(pos, 1.0), normalized=True)
                lon, lat = punto.x, punto.y
                
                km_actual = int(i * DIST_SUBTRAMO)

                # 1. Determinar si el punto actual está navegando por el lago
                esta_en_el_lago = False
                
                # 2. Calcular las horas de conducción netas sobre tierra firme de forma matemática estricta
                if es_hua_hum:
                    if esta_en_el_lago:
                        # Si está en el transbordador, el coche no se mueve por tierra firme.
                        # Guardamos el kilometraje de conducción justo antes de entrar al puerto.
                        # Aproximamos la distancia terrestre recorrida hasta este punto (KM 80 en tu ruta)
                        km_conduccion_real = 80.0 
                    elif ferri_procesado:
                        # Si ya cruzamos, restamos exactamente la distancia que se hizo navegando (26 km de lago)
                        # para que no se sumen como si hubiésemos conducido en auto.
                        km_conduccion_real = max(0.0, (i * DIST_SUBTRAMO) - 26.0)
                    else:
                        # Si aún no llegamos al ferri, conducimos normalmente por los kilómetros recorridos
                        km_conduccion_real = i * DIST_SUBTRAMO
                else:
                    # Ruta estándar sin transbordadores
                    km_conduccion_real = i * DIST_SUBTRAMO

                # Las horas de manejo reales solo se calculan en base a la conducción por tierra
                horas_conduccion = km_conduccion_real / VEL_PROMEDIO

                # ---------------------------------------------------------------------
                # CASO DE USO A: SENTIDO IDA (ARGENTINA -> CHILE)
                # ---------------------------------------------------------------------
                if es_cruce_frontera and sentido_viaje == "ARG-CHI":
                    if lon <= lon_frontera and not aduana_procesada:
                        hora_llegada_aduana = hora_inicio + timedelta(hours=horas_conduccion + demora_acumulada)
                        hora_salida_aduana = hora_llegada_aduana + timedelta(hours=DEMORA_ADUANA)
                        
                        data_cruce = consultar_datos(lat, lon, FECHA_TRAMO)
                        if data_cruce:
                            clima_cruce = data_cruce['hourly']
                            idx_c = min(hora_llegada_aduana.hour, 23)
                            
                            # Fila de Aduana (Aduana Argentina está en huso ARG)
                            resultados.append({
                                "KM": km_actual,
                                "HORA": f"🇦🇷 {hora_llegada_aduana.strftime('%H:%M')} ➔ {hora_salida_aduana.strftime('%H:%M')}",
                                "ALERTAS": "⏳ ESPERA ADUANA",
                                "ALTITUD (m)": int(data_cruce['elevation_msnm']),
                                "Amanece": data_cruce['daily']['sunrise'][0][-5:],
                                "Anochece": data_cruce['daily']['sunset'][0][-5:],
                                "Temp (°C)": clima_cruce['temperature_2m'][idx_c],
                                "Altitud 0°C (m)": clima_cruce['freezing_level_height'][idx_c],
                                "Lluvia (%)": clima_cruce['precipitation_probability'][idx_c],
                                "Lluvia (mm)": clima_cruce['rain'][idx_c],
                                "Nubes": clima_cruce['cloud_cover'][idx_c],
                                "Viento (km/h)": clima_cruce['windspeed_10m'][idx_c],
                                "hPa": clima_cruce['surface_pressure'][idx_c],
                                "lat": lat, "lon": lon
                            })
                        
                        # Al salir de la aduana y cruzar la frontera física, aplicamos la diferencia horaria (-1 hora en Chile)
                        # De esta forma, todo el viaje en Chile se calculará sobre la hora local chilena
                        demora_acumulada += DEMORA_ADUANA - 1.0
                        aduana_procesada = True

                        if es_hua_hum:
                            # Hora de llegada al puerto de Pirehueico (Huso Chile)
                            hora_llegada_puerto = hora_inicio + timedelta(hours=horas_conduccion + demora_acumulada)
                            
                            # El zarpe real tiene una hora fija programada en el día (Huso Chile)
                            hora_zarpe_real = datetime.combine(fecha_dt, HORA_ZARPE_FERRI)
                            
                            # --- CÁLCULO DE ANTICIPACIÓN (IDA) ---
                            margen_embarque_minutos = (hora_zarpe_real - hora_llegada_puerto).total_seconds() / 60
                            hora_llegada_puerto_registro = hora_llegada_puerto
                            nombre_puerto_registro = "Puerto Pirehueico"
                            
                            if data_cruce:
                                idx_p = min(hora_llegada_puerto.hour, 23)
                                # Fila de Espera en Puerto (Desde llegada hasta la hora real de zarpe)
                                resultados.append({
                                    "KM": km_actual,
                                    "HORA": f"🇨🇱 {hora_llegada_puerto.strftime('%H:%M')} ➔ {hora_zarpe_real.strftime('%H:%M')}",
                                    "ALERTAS": "⚓ ESPERA PUERTO",
                                    "ALTITUD (m)": int(data_cruce['elevation_msnm']),
                                    "Amanece": data_cruce['daily']['sunrise'][0][-5:],
                                    "Anochece": data_cruce['daily']['sunset'][0][-5:],
                                    "Temp (°C)": clima_cruce['temperature_2m'][idx_p],
                                    "Altitud 0°C (m)": clima_cruce['freezing_level_height'][idx_p],
                                    "Lluvia (%)": clima_cruce['precipitation_probability'][idx_p],
                                    "Lluvia (mm)": clima_cruce['rain'][idx_p],
                                    "Nubes": clima_cruce['cloud_cover'][idx_p],
                                    "Viento (km/h)": clima_cruce['windspeed_10m'][idx_p],
                                    "hPa": clima_cruce['surface_pressure'][idx_p],
                                    "lat": lat, "lon": lon
                                })
                                
                                # Fila de Navegación (Empieza estrictamente a la hora de zarpe y dura 1.5 horas)
                                hora_arribo_fuy = hora_zarpe_real + timedelta(hours=1.5)
                                idx_n = min(hora_zarpe_real.hour, 23)
                                resultados.append({
                                    "KM": km_actual,
                                    "HORA": f"🇨🇱 {hora_zarpe_real.strftime('%H:%M')} ➔ {hora_arribo_fuy.strftime('%H:%M')}",
                                    "ALERTAS": "🚢 NAVEGACIÓN",
                                    "ALTITUD (m)": int(data_cruce['elevation_msnm']),
                                    "Amanece": data_cruce['daily']['sunrise'][0][-5:],
                                    "Anochece": data_cruce['daily']['sunset'][0][-5:],
                                    "Temp (°C)": clima_cruce['temperature_2m'][idx_n],
                                    "Altitud 0°C (m)": clima_cruce['freezing_level_height'][idx_n],
                                    "Lluvia (%)": clima_cruce['precipitation_probability'][idx_n],
                                    "Lluvia (mm)": clima_cruce['rain'][idx_n],
                                    "Nubes": clima_cruce['cloud_cover'][idx_n],
                                    "Viento (km/h)": clima_cruce['windspeed_10m'][idx_n],
                                    "hPa": clima_cruce['surface_pressure'][idx_n],
                                    "lat": lat, "lon": lon
                                })
                            
                            # Sincronizamos la demora acumulada con el fin de la navegación
                            # Al salir del transbordador, el tiempo transcurrido es exactamente (hora_arribo_fuy - hora_inicio)
                            total_horas_hasta_fuy = (hora_arribo_fuy - hora_inicio).total_seconds() / 3600
                            demora_acumulada = total_horas_hasta_fuy - horas_conduccion
                            ferri_procesado = True

                # ---------------------------------------------------------------------
                # CASO DE USO B: SENTIDO VUELTA (CHILE -> ARGENTINA)
                # ---------------------------------------------------------------------
                elif es_cruce_frontera and sentido_viaje == "CHI-ARG":
                    if lon >= LON_FUY and es_hua_hum and not ferri_procesado:
                        # Llegada a Puerto Fuy (Huso Chile)
                        hora_llegada_fuy = hora_inicio + timedelta(hours=horas_conduccion + demora_acumulada)
                        
                        # El zarpe real programado en el día (Huso Chile)
                        hora_zarpe_fuy = datetime.combine(fecha_dt, HORA_ZARPE_FERRI)
                        
                        # --- CÁLCULO DE ANTICIPACIÓN (VUELTA) ---
                        margen_embarque_minutos = (hora_zarpe_fuy - hora_llegada_fuy).total_seconds() / 60
                        hora_llegada_puerto_registro = hora_llegada_fuy
                        nombre_puerto_registro = "Puerto Fuy"
                        
                        data_fuy = consultar_datos(lat, lon, FECHA_TRAMO)
                        if data_fuy:
                            clima_fuy = data_fuy['hourly']
                            idx_f = min(hora_llegada_fuy.hour, 23)
                            
                            # Fila de Espera Puerto (Desde llegada hasta el zarpe programado)
                            resultados.append({
                                "KM": km_actual,
                                "HORA": f"🇨🇱 {hora_llegada_fuy.strftime('%H:%M')} ➔ {hora_zarpe_fuy.strftime('%H:%M')}",
                                "ALERTAS": "⚓ ESPERA PUERTO",
                                "ALTITUD (m)": int(data_fuy['elevation_msnm']),
                                "Amanece": data_fuy['daily']['sunrise'][0][-5:],
                                "Anochece": data_fuy['daily']['sunset'][0][-5:],
                                "Temp (°C)": clima_fuy['temperature_2m'][idx_f],
                                "Altitud 0°C (m)": clima_fuy['freezing_level_height'][idx_f],
                                "Lluvia (%)": clima_fuy['precipitation_probability'][idx_f],
                                "Lluvia (mm)": clima_fuy['rain'][idx_f],
                                "Nubes": clima_fuy['cloud_cover'][idx_f],
                                "Viento (km/h)": clima_fuy['windspeed_10m'][idx_f],
                                "hPa": clima_fuy['surface_pressure'][idx_f],
                                "lat": lat, "lon": lon
                            })
                            
                            # Fila de Navegación (Estrictamente a la hora de zarpe fijada + 1.5 horas)
                            hora_llegada_pirehueico = hora_zarpe_fuy + timedelta(hours=1.5)
                            idx_fn = min(hora_zarpe_fuy.hour, 23)
                            resultados.append({
                                "KM": km_actual,
                                "HORA": f"🇨🇱 {hora_zarpe_fuy.strftime('%H:%M')} ➔ {hora_llegada_pirehueico.strftime('%H:%M')}",
                                "ALERTAS": "🚢 NAVEGACIÓN",
                                "ALTITUD (m)": int(data_fuy['elevation_msnm']),
                                "Amanece": data_fuy['daily']['sunrise'][0][-5:],
                                "Anochece": data_fuy['daily']['sunset'][0][-5:],
                                "Temp (°C)": clima_fuy['temperature_2m'][idx_fn],
                                "Altitud 0°C (m)": clima_fuy['freezing_level_height'][idx_fn],
                                "Lluvia (%)": clima_fuy['precipitation_probability'][idx_fn],
                                "Lluvia (mm)": clima_fuy['rain'][idx_fn],
                                "Nubes": clima_fuy['cloud_cover'][idx_fn],
                                "Viento (km/h)": clima_fuy['windspeed_10m'][idx_fn],
                                "hPa": clima_fuy['surface_pressure'][idx_fn],
                                "lat": lat, "lon": lon
                            })
                        
                        # Sincronizamos la demora acumulada con el desembarco en Pirehueico
                        total_horas_hasta_pirehueico = (hora_llegada_pirehueico - hora_inicio).total_seconds() / 3600
                        demora_acumulada = total_horas_hasta_pirehueico - horas_conduccion
                        ferri_procesado = True

                    if lon >= lon_frontera and not aduana_procesada:
                        hora_llegada_aduana = hora_inicio + timedelta(hours=horas_conduccion + demora_acumulada)
                        hora_salida_aduana = hora_llegada_aduana + timedelta(hours=DEMORA_ADUANA)
                        
                        data_aduana = consultar_datos(lat, lon, FECHA_TRAMO)
                        if data_aduana:
                            clima_aduana = data_aduana['hourly']
                            idx_ad = min(hora_llegada_aduana.hour, 23)
                            
                            # Fila de Espera Aduana (Aduana Chilena está en huso Chile)
                            resultados.append({
                                "KM": km_actual,
                                "HORA": f"🇨🇱 {hora_llegada_aduana.strftime('%H:%M')} ➔ {hora_salida_aduana.strftime('%H:%M')}",
                                "ALERTAS": "⏳ ESPERA ADUANA",
                                "ALTITUD (m)": int(data_aduana['elevation_msnm']),
                                "Amanece": data_aduana['daily']['sunrise'][0][-5:],
                                "Anochece": data_aduana['daily']['sunset'][0][-5:],
                                "Temp (°C)": clima_aduana['temperature_2m'][idx_ad],
                                "Altitud 0°C (m)": clima_aduana['freezing_level_height'][idx_ad],
                                "Lluvia (%)": clima_aduana['precipitation_probability'][idx_ad],
                                "Lluvia (mm)": clima_aduana['rain'][idx_ad],
                                "Nubes": clima_aduana['cloud_cover'][idx_ad],
                                "Viento (km/h)": clima_aduana['windspeed_10m'][idx_ad],
                                "hPa": clima_aduana['surface_pressure'][idx_ad],
                                "lat": lat, "lon": lon
                            })
                        
                        # Al cruzar la frontera física al territorio argentino, sumamos 1 hora (huso ARG)
                        demora_acumulada += DEMORA_ADUANA + 1.0
                        aduana_procesada = True

                # ---------------------------------------------------------------------
                # TRAMOS EN TIERRA FIRME
                # ---------------------------------------------------------------------
                if not esta_en_el_lago:
                    horas_totales = horas_conduccion + demora_acumulada
                    hora_paso = hora_inicio + timedelta(hours=horas_totales)

                    data = consultar_datos(lat, lon, FECHA_TRAMO)
                    if data:
                        horario = data['hourly']
                        idx = min(hora_paso.hour, 23)
                        temp = horario['temperature_2m'][idx]
                        viento = horario['windspeed_10m'][idx]
                        lluvia_cant = horario['rain'][idx]
                        lluvia_prob = horario['precipitation_probability'][idx]
                        altitud = data['elevation_msnm']
                        hielo = horario['freezing_level_height'][idx]
                        presion_atm = horario['surface_pressure'][idx]
                        nubosidad = horario['cloud_cover'][idx]
                        amanece = data['daily']['sunrise'][0][-5:]
                        anochece = data['daily']['sunset'][0][-5:]
                        
                        alertas = []
                        if viento > 45: alertas.append("💨 VIENTO")
                        if temp < 3: alertas.append("❄️ HIELO")
                        if lluvia_prob > 60: alertas.append("🌧️ LLUVIA")
                        
                        if lluvia_cant > 0 and lluvia_cant <= 2: alertas.append("🌦️ LLUVIA DÉBIL")
                        elif lluvia_cant > 2 and lluvia_cant <= 8: alertas.append("🌧️ USA TRAJE")
                        elif lluvia_cant > 8: alertas.append("⚠️ BUSCA TECHO")
                                                                 
                        bandera = obtener_bandera_pais(lon, sentido_viaje, aduana_procesada, es_cruce_frontera)
                       
                        resultados.append({
                            "KM": km_actual,
                            "HORA": f"{bandera} {hora_paso.strftime('%H:%M')}", # <--- ¡Aquí se agrega la bandera!
                            "ALERTAS": " | | ".join(alertas) if alertas else "✅  DESPEJADO",
                            "ALTITUD (m)": int(altitud),
                            "Amanece": amanece,
                            "Anochece": anochece,
                            "Temp (°C)": temp,
                            "Altitud 0°C (m)": hielo,
                            "Lluvia (%)": lluvia_prob,
                            "Lluvia (mm)": lluvia_cant,
                            "Nubes": nubosidad,
                            "Viento (km/h)": viento,
                            "hPa": presion_atm,
                            "lat": lat, "lon": lon
                        })

            # =========================================================================
            # --- SECCIÓN DE VISUALIZACIÓN INTEGRADA Y ALERTAS ---
            # =========================================================================
            import pandas as pandas_global  # Importación local segura con alias único
            df_final = pandas_global.DataFrame(resultados)
            
            # Calcular distancias terrestres seguras
            if es_hua_hum:
                distancia_lago = 26.0
                distancia_terrestre = max(0.0, distancia_total_km - distancia_lago)
            else:
                distancia_terrestre = distancia_total_km
                
            tiempo_conduccion = distancia_terrestre / VEL_PROMEDIO
            tiempo_total_viaje = tiempo_conduccion + demora_acumulada

            # 1. ----------------------------------------------------------------------
            # ALERTA DE ANTICIPACIÓN EN EL PUERTO (BANNER VISUAL)
            # -------------------------------------------------------------------------
            if es_hua_hum and margen_embarque_minutos is not None:
                # Caso A: Llegas después de la hora de zarpe (Retraso)
                if margen_embarque_minutos < 0:
                    st.error(
                        f"🚨 **¡EMBARQUE EN RIESGO EXTREMO / PERDIDO!** El ferri zarpa a las **{HORA_ZARPE_FERRI.strftime('%H:%M')}**. "
                        f"Estimas llegar a {nombre_puerto_registro} a las **{hora_llegada_puerto_registro.strftime('%H:%M')}** "
                        f"({int(abs(margen_embarque_minutos))} minutos tarde)."
                    )
                # Caso B: Llegas con menos de 1 hora (60 min) de anticipación
                elif margen_embarque_minutos < 60:
                    st.warning(
                        f"⚠️ **ADVERTENCIA DE ANTICIPACIÓN:** Estimas llegar a {nombre_puerto_registro} a las **{hora_llegada_puerto_registro.strftime('%H:%M')}** "
                        f"para el zarpe de las **{HORA_ZARPE_FERRI.strftime('%H:%M')}**. "
                        f"Tienes solo **{int(margen_embarque_minutos)} minutos** de margen (Se exige mínimo 1 hora de anticipación)."
                    )
                # Caso C: Llegas con tiempo suficiente de sobra
                else:
                    st.success(
                        f"✅ **Embarque seguro:** Estimas llegar a {nombre_puerto_registro} a las **{hora_llegada_puerto_registro.strftime('%H:%M')}**. "
                        f"Cuentas con un excelente margen de **{int(margen_embarque_minutos)} minutos** antes del zarpe ({HORA_ZARPE_FERRI.strftime('%H:%M')})."
                    )

            # 2. ----------------------------------------------------------------------
            # PANEL DE MÉTRICAS (Rendimiento del Viaje)
            # -------------------------------------------------------------------------
            lista_alertas_col = df_final['ALERTAS'].str.lower().tolist()
            if any("nieve" in c or "❄️" in c for c in lista_alertas_col):
                clima_general = "Nevadas ❄️"
            elif any("lluvia" in c or "🌧️" in c or "🌦️" in c for c in lista_alertas_col):
                clima_general = "Lluvias 🌧️"
            elif any("nubes" in c or "nublado" in c for c in lista_alertas_col):
                clima_general = "Nublado ☁️"
            else:
                clima_general = "Despejado ☀️"

            # Formateamos el tiempo total de viaje a horas y minutos legibles
            horas_total_int = int(tiempo_total_viaje)
            minutos_total_int = int((tiempo_total_viaje - horas_total_int) * 60)

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("⏱️ Tiempo de Viaje", f"{horas_total_int}h {minutos_total_int}m")
            with col2:
                st.metric("⏳ Espera Aduana", f"{DEMORA_ADUANA} hs" if es_cruce_frontera else "0.0 hs")
            with col3:
                tiempo_navegacion = "2.5 hs" if es_hua_hum else "0.0 hs"
                st.metric("🚢 Navegación / Puerto", tiempo_navegacion)
            with col4:
                st.metric("🌡️ Clima General", clima_general)

            # 3. ----------------------------------------------------------------------
            # MAPA INTERACTIVO DE LA RUTA (PYDECK)
            # -------------------------------------------------------------------------
            st.subheader("🗺️ Trazado de la Ruta y Puntos de Análisis")
            st.markdown(f"##### 📍 **Distancia Total:** {distancia_total_km:.0f} km | ⏳ **Tiempo de Conducción Neto:** {int(tiempo_conduccion)}h {int((tiempo_conduccion - int(tiempo_conduccion)) * 60)}min (@{VEL_PROMEDIO} km/h)")
            
            view_state = pdk.ViewState(
                latitude=df_final['lat'].mean(),
                longitude=df_final['lon'].mean(),
                zoom=6,
                pitch=0
            )

            capa_ruta = pdk.Layer(
                "PathLayer",
                data=[{"path": df_final[['lon', 'lat']].values.tolist()}],
                get_path="path",
                get_color=[255, 0, 0, 150], 
                get_width=5,
                width_min_pixels=3,
            )

            capa_puntos = pdk.Layer(
                "ScatterplotLayer",
                data=df_final,
                get_position="[lon, lat]",
                get_color=[30, 144, 255, 200], 
                get_radius=1500,
                radius_min_pixels=6,
                pickable=True,
            )

            st.pydeck_chart(pdk.Deck(
                map_provider="carto",
                map_style="light", 
                initial_view_state=view_state,
                layers=[capa_ruta, capa_puntos],
                tooltip={
                    "html": "<b>Subtramo:</b> {KM} km<br/><b>Altitud:</b> {ALTITUD (m)} msnm<br/><b>Hora:</b> {HORA}<br/><b>Alertas:</b> {ALERTAS}",
                    "style": {"backgroundColor": "#002b36", "color": "white"}
                }
            ))

            # 4. ----------------------------------------------------------------------
            # TABLA DE RESULTADOS DETALLADOS
            # -------------------------------------------------------------------------
            st.subheader("📋 Resultados del Análisis")
            st.dataframe(df_final, use_container_width=True)

            # 5. ----------------------------------------------------------------------
            # SISTEMA DE DESCARGA DE REPORTE CSV
            # -------------------------------------------------------------------------
            dia_num = CSV_RUTA.replace("prevision_dia ", "").replace(".csv", "")
            ahora_analisis = datetime.now().strftime("%Y%m%d_%H%M")
            nombre_salida = f"Tramo{dia_num}_{FECHA_TRAMO}_Analizado_{ahora_analisis}.csv"
            csv_bytes = df_final.to_csv(index=False).encode('utf-8')
            
            st.download_button(
                label=f"📥 Descargar {nombre_salida}",
                data=csv_bytes,
                file_name=nombre_salida,
                mime='text/csv'
            )

        except Exception as e:
            st.error(f"❌ Error durante el proceso: {e}")

if __name__ == "__main__":
    main()
