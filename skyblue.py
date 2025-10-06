import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict

import requests
import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import folium
from streamlit_folium import st_folium
from streamlit_option_menu import option_menu
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Import WAQI as fallback
# Make sure this file exists in your project: from data_sources.waqi import get_waqi_stations_nearby
from data_sources.waqi import get_waqi_stations_nearby

# -----------------------------
# Configuration
# -----------------------------
st.set_page_config(page_title="School Air Index", page_icon="🏫", layout="wide")
st.title("🏫 School Air Index")

# -----------------------------
# TOP NAVIGATION BAR
# -----------------------------
page = option_menu(
    menu_title=None,
    options=["Home", "How to Use", "Health Impact", "Recommendations Guide"],
    icons=["house-heart-fill", "info-circle-fill", "lungs-fill", "clipboard2-check-fill"],  # Bootstrap Icons
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
# UI State and Helpers
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
    st.session_state.last_result = {"pm25": None, "dt_iso": None, "source": "No data"}
if "last_search_log" not in st.session_state:
    st.session_state.last_search_log = []

# --- Helper Functions ---
OPENAQ_KEY: str = "08f176ffd0ccb07a617b9d9cf0f740366b783adfcef064fcc601a7a636463473"
OPENAQ_BASE: str = "https://api.openaq.org/v3"
HEADERS: dict = {"X-API-Key": OPENAQ_KEY} if OPENAQ_KEY else {}
DEFAULT_MAX_STATIONS_TO_QUERY: int = 10

def _get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    try:
        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets.get(name))
    except Exception:
        pass
    return os.getenv(name, default)

# Twilio WhatsApp (uses local test credentials by default; override with secrets/env in prod)
TWILIO_ACCOUNT_SID = _get_secret("TWILIO_ACCOUNT_SID", "ACf307b067a65d0c6791bbfe0e27f2242c")
TWILIO_AUTH_TOKEN = _get_secret("TWILIO_AUTH_TOKEN", "49310e467520a35272f8378ead242dce")
TWILIO_WHATSAPP_FROM = _get_secret("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")  # sandbox
TWILIO_CONTENT_SID = _get_secret("TWILIO_CONTENT_SID", "HXb5b62575e6e4ff6129ad7c8efe1f983e")
TWILIO_CONTENT_VARIABLES = _get_secret("TWILIO_CONTENT_VARIABLES", '{"1":"12/1","2":"3pm"}')
TWILIO_WHATSAPP_RECIPIENTS: List[str] = [
    "whatsapp:+593995532793",
    "whatsapp:+593939972193",
]

def _twilio_config_check() -> Optional[str]:
    """Return an error message if Twilio config looks invalid; otherwise None."""
    sid = (TWILIO_ACCOUNT_SID or "").strip()
    token = (TWILIO_AUTH_TOKEN or "").strip()
    sender = (TWILIO_WHATSAPP_FROM or "").strip()
    if not sid or not token:
        return "Twilio credentials are missing. Please set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."
    if not sid.startswith("AC") or len(sid) < 30:
        return "Invalid TWILIO_ACCOUNT_SID. It should start with 'AC' and be your account SID."
    if len(token) < 20:
        return "TWILIO_AUTH_TOKEN seems invalid. Please check your account token."
    if not sender.startswith("whatsapp:+"):
        return "Invalid TWILIO_WHATSAPP_FROM. Use the format 'whatsapp:+<code><number>'."
    return None

def send_whatsapp_message(body: str, to_number: str, from_number: str, *, content_sid: Optional[str] = None, content_variables_json: Optional[str] = None) -> bool:
    """Sends a WhatsApp message using the Twilio API (sandbox compatible). Returns True if successful."""
    try:
        if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
            return False
        def normalize(num: str) -> str:
            num = (num or '').strip()
            return num if num.startswith("whatsapp:") else f"whatsapp:{num}"
        to_w = normalize(to_number)
        from_w = normalize(from_number)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
        data = {"To": to_w, "From": from_w}
        if content_sid:
            data["ContentSid"] = content_sid
            if content_variables_json:
                data["ContentVariables"] = content_variables_json
        else:
            data["Body"] = body
        resp = requests.post(url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=20)
        if resp.status_code in (200, 201):
            return True
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text[:400] if isinstance(resp.text, str) else str(resp.text)}
        st.error(
            f"Twilio WhatsApp failed (HTTP {resp.status_code}). Details: "
            f"{payload.get('message') or payload.get('error_message') or payload}"
        )
        return False
    except Exception:
        return False

