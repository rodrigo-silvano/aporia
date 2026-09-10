---
name: aporia
description: >-
  APORIA cognitive architecture for autonomous agent safety and runtime governance.
  Use when the user asks to set up, configure, integrate, or understand APORIA.
  Trigger on mentions of: 'APORIA', 'cognitive architecture', 'agent governance',
  'runtime safety', 'kill switches', 'event fabric', 'effect compiler',
  'harness integration', 'agent observation', or 'autobiographical export'.
  Routes to specialized sub-skills for governance (aporia-governance) and
  observation (aporia-observe).
---

# APORIA — Cognitive Architecture for Agent Safety

APORIA provides scientific safety, runtime governance, and behavioral observation
for autonomous agents. It is 100% Python standard library with zero external
dependencies and works with any agent harness or model.

## When to Use This Skill

- Setting up APORIA for the first time
- Integrating APORIA with an agent runtime or harness
- Understanding APORIA's architecture and capabilities
- Routing to specialized governance or observation workflows

## Quick Start

### 1. Initialize the Database and Client

```python
from aporia.client import AporiaClient
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL

# Create connection (in-memory for development, file path for production)
conn = get_connection()  # Uses APORIA_DB_PATH env var or :memory:
conn.executescript(SCHEMA_SQL)

# Initialize client
client = AporiaClient(pdo=conn, secret="your-bridge-secret")
assert client.is_healthy()
```

### 2. Integrate with Any Harness

APORIA accepts JWT tokens signed with HMAC-SHA256 from any agent framework:

```python
from aporia.crypto import jwt_encode

token = jwt_encode(
    {
        "iss": "my-agent-framework",   # Any issuer
        "aud": "aporia-runtime",        # Any audience
        "tenant_id": 1,
        "agent_session_id": "session-001",
        "exp": 1893456000,
    },
    key="your-bridge-secret",
)
```

### 3. Health Check Script

Run the built-in health check:

```bash
python3 .agents/plugins/aporia/scripts/aporia_health.py
```

## Core Capabilities

| Capability | Module | Description |
| :--- | :--- | :--- |
| **Kill Switches** | `aporia.infrastructure.control_plane` | Independent, fail-closed safety switches |
| **Event Fabric** | `aporia.infrastructure.event_fabric` | Causal DAG with HLC, idempotent ingestion |
| **Effect Compiler** | `aporia.infrastructure.effect_compiler` | Deterministic risk assessment for tool calls |
| **Identity Continuity** | `aporia.infrastructure.identity_continuity` | Cryptographic identity chain tracking |
| **Ontology** | `aporia.infrastructure.ontology` | Runtime ontology management |
| **Perspective Projector** | `aporia.infrastructure.perspective_projector` | Behavioral observation and projection |
| **Autobiography** | `aporia.application.autobiography_export` | Verified narrative export |
| **Harness Layer** | `aporia.harness` | Pluggable adapters for any agent framework |

## Routing to Sub-Skills

- **For governance, kill switches, runtime modes, and effect compilation**: Load the `aporia-governance` skill.
- **For event ingestion, state queries, and autobiography export**: Load the `aporia-observe` skill.

## Environment Configuration

Copy `.env.example` to `.env` and configure:

```bash
APORIA_DB_PATH=aporia.sqlite        # Database path (or :memory:)
APORIA_BRIDGE_SECRET=your-secret     # JWT verification secret
APORIA_MODE=guarded_reversible       # disabled | advisory | guarded_reversible (default: guarded_reversible)
APORIA_ENV=staging                   # production | staging
```

See the full variable reference in [AGENTS.md](../../rules/AGENTS.md).
