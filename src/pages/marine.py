"""
Marine Risk page — Wind & Wave Risk analysis.
Port of WWR Interactive (app_v26.py) with full callbacks + live data.
"""
import asyncio
import logging
import os
from datetime import datetime

import dash
import pandas as pd
import pytz
from dash import html, dcc, callback, clientside_callback, Input, Output, State, no_update, ctx, ALL
import dash_mantine_components as dmc
from dash_iconify import DashIconify

from src.utils.constants import (
    TRIAL_SITES,
    TRIAL_CITIES,
    MARINE_SITES,
    IMOS_WAVE_BUOYS,
    MARINE_WIND_MODELS,
    MARINE_AI_WIND_MODELS,
    MARINE_ALL_WIND_MODELS,
    DEFAULT_WEATHER_WINDOW_WIND_KN,
    DEFAULT_WEATHER_WINDOW_GUST_KN,
    DEFAULT_WEATHER_WINDOW_WAVE_M,
    DEFAULT_WEATHER_WINDOW_MODEL,
    WEATHER_WINDOW_MODELS,
    TIMEZONE_OPTIONS,
)

logger = logging.getLogger(__name__)

dash.register_page(
    __name__,
    path="/",
    name="Marine Weather Risk — Demo",
    title="Marine Weather Risk — Demo",
)

def _convert_tz(df: pd.DataFrame, tz_str: str) -> pd.DataFrame:
    """Convert a DataFrame index to the user-selected timezone."""
    if df.empty or not tz_str:
        return df
    try:
        target_tz = pytz.timezone(tz_str)
        if df.index.tz is None:
            # Assume UTC if naive
            df.index = df.index.tz_localize("UTC").tz_convert(target_tz)
        else:
            df.index = df.index.tz_convert(target_tz)
    except Exception:
        pass  # Graceful fallback — keep original timestamps
    return df


def _convert_wind_data_tz(wind_data: dict, tz_str: str) -> dict:
    """Convert all wind ensemble DataFrames to the user-selected timezone."""
    for model_key, result in wind_data.items():
        df = result.get("df", pd.DataFrame())
        if not df.empty:
            result["df"] = _convert_tz(df, tz_str)
    return wind_data


def _convert_wave_data_tz(wave_data: dict, tz_str: str) -> dict:
    """Convert all wave-model DataFrames to the user-selected timezone."""
    for name, df in list(wave_data.items()):
        if not df.empty:
            wave_data[name] = _convert_tz(df, tz_str)
    return wave_data


_INPUT_STYLE = {
    "input": {"backgroundColor": "#0d1320", "border": "1px solid #1e293b"},
}
_COMBOBOX_PORTAL = {"withinPortal": True, "zIndex": 1000}


