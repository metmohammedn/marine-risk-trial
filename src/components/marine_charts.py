"""
Marine Risk chart factories — wind exceedance, ensemble spread, gusts, waves.
Ports all Plotly chart logic from wwr-interactive/app_v26.py.
"""
import logging
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objs as go

from src.utils.constants import (
    MARINE_WIND_MODELS,
    MARINE_AI_WIND_MODELS,
    MARINE_ALL_WIND_MODELS,
    PLOTLY_LAYOUT_DEFAULTS,
    BUOY_OBS_TRACE_COLOR,
    BUOY_OBS_TRACE_LINE_WIDTH,
    BUOY_OBS_TRACE_MARKER_SIZE,
    BUOY_OBS_TRACE_MARKER_SYMBOL,
    BUOY_OBS_TRACE_NAME,
    WEATHER_WINDOW_COLOR,
    PREV_RUN_TRACE_COLOR,
    PREV_RUN_TRACE_DASH,
    PREV_RUN_TRACE_WIDTH,
    PREV_RUN_TRACE_OPACITY,
)

logger = logging.getLogger(__name__)


def _base(**overrides) -> dict:
    layout = dict(PLOTLY_LAYOUT_DEFAULTS)
    layout.update(overrides)
    return layout


def _empty(msg: str, height: int = 450) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_base(
        height=height,
        annotations=[{
            "text": msg, "xref": "paper", "yref": "paper",
            "x": 0.5, "y": 0.5, "showarrow": False,
            "font": {"size": 16, "color": "#64748b"},
        }],
        xaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False),
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# WEATHER WINDOW + PREVIOUS RUN OVERLAY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def add_weather_windows(fig: go.Figure, windows: List) -> None:
    """Add green vertical bands to a figure for each optimal weather window."""
    for i, (start, end) in enumerate(windows):
        if i == 0:
            fig.add_vrect(
                x0=start, x1=end,
                fillcolor=WEATHER_WINDOW_COLOR,
                layer="below",
                line_width=0,
                annotation_text="⛵ Optimal",
                annotation_position="top left",
                annotation=dict(font_size=14, font_color="#22c55e", font_family="Arial"),
            )
        else:
            # Explicitly set annotation_text="" to suppress Plotly's
            # default "new text" placeholder annotation.
            fig.add_vrect(
                x0=start, x1=end,
                fillcolor=WEATHER_WINDOW_COLOR,
                layer="below",
                line_width=0,
                annotation_text="",
            )


def add_previous_run_trace(
    fig: go.Figure,
    prev_series: pd.Series,
    label: str,
) -> None:
    """Overlay a previous model run as a gray dashed line."""
    if prev_series is None or prev_series.empty:
        return
    fig.add_trace(go.Scatter(
        x=prev_series.index,
        y=prev_series.values,
        mode="lines",
        name=f"Previous Run — {label}",
        line=dict(
            color=PREV_RUN_TRACE_COLOR,
            width=PREV_RUN_TRACE_WIDTH,
            dash=PREV_RUN_TRACE_DASH,
        ),
        opacity=PREV_RUN_TRACE_OPACITY,
        hovertemplate=f"<b>Prev {label}</b><br>%{{y:.1f}}<br>%{{x}}<extra></extra>",
    ))


# ─────────────────────────────────────────────────────────────────────────────
# 1. WIND EXCEEDANCE PROBABILITY CHART
# ─────────────────────────────────────────────────────────────────────────────

