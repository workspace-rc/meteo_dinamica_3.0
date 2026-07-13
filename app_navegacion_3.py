import requests
import streamlit as st
import pydeck as pdk
import pandas as pd
import numpy as np
from shapely.geometry import LineString
from datetime import datetime, timedelta
import os
import io
import re

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
        from datetime import time
        # Selección del horario de zarpe reservado
        hora_zarpe = st.sidebar.time_input("Horario de zarpe reservado", time(14, 0))

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
            # 4. BUCLE FOR DEL TRAYECTO (Pégalo debajo del aviso)
            # =========================================================================
    
            resultados = []
            barra_progreso = st.progress(0)
            aduana_procesada = False
            demora_acumulada = 0.0

            for i in range(num_subtramos):
                barra_progreso.progress((i + 1) / num_subtramos)
                
                pos = (i * DIST_SUBTRAMO) / distancia_total_km
                punto = linea.interpolate(min(pos, 1.0), normalized=True)
                lon, lat = punto.x, punto.y
                
                # 1. Tiempo lineal de viaje
                horas_transcurridas = (i * DIST_SUBTRAMO) / VEL_PROMEDIO
                
                # 2. LÓGICA DINÁMICA DE FRONTERA
                if es_cruce_frontera:
                    # CASO IDA: Cruzando de Argentina a Chile (Hacia el Oeste)
                    if sentido_viaje == "ARG-CHI":
                        if lon <= lon_frontera: # Ya cruzamos la frontera hacia Chile
                            if not aduana_procesada:
                                horas_transcurridas += DEMORA_ADUANA
                                aduana_procesada = True
                            
                            # Hora en Chile: Sumamos demora y restamos 1 hora por huso chileno de invierno
                            hora_paso = hora_inicio + timedelta(hours=horas_transcurridas) - timedelta(hours=1)
                        else:
                            # Aún en Argentina
                            hora_paso = hora_inicio + timedelta(hours=horas_transcurridas)
                            
                    # CASO VUELTA: Cruzando de Chile a Argentina (Hacia el Este)
                    elif sentido_viaje == "CHI-ARG":
                        if lon >= lon_frontera: # Ya cruzamos la frontera hacia Argentina
                            if not aduana_procesada:
                                horas_transcurridas += DEMORA_ADUANA
                                aduana_procesada = True
                            
                            # Hora en Argentina: Sumamos demora y sumamos 1 hora para volver al huso de Arg
                            hora_paso = hora_inicio + timedelta(hours=horas_transcurridas) + timedelta(hours=1)
                        else:
                            # Aún en Chile
                            hora_paso = hora_inicio + timedelta(hours=horas_transcurridas)
                else:
                    # Trayecto local sin cruce: se calcula directo sin demoras ni saltos de huso
                    hora_paso = hora_inicio + timedelta(hours=horas_transcurridas)

                # --- CONSULTA DE CLIMA ---
                data = consultar_datos(lat, lon, FECHA_TRAMO)
                if data:
                    horario = data['hourly']
                    idx = hora_paso.hour 
                    
                    # (Continúa la extracción de variables de clima y guardado en resultados...)
                    
                    # Extracción de variables
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
                    
                    # --- LÓGICA DE ALERTAS ---
                    alertas = []
                    if viento > 45: alertas.append("💨 VIENTO")
                    if temp < 3: alertas.append("❄️ HIELO")
                    if lluvia_prob > 60: alertas.append("🌧️ LLUVIA")
                    
                    # Alerta basada en lluvia_cant (intensidad)
                    if lluvia_cant > 0 and lluvia_cant <= 2: alertas.append("🌦️ LLUVIA DÉBIL")
                    elif lluvia_cant > 2 and lluvia_cant <= 8: alertas.append("🌧️ USA TRAJE")
                    elif lluvia_cant > 8: alertas.append("⚠️ BUSCA TECHO")
                    
                    resultados.append({
                        "KM": int(i * DIST_SUBTRAMO),
                        "HORA": hora_paso.strftime('%H:%M'),
                        "ALERTAS": " | | ".join(alertas),
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

            # --- SECCIÓN DE VISUALIZACIÓN (CÓDIGO IDEAL) ---
            if resultados:
                df_final = pd.DataFrame(resultados)

                st.subheader("📋 Resultados del Análisis")
                st.dataframe(df_final, use_container_width=True)

                # distancia total
                distancia_final = df_final['KM'].max()

                #tiempo total estimado según velocidad promedio
                tiempo_total_horas = distancia_final / VEL_PROMEDIO
                horas = int(tiempo_total_horas)
                minutos = int((tiempo_total_horas - horas) * 60)
                
                st.subheader("🗺️ Trazado de la Ruta y Puntos de Análisis")
                st.markdown(f"##### 📍 **Distancia Total:** {distancia_final} km | ⏳ **Tiempo Estimado:** {horas}h {minutos}min (@{VEL_PROMEDIO} km/h)")
                
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
                    # MAPA SATELITAL
                    map_provider="carto",
                    map_style="light", 
                    initial_view_state=view_state,
                    layers=[capa_ruta, capa_puntos],
                    tooltip={
                        "html": "<b>Subtramo:</b> {KM} km<br/><b>Altitud:</b> {Altitud (msnm)} msnm<br/><b>Hora:</b> {HORA}<br/><b>Alertas:</b> {ALERTAS}",
                        "style": {"backgroundColor": "#002b36", "color": "white"}
                    }
                ))

                # --- Lógica de Descarga ---
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