def send_bulk_whatsapp(body: str, *, use_content_template: bool = True) -> int:
    """Sends the same message to all numbers in TWILIO_WHATSAPP_RECIPIENTS. Returns success count."""
    success_count = 0
    for to_number in TWILIO_WHATSAPP_RECIPIENTS:
        ok = send_whatsapp_message(
            body,
            to_number,
            TWILIO_WHATSAPP_FROM,
            content_sid=TWILIO_CONTENT_SID if use_content_template else None,
            content_variables_json=TWILIO_CONTENT_VARIABLES if use_content_template else None,
        )
        if ok:
            success_count += 1
    return success_count

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

@st.cache_data(ttl=3600)
def get_coords_from_city(city_name: str) -> Optional[Tuple[float, float]]:
    """
    Gets coordinates for a city using Open-Meteo Geocoding first.
    If it fails or returns no results, it uses Nominatim (OSM) as a fallback.
    """
    city_query = (city_name or "").strip()
    if not city_query:
        return None

    session = _create_retry_session()

    # 1) Try with Open-Meteo Geocoding API first (Primary)
    try:
        om_url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": city_query, "count": 1, "language": "en", "format": "json"}
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
            
    except requests.exceptions.RequestException:
        st.info("Could not contact Open-Meteo. Trying alternative provider (Nominatim)...")
    except Exception:
        st.info("There was a problem with the Open-Meteo response. Trying alternative provider (Nominatim)...")

    # 2) Fallback with Nominatim (OSM)
    try:
        nominatim_url = "https://nominatim.openstreetmap.org/search"
        params = {"q": city_query, "format": "json", "limit": 1}
        headers = {
            "User-Agent": "SchoolAirIndex/1.0 (contact: your-email@example.com)",
            "Accept": "application/json",
            "Accept-Language": "en",
            "Referer": "https://school-air-index.app/",
        }
        
        response = session.get(nominatim_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        
        if isinstance(results, list) and results:
            lat = float(results[0].get("lat"))
            lon = float(results[0].get("lon"))
            return lat, lon
            
    except requests.exceptions.RequestException:
        st.error("Network error during geocoding with both providers. Check server connectivity.")
        return None
    except Exception:
        st.error("An unexpected error occurred while geocoding the city with both providers.")
        return None
    
    return None


def iso_label(dt_iso: Optional[str]) -> Optional[str]:
    if not dt_iso: return None
    try:
        local_tz = timezone(timedelta(hours=-5)) # Example: EST
        dt_utc = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt_local = dt_utc.astimezone(local_tz)
        return dt_local.strftime("%Y-%m-%d %I:%M %p")
    except Exception:
        return dt_iso

def pm25_to_level(pm25: float) -> Tuple[str, str]:
    if pm25 <= 35.0: return "🟢 Green (Good)", "Normal activities"
    if pm25 <= 55.0: return "🟡 Yellow (Moderate)", "Reduce strenuous activity"
    return "🔴 Red (Unhealthy)", "Avoid outdoor activities"

def _request_openaq(endpoint: str, params: Optional[dict] = None) -> dict:
    url = f"{OPENAQ_BASE}/{endpoint}"
    r = requests.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=600)
