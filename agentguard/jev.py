"""
Jev Decisions Client
--------------------
Thin client for TypeSafe's Jev model, served by OpenRouter's Decisions API.

Jev is not a chat model: it takes a `state` (text or JSON) plus named, typed
questions and returns typed answers with calibrated probabilities — no free
text. That makes it a drop-in upgrade for the one-word LLM prompts AgentGuard
uses at its decision points (safety guard, reflection, routing), and useless
for anything generative (task decomposition, synthesis).

Question primitives used here:
  noul   — {"type": "noul", "instructions": ...}
           -> {"type": "noul", "noul": <P(yes)>}
  choice — {"type": "choice", "instructions": ..., "criteria": {key: description}}
           -> {"type": "choice", "choice": key, "probabilities": {...}, "confidence": c}

All questions in one request are answered in parallel against the same state
and cannot see each other's answers.

Obtain a client via agentguard.llm.get_jev(use_case) rather than constructing
one directly — that is where the per-use-case opt-in lives.
"""
import json
import logging
from typing import Any, Dict, Optional, Tuple, Union

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "typesafe/jev-1.13"  # pinned: governance verdicts should be reproducible
DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"


class JevError(RuntimeError):
    """Raised when the Decisions API call fails or returns an unusable answer."""


class JevClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        url: str = DEFAULT_URL,
        timeout: float = 10.0,
    ):
        self.model = model
        self.url = url
        # One pooled client: the guard calls Jev on every step.
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/villalbaweb/agent-guard",
                "X-Title": "AgentGuard",
            },
        )

    def __repr__(self) -> str:
        return f"JevClient(model={self.model!r})"

    def decide(self, state: Union[str, Dict[str, Any]], questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Submit one Decisions request; returns the `answers` mapping.

        Raises JevError on transport errors, non-2xx responses, or when an
        asked question is missing from the answers — callers decide whether
        that fails closed (guard) or falls back (reflection, routing).
        """
        payload = {"model": self.model, "state": state, "questions": questions}
        try:
            resp = self._http.post(self.url, json=payload)
        except httpx.HTTPError as e:
            raise JevError(f"Jev request failed: {e}") from e

        if resp.status_code >= 400:
            raise JevError(f"Jev returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except json.JSONDecodeError as e:
            raise JevError(f"Jev returned non-JSON body: {resp.text[:300]}") from e

        answers = body.get("answers") or {}
        missing = set(questions) - set(answers)
        if missing:
            raise JevError(f"Jev response missing answers for: {sorted(missing)}")

        logger.debug(f"Jev: {len(answers)} answer(s), cost={body.get('usage', {}).get('cost')}")
        return answers

    def noul(self, state: Union[str, Dict[str, Any]], instructions: str) -> float:
        """Ask a single yes/no question; returns P(yes) in [0, 1]."""
        answer = self.decide(state, {"q": {"type": "noul", "instructions": instructions}})["q"]
        p = answer.get("noul")
        if not isinstance(p, (int, float)):
            raise JevError(f"Jev noul answer has no probability: {answer}")
        return float(p)

    def choices(
        self,
        state: Union[str, Dict[str, Any]],
        questions: Dict[str, Tuple[str, Dict[str, str]]],
    ) -> Dict[str, Dict[str, Any]]:
        """Ask several choice questions in one request.

        `questions` maps name -> (instructions, criteria). Returns
        name -> {"choice", "probabilities", "confidence"}.
        """
        answers = self.decide(state, {
            name: {"type": "choice", "instructions": instructions, "criteria": criteria}
            for name, (instructions, criteria) in questions.items()
        })
        for name, (_, criteria) in questions.items():
            if answers[name].get("choice") not in criteria:
                raise JevError(f"Jev choice for '{name}' is not a valid option: {answers[name]}")
        return answers
