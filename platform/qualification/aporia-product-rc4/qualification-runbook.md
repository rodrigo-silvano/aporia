# APORIA RC4 targeted diagnostic execution

RC4 is not final release evidence. Run only the RC3 failure families here; after they pass, create a fresh
independent qualification cycle and execute its complete blinded corpus.

Run from the active staging private release with staging configuration loaded by the normal service
loader. Use the AI Gateway virtual environment for participant/blind model evaluation and the agent
runtime virtual environment for authenticated WebSocket E2E/load/soak. Do not source or print `.env`.

## Locked local gates

```text
python3 generate_corpus.py --verify
python3 operational_qualification.py --output /tmp/aporia-operational-report.json
python3 run_product_evaluation.py --validate-only
python3 blind_evaluate.py --validate-only
```

The regenerated operational report must byte-match the committed report. Product and blind validation
must report G8 as unused.

## Staging product and blind evaluation

```text
python3 run_product_evaluation.py --phase all --concurrency 2
python3 blind_evaluate.py --phase all --concurrency 2
python3 score_results.py blinded-results.jsonl \
  --longitudinal-results blinded-longitudinal-results.jsonl \
  --output /tmp/aporia-product-evaluation-report.json
```

Do not unblind, inspect comparative results or alter the evaluator between participant execution and
blind scoring. The participant runner uses identical model, output budget and non-executing tool schemas
for both arms. Raw fixtures are synthetic; they must not contain account or user data.

## Authenticated runtime load and soak

`APORIA_OPERATOR_ADMIN_ID` must identify an active administrator; `--tenant-id` remains restricted to the
approved pilot tenant by the server-side helper. The helper creates only synthetic sessions whose titles
start with `APORIA RC4 diagnostic`, issues 45-second read-only tickets, and archives only those owned
sessions.

```text
runtime/dependencies/python/agent-runtime/bin/python staging_e2e.py \
  --sessions 5 --output /tmp/aporia-staging-e2e-report.json

runtime/dependencies/python/agent-runtime/bin/python staging_runtime_probe.py \
  --phase load --sessions 20 --predicted-peak-sessions 10 \
  --active-turns 2 --output /tmp/aporia-staging-load-report.json

runtime/dependencies/python/agent-runtime/bin/python staging_runtime_probe.py \
  --phase soak --sessions 20 --predicted-peak-sessions 10 \
  --active-turns 2 --duration-seconds 7200 --interval-seconds 600 \
  --output /tmp/aporia-staging-soak-report.json
```

The soak flag permitting a shorter duration is for local script validation only and is invalid release
evidence. Required gates are zero lost and duplicate events, unrecovered error rate below 0.5%, all
required sessions open, and measured TTFT/latency present.

## Final evidence

Capture health for tenant 49 and globally, independent-control tests, 30-conversation E2E, service and
docroot validation, rollback/recovery, final `guarded_reversible` resolution, and the active release ID.
Every report must be aggregate-only and carry a SHA-256 commitment. Build the RC manifest only after
all evidence is present. Missing evidence prevents qualification.