def find_locations_by_coordinates(latitude: float, longitude: float, radius_km: int) -> List[Dict]:
    msg = f"Searching for stations within a {radius_km} km radius..."
    st.session_state.last_search_log.append({"level": "info", "text": msg})
    st.info(msg)
    params = {"coordinates": f"{latitude},{longitude}", "radius": radius_km * 1000, "limit": 100}
    try:
        data = _request_openaq("locations", params=params)
        locations = data.get("results", [])
        pm25_locations = [loc for loc in locations if any(s.get("parameter", {}).get("name") == "pm25" for s in loc.get("sensors", []))]
        sorted_locations = sorted(pm25_locations, key=lambda loc: loc.get('distance', float('inf')))
        msg_ok = f"Found {len(sorted_locations)} nearby stations with a PM2.5 (Air Quality) sensor."
        st.session_state.last_search_log.append({"level": "success", "text": msg_ok})
        st.success(msg_ok)
        return sorted_locations
    except Exception as e:
        err = f"Error finding nearby stations: {e}"
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
    now_utc = datetime.now(timezone.utc)
    twenty_four_hours_ago = now_utc - timedelta(hours=24)
    params = {"limit": 100, "page": 1, "datetime_from": twenty_four_hours_ago.isoformat(), "datetime_to": now_utc.isoformat(), "order_by": "datetime", "sort": "desc"}
    try:
        data = _request_openaq(f"sensors/{sensor_id}/measurements", params=params)
    except requests.exceptions.HTTPError as http_err:
        if getattr(http_err, 'response', None) is not None and http_err.response.status_code == 429:
            st.session_state.openaq_rate_limited = True
            return None, None
        raise
    results = data.get("results", [])
    if results:
        valid_results = [r for r in results if r.get("period", {}).get("datetimeTo", {}).get("utc")]
        if not valid_results: return None, None
        sorted_results = sorted(valid_results, key=lambda r: r["period"]["datetimeTo"]["utc"], reverse=True)
        latest_measurement = sorted_results[0]
        value = latest_measurement.get("value")
        dt = latest_measurement.get("period", {}).get("datetimeTo", {}).get("utc")
        if value is not None:
            return float(value), dt
    return None, None

def get_pm25(latitude: float, longitude: float, radius_km: int) -> Tuple[Optional[float], Optional[str], str]:
    try:
        st.session_state.last_search_log = []
        candidate_locations = find_locations_by_coordinates(latitude, longitude, radius_km=radius_km)
        if not candidate_locations:
            warn1 = "No monitoring stations with PM2.5 sensors were found nearby in OpenAQ."
            st.session_state.last_search_log.append({"level": "warning", "text": warn1})
            st.warning(warn1)
            return None, None, "No stations found"
        
        limited_locations = candidate_locations[:DEFAULT_MAX_STATIONS_TO_QUERY]
        valid_measurements = []
        st.session_state.openaq_rate_limited = False
        
        for i, location in enumerate(limited_locations):
            loc_name = location.get('name', 'N/A')
            distance = location.get('distance')
            dist_label = f"{distance/1000:.1f} km away" if distance is not None else ""
            step = f"Step #{i+1}: Checking '{loc_name}' {dist_label}..."
            st.session_state.last_search_log.append({"level": "info", "text": step})
            st.info(step)
            pm25_sensor_id = get_pm25_sensor_id_from_location(location)
            if not pm25_sensor_id: continue
            
            v, dt = get_latest_measurement_from_sensor(pm25_sensor_id)
            if v is not None and dt is not None:
                valid_measurements.append({"value": v, "dt_iso": dt, "source": f"OpenAQ • {loc_name}"})
        
        if st.session_state.get('openaq_rate_limited'):
            rate_msg = "You have reached the OpenAQ request limit. Some stations could not be queried."
            st.session_state.last_search_log.append({"level": "warning", "text": rate_msg})
            st.warning(rate_msg)
        
        if not valid_measurements:
            warn2 = "No nearby stations reported fresh PM2.5 data."
            st.session_state.last_search_log.append({"level": "warning", "text": warn2})
            st.warning(warn2)
            return None, None, "Stations with no recent data"
        
        most_recent = sorted(valid_measurements, key=lambda x: x['dt_iso'], reverse=True)[0]
        ok_msg = f"✓ Using the most recent measurement from: '{most_recent['source']}'"
        st.session_state.last_search_log.append({"level": "success", "text": ok_msg})
        st.success(ok_msg)
        return (most_recent['value'], most_recent['dt_iso'], most_recent['source'])
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return None, None, "Application error"

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
# --- End of Helper Functions ---


# -----------------------------
# Sidebar UI (STATIC CONTENT)
# -----------------------------
with st.sidebar:
    st.image("logo.png", use_container_width=True)
    st.divider()
    st.info("This is a tool to monitor air quality in school environments.")
    st.success("Select a page from the top navigation bar to get started.")

