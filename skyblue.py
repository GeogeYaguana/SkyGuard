import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

import requests
import streamlit as st
from streamlit_geolocation import streamlit_geolocation

# -----------------------------
# Configuración
# -----------------------------
st.set_page_config(page_title="School Air Index", page_icon="🏫", layout="centered")
st.title("🏫 School Air Index")

# Clave de API para OpenAQ (reemplázala si tienes la tuya)
OPENAQ_KEY: str = "08f176ffd0ccb07a617b9d9cf0f740366b783adfcef064fcc601a7a636463473"
OPENAQ_BASE: str = "https://api.openaq.org/v3"
HEADERS: dict = {"X-API-Key": OPENAQ_KEY} if OPENAQ_KEY else {}

# -----------------------------
# Estado de la UI
# -----------------------------
if "alert_ozone" not in st.session_state:
    st.session_state.alert_ozone = False
if "search_triggered" not in st.session_state:
    st.session_state.search_triggered = False
if "coords_to_process" not in st.session_state:
    st.session_state.coords_to_process = None

# -----------------------------
# Helpers de Procesamiento
# -----------------------------
def iso_label(dt_iso: Optional[str]) -> Optional[str]:
    """Convierte una fecha ISO (UTC) a un formato de hora local legible."""
    if not dt_iso: return None
    try:
        # Define la zona horaria local (ej: UTC-5 para Ecuador)
        local_tz = timezone(timedelta(hours=-5))
        # Convierte el string ISO (que está en UTC) a un objeto datetime
        dt_utc = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        # Convierte de UTC a la zona horaria local
        dt_local = dt_utc.astimezone(local_tz)
        # Formatea la fecha y hora locales
        return dt_local.strftime("%d/%m/%Y - %I:%M %p")
    except Exception:
        # Si algo sale mal, devuelve el string original para no romper la app
        return dt_iso

def pm25_to_level(pm25: float) -> Tuple[str, str]:
    """Determina el nivel de calidad del aire y la acción recomendada según el PM2.5."""
    if pm25 <= 35.0: return "🟢 Verde (Bueno)", "Actividades normales"
    if pm25 <= 55.0: return "🟡 Amarillo (Moderado)", "Reducir esfuerzo físico"
    return "🔴 Rojo (Insalubre)", "Evitar actividades al aire libre"

# -----------------------------
# Helpers de API (Lógica por Coordenadas)
# -----------------------------
def _request_openaq(endpoint: str, params: Optional[dict] = None) -> dict:
    """Función genérica para hacer solicitudes a la API de OpenAQ."""
    url = f"{OPENAQ_BASE}/{endpoint}"
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=600)
def find_locations_by_coordinates(latitude: float, longitude: float, radius_km: int) -> List[Dict]:
    """Paso 1: Encuentra estaciones de monitoreo por coordenadas."""
    st.info(f"Buscando estaciones en un radio de {radius_km} km...")
    params = {
        "coordinates": f"{latitude},{longitude}",
        "radius": radius_km * 1000,
        "limit": 100,
    }
    try:
        data = _request_openaq("locations", params=params)
        locations = data.get("results", [])
        
        pm25_locations = [
            loc for loc in locations
            if any(s.get("parameter", {}).get("name") == "pm25" for s in loc.get("sensors", []))
        ]
        
        # Ordenamos los resultados por distancia en Python
        sorted_locations = sorted(pm25_locations, key=lambda loc: loc.get('distance', float('inf')))

        st.success(f"Se encontraron {len(sorted_locations)} estaciones con sensor PM2.5 cerca.")
        return sorted_locations
        
    except Exception as e:
        st.error(f"Error al buscar estaciones cercanas: {e}")
        return []

def get_pm25_sensor_id_from_location(location_data: Dict) -> Optional[int]:
    """Paso 2: Extrae el ID del sensor de PM2.5 de una estación."""
    sensors = location_data.get("sensors", [])
    for sensor in sensors:
        if sensor.get("parameter", {}).get("name") == "pm25":
            return sensor.get("id")
    return None

