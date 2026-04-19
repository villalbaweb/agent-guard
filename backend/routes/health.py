"""GET /health — Redis + PostgreSQL + LLM provider liveness probe."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from agentguard.memory import MemoryManager
from agentguard.llm import get_llm
from ..dependencies import get_memory, get_database
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health_check(memory: Annotated[MemoryManager, Depends(get_memory)]) -> HealthResponse:
    """Check Redis, PostgreSQL, and LLM provider availability."""
    # Redis probe
    redis_status = "ok"
    try:
        if memory._redis:
            memory._redis.ping()
        else:
            redis_status = "unavailable"
    except Exception:
        redis_status = "unavailable"

    # PostgreSQL probe (U-09/U-12)
    db = get_database()
    postgres_status = "ok" if db.available else "unavailable"
    if db.available:
        try:
            db.execute_one("SELECT 1")
        except Exception:
            postgres_status = "unavailable"

    # LLM probe
    llm_status = "ok" if get_llm() is not None else "unavailable"

    # Overall status: degraded if any component is down
    if redis_status == "ok" and postgres_status == "ok":
        overall = "ok"
    elif redis_status == "unavailable" and postgres_status == "unavailable":
        overall = "unhealthy"
    else:
        overall = "degraded"

    return HealthResponse(
        status=overall,
        redis=redis_status,
        postgres=postgres_status,
        llm=llm_status,
        timestamp=datetime.now(timezone.utc),
    )
