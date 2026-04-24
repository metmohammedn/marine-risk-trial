"""
IMOS/AODN wave buoy observation service — fetches recent wave buoy data
from the AODN cloud-optimised S3 bucket for overlay on marine forecast charts.

Data source: s3://aodn-cloud-optimised/wave_buoy_realtime_nonqc.parquet/
Format:      Hive-partitioned Parquet (partitioned by ``site_name``, then
             ``timestamp`` for monthly files)
Auth:        Public (unsigned requests)
Updates:     Hourly (staggered by provider)
Coverage:    ~78 Australian wave buoy sites (WA focus for marine page)
"""
import io
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Dict, Optional, Tuple
from urllib.parse import quote

import pandas as pd

from src.utils.constants import (
    IMOS_WAVE_BUOYS,
    BUOY_CACHE_TTL_SECONDS,
    BUOY_MAX_DISTANCE_KM,
    BUOY_PAST_HOURS,
)

logger = logging.getLogger(__name__)

# ── S3 Configuration ─────────────────────────────────────────────────
S3_BUCKET = "aodn-cloud-optimised"
S3_PREFIX = "wave_buoy_realtime_nonqc.parquet"
S3_REGION = "ap-southeast-2"

# ── NetCDF / Parquet variable mapping ─────────────────────────────────
# Parquet column → canonical chart variable name
BUOY_VARIABLE_MAP = {
    "WHTH": "wave_height",       # Significant wave height (Hs) in metres
    "WPPE": "wave_period",       # Peak spectral wave period (Tp) in seconds
    "WPDI": "wave_direction",    # Peak wave direction in degrees
}

# ── In-memory cache ──────────────────────────────────────────────────
_cache_lock = threading.Lock()
_buoy_cache: Dict[str, pd.DataFrame] = {}   # keyed by S3 site_name
_buoy_cache_time: Dict[str, float] = {}
_match_cache: Dict[Tuple[float, float], Optional[Tuple[str, dict, float]]] = {}


# ── Haversine distance (mirrors obs_service.py) ──────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


# ── Station matching ──────────────────────────────────────────────────

def find_nearest_buoy(
    target_lat: float,
    target_lon: float,
    max_distance_km: float = BUOY_MAX_DISTANCE_KM,
) -> Optional[Tuple[str, dict, float]]:
    """
    Find the nearest IMOS wave buoy to a target coordinate.

    Returns
    -------
    (buoy_name, buoy_config, distance_km)  if found within *max_distance_km*
    None  otherwise
    """
    cache_key = (round(target_lat, 4), round(target_lon, 4))
    if cache_key in _match_cache:
        return _match_cache[cache_key]

    best_name, best_config, best_dist = None, None, float("inf")
    for name, config in IMOS_WAVE_BUOYS.items():
        dist = _haversine(target_lat, target_lon, config["lat"], config["lon"])
        if dist < best_dist:
            best_name, best_config, best_dist = name, config, dist

    if best_dist <= max_distance_km:
        result = (best_name, best_config, best_dist)
    else:
        result = None

    _match_cache[cache_key] = result
    return result


# ── S3 data fetch ─────────────────────────────────────────────────────