def get_latest_measurement_from_sensor(sensor_id: int) -> Tuple[Optional[float], Optional[str]]:
    """
    Paso 3: Obtiene la última medición de un sensor específico en las últimas 24 horas.
    Busca datos recientes y devuelve el más actual para evitar problemas de ordenamiento en la API.
    """
    # Define el rango de tiempo para la búsqueda (últimas 24 horas)
    now_utc = datetime.now(timezone.utc)
    twenty_four_hours_ago = now_utc - timedelta(hours=24)

    params = {
        "limit": 100,  # Pedimos hasta 100 mediciones en las últimas 24h
        "page": 1,
        "datetime_from": twenty_four_hours_ago.isoformat(),
        "datetime_to": now_utc.isoformat(),
        "order_by": "datetime", # Mantenemos el orden por si funciona, pero no confiaremos solo en él
        "sort": "desc"
    }
    
    data = _request_openaq(f"sensors/{sensor_id}/measurements", params=params)
    results = data.get("results", [])

    if results:
        # Filtramos mediciones que podrían no tener fecha por alguna razón
        valid_results = [r for r in results if r.get("period", {}).get("datetimeTo", {}).get("utc")]
        
        if not valid_results:
            return None, None
            
        # Ordenamos los resultados en Python para GARANTIZAR que obtenemos el más reciente
        sorted_results = sorted(valid_results, key=lambda r: r["period"]["datetimeTo"]["utc"], reverse=True)
        
        latest_measurement = sorted_results[0]
        value = latest_measurement.get("value")
        dt = latest_measurement.get("period", {}).get("datetimeTo", {}).get("utc")
        
        if value is not None:
            return float(value), dt
            
    return None, None

def get_pm25(latitude: float, longitude: float, radius_km: int) -> Tuple[Optional[float], Optional[str], str]:
    """
    Función orquestadora para obtener el dato de PM2.5.
    Busca en todas las estaciones cercanas y devuelve la medición MÁS RECIENTE de todas ellas.
    """
    try:
        candidate_locations = find_locations_by_coordinates(latitude, longitude, radius_km=radius_km)
        if not candidate_locations:
            st.error("No se encontró ninguna estación de monitoreo con sensores PM2.5 cerca.")
            return None, None, "Sin estaciones cercanas"

        valid_measurements = []

        for i, location in enumerate(candidate_locations):
            loc_name = location.get('name', 'Nombre Desconocido')
            distance = location.get('distance')
            dist_label = f"a {distance/1000:.1f} km" if distance is not None else ""
            
            st.info(f"Paso #{i+1}: Revisando estación '{loc_name}' {dist_label}...")
            
            pm25_sensor_id = get_pm25_sensor_id_from_location(location)
            if not pm25_sensor_id:
                st.warning(f"La estación '{loc_name}' no tiene sensor PM2.5. Saltando.")
                continue
            
            v, dt = get_latest_measurement_from_sensor(pm25_sensor_id)
            if v is not None and dt is not None:
                st.write(f"✔️ Dato válido encontrado en '{loc_name}'.")
                valid_measurements.append({
                    "value": v,
                    "dt_iso": dt,
                    "source": f"OpenAQ • {loc_name}"
                })
            else:
                st.write(f"⚠️ El sensor de '{loc_name}' no reportó datos recientes.")

        if not valid_measurements:
            st.error(f"Se revisaron {len(candidate_locations)} estaciones, pero ninguna tiene datos de PM2.5 válidos.")
            return None, None, "Sin datos disponibles"

        # Ordenar las mediciones por fecha para encontrar la más reciente
        most_recent_measurement = sorted(valid_measurements, key=lambda x: x['dt_iso'], reverse=True)[0]
        
        st.success(f"✓ Seleccionada la medición más reciente de todas las estaciones, proveniente de: '{most_recent_measurement['source']}'")
        
        return (
            most_recent_measurement['value'],
            most_recent_measurement['dt_iso'],
            most_recent_measurement['source']
        )

    except requests.exceptions.RequestException as e:
        st.error(f"Error al contactar la API de OpenAQ: {e}")
    except Exception as e:
        st.error(f"Error inesperado: {e}")

    return None, None, "Sin datos disponibles"

