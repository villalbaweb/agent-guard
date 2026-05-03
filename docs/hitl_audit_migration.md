# Migration Plan: HIPAA-First Compliance Audit Infrastructure

*Status: Proposed | Last updated: 2026-04-25*

---

## Context

The PG-style evaluation in `docs/internal/pg_evaluation.md` flagged that AgentGuard's "AI Governance Platform" positioning is too soft to win against well-funded incumbents (Arize $70M Series C, Langfuse acquired by ClickHouse, InfiniteWatch with Sequoia/A16Z scout backing). The substantial code that already exists — a causal trace with parent/child event linking, inline policy enforcement with HITL flags, per-tenant policy hot-reload, identity stamping in every governance decision — is structurally a **compliance audit ledger**, not a generic observability tool.

This migration repositions and extends the project as **lightweight HITL + audit infrastructure for AI agents in regulated industries**, leading with HIPAA (US healthcare) where the regulatory tailwind is strongest (2025 Security Rule NPRM, 2026 final rule expected) and where Microsoft Presidio's built-in PHI recognizers reduce engineering cost.

**Intended outcome.** A focused product that a healthcare CISO can evaluate, deploy, and pass through a vendor security review — built on the existing trace + policy + HITL primitives, extended with tamper-evident audit persistence, PHI redaction, SSO identity capture in approval records, and a HIPAA audit report generator. The compliance core (policy/trace/audit/redaction/HITL) becomes framework-agnostic via a new `agentguard.guard` wrapper; the existing LangGraph executor is preserved as an adapter, not the only entry point.

**Execution profile.** Solo, evenings/weekends (~10–15 hrs/week). Plan is aggressively descoped: ships value at every phase, lands an MVP suitable for a design-partner conversation in ~14 weekend-weeks, defers SOX/FINRA reports and public SDK release until a paying design partner signals demand.

---

## What Already Exists (preserve and reuse)

These primitives are real and stay — extending them, not replacing.

- `agentguard/trace.py` — canonical causal trace JSON (`schema_version 1.0`), parent/child event IDs, redactor hook, `dump()` and `write_to_disk()`. **Extend with**: append-on-emit recorder hook, hash chain, sequence numbers.
- `agentguard/policy.py` — five-layer policy evaluation (per-step budget → cumulative budget → content rules → LLM semantic guard → loop detection), `require_hitl` rule action already wired, `[subject:auth]` stamp in every `GovernanceDecision`. **Extend with**: redaction config block, token claims in decision records.
- `agentguard/state.py` — `AgentGuardState` TypedDict with `auth_context`, `policy_id`, `governance_decisions`, `trace_events` accumulators. Stays unchanged in shape.
- `agentguard/auth.py` — HS256 JWT decoder, `AuthContext` dataclass (subject, tenant_id, roles, jti, iat, exp). **Extend with**: OIDC support (Authlib).
- `agentguard/executor.py:305-348` — `lg_interrupt([hitl_payload])` emission and approval resume. Stays functionally but **moves to** `agentguard/langgraph_adapter/executor.py`.
- `backend/routes/runs.py` — existing `/runs`, `/runs/{id}`, `/runs/{id}/trace`, `/runs/{id}/approve`. The approve endpoint already plumbs `Command(resume=approval_payload)` through LangGraph. **Stays**, with the reviewer console added alongside.
- `examples/asset_management.py` (Meridian Capital) — six worked scenarios including HITL trade block. **Stays as a secondary financial-services example**; the headline becomes a new healthcare scenario.

## What Gets Deprecated or Relocated

- `agentguard/orchestrator.py` and `agentguard/executor.py` → moved to a new `agentguard/langgraph_adapter/` package (LangGraph-specific). Compliance customers reach the policy/trace/audit/HITL stack via a new framework-agnostic `agentguard.guard` wrapper — they don't have to adopt LangGraph.
- `docs/paul_graham_evaluator.md`, `docs/pg_evaluation.md`, `docs/modernization_and_llm_integration.md`, `docs/unknown_items.md` → moved to `docs/internal/` and excluded from the public-facing README. These are credibility leaks for a CISO buyer landing on the repo.
- "AI Governance Platform" framing across README and docs → replaced with "Compliance audit infrastructure for AI agents in regulated industries (HIPAA-first)."
- `pgvector` dependency in the registry → moved behind a feature flag (`AGENTGUARD_ENABLE_VECTOR_REGISTRY=1`, default off). Many healthcare IT departments reject Postgres extensions; the audit ledger must work on stock RDS Postgres.

