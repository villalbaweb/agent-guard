"""
mcp_proxy.py — MCP Safety Middleware
--------------------------------------
Intercepts MCP tool calls and passes them through PolicyEngine.check_step()
before execution.

This closes the governance gap for agents whose tool calls are entirely
MCP-based: without this proxy, AgentGuard is blind to those calls.

Usage (async context):
    from agentguard.mcp_proxy import MCPSafetyProxy

    proxy = MCPSafetyProxy(policy_engine=executor.policy, state=agent_state)

    # Wrap any async MCP call:
    result = await proxy.call_tool(
        tool_name="web_search",
        arguments={"query": "solar energy trends"},
        tool_fn=my_mcp_session.call_tool,   # any async callable
        cost_estimate=0.01,
    )

    # Dry-run (policy check only, no actual call):
    result = await proxy.call_tool("web_search", {"query": "..."})

MCP framework compatibility:
    The proxy is framework-agnostic. Pass any async callable as `tool_fn`:
    - Official MCP Python SDK: mcp.ClientSession.call_tool
    - LangChain MCP adapter: tool.ainvoke
    - Custom HTTP client: any coroutine returning the tool result

Raises:
    MCPCallBlocked:      policy hard-blocked the call.
    MCPCallHITLRequired: policy requires human approval before proceeding.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class MCPSafetyProxy:
    """Policy interception layer for MCP tool calls.

    Every call to `call_tool()` is validated by PolicyEngine.check_step()
    before the underlying MCP function is invoked.  Trace events are emitted
    automatically so every MCP call appears in the causal graph.
    """

    def __init__(self, policy_engine, state: Dict[str, Any]):
        """
        Args:
            policy_engine: A PolicyEngine instance (or any object with
                           .check_step(state, action, intent, cost_estimate)).
            state:         The current AgentGuardState dict — used for budget
                           tracking, auth context, and policy selection.
        """
        self.policy = policy_engine
        self.state = state

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_fn: Optional[Callable[..., Awaitable[Any]]] = None,
        cost_estimate: float = 0.0,
    ) -> Any:
        """Check policy then call the MCP tool.

        Args:
            tool_name:     MCP tool identifier used as the policy action string.
            arguments:     Tool arguments — serialized into the policy intent.
            tool_fn:       Async callable that performs the actual MCP call.
                           Signature: (tool_name: str, arguments: dict) -> Any.
                           Pass None for a dry-run policy check with no execution.
            cost_estimate: Estimated USD cost for budget enforcement.

        Returns:
            The tool's return value, or a dry-run dict if tool_fn is None.

        Raises:
            MCPCallBlocked:      if policy blocks the call.
            MCPCallHITLRequired: if policy requires human approval.
        """
        action = f"mcp:{tool_name}"
        intent = f"MCP tool '{tool_name}' with args: {_summarize(arguments)}"

        allowed, decision = self.policy.check_step(
            state=self.state,
            action=action,
            intent=intent,
            cost_estimate=cost_estimate,
        )

        if not allowed:
            if decision.get("hitl_required"):
                logger.info(f"MCPSafetyProxy: HITL required for {action}")
                raise MCPCallHITLRequired(
                    tool_name=tool_name,
                    reason=decision.get("reason", "HITL required."),
                    decision=decision,
                )
            logger.info(f"MCPSafetyProxy: BLOCKED {action} — {decision.get('reason')}")
            raise MCPCallBlocked(
                tool_name=tool_name,
                reason=decision.get("reason", "Call blocked by policy."),
                decision=decision,
            )

        logger.info(f"MCPSafetyProxy: ALLOW {action}")

        if tool_fn is None:
            return {"status": "allowed", "tool": tool_name, "dry_run": True, "decision": decision}

        return await tool_fn(tool_name, arguments)

    def call_tool_sync(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_fn: Optional[Callable[..., Any]] = None,
        cost_estimate: float = 0.0,
    ) -> Any:
        """Synchronous variant for non-async contexts.

        Same semantics as call_tool() but calls tool_fn synchronously.
        """
        action = f"mcp:{tool_name}"
        intent = f"MCP tool '{tool_name}' with args: {_summarize(arguments)}"

        allowed, decision = self.policy.check_step(
            state=self.state,
            action=action,
            intent=intent,
            cost_estimate=cost_estimate,
        )

        if not allowed:
            if decision.get("hitl_required"):
                raise MCPCallHITLRequired(tool_name=tool_name, reason=decision.get("reason"), decision=decision)
            raise MCPCallBlocked(tool_name=tool_name, reason=decision.get("reason"), decision=decision)

        logger.info(f"MCPSafetyProxy: ALLOW {action}")

        if tool_fn is None:
            return {"status": "allowed", "tool": tool_name, "dry_run": True, "decision": decision}

        return tool_fn(tool_name, arguments)


# --------------------------------------------------------------------------- #
#  Exceptions                                                                  #
# --------------------------------------------------------------------------- #

class MCPCallBlocked(Exception):
    """Raised when a MCP tool call is hard-blocked by policy."""
    def __init__(self, tool_name: str, reason: str, decision: Any = None):
        super().__init__(f"MCP call '{tool_name}' blocked: {reason}")
        self.tool_name = tool_name
        self.reason = reason
        self.decision = decision


class MCPCallHITLRequired(Exception):
    """Raised when a MCP tool call requires human-in-the-loop approval."""
    def __init__(self, tool_name: str, reason: str, decision: Any = None):
        super().__init__(f"MCP call '{tool_name}' requires HITL approval: {reason}")
        self.tool_name = tool_name
        self.reason = reason
        self.decision = decision


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _summarize(arguments: Dict[str, Any], max_len: int = 200) -> str:
    try:
        s = json.dumps(arguments)
        return s[:max_len] + "..." if len(s) > max_len else s
    except Exception:
        return str(arguments)[:max_len]
