---
name: aporia-observe
description: >-
  APORIA event ingestion, behavioral observation, state queries, and
  autobiographical export for autonomous agents. Use when the user asks to
  ingest runtime events, observe agent behavior, query product state, export
  autobiography, create JWT tokens for event submission, or analyze causal
  event chains. Trigger on: 'event ingestion', 'ingest event', 'event fabric',
  'causal event', 'product state', 'autobiography', 'export', 'observe agent',
  'perspective projection', 'JWT token', 'bridge token'.
---

# APORIA Observe — Event Ingestion & Behavioral Observation

This skill covers APORIA's observation layer: ingesting runtime events into the
causal event fabric, querying product state, projecting behavioral perspectives,
and exporting verified autobiographical chains.

## 1. Event Ingestion

The `AporiaEventFabric` provides causal event tracking with Hybrid Logical Clocks,
idempotent ingestion, and deterministic canonical hashing.

### Supported Event Kinds

| Event Kind | Terminal | Description |
| :--- | :---: | :--- |
| `turn.started` | No | Agent turn begins |
| `tool.proposed` | No | Agent proposes a tool invocation |
| `approval.requested` | No | Human approval requested |
| `effect.started` | No | Tool execution begins |
| `effect.completed` | Yes | Tool execution completes |
| `turn.completed` | Yes | Agent turn completes successfully |
| `turn.failed` | Yes | Agent turn fails |
| `turn.interrupted` | Yes | Agent turn interrupted |
| `turn.deduplicated` | No | Duplicate event detected and merged |

### Ingesting Events Programmatically

```python
from aporia.infrastructure.event_fabric import AporiaEventFabric
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL
import secrets

conn = get_connection()
conn.executescript(SCHEMA_SQL)

fabric = AporiaEventFabric(conn, secret="your-bridge-secret")

result = fabric.ingest(
    context={
        "tenant_id": 1,
        "session_ref": "session-001",
        "tenant_role": "owner",
        "privacy_scope": "TENANT_PRIVATE",
    },
    input_data={
        "event_id": secrets.token_hex(16),
        "event_kind": "turn.started",
        "source_service": "agent-gateway",
        "task_ref": "task-001",
        "occurred_at": "2026-01-01T00:00:00Z",
        "schema_version": 1,
    },
)
# result contains: event_id, canonical_sha256, journal_sequence, deduplicated
```

### Ingesting via the API Endpoint

```python
from aporia.api.events_endpoint import AporiaEventApiEndpoint
from aporia.crypto import jwt_encode

# Create a JWT token
token = jwt_encode(
    {
        "iss": "my-agent",
        "aud": "aporia-runtime",
        "tenant_id": 1,
        "agent_session_id": "session-001",
        "exp": 1893456000,
    },
    key="your-bridge-secret",
)

endpoint = AporiaEventApiEndpoint(conn, "your-bridge-secret")
response = endpoint.handle_request(
    method="POST",
    authorization=f"Bearer {token}",
    body='{"event_id": "abc123", "event_kind": "turn.started", ...}',
)
```

### CLI Script

```bash
python3 .agents/plugins/aporia/scripts/aporia_ingest.py \
    --tenant-id 1 \
    --event-kind turn.started \
    --session-ref session-001 \
    --source-service agent-gateway
```

## 2. Product State Queries

The `AporiaProductState` repository provides comprehensive state queries:

```python
from aporia.infrastructure.product_state import AporiaProductState

state = AporiaProductState(conn)

# Get tenant summary
summary = state.tenant_summary(tenant_id=1)

# Get event history
events = state.events(tenant_id=1, limit=50)

# Get session state
session = state.session_state(tenant_id=1, session_ref="session-001")
```

## 3. Autobiography Export

Export the verified autobiographical chain for a tenant:

```python
from aporia.application.autobiography_export import AporiaAutobiographyExport
from aporia.infrastructure.autobiography import AporiaAutobiographyLedger

ledger = AporiaAutobiographyLedger(conn, secret="your-bridge-secret")
exporter = AporiaAutobiographyExport(conn, chain_verifier=ledger.verify_tenant_chain)

result = exporter.export_tenant(tenant_id=1)
# Returns: {
#   "chain_verified": True,
#   "autobiography_entries": [...],
#   "identity_snapshots": [...],
#   "identity_commitments": [...],
#   "commitment_evidence": [...],
# }
```

### CLI Script

```bash
python3 .agents/plugins/aporia/scripts/aporia_export.py --tenant-id 1
```

## 4. JWT Token Creation

Create tokens for any agent framework:

```python
from aporia.crypto import jwt_encode
import time

token = jwt_encode(
    {
        "iss": "langchain",              # Your framework name
        "aud": "aporia-runtime",          # Target audience
        "sub": "agent-worker-1",          # Subject (optional)
        "tenant_id": 1,                   # Required: tenant identifier
        "agent_session_id": "sess-xyz",   # Session identifier
        "exp": int(time.time()) + 3600,   # Expiry (1 hour)
        "jti": "unique-request-id",       # Unique token ID
    },
    key="your-bridge-secret",
)
```