# -----------------------------
# Main Content by Page
# -----------------------------

# --- PAGE 1: HOME (Search & Results) ---
if page == "Home":
    st.subheader("Search for Air Quality Data")
    
    # --- SEARCH CONTROLS (Moved from Sidebar) ---
    col1, col2 = st.columns(2)
    with col1:
        st.info("ℹ️ To search by your current location, your browser will ask for permission. Please click 'Allow'.")
        location_data = streamlit_geolocation()
        
        if st.button("Search My Location", use_container_width=True, type="primary"):
            if location_data and location_data.get('latitude'):
                st.session_state.search_triggered = True
                st.session_state.coords_to_process = {"lat": location_data['latitude'], "lon": location_data['longitude']}
            else:
                st.error("Could not get your location. Please ensure you have granted permissions.")
        
    with col2:
        st.markdown("**Search by Location Name, example ( Guayaquil or Guayaquil,Ecuador)**")
        city_input = st.text_input("Enter a city and country (e.g., 'Guayaquil, Ecuador')", label_visibility="collapsed")

        if st.button("Search by City", use_container_width=True):
            if city_input:
                coords = get_coords_from_city(city_input)
                if coords:
                    lat, lon = coords
                    st.success(f"📍 City found. Using coordinates: {lat:.4f}, {lon:.4f}")
                    st.session_state.search_triggered = True
                    st.session_state.coords_to_process = {"lat": lat, "lon": lon}
                else:
                    st.error("Could not find the city. Try being more specific (e.g., 'City, Country').")
            else:
                st.warning("Please enter a city name to search.")

    st.subheader("⚙️ Search Options & Alerts")
    c1, c2 = st.columns([0.6, 0.4])
    with c1:
        radius_input = st.slider("Search Radius (km)", 1, 25, 15)
        st.session_state.radius_input = radius_input
    with c2:
         with st.expander("Alert Simulation (Demo)"):
            st.caption("Send a WhatsApp alert to preconfigured recipients (Twilio)")
            if st.button("🔴 Activate Ozone Alert", use_container_width=True):
                st.session_state.alert_ozone = True
                msg = "🔴 Red (Ozone ALERT) — High ozone levels: Avoid outdoor activities"
                sent = send_bulk_whatsapp(msg, use_content_template=False)
                if sent > 0: st.success(f"WhatsApp sent to {sent} recipient(s).")
                else: st.warning("No WhatsApp messages were sent. Check Twilio config.")
            
            if st.button("✅ Deactivate Alert", use_container_width=True):
                st.session_state.alert_ozone = False
                msg = "✅ Ozone Alert deactivated."
                sent = send_bulk_whatsapp(msg, use_content_template=False)
                if sent > 0: st.success(f"WhatsApp sent to {sent} recipient(s).")
                else: st.warning("No WhatsApp messages were sent. Check Twilio config.")

    st.divider()

    # --- RESULTS SCREEN (Display if search is triggered) ---
    if st.session_state.search_triggered:
        res_col1, res_col2 = st.columns([0.5, 0.5], gap="large")
        with res_col1:
            pm25, dt_iso, source = (None, None, "No data")
            radius_input = st.session_state.get('radius_input', 15)
            
            with st.spinner("Searching for data..."):
                if st.session_state.coords_to_process:
                    lat, lon = st.session_state.coords_to_process["lat"], st.session_state.coords_to_process["lon"]
                    current_query = (round(float(lat), 4), round(float(lon), 4), int(radius_input))
                    if st.session_state.last_query != current_query:
                        with st.expander("View detailed search process..."):
                            pm25, dt_iso, source = get_pm25(lat, lon, radius_input)
                        st.session_state.last_query = current_query
                        st.session_state.last_result = {"pm25": pm25, "dt_iso": dt_iso, "source": source}
                    else:
                        cached = st.session_state.last_result or {}
                        with st.expander("View detailed search process..."):
                            for entry in st.session_state.get('last_search_log', []):
                                lvl, txt = entry.get('level'), entry.get('text', '')
                                if lvl == 'success': st.success(txt)
                                elif lvl == 'warning': st.warning(txt)
                                elif lvl == 'error': st.error(txt)
                                else: st.info(txt)
                        pm25, dt_iso, source = cached.get("pm25"), cached.get("dt_iso"), cached.get("source", "No data")

            if pm25 is not None:
                pm25_display, datetime_for_metric = pm25, iso_label(dt_iso)
            else:
                st.warning("No real data found. Displaying an example value.")
                pm25_display, source = 42.0, "Simulated Value"
                datetime_for_metric = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %I:%M %p")

            level, action = pm25_to_level(pm25_display)
            if st.session_state.alert_ozone:
                level, action = "🔴 Red (Ozone ALERT)", "High ozone levels: Avoid outdoor activities"

            st.subheader("Summary 🌬️")
            with st.container(border=True):
                c1, c2 = st.columns(2)
                with c1: st.metric(f"PM2.5 (µg/m³)", f"{pm25_display:.1f}")
                with c2: st.caption(f"Last Measurement:\n{datetime_for_metric}")
                st.subheader(f"Level: {level}")
                if "Green" in level: st.success(f"**Recommendation:** {action}")
                elif "Yellow" in level: st.warning(f"**Recommendation:** {action}")
                else: st.error(f"**Recommendation:** {action}")

            st.subheader("Key Recommendations 🏫")
            with st.container(border=True):
                if "Green" in level:
                    st.markdown("##### 🟢 **Summary for Good Level:**")
                    st.markdown("- **Outdoor Activities:** Green light! Proceed without restrictions.\n- **Ventilation:** Keep windows open.")
                elif "Yellow" in level:
                    st.markdown("##### 🟡 **Summary for Moderate Level:**")
                    st.markdown("- **Outdoor Activities:** Reduce intensity and duration.\n- **Sensitive Groups:** Pay special attention.")
                else:
                    st.markdown("##### 🔴 **Summary for Unhealthy Level:**")
                    st.markdown("- **Outdoor Activities:** **CANCEL**.\n- **Ventilation:** **CLOSE** windows.")
            
            st.info("To see the full action plan, click the **Recommendations Guide** tab in the top menu. 👆")

        with res_col2:
            st.subheader("🗺️ Monitoring Map")
            if st.session_state.coords_to_process:
                lat, lon = st.session_state.coords_to_process["lat"], st.session_state.coords_to_process["lon"]
                m = folium.Map(location=[lat, lon], zoom_start=11)
                folium.Marker([lat, lon], popup="📍 School", icon=folium.Icon(color="blue", icon="school", prefix="fa")).add_to(m)
                
                candidate_locations = find_locations_by_coordinates(lat, lon, radius_input)
                for idx, loc in enumerate(candidate_locations):
                    coords = loc["coordinates"]["latitude"], loc["coordinates"]["longitude"]
                    pm25_value = get_pm25_for_station(loc) if idx < DEFAULT_MAX_STATIONS_TO_QUERY else None
                    pm25_label = f"{pm25_value:.1f}" if isinstance(pm25_value, (int, float)) else "N/A"
                    color, _ = get_color_and_opacity(pm25_value) if isinstance(pm25_value, (int, float)) else ("gray", 0.2)
                    folium.Marker(
                        coords,
                        popup=f"{loc.get('name', 'N/A')}<br>PM2.5: {pm25_label}",
                        icon=folium.Icon(color=color, icon="cloud")
                    ).add_to(m)

                if not candidate_locations:
                    st.info("No stations found in OpenAQ. Trying WAQI...")
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
                        st.warning("No stations found within the set radius in either OpenAQ or WAQI.")
                
                st_folium(m, width=None, height=450)
                st.caption(f"**Primary Data Source:** {source}")
    else:
        st.info("⬅️ Please select a location above to view the air quality report.")


