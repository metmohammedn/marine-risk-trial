# Marine Weather Risk — Demo

Bureau of Meteorology · Energy & Resources Sector · Capabilities trial.

A six-month AWS-hosted demo of marine weather-risk tooling for offshore
operations, shared with prospective clients during the evaluation period. No
client names or facility coordinates — the four sites below are generic
Western Australian locations chosen to demonstrate capability against both
coastal and offshore conditions.

**Not a product.** This is a marketing / evaluation instance; the per-client
production build lives in a separate repository.

## What it demonstrates

- **Multi-model wind ensemble forecasts** — ECMWF IFS (51 members), GFS
  (31), ICON (40), BoM ACCESS-G (deterministic), ACCESS-GE (18 members), plus
  two AI ensembles (AIFS, AIGFS) for comparison. Exceedance probability +
  model agreement scoring.
- **Wind gust analysis** — multi-model comparison with Beaufort reference
  lines (Gale 34 kn, Storm 48 kn).
- **Wave height and period forecasts** — ECMWF WAM / GFS Wave / GWAM with
  sea-state classification (Choppy / Standard / Swell).
- **Optimal windows** — operator-defined wind / gust / wave thresholds
  generate P90-wind, P100-gust time bands across chosen ensemble models.
- **IMOS wave buoy observations** — nine stations overlaid on forecasts,
  auto-matched to site by proximity.
- **Interactive map** — offshore platforms, buoys, and land stations with
  click-to-select.
- **Timezone selector** — all charts convert from UTC at render time.
- **Exports** — interactive HTML, CSV, and PDF reports.

## Trial sites

| Site | Lat | Lon | Type |
|---|---|---|---|
| Broome | -17.96 | 122.24 | Land |
| Karratha | -20.74 | 116.85 | Land |
| Browse Basin North | -13.50 | 123.00 | Offshore |
| Browse Basin South | -15.00 | 122.50 | Offshore |

Plus nine IMOS wave buoy stations (Rottnest, Cottesloe, Mandurah, Jurien,
Cape Naturaliste, Albany, Esperance, Dampier, Port-Hedland) as shared
reference data.

## Quick start

```bash
# One-time setup
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# Run
venv/bin/python app.py
# http://localhost:8050
```

## Docker

```bash
docker compose up           # App + Redis sidecar
# http://localhost:8050
```

## Configuration

Copy `.env.example` to `.env` and fill in whatever you want enabled. All keys
are optional — the app degrades gracefully when any is absent.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8050` | App port |
| `DEBUG` | `false` | Dash dev mode |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis cache (optional). Use `127.0.0.1`, not `localhost`, to avoid IPv6 resolution stalls on macOS when Redis is down. |
| `OPENMETEO_API_KEY` | _(empty)_ | Commercial Open-Meteo key. Blank uses free tier. |
| `BOM_API_USERNAME` / `BOM_API_PASSWORD` | _(empty)_ | BoM ACCESS-G / GE credentials. Blank skips ACCESS models. |
| `POSTHOG_PROJECT_API_KEY` | _(empty)_ | PostHog public ingest key. Blank disables analytics entirely. |
| `POSTHOG_HOST` | `https://us.i.posthog.com` | PostHog host URL. |
| `APP_VERSION` | `dev` | Git short SHA in prod. Attached as a super-property to every PostHog event. |
| `DATA_SOURCE` | `open-meteo` | Reserved for future data-provider switching. |

## Analytics

PostHog US Cloud captures product analytics + session replay for the trial
period. Seven custom events instrumented via Dash clientside callbacks
(site changes, threshold adjustments, tab switches, etc.) plus autocapture
for pageviews and clicks. No personally-identifying information collected —
the trial has no user accounts.

Data retention on Free tier: **events 12 months · session recordings 30
days (rolling)**. See `CLAUDE.md` → *Analytics* for the full event
taxonomy.

## Project structure

```
marine-risk-trial/
├── app.py                     # Dash app entry, header + PostHog injection
├── config.py                  # Env-driven config
├── Dockerfile                 # Python 3.12-slim + gunicorn
├── docker-compose.yml         # App + Redis sidecar (local)
├── docker-compose.prod.yml    # Prod-like local testing
├── requirements.txt
├── .env.example
├── assets/
│   ├── robots.txt             # Disallow: / — belt-and-braces with noindex meta
│   └── styles/                # Dark theme CSS
├── src/
│   ├── pages/
│   │   └── marine.py          # Page layout + server callbacks + PostHog clientside callbacks
│   ├── services/
│   │   ├── marine_service.py  # Wind/wave fetching + analysis + optimal windows
│   │   ├── buoy_service.py    # IMOS wave buoy observations (AODN S3)
│   │   └── export_service.py  # CSV / PDF / interactive-HTML exports
│   ├── components/
│   │   ├── marine_charts.py   # Plotly chart factories
│   │   └── map_components.py  # Leaflet map builder
│   ├── data/
│   │   ├── api_client.py      # Open-Meteo async HTTP client
│   │   ├── bom_api_client.py  # BoM ACCESS adapter
│   │   └── cache.py           # Redis wrapper
│   └── utils/
│       └── constants.py       # TRIAL_SITES, IMOS buoys, models, thresholds, styling
├── docs/
│   └── DEPLOYMENT.md          # AWS Beanstalk + Cognito PoC notes
└── tests/
```

## Data sources

| Source | Data | Notes |
|---|---|---|
| [Open-Meteo Ensemble API](https://open-meteo.com/en/docs/ensemble-api) | Wind speed + gust ensemble members | ECMWF IFS, GFS, ICON, AIFS, AIGFS |
| [Open-Meteo Marine API](https://open-meteo.com/en/docs/marine-weather-api) | Wave height + wave period | ECMWF WAM, GFS Wave, GWAM |
| [BoM Weather API](https://api.bsc.bom.gov.au/) | ACCESS-G / ACCESS-GE ensembles | Credentialed; deterministic + ensemble wind |
| [AODN Cloud Optimised (S3)](https://aodn.org.au/) | Wave buoy observations | IMOS network, public bucket |

## Health check

`GET /health` returns `{"status": "healthy"}` for ALB health probes.
`GET /robots.txt` returns `User-agent: * / Disallow: /` so crawlers stay
away even if a URL leaks. `<meta name="robots" content="noindex, nofollow">`
is set in the `<head>` as a second layer.

## Tech stack

Python 3.12 · Dash 4.0 · Dash Mantine Components · Plotly · Dash Leaflet
· httpx (async) · Redis · gunicorn · Docker · PostHog (US Cloud).

## Trial end-of-life

- **Deploy:** initial AWS Beanstalk rollout (see `docs/DEPLOYMENT.md`).
- **Runs:** 6 months from deploy date.
- **At EOL:** export PostHog event data / insights (the 12-month retention
  covers the full trial + buffer); tear down the Beanstalk environment or
  scale to zero; archive this repo. Session recordings auto-expire at 30
  days on rolling basis, so no end-of-trial export needed there.
- **If the trial leads to a paid product:** the next build uses Cognito +
  DynamoDB for auth (see `../marine-standalone/` for the per-client scoping
  pattern).

## Licence

Bureau of Meteorology — internal / evaluation use only.
