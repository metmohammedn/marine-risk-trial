"""
Marine Risk service — wind ensemble + wave forecast data, exceedance analysis,
model agreement scoring, weather windows, timing shift.
Ports logic from wwr-interactive/app_v26.py.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.constants import (
    MARINE_SITES,
    TRIAL_CITIES,
    IMOS_WAVE_BUOYS,
    MARINE_WIND_MODELS,
    MARINE_AI_WIND_MODELS,
    MARINE_BOM_WIND_MODELS,
    MARINE_ALL_WIND_MODELS,
    MARINE_WAVE_MODELS,
    KMH_TO_KNOTS,
    MS_TO_KNOTS,
    DEFAULT_WIND_THRESHOLD_KN,
    DEFAULT_WAVE_THRESHOLD_M,
    DEFAULT_GUST_THRESHOLD_KN,
    DEFAULT_WEATHER_WINDOW_WIND_KN,
    DEFAULT_WEATHER_WINDOW_GUST_KN,
    DEFAULT_WEATHER_WINDOW_WAVE_M,
    DEFAULT_WEATHER_WINDOW_MODEL,
    DEFAULT_WEATHER_WINDOW_TIME_BLOCK,
    WEATHER_WINDOW_WIND_PERCENTILE,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# API / cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_client():
    try:
        from src.data.api_client import get_api_client
        return get_api_client()
    except RuntimeError:
        return None


def _get_cache():
    try:
        from src.data.cache import get_cache
        cache = get_cache()
        return cache if cache.is_available else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Site helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_marine_site_options() -> List[Dict[str, str]]:
    return [{"label": name, "value": name} for name in MARINE_SITES.keys()]


def get_marine_coords(site_name: str) -> Tuple[float, float]:
    site = MARINE_SITES.get(site_name)
    if not site:
        raise ValueError(f"Marine site not found: {site_name}")
    return site["lat"], site["lon"]


def _resolve_buoy_name(site_name: str) -> Optional[str]:
    """Strip 'buoy:' prefix and return the buoy name if valid, else None."""
    if site_name and site_name.startswith("buoy:"):
        name = site_name[5:]
        if name in IMOS_WAVE_BUOYS:
            return name
    return None


def is_marine_site(site_name: str) -> bool:
    """Check whether a site is an offshore marine site or wave buoy."""
    return site_name in MARINE_SITES or _resolve_buoy_name(site_name) is not None


def is_buoy_site(site_name: str) -> bool:
    """Check whether a site is an IMOS wave buoy station."""
    return _resolve_buoy_name(site_name) is not None


def _get_land_site_coords(site_name: str) -> Tuple[float, float]:
    """Look up land station coords from the hardwired TRIAL_CITIES dict."""
    city = TRIAL_CITIES.get(site_name)
    if not city:
        raise ValueError(f"Land site not found: {site_name}")
    return city["lat"], city["lon"]


def get_combined_site_coords(site_name: str) -> Tuple[float, float]:
    """Return (lat, lon) for a marine platform, wave buoy, or land site."""
    if site_name in MARINE_SITES:
        return MARINE_SITES[site_name]["lat"], MARINE_SITES[site_name]["lon"]
    buoy_name = _resolve_buoy_name(site_name)
    if buoy_name:
        return IMOS_WAVE_BUOYS[buoy_name]["lat"], IMOS_WAVE_BUOYS[buoy_name]["lon"]
    return _get_land_site_coords(site_name)


# ─────────────────────────────────────────────────────────────────────────────
# Wind ensemble fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_wind_ensemble(
    lat: float, lon: float, model_key: str = "ECMWF IFS"
) -> Dict[str, Any]:
    """
    Fetch wind ensemble forecast for a single model.
    Returns dict with 'df' (wind_speed + gust members in knots),
    'generation_time', 'first_forecast_time'.
    """
    model_config = MARINE_ALL_WIND_MODELS.get(model_key)
    if not model_config:
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    if model_config.get("provider") == "bom":
        return _fetch_bom_wind_ensemble_sync(lat, lon, model_key, model_config)

    api_model = model_config["api_model"]
    cache_key = f"marine:wind:{lat:.4f}:{lon:.4f}:{api_model}"
    cache = _get_cache()
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    client = _get_client()
    if client is None:
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    start = time.time()
    try:
        result = client.get_wind_ensemble_forecast(lat, lon, model=api_model)
        elapsed = time.time() - start
        df = result.get("df", pd.DataFrame())
        logger.info(
            "Wind ensemble (%s) fetched in %.2fs — %d rows, %d cols",
            model_key, elapsed, len(df), len(df.columns),
        )
        if cache and not df.empty:
            cache.set(cache_key, result, ttl_seconds=3600)
        return result
    except Exception as e:
        logger.error("Wind ensemble fetch failed (%s): %s", model_key, e)
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}


def fetch_all_wind_ensembles(
    lat: float, lon: float
) -> Dict[str, Dict[str, Any]]:
    """Fetch wind ensembles for all marine wind models (physics + AI)."""
    results = {}
    for model_key in MARINE_ALL_WIND_MODELS:
        results[model_key] = fetch_wind_ensemble(lat, lon, model_key)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Wave forecast fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_wave_forecast(
    lat: float, lon: float, model_name: str = "best_match"
) -> pd.DataFrame:
    """Fetch deterministic wave height + wave period forecast."""
    cache_key = f"marine:wave:{lat:.4f}:{lon:.4f}:{model_name}"
    cache = _get_cache()
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    client = _get_client()
    if client is None:
        return pd.DataFrame()

    try:
        df = client.get_wave_forecast(lat, lon, model=model_name)
        if cache and not df.empty:
            cache.set(cache_key, df, ttl_seconds=3600)
        return df
    except Exception as e:
        logger.error("Wave forecast fetch failed (%s): %s", model_name, e)
        return pd.DataFrame()


def fetch_all_wave_forecasts(lat: float, lon: float) -> Dict[str, pd.DataFrame]:
    """Fetch wave forecasts from ECMWF WAM, NCEP GFS Wave, and DWD GWAM."""
    results = {}
    for name, cfg in MARINE_WAVE_MODELS.items():
        results[name] = fetch_wave_forecast(lat, lon, cfg["api_model"])
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Data processing: exceedance probability
# ─────────────────────────────────────────────────────────────────────────────

def calculate_wind_exceedance(df: pd.DataFrame, threshold: float) -> pd.Series:
    """
    Calculate % of wind ensemble members exceeding threshold at each timestep.
    Returns Series of probabilities (0-100).
    """
    wind_cols = [c for c in df.columns if "wind_speed_10m" in c and "member" in c]
    if not wind_cols:
        return pd.Series(dtype=float)
    exceedance = (df[wind_cols] > threshold).sum(axis=1) / len(wind_cols) * 100
    return exceedance


def calculate_ensemble_stats(df: pd.DataFrame, variable: str = "wind_speed_10m") -> pd.DataFrame:
    """
    Calculate median, p10, p90 from ensemble members.
    Returns DataFrame with 'median', 'p10', 'p90' columns.
    """
    member_cols = [c for c in df.columns if variable in c and "member" in c]
    if not member_cols:
        return pd.DataFrame()

    stats = pd.DataFrame(index=df.index)
    member_data = df[member_cols]
    stats["median"] = member_data.median(axis=1, skipna=True)
    stats["p10"] = member_data.quantile(0.10, axis=1)
    stats["p90"] = member_data.quantile(0.90, axis=1)
    return stats


def get_gust_stats(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Calculate max, median, p90 gust from ensemble members.

    Returns None when there are no gust member columns OR when every value
    is null — the latter is the case for AI models (AIFS, AIGFS) which return
    the gust columns as a courtesy but with no actual data.
    """
    gust_cols = [c for c in df.columns if "wind_gusts_10m" in c and "member" in c]
    if not gust_cols:
        return None
    member_data = df[gust_cols]
    if member_data.isna().all().all():
        return None
    stats = pd.DataFrame(index=df.index)
    stats["max"] = member_data.max(axis=1, skipna=True)
    stats["median"] = member_data.median(axis=1, skipna=True)
    stats["p90"] = member_data.quantile(0.90, axis=1)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Risk analysis helpers (ported from app_v26.py)
