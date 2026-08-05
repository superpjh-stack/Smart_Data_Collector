import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cloud_api.db import close_pool, init_pool
from cloud_api.routers import alarms, config, ingestion, raw_data, readings, status


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="Smart Data Collector — Cloud Ingestion API", lifespan=lifespan)
app.include_router(ingestion.router)
app.include_router(config.router)
app.include_router(status.router)
app.include_router(readings.router)
app.include_router(alarms.router)
app.include_router(raw_data.router)

# The dashboard is a separate frontend project (its own origin/port in dev);
# edge collectors never hit these GET routes from a browser, so CORS only
# needs to cover the dashboard's known origin(s), not "*".
_dashboard_origins = os.environ.get("DASHBOARD_ORIGINS", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _dashboard_origins.split(",") if o.strip()],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
