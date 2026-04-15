"""
health_checker.py — Background Agent Health Monitor  (U-09 / U-12)
-------------------------------------------------------------------
Periodically probes all registered agents that have an HTTP endpoint
configured, updating ``health_status`` and ``last_heartbeat`` in the
registry.

Configuration (environment variables)
--------------------------------------
AGENT_HEALTH_INTERVAL   Seconds between full sweeps. Default: 60
AGENT_HEALTH_TIMEOUT    Seconds per HTTP probe.      Default: 5
AGENT_UNHEALTHY_THRESHOLD  Consecutive failures before marking unhealthy.
                            Default: 3

Usage (wired in backend/main.py lifespan)::

    checker = AgentHealthChecker(get_registry())
    task = asyncio.create_task(checker.run())
    yield
    task.cancel()
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Dict

logger = logging.getLogger(__name__)


class AgentHealthChecker:
    """Periodic background task that health-checks all registered agents
    with an HTTP endpoint.

    Agents without an ``endpoint`` are skipped (status remains 'unknown').
    """

    def __init__(self, registry) -> None:
        self._registry = registry
        self._interval: int = int(os.environ.get("AGENT_HEALTH_INTERVAL", "60"))
        self._timeout: int = int(os.environ.get("AGENT_HEALTH_TIMEOUT", "5"))
        self._threshold: int = int(os.environ.get("AGENT_UNHEALTHY_THRESHOLD", "3"))
        self._failure_counts: Dict[str, int] = defaultdict(int)

    async def run(self) -> None:
        """Run the health check sweep in a loop until cancelled."""
        logger.info(
            f"AgentHealthChecker: starting (interval={self._interval}s, "
            f"timeout={self._timeout}s, threshold={self._threshold})"
        )
        while True:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                logger.info("AgentHealthChecker: cancelled — stopping.")
                break
            except Exception as exc:
                logger.error(f"AgentHealthChecker: sweep error ({exc}).")
            await asyncio.sleep(self._interval)

    async def _sweep(self) -> None:
        """Check all agents with endpoints in parallel."""
        agents = self._registry.list_all(include_inactive=False)
        tasks_agents = [a for a in agents if a.get("endpoint")]

        if not tasks_agents:
            return

        logger.debug(f"AgentHealthChecker: probing {len(tasks_agents)} agent(s).")
        await asyncio.gather(
            *[self._probe(agent) for agent in tasks_agents],
            return_exceptions=True,
        )

    async def _probe(self, agent: dict) -> None:
        """Probe a single agent endpoint and update its health status."""
        agent_id = agent["id"]
        endpoint = agent.get("endpoint", "")
        probe_url = endpoint if endpoint.endswith("/health") else f"{endpoint}/health"

        try:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(probe_url)
            is_healthy = resp.status_code < 400
        except Exception as exc:
            logger.debug(f"AgentHealthChecker: probe failed for '{agent_id}' ({exc}).")
            is_healthy = False

        if is_healthy:
            self._failure_counts[agent_id] = 0
            self._registry.update_health(agent_id, "healthy")
            logger.debug(f"AgentHealthChecker: '{agent_id}' is healthy.")
        else:
            self._failure_counts[agent_id] += 1
            count = self._failure_counts[agent_id]
            if count >= self._threshold:
                self._registry.update_health(agent_id, "unhealthy")
                logger.warning(
                    f"AgentHealthChecker: '{agent_id}' marked unhealthy "
                    f"({count} consecutive failures)."
                )