# ─────────────────────────────────────────────────────────────────────────────

def analyze_wind_exceedance(
    df: pd.DataFrame, threshold: float, window_hours: int, model_name: str
) -> Optional[Dict]:
    """Analyze wind speed exceedance for a model within a time window."""
    if df.empty:
        return None

    cutoff = df.index[0] + pd.Timedelta(hours=window_hours)
    df_w = df[df.index <= cutoff]
    if df_w.empty:
        return None

    wind_cols = [c for c in df_w.columns if "wind_speed_10m" in c and "member" in c]
    if not wind_cols:
        return None

    median_wind = df_w[wind_cols].median(axis=1, skipna=True)
    peak_median = float(median_wind.max())
    exceedance_pct = float((df_w[wind_cols] > threshold).mean(axis=0).mean() * 100)

    first_exceed_mask = median_wind > threshold
    first_time = None
    duration_hours = 0
    if first_exceed_mask.any():
        first_time = median_wind[first_exceed_mask].index[0]
        duration_hours = int(first_exceed_mask.sum())

    return {
        "model": model_name,
        "peak_median": peak_median,
        "exceedance_pct": exceedance_pct,
        "first_time": first_time,
        "duration_hours": duration_hours,
        "exceeds": peak_median > threshold,
    }


def analyze_gust_exceedance(
    df: pd.DataFrame, threshold: float, window_hours: int, model_name: str
) -> Optional[Dict]:
    """Analyze wind gust exceedance for a model."""
    if df.empty:
        return None
    cutoff = df.index[0] + pd.Timedelta(hours=window_hours)
    df_w = df[df.index <= cutoff]
    if df_w.empty:
        return None

    gust_cols = [c for c in df_w.columns if "wind_gusts_10m" in c and "member" in c]
    if not gust_cols:
        return None
    if df_w[gust_cols].isna().all().all():
        # AI models return gust columns full of nulls — treat as no data.
        return None

    peak_gust = float(df_w[gust_cols].max(axis=1, skipna=True).max())
    median_gust = float(df_w[gust_cols].median(axis=1, skipna=True).max())
    exceedance_pct = float((df_w[gust_cols] > threshold).mean(axis=0).mean() * 100)

    peak_series = df_w[gust_cols].max(axis=1, skipna=True)
    first_mask = peak_series > threshold
    first_time = None
    duration = 0
    if first_mask.any():
        first_time = peak_series[first_mask].index[0]
        duration = int(first_mask.sum())

    return {
        "model": model_name,
        "peak_gust": peak_gust,
        "median_gust": median_gust,
        "exceedance_pct": exceedance_pct,
        "first_time": first_time,
        "duration_hours": duration,
        "exceeds": median_gust > threshold or peak_gust > threshold,
    }


