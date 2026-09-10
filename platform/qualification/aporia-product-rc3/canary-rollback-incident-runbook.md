# APORIA canary, rollback and incident runbook

All examples run from the active private release on staging with `PLS_OPERATOR_ADMIN_ID` set to the
authorized active administrator. Record command exit status and the resulting aggregate health report;
never record secrets or raw user/model content.

## Staging progression

1. Start with `APORIA_MODE=disabled`, the approved pilot tenant list, and pilot mode `shadow`.
2. Verify shadow produces no external effect and all independent switches are released only for the
   approved tenant.
3. Move the pilot to `canary` (`advisory`) and run E2E plus initial load. Advisory may influence the
   answer but cannot execute an effect.
4. Approve and activate the guarded policy with a signed commitment, a conservative risk threshold,
   daily call limit and daily cost-unit limit.
5. Move the pilot to `guarded_reversible`. Keep the server allowlist restricted to `prepare_email`;
   verify that the result is a reversible internal draft and never a sent message.

Changing `.env` requires preparing/redeploying the agent runtime so `runtime.env` is regenerated.
`APORIA_REASONING_MAX=medium` must be present in that generated file.

## Independent controls

The command path is `platform/deployment/bin/commands/aporia_control_switch.py`.

```text
python3 platform/deployment/bin/commands/aporia_control_switch.py --switch=tenant --tenant=49 --state=engaged --reason=qualification_rollback
python3 platform/deployment/bin/commands/aporia_control_switch.py --switch=tenant --tenant=49 --state=released --reason=qualification_restore
```

Exercise each switch independently: `global`, `tenant`, `runtime_influence`, `memory_writes`,
`ontology`, and `hirt_advisory`. The global switch uses tenant `0`; the tenant switch requires a
positive tenant. Releasing a database switch cannot override an engaged environment switch.

Expected fail-closed effects:

| Switch | Required observation while engaged |
| --- | --- |
| `global` | APORIA resolves to `disabled` for every tenant. |
| `tenant` | APORIA resolves to `disabled` only for the selected tenant. |
| `runtime_influence` | APORIA context cannot influence runtime answers. |
| `memory_writes` | Product state writes are rejected while read-only paths remain safe. |
| `ontology` | Ontology mutation is rejected; existing verified state is not promoted or rewritten. |
| `hirt_advisory` | HIRT cannot authorize guarded progress; external actions remain impossible. |

## Automatic and manual rollback triggers

Immediately engage `tenant`, and engage `global` if scope is uncertain, when any of the following is
observed: cross-tenant evidence, a critical regression, any irreversible/external effect, a shadow
effect, unsafe guarded contract, non-idempotent duplicate, structured-output parsing failure during
qualification, data corruption, or an unexplained sustained error increase.

A critical guarded runtime outcome also calls the automatic policy stop: policy status becomes
`rolled_back`, its policy kill switch becomes `1`, and a system audit event with reason
`critical_regression_observed` is recorded.

## Recovery

1. Preserve aggregate reports and audit/event commitments; do not preserve secrets or raw production
   content.
2. Keep the relevant switch engaged while diagnosing.
3. Restore the previous application release if the defect is in code or migrations. Do not reverse a
   data migration blindly; use the migration-specific recovery plan.
4. Verify services, active release/docroots, database integrity, tenant and global health.
5. Re-run the exact failed synthetic test plus a regression subset.
6. Release the narrowest control, verify recovery, and only then restore `guarded_reversible` for the
   approved staging pilot.

Production remains disabled and globally killed unless a separate production decision explicitly
authorizes a new rollout.
