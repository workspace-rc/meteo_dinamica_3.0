import requests
import streamlit as st
import pydeck as pdk
import pandas as pd
import numpy as np
from shapely.geometry import LineString
from datetime import datetime, timedelta
import os

# --- 1. CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Navegador Meteorológico Chile", layout="wide")

# --- 2. CONSULTA API: OPEN-METEO ---
def consultar_api(lat, lon, fecha_str):
    """
    Consulta la previsión meteorológica para un punto y fecha específicos.
    """
    dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    # Open-Meteo permite consultar hasta 7-14 días a futuro gratis
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":   round(lat, 4),
        "longitude":  round(lon, 4),
        "start_date": fecha_str,
        "end_date":   fecha_str,
        "hourly": [
            "temperature_2m",
            "precipitation_probability",
            "rain",
            "cloud_cover",
            "windspeed_10m",
            "winddirection_10m",
            "windgusts_10m",
            "surface_pressure",
            "freezing_level_height",
        ],
        "daily":    "sunrise,sunset",
        "timezone": "auto", # Se adapta automáticamente a la zona horaria de Chile/local
        "wind_speed_unit": "kmh",
    }

    r = requests.get(url, params=params, timeout=15)
    data = r.json()

    if 'hourly' not in data:
        raise ValueError(f"Sin datos para ({lat},{lon})")
    return data

# --- 3. INTERFAZ (SIDEBAR) ---
st.sidebar.header("⚙️ Parámetros de Travesía")

# Fecha y Hora
fecha_dt = st.sidebar.date_input("Fecha del tramo", value=datetime.now())
FECHA_TRAMO = fecha_dt.strftime("%Y-%m-%d")
HORA_SALIDA = st.sidebar.text_input("Hora de salida (HH:MM)", value="09:00")

# Variables de navegación
VEL_PROMEDIO = st.sidebar.slider("Velocidad promedio (km/h)", 5, 120, 50)
DIST_SUBTRAMO = st.sidebar.slider("Resolución: Chequeo cada (km)", 5, 100, 20)

# Selector de Archivo de Ruta (.csv)
archivos_disponibles = [f for f in os.listdir('.') if f.startswith("prevision_dia") and f.endswith(".csv")]
archivos_disponibles.sort()

if archivos_disponibles:
    CSV_RUTA = st.sidebar.selectbox("Archivo de ruta:", options=archivos_disponibles)
else:
    st.sidebar.error("❌ No se encontraron archivos de ruta.")
    CSV_RUTA = None

# --- 4. LÓGICA PRINCIPAL ---
def main():
    st.title("🛰️ Navegador Meteorológico Táctico")
    st.info(f"Analizando ruta basada en previsión para el día: **{FECHA_TRAMO}**")

    if CSV_RUTA and st.sidebar.button("🚀 Iniciar Análisis", use_container_width=True):
        try:
            # Cargar y limpiar CSV de ruta
            df_ruta = pd.read_csv(CSV_RUTA)
            for col in ['X', 'Y']:
                if df_ruta[col].dtype == object:
                    df_ruta[col] = pd.to_numeric(df_ruta[col].str.replace('"', '').str.replace("'", ""))
            
            puntos = list(zip(df_ruta['X'], df_ruta['Y']))
            linea = LineString(puntos)
            distancia_total_km = linea.length * 111.1  # Conversión aproximada grado a km
            
            num_subtramos = int(distancia_total_km // DIST_SUBTRAMO) + 1
            hora_inicio = datetime.strptime(f"{FECHA_TRAMO} {HORA_SALIDA}", "%Y-%m-%d %H:%M")
            
            resultados = []
            barra_progreso = st.progress(0)

            for i in range(num_subtramos):
                barra_progreso.progress((i + 1) / num_subtramos)
                
                # Ubicación y tiempo estimado
                pos = (i * DIST_SUBTRAMO) / distancia_total_km
                punto = linea.interpolate(min(pos, 1.0), normalized=True)
                lon, lat = punto.x, punto.y
                hora_paso = hora_inicio + timedelta(hours=(i * DIST_SUBTRAMO) / VEL_PROMEDIO)

                # Consulta API
                try:
                    data = consultar_api(lat, lon, FECHA_TRAMO)
                    horario = data['hourly']
                    tiempos = pd.to_datetime(horario['time'])
                    idx = hora_paso.hour # Índice simplificado por hora
                    
                    # Extraer valores
                    temp = horario['temperature_2m'][idx]
                    viento = horario['windspeed_10m'][idx]
                    nubosidad = horario['cloud_cover'][idx]
                    lluvia_cant = horario['rain'][idx]
                    lluvia_prob = horario['precipitation_probability'][idx]
                    rafagas = horario['windgusts_10m'][idx]
                    presion_api = horario['surface_pressure'][idx]
                    nivel_hielo = horario['freezing_level_height'][idx]

                    sunrise = data['daily']['sunrise'][-1][-5:] if 'daily' in data else '--:--'
                    sunset  = data['daily']['sunset'][-1][-5:]  if 'daily' in data else '--:--'
                
                    # Lógica de Alertas Absolutas
                    alertas = []
                    if viento > 45: alertas.append("💨 VIENTO FUERTE")
                    if temp < 3: alertas.append("❄️ RIESGO HIELO")
                    if prob_lluvia > 70: alertas.append("🌧️ ALTA PROB. LLUVIA")
                    if lluvia_cant > 3: alertas.append("🧥 USA TRAJE")
                    
                    resultados.append({
                        "ALERTAS": " | ".join(alertas),
                        "KM": i * DIST_SUBTRAMO,
                        "Altitud (msnm)": int(altitud_m),
                        "HORA": hora_paso.strftime('%H:%M'),
                        "Primera luz": sunrise,
                        "Última luz": sunset,
                        "Temp (°C)": temp,
                        "Lluvia (mm)": lluvia_cant,
                        "Lluvia (%)": lluvia_prob,
                        "Nubes (%)": nubosidad,
                        "Viento (km/h)": viento,
                        "Ráfagas (km/h)": rafagas,
                        "Presión (hpa)": presion_api,
                        "Altitud 0°C (m)": nivel_hielo,
                        
                        "lat": lat,
                        "lon": lon
                    })
                except Exception as e:
                    continue

            # --- VISUALIZACIÓN ---
            # 1. Preparar datos
                ruta_coords = df_final[['lon', 'lat']].values.tolist()
                view_state = pdk.ViewState(
                    latitude=df_final['lat'].mean(),
                    longitude=df_final['lon'].mean(),
                    zoom=6,
                    pitch=0
                )

                # 2. Definir capas
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
                    get_radius=1500, #radio en metros
                    radius_min_pixels=6,
                    pickable=True,
                )

                # 3. Renderizar
                st.pydeck_chart(pdk.Deck(
                    # Usamos un estilo de mapa base de Pydeck que siempre funciona
                    map_provider="carto",
                    map_style="light", 
                    initial_view_state=view_state,
                    layers=[capa_ruta, capa_puntos],
                    tooltip={
                        "html": "<b>Subtramo:</b> {KM} km<br/><b>Altitud:</b> {Altitud (msnm)} msnm<br/><b>Hora:</b> {HORA}<br/><b>Alertas:</b> {ALERTAS}",
                        "style": {"backgroundColor": "#002b36", "color": "white"}
                    }
                ))

        except Exception as e:
            st.error(f"Hubo un problema al procesar el archivo: {e}")

if __name__ == "__main__":
    main()
