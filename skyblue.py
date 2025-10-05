import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

import requests
import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import folium
from streamlit_folium import st_folium
from streamlit_option_menu import option_menu # <-- Importación nueva

# Import WAQI as fallback
# Asegúrate de que este archivo exista en tu proyecto: from data_sources.waqi import get_waqi_by_coordinates
from data_sources.waqi import get_waqi_stations_nearby

# -----------------------------
# Configuración
# -----------------------------
st.set_page_config(page_title="School Air Index", page_icon="🏫", layout="wide") # Cambiado a layout="wide"
st.title("🏫 School Air Index")

# -----------------------------
# BARRA DE NAVEGACIÓN SUPERIOR
# -----------------------------
page = option_menu(
    menu_title=None,
    options=["Inicio", "Impacto en la Salud", "Guía de Recomendaciones"],
    icons=["house-heart-fill", "lungs-fill", "clipboard2-check-fill"],  # Iconos de Bootstrap
    orientation="horizontal",
    styles={
        "container": {"padding": "0!important", "background-color": "#fafafa", "border-radius": "8px"},
        "icon": {"color": "#036A99", "font-size": "22px"},
        "nav-link": {
            "font-size": "16px",
            "text-align": "center",
            "margin": "0px",
            "--hover-color": "#eee",
        },
        "nav-link-selected": {"background-color": "#009E73"},
    }
)

# -----------------------------
# Estado de la UI y Helpers (SIN CAMBIOS)
# -----------------------------
if "alert_ozone" not in st.session_state:
    st.session_state.alert_ozone = False
if "search_triggered" not in st.session_state:
    st.session_state.search_triggered = False
if "coords_to_process" not in st.session_state:
    st.session_state.coords_to_process = None
if "openaq_rate_limited" not in st.session_state:
    st.session_state.openaq_rate_limited = False
if "last_query" not in st.session_state:
    st.session_state.last_query = None
if "last_result" not in st.session_state:
    st.session_state.last_result = {"pm25": None, "dt_iso": None, "source": "Sin datos"}
if "last_search_log" not in st.session_state:
    st.session_state.last_search_log = []

# (Aquí van todas tus funciones helper: iso_label, pm25_to_level, find_locations_by_coordinates, etc.
#  Las omito aquí por brevedad, pero deben estar en tu script)
# --- Pega aquí todas tus funciones helper ---
OPENAQ_KEY: str = "08f176ffd0ccb07a617b9d9cf0f740366b783adfcef064fcc601a7a636463473"
OPENAQ_BASE: str = "https://api.openaq.org/v3"
HEADERS: dict = {"X-API-Key": OPENAQ_KEY} if OPENAQ_KEY else {}
# Limitar cantidad de estaciones consultadas para evitar 429 (rate limit)
DEFAULT_MAX_STATIONS_TO_QUERY: int = 10

# Carga segura de secretos: primero st.secrets (si existe), luego variables de entorno
def _get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets.get(name))
    except Exception:
        pass
    return os.getenv(name, default)

# Twilio WhatsApp (por defecto usa credenciales de prueba locales; en prod sobreescribe con secrets/env)
TWILIO_ACCOUNT_SID = _get_secret("TWILIO_ACCOUNT_SID", "ACf307b067a65d0c6791bbfe0e27f2242c")
TWILIO_AUTH_TOKEN = _get_secret("TWILIO_AUTH_TOKEN", "49310e467520a35272f8378ead242dce")
TWILIO_WHATSAPP_FROM = _get_secret("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")  # sandbox
# Content API (opcional)
TWILIO_CONTENT_SID = _get_secret("TWILIO_CONTENT_SID", "HXb5b62575e6e4ff6129ad7c8efe1f983e")
# Variables de contenido (JSON string)
TWILIO_CONTENT_VARIABLES = _get_secret("TWILIO_CONTENT_VARIABLES", '{"1":"12/1","2":"3pm"}')
# Lista de destinatarios de WhatsApp (quemados). Reemplaza con tus números.
TWILIO_WHATSAPP_RECIPIENTS: List[str] = [
    "whatsapp:+593995532793",
    "whatsapp:+593939972193",
]

def _twilio_config_check() -> Optional[str]:
    """Return an error message if Twilio config looks invalid; otherwise None."""
    sid = (TWILIO_ACCOUNT_SID or "").strip()
    token = (TWILIO_AUTH_TOKEN or "").strip()
    sender = (TWILIO_WHATSAPP_FROM or "").strip()
    # Detectar placeholders conocidos para advertir
    if not sid or not token:
        return "Faltan credenciales de Twilio. Define TWILIO_ACCOUNT_SID y TWILIO_AUTH_TOKEN."
    if not sid.startswith("AC") or len(sid) < 30:
        return "TWILIO_ACCOUNT_SID inválido. Debe empezar con 'AC' y ser el SID de cuenta."
    if len(token) < 20:
        return "TWILIO_AUTH_TOKEN parece inválido. Verifica el token de tu cuenta."
    if not sender.startswith("whatsapp:+"):
        return "TWILIO_WHATSAPP_FROM inválido. Usa el formato 'whatsapp:+<código><número>'."
    return None