def analyze_wave_exceedance(
    df: pd.DataFrame, threshold: float, window_hours: int, model_name: str
) -> Optional[Dict]:
    """Analyze wave height exceedance."""
    if df.empty:
        return None
    cutoff = df.index[0] + pd.Timedelta(hours=window_hours)
    df_w = df[df.index <= cutoff]
    if df_w.empty:
        return None

    if "wave_height" in df_w.columns:
        wave_series = df_w["wave_height"]
    else:
        wave_cols = [c for c in df_w.columns if "wave_height" in c]
        if not wave_cols:
            return None
        wave_series = df_w[wave_cols].median(axis=1, skipna=True)

    peak_wave = float(wave_series.max())
    first_mask = wave_series > threshold
    first_time = None
    duration = 0
    if first_mask.any():
        first_time = wave_series[first_mask].index[0]
        duration = int(first_mask.sum())

    return {
        "model": model_name,
        "peak_wave": peak_wave,
        "exceedance_amount": max(0, peak_wave - threshold),
        "first_time": first_time,
        "duration_hours": duration,
        "exceeds": peak_wave > threshold,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Model agreement scoring (ported from app_v26.py)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_model_agreement(
    wind_data: Dict[str, Dict[str, Any]],
    window_hours: int = 168,
) -> Dict:
    """
    Calculate model agreement score from multiple wind ensembles.
    Returns dict with score, level, color, interpretation.
    """
    model_medians = []
    model_names = []

    for model_name, result in wind_data.items():
        df = result.get("df", pd.DataFrame())
        if df.empty:
            continue
        wind_cols = [c for c in df.columns if "wind_speed_10m" in c and "member" in c]
        if not wind_cols:
            continue

        cutoff = df.index[0] + pd.Timedelta(hours=window_hours)
        df_w = df[df.index <= cutoff]
        median = df_w[wind_cols].median(axis=1, skipna=True)
        model_medians.append(median)
        model_names.append(model_name)

    if len(model_medians) < 2:
        return {
            "score": None, "level": "Insufficient Data",
            "color": "gray", "interpretation": "Need at least 2 models",
        }

    # Find common timestamps
    common_idx = model_medians[0].index
    for s in model_medians[1:]:
        common_idx = common_idx.intersection(s.index)

    if len(common_idx) == 0:
        return {
            "score": None, "level": "Insufficient Data",
            "color": "gray", "interpretation": "No overlapping timestamps",
        }

    spreads = []
    for ts in common_idx:
        vals = [s.loc[ts] for s in model_medians if ts in s.index]
        if len(vals) >= 2:
            spreads.append(float(np.std(vals)))

    if not spreads:
        return {
            "score": None, "level": "Insufficient Data",
            "color": "gray", "interpretation": "Cannot compute spread",
        }

    avg_spread = np.mean(spreads)
    score = max(0, 100 - (avg_spread / 20 * 100))

    if score >= 80:
        level, color = "High Confidence", "green"
        interpretation = "All models forecast similar conditions — reliable forecast"
    elif score >= 60:
        level, color = "Moderate Confidence", "blue"
        interpretation = "Models show reasonable agreement — standard confidence"
    elif score >= 40:
        level, color = "Low Confidence", "orange"
        interpretation = "Models diverge moderately — increased uncertainty"
    else:
        level, color = "Very Low Confidence", "red"
        interpretation = "Models disagree significantly — high uncertainty"

    return {
        "score": score,
        "level": level,
        "color": color,
        "interpretation": interpretation,
        "avg_spread": avg_spread,
        "num_models": len(model_medians),
        "models": model_names,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Weather windows — optimal operating periods
# ─────────────────────────────────────────────────────────────────────────────

def _time_block_mask(index: pd.DatetimeIndex, time_block: str) -> pd.Series:
    """
    Build a boolean Series over `index` that is True only inside the selected
    time-of-day block. Hours are read from the index as-is — the caller is
    expected to have already converted to the user-facing timezone.

    Blocks are defined inclusive of the start hour and exclusive of the end
    hour, matching the dropdown labels in `WEATHER_WINDOW_TIME_BLOCKS`.
    """
    hour = index.hour
    if time_block == "morning":     # 7am – 12pm
        mask = (hour >= 7) & (hour < 12)
    elif time_block == "afternoon": # 12pm – 5pm
        mask = (hour >= 12) & (hour < 17)
    elif time_block == "overnight": # 5pm – 7am (spans midnight)
        mask = (hour >= 17) | (hour < 7)
    else:                           # "all" or unknown → no filter
        mask = np.ones(len(index), dtype=bool)
    return pd.Series(mask, index=index)


def calculate_weather_windows(
    wind_data: Dict[str, Dict[str, Any]],
    wave_df: pd.DataFrame,
    wind_thresh: float = DEFAULT_WEATHER_WINDOW_WIND_KN,
    gust_thresh: float = DEFAULT_WEATHER_WINDOW_GUST_KN,
    wave_thresh: float = DEFAULT_WEATHER_WINDOW_WAVE_M,
    model_key: str = DEFAULT_WEATHER_WINDOW_MODEL,
    time_block: str = DEFAULT_WEATHER_WINDOW_TIME_BLOCK,
) -> Dict:
    """
    Calculate optimal weather windows where all conditions are simultaneously met:
    - P90 wind speed across the selected model's ensemble < wind_thresh
    - Ensemble max gust (P100) < gust_thresh
    - Wave height < wave_thresh (skipped for land stations)
    - Hour-of-day falls inside `time_block` (morning / afternoon / overnight /
      all). The overnight block spans midnight, so a calm run from 5pm Mon to
      7am Tue surfaces as one window and the next night surfaces as another.

    ``model_key`` selects the wind source. Only ensemble models with both wind
    and gust variables are supported: "ECMWF IFS", "GFS", "ACCESS-GE". Waves
    always come from ECMWF WAM regardless of the wind model.

    Returns dict with 'windows' (list of (start, end) tuples), 'total_hours',
    'next_window' (tuple or None), 'is_open_now' (bool), 'model_key' (str),
    'time_block' (str).
    """
    empty = {
        "windows": [], "total_hours": 0, "next_window": None,
        "is_open_now": False, "model_key": model_key, "time_block": time_block,
    }

    model_data = wind_data.get(model_key, {})
    df = model_data.get("df", pd.DataFrame())
    if df.empty:
        # Fall back to the default model if the requested one has no data.
        if model_key != DEFAULT_WEATHER_WINDOW_MODEL:
            fallback = wind_data.get(DEFAULT_WEATHER_WINDOW_MODEL, {}).get("df", pd.DataFrame())
            if not fallback.empty:
                df = fallback
                model_key = DEFAULT_WEATHER_WINDOW_MODEL
                empty["model_key"] = model_key
            else:
                return empty
        else:
            return empty

    wind_cols = [c for c in df.columns if "wind_speed_10m" in c and "member" in c]
    if not wind_cols:
        return empty
    p90_wind = df[wind_cols].quantile(WEATHER_WINDOW_WIND_PERCENTILE, axis=1)

    gust_cols = [c for c in df.columns if "wind_gusts_10m" in c and "member" in c]
    max_gust = df[gust_cols].max(axis=1, skipna=True) if gust_cols else pd.Series(0.0, index=df.index)

    safe_mask = (p90_wind < wind_thresh) & (max_gust < gust_thresh)

    if not wave_df.empty and "wave_height" in wave_df.columns:
        wave_reindexed = wave_df["wave_height"].reindex(df.index, method="nearest", tolerance="2h")
        wave_safe = wave_reindexed.isna() | (wave_reindexed < wave_thresh)
        safe_mask = safe_mask & wave_safe

    safe_mask = safe_mask & _time_block_mask(df.index, time_block)

    windows = _extract_contiguous_windows(safe_mask)
    total_hours = sum(
        int((end - start).total_seconds() / 3600) for start, end in windows
    )

    now = pd.Timestamp.now(tz=df.index.tz) if df.index.tz else pd.Timestamp.now()
    is_open_now = False
    next_window = None

    for start, end in windows:
        if start <= now <= end:
            is_open_now = True
            next_window = (start, end)
            break
        if start > now and next_window is None:
            next_window = (start, end)

    return {
        "windows": windows,
        "total_hours": total_hours,
        "next_window": next_window,
        "is_open_now": is_open_now,
        "model_key": model_key,
        "time_block": time_block,
    }


def _extract_contiguous_windows(mask: pd.Series) -> List[Tuple]:
    """Extract contiguous True runs from a boolean Series as (start, end) tuples."""
    if mask.empty or not mask.any():
        return []

    windows = []
    in_window = False
    start = None

    for ts, val in mask.items():
        if val and not in_window:
            start = ts
            in_window = True
        elif not val and in_window:
            windows.append((start, prev_ts))
            in_window = False
        prev_ts = ts

    if in_window:
        windows.append((start, prev_ts))

    return windows


# ─────────────────────────────────────────────────────────────────────────────
# Timing shift — compare current vs previous model run
# ─────────────────────────────────────────────────────────────────────────────

def calculate_onset_timing(
    df: pd.DataFrame, threshold: float, variable_prefix: str = "wind_speed_10m"
) -> Optional[pd.Timestamp]:
    """
    Find the first time the ensemble median exceeds the threshold.
    Returns the timestamp or None if threshold is never exceeded.
    """
    if df.empty:
        return None

    member_cols = [c for c in df.columns if variable_prefix in c and "member" in c]
    if not member_cols:
        # For non-ensemble data (e.g. wave_height), use the column directly
        if variable_prefix in df.columns:
            series = df[variable_prefix]
        else:
            return None
    else:
        series = df[member_cols].median(axis=1, skipna=True)

    exceed_mask = series > threshold
    if not exceed_mask.any():
        return None
    return series[exceed_mask].index[0]


def calculate_gust_onset_timing(
    df: pd.DataFrame, threshold: float,
) -> Optional[pd.Timestamp]:
    """Find the first time max gust across ensemble members exceeds threshold."""
    if df.empty:
        return None
    gust_cols = [c for c in df.columns if "wind_gusts_10m" in c and "member" in c]
    if not gust_cols:
        return None
    max_gust = df[gust_cols].max(axis=1, skipna=True)
    exceed_mask = max_gust > threshold
    if not exceed_mask.any():
        return None
    return max_gust[exceed_mask].index[0]


def calculate_timing_shift(
    current_wind: Dict[str, Dict[str, Any]],
    previous_wind: Dict[str, Dict[str, Any]],
    wind_thresh: float,
    gust_thresh: float,
    wave_thresh: float,
    wave_current: pd.DataFrame,
    wave_previous: pd.DataFrame,
    model_key: str = "ECMWF IFS",
) -> Dict:
    """
    Compare onset timing between current and previous model run.

    Returns dict with delta_hours for wind/gust/wave and a summary string.
    Positive delta = onset moved later (conditions improved).
    Negative delta = onset moved earlier (conditions deteriorated).
    """
    result = {
        "wind_onset_current": None,
        "wind_onset_previous": None,
        "wind_delta_hours": None,
        "gust_onset_current": None,
        "gust_onset_previous": None,
        "gust_delta_hours": None,
        "wave_onset_current": None,
        "wave_onset_previous": None,
        "wave_delta_hours": None,
        "summary_parts": [],
    }

    # Wind onset comparison
    cur_df = current_wind.get(model_key, {}).get("df", pd.DataFrame())
    prev_df = previous_wind.get(model_key, {}).get("df", pd.DataFrame())

    cur_wind_onset = calculate_onset_timing(cur_df, wind_thresh, "wind_speed_10m")
    prev_wind_onset = calculate_onset_timing(prev_df, wind_thresh, "wind_speed_10m")
    result["wind_onset_current"] = cur_wind_onset
    result["wind_onset_previous"] = prev_wind_onset

    if cur_wind_onset and prev_wind_onset:
        delta = (cur_wind_onset - prev_wind_onset).total_seconds() / 3600
        result["wind_delta_hours"] = delta
        direction = "later" if delta > 0 else "earlier"
        result["summary_parts"].append(f"Gale onset {abs(delta):.0f}h {direction}")
    elif cur_wind_onset and not prev_wind_onset:
        result["summary_parts"].append("Gale now forecast (not in previous run)")
    elif not cur_wind_onset and prev_wind_onset:
        result["summary_parts"].append("Gale no longer forecast")

    # Gust onset comparison
    cur_gust_onset = calculate_gust_onset_timing(cur_df, gust_thresh)
    prev_gust_onset = calculate_gust_onset_timing(prev_df, gust_thresh)
    result["gust_onset_current"] = cur_gust_onset
    result["gust_onset_previous"] = prev_gust_onset

    if cur_gust_onset and prev_gust_onset:
        delta = (cur_gust_onset - prev_gust_onset).total_seconds() / 3600
        result["gust_delta_hours"] = delta
        direction = "later" if delta > 0 else "earlier"
        result["summary_parts"].append(f"Gust onset {abs(delta):.0f}h {direction}")

    # Wave onset comparison
    cur_wave_onset = calculate_onset_timing(wave_current, wave_thresh, "wave_height")
    prev_wave_onset = calculate_onset_timing(wave_previous, wave_thresh, "wave_height")
    result["wave_onset_current"] = cur_wave_onset
    result["wave_onset_previous"] = prev_wave_onset

    if cur_wave_onset and prev_wave_onset:
        delta = (cur_wave_onset - prev_wave_onset).total_seconds() / 3600
        result["wave_delta_hours"] = delta
        direction = "later" if delta > 0 else "earlier"
        result["summary_parts"].append(f"Wave > {wave_thresh}m onset {abs(delta):.0f}h {direction}")

    if not result["summary_parts"]:
        result["summary_parts"] = ["No significant timing changes"]

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Async fetch wrappers — concurrent model fetches
# ─────────────────────────────────────────────────────────────────────────────

def _get_async_client():
    try:
        from src.data.api_client import get_api_client
        return get_api_client()
    except RuntimeError:
        return None


async def async_fetch_wind_ensemble(
    lat: float, lon: float, model_key: str = "ECMWF IFS",
    past_days: int = 0,
) -> Dict[str, Any]:
    """Async wind ensemble fetch for a single model with optional past_days.

    Branches on the model's `provider` field: Open-Meteo models go through the
    OpenMeteoClient async path; BoM ACCESS models go through the synchronous
    BomApiClient wrapped in `loop.run_in_executor`.
    """
    model_config = MARINE_ALL_WIND_MODELS.get(model_key)
    if not model_config:
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    provider = model_config.get("provider", "open-meteo")
    if provider == "bom":
        return await _async_fetch_bom_wind_ensemble(lat, lon, model_key, model_config, past_days)

    api_model = model_config["api_model"]
    suffix = ":prev" if past_days > 0 else ""
    cache_key = f"marine:wind{suffix}:{lat:.4f}:{lon:.4f}:{api_model}"
    cache = _get_cache()
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    client = _get_async_client()
    if client is None:
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    start = time.time()
    try:
        result = await client.async_get_wind_ensemble_forecast(
            lat, lon, model=api_model, past_days=past_days,
        )
        elapsed = time.time() - start
        df = result.get("df", pd.DataFrame())
        logger.info(
            "Async wind ensemble (%s, past_days=%d) fetched in %.2fs — %d rows",
            model_key, past_days, elapsed, len(df),
        )
        if cache and not df.empty:
            cache.set(cache_key, result, ttl_seconds=3600)
        return result
    except Exception as e:
        logger.error("Async wind ensemble fetch failed (%s): %s", model_key, e)
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}


# ─────────────────────────────────────────────────────────────────────────────
# BoM ACCESS adapter — fetches via the sync BomApiClient and reshapes the
# response into the wide-format columns the rest of marine_service expects
# (wind_speed_10m_<model>_member_<NN>, wind_gusts_10m_<model>_member_<NN>),
# in knots. Deterministic models (ACCESS-G) are emitted as a single
# pseudo-member so the existing exceedance/median code paths Just Work.
# ─────────────────────────────────────────────────────────────────────────────

def _get_bom_client():
    try:
        from src.data.bom_api_client import get_bom_client
        return get_bom_client()
    except Exception:
        return None


def _get_bom_async_client():
    """Return the native-async BoM client if `beta.py` has initialized it."""
    try:
        from src.data.bom_api_client_async import get_bom_async_client
        return get_bom_async_client()
    except Exception:
        return None


def _reshape_bom_wind_to_marine_format(
    raw_df: pd.DataFrame,
    api_model: str,
    deterministic: bool,
) -> pd.DataFrame:
    """
    Convert the parent BomApiClient `get_point_dataframe` output into the
    wide format expected by marine_service: columns named
        wind_speed_10m_<api_model>_member_<NN>
        wind_gusts_10m_<api_model>_member_<NN>
        wind_direction_10m_<api_model>_member_<NN>
    All wind/gust values are in knots (BoM returns m/s natively).

    For deterministic ACCESS-G the parent client emits columns without the
    `_member_NN` suffix; we synthesise a single member_00 series so the
    downstream member-column filters in this module match it.
    """
    if raw_df is None or raw_df.empty:
        return pd.DataFrame()

    df = raw_df.copy()

    if deterministic:
        rename_map = {}
        for col in df.columns:
            # Parent BomApiClient appends `_<api_model>` to canonical names for
            # deterministic models (e.g. wind_speed_10m_access-g).
            if col.endswith(f"_{api_model}"):
                base = col[: -(len(api_model) + 1)]
                rename_map[col] = f"{base}_{api_model}_member_00"
        if rename_map:
            df = df.rename(columns=rename_map)

    # m/s → knots for wind speed and gust columns (NOT direction)
    for col in df.columns:
        if "wind_speed_10m" in col or "wind_gusts_10m" in col:
            df[col] = pd.to_numeric(df[col], errors="coerce") * MS_TO_KNOTS

    return df


def _upsample_to_hourly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Upsample a DataFrame indexed by an irregular / multi-hourly DatetimeIndex
    onto a contiguous hourly grid via linear time interpolation. Used to
    convert ACCESS-GE's native 3-hourly cadence to the hourly index the rest
    of the pipeline assumes (alongside ECMWF/GFS/ICON).

    Only numeric columns are interpolated — non-numeric coordinate columns
    (e.g. ensemble member labels, time-of-day strings) are dropped, since
    pandas `interpolate(method="time")` raises NotImplementedError on
    datetime-like dtypes.
    """
    if df.empty:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        return df

    # Restrict to numeric columns to avoid datetime/object interpolate errors.
    numeric_df = df.select_dtypes(include=["number"])
    if numeric_df.empty:
        return numeric_df

    # Strip tz for resample, restore after
    tz = numeric_df.index.tz
    if tz is not None:
        numeric_df = numeric_df.tz_convert("UTC").tz_localize(None)
    hourly = numeric_df.resample("1h").interpolate(method="time")
    if tz is not None:
        hourly.index = hourly.index.tz_localize("UTC").tz_convert(tz)
    return hourly


def _fetch_bom_wind_ensemble_sync(
    lat: float, lon: float, model_key: str, model_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Synchronous twin of `_async_fetch_bom_wind_ensemble` — used by the
    legacy sync code paths in marine.py. Same caching, same reshape, same
    upsample."""
    api_model = model_config["api_model"]
    deterministic = model_config.get("deterministic", False)

    cache_key = f"marine:wind:{lat:.4f}:{lon:.4f}:{api_model}"
    cache = _get_cache()
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Prefer the native-async client when registered (beta.py): bridge this
    # sync caller into a fresh event loop so per-model chart callbacks use
    # the same auth/fetch path as the async fan-out. App.py never registers
    # the async client, so it falls through to the sync path verbatim.
    async_bom = _get_bom_async_client()
    start = time.time()
    if async_bom is not None and async_bom.is_available:
        loop = asyncio.new_event_loop()
        try:
            raw_df = loop.run_until_complete(
                async_bom.get_point_dataframe(
                    model=api_model,
                    lons=[lon],
                    lats=[lat],
                    variables=["wind_speed", "wind_direction", "gust"],
                    map_variable_names=True,
                )
            )
        except Exception as e:
            logger.error("BoM wind ensemble fetch failed (%s, sync→async): %s", model_key, e)
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}
        finally:
            loop.close()
    else:
        bom_client = _get_bom_client()
        if bom_client is None or not bom_client.is_available:
            logger.debug("BoM client unavailable — skipping %s", model_key)
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}
        try:
            raw_df = bom_client.get_point_dataframe(
                model=api_model,
                lons=[lon],
                lats=[lat],
                variables=["wind_speed", "wind_direction", "gust"],
                map_variable_names=True,
            )
        except Exception as e:
            logger.error("BoM wind ensemble fetch failed (%s): %s", model_key, e)
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    df = _reshape_bom_wind_to_marine_format(raw_df, api_model, deterministic)
    if not df.empty and not deterministic:
        df = _upsample_to_hourly(df)

    elapsed = time.time() - start
    logger.info(
        "BoM wind ensemble (%s, sync) fetched in %.2fs — %d rows, %d cols",
        model_key, elapsed, len(df), len(df.columns),
    )

    result = {
        "df": df,
        "generation_time": None,
        "first_forecast_time": df.index[0] if not df.empty else None,
    }
    if cache and not df.empty:
        cache.set(cache_key, result, ttl_seconds=3600)
    return result