## Critical Bugs to Fix in Phase 0

These are not features — they are regressions that disqualify the project from a security review today.

- **`backend/dependencies.py:144-145`** — the static `AGENTGUARD_API_KEY` path returns `["user", "approver"]` roles. **Anyone with the API key can self-approve their own HITL pause.** This defeats the entire HITL audit trail. Fix: API key path returns `["user"]` only; `/approve` requires a real JWT.
- **`backend/dependencies.py:167-170`** — `AGENTGUARD_NO_AUTH=1` development bypass. Add a startup-time refusal when `ENVIRONMENT=production` and `AGENTGUARD_NO_AUTH=1` are both set.

---

## Migration Phases

Calendar weeks below assume ~10–15 hrs/week (evenings/weekends).

### Phase 0 — Repositioning + Critical Bugs (weeks 1–2)

**Goal:** Repo is presentable to a healthcare CISO. Self-approval bug is closed.

- Rewrite `README.md` around HIPAA-first compliance audit positioning.
- Move `docs/paul_graham_evaluator.md`, `docs/pg_evaluation.md`, `docs/modernization_and_llm_integration.md`, `docs/unknown_items.md` to `docs/internal/`.
- Add `docs/compliance/baa-template.md` (one-page Business Associate Agreement template). Marketing artifact.
- Add `docs/compliance/hipaa-mapping.md` — placeholder for control-by-control mapping (filled in Phase 5).
- Add `policies/hipaa_template.yaml` — example policy with PHI redaction rules and HITL triggers for write-to-EHR actions.
- Fix `backend/dependencies.py:144-145` (static API key role overreach) and `:167-170` (production no-auth bypass).
- Add Alembic to `pyproject.toml` and initialize `migrations/alembic/` (used in every subsequent phase). The existing `migrations/001_registry.sql` becomes the baseline.

### Phase 1 — Postgres-Backed Audit Ledger + Checkpointer (weeks 3–8)

**Goal:** Every governance decision and trace event is persisted to a tamper-evident Postgres ledger synchronously on emission. HITL pauses survive backend restarts.

- New migration `002_audit_log.sql`:
  - `audit_events` table: `event_id` (PK), `run_id`, `tenant_id`, `seq_no` (NOT NULL, UNIQUE per run_id), `parent_event_id`, `node`, `depth`, `timestamp`, `agent_id`, `action`, `intent`, `decision_json`, `cost_delta`, `cumulative_cost`, `status`, `metadata_json`, `event_hash`, `prev_event_hash`, `auth_subject`, `auth_token_id`, `auth_issued_at`.
  - Postgres Row-Level Security policy: `tenant_id = current_setting('agentguard.tenant_id')::text`. Every backend session sets this on connection acquire.
  - Vanilla Postgres only (no pgvector required for the audit ledger).
- New module `agentguard/audit.py`:
  - `AuditRecorder` class with `record(event)` method that computes `event_hash = HMAC_SHA256(tenant_key, canonical_json(event) || prev_event_hash)`, asserts strict monotonic `seq_no`, writes to Postgres synchronously.
  - Tenant-scoped HMAC signing key (read from env `AGENTGUARD_TENANT_HMAC_KEY_<tenant_id>` or from a secrets store) — so insider threat at the operator can't forge cross-tenant events.
