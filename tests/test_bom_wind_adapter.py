"""
Smoke tests for the BoM ACCESS wind adapter in `marine_service`.

These tests do NOT hit the BoM API. They construct DataFrames in the same
shape that the parent repo's `BomApiClient.get_point_dataframe` returns, then
verify the adapter:
  1. Renames columns to the wide `wind_*_<api_model>_member_NN` convention
     marine_service expects.
  2. Converts m/s → knots for wind speed and gust columns (but not direction).
  3. Upsamples 3-hourly ACCESS-GE data onto an hourly index for ACCESS-GE.
  4. Leaves ACCESS-G as a deterministic single-member series.

Run with: ../venv/bin/python -m pytest tests/test_bom_wind_adapter.py -v
(or just `python -m pytest` from inside marine-standalone with pandas + numpy
available).
"""
import os
import sys

import numpy as np
import pandas as pd

# Make `src` importable when run from the marine-standalone root.
HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.services.marine_service import (  # noqa: E402
    _reshape_bom_wind_to_marine_format,
    _upsample_to_hourly,
)
from src.utils.constants import MS_TO_KNOTS  # noqa: E402


# ─── ACCESS-G (deterministic) ────────────────────────────────────────────────


def _fake_access_g_raw_df() -> pd.DataFrame:
    """Mimic parent BomApiClient.get_point_dataframe output for ACCESS-G."""
    times = pd.date_range("2026-04-09T00:00", periods=6, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "wind_speed_10m_access-g": [10.0, 11.0, 12.5, 13.0, 12.0, 11.5],
            "wind_direction_10m_access-g": [180, 185, 190, 195, 200, 205],
            "wind_gusts_10m_access-g": [15.0, 16.0, 17.5, 18.0, 17.0, 16.5],
        },
        index=times,
    )


def test_access_g_reshape_synthesises_member_00():
    raw = _fake_access_g_raw_df()
    out = _reshape_bom_wind_to_marine_format(raw, api_model="access-g", deterministic=True)

    expected = {
        "wind_speed_10m_access-g_member_00",
        "wind_direction_10m_access-g_member_00",
        "wind_gusts_10m_access-g_member_00",
    }
    assert expected.issubset(set(out.columns)), f"missing cols: {expected - set(out.columns)}"


def test_access_g_units_converted_to_knots_for_speed_and_gust_only():
    raw = _fake_access_g_raw_df()
    out = _reshape_bom_wind_to_marine_format(raw, api_model="access-g", deterministic=True)

    # 10 m/s ≈ 19.43844 kn
    np.testing.assert_allclose(
        out["wind_speed_10m_access-g_member_00"].iloc[0],
        10.0 * MS_TO_KNOTS,
        rtol=1e-6,
    )
    np.testing.assert_allclose(
        out["wind_gusts_10m_access-g_member_00"].iloc[0],
        15.0 * MS_TO_KNOTS,
        rtol=1e-6,
    )
    # Direction must NOT be scaled.
    np.testing.assert_allclose(
        out["wind_direction_10m_access-g_member_00"].iloc[0],
        180.0,
        rtol=1e-6,
    )


def test_access_g_marine_service_member_filter_matches_synthesised_columns():
    """The downstream code in marine_service filters columns by the substrings
    'wind_speed_10m' AND 'member' — make sure our reshape produces cols that
    match both."""
    raw = _fake_access_g_raw_df()
    out = _reshape_bom_wind_to_marine_format(raw, api_model="access-g", deterministic=True)

    matched = [c for c in out.columns if "wind_speed_10m" in c and "member" in c]
    assert len(matched) == 1, f"expected 1 wind_speed_10m member col, got {matched}"


# ─── ACCESS-GE (ensemble, 3-hourly) ──────────────────────────────────────────


def _fake_access_ge_raw_df(n_members: int = 3) -> pd.DataFrame:
    """Mimic parent BomApiClient.get_point_dataframe ensemble output for
    ACCESS-GE — already in wide `_member_NN` format, native 3-hourly cadence."""
    times = pd.date_range("2026-04-09T00:00", periods=4, freq="3h", tz="UTC")
    cols = {}
    for m in range(n_members):
        suffix = f"access-ge_member_{m:02d}"
        cols[f"wind_speed_10m_{suffix}"] = [10.0 + m, 12.0 + m, 11.0 + m, 13.0 + m]
        cols[f"wind_direction_10m_{suffix}"] = [180, 190, 200, 210]
        cols[f"wind_gusts_10m_{suffix}"] = [15.0 + m, 17.0 + m, 16.0 + m, 18.0 + m]
    return pd.DataFrame(cols, index=times)


def test_access_ge_reshape_preserves_member_columns():
    raw = _fake_access_ge_raw_df(n_members=3)
    out = _reshape_bom_wind_to_marine_format(raw, api_model="access-ge", deterministic=False)

    # All 3 ensemble members for wind_speed_10m present and renamed to nothing
    members = [c for c in out.columns if "wind_speed_10m" in c and "member" in c]
    assert len(members) == 3, f"expected 3 wind speed members, got {members}"


def test_access_ge_units_converted_to_knots():
    raw = _fake_access_ge_raw_df(n_members=2)
    out = _reshape_bom_wind_to_marine_format(raw, api_model="access-ge", deterministic=False)

    np.testing.assert_allclose(
        out["wind_speed_10m_access-ge_member_00"].iloc[0],
        10.0 * MS_TO_KNOTS,
        rtol=1e-6,
    )
    np.testing.assert_allclose(
        out["wind_gusts_10m_access-ge_member_01"].iloc[1],
        18.0 * MS_TO_KNOTS,
        rtol=1e-6,
    )


def test_access_ge_upsample_3h_to_hourly():
    raw = _fake_access_ge_raw_df(n_members=2)
    reshaped = _reshape_bom_wind_to_marine_format(raw, api_model="access-ge", deterministic=False)
    hourly = _upsample_to_hourly(reshaped)

    # 4 native points × 3h = 9h span → expect 10 hourly points (0..9 inclusive)
    assert len(hourly) == 10, f"expected 10 hourly rows, got {len(hourly)}"

    # Endpoints preserved
    np.testing.assert_allclose(
        hourly["wind_speed_10m_access-ge_member_00"].iloc[0],
        reshaped["wind_speed_10m_access-ge_member_00"].iloc[0],
        rtol=1e-6,
    )
    np.testing.assert_allclose(
        hourly["wind_speed_10m_access-ge_member_00"].iloc[-1],
        reshaped["wind_speed_10m_access-ge_member_00"].iloc[-1],
        rtol=1e-6,
    )

    # Hourly index is contiguous 1h spacing
    deltas = hourly.index.to_series().diff().dropna().unique()
    assert len(deltas) == 1, f"non-uniform hourly spacing: {deltas}"
    assert deltas[0] == pd.Timedelta(hours=1)


def test_upsample_handles_empty_input():
    assert _upsample_to_hourly(pd.DataFrame()).empty


def test_reshape_handles_empty_input():
    assert _reshape_bom_wind_to_marine_format(pd.DataFrame(), "access-g", True).empty