def send_whatsapp_message(body: str, to_number: str, from_number: str, *, content_sid: Optional[str] = None, content_variables_json: Optional[str] = None) -> bool:
    """Envía un WhatsApp usando la API de Twilio (sandbox compatible). Retorna True si fue exitoso."""
    try:
        if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
            return False
        # Normalizar prefijos whatsapp:
        def normalize(num: str) -> str:
            num = (num or '').strip()
            if num.startswith("whatsapp:"):
                return num
            return f"whatsapp:{num}"
        to_w = normalize(to_number)
        from_w = normalize(from_number)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        # Si se proporciona ContentSid (plantilla), usar Content API
        data = {"To": to_w, "From": from_w}
        if content_sid:
            data["ContentSid"] = content_sid
            if content_variables_json:
                data["ContentVariables"] = content_variables_json
        else:
            data["Body"] = body
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=20)
        # 201 Created en éxito
        if resp.status_code in (200, 201):
            return True
        # Mostrar diagnóstico útil en la UI
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text[:400] if isinstance(resp.text, str) else str(resp.text)}
        st.error(
            f"Twilio WhatsApp falló (HTTP {resp.status_code}). Detalles: "
            f"{payload.get('message') or payload.get('error_message') or payload}"
        )
        return False
    except Exception:
        return False

def send_bulk_whatsapp(body: str, *, use_content_template: bool = True) -> int:
    """Envía el mismo mensaje a todos los números en TWILIO_WHATSAPP_RECIPIENTS. Retorna cuántos se enviaron con éxito."""
    success_count = 0
    for to_number in TWILIO_WHATSAPP_RECIPIENTS:
        if use_content_template:
            ok = send_whatsapp_message(
                body,
                to_number,
                TWILIO_WHATSAPP_FROM,
                content_sid=TWILIO_CONTENT_SID,
                content_variables_json=TWILIO_CONTENT_VARIABLES,
            )
        else:
            ok = send_whatsapp_message(body, to_number, TWILIO_WHATSAPP_FROM)
        if ok:
            success_count += 1
    return success_count
