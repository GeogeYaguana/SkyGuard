import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

import requests
import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import folium
from streamlit_folium import st_folium
# Import WAQI as fallback
from data_sources.waqi import get_waqi_by_coordinates

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

def is_data_fresh(dt_iso: Optional[str], max_age_days: int = 7) -> bool:
    """Verifica si los datos son frescos (no más antiguos que max_age_days días)."""
    if not dt_iso:
        return False
    
    try:
        # Convierte el string ISO a datetime
        dt_utc = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)
        age_days = (now_utc - dt_utc).days
        return age_days <= max_age_days
    except Exception:
        return False

def get_pm25_from_waqi(latitude: float, longitude: float) -> Tuple[Optional[float], Optional[str], str]:
    """Obtiene datos de PM2.5 desde WAQI como fallback."""
    try:
        st.info("🔄 Intentando obtener datos desde WAQI como respaldo...")
        waqi_measurements = get_waqi_by_coordinates(latitude, longitude)
        
        if waqi_measurements:
            # Buscar medición de PM2.5
            pm25_measurement = None
            for measurement in waqi_measurements:
                if measurement.parameter == 'pm25':
                    pm25_measurement = measurement
                    break
            
            if pm25_measurement:
                st.success(f"✅ Datos encontrados en WAQI: {pm25_measurement.value:.1f} µg/m³")
                return (
                    pm25_measurement.value,
                    pm25_measurement.date.isoformat(),
                    f"WAQI • {pm25_measurement.location}"
                )
        
        st.warning("⚠️ WAQI no tiene datos de PM2.5 para esta ubicación.")
        return None, None, "WAQI sin datos PM2.5"
        
    except Exception as e:
        st.error(f"Error al consultar WAQI: {e}")
        return None, None, "Error WAQI"

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
    Si no encuentra datos frescos en OpenAQ, usa WAQI como fallback.
    """
    try:
        candidate_locations = find_locations_by_coordinates(latitude, longitude, radius_km=radius_km)
        if not candidate_locations:
            st.warning("No se encontró ninguna estación de monitoreo con sensores PM2.5 cerca en OpenAQ.")
            st.info("🔄 Intentando con WAQI como respaldo...")
            return get_pm25_from_waqi(latitude, longitude)

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
                # Verificar si los datos son frescos (máximo 7 días)
                if is_data_fresh(dt, max_age_days=7):
                    st.write(f"✔️ Dato válido y fresco encontrado en '{loc_name}'.")
                    valid_measurements.append({
                        "value": v,
                        "dt_iso": dt,
                        "source": f"OpenAQ • {loc_name}"
                    })
                else:
                    st.write(f"⚠️ Datos de '{loc_name}' son muy antiguos (más de 7 días).")
            else:
                st.write(f"⚠️ El sensor de '{loc_name}' no reportó datos recientes.")

        if not valid_measurements:
            st.warning(f"Se revisaron {len(candidate_locations)} estaciones en OpenAQ, pero ninguna tiene datos de PM2.5 frescos.")
            st.info("🔄 Intentando con WAQI como respaldo...")
            return get_pm25_from_waqi(latitude, longitude)

        # Ordenar las mediciones por fecha para encontrar la más reciente
        most_recent_measurement = sorted(valid_measurements, key=lambda x: x['dt_iso'], reverse=True)[0]
        
        st.success(f"✓ Seleccionada la medición más reciente de OpenAQ: '{most_recent_measurement['source']}'")
        
        return (
            most_recent_measurement['value'],
            most_recent_measurement['dt_iso'],
            most_recent_measurement['source']
        )

    except requests.exceptions.RequestException as e:
        st.error(f"Error al contactar la API de OpenAQ: {e}")
        st.info("🔄 Intentando con WAQI como respaldo...")
        return get_pm25_from_waqi(latitude, longitude)
    except Exception as e:
        st.error(f"Error inesperado: {e}")
        st.info("🔄 Intentando con WAQI como respaldo...")
        return get_pm25_from_waqi(latitude, longitude)

# --------------------------
# Helpers para pintar cada estación según su PM2.5
# --------------------------
def get_color_and_opacity(pm25: float) -> Tuple[str, float]:
    """Devuelve color + intensidad tipo semáforo según el valor de PM2.5."""
    if pm25 <= 35:
        color = "green"
        opacity = 0.15 + (pm25 / 35) * (0.6 - 0.15)
    elif pm25 <= 55:
        color = "orange"
        opacity = 0.15 + ((pm25 - 35) / (55 - 35)) * (0.6 - 0.15)
    else:
        color = "red"
        max_val = min(pm25, 150)  # limitamos para no explotar
        opacity = 0.15 + ((max_val - 55) / (150 - 55)) * (0.6 - 0.15)
    return color, opacity

def get_pm25_for_station(location: Dict) -> Optional[float]:
    """Obtiene el último valor de PM2.5 para una estación específica."""
    sensor_id = get_pm25_sensor_id_from_location(location)
    if not sensor_id:
        return None
    v, _ = get_latest_measurement_from_sensor(sensor_id)
    return v





# -----------------------------
# UI de la Barra Lateral (Sin cambios)
# -----------------------------
with st.sidebar:
    st.image("logo.png", width=150) # El logo tendrá un ancho de 150 píxeles    st.subheader("📍 Elige tu Ubicación")

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

    # --- Simulación de Alertas (en un expander para no distraer) ---
    with st.expander("Simulación de Alertas (Demo)"):
        if st.button("🔴 Activar Alerta Ozono", use_container_width=True): st.session_state.alert_ozone = True
        if st.button("✅ Desactivar Alerta", use_container_width=True): st.session_state.alert_ozone = False


# -----------------------------
# Flujo Principal y UI de Resultado
# -----------------------------

# --- Pantalla de Bienvenida ---
if not st.session_state.search_triggered:
    st.markdown("### ¡Hola, docente! 🍎")
    st.markdown(
        "Esta herramienta te ayuda a conocer la calidad del aire cerca de tu escuela "
        "para tomar decisiones informadas sobre las actividades al aire libre."
    )
    st.info("👋 **Para comenzar, usa una de las opciones en el panel de la izquierda.**")
    st.image("https://i.imgur.com/fA2x1Bq.png", caption="Los niveles de calidad del aire se miden con un semáforo de colores.")


# --- Pantalla de Resultados ---
else:
    pm25, dt_iso, source = (None, None, "Sin datos de OpenAQ")
    
    with st.spinner("Buscando datos de calidad del aire... 🛰️"):
        if st.session_state.coords_to_process:
            lat = st.session_state.coords_to_process["lat"]
            lon = st.session_state.coords_to_process["lon"]

            # Ocultamos el log técnico en un expander para una UI más limpia
            with st.expander("Ver proceso de búsqueda detallado..."):
                pm25, dt_iso, source = get_pm25(lat, lon, radius_input)
    
    # Prepara la etiqueta de la fecha/hora para la métrica
    datetime_for_metric = ""
    if pm25 is not None:
        pm25_display = pm25
        datetime_for_metric = iso_label(dt_iso)
    else:
        st.warning("No se pudo obtener un valor real de PM2.5. Mostrando un valor de ejemplo.")
        pm25_display = 42.0
        source = "Valor simulado"
        local_tz = timezone(timedelta(hours=-5))
        now_local = datetime.now(local_tz)
        datetime_for_metric = now_local.strftime("%d/%m/%Y - %I:%M %p")

    nivel, accion = pm25_to_level(pm25_display)
    if st.session_state.alert_ozone:
        nivel = "🔴 Rojo (TEMPO Ozono)"; accion = "Ozono elevado: Evitar actividades al aire libre"
        st.info("🚨 **Alerta de Ozono (TEMPO) activa.**")

    # --- Tarjeta de Resumen Principal ---
    st.subheader("Resumen de Calidad del Aire 🌬️")
    with st.container(border=True):
        col1, col2 = st.columns([0.4, 0.6])
        with col1:
            st.metric(f"PM2.5 (µg/m³)", f"{pm25_display:.1f}", help="Partículas Finas (≤ 2.5µm).")
            st.caption(f"Medición de las {datetime_for_metric}")
        with col2:
            st.subheader(f"Nivel: {nivel}")
            if "Verde" in nivel: st.success(f"**Recomendación Principal:** {accion}")
            elif "Amarillo" in nivel: st.warning(f"**Recomendación Principal:** {accion}")
            else: st.error(f"**Recomendación Principal:** {accion}")

    st.divider()

    # --- Sección Educativa y Recomendaciones Detalladas ---
    st.subheader("Recomendaciones para el Entorno Escolar 🏫")
    
    with st.expander("🟢 **Nivel Bueno**: ¿Qué significa?", expanded="Verde" in nivel):
        st.markdown(
            """
            - **Actividades al aire libre:** ¡Adelante! Es un buen día para que los estudiantes disfruten del patio, deportes y recreo sin restricciones.
            - **Ventilación:** Se recomienda abrir las ventanas de las aulas para permitir la circulación de aire fresco.
            - **Grupos sensibles:** No se esperan riesgos para la salud.
            """
        )

    with st.expander("🟡 **Nivel Moderado**: ¿Qué significa?", expanded="Amarillo" in nivel):
        st.markdown(
            """
            - **Actividades al aire libre:** Se pueden realizar, pero considere reducir la intensidad de los ejercicios prolongados (ej. carreras largas).
            - **Ventilación:** Ventile las aulas, pero esté atento a posibles olores o bruma en el exterior.
            - **Grupos sensibles:** Estudiantes con asma o problemas respiratorios podrían experimentar síntomas. Aconséjeles tomarlo con calma.
            """
        )

    with st.expander("🔴 **Nivel Insalubre**: ¿Qué significa?", expanded="Rojo" in nivel):
        st.markdown(
            """
            - **Actividades al aire libre:** **Deben evitarse.** Cancele o posponga las clases de educación física, recreos y cualquier evento al aire libre.
            - **Ventilación:** **Mantenga las ventanas de las aulas cerradas** para evitar que la contaminación ingrese a los espacios interiores.
            - **Grupos sensibles:** Todos los estudiantes, especialmente aquellos con condiciones preexistentes, están en riesgo. Monitoree de cerca cualquier síntoma como tos o dificultad para respirar.
            """
        )

    st.divider()

    # --- Mapa Visual ---
    st.subheader("🗺️ Mapa de Monitoreo en tu Zona")
    import folium
    from streamlit_folium import st_folium

    m = folium.Map(location=[lat, lon], zoom_start=11)

    # 1. Ubicación del usuario
    folium.Marker(
        [lat, lon],
        popup="📍 Escuela / Punto de Búsqueda",
        tooltip="Tu ubicación",
        icon=folium.Icon(color="blue", icon="school", prefix="fa")
    ).add_to(m)

    # 2. Estaciones OpenAQ (cada una con su valor real)
    candidate_locations = find_locations_by_coordinates(lat, lon, radius_km=radius_input)

    for loc in candidate_locations:
        coords = loc["coordinates"]["latitude"], loc["coordinates"]["longitude"]
        station_name = loc.get("name", "Estación sin nombre")
        pm25_value = get_pm25_for_station(loc)
        
        if pm25_value is None:
            continue

        color, opacity = get_color_and_opacity(pm25_value)

        # Marcador con icono
        folium.Marker(
            coords,
            popup=f"{station_name}<br>PM2.5: {pm25_value:.1f} µg/m³",
            tooltip=f"{station_name} - {pm25_value:.1f} µg/m³",
            icon=folium.Icon(color=color, icon="cloud")
        ).add_to(m)

        # Círculo de influencia visual para cada estación
        folium.Circle(
            location=coords,
            radius=500,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=opacity,
            tooltip=f"{station_name} (PM2.5: {pm25_value:.1f})"
        ).add_to(m)

    # 3. Círculo del radio de búsqueda general
    circle_color, opacity = get_color_and_opacity(pm25_display)
    folium.Circle(
        location=[lat, lon],
        radius=radius_input * 1000,
        color=circle_color,
        weight=2,
        fill=True,
        fill_color=circle_color,
        fill_opacity=max(0.1, opacity - 0.1), # Hacemos el círculo grande más tenue
        tooltip=f"Radio de búsqueda: {radius_input} km (PM2.5 promedio: {pm25_display:.1f})"
    ).add_to(m)
    
    st_folium(m, width=750, height=500)

    # --- Footer ---
    st.divider()
    footer = f"**Fuente de Datos Principal:** {source}"
    if st.session_state.alert_ozone:
        footer += " • **Alerta:** Datos TEMPO simulados para demo"
    st.caption(footer)