# --- PAGE 2: HOW TO USE ---
elif page == "How to Use":
    st.markdown("### Hello, educator! 🍎")
    st.markdown(
        "Welcome to the **School Air Index**. This tool helps you make informed decisions about "
        "outdoor activities to protect your students' health."
    )
    st.info("#### Follow these simple steps to get started:")
    col1, col2 = st.columns([0.5, 0.5], gap="large")
    with col1:
        st.subheader("Step 1: Go to the Home Page")
        st.markdown("""
        Navigate to the **'Home'** page using the top menu. There, you can select your school's location. You have two options:
        """)
        
        # === NEW/MODIFIED SECTION STARTS HERE ===
        st.markdown("##### A) Use Your Current Location 🛰️")
        st.markdown("First, on the Home page, the browser will ask for your permission to get your location. You may need to click an icon like this:")
        st.image(
            "icono.jpg",
            caption="Look for a location icon or a browser pop-up.",
            width=80,
        )
        st.markdown("After you click **'Allow'**, then press the **'Search My Location'** button to get the data.")
        st.warning("**Important:** If you don't see a pop-up, check if your browser is blocking it!")
        # === NEW/MODIFIED SECTION ENDS HERE ===
        
        st.markdown("---")
        st.markdown("##### B) Enter a City ✍️")
        st.markdown("Type the city and country, then press the **'Search by City'** button.")


    with col2:
        st.subheader("Step 2: Analyze the Report 📊")
        st.markdown("""
        Once you search, a report will appear on the Home page with:
        - A **clear summary** with the PM2.5 level and a color code (🟢, 🟡, 🔴).
        - An **interactive map** with your location and nearby stations.
        - **Quick recommendations** for the day.
        """)
        st.subheader("Step 3: Deepen Your Knowledge 📚")
        st.markdown("""
        Use the **top navigation bar** to explore:
        - **Health Impact:** Understand the scientific evidence behind the risks.
        - **Recommendations Guide:** Find a detailed action plan for each level.
        """)
    st.success("**Ready! You can now navigate to the 'Home' page to start your first search.**")
    st.info("""
    **⚙️ Customize Your Search:** On the Home page, you can also adjust the **search radius** using the slider to define how far to look for stations.
        """)
    st.info("""
    **⚙️ Alert System:** When it detects that the air quality level is red, it sends a WhatsApp message warning the teaching staff. You can also run drills using the alert simulation button on the Home page.
    """)

