"""
Bureau of Meteorology (BoM) API client.

Provides authenticated access to the BoM Weather API for ACCESS model
forecasts and GSO solar nowcast data. Uses AWS Cognito for authentication
with automatic token refresh.

API Base: https://api.bsc.bom.gov.au
Models:   ACCESS-G (deterministic), ACCESS-GE (ensemble), ACCESS-CE (regional ensemble), GSO (solar nowcast)
Auth:     AWS Cognito USER_PASSWORD_AUTH flow via boto3
Response: Raw netCDF bytes → parsed with xarray
"""
import io
import logging
import threading
import time
from typing import Dict, List, Optional

import httpx
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

BOM_API_BASE_URL = "https://api.bsc.bom.gov.au"
COGNITO_CLIENT_ID = "3k33ol5egcaa195rpok06d077f"
COGNITO_REGION = "ap-southeast-2"

# Model definitions
BOM_MODELS = ["access-g", "access-c", "access-ge", "access-ce", "gso"]
BOM_DETERMINISTIC_MODELS = ["access-g", "access-c"]
BOM_ENSEMBLE_MODELS = ["access-ge", "access-ce"]
BOM_SOLAR_MODEL = "gso"

# ACCESS-CE domains (capital city sub-domains)
ACCESS_CE_DOMAINS = [
    "adelaide", "brisbane", "canberra", "darwin",
    "hobart", "melbourne", "perth", "sydney",
]

# Variable name mapping: BoM API names → canonical (Open-Meteo compatible) names
BOM_VARIABLE_MAP = {
    "t2m_celsius": "temperature_2m",
    "tmax_celsius": "temperature_2m_max",
    "tmin_celsius": "temperature_2m_min",
    "d2m_celsius": "dewpoint_2m",
    "wind_speed": "wind_speed_10m",
    "wind_direction": "wind_direction_10m",
    "gust": "wind_gusts_10m",
    "rh2m": "relative_humidity_2m",
    "sw_dn_avg": "shortwave_radiation",
    "prcp": "precipitation",
    "cldt": "cloud_cover",
    "rare": "radar_reflectivity",
}

# Reverse mapping: canonical → BoM API name
CANONICAL_TO_BOM = {v: k for k, v in BOM_VARIABLE_MAP.items()}

# GSO solar variable names (no mapping needed — display as-is)
GSO_VARIABLES = [
    "surface_global_irradiance",
    "surface_diffuse_irradiance",
    "direct_normal_irradiance",
]

# Token refresh buffer (refresh 5 minutes before expiry)
TOKEN_REFRESH_BUFFER_SECONDS = 300


# ─── BomApiClient ─────────────────────────────────────────────────────────────

