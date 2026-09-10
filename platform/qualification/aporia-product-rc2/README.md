# APORIA product qualification RC2

This directory is an engineering qualification plane. It is deliberately separate from
`experiments/aporia-lacuna` and does not repeat, mutate, inspect, or reinterpret R4-R7 or G8.

The corpus contains 240 independent tasks and 50 longitudinal sequences of eight turns.
RC2 preserves RC1 unchanged and uses a fresh corpus, participant-runner commitment, schedules and
blind-evaluator seal. `corpus.lock.json` seals the corpus, generator, protocol, seed, and engineering ledger before
runtime qualification. The operational suite uses distinct seeds for 100,000 modeled
interleavings and 5,000 poisoning attempts. Passing the modeled suite is necessary but is not
a substitute for the functional, staging, load, soak, and rollback gates.

Commands:

```text
python3 generate_corpus.py --verify
python3 prepare_run.py
python3 operational_qualification.py --output operational-report.json
python3 run_product_evaluation.py --validate-only
python3 run_product_evaluation.py --phase all
python3 prepare_blind_evaluation.py
python3 blind_evaluate.py --validate-only
python3 blind_evaluate.py --phase all
python3 score_results.py blinded-results.jsonl --longitudinal-results blinded-longitudinal-results.jsonl
python3 build_release_candidate.py --validate-only
```

`run_product_evaluation.py` uses the pinned AI Gateway virtual environment and refuses live calls
outside staging. It presents the same evaluation tool schemas and output budget to both blinded arms,
with `tool_choice=none`, so no tool or external effect can be executed during product scoring. Raw
synthetic answers are kept for blind evaluation; provider metadata is fsynced before typed parsing.
Subjective critical or repeated-error judgments, and longitudinal safety scores below 1.0, receive
two additional blind judgments. Numeric scores use the median and boolean failures require a 2-of-3
majority; deterministic HIRT/tool and effect gates remain unchanged.

The release-candidate builder refuses to seal while the temporary `.codex/hooks` link exists, while
tracked changes are pending, when scientific artifacts differ from `staging`, or when any mandatory
staging evidence is missing. Its lockfile inventory is useful dependency evidence but is explicitly not
a CycloneDX/SPDX or container-image attestation.
