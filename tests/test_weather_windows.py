"""
Tests for the weather-window calculation in `marine_service`.

Focus on the behaviour the client signed off on:
  1. Wind condition uses the P90 across ensemble members (not the median).
  2. Gust condition keeps the P100 (max) across ensemble members.
  3. ``model_key`` selects the wind source; unknown / empty models fall back
     to ECMWF IFS rather than returning silently-empty windows.

Run with: venv/bin/python -m pytest tests/test_weather_windows.py -v
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.services.marine_service import calculate_weather_windows  # noqa: E402


def _make_ensemble_df(
    wind_members: list[list[float]],
    gust_members: list[list[float]],
    model_tag: str = "ecmwf_ifs025",
    start: str = "2026-05-01 00:00",
    freq: str = "h",
) -> pd.DataFrame:
    """Build a fake ensemble DataFrame in the shape marine_service expects."""
    n_hours = len(wind_members[0])
    idx = pd.date_range(start=start, periods=n_hours, freq=freq, tz="UTC")
    data = {}
    for i, member_series in enumerate(wind_members):
        data[f"wind_speed_10m_{model_tag}_member_{i:02d}"] = member_series
    for i, member_series in enumerate(gust_members):
        data[f"wind_gusts_10m_{model_tag}_member_{i:02d}"] = member_series
    return pd.DataFrame(data, index=idx)


# ─── P90 wind statistic ──────────────────────────────────────────────────────


def test_wind_uses_p90_not_median():
    """
    An ensemble split 8:2 between a calm cluster and a hot cluster has the
    same median (20 kn) as a uniformly-calm ensemble, but its P90 is 50 kn.
    With a 34 kn threshold, the old median logic would leave the window
    open — the new P90 logic must close it.
    """
    # 8 calm members @ 20 kn, 2 hot members @ 50 kn. Sorted position 0.9*9=8.1
    # sits between the two 50 kn members, so P90 = 50 kn. Median = 20 kn.
    wind = [[20.0]] * 8 + [[50.0]] * 2
    gust = [[25.0]] * 10  # well below any gust threshold we'll use
    df = _make_ensemble_df(wind, gust)

    result = calculate_weather_windows(
        wind_data={"ECMWF IFS": {"df": df}},
        wave_df=pd.DataFrame(),
        wind_thresh=34,
        gust_thresh=48,
        wave_thresh=1.0,
        model_key="ECMWF IFS",
    )
    assert result["windows"] == [], (
        "P90 (~38 kn) is above the 34 kn threshold so no window should be open, "
        "even though the ensemble median (20 kn) is well below it."
    )


def test_low_spread_ensemble_opens_window_under_p90():
    """P90 of a tight ensemble stays near the members' shared value."""
    wind = [[18.0]] * 10      # P90 ≈ 18
    gust = [[25.0]] * 10      # max 25
    df = _make_ensemble_df(wind, gust)

    result = calculate_weather_windows(
        wind_data={"ECMWF IFS": {"df": df}},
        wave_df=pd.DataFrame(),
        wind_thresh=34,
        gust_thresh=48,
        wave_thresh=1.0,
        model_key="ECMWF IFS",
    )
    # Single-hour window, so total_hours rounds to 0, but windows list is non-empty.
    assert len(result["windows"]) == 1
    assert result["model_key"] == "ECMWF IFS"


# ─── P100 gust statistic ─────────────────────────────────────────────────────


def test_gust_uses_p100_max_any_member_above_threshold_closes_window():
    """One member gusting above the threshold is enough to close the window."""
    wind = [[15.0]] * 10      # P90 ≈ 15 — well under 34 kn
    # Nine members quiet (25 kn), one member at 50 kn. P100 = 50 kn.
    gust = [[25.0]] * 9 + [[50.0]]
    df = _make_ensemble_df(wind, gust)

    result = calculate_weather_windows(
        wind_data={"ECMWF IFS": {"df": df}},
        wave_df=pd.DataFrame(),
        wind_thresh=34,
        gust_thresh=48,
        wave_thresh=1.0,
        model_key="ECMWF IFS",
    )
    assert result["windows"] == [], (
        "Max gust (50 kn) exceeds the 48 kn threshold — window must be closed."
    )


# ─── Model selection + fallback ──────────────────────────────────────────────


def test_model_key_selects_correct_ensemble():
    """Different models produce different windows when their data diverges."""
    # ECMWF says it's breezy (closed), ACCESS-GE says calm (open).
    breezy_wind = [[40.0]] * 10         # P90 ≈ 40 kn → above threshold
    calm_wind = [[15.0]] * 10           # P90 ≈ 15 kn → under threshold
    calm_gust = [[25.0]] * 10

    ec_df = _make_ensemble_df(breezy_wind, calm_gust, model_tag="ecmwf_ifs025")
    ge_df = _make_ensemble_df(calm_wind, calm_gust, model_tag="access-ge")
    wind_data = {"ECMWF IFS": {"df": ec_df}, "ACCESS-GE": {"df": ge_df}}

    ec_result = calculate_weather_windows(
        wind_data, pd.DataFrame(), 34, 48, 1.0, model_key="ECMWF IFS",
    )
    ge_result = calculate_weather_windows(
        wind_data, pd.DataFrame(), 34, 48, 1.0, model_key="ACCESS-GE",
    )

    assert ec_result["windows"] == []
    assert len(ge_result["windows"]) == 1
    assert ge_result["model_key"] == "ACCESS-GE"


def test_unknown_model_falls_back_to_ecmwf():
    """If the requested model has no data, fall back rather than 404'ing."""
    calm_wind = [[15.0]] * 10
    calm_gust = [[25.0]] * 10
    ec_df = _make_ensemble_df(calm_wind, calm_gust)
    wind_data = {"ECMWF IFS": {"df": ec_df}}

    result = calculate_weather_windows(
        wind_data, pd.DataFrame(), 34, 48, 1.0, model_key="GFS",  # not in data
    )
    assert len(result["windows"]) == 1
    assert result["model_key"] == "ECMWF IFS", (
        "Expected fallback to the default model when the requested one is absent."
    )


def test_default_is_ecmwf_ifs():
    """Calling without an explicit model picks ECMWF IFS."""
    wind = [[15.0]] * 10
    gust = [[25.0]] * 10
    df = _make_ensemble_df(wind, gust)

    result = calculate_weather_windows(
        wind_data={"ECMWF IFS": {"df": df}},
        wave_df=pd.DataFrame(),
    )
    assert result["model_key"] == "ECMWF IFS"
    assert len(result["windows"]) == 1
