"""
Marine Weather Risk — Demo (marine-risk-trial).
Capabilities trial app for the BoM Energy & Resources Sector.
Run with: python app.py
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


# PostHog snippet injected into Dash's index template when an API key is set.
# Placeholders (__POSTHOG_KEY__, __POSTHOG_HOST__, __APP_VERSION__,
# __ENVIRONMENT__) are substituted in create_app(); the {%…%} tokens are
# Dash's own placeholders and stay literal for Dash to render.
_POSTHOG_INDEX_TEMPLATE = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <script>
!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once unregister identify alias people.set people.set_once set_config reset opt_out_capturing has_opted_out_capturing opt_in_capturing get_distinct_id get_property getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags onFeatureFlags group updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures getActiveMatchingSurveys getSurveys onSessionId startSessionRecording stopSessionRecording".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
posthog.init("__POSTHOG_KEY__", {
    api_host: "__POSTHOG_HOST__",
    person_profiles: "identified_only"
});
posthog.register({
    app_version: "__APP_VERSION__",
    environment: "__ENVIRONMENT__",
    browser_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
});
        </script>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""


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

    # ── PostHog analytics (optional — app runs fine without it) ──────────
    # Injected via index_string override so the snippet loads in <head> before
    # the Dash app hydrates. When POSTHOG_PROJECT_API_KEY is blank we leave
    # Dash's default index template alone, and no analytics script is served.
    posthog_key = config.POSTHOG_PROJECT_API_KEY
    if posthog_key:
        env_name = "development" if config.DEBUG else "production"
        app.index_string = _POSTHOG_INDEX_TEMPLATE \
            .replace("__POSTHOG_KEY__", posthog_key) \
            .replace("__POSTHOG_HOST__", config.POSTHOG_HOST) \
            .replace("__APP_VERSION__", config.APP_VERSION) \
            .replace("__ENVIRONMENT__", env_name)
        logger.info("PostHog analytics enabled (env=%s, version=%s)",
                    env_name, config.APP_VERSION)
    else:
        logger.info("PostHog analytics disabled (no POSTHOG_PROJECT_API_KEY set)")

    # ── Initialize services ──────────────────────────────────────────────

    # Redis cache (optional — app runs fine without it)
    try:
        from src.data.cache import init_cache
        init_cache(config.REDIS_URL)
        logger.info("Redis cache initialized")
    except Exception as e:
        logger.warning("Redis cache unavailable (app will run without caching): %s", e)

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

    # BoM Weather API client (ACCESS-G + ACCESS-GE wind models).
    # Optional — when credentials are absent, the BoM models are silently
    # skipped at fetch time and the dashboard runs as Open-Meteo only.
    if config.BOM_API_USERNAME and config.BOM_API_PASSWORD:
        try:
            from src.data.bom_api_client import init_bom_client
            init_bom_client(
                username=config.BOM_API_USERNAME,
                password=config.BOM_API_PASSWORD,
                base_url=config.BOM_API_BASE_URL,
            )
            logger.info("BoM API client initialized (ACCESS-G, ACCESS-GE)")
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