async def _async_fetch_bom_wind_ensemble(
    lat: float, lon: float, model_key: str, model_config: Dict[str, Any],
    past_days: int = 0,
) -> Dict[str, Any]:
    """
    Fetch a BoM ACCESS wind forecast and adapt it to the marine_service
    wide-format convention. The BoM client is synchronous, so we wrap the
    call in `loop.run_in_executor` to keep the surrounding async fan-out
    fully concurrent (same trick `_fetch_buoy` uses for the IMOS S3 reader).

    Previous-run overlays (past_days > 0) are not yet supported for BoM
    models — the BoM API exposes per-basetime archives but wiring them up
    is a follow-up. We return empty for now so the overlay toggle silently
    skips ACCESS rather than 500ing.
    """
    api_model = model_config["api_model"]
    deterministic = model_config.get("deterministic", False)

    if past_days > 0:
        # TODO: wire previous-run overlay via BomApiClient `basetime` param.
        return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    cache_key = f"marine:wind:{lat:.4f}:{lon:.4f}:{api_model}"
    cache = _get_cache()
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Prefer the native-async BoM client when registered (beta.py path) —
    # avoids the thread-pool executor entirely. Falls through to the sync
    # client + run_in_executor when only the sync client is initialized
    # (app.py path), preserving the original behavior bit-for-bit.
    async_bom = _get_bom_async_client()
    start = time.time()
    if async_bom is not None and async_bom.is_available:
        try:
            raw_df = await async_bom.get_point_dataframe(
                model=api_model,
                lons=[lon],
                lats=[lat],
                variables=["wind_speed", "wind_direction", "gust"],
                map_variable_names=True,
            )
        except Exception as e:
            logger.error("BoM wind ensemble fetch failed (%s, async): %s", model_key, e)
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}
    else:
        bom_client = _get_bom_client()
        if bom_client is None or not bom_client.is_available:
            logger.debug("BoM client unavailable — skipping %s", model_key)
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}
        try:
            loop = asyncio.get_event_loop()
            raw_df = await loop.run_in_executor(
                None,
                lambda: bom_client.get_point_dataframe(
                    model=api_model,
                    lons=[lon],
                    lats=[lat],
                    variables=["wind_speed", "wind_direction", "gust"],
                    map_variable_names=True,
                ),
            )
        except Exception as e:
            logger.error("BoM wind ensemble fetch failed (%s): %s", model_key, e)
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

    df = _reshape_bom_wind_to_marine_format(raw_df, api_model, deterministic)

    # ACCESS-GE is natively 3-hourly; the rest of the pipeline assumes hourly.
    if not df.empty and not deterministic:
        df = _upsample_to_hourly(df)

    elapsed = time.time() - start
    logger.info(
        "BoM wind ensemble (%s) fetched + reshaped in %.2fs — %d rows, %d cols",
        model_key, elapsed, len(df), len(df.columns),
    )

    result = {
        "df": df,
        "generation_time": None,
        "first_forecast_time": df.index[0] if not df.empty else None,
    }
    if cache and not df.empty:
        cache.set(cache_key, result, ttl_seconds=3600)
    return result


