"""GET /health — Redis + LLM provider liveness probe."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from agentguard.memory import MemoryManager
from agentguard.llm import get_llm
from ..dependencies import get_memory
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health_check(memory: Annotated[MemoryManager, Depends(get_memory)]) -> HealthResponse:
    """Check Redis connectivity and LLM provider availability."""
    # Redis probe
    redis_status = "ok"
    try:
        if memory._redis:
            memory._redis.ping()
        else:
            redis_status = "unavailable"
    except Exception:
        redis_status = "unavailable"

    # LLM probe
    llm_status = "ok" if get_llm() is not None else "unavailable"

    overall = "ok" if redis_status == "ok" else "degraded"

    return HealthResponse(
        status=overall,
        redis=redis_status,
        llm=llm_status,
        timestamp=datetime.now(timezone.utc),
    )