def _get_s3_client():
    """Create an unsigned boto3 S3 client for public AODN data."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    return boto3.client(
        "s3",
        config=Config(signature_version=UNSIGNED),
        region_name=S3_REGION,
    )


def _fetch_buoy_data(
    site_name: str,
    past_hours: int = BUOY_PAST_HOURS,
) -> pd.DataFrame:
    """
    Download recent wave buoy data from the AODN S3 Parquet dataset.

    Parameters
    ----------
    site_name : str
        Exact site name matching the S3 partition key (e.g. "Dampier").
    past_hours : int
        How many hours of past data to return.

    Returns
    -------
    pd.DataFrame
        Indexed by UTC datetime with columns ``wave_height``, ``wave_period``,
        ``wave_direction`` (where available).  Empty DataFrame on failure.
    """
    try:
        s3 = _get_s3_client()
    except Exception as exc:
        logger.warning("Failed to create S3 client: %s", exc)
        return pd.DataFrame()

    # URL-encode the site name for the S3 prefix (spaces → %20 etc.)
    encoded_name = quote(site_name, safe="")
    prefix = f"{S3_PREFIX}/site_name={encoded_name}/"

    try:
        # List all Parquet files for this site, find the most recent
        paginator = s3.get_paginator("list_objects_v2")
        all_files = []
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".parquet"):
                    all_files.append(obj)

        if not all_files:
            logger.info("No Parquet files found for buoy '%s'", site_name)
            return pd.DataFrame()

        # Sort by timestamp partition (descending) to get most recent months
        def _extract_timestamp(obj):
            parts = obj["Key"].split("timestamp=")
            if len(parts) > 1:
                try:
                    return int(parts[1].split("/")[0])
                except (ValueError, IndexError):
                    pass
            return 0

        all_files.sort(key=_extract_timestamp, reverse=True)

        # Read the most recent file(s) until we have enough past_hours
        cutoff = datetime.now(timezone.utc) - timedelta(hours=past_hours)
        frames = []

        for obj in all_files[:3]:  # At most 3 monthly files (current + 2 prev)
            resp = s3.get_object(Bucket=S3_BUCKET, Key=obj["Key"])
            df_part = pd.read_parquet(io.BytesIO(resp["Body"].read()))

            if "TIME" not in df_part.columns:
                continue

            df_part["TIME"] = pd.to_datetime(df_part["TIME"], utc=True)
            df_part = df_part[df_part["TIME"] >= cutoff]

            if not df_part.empty:
                frames.append(df_part)

            # If earliest record in this file is before cutoff, no need
            # to read older files
            earliest = pd.to_datetime(df_part["TIME"].min()) if not df_part.empty else None
            if earliest and earliest <= cutoff:
                break

        if not frames:
            logger.info("No recent data (last %dh) for buoy '%s'", past_hours, site_name)
            return pd.DataFrame()

        raw = pd.concat(frames, ignore_index=True)
        raw = raw.sort_values("TIME").drop_duplicates(subset=["TIME"], keep="last")
        raw = raw.set_index("TIME")

        # Map Parquet columns to canonical names
        result = pd.DataFrame(index=raw.index)
        for parquet_col, canonical in BUOY_VARIABLE_MAP.items():
            if parquet_col in raw.columns:
                result[canonical] = pd.to_numeric(raw[parquet_col], errors="coerce")

        result = result.dropna(how="all")

        if not result.empty:
            logger.info(
                "Buoy '%s': %d observations from %s to %s",
                site_name, len(result),
                result.index.min().strftime("%Y-%m-%d %H:%M"),
                result.index.max().strftime("%Y-%m-%d %H:%M"),
            )

        return result

    except Exception as exc:
        logger.warning("Buoy data fetch failed for '%s': %s", site_name, exc)
        return pd.DataFrame()


# ── Public API ────────────────────────────────────────────────────────

def fetch_buoy_observations(
    lat: float,
    lon: float,
    past_hours: int = BUOY_PAST_HOURS,
) -> Tuple[pd.DataFrame, Optional[Dict]]:
    """
    Fetch wave buoy observations for the nearest IMOS buoy.

    Parameters
    ----------
    lat, lon : float
        Target site coordinates.
    past_hours : int
        Hours of past data to retrieve.

    Returns
    -------
    (buoy_df, buoy_meta)
        buoy_df:   DataFrame with ``wave_height``, ``wave_period``, etc.
        buoy_meta: dict with ``name``, ``provider``, ``distance_km``
                   or None if no buoy found.
    """
    match = find_nearest_buoy(lat, lon)
    if match is None:
        return pd.DataFrame(), None

    buoy_name, buoy_config, distance_km = match
    buoy_meta = {
        "name": buoy_name,
        "provider": buoy_config.get("provider", "IMOS"),
        "distance_km": distance_km,
    }

    # Check cache
    now = time.time()
    with _cache_lock:
        if buoy_name in _buoy_cache and buoy_name in _buoy_cache_time:
            age = now - _buoy_cache_time[buoy_name]
            if age < BUOY_CACHE_TTL_SECONDS:
                cached_df = _buoy_cache[buoy_name]
                logger.debug(
                    "Buoy cache hit for '%s' (age %.0fs)", buoy_name, age,
                )
                return cached_df, buoy_meta

    # Fetch fresh data
    try:
        buoy_df = _fetch_buoy_data(buoy_name, past_hours=past_hours)
    except Exception as exc:
        logger.warning("Buoy fetch failed for '%s': %s", buoy_name, exc)
        buoy_df = pd.DataFrame()

    # Update cache
    with _cache_lock:
        _buoy_cache[buoy_name] = buoy_df
        _buoy_cache_time[buoy_name] = now

    if buoy_df.empty:
        return buoy_df, None

    return buoy_df, buoy_meta


def get_buoy_station_count() -> int:
    """Return the number of configured buoy stations."""
    return len(IMOS_WAVE_BUOYS)


def is_buoy_data_available() -> bool:
    """Check if any buoy data has been cached."""
    with _cache_lock:
        return any(not df.empty for df in _buoy_cache.values())
