"""
Marine Weather Risk — Demo. Configuration.
Environment-driven config for the capabilities-trial deployment.
"""
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""

    # App
    APP_NAME = "Marine Weather Risk — Demo"
    DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 8050))

    # Redis cache (optional — app runs without it)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Open-Meteo API key (commercial subscription — leave blank for free tier)
    OPENMETEO_API_KEY = os.getenv("OPENMETEO_API_KEY", "")

    # Open-Meteo API endpoints (auto-switch to customer URLs when API key is set)
    OPENMETEO_FORECAST_URL = os.getenv("OPENMETEO_FORECAST_URL") or (
        "https://customer-api.open-meteo.com/v1/forecast"
        if os.getenv("OPENMETEO_API_KEY")
        else "https://api.open-meteo.com/v1/forecast"
    )
    OPENMETEO_ENSEMBLE_URL = os.getenv("OPENMETEO_ENSEMBLE_URL") or (
        "https://customer-ensemble-api.open-meteo.com/v1/ensemble"
        if os.getenv("OPENMETEO_API_KEY")
        else "https://ensemble-api.open-meteo.com/v1/ensemble"
    )
    OPENMETEO_MARINE_URL = os.getenv("OPENMETEO_MARINE_URL") or (
        "https://customer-marine-api.open-meteo.com/v1/marine"
        if os.getenv("OPENMETEO_API_KEY")
        else "https://marine-api.open-meteo.com/v1/marine"
    )

    # API tuning
    API_TIMEOUT = int(os.getenv("API_TIMEOUT", 30))
    API_MAX_CONNECTIONS = int(os.getenv("API_MAX_CONNECTIONS", 10))

    # Data source selector (future: "bom" for Bureau of Meteorology API)
    DATA_SOURCE = os.getenv("DATA_SOURCE", "open-meteo")

    # BoM Weather API (ACCESS-G / ACCESS-GE wind models).
    # When credentials are absent, the BoM models are silently skipped and the
    # dashboard runs as Open-Meteo only — same graceful pattern as OPENMETEO_API_KEY.
    BOM_API_USERNAME = os.getenv("BOM_API_USERNAME", "")
    BOM_API_PASSWORD = os.getenv("BOM_API_PASSWORD", "")
    BOM_API_BASE_URL = os.getenv("BOM_API_BASE_URL", "https://api.bsc.bom.gov.au")

    # PostHog analytics (optional — app runs fine without it).
    # Write-only public ingest key; safe to ship in frontend. Leave blank to
    # disable analytics entirely. US Cloud is the only supported host for the
    # trial — if BoM governance asks for AU-hosted, flip POSTHOG_HOST to
    # 'https://eu.i.posthog.com' (EU Cloud) and re-init the project there.
    POSTHOG_PROJECT_API_KEY = os.getenv("POSTHOG_PROJECT_API_KEY", "")
    POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")

    # Build identifier attached as a super-property to every PostHog event.
    # In prod, set to the short git SHA via a Docker build-arg so we can slice
    # telemetry by release; locally, it stays "dev".
    APP_VERSION = os.getenv("APP_VERSION", "dev")


def get_config() -> Config:
    """Return the application configuration."""
    return Config()
