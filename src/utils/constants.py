"""
Marine Weather Risk — Demo. Constants.
Shared registry of sites, wind/wave models, IMOS buoys, thresholds, styling.
"""

# =============================================================================
# TRIAL SITES — 4 generic WA locations (no client facilities)
# =============================================================================
# The trial is shared with multiple prospective clients, so no real operator
# assets appear. Browse Basin points are offset from the Ichthys / Prelude /
# Crux / Torosa cluster (~13.5°S / 123.3°E) to avoid coinciding with any
# known facility.

TRIAL_SITES = [
    {"name": "Broome",             "lat": -17.96, "lon": 122.24, "type": "city"},
    {"name": "Karratha",           "lat": -20.74, "lon": 116.85, "type": "city"},
    {"name": "Browse Basin North", "lat": -13.50, "lon": 123.00, "type": "offshore"},
    {"name": "Browse Basin South", "lat": -15.00, "lon": 122.50, "type": "offshore"},
]

# Downstream coord-resolver and map helpers expect these dict shapes. Derive
# from TRIAL_SITES so the list above stays the single source of truth.
MARINE_SITES = {s["name"]: {"lat": s["lat"], "lon": s["lon"]}
                for s in TRIAL_SITES if s["type"] == "offshore"}
TRIAL_CITIES = {s["name"]: {"lat": s["lat"], "lon": s["lon"]}
                for s in TRIAL_SITES if s["type"] == "city"}

# Marine wind ensemble models — physics-based NWP (Open-Meteo).
# `provider` is implicit "open-meteo" — see MARINE_BOM_WIND_MODELS for BoM ACCESS.
MARINE_WIND_MODELS = {
    "ECMWF IFS": {"provider": "open-meteo", "api_model": "ecmwf_ifs025", "members": 51, "color": "#d62728", "supports_gusts": True},
    "GFS": {"provider": "open-meteo", "api_model": "gfs025", "members": 31, "color": "#1f77b4", "supports_gusts": True},
    "ICON": {"provider": "open-meteo", "api_model": "icon_global", "members": 40, "color": "#2ca02c", "supports_gusts": True},
}

# Marine wind ensemble models — AI / machine-learning driven.
# Both run on Open-Meteo's commercial ensemble endpoint
# (customer-ensemble-api.open-meteo.com).  Native step is 6h but the API
# interpolates to hourly.  Neither model predicts wind gusts, so they are
# excluded from the gust chart at render time.
MARINE_AI_WIND_MODELS = {
    "ECMWF AIFS Ens": {"provider": "open-meteo", "api_model": "ecmwf_aifs025_ensemble", "members": 51, "color": "#9467bd", "supports_gusts": False},
    "AIGFS Ens": {"provider": "open-meteo", "api_model": "ncep_aigefs025", "members": 31, "color": "#17becf", "supports_gusts": False},
}

# Marine wind models — BoM ACCESS (Australia's national NWP).
# ACCESS-G is deterministic (12.5 km, hourly, 10 days).
# ACCESS-GE is ensemble (33 km, native 3-hourly, 10 days, 18 members) — its
# 3-hourly cadence is upsampled to hourly inside the BoM adapter so the rest
# of the pipeline (which assumes an hourly index) Just Works.
# Both expose `wind_speed`, `wind_direction` and `gust` in m/s.
# ACCESS-CE (2 km city ensemble, 2-day horizon, capital-city domain only) is
# intentionally not included — too short a horizon and too narrow a domain
# for offshore marine sites.
MARINE_BOM_WIND_MODELS = {
    "ACCESS-G": {"provider": "bom", "api_model": "access-g", "members": 1, "deterministic": True, "color": "#ff7f0e", "supports_gusts": True},
    "ACCESS-GE": {"provider": "bom", "api_model": "access-ge", "members": 18, "color": "#8c564b", "supports_gusts": True},
}

# Combined registry — used everywhere we iterate over the full set of wind models.
MARINE_ALL_WIND_MODELS = {**MARINE_WIND_MODELS, **MARINE_AI_WIND_MODELS, **MARINE_BOM_WIND_MODELS}

# Marine wave models — verified live on customer-marine-api.open-meteo.com.
# All three return wave_height + wave_period for offshore Australia. Note that
# "ICON" here is DWD's GWAM (Global Wave Model) — same provider as ICON, since
# Open-Meteo doesn't ship a separate icon_wave product.
MARINE_WAVE_MODELS = {
    "ECMWF": {"api_model": "ecmwf_wam025", "color": "#d62728"},
    "GFS":   {"api_model": "ncep_gfswave025", "color": "#1f77b4"},
    "ICON":  {"api_model": "gwam", "color": "#2ca02c"},
}

# =============================================================================
# IMOS WAVE BUOY STATIONS
# =============================================================================

