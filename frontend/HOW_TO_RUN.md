# How to Run

## 0. Start the PostgreSQL database

The database runs in Docker. Start it once (it persists across restarts):

```bash
docker start stock-news-pg
```

If the container doesn't exist yet (first time setup):

```bash
docker run -d --name stock-news-pg \
  -e POSTGRES_USER=stocknews \
  -e POSTGRES_PASSWORD=stocknews \
  -e POSTGRES_DB=stocknews \
  -p 5432:5432 \
  postgres:16
```

## 1. Start the FastAPI backend

From the repo root (`stock-news/`):

```bash
python -m uvicorn api:app --reload --host 0.0.0.0
```

The API will be available at http://127.0.0.1:8000.
Interactive docs at http://127.0.0.1:8000/docs.

> Use `python -m uvicorn` (not bare `uvicorn`) to ensure the correct Python
> environment (miniconda base) is used.
> `--host 0.0.0.0` is required when running in WSL2 so the Windows browser can reach the API.

## 2. Start the Next.js frontend

From this directory (`frontend/`):

```bash
npm run dev
```

The app will be available at http://localhost:3000.

---

All three processes must be running at the same time.
