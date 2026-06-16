"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import ai, auth, channels, dm, messages, shipments, ws

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    "http://localhost:3000",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Hemut Logistics API starting up")
    yield
    logger.info("Hemut Logistics API shutting down")


app = FastAPI(
    title="Hemut Logistics API",
    description="Real-time collaboration platform for logistics teams",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(channels.router, prefix="/api/channels", tags=["channels"])
app.include_router(messages.router, prefix="/api", tags=["messages"])
app.include_router(ws.router, prefix="/api", tags=["websocket"])
app.include_router(dm.router, prefix="/api/dm", tags=["dm"])
app.include_router(ai.router, prefix="/api", tags=["ai"])
app.include_router(shipments.router, prefix="/api/shipments", tags=["shipments"])


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Health check used by load balancers and local verification."""
    return {"status": "ok"}