- Wire `AuditRecorder` into `agentguard/trace.py` `new_event()` — every event is recorded as it's created, not at `dump()` time.
- Extend `agentguard/state.py` `GovernanceDecision` with `auth_token_id: Optional[str]` and `auth_issued_at: Optional[str]`. Update `agentguard/policy.py` `_make_decision()` to populate these from `state["auth_context"]`.
- Swap `RedisSaver` for `PostgresSaver` in `backend/dependencies.py:_build_checkpointer()`. Add `langgraph-checkpoint-postgres` to `pyproject.toml`. Redis becomes optional (hot trace read cache only).
- New migration `002b_checkpoint.sql` runs the `PostgresSaver.setup()` SQL.
- Update `backend/routes/runs.py` to load audit-aware traces from Postgres on `GET /runs/{id}/trace` (not Redis). Keep Redis as a fast read-through cache.
- Tests: `tests/test_audit_chain.py` — verify hash chain is unbroken after a sequence of events; verify a deleted middle row is detected via `seq_no` gap; verify cross-tenant queries return empty under RLS.

### Phase 1.5 — External Anchoring (week 9)

**Goal:** The hash chain proves order; external anchoring proves "no events were deleted between the last anchor and now."

- Daily background job: compute Merkle root of all audit events in the last 24 hours per tenant; write to S3 with Object Lock (Compliance mode, retention period = 6 years for HIPAA).
- New migration `003_anchor_log.sql`: stores `anchor_id`, `tenant_id`, `period_start`, `period_end`, `merkle_root`, `s3_object_uri`, `created_at`.
- Add `docs/compliance/audit-architecture.md` — architecture diagram for prospect security teams. This is a sales artifact.

### Phase 2 — PHI Redaction (Presidio) + Clinical Example (weeks 10–14)

**Goal:** PHI never leaves the trust boundary unredacted. Healthcare scenario is the headline demo.

- Add `presidio-analyzer` and `presidio-anonymizer` to `pyproject.toml`. Bundles HIPAA Safe Harbor recognizers (18 PHI identifiers including MRN, NPI, DEA numbers).
- New module `agentguard/redaction.py`:
  - `PHIRedactor` class wrapping Presidio.
  - `redact(text, policy)` returns redacted text plus a metadata blob describing what was redacted (entity types and positions, not values).
  - Configurable allowlist/denylist via the new `redaction:` block in `policy.yaml`.
- Wire redaction at three sites:
  1. `agentguard/trace.py` `new_event()` — redact `intent`, `action`, `metadata` fields **before** computing `event_hash`.
  2. `agentguard/langgraph_adapter/executor.py:319` — redact the `hitl_payload` before `lg_interrupt()`. Critical: a HITL Slack/Teams notification containing PHI is itself a HIPAA breach.
  3. `backend/routes/runs.py` `GET /runs/{id}/trace` — final redaction pass on read (defense in depth).
- New module `agentguard/encryption.py` — column-level Fernet encryption for the few audit fields that must store sensitive data unredacted (e.g. encrypted PHI for a regulator subpoena response). Keys stored in env or AWS KMS.
- New example `examples/clinical_documentation.py`:
  - Scenario: AI assistant drafts a SOAP note from a doctor-patient conversation transcript.
  - Three policy gates: (1) PHI redaction in trace, (2) HITL approval before write to EHR, (3) audit log captures clinician's SSO identity and timestamp.
  - Becomes the headline demo in `README.md`.

### Phase 6a — Internal SDK Seam (week 15)

**Goal:** Compliance core is framework-agnostic. Customers can use AgentGuard without adopting LangGraph.

This is *not* a public PyPI release. It's an internal namespace cleanup that takes ~1 weekend-week and unlocks framework-agnostic positioning for the rest of the migration.

- Create `agentguard/langgraph_adapter/` directory.
- Move `agentguard/orchestrator.py` → `agentguard/langgraph_adapter/orchestrator.py`.
- Move `agentguard/executor.py` → `agentguard/langgraph_adapter/executor.py`.
- Create `agentguard/guard.py` — framework-agnostic API:
  - `@guard(policy_id=..., tenant_id=...)` decorator for wrapping an arbitrary function (LLM call, tool invocation, agent step). Computes preflight check, records trace event, runs the function, records the result, applies budget enforcement.
  - `with guard.session(...)` context manager for multi-step workflows.
  - HITL pause is exposed as a `GuardPaused` exception that the caller catches and persists. Resume is a function call, not a LangGraph `Command`. The LangGraph adapter is *one* implementation of pause/resume.