# --- PAGE 3: HEALTH IMPACT ---
elif page == "Health Impact":
    st.header("Impact of Air Quality on Children's Health 🩺")
    st.markdown("---")
    st.markdown("""
    Children are **biologically more vulnerable** to the harmful effects of air pollution. Their bodies and defenses are still developing, putting them at a significantly greater risk than adults. The key reasons, backed by the scientific community, are:
    - **Developing Lungs:** Their lungs continue to grow until adolescence. Damage from pollutants at this age can be permanent and reduce their lung function for life.
    - **Respiratory Rate:** Children breathe faster, inhaling a larger volume of air (and pollutants) per kilogram of body weight.
    - **Immature Immune System:** Their defense system is not fully developed, making them more susceptible to respiratory infections aggravated by pollution.
    """)
    
    col1, col2 = st.columns([0.6, 0.4], gap="large")

    with col1:
        st.subheader("Main Health Effects (Evidence-Based)")
        st.error("#### 🫁 Respiratory System")
        st.markdown("""
        This is the most immediately affected system. Exposure to fine particulate matter (PM2.5) is directly associated with:
        - An **increase in the frequency and severity of asthma attacks**.
        - A higher risk of developing acute respiratory infections like **pneumonia and bronchitis**.
        - A **measurable reduction in lung growth and function**, an effect that can persist into adulthood.
        
        *Source: [World Health Organization (WHO)](https://www.who.int/news-room/fact-sheets/detail/ambient-(outdoor)-air-quality-and-health)*
        """)
        st.warning("#### 🧠 Neurological and Cognitive Development")
        st.markdown("""
        The scientific evidence is alarming. Ultrafine particles (UFPs), generated by combustion, can cross the blood-brain barrier and cause **neuroinflammation**, directly affecting brain development. This has been linked to:
        - **Cognitive deficits** impacting learning, memory, and attention.
        - **Impairments in balance, gait, smell, and sleep disorders.**
        - The appearance of early biological markers associated with neurodegenerative diseases like **Alzheimer's and Parkinson's**.
        
        *Source: Research from Dr. Lilian Calderón, among others, highlights these risks.*
        """)
        st.info("#### ❤️ Long-Term Risks")
        st.markdown("""
        Exposure to air pollution during the critical years of childhood not only affects immediate health but also lays the groundwork for future diseases. This includes a **higher risk of developing cardiovascular and chronic respiratory diseases** in adulthood.
        
        *Source: [European Environment Agency](https://www.eea.europa.eu/publications/air-pollution-and-childrens-health)*
        """)
    
    with col2:
        st.image(
            "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fassets.weforum.org%2Feditor%2FLsZMSqYpbphXvnxI2lBku8E6K4Dw19JPgR0A00PVHi4.jpg&f=1&nofb=1&ipt=94de6ea47d8c21de93c3e78da51ccce9c253f2bca0aff288fc1700740e4e9492",
            caption="Educational infographic on the relationship between environmental pollution and children's health."
        )
        st.markdown("<br>", unsafe_allow_html=True)
        st.success("""
        **Why is this crucial in schools?**
        
        Since children spend a significant portion of their day at school, ensuring cleaner air in this environment is one of the most effective public health interventions to protect their future.
        """)

