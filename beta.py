"""
Marine Weather Risk — Demo (marine-risk-trial) — AWS BETA build.

Sibling of app.py. Stripped of Redis caching and PostHog analytics per the
2026-05-01 PoC decisions, and routed through the native-async BoM client so
the call path is end-to-end asyncio (no thread-pool executor anywhere).

Run with: python beta.py  (or: gunicorn beta:server)
"""
import logging
import os
import sys

import dash
from dash import Dash, dcc, html
import dash_mantine_components as dmc

# Ensure src is importable
sys.path.insert(0, os.path.dirname(__file__))

from config import get_config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Dash:
    """Create and configure the Marine Weather Risk demo app."""
    config = get_config()

    # Pre-import component libraries so Dash registers them before callbacks fire
    import dash_leaflet  # noqa: F401 — used by map_components
    import dash_iconify  # noqa: F401 — used by marine.py layout

    # Create Dash app with multi-page support.
    # meta_tags populate the {%metas%} slot in both Dash's default index
    # template and our PostHog override — noindex applies regardless of
    # analytics state.
    app = Dash(
        __name__,
        use_pages=True,
        pages_folder=os.path.join(os.path.dirname(__file__), "src", "pages"),
        suppress_callback_exceptions=True,
        title=config.APP_NAME,
        update_title="Loading...",
        meta_tags=[
            {"name": "robots", "content": "noindex, nofollow"},
        ],
        external_stylesheets=[
            "https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600&display=swap",
        ],
    )

    server = app.server

    # ── Initialize services ──────────────────────────────────────────────
    # PostHog and Redis are intentionally not initialized in this build —
    # see app.py for the side-by-side reference build that uses them.

    # Open-Meteo API client
    try:
        from src.data.api_client import init_api_client
        init_api_client(
            forecast_url=config.OPENMETEO_FORECAST_URL,
            ensemble_url=config.OPENMETEO_ENSEMBLE_URL,
            marine_url=config.OPENMETEO_MARINE_URL,
            api_key=config.OPENMETEO_API_KEY,
            timeout=config.API_TIMEOUT,
            max_connections=config.API_MAX_CONNECTIONS,
        )
        logger.info("API client initialized")
    except Exception as e:
        logger.warning("API client initialization failed: %s", e)

    # BoM Weather API client (ACCESS-G + ACCESS-GE wind models) —
    # native-async variant: httpx.AsyncClient end-to-end, direct Cognito
    # auth (no boto3), no thread-pool executor on the BoM path.
    if config.BOM_API_USERNAME and config.BOM_API_PASSWORD:
        try:
            from src.data.bom_api_client_async import init_bom_async_client
            init_bom_async_client(
                username=config.BOM_API_USERNAME,
                password=config.BOM_API_PASSWORD,
                base_url=config.BOM_API_BASE_URL,
            )
            logger.info("BoM API client initialized — async (ACCESS-G, ACCESS-GE)")
        except Exception as e:
            logger.warning("BoM API client initialization failed: %s", e)
    else:
        logger.info("BoM API disabled (no BOM_API_USERNAME / BOM_API_PASSWORD set)")

    # ── Dark theme (matches main dashboard) ──────────────────────────────
    dark_theme = dmc.DEFAULT_THEME.copy()
    dark_theme.update({
        "colorScheme": "dark",
        "primaryColor": "orange",
        "fontFamily": "DM Sans, sans-serif",
        "fontFamilyMonospace": "JetBrains Mono, monospace",
        "headings": {"fontFamily": "DM Sans, sans-serif"},
        "colors": {
            "dark": [
                "#C1C2C5", "#A6A7AB", "#909296", "#5c5f66",
                "#373A40", "#2C2E33", "#1e293b", "#111827",
                "#0d1320", "#080c14",
            ],
        },
    })

    # ── App layout ───────────────────────────────────────────────────────
    app.layout = dmc.MantineProvider(
        theme=dark_theme,
        forceColorScheme="dark",
        children=html.Div(
            style={
                "backgroundColor": "#080c14",
                "minHeight": "100vh",
            },
            children=[
                dcc.Location(id="url"),

                # BETA banner — thin strip pinned above the title header.
                # Required for the trial per the 2026-05-01 PoC decisions.
                dmc.Paper(
                    p="xs",
                    radius=0,
                    style={
                        "backgroundColor": "#fbbf24",
                        "color": "#1e293b",
                        "textAlign": "center",
                        "fontWeight": 600,
                        "fontSize": "13px",
                        "letterSpacing": "0.02em",
                    },
                    children="BETA — capabilities trial for evaluation. Not for operational use.",
                ),

                # Header — title + BoM attribution subtitle.
                # "LIVE DATA" badge stays because it refers to the forecast
                # data feed being real-time, not to product status.
                dmc.Paper(
                    p="sm",
                    style={
                        "backgroundColor": "#0d1320",
                        "borderBottom": "1px solid #1e293b",
                    },
                    children=dmc.Stack(
                        gap=2,
                        children=[
                            dmc.Group(
                                gap="sm",
                                children=[
                                    dmc.Text(
                                        "⚓ Marine Weather Risk — Demo",
                                        size="lg", fw=700, c="white",
                                    ),
                                    dmc.Badge(
                                        "LIVE DATA",
                                        color="green", variant="dot", size="sm",
                                    ),
                                ],
                            ),
                            dmc.Text(
                                "Bureau of Meteorology · Energy & Resources Sector · Capabilities trial",
                                size="xs", c="dimmed",
                            ),
                        ],
                    ),
                ),
                # Page content
                html.Div(
                    style={"padding": "0"},
                    children=dash.page_container,
                ),
            ],
        ),
    )

    # ── Security headers ───────────────────────────────────────────────────
    @server.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if not config.DEBUG:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    # ── Health check endpoint (for Docker / ALB) ─────────────────────────
    @server.route("/health")
    def health():
        return {"status": "healthy"}

    # ── /robots.txt at the standard root path ────────────────────────────
    # Dash auto-serves static files under /assets/* so assets/robots.txt
    # alone would land at /assets/robots.txt, not where crawlers look. This
    # route forwards /robots.txt to the same file so crawlers see it.
    from flask import send_from_directory
    _assets_dir = os.path.join(os.path.dirname(__file__), "assets")

    @server.route("/robots.txt")
    def robots_txt():
        return send_from_directory(_assets_dir, "robots.txt", mimetype="text/plain")

    return app


# ── Entry point ──────────────────────────────────────────────────────────
app = create_app()
server = app.server  # For gunicorn: gunicorn app:server

if __name__ == "__main__":
    config = get_config()
    app.run(debug=config.DEBUG, host=config.HOST, port=config.PORT)
