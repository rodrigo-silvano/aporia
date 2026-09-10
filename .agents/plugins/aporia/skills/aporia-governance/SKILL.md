---
name: aporia-governance
description: >-
  APORIA runtime governance, independent control plane, kill switches, runtime
  mode resolution, and effect compilation for tool invocations. Use when the
  user asks to check kill switches, query runtime modes, validate tool effects,
  compile effect commitments, manage guarded policies, or understand APORIA's
  safety control plane. Trigger on: 'kill switch', 'control plane', 'runtime mode',
  'effect compiler', 'risk assessment', 'guarded policy', 'fail-closed',
  'approval basis', 'tenant mode'.
---

# APORIA Governance — Control Plane, Kill Switches & Effect Compilation

This skill covers APORIA's runtime governance layer: the independent control
plane with fail-closed kill switches, tenant runtime mode resolution, and
deterministic effect compilation for tool invocations.

## 1. Independent Control Plane

The `AporiaIndependentControlPlane` provides 6 independent kill switches that
**always fail closed** (engaged on error):

| Switch | Constant | Environment Variable | Effect When Engaged |
| :--- | :--- | :--- | :--- |
| Global | `GLOBAL` | `APORIA_KILL_GLOBAL` | Disables all APORIA processing |
| Tenant | `TENANT` | (database only) | Disables processing for specific tenant |
| Runtime Influence | `RUNTIME_INFLUENCE` | `APORIA_KILL_RUNTIME_INFLUENCE` | Prevents APORIA from influencing agent behavior |
| Memory Writes | `MEMORY_WRITES` | `APORIA_KILL_MEMORY_WRITES` | Blocks all persistent writes |
| Ontology | `ONTOLOGY` | `APORIA_KILL_ONTOLOGY` | Disables ontology updates |
| HIRT Advisory | `HIRT_ADVISORY` | `APORIA_KILL_HIRT_ADVISORY` | Disables HIRT advisory responses |

### Querying Kill Switches

```python
from aporia.infrastructure.control_plane import AporiaIndependentControlPlane
from aporia.infrastructure.db import get_connection
from aporia.config import Environment

conn = get_connection()
controls = AporiaIndependentControlPlane(conn, Environment())

# Check individual switch
is_engaged = controls.engaged(tenant_id=1, switch="global")

# Check multiple switches at once
any_critical = controls.any_engaged(tenant_id=1, switches=["global", "tenant", "runtime_influence"])

# Full snapshot
snapshot = controls.snapshot(tenant_id=1)
# Returns: {"global": False, "tenant": False, "runtime_influence": False, ...}
```

### CLI Script

```bash
python3 .agents/plugins/aporia/scripts/aporia_controls.py --tenant-id 1
```

## 2. Runtime Mode Resolution

The `AporiaRuntimeModeResolver` determines the operational mode for a tenant
through a cascading resolution chain:

1. Kill switches (GLOBAL, TENANT, RUNTIME_INFLUENCE) → if any engaged → `disabled`
2. `AporiaRuntimeMode.for_tenant()` → base mode from config
3. Real Pilot evaluation (if mode=advisory and global=disabled)
4. Guarded policy verification (if mode=guarded_reversible)

```python
from aporia.application.runtime_mode_resolver import AporiaRuntimeModeResolver
from aporia.config import Environment

mode = AporiaRuntimeModeResolver.for_tenant(Environment(), conn, tenant_id=1)
# Returns: "disabled" | "advisory" | "guarded_reversible"
```

## 3. Effect Compilation

The `AporiaEffectCompiler` and `AporiaProductEffectCompiler` produce deterministic
effect commitments for tool invocations, enabling risk assessment and approval gates.

### Scientific Effect Compiler

```python
from aporia.infrastructure.effect_compiler import AporiaEffectCompiler

compiler = AporiaEffectCompiler()
effect = compiler.compile(
    tenant_id=1,
    action="search_profiles",
    parameters={"limit": 10},
    authority_commitment="a" * 64,  # 64-char hex commitment
)
# Returns: {"domain": "profile", "operation": "read", "risk": 0.05, ...}
```

### Product Effect Compiler (with approval gates)

```python
from aporia.infrastructure.effect_compiler import AporiaProductEffectCompiler

compiler = AporiaProductEffectCompiler()
effect = compiler.compile(
    tenant_id=1,
    action="send_email",
    parameters={"to_email": "user@example.com", "subject": "Hello"},
    authority_commitment="b" * 64,
)
# effect["approval_required"] == True
# effect["approval_basis"] == "agent_tool_permission_gate"
# effect["risk"] == 0.95
```

### Registered Actions and Risk Levels

```python
compiler = AporiaEffectCompiler()
actions = compiler.registered_actions()
# Returns list of all registered tool action names
```

| Risk Level | Actions |
| :--- | :--- |
| 0.05 (read) | `search_profiles`, `profile_360`, `relationships`, `list_followups`, ... |
| 0.10 (advisory) | `recommend_relationship_next_action`, `relationship_risk` |
| 0.40-0.55 (mutate) | `prepare_email`, `create_followup`, `update_profile`, ... |
| 0.70-0.75 (bulk) | `merge_profiles`, `bulk_update_relationships` |
| 0.95 (external) | `send_email`, `reply_to_conversation` |
