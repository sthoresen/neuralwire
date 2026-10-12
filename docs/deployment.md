# Deployment

NeuralWire runs as three services and a database in one Railway project, plus the Next.js
frontend on Vercel. Every Railway service builds the same image from the repo's `Dockerfile`,
the one CI and `docker compose` use, and they differ only in their start command.

Railway's settings live in each service's dashboard (Settings), not in the repo. This page
documents them, so keep it in sync by hand when you change something there.

## Build

Each app service has **Dockerfile Path** set to `Dockerfile`. Railway would also pick up a
`Dockerfile` at the repo root on its own, but the explicit path keeps the builds from ever
falling back to Railway's default builder.

Start commands run **without a shell** when building from a Dockerfile, so they can't use
`$VARIABLES`. That's why `web` has no start command and uses the Dockerfile's `CMD`, which reads
`$PORT` itself.

## Services

| Service | Start command | What it does |
|---|---|---|
| `web` | none (the Dockerfile's `CMD`: uvicorn on `$PORT`) | Always on. Serves the FastAPI read API to the frontend. Healthcheck path `/health`, set on this service only: the workers serve no HTTP, so their deploys would fail on a healthcheck. |
| `neuralwire` | `python scheduler.py` | Always on. The APScheduler process that owns every periodic job and runs each one as a subprocess. |
| `analysis-loop` | `python run_analysis.py` | Always on, and deliberately separate from `neuralwire`: the analysis loop is the component that has actually crashed, and isolating it keeps the scheduled jobs running when it does. It paces itself (small batches, sleeping only when the queue is drained). Don't put it on a cron: as a 30-minute cron it outlived its own schedule, and every re-fire was dropped with "maximum number of running instances reached". |
| `Postgres` | Railway Postgres plugin with `postgres-volume` | Grow the volume in its Settings tab; billing is on data stored, not on provisioned size. |

## Scheduled jobs

The schedules live in [`scheduler.py`](../scheduler.py), not in Railway cron.

| Job | When |
|---|---|
| `run_fetch.py` | every 2 hours |
| `run_earnings.py` | 06:00 UTC daily |
| `run_events.py` | every 4 hours |
| `run_artefacts.py` | 05:00 UTC daily |
| `run_sync_daily_prices.py` | 22:00 UTC on weekdays, after the NYSE close |
| `run_sync_intraday.py` | 21:15 UTC on weekdays, 15 minutes after the close |
| `run_x_refresh.py` | weekdays at 9:35, 12:45 and 16:05 New York time (16:05 adds `--weekly`); weekends at 12:00 with `--weekly`. Only when `X_PIPELINE_ENABLED=1`, since each run spends a little on Apify and LLMs. |

## Environment variables

Every app service needs `DATABASE_URL`, `OPENROUTER_KEY`, `XAI_API_KEY`, `FINNHUB_API_KEY`,
`ALPACA_KEY` and `ALPACA_SECRET`. The news fetcher also maps `POLYGON_API_KEY` and
`ALPHA_VANTAGE_API_KEY` for its other providers.

The X pipeline (`neuralwire` only) also needs `APIFY_TOKEN` and `X_PIPELINE_ENABLED=1`.
`X_BEARER_TOKEN` is optional: it only powers the free coverage check against X's own post counts.

`web` reads `FRONTEND_URL` (the allowed CORS origin) and, optionally, `FRONTEND_PREVIEW_REGEX`
for Vercel preview deployments.
