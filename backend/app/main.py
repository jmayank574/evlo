"""Voltpath FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="Voltpath API",
    description="EV-aware load feasibility and charging planner.",
    version="0.1.0",
)

# Dev + deployed frontends. Tighten allow_origins in production if desired.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root() -> dict:
    return {"service": "voltpath", "docs": "/docs", "health": "/api/health"}