IMOS_WAVE_BUOYS = {
    "Rottnest Island":   {"lat": -32.10, "lon": 115.40, "provider": "DoT-WA"},
    "Cottesloe":         {"lat": -31.98, "lon": 115.68, "provider": "DoT-WA"},
    "Mandurah":          {"lat": -32.45, "lon": 115.57, "provider": "DoT-WA"},
    "Jurien":            {"lat": -30.30, "lon": 114.92, "provider": "DoT-WA"},
    "Cape Naturaliste":  {"lat": -33.53, "lon": 114.93, "provider": "DoT-WA"},
    "Albany":            {"lat": -35.20, "lon": 117.73, "provider": "DoT-WA"},
    "Esperance":         {"lat": -33.87, "lon": 121.89, "provider": "DoT-WA"},
    # PPA stopped publishing to the public AODN feed in mid-2024 — keep on the
    # map for context, but exclude from the nearest-buoy auto-match.
    "Dampier":           {"lat": -20.44, "lon": 116.73, "provider": "PPA", "realtime_active": False},
    "Port-Hedland":      {"lat": -20.31, "lon": 118.58, "provider": "PPA", "realtime_active": False},
}

# Buoy matching & cache
BUOY_MAX_DISTANCE_KM = 200
BUOY_PAST_HOURS = 48
BUOY_CACHE_TTL_SECONDS = 1800

# Buoy observation trace styling
BUOY_OBS_TRACE_COLOR = "#22d3ee"
BUOY_OBS_TRACE_LINE_WIDTH = 2.5
BUOY_OBS_TRACE_MARKER_SIZE = 6
BUOY_OBS_TRACE_MARKER_SYMBOL = "triangle-up"
BUOY_OBS_TRACE_NAME = "Buoy Obs"

# =============================================================================
# UNIT CONVERSION & THRESHOLDS
# =============================================================================

KMH_TO_KNOTS = 0.539957
MS_TO_KNOTS = 1.943844  # m/s → knots, used by the BoM ACCESS adapter (BoM returns wind in m/s, Open-Meteo in km/h)

# Default thresholds
DEFAULT_WIND_THRESHOLD_KN = 34
DEFAULT_WAVE_THRESHOLD_M = 2.5
DEFAULT_GUST_THRESHOLD_KN = 48

# Weather window defaults
DEFAULT_WEATHER_WINDOW_WIND_KN = 34
DEFAULT_WEATHER_WINDOW_GUST_KN = 34
DEFAULT_WEATHER_WINDOW_WAVE_M = 1.0
WEATHER_WINDOW_COLOR = "rgba(34, 197, 94, 0.12)"

# Weather window model selection — only ensemble models with both wind and gust
# columns are eligible (ACCESS-G and the AI models are excluded).
DEFAULT_WEATHER_WINDOW_MODEL = "ECMWF IFS"
WEATHER_WINDOW_WIND_PERCENTILE = 0.90  # wind speed statistic across ensemble members
WEATHER_WINDOW_MODELS = [
    {"value": "ECMWF IFS", "label": "ECMWF IFS Ensemble (51 members)"},
    {"value": "GFS",       "label": "GFS Ensemble (31 members)"},
    {"value": "ACCESS-GE", "label": "BoM ACCESS-GE (18 members)"},
]

# Previous model run trace styling
PREV_RUN_TRACE_COLOR = "#6b7280"
PREV_RUN_TRACE_DASH = "dash"
PREV_RUN_TRACE_WIDTH = 2
PREV_RUN_TRACE_OPACITY = 0.7

# =============================================================================
# TIMEZONE OPTIONS
# =============================================================================

TIMEZONE_OPTIONS = [
    {"label": "UTC", "value": "UTC"},
    {"label": "Australia/Brisbane (AEST)", "value": "Australia/Brisbane"},
    {"label": "Australia/Sydney (AEST/AEDT)", "value": "Australia/Sydney"},
    {"label": "Australia/Melbourne (AEST/AEDT)", "value": "Australia/Melbourne"},
    {"label": "Australia/Perth (AWST)", "value": "Australia/Perth"},
    {"label": "Australia/Adelaide (ACST/ACDT)", "value": "Australia/Adelaide"},
    {"label": "Australia/Darwin (ACST)", "value": "Australia/Darwin"},
    {"label": "Australia/Hobart (AEST/AEDT)", "value": "Australia/Hobart"},
]

# =============================================================================
# PLOTLY CHART DEFAULTS
# =============================================================================

PLOTLY_LAYOUT_DEFAULTS = {
    "template": "plotly_dark",
    "paper_bgcolor": "#111827",
    "plot_bgcolor": "#0d1320",
    "font": {"family": "DM Sans, sans-serif", "color": "#f1f5f9", "size": 12},
    "margin": {"l": 50, "r": 30, "t": 40, "b": 80},
    "hovermode": "x unified",
    "legend": {
        "orientation": "h",
        "yanchor": "top",
        "y": -0.30,
        "xanchor": "left",
        "x": 0,
        "font": {"size": 10},
    },
}

# =============================================================================
# MAP DEFAULTS
# =============================================================================

MAP_TILES = {
    "dark": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    "natgeo": "https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}",
    "osm": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
}

AUSTRALIA_CENTER = [-25.5, 134.0]
AUSTRALIA_ZOOM = 4
