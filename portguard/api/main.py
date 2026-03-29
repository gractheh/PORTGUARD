"""PORTGUARD FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from portguard.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    # Future: could warm up model connections here
    yield
    # Future: cleanup resources here


app = FastAPI(
    title="PORTGUARD Trade Compliance API",
    description=(
        "Multi-agent pipeline for US import compliance screening. "
        "Checks HTS classification, Section 301/232 tariffs, AD/CVD orders, "
        "OFAC sanctions, and ISF/PGA requirements."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api/v1")
