"""
Bureau of Meteorology (BoM) API client — native async variant.

Same public surface as `bom_api_client.BomApiClient` but uses
`httpx.AsyncClient` end-to-end. Cognito authentication is performed via a
direct POST to the IDP HTTPS endpoint, so no thread-pool executor is used
anywhere on the BoM call path.

Used by `beta.py` (AWS deployment build). `app.py` continues to use the
sync client wrapped in `loop.run_in_executor`, so the two paths remain
side-by-side for the trial's "no threading" demonstration.

API Base: https://api.bsc.bom.gov.au
Auth:     Cognito USER_PASSWORD_AUTH via direct POST to
          cognito-idp.{region}.amazonaws.com (the public AWS API that
          boto3.client("cognito-idp").initiate_auth wraps).
"""
import asyncio
import io
import logging
import time
from typing import Dict, List, Optional

import httpx
import pandas as pd
import xarray as xr

from src.data.bom_api_client import (
    BOM_API_BASE_URL,
    COGNITO_CLIENT_ID,
    COGNITO_REGION,
    TOKEN_REFRESH_BUFFER_SECONDS,
    _reshape_point_dataframe,
)

logger = logging.getLogger(__name__)


class BomApiAsyncClient:
    """
    Async client for the BoM Weather API.

    AsyncClient is rebuilt per event loop (Dash callbacks open a fresh loop
    on each invocation) following the same pattern as
    `OpenMeteoClient._get_async_client`. Token state is guarded by an
    asyncio.Lock so concurrent fan-outs don't race the refresh.
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

        self._id_token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._lock = asyncio.Lock()

        self._async_client: Optional[httpx.AsyncClient] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

        self._available = True

    @property
    def is_available(self) -> bool:
        """Whether the client is configured and Cognito auth has not failed."""
        return self._available and bool(self._username) and bool(self._password)

    def _get_async_client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._async_client is None or self._async_loop is not current_loop:
            self._async_client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=5,
                    max_keepalive_connections=3,
                ),
            )
            self._async_loop = current_loop
        return self._async_client

    async def _ensure_token(self) -> None:
        """Fetch / refresh the Cognito ID token via direct HTTPS (no boto3)."""
        async with self._lock:
            now = time.time()
            if self._id_token and now < (self._token_expiry - TOKEN_REFRESH_BUFFER_SECONDS):
                return  # Token still valid

            url = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
            payload = {
                "AuthFlow": "USER_PASSWORD_AUTH",
                "ClientId": COGNITO_CLIENT_ID,
                "AuthParameters": {
                    "USERNAME": self._username,
                    "PASSWORD": self._password,
                },
            }
            headers = {
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                "Content-Type": "application/x-amz-json-1.1",
            }

            try:
                client = self._get_async_client()
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                auth_result = resp.json()["AuthenticationResult"]
                self._id_token = auth_result["IdToken"]
                self._token_expiry = now + auth_result.get("ExpiresIn", 3600)
                logger.info(
                    "BoM API (async): Cognito token obtained (expires in %ds)",
                    auth_result.get("ExpiresIn", 3600),
                )
            except Exception as e:
                logger.error("BoM API (async): Cognito authentication failed: %s", e)
                self._available = False
                raise

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._id_token}",
            "Content-Type": "application/json",
        }

    # ── Point-based data extraction ──────────────────────────────────────

    async def get_point_data(
        self,
        model: str,
        lons: List[float],
        lats: List[float],
        variables: Optional[List[str]] = None,
        domain: Optional[str] = None,
        member: Optional[List[int]] = None,
        basetime: Optional[str] = None,
    ) -> xr.Dataset:
        """Async twin of `BomApiClient.get_point_data` — same payload shape."""
        await self._ensure_token()

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

        logger.debug(
            "BoM API (async) POST %s — %d points, vars=%s", url, len(lons), variables,
        )

        client = self._get_async_client()
        try:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            logger.error(
                "BoM API (async) error (%s %s): %s",
                model, e.response.status_code, detail,
            )
            raise
        except httpx.RequestError as e:
            logger.error("BoM API (async) request failed (%s): %s", model, e)
            raise

        buffer = io.BytesIO(resp.content)
        return xr.open_dataset(buffer, decode_timedelta=False)

    async def get_point_dataframe(
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
        """Async twin of `BomApiClient.get_point_dataframe`."""
        ds = await self.get_point_data(model, lons, lats, variables, domain, member)
        return _reshape_point_dataframe(
            ds, model, point_index=point_index, map_variable_names=map_variable_names,
        )

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None


# ─── Module-level singleton ───────────────────────────────────────────────────

_async_client: Optional[BomApiAsyncClient] = None


def init_bom_async_client(
    username: str,
    password: str,
    base_url: str = BOM_API_BASE_URL,
) -> Optional[BomApiAsyncClient]:
    """Initialize the global async BoM client. Returns None if creds missing."""
    global _async_client
    if not username or not password:
        logger.warning(
            "BoM API credentials not provided — BoM features will be unavailable",
        )
        return None
    _async_client = BomApiAsyncClient(username, password, base_url)
    return _async_client


def get_bom_async_client() -> Optional[BomApiAsyncClient]:
    """Return the global async BoM client, or None if not initialized."""
    return _async_client