def create_wind_exceedance_chart(
    exceedance_data: Dict[str, pd.Series],
    wind_threshold: float,
    site_name: str,
    agreement: Optional[Dict] = None,
) -> go.Figure:
    """
    Multi-model wind exceedance probability chart with risk zones.
    exceedance_data: {model_name: pd.Series of % probabilities}
    """
    fig = go.Figure()

    if not exceedance_data:
        return _empty("No wind exceedance data")

    # Dash styles for each model. Physics models = solid/dot/dash; AI models =
    # long-dash so they read as a different category at a glance.
    line_styles = {
        "ECMWF IFS": {"color": "#d62728", "width": 3, "dash": None},
        "GFS": {"color": "#1f77b4", "width": 3, "dash": "dot"},
        "ICON": {"color": "#2ca02c", "width": 3, "dash": "dash"},
        "ECMWF AIFS Ens": {"color": "#9467bd", "width": 3, "dash": "longdash"},
        "AIGFS Ens": {"color": "#17becf", "width": 3, "dash": "longdash"},
    }

    for model_name, probs in exceedance_data.items():
        if probs.empty:
            continue
        style = line_styles.get(model_name, {"color": "#888", "width": 2, "dash": None})
        members = MARINE_ALL_WIND_MODELS.get(model_name, {}).get("members", "?")
        fig.add_trace(go.Scatter(
            x=probs.index, y=probs.values,
            mode="lines",
            name=f"{model_name} ({members} Members)",
            line=dict(**style),
            fill="tozeroy" if model_name == "ECMWF IFS" else None,
            fillcolor="rgba(214, 39, 40, 0.08)" if model_name == "ECMWF IFS" else None,
            hovertemplate=f"<b>{model_name}</b><br>Risk: %{{y:.1f}}%<br>%{{x}}<extra></extra>",
        ))

    # Risk zone bands
    fig.add_hrect(y0=0, y1=10, fillcolor="green", opacity=0.07,
                  annotation_text="Low Risk", annotation_position="right",
                  annotation=dict(font_size=10, font_color="#22c55e"))
    fig.add_hrect(y0=10, y1=30, fillcolor="yellow", opacity=0.07,
                  annotation_text="Moderate", annotation_position="right",
                  annotation=dict(font_size=10, font_color="#f59e0b"))
    fig.add_hrect(y0=30, y1=100, fillcolor="red", opacity=0.07,
                  annotation_text="High Risk", annotation_position="right",
                  annotation=dict(font_size=10, font_color="#ef4444"))

    # Model agreement badge
    if agreement and agreement.get("score") is not None:
        color_map = {"green": "green", "blue": "#3b82f6", "orange": "orange", "red": "red"}
        border = color_map.get(agreement["color"], "gray")
        fig.add_annotation(
            text=f"Model Agreement: {agreement['score']:.0f}% ({agreement['level']})",
            xref="paper", yref="paper", x=0.02, y=0.97,
            showarrow=False,
            bgcolor="rgba(17, 24, 39, 0.9)",
            bordercolor=border, borderwidth=2, borderpad=8,
            font=dict(size=11, color="#f1f5f9"),
        )

    fig.update_layout(**_base(
        title=f"Multi-Model Risk Probability: Wind > {wind_threshold} kn — {site_name}",
        yaxis_title="Probability (%)",
        xaxis_title="Date & Time",
        yaxis=dict(range=[0, 105]),
        height=450,
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 2. ENSEMBLE SPREAD CHART (per-model)
# ─────────────────────────────────────────────────────────────────────────────

def create_ensemble_spread_chart(
    stats_df: pd.DataFrame,
    wind_threshold: float,
    model_name: str,
    color_main: str,
    color_fill: str,
    member_count: int,
    weather_windows: Optional[List] = None,
) -> go.Figure:
    """Wind forecast chart. Ensemble spread (10th-90th band + median) for
    multi-member models; single line labelled as a deterministic forecast
    when `member_count == 1` (ACCESS-G)."""
    fig = go.Figure()

    if stats_df.empty:
        return _empty(f"No ensemble data for {model_name}")

    is_deterministic = member_count == 1

    # 10-90% uncertainty band — only meaningful for true ensembles.
    if not is_deterministic:
        fig.add_trace(go.Scatter(
            x=stats_df.index.tolist() + stats_df.index[::-1].tolist(),
            y=stats_df["p90"].tolist() + stats_df["p10"][::-1].tolist(),
            fill="toself",
            fillcolor=color_fill,
            line=dict(color="rgba(255,255,255,0)"),
            name="10th–90th Percentile",
            hoverinfo="skip",
        ))

    # Forecast line — label drops the "Median" qualifier for deterministic.
    line_label = "Forecast" if is_deterministic else "Median Forecast"
    hover_label = "Forecast" if is_deterministic else "Median"
    fig.add_trace(go.Scatter(
        x=stats_df.index, y=stats_df["median"],
        line=dict(color=color_main, width=3),
        name=line_label,
        hovertemplate=f"<b>{hover_label}</b><br>Wind: %{{y:.1f}} kn<br>%{{x}}<extra></extra>",
    ))

    # Threshold line
    fig.add_hline(
        y=wind_threshold, line_dash="dash", line_color="#ef4444",
        annotation_text=f"Threshold: {wind_threshold} kn",
        annotation_position="top right",
        annotation=dict(font_color="#ef4444"),
    )

    if weather_windows:
        add_weather_windows(fig, weather_windows)

    title = (
        f"{model_name} — Deterministic Forecast"
        if is_deterministic
        else f"{model_name} Ensemble Spread ({member_count} Members)"
    )
    fig.update_layout(**_base(
        title=title,
        yaxis_title="Wind Speed (knots)",
        xaxis_title="Date & Time",
        yaxis=dict(rangemode="tozero"),
        height=400,
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 3. GUST COMPARISON CHART
# ─────────────────────────────────────────────────────────────────────────────

def create_gust_chart(
    gust_data: Dict[str, Optional[pd.DataFrame]],
    gust_threshold: float,
    site_name: str,
    weather_windows: Optional[List] = None,
) -> go.Figure:
    """Multi-model wind gust forecast comparison."""
    fig = go.Figure()

    styles = {
        "ECMWF IFS": {"color": "#d62728", "main_width": 3, "dotted_width": 2},
        "GFS": {"color": "#1f77b4", "main_width": 3, "dotted_width": 2},
        "ICON": {"color": "#2ca02c", "main_width": 3, "dotted_width": 2},
    }
    has_data = False

    for model_name, stats in gust_data.items():
        if stats is None or stats.empty:
            continue
        # AI ensemble models (AIFS, AIGFS) don't predict gusts — hide them on
        # this chart even if the upstream service hands us an empty stats frame.
        if not MARINE_ALL_WIND_MODELS.get(model_name, {}).get("supports_gusts", True):
            continue
        has_data = True
        s = styles.get(model_name, {"color": "#888", "main_width": 2, "dotted_width": 2})

        # Max gust line (dotted)
        if "max" in stats.columns:
            fig.add_trace(go.Scatter(
                x=stats.index, y=stats["max"],
                mode="lines",
                name=f"{model_name} Max Gust",
                line=dict(color=s["color"], width=s["dotted_width"], dash="dot"),
                hovertemplate=f"<b>{model_name} Max</b><br>Gust: %{{y:.1f}} kn<br>%{{x}}<extra></extra>",
            ))

        # Median gust line (solid)
        fig.add_trace(go.Scatter(
            x=stats.index, y=stats["median"],
            mode="lines",
            name=f"{model_name} Median Gust",
            line=dict(color=s["color"], width=s["main_width"]),
            hovertemplate=f"<b>{model_name} Median</b><br>Gust: %{{y:.1f}} kn<br>%{{x}}<extra></extra>",
        ))

    if not has_data:
        return _empty("No gust data available")

    # Threshold + Beaufort reference lines
    fig.add_hline(
        y=gust_threshold, line_dash="dash", line_color="#ef4444",
        annotation_text=f"Threshold: {gust_threshold} kn",
        annotation_position="top right",
    )
    fig.add_hline(
        y=34, line_dash="dot", line_color="#f59e0b",
        annotation_text="Gale (34 kn)", annotation_position="bottom right",
        annotation=dict(font_size=10),
    )
    fig.add_hline(
        y=48, line_dash="dot", line_color="#ef4444",
        annotation_text="Storm (48 kn)", annotation_position="bottom right",
        annotation=dict(font_size=10),
    )

    if weather_windows:
        add_weather_windows(fig, weather_windows)

    fig.update_layout(**_base(
        title=f"Multi-Model Wind Gust Forecast — {site_name}",
        yaxis_title="Wind Gusts (knots)",
        xaxis_title="Date & Time",
        yaxis=dict(rangemode="tozero"),
        height=450,
        # Push the legend a touch further below the x-axis label so it isn't
        # crowding the "Date & Time" caption.
        legend=dict(
            orientation="h", yanchor="top", y=-0.38,
            xanchor="left", x=0, font=dict(size=10),
        ),
        margin=dict(l=50, r=30, t=40, b=95),
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# BUOY OBSERVATION OVERLAY HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _add_buoy_obs_trace(
    fig: go.Figure,
    buoy_df: Optional[pd.DataFrame],
    variable: str,
    buoy_meta: Optional[Dict] = None,
) -> None:
    """
    Overlay buoy observation data on a marine chart.

    Styling: Cyan line with triangle-up markers — visually distinct from
    forecast model traces (red/blue/green).
    """
    if buoy_df is None or buoy_df.empty or variable not in buoy_df.columns:
        return

    obs_data = buoy_df[variable].dropna()
    if obs_data.empty:
        return

    buoy_name = (buoy_meta or {}).get("name", "Buoy")
    distance = (buoy_meta or {}).get("distance_km", 0)
    label = f"{BUOY_OBS_TRACE_NAME} ({buoy_name}, {distance:.0f} km)"

    fig.add_trace(go.Scatter(
        x=obs_data.index,
        y=obs_data.values,
        mode="lines+markers",
        name=label,
        line=dict(
            color=BUOY_OBS_TRACE_COLOR,
            width=BUOY_OBS_TRACE_LINE_WIDTH,
        ),
        marker=dict(
            size=BUOY_OBS_TRACE_MARKER_SIZE,
            symbol=BUOY_OBS_TRACE_MARKER_SYMBOL,
            color=BUOY_OBS_TRACE_COLOR,
        ),
        hovertemplate=(
            f"<b>{buoy_name}</b><br>"
            f"Observed: %{{y:.2f}}<br>"
            f"%{{x}}<extra>Buoy ({distance:.0f} km)</extra>"
        ),
    ))


# ─────────────────────────────────────────────────────────────────────────────
# 4. WAVE HEIGHT CHART
# ─────────────────────────────────────────────────────────────────────────────

def create_wave_chart(
    wave_data: Dict[str, pd.DataFrame],
    wave_threshold: float,
    site_name: str,
    buoy_df: Optional[pd.DataFrame] = None,
    buoy_meta: Optional[Dict] = None,
    weather_windows: Optional[List] = None,
) -> go.Figure:
    """Multi-model wave height forecast comparison with optional buoy overlay."""
    fig = go.Figure()

    styles = {
        "ECMWF": {"color": "#d62728", "width": 3, "dash": None},
        "GFS": {"color": "#1f77b4", "width": 3, "dash": "dot"},
        "ICON": {"color": "#2ca02c", "width": 3, "dash": "dash"},
    }
    has_data = False

    for model_name, df in wave_data.items():
        if df.empty:
            continue
        if "wave_height" not in df.columns:
            wave_cols = [c for c in df.columns if "wave_height" in c]
            if not wave_cols:
                continue
            wave_series = df[wave_cols].median(axis=1)
        else:
            wave_series = df["wave_height"]

        has_data = True
        s = styles.get(model_name, {"color": "#888", "width": 2, "dash": None})
        fig.add_trace(go.Scatter(
            x=df.index, y=wave_series,
            mode="lines",
            name=f"{model_name}",
            line=dict(color=s["color"], width=s["width"], dash=s["dash"]),
            hovertemplate=f"<b>{model_name}</b><br>Wave: %{{y:.2f}} m<br>%{{x}}<extra></extra>",
        ))

    # Overlay buoy observations (cyan trace)
    _add_buoy_obs_trace(fig, buoy_df, "wave_height", buoy_meta)

    if not has_data and (buoy_df is None or buoy_df.empty):
        return _empty("No wave data available")

    fig.add_hline(
        y=wave_threshold, line_dash="dash", line_color="#ef4444",
        annotation_text=f"Threshold: {wave_threshold} m",
        annotation_position="top right",
        annotation=dict(font_color="#ef4444"),
    )

    if weather_windows:
        add_weather_windows(fig, weather_windows)

    fig.update_layout(**_base(
        title=f"Multi-Model Wave Height Forecast — {site_name}",
        yaxis_title="Significant Wave Height (m)",
        xaxis_title="Date & Time",
        yaxis=dict(rangemode="tozero"),
        height=450,
        margin=dict(r=80),
    ))
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 5. WAVE PERIOD CHART
# ─────────────────────────────────────────────────────────────────────────────

def create_wave_period_chart(
    wave_data: Dict[str, pd.DataFrame],
    site_name: str,
    buoy_df: Optional[pd.DataFrame] = None,
    buoy_meta: Optional[Dict] = None,
) -> go.Figure:
    """Wave period comparison with sea-state classification zones and optional buoy overlay."""
    fig = go.Figure()

    styles = {
        "ECMWF": {"color": "#d62728", "width": 3, "dash": None},
        "GFS": {"color": "#1f77b4", "width": 3, "dash": "dot"},
        "ICON": {"color": "#2ca02c", "width": 3, "dash": "dash"},
    }
    has_data = False

    for model_name, df in wave_data.items():
        if df.empty or "wave_period" not in df.columns:
            continue
        has_data = True
        s = styles.get(model_name, {"color": "#888", "width": 2, "dash": None})
        fig.add_trace(go.Scatter(
            x=df.index, y=df["wave_period"],
            mode="lines",
            name=model_name,
            line=dict(color=s["color"], width=s["width"], dash=s["dash"]),
            hovertemplate=f"<b>{model_name}</b><br>Period: %{{y:.1f}} s<br>%{{x}}<extra></extra>",
        ))

    # Overlay buoy observations (cyan trace)
    _add_buoy_obs_trace(fig, buoy_df, "wave_period", buoy_meta)

    if not has_data and (buoy_df is None or buoy_df.empty):
        return _empty("No wave period data available", height=400)

    # Sea-state classification zones
    fig.add_hrect(y0=0, y1=6, fillcolor="orange", opacity=0.06,
                  annotation_text="Short Period (Choppy)", annotation_position="left",
                  annotation=dict(font_size=10, font_color="#f59e0b"))
    fig.add_hrect(y0=6, y1=10, fillcolor="blue", opacity=0.06,
                  annotation_text="Medium Period", annotation_position="left",
                  annotation=dict(font_size=10, font_color="#3b82f6"))
    fig.add_hrect(y0=10, y1=20, fillcolor="green", opacity=0.06,
                  annotation_text="Long Period (Swell)", annotation_position="left",
                  annotation=dict(font_size=10, font_color="#22c55e"))

    fig.update_layout(**_base(
        title=f"Wave Period Forecast — {site_name}",
        yaxis_title="Wave Period (seconds)",
        xaxis_title="Date & Time",
        yaxis=dict(rangemode="tozero"),
        height=400,
        margin=dict(l=120, r=30, t=40, b=40),
    ))
    return fig