def layout():
    # Site dropdown is filled on page mount by populate_sites() (below) from
    # TRIAL_SITES. Start empty so the DMC Select renders before the store
    # write arrives.
    site_options: list = []
    first_site = None

    return dmc.Stack(
        gap="md",
        style={"padding": "16px"},
        children=[
            # ── Controls bar ─────────────────────────────────────────────
            dmc.Paper(
                shadow="sm", p="md", radius="md",
                className="weather-controls-bar",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b", "overflow": "visible"},
                children=dmc.Group(
                    gap="lg",
                    wrap="wrap",
                    children=[
                        dmc.Select(
                            id="marine-site-select",
                            label="Site",
                            data=site_options,
                            value=first_site,
                            w={"base": "100%", "sm": 300},
                            leftSection=DashIconify(icon="tabler:map-pin"),
                            searchable=True,
                            styles=_INPUT_STYLE,
                            comboboxProps=_COMBOBOX_PORTAL,
                        ),
                        dmc.Stack(
                            gap=2,
                            children=[
                                dmc.Text("Wind < (kn)", size="xs", c="dimmed"),
                                dmc.Slider(
                                    id="marine-ww-wind-thresh",
                                    value=DEFAULT_WEATHER_WINDOW_WIND_KN,
                                    min=10, max=60, step=1,
                                    marks=[
                                        {"value": 10, "label": "10"},
                                        {"value": 25, "label": "25"},
                                        {"value": 34, "label": "34"},
                                        {"value": 48, "label": "48"},
                                        {"value": 60, "label": "60"},
                                    ],
                                    w={"base": "100%", "xs": 200}, color="orange",
                                ),
                            ],
                        ),
                        dmc.Stack(
                            gap=2,
                            children=[
                                dmc.Text("Gust < (kn)", size="xs", c="dimmed"),
                                dmc.Slider(
                                    id="marine-ww-gust-thresh",
                                    value=DEFAULT_WEATHER_WINDOW_GUST_KN,
                                    min=10, max=100, step=1,
                                    marks=[
                                        {"value": 10, "label": "10"},
                                        {"value": 34, "label": "34"},
                                        {"value": 48, "label": "48"},
                                        {"value": 70, "label": "70"},
                                        {"value": 100, "label": "100"},
                                    ],
                                    w={"base": "100%", "xs": 200}, color="orange",
                                ),
                            ],
                        ),
                        dmc.Stack(
                            gap=2,
                            children=[
                                dmc.Text("Waves < (m)", size="xs", c="dimmed"),
                                dmc.Slider(
                                    id="marine-ww-wave-thresh",
                                    value=DEFAULT_WEATHER_WINDOW_WAVE_M,
                                    min=0.5, max=6.0, step=0.25,
                                    marks=[
                                        {"value": 0.5, "label": "0.5"},
                                        {"value": 1.0, "label": "1"},
                                        {"value": 2.5, "label": "2.5"},
                                        {"value": 4, "label": "4"},
                                        {"value": 6, "label": "6"},
                                    ],
                                    w={"base": "100%", "xs": 200}, color="blue",
                                ),
                            ],
                        ),
                        dmc.NumberInput(
                            id="marine-forecast-window",
                            label="Forecast Window (hrs)",
                            value=168, min=24, max=168, step=24, w={"base": "100%", "xs": 160},
                            styles=_INPUT_STYLE,
                        ),
                        dmc.Select(
                            id="marine-timezone-select",
                            label="Timezone",
                            data=TIMEZONE_OPTIONS,
                            value="Australia/Brisbane",
                            w={"base": "100%", "sm": 220},
                            searchable=True,
                            styles=_INPUT_STYLE,
                            comboboxProps=_COMBOBOX_PORTAL,
                        ),
                        dmc.Button(
                            "Refresh",
                            id="marine-refresh-btn",
                            leftSection=DashIconify(icon="tabler:refresh"),
                            variant="light", color="orange", mt=22,
                        ),
                        # NOTE: Overlay Previous Run removed — Open-Meteo ensemble API
                        # does not support previous model initialisations. Will revisit
                        # when we add our own run caching or switch to a different API.
                        html.Div(id="marine-prev-run-toggle", style={"display": "none"}),
                    ],
                ),
            ),

            # ── Optimal Window controls (toggle + model picker) ───────
            dmc.Paper(
                shadow="sm", p="sm", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                children=dmc.Group(
                    gap="lg", wrap="wrap", align="center",
                    children=[
                        dmc.Group(gap="sm", children=[
                            DashIconify(icon="tabler:clock-check", color="#22c55e"),
                            dmc.Text("Optimal Windows", size="sm", fw=600, c="white"),
                            dmc.Switch(
                                id="marine-weather-window-toggle",
                                checked=False,
                                color="green",
                                size="sm",
                            ),
                        ]),
                        dmc.Stack(
                            gap=2,
                            children=[
                                dmc.Text("Model (P90 wind, P100 gust)", size="xs", c="dimmed"),
                                dmc.Select(
                                    id="marine-ww-model",
                                    data=WEATHER_WINDOW_MODELS,
                                    value=DEFAULT_WEATHER_WINDOW_MODEL,
                                    allowDeselect=False,
                                    w={"base": "100%", "xs": 260},
                                ),
                            ],
                        ),
                        dmc.Text(
                            "Thresholds above drive both the red lines on charts and the optimal-window calculation.",
                            size="xs", c="dimmed",
                        ),
                    ],
                ),
            ),

            # ── Weather window summary + Timing stats ─────────────────
            html.Div(id="marine-weather-window-summary"),
            html.Div(id="marine-timing-stats"),

            # ── Cached data stores for lightweight recalculation ────────
            dcc.Store(id="marine-prev-run-store"),
            # Site list + lat/lon map, populated by populate_sites on page
            # mount from the hardwired TRIAL_SITES constant. Downstream
            # callbacks read this for coord lookup.
            dcc.Store(id="marine-user-locations"),
            # Analytics sink — clientside callbacks write here as a no-op
            # side-channel so we can hang posthog.capture() calls off Dash's
            # component lifecycle without needing a real Output target.
            dcc.Store(id="posthog-sink"),

            # ── Site map ───────────────────────────────────────────────
            dmc.Paper(
                shadow="sm", p="md", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                children=dcc.Loading(
                    type="circle", color="#f59e0b",
                    children=html.Div(id="marine-map-container"),
                ),
            ),

            # ── Buoy observation source store ─────────────────────────────
            dcc.Store(id="marine-buoy-source-store", data={"source": "none", "label": ""}),

            # ── Status banner + buoy badge ────────────────────────────────
            dmc.Group(
                gap="sm",
                children=[
                    html.Div(id="marine-status-banner", style={"flex": 1}),
                    html.Div(id="marine-buoy-status"),
                ],
            ),

            # ── Tabs: Wind Risk | Wind Gusts | Wave Forecast ─────────────
            dmc.Tabs(
                id="marine-tabs",
                value="wind-risk",
                color="orange",
                children=[
                    dmc.TabsList(children=[
                        dmc.TabsTab("Wind Risk", value="wind-risk",
                                    leftSection=DashIconify(icon="tabler:wind")),
                        dmc.TabsTab("Wind Gusts", value="wind-gusts",
                                    leftSection=DashIconify(icon="tabler:tornado")),
                        dmc.TabsTab("Wave Forecast", value="wave-forecast",
                                    leftSection=DashIconify(icon="tabler:waves")),
                    ]),

                    # ── Wind Risk panel ─────────────────────────────────
                    dmc.TabsPanel(
                        value="wind-risk",
                        children=dmc.Stack(gap="md", pt="md", children=[
                            dmc.Paper(
                                shadow="sm", p="md", radius="md",
                                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                                children=dcc.Loading(type="circle", color="#f59e0b", children=
                                    dcc.Graph(id="marine-exceedance-chart",
                                              config={"displaylogo": False},
                                              className="wid-chart-md")),
                            ),
                            dmc.Paper(
                                shadow="sm", p="md", radius="md",
                                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                                children=[
                                    dmc.SegmentedControl(
                                        id="marine-model-select",
                                        data=[
                                            {"label": "ECMWF", "value": "ECMWF IFS"},
                                            {"label": "GFS", "value": "GFS"},
                                            {"label": "ICON", "value": "ICON"},
                                            {"label": "AIFS", "value": "ECMWF AIFS Ens"},
                                            {"label": "AIGFS", "value": "AIGFS Ens"},
                                            {"label": "ACCESS-G", "value": "ACCESS-G"},
                                            {"label": "ACCESS-GE", "value": "ACCESS-GE"},
                                        ],
                                        value="ECMWF IFS", color="orange", mb="md",
                                    ),
                                    dcc.Loading(type="circle", color="#f59e0b", children=
                                        dcc.Graph(id="marine-ensemble-chart",
                                                  config={"displaylogo": False},
                                                  className="wid-chart-md")),
                                ],
                            ),
                            # Summary stats
                            html.Div(id="marine-wind-stats"),
                        ]),
                    ),

                    # ── Wind Gusts panel ────────────────────────────────
                    dmc.TabsPanel(
                        value="wind-gusts",
                        children=dmc.Stack(gap="md", pt="md", children=[
                            dmc.Paper(
                                shadow="sm", p="md", radius="md",
                                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                                children=dcc.Loading(type="circle", color="#f59e0b", children=
                                    dcc.Graph(id="marine-gust-chart",
                                              config={"displaylogo": False},
                                              className="wid-chart-md")),
                            ),
                            html.Div(id="marine-gust-stats"),
                        ]),
                    ),

                    # ── Wave Forecast panel ─────────────────────────────
                    dmc.TabsPanel(
                        value="wave-forecast",
                        children=dmc.Stack(gap="md", pt="md", children=[
                            dmc.Paper(
                                shadow="sm", p="md", radius="md",
                                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                                children=dcc.Loading(type="circle", color="#f59e0b", children=
                                    dcc.Graph(id="marine-wave-chart",
                                              config={"displaylogo": False},
                                              className="wid-chart-md")),
                            ),
                            dmc.Paper(
                                shadow="sm", p="md", radius="md",
                                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                                children=dcc.Loading(type="circle", color="#f59e0b", children=
                                    dcc.Graph(id="marine-wave-period-chart",
                                              config={"displaylogo": False},
                                              className="wid-chart-md")),
                            ),
                            html.Div(id="marine-wave-stats"),
                        ]),
                    ),
                ],
            ),

            # ── Download section ─────────────────────────────────────────
            dmc.Paper(
                shadow="sm", p="md", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
                children=dmc.Group(
                    gap="md",
                    children=[
                        dmc.Text("Downloads", size="sm", fw=600, c="white"),
                        dmc.Button(
                            "Interactive HTML",
                            id="marine-html-btn",
                            leftSection=DashIconify(icon="tabler:file-code"),
                            variant="light", color="orange", size="sm",
                        ),
                        dmc.Button(
                            "CSV Export",
                            id="marine-csv-btn",
                            leftSection=DashIconify(icon="tabler:file-spreadsheet"),
                            variant="light", color="green", size="sm",
                        ),
                        dmc.Button(
                            "PDF Report",
                            id="marine-pdf-btn",
                            leftSection=DashIconify(icon="tabler:file-type-pdf"),
                            variant="light", color="red", size="sm",
                        ),
                        dcc.Download(id="marine-download"),
                    ],
                ),
            ),
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# CALLBACKS
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_coords(site_name: str, user_locs: dict):
    """
    Look up lat/lon for a site, preferring the populate_sites store.
    Falls back to the global MARINE_SITES / TRIAL_CITIES / IMOS_WAVE_BUOYS
    tables for the brief window before populate_sites fires on mount.
    """
    from src.services.marine_service import get_combined_site_coords
    if user_locs and site_name in user_locs:
        return user_locs[site_name]["lat"], user_locs[site_name]["lon"]
    return get_combined_site_coords(site_name)


def _is_marine_for_user(site_name: str, user_locs: dict) -> bool:
    """Marine = offshore-typed site in the store, or an IMOS wave buoy."""
    from src.services.marine_service import _resolve_buoy_name
    if user_locs and site_name in user_locs:
        return user_locs[site_name].get("type") == "offshore"
    from src.services.marine_service import is_marine_site
    return is_marine_site(site_name) or _resolve_buoy_name(site_name) is not None


# 1. Master data fetch + all charts
@callback(
    Output("marine-status-banner", "children"),
    Output("marine-exceedance-chart", "figure"),
    Output("marine-gust-chart", "figure"),
    Output("marine-wave-chart", "figure"),
    Output("marine-wave-period-chart", "figure"),
    Output("marine-wind-stats", "children"),
    Output("marine-gust-stats", "children"),
    Output("marine-wave-stats", "children"),
    Output("marine-buoy-source-store", "data"),
    Output("marine-weather-window-summary", "children"),
    Input("marine-site-select", "value"),
    Input("marine-ww-wind-thresh", "value"),
    Input("marine-ww-gust-thresh", "value"),
    Input("marine-ww-wave-thresh", "value"),
    Input("marine-forecast-window", "value"),
    Input("marine-refresh-btn", "n_clicks"),
    Input("marine-weather-window-toggle", "checked"),
    Input("marine-ww-model", "value"),
    Input("marine-timezone-select", "value"),
    State("marine-user-locations", "data"),
    prevent_initial_call=False,
)
def update_marine_charts(
    site_name, wind_threshold, gust_threshold, wave_threshold, forecast_window,
    _n_clicks, ww_enabled, ww_model, timezone_str, user_locs,
):
    from src.services.marine_service import (
        async_fetch_all_marine_data,
        calculate_wind_exceedance, get_gust_stats,
        analyze_wind_exceedance, analyze_gust_exceedance, analyze_wave_exceedance,
        calculate_model_agreement, calculate_weather_windows,
    )
    from src.components.marine_charts import (
        create_wind_exceedance_chart, create_gust_chart,
        create_wave_chart, create_wave_period_chart, _empty,
    )

    # Single-source thresholds — the optimal-window sliders drive every chart's
    # red reference line, the exceedance banner, and (when the toggle is on)
    # the green window bands.
    wind_threshold = wind_threshold or DEFAULT_WEATHER_WINDOW_WIND_KN
    gust_threshold = gust_threshold or DEFAULT_WEATHER_WINDOW_GUST_KN
    wave_threshold = wave_threshold or DEFAULT_WEATHER_WINDOW_WAVE_M
    forecast_window = forecast_window or 168
    ww_model = ww_model or DEFAULT_WEATHER_WINDOW_MODEL

    empty_fig = _empty("Select a site to view data")
    empty_banner = _make_banner("Loading...", "gray", "tabler:loader")
    empty_stats = html.Div()
    no_buoy_meta = {"source": "none", "label": ""}
    empty_return = (empty_banner, empty_fig, empty_fig, empty_fig, empty_fig,
                    empty_stats, empty_stats, empty_stats, no_buoy_meta, html.Div())

    if not site_name:
        return empty_return

    try:
        lat, lon = _resolve_coords(site_name, user_locs)
    except ValueError:
        return empty_return

    is_marine = _is_marine_for_user(site_name, user_locs)

    # ── Async fetch: wind + wave + buoy concurrently ─────────────────
    try:
        loop = asyncio.new_event_loop()
        data = loop.run_until_complete(async_fetch_all_marine_data(lat, lon, is_marine))
    finally:
        loop.close()

    wind_data = data["wind_data"]
    wave_data = data.get("wave_data", {})
    wave_df = data["wave_df"]  # ECMWF primary, used by analysis routines
    buoy_df = data["buoy_df"]
    buoy_meta = data["buoy_meta"]

    # ── Convert timestamps to the user-selected timezone ──────────
    tz_str = timezone_str or "Australia/Brisbane"
    wind_data = _convert_wind_data_tz(wind_data, tz_str)
    wave_data = _convert_wave_data_tz(wave_data, tz_str)
    wave_df = wave_data.get("ECMWF", _convert_tz(wave_df, tz_str))
    if buoy_df is not None and not buoy_df.empty:
        buoy_df = _convert_tz(buoy_df, tz_str)

    if buoy_meta:
        logger.info(
            "Buoy matched: %s (%.0f km from %s)",
            buoy_meta["name"], buoy_meta["distance_km"], site_name,
        )

    # ── Weather windows ──────────────────────────────────────────────
    windows = None
    ww_summary = html.Div()
    if ww_enabled:
        ww_result = calculate_weather_windows(
            wind_data, wave_df, wind_threshold, gust_threshold, wave_threshold,
            model_key=ww_model,
        )
        windows = ww_result["windows"]
        ww_summary = _weather_window_summary(
            ww_result, wind_threshold, gust_threshold, wave_threshold,
        )

    # ── Wind exceedance probabilities ────────────────────────────────
    exceedance_data = {}
    for model_key, result in wind_data.items():
        df = result.get("df", pd.DataFrame())
        if not df.empty:
            exceedance_data[model_key] = calculate_wind_exceedance(df, wind_threshold)

    # ── Model agreement ──────────────────────────────────────────────
    agreement = calculate_model_agreement(wind_data, forecast_window)

    # ── Wind exceedance chart ────────────────────────────────────────
    exc_fig = create_wind_exceedance_chart(exceedance_data, wind_threshold, site_name, agreement)

    # ── Gust chart ───────────────────────────────────────────────────
    gust_data = {}
    for model_key, result in wind_data.items():
        df = result.get("df", pd.DataFrame())
        if not df.empty:
            gust_data[model_key] = get_gust_stats(df)
    gust_fig = create_gust_chart(gust_data, gust_threshold, site_name,
                                 weather_windows=windows)

    # ── Wave charts (with buoy observation overlay) ─────────────────
    wave_fig = create_wave_chart(
        wave_data, wave_threshold, site_name,
        buoy_df=buoy_df, buoy_meta=buoy_meta,
        weather_windows=windows,
    )
    wave_period_fig = create_wave_period_chart(
        wave_data, site_name,
        buoy_df=buoy_df, buoy_meta=buoy_meta,
    )

    # ── Risk analysis for status banner ──────────────────────────────
    any_exceedance = False
    exceed_parts = []

    for model_key, result in wind_data.items():
        df = result.get("df", pd.DataFrame())
        w_result = analyze_wind_exceedance(df, wind_threshold, forecast_window, model_key)
        if w_result and w_result["exceeds"]:
            any_exceedance = True
            exceed_parts.append(f"Wind: {model_key}")

        g_result = analyze_gust_exceedance(df, gust_threshold, forecast_window, model_key)
        if g_result and g_result["exceeds"]:
            any_exceedance = True
            exceed_parts.append(f"Gusts: {model_key}")

    if is_marine:
        wav_result = analyze_wave_exceedance(wave_df, wave_threshold, forecast_window, "ECMWF")
        if wav_result and wav_result["exceeds"]:
            any_exceedance = True
            exceed_parts.append(f"Waves: ECMWF ({wav_result['peak_wave']:.1f} m)")

    # ── Build status banner ──────────────────────────────────────────
    banners = []
    if not is_marine:
        banners.append(dmc.Alert(
            "This is a land-based location. Wave and swell data is not available for this site. "
            "Wind speed and gust analysis is shown below.",
            title="Land Station",
            color="blue", variant="light",
            icon=DashIconify(icon="tabler:map-pin"),
        ))

    if any_exceedance:
        banners.append(_make_banner(
            f"Threshold exceedances — {site_name} — Next {forecast_window}h: {' | '.join(exceed_parts)}",
            "red", "tabler:alert-triangle",
        ))
    else:
        banners.append(_make_banner(
            f"All parameters within thresholds — {site_name} — Next {forecast_window}h",
            "green", "tabler:check",
        ))
    banner = html.Div(banners) if len(banners) > 1 else banners[0]

    # ── Summary stat cards ───────────────────────────────────────────
    wind_stats = _wind_stat_cards(wind_data, wind_threshold, agreement)
    gust_stats = _gust_stat_cards(gust_data)
    wave_stats = _wave_stat_cards(wave_data, buoy_df=buoy_df, buoy_meta=buoy_meta)

    # ── Buoy source metadata for store ────────────────────────────────
    buoy_source_out = no_buoy_meta
    if buoy_meta:
        buoy_source_out = {
            "source": "imos_buoy",
            "label": f"{buoy_meta['name']} ({buoy_meta['distance_km']:.0f} km)",
        }

    return (banner, exc_fig, gust_fig, wave_fig, wave_period_fig,
            wind_stats, gust_stats, wave_stats, buoy_source_out, ww_summary)


# 1b. Buoy observation status badge
@callback(
    Output("marine-buoy-status", "children"),
    Input("marine-buoy-source-store", "data"),
)
def update_buoy_status(buoy_source_data):
    """Show which buoy is matched (or none)."""
    source = (buoy_source_data or {}).get("source", "none")
    label = (buoy_source_data or {}).get("label", "")

    if source == "imos_buoy":
        return dmc.Badge(
            f"BUOY: {label}",
            color="cyan", variant="light", size="sm",
        )
    return dmc.Badge(
        "No buoy nearby", color="gray", variant="outline", size="sm",
    )


# 2. Ensemble spread chart (per-model selection)
@callback(
    Output("marine-ensemble-chart", "figure"),
    Input("marine-model-select", "value"),
    Input("marine-site-select", "value"),
    Input("marine-ww-wind-thresh", "value"),
    Input("marine-refresh-btn", "n_clicks"),
    Input("marine-timezone-select", "value"),
    State("marine-user-locations", "data"),
    prevent_initial_call=False,
)
def update_ensemble_chart(model_key, site_name, wind_threshold, _n, timezone_str, user_locs):
    from src.services.marine_service import (
        fetch_wind_ensemble, calculate_ensemble_stats,
    )
    from src.components.marine_charts import create_ensemble_spread_chart, _empty

    if not site_name or not model_key:
        return _empty("Select a site and model")

    wind_threshold = wind_threshold or DEFAULT_WEATHER_WINDOW_WIND_KN
    try:
        lat, lon = _resolve_coords(site_name, user_locs)
    except ValueError:
        return _empty("Site not found")

    result = fetch_wind_ensemble(lat, lon, model_key)
    df = result.get("df", pd.DataFrame())
    if df.empty:
        return _empty(f"No data available for {model_key}")

    df = _convert_tz(df, timezone_str or "Australia/Brisbane")
    stats = calculate_ensemble_stats(df)
    if stats.empty:
        return _empty(f"No ensemble members for {model_key}")

    config = MARINE_ALL_WIND_MODELS.get(model_key, {})
    color = config.get("color", "#888888")
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    fill = f"rgba({r}, {g}, {b}, 0.15)"
    members = config.get("members", "?")

    return create_ensemble_spread_chart(stats, wind_threshold, model_key, color, fill, members)


# 1d. Populate site dropdown on page mount.
# Site list is hardwired via TRIAL_SITES (4 sites) + IMOS buoys. The
# marine-user-locations store shape (name -> {lat, lon, type}) is preserved
# from the parent app so the downstream chart / map / download callbacks are
# unchanged.
@callback(
    Output("marine-site-select", "data"),
    Output("marine-site-select", "value", allow_duplicate=True),
    Output("marine-user-locations", "data"),
    Input("url", "pathname"),
    prevent_initial_call="initial_duplicate",
)
def populate_sites(_pathname):
    offshore_items = [s for s in TRIAL_SITES if s["type"] == "offshore"]
    city_items     = [s for s in TRIAL_SITES if s["type"] == "city"]

    locs_map = {s["name"]: {"lat": s["lat"], "lon": s["lon"], "type": s["type"]}
                for s in TRIAL_SITES}

    offshore_options = [{"label": f"\u2693 {loc['name']}", "value": loc["name"]} for loc in offshore_items]
    city_options     = [{"label": loc["name"], "value": loc["name"]} for loc in city_items]
    buoy_options     = [{"label": f"\U0001f4a7 {name}", "value": f"buoy:{name}"}
                        for name in IMOS_WAVE_BUOYS.keys()]

    groups = []
    if offshore_options:
        groups.append({"group": "Offshore", "items": offshore_options})
    if city_options:
        groups.append({"group": "Land", "items": city_options})
    groups.append({"group": "Wave Buoys (IMOS)", "items": buoy_options})

    default_env = os.getenv("DEFAULT_SITE", "")
    first_site = (
        default_env if default_env in locs_map
        else (offshore_items[0]["name"] if offshore_items
              else (city_items[0]["name"] if city_items else None))
    )
    return groups, first_site, locs_map


# 2b. Previous run overlay — DISABLED
# Open-Meteo's ensemble API does not support previous model initialisations.
# The _previous_day1 suffix only works on deterministic forecast variables, not
# ensemble model names.  Will revisit when we add our own run caching (Redis)
# or switch to an API that stores prior ensemble runs.
@callback(
    Output("marine-timing-stats", "children"),
    Output("marine-prev-run-store", "data"),
    Input("marine-prev-run-toggle", "checked"),
    prevent_initial_call=True,
)
def update_previous_run_overlay(prev_enabled):
    # No-op — hidden toggle, but kept so Dash doesn't complain about missing outputs
    return html.Div(), None


# 3. CSV / PDF / Interactive HTML download
@callback(
    Output("marine-download", "data"),
    Input("marine-csv-btn", "n_clicks"),
    Input("marine-pdf-btn", "n_clicks"),
    Input("marine-html-btn", "n_clicks"),
    State("marine-site-select", "value"),
    State("marine-ww-wind-thresh", "value"),
    State("marine-ww-gust-thresh", "value"),
    State("marine-ww-wave-thresh", "value"),
    State("marine-forecast-window", "value"),
    State("marine-user-locations", "data"),
    prevent_initial_call=True,
)
def download_marine(csv_clicks, pdf_clicks, html_clicks, site_name,
                    wind_thr, gust_thr, wave_thr, window, user_locs):
    from src.services.marine_service import (
        fetch_wind_ensemble, fetch_wave_forecast,
        calculate_model_agreement, fetch_all_wind_ensembles,
        calculate_wind_exceedance, get_gust_stats, calculate_ensemble_stats,
    )
    from src.services.export_service import convert_df_to_csv, generate_pdf_report, generate_interactive_html
    from src.components.marine_charts import (
        create_wind_exceedance_chart, create_gust_chart,
        create_wave_chart, create_wave_period_chart, create_ensemble_spread_chart,
    )

    triggered = ctx.triggered_id
    if not site_name:
        return no_update

    lat, lon = _resolve_coords(site_name, user_locs)
    is_marine = _is_marine_for_user(site_name, user_locs)
    safe_name = site_name.replace(" ", "_").replace("/", "-")
    today = datetime.now().strftime("%Y%m%d")
    wind_thr = wind_thr or DEFAULT_WEATHER_WINDOW_WIND_KN
    gust_thr = gust_thr or DEFAULT_WEATHER_WINDOW_GUST_KN
    wave_thr = wave_thr or DEFAULT_WEATHER_WINDOW_WAVE_M
    window = window or 168

    # CSV download — ECMWF wind data
    if triggered == "marine-csv-btn":
        result = fetch_wind_ensemble(lat, lon, "ECMWF IFS")
        df = result.get("df", pd.DataFrame())
        if df.empty:
            return no_update
        csv = convert_df_to_csv(df)
        return dict(content=csv, filename=f"{safe_name}_ECMWF_Wind_{today}.csv")

    # PDF download
    if triggered == "marine-pdf-btn":
        try:
            ecmwf = fetch_wind_ensemble(lat, lon, "ECMWF IFS")
            wind_df = ecmwf.get("df", pd.DataFrame())
            wave_df = fetch_wave_forecast(lat, lon) if is_marine else pd.DataFrame()
            wind_data = fetch_all_wind_ensembles(lat, lon)
            agreement = calculate_model_agreement(wind_data, window)

            pdf_bytes = generate_pdf_report(
                site_name, wind_df, wave_df,
                wind_thr, wave_thr,
                "ECMWF IFS",
                gust_df=wind_df,
                model_agreement=agreement,
            )
            if not pdf_bytes:
                logger.error("PDF generation returned empty bytes — is ReportLab installed?")
                return no_update
            return dcc.send_bytes(
                pdf_bytes,
                filename=f"{safe_name}_ECMWF_Report_{today}.pdf",
                type="application/pdf",
            )
        except Exception as e:
            logger.error("Failed to generate PDF report: %s", e)
            return no_update

    # Interactive HTML download — all charts bundled into one file
    if triggered == "marine-html-btn":
        try:
            from src.services.marine_service import async_fetch_all_marine_data
            try:
                loop = asyncio.new_event_loop()
                data = loop.run_until_complete(async_fetch_all_marine_data(lat, lon, is_marine))
            finally:
                loop.close()
            wind_data = data["wind_data"]
            wave_data = data.get("wave_data", {})
            wave_df = data["wave_df"]
            buoy_df = data["buoy_df"]
            buoy_meta = data["buoy_meta"]

            # Wind exceedance probabilities
            exceedance_data = {}
            for model_key, result in wind_data.items():
                df = result.get("df", pd.DataFrame())
                if not df.empty:
                    exceedance_data[model_key] = calculate_wind_exceedance(df, wind_thr)

            # Model agreement
            agreement = calculate_model_agreement(wind_data, window)

            # Build all figures
            figures = {}

            # 1. Wind exceedance chart
            figures["Wind Speed Exceedance Probability"] = create_wind_exceedance_chart(
                exceedance_data, wind_thr, site_name, agreement,
            )

            # 2. Ensemble spread charts — one per model
            for model_key, result in wind_data.items():
                df = result.get("df", pd.DataFrame())
                if df.empty:
                    continue
                stats = calculate_ensemble_stats(df)
                if stats.empty:
                    continue
                config = MARINE_ALL_WIND_MODELS.get(model_key, {})
                color = config.get("color", "#888888")
                r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
                fill = f"rgba({r}, {g}, {b}, 0.15)"
                members = config.get("members", "?")
                figures[f"Ensemble Spread \u2014 {model_key}"] = create_ensemble_spread_chart(
                    stats, wind_thr, model_key, color, fill, members,
                )

            # 3. Gust chart
            gust_data = {}
            for model_key, result in wind_data.items():
                df = result.get("df", pd.DataFrame())
                if not df.empty:
                    gust_data[model_key] = get_gust_stats(df)
            figures["Wind Gust Comparison"] = create_gust_chart(
                gust_data, gust_thr, site_name,
            )

            # 4. Wave height (multi-model + buoy observations)
            figures["Significant Wave Height"] = create_wave_chart(
                wave_data, wave_thr, site_name,
                buoy_df=buoy_df, buoy_meta=buoy_meta,
            )

            # 5. Wave period (multi-model + buoy observations)
            figures["Wave Period & Sea State"] = create_wave_period_chart(
                wave_data, site_name,
                buoy_df=buoy_df, buoy_meta=buoy_meta,
            )

            # Summary stats for the header
            summary = {}
            for model_key, result in wind_data.items():
                df = result.get("df", pd.DataFrame())
                wind_cols = [c for c in df.columns if "wind_speed_10m" in c and "member" in c]
                if wind_cols:
                    median = df[wind_cols].median(axis=1, skipna=True)
                    summary[f"{model_key} Peak Median Wind"] = f"{median.max():.1f} kn"
            if not wave_df.empty and "wave_height" in wave_df.columns:
                summary["Peak Wave Height"] = f"{wave_df['wave_height'].max():.2f} m"
            if not wave_df.empty and "wave_period" in wave_df.columns:
                avg_p = wave_df["wave_period"].mean()
                summary["Avg Wave Period"] = f"{avg_p:.1f} s"

            html_content = generate_interactive_html(
                figures=figures,
                site_name=site_name,
                wind_threshold=wind_thr,
                wave_threshold=wave_thr,
                forecast_window=window,
                model_agreement=agreement,
                summary_stats=summary,
            )

            return dict(
                content=html_content,
                filename=f"{safe_name}_Interactive_Report_{today}.html",
            )

        except Exception as e:
            logger.error("Failed to generate interactive HTML: %s", e)
            return no_update

    return no_update


# 4. Map render — fires once the logged-in user's locations are known.
# Triggering off user-locations (not site-select) avoids a race with
# populate_user_sites: previously, for fast single-client logins, populate
# would set the site value before render_marine_map's initial call, which
# then saw ctx.triggered_id='marine-site-select' and skipped rendering
# entirely. Now the map redraws per user (login/logout/user switch) and
# site-select changes don't retrigger it, which preserves zoom/pan.
@callback(
    Output("marine-map-container", "children"),
    Input("marine-user-locations", "data"),
    State("marine-site-select", "value"),
    prevent_initial_call=False,
)
def render_marine_map(user_locs, selected_site):
    from src.components.map_components import create_marine_combined_map

    # Marker source: the populated user-locations store in steady state; fall
    # back to MARINE_SITES / TRIAL_CITIES during the brief window before
    # populate_sites fires on first mount.
    if user_locs:
        marine_dict = {name: {"lat": loc["lat"], "lon": loc["lon"]}
                       for name, loc in user_locs.items() if loc.get("type") == "offshore"}
        land_df = pd.DataFrame([
            {"site": name, "lat": loc["lat"], "lon": loc["lon"]}
            for name, loc in user_locs.items() if loc.get("type") == "city"
        ])
    else:
        marine_dict = MARINE_SITES
        land_df = pd.DataFrame([
            {"site": name, "lat": v["lat"], "lon": v["lon"]}
            for name, v in TRIAL_CITIES.items()
        ])

    return create_marine_combined_map(
        land_sites_df=land_df,
        marine_sites_dict=marine_dict,
        selected_site=selected_site,
        buoy_sites_dict=IMOS_WAVE_BUOYS,
    )


# 5. Click a station marker on the map → update dropdown
@callback(
    Output("marine-site-select", "value", allow_duplicate=True),
    Input({"type": "marine-map-marker", "site": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_marine_site_from_map(n_clicks_list):
    if not ctx.triggered_id or not any(n for n in n_clicks_list if n):
        return no_update
    return ctx.triggered_id["site"]


# ─────────────────────────────────────────────────────────────────────────────
# HELPER COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

def _make_banner(message, color, icon):
    return dmc.Alert(
        message, title="Threshold Status",
        color=color, variant="light",
        icon=DashIconify(icon=icon),
    )


def _wind_stat_cards(wind_data, wind_threshold, agreement):
    cards = []
    for model_key, result in wind_data.items():
        df = result.get("df", pd.DataFrame())
        if df.empty:
            continue
        wind_cols = [c for c in df.columns if "wind_speed_10m" in c and "member" in c]
        if not wind_cols:
            continue
        median = df[wind_cols].median(axis=1, skipna=True)
        p90 = df[wind_cols].quantile(0.9, axis=1)
        color = MARINE_ALL_WIND_MODELS.get(model_key, {}).get("color", "#888")
        members = MARINE_ALL_WIND_MODELS.get(model_key, {}).get("members", "?")
        # Deterministic models (ACCESS-G) have no spread: collapse to a
        # single "Max" line so we don't imply a 1-member ensemble.
        if members == 1:
            stat_lines = [
                dmc.Text(f"Max: {median.max():.1f} kn", size="xs", c="dimmed"),
            ]
        else:
            stat_lines = [
                dmc.Text(f"Max Median: {median.max():.1f} kn", size="xs", c="dimmed"),
                dmc.Text(f"Max P90: {p90.max():.1f} kn", size="xs", c="dimmed"),
            ]
        cards.append(
            dmc.Paper(
                shadow="sm", p="sm", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b", "flex": 1},
                children=dmc.Stack(gap=2, children=[
                    dmc.Group(gap="xs", children=[
                        html.Div(style={"width": "10px", "height": "10px", "borderRadius": "50%",
                                        "backgroundColor": color}),
                        dmc.Text(f"{model_key} ({members})", size="xs", fw=600, c="white"),
                    ]),
                    *stat_lines,
                ]),
            )
        )

    # Agreement card
    if agreement and agreement.get("score") is not None:
        cards.append(
            dmc.Paper(
                shadow="sm", p="sm", radius="md",
                style={"backgroundColor": "#111827", "border": f"1px solid {agreement['color']}", "flex": 1},
                children=dmc.Stack(gap=2, children=[
                    dmc.Text("Model Agreement", size="xs", fw=600, c="white"),
                    dmc.Text(f"{agreement['score']:.0f}% — {agreement['level']}",
                             size="sm", fw=700, c=agreement["color"]),
                    dmc.Text(agreement["interpretation"], size="xs", c="dimmed"),
                ]),
            )
        )

    return dmc.Group(gap="md", grow=True, wrap="wrap", children=cards) if cards else html.Div()


def _gust_stat_cards(gust_data):
    cards = []
    for model_key, stats in gust_data.items():
        if stats is None or stats.empty:
            continue
        color = MARINE_ALL_WIND_MODELS.get(model_key, {}).get("color", "#888")
        col = "max" if "max" in stats.columns else "median"
        series = stats[col].dropna()
        if series.empty:
            continue
        peak = float(series.max())
        peak_time = series.idxmax()
        peak_str = peak_time.strftime("%a %d %b %H:%M") if hasattr(peak_time, "strftime") else ""
        cards.append(
            dmc.Paper(
                shadow="sm", p="sm", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b", "flex": 1},
                children=dmc.Stack(gap=2, children=[
                    dmc.Group(gap="xs", children=[
                        html.Div(style={"width": "10px", "height": "10px", "borderRadius": "50%",
                                        "backgroundColor": color}),
                        dmc.Text(model_key, size="xs", fw=600, c="white"),
                    ]),
                    dmc.Text(f"Peak Gust: {peak:.0f} kn", size="sm", fw=700, c="white"),
                    dmc.Text(peak_str, size="xs", c="dimmed"),
                ]),
            )
        )
    return dmc.Group(gap="md", grow=True, wrap="wrap", children=cards) if cards else html.Div()


def _wave_stat_cards(wave_data, buoy_df=None, buoy_meta=None):
    """Render one peak-Hs card per wave model + an average-period card from ECMWF."""
    from src.utils.constants import MARINE_WAVE_MODELS
    cards = []

    # Per-model peak Hs cards
    for model_name, df in (wave_data or {}).items():
        if df is None or df.empty or "wave_height" not in df.columns:
            continue
        hs_series = df["wave_height"].dropna()
        if hs_series.empty:
            continue
        max_hs = float(hs_series.max())
        max_hs_time = hs_series.idxmax()
        hs_time_str = max_hs_time.strftime("%a %d %b %H:%M") if hasattr(max_hs_time, "strftime") else ""
        color = MARINE_WAVE_MODELS.get(model_name, {}).get("color", "#888")
        cards.append(
            dmc.Paper(
                shadow="sm", p="sm", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #1e293b", "flex": 1},
                children=dmc.Stack(gap=2, children=[
                    dmc.Group(gap="xs", children=[
                        html.Div(style={"width": "10px", "height": "10px", "borderRadius": "50%",
                                        "backgroundColor": color}),
                        dmc.Text(f"{model_name} Waves", size="xs", fw=600, c="white"),
                    ]),
                    dmc.Text(f"Max Height: {max_hs:.2f} m",
                             size="sm", fw=700, c="white"),
                    dmc.Text(hs_time_str, size="xs", c="dimmed"),
                ]),
            )
        )

    # Single ECMWF wave-period card (period is similar across models)
    ecmwf_df = (wave_data or {}).get("ECMWF", pd.DataFrame())
    if not ecmwf_df.empty and "wave_period" in ecmwf_df.columns:
        avg_period = ecmwf_df["wave_period"].mean()
        if pd.notna(avg_period):
            sea = "Choppy" if avg_period < 6 else ("Swell" if avg_period > 10 else "Standard")
            cards.append(
                dmc.Paper(
                    shadow="sm", p="sm", radius="md",
                    style={"backgroundColor": "#111827", "border": "1px solid #1e293b", "flex": 1},
                    children=dmc.Stack(gap=2, children=[
                        dmc.Text("Wave Period (ECMWF)", size="xs", fw=600, c="white"),
                        dmc.Text(f"Avg: {avg_period:.1f} s ({sea})", size="sm", fw=700, c="white"),
                    ]),
                )
            )

    # Buoy observation stat card
    if buoy_df is not None and not buoy_df.empty and buoy_meta:
        buoy_name = buoy_meta.get("name", "Buoy")
        distance = buoy_meta.get("distance_km", 0)
        buoy_children = [
            dmc.Text(f"Buoy: {buoy_name} ({distance:.0f} km)", size="xs", fw=600, c="white"),
        ]
        if "wave_height" in buoy_df.columns:
            latest_hs = buoy_df["wave_height"].dropna()
            if not latest_hs.empty:
                buoy_children.append(
                    dmc.Text(f"Latest Hs: {latest_hs.iloc[-1]:.2f} m", size="sm", fw=700, c="#22d3ee"),
                )
        if "wave_period" in buoy_df.columns:
            latest_tp = buoy_df["wave_period"].dropna()
            if not latest_tp.empty:
                buoy_children.append(
                    dmc.Text(f"Latest Tp: {latest_tp.iloc[-1]:.1f} s", size="xs", c="#22d3ee"),
                )
        cards.append(
            dmc.Paper(
                shadow="sm", p="sm", radius="md",
                style={"backgroundColor": "#111827", "border": "1px solid #22d3ee", "flex": 1},
                children=dmc.Stack(gap=2, children=buoy_children),
            )
        )

    return dmc.Group(gap="md", grow=True, wrap="wrap", children=cards) if cards else html.Div()


def _weather_window_summary(ww_result, wind_thresh, gust_thresh, wave_thresh):
    """Build a summary card for weather windows."""
    windows = ww_result.get("windows", [])
    total_hours = ww_result.get("total_hours", 0)
    next_window = ww_result.get("next_window")
    is_open = ww_result.get("is_open_now", False)
    model_key = ww_result.get("model_key", DEFAULT_WEATHER_WINDOW_MODEL)

    status_color = "green" if is_open else "red"
    status_text = "WINDOW OPEN" if is_open else "WINDOW CLOSED"

    children = [
        dmc.Group(gap="sm", children=[
            DashIconify(icon="tabler:clock-check", color="#22c55e", width=20),
            dmc.Text("Weather Windows", size="sm", fw=600, c="white"),
            dmc.Badge(status_text, color=status_color, variant="light", size="sm"),
            dmc.Badge(f"{model_key} · P90 wind / P100 gust", color="gray", variant="light", size="sm"),
        ]),
        dmc.Text(
            f"Wind < {wind_thresh} kn, Gusts < {gust_thresh} kn, Waves < {wave_thresh} m",
            size="xs", c="dimmed",
        ),
        dmc.Text(
            f"{len(windows)} window(s) found — {total_hours}h total optimal time",
            size="xs", c="white",
        ),
    ]

    if next_window:
        start, end = next_window
        fmt = "%a %d %b %H:%M"
        label = "Current window" if is_open else "Next window"
        children.append(
            dmc.Text(
                f"{label}: {start.strftime(fmt)} — {end.strftime(fmt)}",
                size="xs", fw=600, c="#22c55e",
            )
        )

    return dmc.Paper(
        shadow="sm", p="sm", radius="md",
        style={"backgroundColor": "#111827", "border": "1px solid #22c55e"},
        children=dmc.Stack(gap=4, children=children),
    )


def _timing_shift_card(timing):
    """Build a stat card showing forecast timing shift between model runs."""
    parts = timing.get("summary_parts", [])
    if not parts or parts == ["No significant timing changes"]:
        return dmc.Paper(
            shadow="sm", p="sm", radius="md",
            style={"backgroundColor": "#111827", "border": "1px solid #1e293b"},
            children=dmc.Stack(gap=2, children=[
                dmc.Group(gap="sm", children=[
                    DashIconify(icon="tabler:clock-shift-left", color="#6b7280", width=20),
                    dmc.Text("Timing Shift", size="sm", fw=600, c="white"),
                ]),
                dmc.Text("No significant timing changes between runs", size="xs", c="dimmed"),
            ]),
        )

    children = [
        dmc.Group(gap="sm", children=[
            DashIconify(icon="tabler:clock-shift-left", color="#f59e0b", width=20),
            dmc.Text("Timing Shift (vs Previous Run)", size="sm", fw=600, c="white"),
        ]),
    ]
    for part in parts:
        if "earlier" in part:
            c = "#ef4444"
        elif "later" in part:
            c = "#22c55e"
        else:
            c = "#f59e0b"
        children.append(dmc.Text(part, size="xs", fw=600, c=c))

    return dmc.Paper(
        shadow="sm", p="sm", radius="md",
        style={"backgroundColor": "#111827", "border": "1px solid #f59e0b"},
        children=dmc.Stack(gap=2, children=children),
    )


# ═════════════════════════════════════════════════════════════════════════════
# PostHog analytics — clientside event capture
# ═════════════════════════════════════════════════════════════════════════════
# Each callback writes to dcc.Store('posthog-sink') as a throwaway Output (Dash
# requires one). `window.posthog` is only present when POSTHOG_PROJECT_API_KEY
# is set in app.py; when it's absent, all capture() calls are no-ops, so these
# callbacks are safe to wire unconditionally. Event taxonomy is documented in
# MEMORY.md (project_marine_risk_trial.md).

_THRESHOLD_DEBOUNCE_MS = 600


# site_changed
clientside_callback(
    """
    function(site) {
        if (site && window.posthog) {
            window.posthog.capture('site_changed', {site_name: site});
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("posthog-sink", "data", allow_duplicate=True),
    Input("marine-site-select", "value"),
    prevent_initial_call=True,
)

# threshold_adjusted — one debounced callback per slider because Mantine
# sliders fire `value` on every drag frame, which would flood PostHog.
for _slider_id, _threshold_type, _unit in [
    ("marine-ww-wind-thresh", "wind", "kn"),
    ("marine-ww-gust-thresh", "gust", "kn"),
    ("marine-ww-wave-thresh", "wave", "m"),
]:
    clientside_callback(
        f"""
        function(v) {{
            if (v == null) return window.dash_clientside.no_update;
            if (!window._phThrottle) window._phThrottle = {{}};
            clearTimeout(window._phThrottle["{_threshold_type}"]);
            window._phThrottle["{_threshold_type}"] = setTimeout(function() {{
                if (window.posthog) {{
                    window.posthog.capture("threshold_adjusted", {{
                        threshold_type: "{_threshold_type}",
                        value: v,
                        unit: "{_unit}"
                    }});
                }}
            }}, {_THRESHOLD_DEBOUNCE_MS});
            return window.dash_clientside.no_update;
        }}
        """,
        Output("posthog-sink", "data", allow_duplicate=True),
        Input(_slider_id, "value"),
        prevent_initial_call=True,
    )

# weather_window_toggled (switch on/off)
clientside_callback(
    """
    function(checked) {
        if (window.posthog) {
            window.posthog.capture('weather_window_toggled', {enabled: !!checked});
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("posthog-sink", "data", allow_duplicate=True),
    Input("marine-weather-window-toggle", "checked"),
    prevent_initial_call=True,
)

# weather_window_model_toggled (model picker change within the window controls)
clientside_callback(
    """
    function(model) {
        if (model && window.posthog) {
            window.posthog.capture('weather_window_model_toggled', {model: model});
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("posthog-sink", "data", allow_duplicate=True),
    Input("marine-ww-model", "value"),
    prevent_initial_call=True,
)

# chart_tab_opened
clientside_callback(
    """
    function(tab) {
        if (tab && window.posthog) {
            window.posthog.capture('chart_tab_opened', {tab_name: tab});
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("posthog-sink", "data", allow_duplicate=True),
    Input("marine-tabs", "value"),
    prevent_initial_call=True,
)

# timezone_changed
clientside_callback(
    """
    function(tz) {
        if (tz && window.posthog) {
            window.posthog.capture('timezone_changed', {timezone: tz});
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("posthog-sink", "data", allow_duplicate=True),
    Input("marine-timezone-select", "value"),
    prevent_initial_call=True,
)

# forecast_refreshed
clientside_callback(
    """
    function(n) {
        if (n && window.posthog) {
            window.posthog.capture('forecast_refreshed', {click_count: n});
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("posthog-sink", "data", allow_duplicate=True),
    Input("marine-refresh-btn", "n_clicks"),
    prevent_initial_call=True,
)
