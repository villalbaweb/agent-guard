"""
tests/test_health_checker.py — AgentHealthChecker unit tests
--------------------------------------------------------------
All tests use mocked HTTP responses — no live agents or Postgres needed.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def make_registry(agents: list) -> MagicMock:
    """Return a mock Registry that yields the given agents list."""
    reg = MagicMock()
    reg.list_all.return_value = agents
    reg.update_health = MagicMock()
    return reg


def make_checker(registry, interval: int = 1, timeout: int = 2, threshold: int = 2):
    import os
    with patch.dict(os.environ, {
        "AGENT_HEALTH_INTERVAL": str(interval),
        "AGENT_HEALTH_TIMEOUT": str(timeout),
        "AGENT_UNHEALTHY_THRESHOLD": str(threshold),
    }):
        from agentguard.health_checker import AgentHealthChecker
        return AgentHealthChecker(registry)


# --------------------------------------------------------------------------- #
#  Tests                                                                       #
# --------------------------------------------------------------------------- #

class TestAgentHealthChecker:
    @pytest.mark.asyncio
    async def test_healthy_endpoint_marks_healthy(self):
        """An agent whose /health responds 200 is marked healthy."""
        agents = [{"id": "agent_01", "endpoint": "http://agent01:8000"}]
        registry = make_registry(agents)
        checker = make_checker(registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await checker._sweep()

        registry.update_health.assert_called_with("agent_01", "healthy")

    @pytest.mark.asyncio
    async def test_unhealthy_endpoint_increments_counter(self):
        """An agent whose endpoint returns 500 increments the failure counter."""
        agents = [{"id": "agent_02", "endpoint": "http://agent02:8000"}]
        registry = make_registry(agents)
        checker = make_checker(registry, threshold=3)

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await checker._sweep()  # 1st failure — below threshold=3

        # Not yet unhealthy after one failure
        for call in registry.update_health.call_args_list:
            assert call[0][1] != "unhealthy", "Should not be marked unhealthy yet"

    @pytest.mark.asyncio
    async def test_marks_unhealthy_after_threshold(self):
        """After threshold consecutive failures, the agent is marked unhealthy."""
        agents = [{"id": "agent_03", "endpoint": "http://agent03:8000"}]
        registry = make_registry(agents)
        threshold = 2
        checker = make_checker(registry, threshold=threshold)

        mock_resp = MagicMock()
        mock_resp.status_code = 503

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            for _ in range(threshold):
                await checker._sweep()

        # After `threshold` failures, update_health should have been called with 'unhealthy'
        unhealthy_calls = [
            c for c in registry.update_health.call_args_list
            if c[0][1] == "unhealthy"
        ]
        assert len(unhealthy_calls) >= 1, "Expected at least one 'unhealthy' call after threshold"

    @pytest.mark.asyncio
    async def test_skips_agents_without_endpoint(self):
        """Agents without an endpoint are skipped (no HTTP call, no health update)."""
        agents = [{"id": "agent_04", "endpoint": ""}]
        registry = make_registry(agents)
        checker = make_checker(registry)

        with patch("httpx.AsyncClient") as mock_httpx:
            await checker._sweep()
            mock_httpx.assert_not_called()

        registry.update_health.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_error_counts_as_failure(self):
        """A connection error on the probe counts as a failure."""
        agents = [{"id": "agent_05", "endpoint": "http://dead:9999"}]
        registry = make_registry(agents)
        checker = make_checker(registry, threshold=1)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            await checker._sweep()

        # After threshold=1 failure, must be unhealthy
        registry.update_health.assert_called_with("agent_05", "unhealthy")

    @pytest.mark.asyncio
    async def test_recovery_after_healthy(self):
        """After a healthy probe, failure count resets so a single failure
        no longer crosses the threshold (threshold=2)."""
        agents = [{"id": "agent_06", "endpoint": "http://agent06:8080"}]
        registry = make_registry(agents)
        threshold = 2
        checker = make_checker(registry, threshold=threshold)

        ok_resp = MagicMock(status_code=200)
        fail_resp = MagicMock(status_code=500)

        ok_client = AsyncMock()
        ok_client.__aenter__ = AsyncMock(return_value=ok_client)
        ok_client.__aexit__ = AsyncMock(return_value=False)
        ok_client.get = AsyncMock(return_value=ok_resp)

        fail_client = AsyncMock()
        fail_client.__aenter__ = AsyncMock(return_value=fail_client)
        fail_client.__aexit__ = AsyncMock(return_value=False)
        fail_client.get = AsyncMock(return_value=fail_resp)

        # 1 failure, then 1 success (resets counter), then 1 failure
        with patch("httpx.AsyncClient", return_value=fail_client):
            await checker._sweep()  # failure #1
        with patch("httpx.AsyncClient", return_value=ok_client):
            await checker._sweep()  # recovery — resets to 0
        with patch("httpx.AsyncClient", return_value=fail_client):
            await checker._sweep()  # failure #1 again (below threshold=2)

        # Should NOT have been marked unhealthy at this point
        unhealthy_calls = [
            c for c in registry.update_health.call_args_list
            if c[0][1] == "unhealthy"
        ]
        assert len(unhealthy_calls) == 0, "Should not be unhealthy after reset"

    @pytest.mark.asyncio
    async def test_sweep_with_no_agents(self):
        """Sweep over an empty registry must not raise."""
        registry = make_registry([])
        checker = make_checker(registry)
        await checker._sweep()  # should not raise
