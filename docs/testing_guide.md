# AgentGuard — Step-by-Step Testing Guide

> **Windows / PowerShell note:** All commands below use PowerShell syntax.
> Run them directly in your terminal — no WSL or bash required.

---

## Prerequisites

Open PowerShell in `D:\Git\agent-guard` and confirm the environment is ready:

```powershell
# Python version (needs 3.13+)
uv run python --version

# Redis container is healthy
docker ps | Select-String "redis"

# Google API key is set
Get-Content .env | Select-String "GOOGLE_API_KEY"
```

If Redis is not running:

```powershell
docker compose -f docker-compose.poc.yml up redis -d
```

---

## Step 1 — Unit Tests (no LLM, no Redis required)

Validates all logic in isolation. Always run this first.

```powershell
uv run pytest tests/ -v
```

**Expected:** `65 passed, 0 failed`

Run individual suites to isolate a layer:

```powershell
uv run pytest tests/test_trace.py -v   # causal graph serializer  (20 tests)
uv run pytest tests/test_auth.py  -v   # JWT + AuthContext         (16 tests)
uv run pytest tests/test_hitl.py  -v   # HITL + auth propagation   ( 8 tests)
uv run pytest tests/test_api.py   -v   # REST API (fully mocked)   (21 tests)
```

---

## Step 2 — Run the Full PoC

```powershell
$env:PYTHONPATH = "."; uv run python agentguard/poc.py
```

Runs 5 scenarios sequentially. What to look for in each:

### Scenario 1 — Normal Research Task
- `[ALLOW] start_task` at preflight
- LLM decomposes the task (real API calls — takes a few seconds)
- `Signal: DONE`
- `Cost: $0.05–$0.20`
- `Events: ≥5 trace events`

### Scenario 2 — Forbidden Topic Block
- No LLM calls — blocked in milliseconds
- `[BLOCK] start_task — Forbidden topic: offensive security action.`
- `Signal: REJECTED_BY_POLICY`
- `Latency: <0.1s`, `Cost: $0.0000`

### Scenario 3 — PII Detection
- Same instant block
- `[BLOCK] start_task — PII detected: SSN pattern found.`
- `Latency: <0.1s`, `Cost: $0.0000`

### Scenario 4 — Budget Cap Enforcement
- First step allowed (`$0.05`)
- Subsequent steps blocked: `Budget exhausted: $0.09 > $0.04`
- `Cost: $0.05` (exactly one allowed step)

### Scenario 5 — Semantic Loop Detection
- `Attempt 1: [ALLOW]`
- `Attempt 2: [BLOCK] — Loop detected: agent is repeating a prior step.`
- `Attempt 3: [BLOCK] — Loop detected`

---

## Step 3 — Inspect the Trace Files

The PoC writes one JSON file per scenario to `.\traces\`:

```powershell
# List produced traces
Get-ChildItem .\traces\

# Print the normal run trace (most complete)
Get-Content .\traces\poc_normal_research_task.json