from typing import Optional, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _create_retry_session(total: int = 3, backoff_factor: float = 0.8) -> requests.Session:
    """Create a requests Session with retry/backoff for transient network errors."""
    session = requests.Session()
    retry = Retry(
        total=total,
        read=total,
        connect=total,
        status=total,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

@st.cache_data(ttl=3600) # Cache results for an hour to avoid repeated API calls
def get_coords_from_city(city_name: str) -> Optional[Tuple[float, float]]:
    """
    Obtiene coordenadas para una ciudad usando Nominatim (OSM) con reintentos y
    cabeceras adecuadas. Si falla o no hay resultados, usa Open‑Meteo Geocoding
    como respaldo.
    """
    city_query = (city_name or "").strip()
    if not city_query:
        return None

    session = _create_retry_session()

    # 1) Intento con Nominatim (respetando política de uso)
    try:
        nominatim_url = "https://nominatim.openstreetmap.org/search"
        params = {"q": city_query, "format": "json", "limit": 1}
        headers = {
            # Incluir email o url de contacto según política de Nominatim
            "User-Agent": "SchoolAirIndex/1.0 (contact: your-email@example.com)",
            "Accept": "application/json",
            "Accept-Language": "es",
            "Referer": "https://school-air-index.app/",
        }
        response = session.get(nominatim_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        if isinstance(results, list) and results:
            lat = float(results[0].get("lat"))
            lon = float(results[0].get("lon"))
            return lat, lon
    except requests.exceptions.RequestException as e:
        # Continuar con fallback
        st.info("No se pudo contactar a Nominatim. Probando proveedor alternativo…")
    except Exception as e:
        # Cualquier otro error: continuar con fallback
        st.info("Hubo un problema al interpretar la respuesta de Nominatim. Probando proveedor alternativo…")

    # 2) Fallback con Open‑Meteo Geocoding API
    try:
        om_url = "https://geocoding-api.open-meteo.com/v1/search"
        # Open‑Meteo soporta atributos como language y count. Usa name con la cadena completa.
        params = {"name": city_query, "count": 1, "language": "es", "format": "json"}
        headers = {"User-Agent": "SchoolAirIndex/1.0 (contact: your-email@example.com)", "Accept": "application/json"}
        resp = session.get(om_url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json() or {}
        results = data.get("results") or []
        if isinstance(results, list) and results:
            first = results[0]
            lat = float(first.get("latitude"))
            lon = float(first.get("longitude"))
            return lat, lon
        return None
    except requests.exceptions.RequestException as e:
        st.error(
            "Error de red al geocodificar. Verifica conectividad del servidor y políticas del proveedor."
        )
        return None
    except Exception as e:
        st.error("Ocurrió un error inesperado al geocodificar la ciudad.")
        return None
def iso_label(dt_iso: Optional[str]) -> Optional[str]:
    if not dt_iso: return None
    try:
        local_tz = timezone(timedelta(hours=-5))
        dt_utc = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt_local = dt_utc.astimezone(local_tz)
        return dt_local.strftime("%d/%m/%Y - %I:%M %p")
    except Exception: return dt_iso
def pm25_to_level(pm25: float) -> Tuple[str, str]:
    if pm25 <= 35.0: return "🟢 Verde (Bueno)", "Actividades normales"
    if pm25 <= 55.0: return "🟡 Amarillo (Moderado)", "Reducir esfuerzo físico"
    return "🔴 Rojo (Insalubre)", "Evitar actividades al aire libre"
def _request_openaq(endpoint: str, params: Optional[dict] = None) -> dict:
    url = f"{OPENAQ_BASE}/{endpoint}"
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()
@st.cache_data(ttl=600)
def find_locations_by_coordinates(latitude: float, longitude: float, radius_km: int) -> List[Dict]:
    msg = f"Buscando estaciones en un radio de {radius_km} km..."
    st.session_state.last_search_log.append({"level": "info", "text": msg})
    st.info(msg)
    params = {"coordinates": f"{latitude},{longitude}", "radius": radius_km * 1000, "limit": 100}
    try:
        data = _request_openaq("locations", params=params)
        locations = data.get("results", [])
        pm25_locations = [loc for loc in locations if any(s.get("parameter", {}).get("name") == "pm25" for s in loc.get("sensors", []))]
        sorted_locations = sorted(pm25_locations, key=lambda loc: loc.get('distance', float('inf')))
        msg_ok = f"Se encontraron {len(sorted_locations)} estaciones con sensor PM2.5 (Calidad del aire) cerca."
        st.session_state.last_search_log.append({"level": "success", "text": msg_ok})
        st.success(msg_ok)
        return sorted_locations
    except Exception as e:
        err = f"Error al buscar estaciones cercanas: {e}"
        st.session_state.last_search_log.append({"level": "error", "text": err})
        st.error(err)
        return []
def get_pm25_sensor_id_from_location(location_data: Dict) -> Optional[int]:
    sensors = location_data.get("sensors", [])
    for sensor in sensors:
        if sensor.get("parameter", {}).get("name") == "pm25":
            return sensor.get("id")
    return None
def get_latest_measurement_from_sensor(sensor_id: int) -> Tuple[Optional[float], Optional[str]]:
    now_utc, twenty_four_hours_ago = datetime.now(timezone.utc), datetime.now(timezone.utc) - timedelta(hours=24)
    params = {"limit": 100, "page": 1, "datetime_from": twenty_four_hours_ago.isoformat(), "datetime_to": now_utc.isoformat(), "order_by": "datetime", "sort": "desc"}
    try:
        data = _request_openaq(f"sensors/{sensor_id}/measurements", params=params)
    except requests.exceptions.HTTPError as http_err:
        # Manejo especial para 429
        if getattr(http_err, 'response', None) is not None and http_err.response is not None and http_err.response.status_code == 429:
            # Marcar en sesión para mostrar un único aviso más adelante
            st.session_state.openaq_rate_limited = True
            return None, None
        raise
    results = data.get("results", [])
    if results:
        valid_results = [r for r in results if r.get("period", {}).get("datetimeTo", {}).get("utc")]
        if not valid_results: return None, None
        sorted_results = sorted(valid_results, key=lambda r: r["period"]["datetimeTo"]["utc"], reverse=True)
        latest_measurement = sorted_results[0]
        value, dt = latest_measurement.get("value"), latest_measurement.get("period", {}).get("datetimeTo", {}).get("utc")
        if value is not None: return float(value), dt
    return None, None
def get_pm25(latitude: float, longitude: float, radius_km: int) -> Tuple[Optional[float], Optional[str], str]:
    try:
        # resetear el historial para esta búsqueda
        st.session_state.last_search_log = []
        candidate_locations = find_locations_by_coordinates(latitude, longitude, radius_km=radius_km)
        if not candidate_locations:
            warn1 = "No se encontró ninguna estación de monitoreo con sensores PM2.5 cerca en OpenAQ."
            st.session_state.last_search_log.append({"level": "warning", "text": warn1})
            st.warning(warn1)
            return None, None, "No se encontraron estaciones"
        # Limitar el número de estaciones a consultar para evitar rate limit
        limited_locations = candidate_locations[:DEFAULT_MAX_STATIONS_TO_QUERY]
        valid_measurements = []
        # Resetear aviso de rate limit por cada búsqueda
        st.session_state.openaq_rate_limited = False
        for i, location in enumerate(limited_locations):
            loc_name, distance = location.get('name', 'N/A'), location.get('distance')
            dist_label = f"a {distance/1000:.1f} km" if distance is not None else ""
            step = f"Paso #{i+1}: Revisando '{loc_name}' {dist_label}..."
            st.session_state.last_search_log.append({"level": "info", "text": step})
            st.info(step)
            pm25_sensor_id = get_pm25_sensor_id_from_location(location)
            if not pm25_sensor_id: continue
            v, dt = get_latest_measurement_from_sensor(pm25_sensor_id)
            if v is not None and dt is not None: valid_measurements.append({"value": v, "dt_iso": dt, "source": f"OpenAQ • {loc_name}"})
        # Mostrar un único aviso si hubo límite de tasa
        if st.session_state.get('openaq_rate_limited'):
            rate = "Has alcanzado el límite de solicitudes de OpenAQ. Algunas estaciones no pudieron consultarse."
            st.session_state.last_search_log.append({"level": "warning", "text": rate})
            st.warning(rate)
        if not valid_measurements:
            warn2 = "Ninguna estación cercana reportó datos de PM2.5 frescos."
            st.session_state.last_search_log.append({"level": "warning", "text": warn2})
            st.warning(warn2)
            return None, None, "Estaciones sin datos frescos"
        most_recent = sorted(valid_measurements, key=lambda x: x['dt_iso'], reverse=True)[0]
        ok = f"✓ Usando la medición más reciente de: '{most_recent['source']}'"
        st.session_state.last_search_log.append({"level": "success", "text": ok})
        st.success(ok)
        return (most_recent['value'], most_recent['dt_iso'], most_recent['source'])
    except Exception as e:
        st.error(f"Error inesperado: {e}")
        return None, None, "Error en la aplicación"
def get_color_and_opacity(pm25: float) -> Tuple[str, float]:
    if pm25 <= 35: color, opacity = "green", 0.15 + (pm25 / 35) * 0.45
    elif pm25 <= 55: color, opacity = "orange", 0.15 + ((pm25 - 35) / 20) * 0.45
    else: color, opacity = "red", 0.15 + (min(pm25, 150) - 55) / 95 * 0.45
    return color, opacity
def get_pm25_for_station(location: Dict) -> Optional[float]:
    sensor_id = get_pm25_sensor_id_from_location(location)
    if not sensor_id: return None
    v, _ = get_latest_measurement_from_sensor(sensor_id)
    return v
# --- Fin de las funciones Helper ---


# -----------------------------
# UI de la Barra Lateral (CONTENIDO DINÁMICO)
# -----------------------------
with st.sidebar:
    st.image("logo.png", use_container_width=True)
    st.divider()

    if page == "Inicio":
        st.info("ℹ️ Para buscar por tu ubicación actual,realiza click en el icono inferior luego tu navegador te pedirá permiso. Por favor, haz clic en 'Permitir'.")
        location_data = streamlit_geolocation()
        if st.button("Buscar en mi Ubicación", use_container_width=True, type="primary"):
            if location_data and location_data.get('latitude'):
                st.session_state.search_triggered = True
                st.session_state.coords_to_process = {"lat": location_data['latitude'], "lon": location_data['longitude']}
            else:
                st.error("No se pudo obtener tu ubicación. Asegúrate de dar permisos.")
        st.markdown("**Opción B: Buscar por Sector**")
        city_input = st.text_input("Ingresa un sector,ciudad y el pais de la ciudad(ej: 'Tarqui,Guayaquil,Ecuador')")

        if st.button("Buscar por Ciudad", use_container_width=True):
            if city_input:
                # Llama a la nueva función que definimos antes
                coords = get_coords_from_city(city_input)
                if coords:
                    lat, lon = coords
                    st.success(f"📍 Ciudad encontrada. Usando coordenadas: {lat:.4f}, {lon:.4f}")
                    
                    # Actualiza el estado de la sesión para iniciar la búsqueda
                    st.session_state.search_triggered = True
                    st.session_state.coords_to_process = {"lat": lat, "lon": lon}
                else:
                    st.error("No se pudo encontrar la ciudad. Intenta ser más específico (ej: 'Ciudad, País').")
            else:
                st.warning("Por favor, ingresa el nombre de una ciudad para buscar.")
        # --- FIN DE LA NUEVA SECCIÓN ---
        st.write("---")
        st.subheader("⚙️ Opciones de Búsqueda")
        radius_input = st.slider("Radio de búsqueda (km)", 1, 25, 15)
        st.session_state.radius_input = radius_input
        # El límite de estaciones a revisar está fijado en DEFAULT_MAX_STATIONS_TO_QUERY
        with st.expander("Simulación de Alertas (Demo)"):
            st.caption("Enviar aviso por WhatsApp a destinatarios preconfigurados (Twilio)")
            if st.button("🔴 Activar Alerta Ozono", use_container_width=True):
                st.session_state.alert_ozone = True
                msg = "🔴 Rojo (TEMPO Ozono) — Ozono elevado: Evitar actividades al aire libre"
                sent = send_bulk_whatsapp(msg, use_content_template=False)
                if sent > 0: st.success(f"WhatsApp enviado a {sent} destinatario(s)")
                else: st.warning("No se envió ningún WhatsApp. Revisa la configuración de Twilio o la lista de destinatarios.")
            if st.button("✅ Desactivar Alerta", use_container_width=True):
                st.session_state.alert_ozone = False
                msg = "✅ Alerta de Ozono desactivada"
                sent = send_bulk_whatsapp(msg, use_content_template=False)
                if sent > 0: st.success(f"WhatsApp enviado a {sent} destinatario(s)")
                else: st.warning("No se envió ningún WhatsApp. Revisa la configuración de Twilio o la lista de destinatarios.")
    else:
        st.info("Esta es una herramienta para monitorear la calidad del aire en entornos escolares.")
        st.success("Selecciona 'Inicio' en la barra superior para realizar una nueva búsqueda.")

# -----------------------------
# Contenido Principal por Página
# -----------------------------

# -----------------------------
# Contenido Principal por Página
# -----------------------------

# --- PÁGINA 1: INICIO ---

if page == "Inicio":
    if not st.session_state.search_triggered:
        # --- PANTALLA DE BIENVENIDA CON CALLOUT AÑADIDO ---
        st.markdown("### ¡Hola, docente! 🍎")
        st.markdown(
            "Bienvenido/a al **School Air Index**. Esta herramienta te ayuda a tomar decisiones informadas sobre las "
            "actividades al aire libre para proteger la salud de tus estudiantes."
        )
        st.info("#### Sigue estos sencillos pasos para empezar:")
        col1, col2 = st.columns([0.5, 0.5], gap="large")
        with col1:
            st.subheader("Paso 1: Selecciona tu ubicación")
            st.markdown("""
            Usa el **panel de la izquierda** para indicarnos dónde te encuentras. Tienes dos opciones:
            """)

            # Opción A
            st.markdown("##### A) Usar tu Ubicación Actual por  🛰️")
            st.markdown("Primero debes dirigirte a la izquierda y hacer clic en el siguiente  icono:.")
            st.image(
                "icono.jpg",
                width=80,
            )
            st.markdown('Segundo: Debes hacer click en el boton rojo "Buscar en mi ubicacion"')

            st.warning("**Importante:** Después de hacer clic, tu navegador mostrará una ventana emergente. **¡Es crucial que selecciones 'Permitir' en esa solicitud!**")
            st.markdown("---")

            # Opción B
            st.markdown("##### B) Ingresar por Sector✍️")
            st.markdown('Escriba en el siguiente formato "sector,ciudad,pais" y haga click en buscar por sector ')
            

        with col2:
            st.subheader("Paso 2: Analiza el Informe 📊")
            st.markdown("""
            Una vez que busques, aparecerá un informe con:
            - Un **resumen claro** con el nivel de PM2.5 (Indice de Contaminacion del aire) y un código de colores (🟢, 🟡, 🔴).
            - Un **mapa interactivo** con tu ubicación y las estaciones cercanas.
            - **Recomendaciones rápidas** para el día.
            """)
            st.subheader("Paso 3: Profundiza tu Conocimiento 📚")
            st.markdown("""
            Usa la **barra de navegación superior** para explorar:
            - **Impacto en la Salud:** Entiende la evidencia científica detrás de los riesgos.
            - **Guía de Recomendaciones:** Encuentra un plan de acción detallado para cada nivel.
            """)
        st.success("**¡Listo! Ya puedes usar el panel de la izquierda para comenzar tu primera búsqueda.**")
        # --- CALLOUT AÑADIDO ---
        st.info("""
        **⚙️ Personaliza tu Búsqueda:** No olvides que también puedes ajustar el **radio de búsqueda** usando el deslizador en el panel de la izquierda para definir qué tan lejos buscar estaciones.
        """)
        st.info("""
        **⚙️ Sistema de alarma:**  Cuando detecta que el nivel de calidad del aire esta en rojo envia un mensaje por whasatapp advirtiendo al cuerpo docente, tambien se pueden realizar simulacro empleando el boton simulacion de alertas.
        """)
        # --- FIN DEL CALLOUT ---

    else:
        # --- PANTALLA DE RESULTADOS (SIN CAMBIOS) ---
        col1, col2 = st.columns([0.5, 0.5], gap="large")
        with col1:
            pm25, dt_iso, source = (None, None, "Sin datos")
            radius_input = st.session_state.get('radius_input', 15)
            
            with st.spinner("Buscando datos..."):
                if st.session_state.coords_to_process:
                    lat, lon = st.session_state.coords_to_process["lat"], st.session_state.coords_to_process["lon"]
                    current_query = (round(float(lat), 4), round(float(lon), 4), int(radius_input))
                    if st.session_state.last_query != current_query:
                        with st.expander("Ver proceso de búsqueda detallado..."):
                            pm25, dt_iso, source = get_pm25(lat, lon, radius_input)
                        st.session_state.last_query = current_query
                        st.session_state.last_result = {"pm25": pm25, "dt_iso": dt_iso, "source": source}
                    else:
                        # Reusar últimos resultados para evitar consultas repetidas en cada rerun
                        cached = st.session_state.last_result or {}
                        with st.expander("Ver proceso de búsqueda detallado..."):
                            for entry in st.session_state.get('last_search_log', []):
                                lvl = entry.get('level')
                                txt = entry.get('text', '')
                                if lvl == 'success':
                                    st.success(txt)
                                elif lvl == 'warning':
                                    st.warning(txt)
                                elif lvl == 'error':
                                    st.error(txt)
                                else:
                                    st.info(txt)
                        pm25 = cached.get("pm25")
                        dt_iso = cached.get("dt_iso")
                        source = cached.get("source", "Sin datos")

            if pm25 is not None:
                pm25_display, datetime_for_metric = pm25, iso_label(dt_iso)
            else:
                st.warning("No se encontraron datos reales. Mostrando un valor de ejemplo.")
                pm25_display, source = 42.0, "Valor simulado"
                datetime_for_metric = datetime.now(timezone(timedelta(hours=-5))).strftime("%d/%m/%Y - %I:%M %p")

            nivel, accion = pm25_to_level(pm25_display)
            if st.session_state.alert_ozone:
                nivel, accion = "🔴 Rojo (TEMPO Ozono)", "Ozono elevado: Evitar actividades al aire libre"

            st.subheader("Resumen 🌬️")
            with st.container(border=True):
                c1, c2 = st.columns(2)
                with c1: st.metric(f"PM2.5 (µg/m³)", f"{pm25_display:.1f}")
                with c2: st.caption(f"Última Medición:\n{datetime_for_metric}")
                st.subheader(f"Nivel: {nivel}")
                if "Verde" in nivel: st.success(f"**Recomendación:** {accion}")
                elif "Amarillo" in nivel: st.warning(f"**Recomendación:** {accion}")
                else: st.error(f"**Recomendación:** {accion}")

            st.subheader("Recomendaciones Clave 🏫")
            with st.container(border=True):
                if "Verde" in nivel:
                    st.markdown("##### 🟢 **Resumen para Nivel Bueno:**")
                    st.markdown("- **Actividades Exteriores:** ¡Luz verde! Realizar sin restricciones.\n- **Ventilación:** Mantener ventanas abiertas.")
                elif "Amarillo" in nivel:
                    st.markdown("##### 🟡 **Resumen para Nivel Moderado:**")
                    st.markdown("- **Actividades Exteriores:** Reducir la intensidad y duración.\n- **Grupos Sensibles:** Prestar especial atención.")
                else:
                    st.markdown("##### 🔴 **Resumen para Nivel Insalubre:**")
                    st.markdown("- **Actividades Exteriores:** **CANCELAR**.\n- **Ventilación:** **CERRAR** ventanas.")
            
            st.info("Para ver el plan de acción completo, haz clic en la pestaña **Guía de Recomendaciones** en el menú superior. 👆")

        with col2:
            st.subheader("🗺️ Mapa de Monitoreo")
            if st.session_state.coords_to_process:
                lat = st.session_state.coords_to_process["lat"]
                lon = st.session_state.coords_to_process["lon"]
                m = folium.Map(location=[lat, lon], zoom_start=11)
                folium.Marker([lat, lon], popup="📍 Escuela", icon=folium.Icon(color="blue", icon="school", prefix="fa")).add_to(m)
                
                candidate_locations = find_locations_by_coordinates(lat, lon, radius_input)
                for idx, loc in enumerate(candidate_locations):
                    coords = loc["coordinates"]["latitude"], loc["coordinates"]["longitude"]
                    # Para evitar exceso de llamadas, solo obtener PM2.5 para las primeras N estaciones
                    pm25_value = get_pm25_for_station(loc) if idx < DEFAULT_MAX_STATIONS_TO_QUERY else None
                    # Safe formatting for PM2.5
                    pm25_label = f"{pm25_value:.1f}" if isinstance(pm25_value, (int, float)) else "N/A"
                    color, _ = get_color_and_opacity(pm25_value) if isinstance(pm25_value, (int, float)) else ("gray", 0.2)
                    folium.Marker(
                        coords,
                        popup=f"{loc.get('name', 'N/A')}<br>PM2.5: {pm25_label}",
                        icon=folium.Icon(color=color, icon="cloud")
                    ).add_to(m)

                # WAQI fallback: show stations if OpenAQ has none or to complement
                if not candidate_locations:
                    st.info("No se hallaron estaciones en OpenAQ. Intentando con WAQI…")
                try:
                    waqi_stations = get_waqi_stations_nearby(lat, lon, radius=float(radius_input))
                except Exception:
                    waqi_stations = []

                if waqi_stations:
                    for stn in waqi_stations:
                        w_coords = [stn['latitude'], stn['longitude']]
                        dist_km = stn.get('distance_km')
                        dist_label = f"{dist_km:.1f} km" if isinstance(dist_km, (int, float)) else "N/A"
                        aqi_val = stn.get('aqi')
                        aqi_label = f"{aqi_val}" if isinstance(aqi_val, (int, float)) else (aqi_val or "N/A")
                        popup = f"WAQI • {stn['name']}<br>AQI: {aqi_label}<br>Dist: {dist_label}"
                        folium.Marker(w_coords, popup=popup, icon=folium.Icon(color="purple", icon="cloud" )).add_to(m)
                else:
                    if not candidate_locations:
                        st.warning("No hay estaciones dentro del radio establecido en OpenAQ ni WAQI.")
                
                st_folium(m, width=None, height=450)
                st.caption(f"**Fuente de Datos Principal:** {source}")
# --- PÁGINA 2: IMPACTO EN LA SALUD (VERSIÓN MEJORADA CON FUENTES) ---
elif page == "Impacto en la Salud":
    st.header("Impacto de la Calidad del Aire en la Salud Infantil 🩺")
    st.markdown("---")
    st.markdown("""
    Los niños son **biológicamente más vulnerables** a los efectos nocivos de la contaminación del aire. Sus cuerpos y defensas aún están en desarrollo, lo que los pone en un riesgo significativamente mayor que a los adultos. Las razones clave, respaldadas por la comunidad científica, son:
    - **Pulmones en Desarrollo:** Sus pulmones continúan creciendo hasta la adolescencia. El daño infligido por los contaminantes a esta edad puede ser permanente y reducir su función pulmonar de por vida.
    - **Frecuencia Respiratoria:** Los niños respiran más rápido, inhalando un mayor volumen de aire (y de contaminantes) por kilogramo de peso corporal.
    - **Sistema Inmune Inmaduro:** Su sistema de defensas no está completamente desarrollado, haciéndolos más susceptibles a infecciones respiratorias agravadas por la polución.
    """)
    
    col1, col2 = st.columns([0.6, 0.4], gap="large")

    with col1:
        st.subheader("Principales Efectos en la Salud (Basado en Evidencia)")

        st.error("#### 🫁 Sistema Respiratorio")
        st.markdown("""
        Es el más afectado de forma inmediata. La exposición a partículas finas (PM2.5) está directamente asociada con:
        - El **aumento en la frecuencia y severidad de los ataques de asma**.
        - Un mayor riesgo de desarrollar infecciones respiratorias agudas como **neumonía y bronquitis**.
        - Una **reducción medible en el crecimiento y la función pulmonar**, un efecto que puede persistir hasta la edad adulta.
        
        *Fuente: [Organización Mundial de la Salud (OMS)](https://www.who.int/es/news-room/fact-sheets/detail/ambient-(outdoor)-air-quality-and-health)*
        """)

        st.warning("#### 🧠 Desarrollo Neurológico y Cognitivo")
        st.markdown("""
        La evidencia científica, destacada por investigadores como la **Dra. Lilian Calderón (UVM)**, es alarmante. Las partículas ultrafinas (UFP), generadas por la combustión, pueden cruzar la barrera hematoencefálica y causar **neuroinflamación**, afectando directamente el desarrollo cerebral.

        En jóvenes de ciudades con alta contaminación, se ha documentado una conexión directa con:

        - **Déficits cognitivos** que impactan el aprendizaje, la memoria y la atención.
        - **Alteraciones del equilibrio, la marcha, el olfato y trastornos del sueño.**
        - La aparición de marcadores biológicos tempranos asociados a enfermedades neurodegenerativas como el **Alzheimer y el Parkinson**.

        Los investigadores concluyen que la prevención es fundamental, ya que una baja exposición a la contaminación durante la infancia y la adolescencia es clave para evitar que estas enfermedades evolucionen.

        *Fuente: [Artículo sobre partículas ultrafinas de Laureate Comunicación](https://laureate-comunicacion.com/prensa/particulas-ultrafinas-que-son-y-por-que-deben-preocuparnos/), basado en la investigación de la Dra. Lilian Calderón y Alberto Ayala.*
        """)
        
        st.info("#### ❤️ Riesgos a Largo Plazo")
        st.markdown("""
        La exposición a la contaminación del aire durante los años críticos de la infancia, e incluso desde la etapa prenatal, no solo afecta la salud inmediata, sino que también sienta las bases para enfermedades futuras. Esto incluye un **mayor riesgo de desarrollar enfermedades cardiovasculares y respiratorias crónicas** en la edad adulta, así como un desarrollo pulmonar reducido.

        *Fuente: [Agencia Europea de Medio Ambiente](https://www.eea.europa.eu/publications/air-pollution-and-childrens-health)*
        """)
    
    with col2:
        st.image(
            "https://dkv.es/corporativo/sites/default/files/2022-04/Contaminaci%C3%B3n%20salud%20infantil%20%282%29.jpg",
            caption="Infografía educativa sobre la relación entre la contaminación ambiental y la salud infantil, destacando los riesgos asociados a la exposición a partículas finas (PM2.5)."
        )
        st.markdown("<br>", unsafe_allow_html=True)
        st.success("""
        **¿Por qué es crucial en las escuelas?**
        
        Dado que los niños pasan una parte significativa de su día en la escuela, garantizar un aire más limpio en este entorno es una de las intervenciones de salud pública más efectivas para proteger su futuro.
        """)
# --- PÁGINA 3: GUÍA DE RECOMENDACIONES (VERSIÓN MEJORADA) ---
elif page == "Guía de Recomendaciones":
    st.header("Guía Detallada de Actividades Escolares ✅")
    st.markdown("---")
    st.info("Usa este plan de acción para tomar decisiones informadas sobre las actividades de los estudiantes según el nivel de calidad del aire.")

    # --- NIVEL BUENO ---
    st.subheader("🟢 Nivel Bueno")
    with st.container(border=True):
        st.markdown("#### Mensaje Clave: ¡Luz verde! Es un día excelente para aprender y jugar al aire libre.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
            ##### **⚽ En el Patio y Deporte**
            - ✅ **Educación Física:** Realizar sin restricciones.
            - ✅ **Recreo:** Disfrutar del tiempo completo en exteriores.
            - ✅ **Deportes:** Entrenamientos y competiciones pueden proceder normalmente.
            """)
        with c2:
            st.markdown("""
            ##### **🏫 En el Aula**
            - ✅ **Ventilación:** Mantener las ventanas abiertas para una buena circulación de aire.
            - ✅ **Actividades:** Considerar realizar clases como lectura o arte al aire libre.
            """)
        with c3:
            st.markdown("""
            ##### **🩺 Grupos Sensibles**
            - ✅ **Niños con asma:** Generalmente no requieren precauciones especiales, pero la vigilancia es siempre una buena práctica.
            """)

    # --- NIVEL MODERADO ---
    st.subheader("🟡 Nivel Moderado")
    with st.container(border=True):
        st.markdown("#### Mensaje Clave: Precaución. Se recomienda reducir la intensidad y duración del esfuerzo físico.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
            ##### **⚽ En el Patio y Deporte**
            - 🟡 **Educación Física:** Modificar actividades para reducir el esfuerzo prolongado. Favorecer ejercicios de habilidad sobre los de resistencia.
            - 🟡 **Recreo:** Aceptable, pero considerar acortarlo y vigilar a los estudiantes más activos.
            - 🟡 **Deportes:** Reducir la duración de entrenamientos intensos y programar más descansos.
            """)
        with c2:
            st.markdown("""
            ##### **🏫 En el Aula**
            - 🟡 **Ventilación:** Ventilar de forma intermitente. Cerrar ventanas si se percibe bruma o malos olores.
            - 🟡 **Actividades:** Realizar las clases que requieran más esfuerzo físico en interiores.
            """)
        with c3:
            st.markdown("""
            ##### **🩺 Grupos Sensibles**
            - ⚠️ **Niños con asma:** Deben **evitar el esfuerzo físico intenso**. Pueden participar en actividades más tranquilas. Asegurarse de que tengan sus inhaladores a mano.
            """)

    # --- NIVEL INSALUBRE ---
    st.subheader("🔴 Nivel Insalubre")
    with st.container(border=True):
        st.markdown("#### Mensaje Clave: ¡Alerta! La salud es la prioridad. Todas las actividades deben realizarse en interiores.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
            ##### **⚽ En el Patio y Deporte**
            - ❌ **Educación Física:** **CANCELAR** todas las actividades al aire libre. Mover a un gimnasio o aula.
            - ❌ **Recreo:** Realizar el recreo **dentro del aula** o en espacios interiores designados.
            - ❌ **Deportes:** **SUSPENDER** todos los entrenamientos y competiciones al aire libre.
            """)
        with c2:
            st.markdown("""
            ##### **🏫 En el Aula**
            - ❌ **Ventilación:** **Mantener todas las ventanas y puertas cerradas.**
            - ❌ **Actividades:** Planificar "pausas activas" (estiramientos, yoga suave) dentro del aula para que los niños se muevan de forma segura.
            """)
        with c3:
            st.markdown("""
            ##### **🩺 Grupos Sensibles**
            - 🛑 **TODOS los niños** se consideran sensibles en este nivel. Es crucial monitorear cualquier síntoma como tos o dificultad para respirar. Los niños con condiciones preexistentes están en riesgo elevado.
            """)