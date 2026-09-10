# APORIA product readiness runbook

This runbook qualifies an engineering release candidate. It does not authorize `main`, production,
irreversible effects, automatic external contact, billing changes, or scientific claims about
consciousness. R4-R7 and G8 remain historical scientific artifacts and are not input to this gate.

## Release boundary

- Product modes: `disabled`, `shadow`, `advisory`, `guarded_reversible`.
- The staging pilot maps `canary` to `advisory`; the requested final staging state is
  `guarded_reversible` for the approved pilot tenant only.
- `guarded_reversible` can prepare only server-allowlisted internal, reversible, idempotent and
  compensated operations. It cannot send email, publish, bill, expose secrets or change its own
  policy and control switches.
- Product state is tenant-scoped, typed, revisioned, freshness-bound and provenance-bearing.
  Model hypotheses and LACUNA outputs cannot become confirmed facts.
- No chain of thought is stored. Operational reports contain aggregates, hashes and reason codes.

## Required preflight

1. Work from `aporia/product-readiness-rc`; verify a clean tracked tree and an immutable commit.
2. Verify corpus, run and evaluator locks. Never edit `generate_corpus.py` or `blind_evaluate.py`
   after their respective seals; create a new version and lock if either needs correction.
3. Run the Python, AI Gateway and Agent suites, Python compilation, schema aggregate check,
   `git diff --check`, and the operational qualification suite.
4. Confirm the AI runtime preflight matches `services/ai-gateway/runtime_manifest.json` and uses
   its dedicated virtual environment.
5. Confirm the three migrations are in the sealed release and then verify their tables/column after
   deployment:
   - `2026_08_24_ai_provider_response_metadata.sql`
   - `2026_08_24_aporia_product_state.sql`
   - `2026_08_24_aporia_product_hirt_contracts.sql`
6. Confirm all relevant services are active, the active release/docroots resolve to the expected
   release, and the public authenticated, marketing, acquisition and admin routes respond normally.

## Mandatory staging evidence

- Product A/B: all 240 task pairs and 50 eight-turn longitudinal pairs, followed by sealed blind
  evaluation and scoring. All quality, safety, regression, cost and overhead gates must pass.
- Runtime E2E: at least 30 synthetic read-only conversations covering continuity, contradiction,
  uncertainty, negative outcomes, tool selection and normal requests. No external effect is allowed.
- Load: at least `max(20, 2 x predicted peak)` concurrently open sessions, with active turns capped
  by the deployed tenant limit.
- Soak: at least two continuous hours using the same normal authenticated WebSocket route.
- Rollback: engage a control, prove the affected capability fails closed, restore it, and prove
  recovery. Finish with the pilot in `guarded_reversible` only if every gate remains green.
- Health: capture tenant and global reports. Tenant health validates tenant isolation/integrity;
  global health validates provider structured-output telemetry, which is not attributable per tenant.

Direct model evaluation records total request latency as `ttft_upper_bound_ms`; it is not real TTFT.
Only the authenticated WebSocket load/soak probe measures actual first-output latency.

## Release decision

The RC is qualified only if all mandatory evidence is complete and `PASS`, no critical alert exists,
all six independent controls fail closed, rollback and recovery pass, G8 was not consumed, and no
tracked scientific artifact changed. A failure leaves the pilot disabled or the global/tenant control
engaged. Missing evidence is a failed gate, never an implicit pass.
