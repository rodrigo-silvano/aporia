# APORIA RC1 qualification execution

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

## Blind participant and judge evaluation

Evidence paths:

```text
raw-task-responses.jsonl
raw-longitudinal-responses.jsonl
provider-metadata.jsonl
blind-evaluation-manifest.json
blind-evaluation-report.json
```

Participant evaluation runs blindly before qualification; judges compare treatment and control without
knowledge of assignments. The decision pipeline enforces strict blind evaluation; code changes invalidate
blind scoring. The participant runner uses identical model, output budget and non-executing tool schemas
for both arms. Raw fixtures are synthetic; they must not contain account or user data.

## Authenticated runtime load and soak

`APORIA_OPERATOR_ADMIN_ID` must identify an active administrator; `--tenant-id` remains restricted to the
approved pilot tenant by the server-side helper. The helper creates only synthetic sessions whose titles
start with `APORIA RC1 qualification`, issues 45-second read-only tickets, and archives only those owned
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

evidence. Required gates are zero lost and duplicate events, unrecovered error rate below 0.5%, all
required sessions open, and measured TTFT/latency present.

## Final evidence

Capture health for tenant 49 and globally, independent-control tests, 30-conversation E2E, service and
docroot validation, rollback/recovery, final `guarded_reversible` resolution, and the active release ID.
Every report must be aggregate-only and carry a SHA-256 commitment. Build the RC manifest only after
all evidence is present. Missing evidence prevents qualification.