# --- PAGE 4: RECOMMENDATIONS GUIDE ---
elif page == "Recommendations Guide":
    st.header("Detailed Guide for School Activities ✅")
    st.markdown("---")
    st.info("Use this action plan to make informed decisions about student activities based on the air quality level.")

    st.subheader("🟢 Good Level")
    with st.container(border=True):
        st.markdown("#### Key Message: Green light! It's an excellent day to learn and play outdoors.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
            ##### **⚽ Yard and Sports**
            - ✅ **Physical Education:** Conduct without restrictions.
            - ✅ **Recess:** Enjoy full time outdoors.
            - ✅ **Sports:** Training and competitions can proceed normally.
            """)
        with c2:
            st.markdown("""
            ##### **🏫 In the Classroom**
            - ✅ **Ventilation:** Keep windows open for good air circulation.
            - ✅ **Activities:** Consider holding classes like reading or art outdoors.
            """)
        with c3:
            st.markdown("""
            ##### **🩺 Sensitive Groups**
            - ✅ **Children with asthma:** Generally do not require special precautions, but monitoring is always good practice.
            """)

    st.subheader("🟡 Moderate Level")
    with st.container(border=True):
        st.markdown("#### Key Message: Caution. It is recommended to reduce the intensity and duration of physical exertion.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
            ##### **⚽ Yard and Sports**
            - 🟡 **Physical Education:** Modify activities to reduce prolonged exertion. Favor skill-based exercises over endurance ones.
            - 🟡 **Recess:** Acceptable, but consider shortening it and monitoring the most active students.
            - 🟡 **Sports:** Reduce the duration of intense training and schedule more breaks.
            """)
        with c2:
            st.markdown("""
            ##### **🏫 In the Classroom**
            - 🟡 **Ventilation:** Ventilate intermittently. Close windows if haze or bad odors are perceived.
            - 🟡 **Activities:** Conduct more strenuous classes indoors.
            """)
        with c3:
            st.markdown("""
            ##### **🩺 Sensitive Groups**
            - ⚠️ **Children with asthma:** Should **avoid intense physical exertion**. They can participate in quieter activities. Ensure they have their inhalers on hand.
            """)

    st.subheader("🔴 Unhealthy Level")
    with st.container(border=True):
        st.markdown("#### Key Message: Alert! Health is the priority. All activities must be conducted indoors.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("""
            ##### **⚽ Yard and Sports**
            - ❌ **Physical Education:** **CANCEL** all outdoor activities. Move to a gym or classroom.
            - ❌ **Recess:** Hold recess **inside the classroom** or in designated indoor spaces.
            - ❌ **Sports:** **SUSPEND** all outdoor training and competitions.
            """)
        with c2:
            st.markdown("""
            ##### **🏫 In the Classroom**
            - ❌ **Ventilation:** **Keep all windows and doors closed.**
            - ❌ **Activities:** Plan "active breaks" (stretching, gentle yoga) inside the classroom for children to move safely.
            """)
        with c3:
            st.markdown("""
            ##### **🩺 Sensitive Groups**
            - 🛑 **ALL children** are considered sensitive at this level. It is crucial to monitor for any symptoms like coughing or difficulty breathing. Children with pre-existing conditions are at high risk.
            """)