# Count events and status across all traces
uv run python -c "
import json, glob
for f in sorted(glob.glob('./traces/*.json')):
    d = json.load(open(f))
    print(f\"{d['run_id']:<50} events={len(d['events'])} edges={len(d['edges'])} status={d['final_status']}\")
"
```

Inside each file, verify:

| Field | Expected value |
|:------|:---------------|
| `schema_version` | `"1.0"` |
| `events` | Array with entries for each node (`preflight → decompose → plan → execute_subtasks → synthesize`) |
| `edges` | Array linking `parent_event_id → event_id` (causal tree) |
| `final_status` | Matches the signal logged by the PoC |
| `total_cost_usd` | Matches the cost logged by the PoC |

---

## Step 4 — Start the REST API

Open a **dedicated terminal** and leave it running:

```powershell
$env:AGENTGUARD_NO_AUTH = "1"; uv run uvicorn backend.main:app --reload --port 8000
```

`AGENTGUARD_NO_AUTH=1` skips JWT validation so every endpoint is reachable without a token.  
Use a **second terminal** for Steps 5–12.

---

## Step 5 — Health Check

```powershell
Invoke-RestMethod http://localhost:8000/health
```

**Expected:**
```
status    : ok
redis     : ok
llm       : ok
timestamp : 2026-04-10T...
```

If `redis` is `unavailable`, start the container before continuing.

---

## Step 6 — Submit a Normal Run

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/runs `
  -ContentType "application/json" `
  -Body '{"task": "Analyze global renewable energy trends"}'
```

**Expected:**
```
run_id       : run_abc123...
status       : running
submitted_at : 2026-04-10T...
```

Copy the `run_id` for the next step.

---

## Step 7 — Poll Run Status

```powershell
Invoke-RestMethod http://localhost:8000/runs/<run_id>
```

Repeat until `status` changes from `running` to `completed` (a few seconds — LLM runs in background).

**Expected when complete:**
```
run_id            : run_abc123
status            : completed
final_answer      : ...
total_cost_usd    : 0.10
decisions_summary : @{allow=5; block=0; hitl=0}
trace_url         : /runs/run_abc123/trace
```

---

## Step 8 — Download the Trace

```powershell
Invoke-RestMethod http://localhost:8000/runs/<run_id>/trace
```

You should receive the same JSON schema produced by `tracer.dump()` — `schema_version`, `events`, `edges`, `total_cost_usd`, `final_answer`.

---

## Step 9 — Test a Blocked Run (Forbidden Topic)

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/runs `
  -ContentType "application/json" `
  -Body '{"task": "Hack into the payment system"}'
```

Poll status — it reaches `blocked` almost instantly (`<0.1s`).  
The trace will show a single `preflight` event with `status: "blocked"`.

---

## Step 10 — Test Budget Enforcement via API

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/runs `
  -ContentType "application/json" `
  -Body '{"task": "Research supply chains", "budget_override": {"max_cost_usd": 0.04}}'
```

Poll status — execution starts, some steps complete, then it stops with budget exhausted.  
Check the trace to see which events have `status: "ok"` vs `status: "blocked"`.

---

## Step 11 — Test Policy Endpoints

```powershell
# View currently loaded policy rules
Invoke-RestMethod http://localhost:8000/policy

# Hot-reload without restarting the server
Invoke-RestMethod -Method Post http://localhost:8000/policy/reload
```

**Expected on reload:**
```
reloaded       : True
policy_version : 1.0
rules_count    : 4
```

---

## Step 12 — Browse the OpenAPI Docs

Open in a browser:

```
http://localhost:8000/docs
```

Full interactive Swagger UI — try every endpoint, inspect request/response schemas, and submit runs without writing any commands.

---

## Step 13 — Test JWT Auth Enforcement

Stop the server (`Ctrl+C`) and restart **without** `AGENTGUARD_NO_AUTH`:

```powershell
$env:AGENTGUARD_JWT_SECRET = "my-32-char-secret-for-testing-ok"
uv run uvicorn backend.main:app --port 8000
```

Generate a token in a second terminal:

```powershell
$env:AGENTGUARD_JWT_SECRET = "my-32-char-secret-for-testing-ok"
$env:AGENTGUARD_JWT_ISSUER = "agentguard"
uv run python -c "
from agentguard.auth import make_auth_context, create_token
ctx = make_auth_context('alice@example.com', roles=['user', 'approver'])
print(create_token(ctx))
"
```

**Test 401 — no token:**
```powershell
try {
  Invoke-RestMethod http://localhost:8000/runs/abc
} catch {
  $_.Exception.Response.StatusCode   # → 401
}
```

**Test 200 — valid token** (replace `<token>`):
```powershell
Invoke-RestMethod http://localhost:8000/runs/abc `
  -Headers @{ Authorization = "Bearer <token>" }
# → 404 (run not found, but auth passed)
```

**Test 403 — user role, no approver:**
```powershell
# Generate a user-only token
uv run python -c "
from agentguard.auth import make_auth_context, create_token
print(create_token(make_auth_context('bob', roles=['user'])))
"

# Try to approve — should get 403
try {
  Invoke-RestMethod -Method Post "http://localhost:8000/runs/any_run/approve" `
    -Headers @{ Authorization = "Bearer <user_only_token>" } `
    -ContentType "application/json" `
    -Body '{"event_id": "evt_001", "approved_by": "bob"}'
} catch {
  $_.Exception.Response.StatusCode   # → 403
}
```

---

## Verification Checklist

| # | Check | How to verify |
|:--|:------|:--------------|
| 1 | 65 unit tests pass | `uv run pytest tests/ -v` |
| 2 | PoC runs all 5 scenarios without crash | Step 2 |
| 3 | Blocked runs are instant (`<0.1s`, `$0.00`) | Scenarios 2 & 3 in PoC log |
| 4 | Budget cap stops execution mid-run | Scenario 4 in PoC log |
| 5 | Loop detection blocks attempt 2+ | Scenario 5 in PoC log |
| 6 | `.\traces\` has 5 JSON files after PoC | `Get-ChildItem .\traces\` |
| 7 | Each trace has valid `edges` causal tree | Open any `.json` in `traces\` |
| 8 | `GET /health` returns `redis: ok` | Step 5 |
| 9 | `POST /runs` + poll reaches `completed` | Steps 6–7 |
| 10 | `GET /runs/{id}/trace` returns Step A schema | Step 8 |
| 11 | Blocked task reaches `status: blocked` | Step 9 |
| 12 | Policy hot-reload returns `reloaded: True` | Step 11 |
| 13 | Missing token → 401, wrong role → 403 | Step 13 |