async def async_fetch_all_wind_ensembles(
    lat: float, lon: float, past_days: int = 0,
) -> Dict[str, Dict[str, Any]]:
    """Fetch wind ensembles for all models concurrently (physics + AI)."""
    tasks = {
        model_key: async_fetch_wind_ensemble(lat, lon, model_key, past_days)
        for model_key in MARINE_ALL_WIND_MODELS
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out = {}
    for model_key, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            logger.error("Async wind ensemble gather failed (%s): %s", model_key, result)
            out[model_key] = {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}
        else:
            out[model_key] = result
    return out


async def async_fetch_wave_forecast(
    lat: float, lon: float, model_name: str = "best_match",
    past_days: int = 0,
) -> pd.DataFrame:
    """Async wave forecast fetch with optional past_days."""
    suffix = ":prev" if past_days > 0 else ""
    cache_key = f"marine:wave{suffix}:{lat:.4f}:{lon:.4f}:{model_name}"
    cache = _get_cache()
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    client = _get_async_client()
    if client is None:
        return pd.DataFrame()

    try:
        df = await client.async_get_wave_forecast(
            lat, lon, model=model_name, past_days=past_days,
        )
        if cache and not df.empty:
            cache.set(cache_key, df, ttl_seconds=3600)
        return df
    except Exception as e:
        logger.error("Async wave forecast fetch failed (%s): %s", model_name, e)
        return pd.DataFrame()


async def async_fetch_all_wave_forecasts(
    lat: float, lon: float, past_days: int = 0,
) -> Dict[str, pd.DataFrame]:
    """Fetch wave forecasts (Hs + Tp) from all configured wave models concurrently."""
    tasks = {
        name: async_fetch_wave_forecast(lat, lon, cfg["api_model"], past_days)
        for name, cfg in MARINE_WAVE_MODELS.items()
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out = {}
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            logger.error("Async wave forecast gather failed (%s): %s", name, result)
            out[name] = pd.DataFrame()
        else:
            out[name] = result
    return out


async def async_fetch_all_marine_data(
    lat: float, lon: float, is_marine: bool, past_days: int = 0,
) -> Dict[str, Any]:
    """
    Top-level async fetch: wind ensembles + multi-model wave forecasts +
    buoy observations all fetched concurrently. Returns a dict with both
    `wave_data` (per-model dict for plotting) and `wave_df` (the ECMWF
    primary used for weather-window / exceedance analysis).
    """
    wind_task = async_fetch_all_wind_ensembles(lat, lon, past_days)

    async def _empty_wave_dict():
        return {name: pd.DataFrame() for name in MARINE_WAVE_MODELS}

    wave_task = async_fetch_all_wave_forecasts(lat, lon, past_days) if is_marine else _empty_wave_dict()

    # Buoy observations (sync wrapped in executor since buoy_service is sync)
    async def _fetch_buoy():
        if not is_marine or past_days > 0:
            return pd.DataFrame(), None
        try:
            from src.services.buoy_service import fetch_buoy_observations
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fetch_buoy_observations, lat, lon)
        except Exception as e:
            logger.warning("Async buoy observation fetch failed: %s", e)
            return pd.DataFrame(), None

    wind_data, wave_data, buoy_result = await asyncio.gather(
        wind_task, wave_task, _fetch_buoy(),
    )
    buoy_df, buoy_meta = buoy_result

    # Canonical single-model wave_df used by weather-window / exceedance
    # analysis (mirrors how wind analysis pins to ECMWF IFS).
    wave_df = wave_data.get("ECMWF", pd.DataFrame())

    return {
        "wind_data": wind_data,
        "wave_data": wave_data,
        "wave_df": wave_df,
        "buoy_df": buoy_df,
        "buoy_meta": buoy_meta,
    }
