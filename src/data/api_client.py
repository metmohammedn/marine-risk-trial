"""
Open-Meteo API client — marine-only subset.
Provides wind ensemble and wave forecast endpoints.
"""
import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx
import pandas as pd

from src.utils.constants import KMH_TO_KNOTS

logger = logging.getLogger(__name__)


class OpenMeteoClient:
    """
    HTTP client for Open-Meteo ensemble and marine API endpoints.
    Features: connection pooling, automatic retries with backoff.
    """

    def __init__(
        self,
        forecast_url: str = "https://api.open-meteo.com/v1/forecast",
        ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble",
        marine_url: str = "https://marine-api.open-meteo.com/v1/marine",
        api_key: str = "",
        timeout: int = 30,
        max_connections: int = 20,
        max_retries: int = 3,
    ):
        self._forecast_url = forecast_url
        self._ensemble_url = ensemble_url
        self._marine_url = marine_url
        self._api_key = api_key
        self._max_retries = max_retries
        self._timeout = timeout
        self._max_connections = max_connections

        self._client = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections // 2,
                keepalive_expiry=60,
            ),
            transport=httpx.HTTPTransport(retries=max_retries),
        )

        # Async client is created lazily, per-event-loop. See _get_async_client.
        # Sharing a single httpx.AsyncClient across asyncio loops causes
        # "Event loop is closed" errors when Dash callbacks open a fresh loop
        # on each invocation — the pooled connections are bound to the prior
        # (now-dead) loop.
        self._async_client: Optional[httpx.AsyncClient] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_async_client(self) -> httpx.AsyncClient:
        """Return an httpx.AsyncClient bound to the currently running event loop.

        Rebuilds the client whenever the running loop changes so connection
        pooling works within a single fan-out but doesn't try (and fail) to
        reuse sockets from a closed loop on the next callback invocation.
        """
        current_loop = asyncio.get_running_loop()
        if self._async_client is None or self._async_loop is not current_loop:
            # Old client (if any) is bound to a now-defunct loop; we can't
            # aclose() it from here, so let GC reclaim it. The dead loop's
            # sockets will be cleaned up by the OS.
            self._async_client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=self._max_connections,
                    max_keepalive_connections=self._max_connections // 2,
                    keepalive_expiry=60,
                ),
                transport=httpx.AsyncHTTPTransport(retries=self._max_retries),
            )
            self._async_loop = current_loop
        return self._async_client

    def close(self):
        """Close the sync HTTP client and release connections."""
        self._client.close()

    async def aclose(self):
        """Close the async HTTP client bound to the current loop, if any."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
            self._async_loop = None

    def _inject_api_key(self, params: dict) -> dict:
        """Append API key to request params if configured."""
        if self._api_key:
            params = {**params, "apikey": self._api_key}
        return params

    def _request(self, url: str, params: dict) -> dict:
        """Make a GET request with retry logic."""
        params = self._inject_api_key(params)
        for attempt in range(self._max_retries):
            try:
                response = self._client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "API HTTP error (attempt %d/%d): %s %s",
                    attempt + 1, self._max_retries, e.response.status_code, url,
                )
                if attempt == self._max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
            except httpx.RequestError as e:
                logger.warning(
                    "API request error (attempt %d/%d): %s",
                    attempt + 1, self._max_retries, e,
                )
                if attempt == self._max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

    async def _async_request(self, url: str, params: dict) -> dict:
        """Make an async GET request with retry logic."""
        params = self._inject_api_key(params)
        client = self._get_async_client()
        for attempt in range(self._max_retries):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Async API HTTP error (attempt %d/%d): %s %s",
                    attempt + 1, self._max_retries, e.response.status_code, url,
                )
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
            except httpx.RequestError as e:
                logger.warning(
                    "Async API request error (attempt %d/%d): %s",
                    attempt + 1, self._max_retries, e,
                )
                if attempt == self._max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    # =========================================================================
    # WIND ENSEMBLE FORECASTS
    # =========================================================================

    async def async_get_wind_ensemble_forecast(
        self,
        lat: float,
        lon: float,
        model: str = "ecmwf_ifs025",
        past_days: int = 0,
    ) -> Dict[str, Any]:
        """Async wind ensemble forecast — returns DataFrame with wind/gust members in knots."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wind_speed_10m,wind_gusts_10m",
            "models": model,
            "timezone": "GMT",
        }
        if past_days > 0:
            params["past_days"] = past_days

        data = await self._async_request(self._ensemble_url, params)
        if "hourly" not in data:
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

        hourly = data["hourly"]
        times = pd.to_datetime(hourly["time"])

        col_data = {}
        for key, values in hourly.items():
            if key == "time":
                continue
            series = pd.to_numeric(pd.Series(values, dtype="float64"), errors="coerce")
            if "wind_speed" in key or "wind_gust" in key:
                series = series * KMH_TO_KNOTS
            col_data[key] = series.values

        df = pd.DataFrame(col_data, index=times)
        df.index.name = "time"

        return {
            "df": df,
            "generation_time": data.get("generationtime_ms"),
            "first_forecast_time": df.index[0] if not df.empty else None,
        }

    def get_wind_ensemble_forecast(
        self,
        lat: float,
        lon: float,
        model: str = "ecmwf_ifs025",
    ) -> Dict[str, Any]:
        """Sync wind ensemble forecast — returns DataFrame with wind/gust members in knots."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wind_speed_10m,wind_gusts_10m",
            "models": model,
            "timezone": "GMT",
        }

        data = self._request(self._ensemble_url, params)
        if "hourly" not in data:
            return {"df": pd.DataFrame(), "generation_time": None, "first_forecast_time": None}

        hourly = data["hourly"]
        times = pd.to_datetime(hourly["time"])

        col_data = {}
        for key, values in hourly.items():
            if key == "time":
                continue
            series = pd.to_numeric(pd.Series(values, dtype="float64"), errors="coerce")
            if "wind_speed" in key or "wind_gust" in key:
                series = series * KMH_TO_KNOTS
            col_data[key] = series.values

        df = pd.DataFrame(col_data, index=times)
        df.index.name = "time"

        return {
            "df": df,
            "generation_time": data.get("generationtime_ms"),
            "first_forecast_time": df.index[0] if not df.empty else None,
        }

    # =========================================================================
    # WAVE FORECASTS
    # =========================================================================

    async def async_get_wave_forecast(
        self,
        lat: float,
        lon: float,
        model: str = "best_match",
        past_days: int = 0,
    ) -> pd.DataFrame:
        """Async wave height + period forecast."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wave_height,wave_period",
            "models": model,
            "timezone": "GMT",
        }
        if past_days > 0:
            params["past_days"] = past_days

        try:
            data = await self._async_request(self._marine_url, params)
        except Exception:
            for fallback in ["ecmwf_wam025", "ncep_gfswave025", "gwam"]:
                if fallback == model:
                    continue
                try:
                    params["models"] = fallback
                    data = await self._async_request(self._marine_url, params)
                    break
                except Exception:
                    continue
            else:
                logger.error("All async wave forecast models failed for (%s, %s)", lat, lon)
                return pd.DataFrame()

        if "hourly" not in data:
            return pd.DataFrame()

        hourly = data["hourly"]
        df = pd.DataFrame({"time": pd.to_datetime(hourly["time"])})
        df = df.set_index("time")

        for key, values in hourly.items():
            if key != "time":
                df[key] = pd.to_numeric(pd.Series(values, index=df.index), errors="coerce")

        return df

    def get_wave_forecast(
        self,
        lat: float,
        lon: float,
        model: str = "best_match",
    ) -> pd.DataFrame:
        """Sync wave height + period forecast."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wave_height,wave_period",
            "models": model,
            "timezone": "GMT",
        }

        try:
            data = self._request(self._marine_url, params)
        except Exception:
            for fallback in ["ecmwf_wam025", "ncep_gfswave025", "gwam"]:
                if fallback == model:
                    continue
                try:
                    params["models"] = fallback
                    data = self._request(self._marine_url, params)
                    break
                except Exception:
                    continue
            else:
                logger.error("All wave forecast models failed for (%s, %s)", lat, lon)
                return pd.DataFrame()

        if "hourly" not in data:
            return pd.DataFrame()

        hourly = data["hourly"]
        df = pd.DataFrame({"time": pd.to_datetime(hourly["time"])})
        df = df.set_index("time")

        for key, values in hourly.items():
            if key != "time":
                df[key] = pd.to_numeric(pd.Series(values, index=df.index), errors="coerce")

        return df


# Module-level singleton
_client: Optional[OpenMeteoClient] = None


def init_api_client(
    forecast_url: str = "https://api.open-meteo.com/v1/forecast",
    ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble",
    marine_url: str = "https://marine-api.open-meteo.com/v1/marine",
    api_key: str = "",
    timeout: int = 30,
    max_connections: int = 20,
) -> OpenMeteoClient:
    """Initialize the global API client."""
    global _client
    _client = OpenMeteoClient(
        forecast_url=forecast_url,
        ensemble_url=ensemble_url,
        marine_url=marine_url,
        api_key=api_key,
        timeout=timeout,
        max_connections=max_connections,
    )
    return _client


def get_api_client() -> OpenMeteoClient:
    """Get the global API client."""
    if _client is None:
        raise RuntimeError("API client not initialized. Call init_api_client() first.")
    return _client