- Update imports across the codebase via `agentguard/__init__.py` so existing import paths keep working.
- Tests: `tests/test_guard_decorator.py` — verify policy + trace + audit + redaction all fire on a plain Python function with no LangGraph in scope.

### Phase 3 + 4 — Reviewer Console + SSO (weeks 16–24)

**Goal:** A compliance reviewer logs in via Okta, sees pending HITL approvals, approves with their full identity captured into the audit log.

These two phases are combined because SSO is a prerequisite for the reviewer console session.

- Add `authlib` to `pyproject.toml`.
- New module `agentguard/sso/oidc.py` — generic OIDC. New module `agentguard/sso/okta.py` — Okta-validated configuration (most common in US healthcare).
- New routes in `backend/routes/sso.py` — `GET /sso/login`, `GET /sso/callback`. Issues a session cookie + a short-lived JWT for API calls.
- Extend `AuthContext` to capture full token claims (jti, iat, iss, full roles list).
- New role: `compliance_reviewer` (read-only access to audit logs across all users in the tenant, no execution rights).
- New migration `003_reviewer_state.sql`:
  - `pending_reviews` table: `review_id`, `run_id`, `tenant_id`, `event_id`, `created_at`, `due_at`, `status` (pending/approved/denied/escalated/auto_rejected), `reviewer_subject`, `reviewer_decided_at`, `decision_reason`.
- New routes in `backend/routes/reviewer.py` — Jinja-rendered (no SPA):
  - `GET /reviewer/queue` — list of pending reviews for the logged-in reviewer's roles.
  - `GET /reviewer/{review_id}` — full context (redacted trace up to the pause point, agent, action, cost estimate).
  - `POST /reviewer/{review_id}/decide` — approve/deny + reason. Captures full SSO identity into the audit log as a first-class event.