# -----------------------------
# UI de la Barra Lateral
# -----------------------------
with st.sidebar:
    st.subheader("📍 Elige tu Ubicación")

    # --- Opción 1: Geolocalización Automática ---
    st.markdown("**Opción A: Usar mi ubicación actual**")
    location_data = streamlit_geolocation()
    if st.button("Buscar en mi Ubicación", use_container_width=True, type="primary"):
        if location_data and location_data.get('latitude'):
            st.session_state.search_triggered = True
            lat = location_data['latitude']
            lon = location_data['longitude']
            st.session_state.coords_to_process = {"lat": lat, "lon": lon}
            st.success(f"Ubicación obtenida: Lat {lat:.4f}, Lon {lon:.4f}")
        else:
            st.error("No se pudo obtener tu ubicación. Asegúrate de dar permisos.")
            st.session_state.search_triggered = False

    # --- Opción 2: Entrada Manual ---
    st.markdown("**Opción B: Ingresar coordenadas**")
    # Usamos Ciudad de México como ejemplo por defecto
    lat_input = st.number_input("Latitud", value=19.4326, format="%.4f", help="Ej: 40.7128 (Nueva York)")
    lon_input = st.number_input("Longitud", value=-99.1332, format="%.4f", help="Ej: -74.0060 (Nueva York)")

    if st.button("Buscar por Coordenadas", use_container_width=True):
        st.session_state.search_triggered = True
        st.session_state.coords_to_process = {"lat": lat_input, "lon": lon_input}
        st.info(f"Usando coords: Lat {lat_input:.4f}, Lon {lon_input:.4f}")
    
    # --- Opciones de Búsqueda ---
    st.write("---")
    st.subheader("⚙️ Opciones de Búsqueda")
    radius_input = st.slider(
        "Radio de búsqueda (km)",
        min_value=1,
        max_value=25,
        value=15,
        help="Define qué tan lejos buscar estaciones de monitoreo."
    )

    # --- Simulación de Alertas ---
    st.write("---")
    st.markdown("**Simulación de Alertas**")
    if st.button("🔴 Activar Alerta Ozono", use_container_width=True): st.session_state.alert_ozone = True
    if st.button("✅ Desactivar Alerta", use_container_width=True): st.session_state.alert_ozone = False

# -----------------------------
# Flujo Principal y UI de Resultado
# -----------------------------
if not st.session_state.search_triggered:
    st.info("👋 ¡Bienvenido! Usa una de las opciones en la barra lateral para buscar la calidad del aire.")
else:
    pm25, dt_iso, source = (None, None, "Sin datos de OpenAQ")
    
    if st.session_state.coords_to_process:
        lat = st.session_state.coords_to_process["lat"]
        lon = st.session_state.coords_to_process["lon"]
        pm25, dt_iso, source = get_pm25(lat, lon, radius_input)
    
    # Prepara la etiqueta de la fecha/hora para la métrica
    datetime_for_metric = ""

    if pm25 is not None:
        pm25_display = pm25
        # Usa la fecha y hora de la MEDICIÓN, convertida a formato local
        datetime_for_metric = iso_label(dt_iso)
    else:
        st.warning("No se pudo obtener un valor real de PM2.5. Usando valor simulado (42 µg/m³).")
        pm25_display = 42.0
        source = "Valor simulado"
        # Para el valor simulado, usa la HORA ACTUAL
        local_tz = timezone(timedelta(hours=-5))
        now_local = datetime.now(local_tz)
        datetime_for_metric = now_local.strftime("%d/%m/%Y - %I:%M %p")

    nivel, accion = pm25_to_level(pm25_display)
    if st.session_state.alert_ozone:
        nivel = "🔴 Rojo (TEMPO Ozono)"; accion = "Ozono elevado: Evitar actividades al aire libre"
        st.info("🚨 **Alerta de Ozono (TEMPO) activa.**")

    # Se modifica el label de la métrica para usar la fecha/hora correspondiente
    st.metric(f"PM2.5 (µg/m³) - {datetime_for_metric}", f"{pm25_display:.1f}", help="Partículas Finas (≤ 2.5µm).")
    
    st.subheader(f"Índice de Calidad del Aire: {nivel}")
    if "Verde" in nivel: st.success(f"**Acción:** {accion}")
    elif "Amarillo" in nivel: st.warning(f"**Acción:** {accion}")
    else: st.error(f"**Acción:** {accion}")

    st.write("### Recomendaciones Deportivas")
    c1, c2, c3 = st.columns(3)
    def colorize_sport(label: str, level: str) -> str:
        if "Verde" in nivel: return f"🟢 **{label}** — OK"
        if "Amarillo" in nivel: return f"🟡 **{label}** — Precauciones"
        return f"🔴 **{label}** — No recomendado"
    with c1: st.markdown(colorize_sport("⚽ Fútbol", nivel))
    with c2: st.markdown(colorize_sport("🏃 Atletismo", nivel))
    with c3: st.markdown(colorize_sport("🤸 Recreación", nivel))

    footer = f"**Fuente PM2.5:** {source}"
    # Si no es simulado, muestra la fecha de la última actualización
    if "simulado" not in source and datetime_for_metric:
        footer += f" • **Última Actualización:** {datetime_for_metric}"
    if st.session_state.alert_ozone:
        footer += " • **Alerta:** Datos TEMPO simulados para demo"
    st.caption(footer)


