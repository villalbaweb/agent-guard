"""
main.py — FastAPI application entry point
------------------------------------------
Thin facade wiring together all routes.  All orchestration logic lives in
agentguard/.

Usage:
  uv run uvicorn backend.main:app --reload --port 8000

Docs:
  http://localhost:8000/docs  (Swagger UI)
  http://localhost:8000/openapi.json
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import asyncio
from .routes import health, policy, runs, agents as agents_router
from .schemas import ErrorResponse, ErrorDetail

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AgentGuard API starting up...")
    allowed_origins = os.environ.get("ALLOWED_ORIGINS", "")
    if not allowed_origins or allowed_origins.strip() == "*":
        logger.warning(
            "SECURITY: ALLOWED_ORIGINS is not set or is '*' (all origins accepted). "
            "Set ALLOWED_ORIGINS to a comma-separated list of allowed origins before deploying. "
            "See .env.example for an example value."
        )
    # Optional background agent health checker (U-09/U-12)
    health_task = None
    try:
        from agentguard.health_checker import AgentHealthChecker
        from .dependencies import get_registry
        health_task = asyncio.create_task(AgentHealthChecker(get_registry()).run())
        logger.info("AgentGuard: health checker started.")
    except Exception as exc:
        logger.warning(f"AgentGuard: health checker could not start ({exc}).")
    yield
    if health_task is not None:
        health_task.cancel()
    logger.info("AgentGuard API shutting down.")


app = FastAPI(
    title="AgentGuard API",
    description=(
        "Policy-driven governance layer for agentic AI systems. "
        "Enforces cost budgets, content rules, and HITL approval workflows "
        "across recursive multi-step agent graphs."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# CORS — restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  Global error handler — consistent error envelope                           #
# --------------------------------------------------------------------------- #

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                code="internal_error",
                message="An unexpected error occurred.",
                details=str(exc),
            )
        ).model_dump(),
    )


# --------------------------------------------------------------------------- #
#  Routers                                                                     #
# --------------------------------------------------------------------------- #

app.include_router(health.router)
app.include_router(runs.router)
app.include_router(policy.router)
app.include_router(agents_router.router)