- New Jinja templates in `backend/templates/reviewer/`.
- Background worker (APScheduler — lighter than Celery): watches `pending_reviews`, transitions to `escalated` after a configurable threshold, then `auto_rejected` after a longer threshold. Each transition emits an audit event.
- Notifications: email primary (Slack BAA only covers Enterprise Grid — healthcare buyers often can't use it). Slack/Teams as optional convenience webhooks documented as not BAA-covered.

### Phase 5 — HIPAA Audit Report Generator (weeks 25–30)

**Goal:** One-click export of the audit log as a HIPAA §164.312(b) audit report that an auditor can actually use.

- New directory `agentguard/reports/`.
- `agentguard/reports/base.py` — shared CSV/PDF/JSON rendering. Reads from `audit_events` table, applies tenant scoping.
- `agentguard/reports/hipaa.py` — generates two reports:
  - §164.312(b) Audit Log Report — every PHI access with subject, timestamp, action, justification.
  - §164.308(a)(1)(ii)(D) Information System Activity Review — periodic review summary.
- New routes in `backend/routes/reports.py` — `POST /reports/hipaa/audit-log` and `POST /reports/hipaa/activity-review`. Returns a signed download URL.
- New migration `004_report_artifacts.sql` — stores generated report metadata for chain-of-custody.
- Output formats: CSV (always), PDF (HIPAA), JSON (machine-readable for downstream compliance platforms like Drata/Vanta).

### Deferred Indefinitely (until a paying design partner asks)

- **Phase 5b** Drata/Vanta/Secureframe integration — the distribution channel to compliance buyers. Worth doing, but only after first paying customer.
- **Phase 6b** Public PyPI release of `agentguard-sdk` with CrewAI/raw-OpenAI adapters. Speculative until a customer asks for non-LangGraph integration.
- **SOX / FINRA report generators** — the framework in `agentguard/reports/` supports them, but the templates and regulatory mappings are real engineering work. Defer until a financial-services design partner appears.
- **EU AI Act report generators** — wait until enforcement timeline for high-risk systems clarifies (currently 2026–2027 phase-in for Article 6).

---

## Critical Files

Files that will be modified or created. Phase column shows when each is touched.

| File | Phase | Action |
|---|---|---|
| `README.md` | 0 | Rewrite around HIPAA-first compliance audit positioning |
| `backend/dependencies.py:144-145` | 0 | Fix API key role overreach (return `["user"]` only) |
| `backend/dependencies.py:167-170` | 0 | Refuse production startup with `AGENTGUARD_NO_AUTH=1` |
| `backend/dependencies.py:_build_checkpointer` | 1 | Swap RedisSaver → PostgresSaver |
| `pyproject.toml` | 0,1,2,3 | Add alembic, langgraph-checkpoint-postgres, presidio-analyzer, presidio-anonymizer, authlib |
| `migrations/alembic/` | 0 | Initialize Alembic |
| `migrations/002_audit_log.sql` | 1 | NEW — append-only ledger with hash chain, RLS, seq_no |
| `migrations/002b_checkpoint.sql` | 1 | NEW — PostgresSaver schema |
| `migrations/003_anchor_log.sql` | 1.5 | NEW — Merkle root anchor ledger |
| `migrations/003_reviewer_state.sql` | 3+4 | NEW — pending_reviews table |
| `migrations/004_report_artifacts.sql` | 5 | NEW — generated report metadata |
| `agentguard/audit.py` | 1 | NEW — AuditRecorder, hash chain, tenant-scoped HMAC signing |
| `agentguard/redaction.py` | 2 | NEW — Presidio wrapper for PHI |
| `agentguard/encryption.py` | 2 | NEW — column-level Fernet for sensitive audit fields |
| `agentguard/guard.py` | 6a | NEW — framework-agnostic `@guard` decorator + session |
| `agentguard/sso/{oidc,okta}.py` | 3+4 | NEW — Authlib-based SSO |
| `agentguard/reports/{base,hipaa}.py` | 5 | NEW — HIPAA report generators |
| `agentguard/langgraph_adapter/executor.py` | 6a | MOVED from `agentguard/executor.py`; redact HITL payload at line 319 |
| `agentguard/langgraph_adapter/orchestrator.py` | 6a | MOVED from `agentguard/orchestrator.py` |
| `agentguard/trace.py` | 1,2 | Wire AuditRecorder into `new_event()`; redact at emit |
| `agentguard/state.py` | 1 | Extend `GovernanceDecision` with `auth_token_id`, `auth_issued_at` |
| `agentguard/policy.py` | 1,2 | Populate token claims into decisions; consume `redaction:` policy block |
| `agentguard/auth.py` | 3+4 | Add `compliance_reviewer` role; capture full token claims into AuthContext |
| `backend/routes/sso.py` | 3+4 | NEW — SSO login/callback |
| `backend/routes/reviewer.py` | 3+4 | NEW — Jinja-rendered reviewer queue + decision |
| `backend/routes/reports.py` | 5 | NEW — report generation API |
| `backend/templates/reviewer/*.html` | 3+4 | NEW — Jinja templates |
| `examples/clinical_documentation.py` | 2 | NEW — headline healthcare demo |
| `policies/hipaa_template.yaml` | 0 | NEW — example policy with PHI redaction + HITL triggers |
| `docs/compliance/baa-template.md` | 0 | NEW — BAA template (marketing artifact) |
| `docs/compliance/hipaa-mapping.md` | 0,5 | NEW — placeholder in P0, filled in P5 |
| `docs/compliance/audit-architecture.md` | 1.5 | NEW — architecture diagram for prospect security reviews |
| `docs/internal/` | 0 | Move PG-flavored docs out of public path |

## Existing Utilities to Reuse (Do Not Reimplement)

- `agentguard.trace.new_event()` — keep its signature; extend internally to call `AuditRecorder.record()`.
- `agentguard.policy.PolicyEngine.check_step()` — already does five-layer evaluation. Add redaction policy consumption inline; do not fork.
- `agentguard.state.make_initial_state()` — keep; extend `GovernanceDecision` only.
- `agentguard.auth.AuthContext` — extend in place to capture token claims.
- `backend/routes/runs.py:approve_run` — already correctly resumes via `Command(resume=...)`. The reviewer console writes through this same endpoint, not a parallel one.
- `examples/asset_management.py` — kept as the financial-services example. Do not delete.

---

## Verification

End-to-end checks for each phase. Each verification is a script or test that proves the phase actually works, not just compiles.

### Phase 0
- `pytest tests/test_auth.py` passes including a new test that asserts `AGENTGUARD_API_KEY`-authenticated requests cannot call `/runs/{id}/approve`.
- `ENVIRONMENT=production AGENTGUARD_NO_AUTH=1 uvicorn backend.main:app` exits with a clear error.
- `README.md` does not contain the phrase "AI governance platform"; landing scan finds "HIPAA" in the first 50 lines.

### Phase 1
- `alembic upgrade head` applies cleanly on a fresh Postgres without the pgvector extension installed.
- New `tests/test_audit_chain.py` passes:
  - 100 events recorded → hash chain verifies.
  - Manually delete a middle row → chain verification detects gap via `seq_no`.
  - Tenant A cannot SELECT tenant B's events under RLS even with elevated app-user privileges.
- HITL durability: start a run, trigger HITL pause, restart the backend, approve. Run resumes correctly. Redis is not running during the test.

### Phase 1.5
- Daily anchor job writes a Merkle root to S3 with Object Lock metadata visible via `aws s3api get-object-retention`.
- `docs/compliance/audit-architecture.md` includes the chain + anchoring diagram.

### Phase 2
- `python examples/clinical_documentation.py` runs end-to-end. Trace JSON contains zero PHI tokens (verified by Presidio re-scan of the saved trace file).
- HITL Slack notification payload (when configured) is verified PHI-free in `tests/test_redaction_hitl.py`.
- Encrypted audit columns can be decrypted given the Fernet key; cannot be read without it.

### Phase 6a
- `tests/test_guard_decorator.py` wraps a plain Python function with `@guard`, asserts policy + trace + audit + redaction all fire, and confirms `import langgraph` is *not* required to run the test.
- All existing tests still pass after the import path move.

### Phase 3+4
- Manual flow: log in via Okta dev tenant → reviewer queue shows pending HITL → approve → audit log shows reviewer's full SSO identity (subject, jti, iat) bound to the approval event.
- `tests/test_reviewer_escalation.py` — pause a HITL, advance APScheduler clock, assert escalation transition emits an audit event, then auto_rejected after the second threshold.

### Phase 5
- `POST /reports/hipaa/audit-log` for a tenant with sample data returns a signed URL; downloaded PDF passes manual review against §164.312(b) checklist in `docs/compliance/hipaa-mapping.md`.
- CSV export round-trips through Excel without encoding issues.
- JSON export matches the schema documented in `docs/compliance/hipaa-mapping.md`.

---

## Risks Tracked

- **HIPAA 2025 NPRM is proposed, not final.** Marketing must not promise "compliance with the 2025 rule" — only "alignment with the proposed direction." Final rule expected late 2026.
- **EU AI Act August 2026 date is GPAI, not Article 6 high-risk.** High-risk healthcare AI obligations phase in 2026–2027. Be precise in sales material.
- **Solo evenings/weekends pace ≈ 9–12 calendar months for the full plan.** Realistically: ship Phase 0–2 (MVP for design partner pitch) in ~14 weeks, then assess whether a design partner is engaged before committing to Phase 3+4 build.
- **Postgres-only deployments mean no vector registry by default.** Document that semantic agent routing is opt-in and not required for compliance posture.
- **Presidio's PHI recognizers are not perfect.** Custom recognizer tuning for MRN/NPI formats may be required per customer; bake this into onboarding, not the engineering plan.

---

## Decision Gate

After Phase 2 ships (~week 14), pause engineering and run the design-partner conversation:

> "Who is the specific compliance officer at a healthcare AI startup whose deployment is blocked because they can't answer a regulator's question about what their agent did? Have I talked to them this month?"

If yes — proceed to Phase 3+4. If no — keep marketing/outreach effort, do not build more infrastructure speculatively. The PG eval's underlying rule applies: 10 users matter more than any architecture decision past this point.