class BomApiClient:
    """
    Client for the Bureau of Meteorology Weather API.

    Handles Cognito authentication with automatic token refresh,
    and provides methods for point-based data extraction and model info.
    """

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = BOM_API_BASE_URL,
        timeout: int = 60,
    ):
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        # Token state (thread-safe)
        self._id_token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._lock = threading.Lock()

        # HTTP client with connection pooling
        self._http = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        )

        self._available = True

    @property
    def is_available(self) -> bool:
        """Check if the BoM API client is configured and can authenticate."""
        return self._available and bool(self._username) and bool(self._password)

    def _ensure_token(self) -> None:
        """Authenticate with Cognito and cache the token. Auto-refreshes before expiry."""
        with self._lock:
            now = time.time()
            if self._id_token and now < (self._token_expiry - TOKEN_REFRESH_BUFFER_SECONDS):
                return  # Token still valid

            try:
                import boto3

                client = boto3.client("cognito-idp", region_name=COGNITO_REGION)
                response = client.initiate_auth(
                    ClientId=COGNITO_CLIENT_ID,
                    AuthFlow="USER_PASSWORD_AUTH",
                    AuthParameters={
                        "USERNAME": self._username,
                        "PASSWORD": self._password,
                    },
                )
                auth_result = response["AuthenticationResult"]
                self._id_token = auth_result["IdToken"]
                self._token_expiry = now + auth_result.get("ExpiresIn", 3600)
                logger.info("BoM API: Cognito token obtained (expires in %ds)", auth_result.get("ExpiresIn", 3600))
            except Exception as e:
                logger.error("BoM API: Cognito authentication failed: %s", e)
                self._available = False
                raise

    def _headers(self) -> Dict[str, str]:
        """Return auth headers for API requests."""
        return {
            "Authorization": f"Bearer {self._id_token}",
            "Content-Type": "application/json",
        }

    # ── Point-based data extraction ──────────────────────────────────────

    def get_point_data(
        self,
        model: str,
        lons: List[float],
        lats: List[float],
        variables: Optional[List[str]] = None,
        domain: Optional[str] = None,
        member: Optional[List[int]] = None,
        basetime: Optional[str] = None,
    ) -> xr.Dataset:
        """
        Fetch point-based forecast data from a BoM model.

        Args:
            model: Model name (access-g, access-ge, access-ce, gso)
            lons: List of longitudes
            lats: List of latitudes
            variables: Optional list of variable names (defaults to all)
            domain: Required for access-ce, optional for gso ("australia")
            member: Optional list of ensemble member indices (0-indexed)
            basetime: Optional forecast issue time (defaults to latest)

        Returns:
            xarray Dataset with dimensions (time, point) and requested variables
        """
        self._ensure_token()

        url = f"{self._base_url}/{model}/point"
        payload: Dict = {"x": lons, "y": lats}

        if variables:
            payload["variable"] = variables
        if domain:
            payload["domain"] = domain
        if member is not None:
            payload["member"] = member
        if basetime:
            payload["basetime"] = basetime

        logger.debug("BoM API POST %s — %d points, vars=%s", url, len(lons), variables)

        try:
            resp = self._http.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Try to extract error detail from response
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            logger.error("BoM API error (%s %s): %s", model, e.response.status_code, detail)
            raise
        except httpx.RequestError as e:
            logger.error("BoM API request failed (%s): %s", model, e)
            raise

        # Parse netCDF response
        buffer = io.BytesIO(resp.content)
        ds = xr.open_dataset(buffer, decode_timedelta=False)
        return ds

    def get_point_dataframe(
        self,
        model: str,
        lons: List[float],
        lats: List[float],
        variables: Optional[List[str]] = None,
        domain: Optional[str] = None,
        member: Optional[List[int]] = None,
        point_index: int = 0,
        map_variable_names: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch point data and convert to a pandas DataFrame for a single location.

        Args:
            model: Model name
            lons: List of longitudes (can be single element)
            lats: List of latitudes (can be single element)
            variables: Optional variable names
            domain: Optional domain
            member: Optional ensemble members
            point_index: Which point to extract (default 0 for single-point requests)
            map_variable_names: If True, rename BoM variable names to canonical names

        Returns:
            DataFrame indexed by time with variable columns
        """
        ds = self.get_point_data(model, lons, lats, variables, domain, member)
        return _reshape_point_dataframe(
            ds, model, point_index=point_index, map_variable_names=map_variable_names,
        )

    # ── Model info ───────────────────────────────────────────────────────

    def get_model_info(self, model: str, domain: Optional[str] = None) -> Dict:
        """
        Get model metadata: available variables, latest basetime, domain bounds.

        Args:
            model: Model name (access-g, access-ge, access-ce, gso)
            domain: Required for access-ce, optional for gso

        Returns:
            Dict with keys: current_basetime, available_basetimes, available_variables,
            domain_bounds, and optionally available_members
        """
        self._ensure_token()
        url = f"{self._base_url}/{model}/info"
        payload = {}
        if domain:
            payload["domain"] = domain

        try:
            resp = self._http.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("BoM API info request failed (%s): %s", model, e)
            return {}

    # ── Cleanup ──────────────────────────────────────────────────────────

    def close(self):
        """Close the HTTP client."""
        self._http.close()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _find_time_column(df: pd.DataFrame) -> Optional[str]:
    """Find the time/datetime column in a DataFrame."""
    for col in ["time", "valid_time", "forecast_time", "datetime"]:
        if col in df.columns:
            return col
    # Fallback: look for datetime64 columns
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    return None


def _reshape_point_dataframe(
    ds: xr.Dataset,
    model: str,
    point_index: int = 0,
    map_variable_names: bool = True,
) -> pd.DataFrame:
    """
    Reshape a BoM API point dataset (single-point or ensemble) into a wide
    DataFrame indexed on datetime. Pure transformation — no I/O. Shared by
    `BomApiClient.get_point_dataframe` and the native-async client in
    `bom_api_client_async.py`.
    """
    # Extract single point if point dimension exists
    if "point" in ds.dims and ds.dims["point"] > 1:
        ds = ds.isel(point=point_index)
    elif "point" in ds.dims:
        ds = ds.isel(point=0)

    # Handle ensemble dimension
    has_ensemble = "number" in ds.dims
    if has_ensemble:
        # Convert ensemble data to wide format: var_model_member_XX
        dfs = []
        for member_idx in ds["number"].values:
            member_ds = ds.sel(number=member_idx)
            member_df = member_ds.to_dataframe().reset_index()

            # Find time column
            time_col = _find_time_column(member_df)
            if time_col and time_col != "datetime":
                member_df = member_df.rename(columns={time_col: "datetime"})

            # Rename variable columns to include model and member
            for var in ds.data_vars:
                # Map BoM name to canonical name if requested
                canonical = BOM_VARIABLE_MAP.get(var, var) if map_variable_names else var
                col_name = f"{canonical}_{model}_member_{int(member_idx):02d}"
                if var in member_df.columns:
                    member_df = member_df.rename(columns={var: col_name})

            dfs.append(member_df)

        if dfs:
            # Merge all members on datetime
            result = dfs[0]
            for df in dfs[1:]:
                shared_cols = ["datetime"]
                data_cols = [c for c in df.columns if c not in result.columns and c != "datetime"]
                if data_cols:
                    result = result.merge(df[["datetime"] + data_cols], on="datetime", how="outer")
            result["datetime"] = pd.to_datetime(result["datetime"])
            return result.set_index("datetime").sort_index()

    # Deterministic: simple conversion
    df = ds.to_dataframe().reset_index()

    # Find and normalize time column
    time_col = _find_time_column(df)
    if time_col and time_col != "datetime":
        df = df.rename(columns={time_col: "datetime"})

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])

    # Rename variables to canonical names + model suffix
    if map_variable_names:
        for bom_name, canonical in BOM_VARIABLE_MAP.items():
            col_target = f"{canonical}_{model}"
            if bom_name in df.columns:
                df = df.rename(columns={bom_name: col_target})

    # Drop coordinate/metadata columns, keep datetime + data
    drop_cols = [
        "point", "height", "height_0", "latitude", "longitude",
        "forecast_reference_time", "time_0", "crs",
    ]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore")

    if "datetime" in df.columns:
        df = df.set_index("datetime").sort_index()

    return df


# ─── Module-level singleton ───────────────────────────────────────────────────

_client: Optional[BomApiClient] = None


def init_bom_client(username: str, password: str, base_url: str = BOM_API_BASE_URL) -> BomApiClient:
    """Initialize the global BoM API client."""
    global _client
    if not username or not password:
        logger.warning("BoM API credentials not provided — BoM features will be unavailable")
        return None
    _client = BomApiClient(username, password, base_url)
    return _client


def get_bom_client() -> Optional[BomApiClient]:
    """Return the global BoM API client, or None if not initialized."""
    return _client
