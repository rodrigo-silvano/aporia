# APORIA

<p align="center">
  <strong>Cognitive Architecture, Scientific Safety & Runtime Governance for Autonomous Agents</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/dependencies-zero%20external-brightgreen.svg" alt="Zero External Dependencies">
  <img src="https://img.shields.io/badge/tests-162%20passed-success.svg" alt="162 Tests Passed">
  <img src="https://img.shields.io/badge/architecture-harness--agnostic-orange.svg" alt="Harness Agnostic">
  <img src="https://img.shields.io/badge/license-MIT-lightgrey.svg" alt="License">
</p>

---

## What is APORIA?

**APORIA** is an autonomous agent cognitive architecture and **scientific runtime safety governance system**.

When AI agents operate in the real world — invoking external tools, querying databases, dispatching emails, or executing code — they require strict runtime guardrails. APORIA acts as an **independent control plane**, ensuring that every agent action is audited, risk-assessed, and governed by **fail-closed kill switches** that guarantee safety under unexpected failures or adversarial conditions.

Engineered with a strict **zero external dependencies** philosophy (built 100% on the Python 3.9+ standard library), APORIA is **completely harness-agnostic**: it works seamlessly across any LLM and any agent orchestration framework, including Antigravity, LangChain, AutoGen, CrewAI, or custom HTTP gateways.

---

## Core Architecture & Pillars

```
                 ┌───────────────────────────────────────┐
                 │        Autonomous Agent / LLM         │
                 │  (Antigravity / LangChain / CrewAI)   │
                 └──────────────────┬────────────────────┘
                                    │ (JWT HS256)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                               APORIA                                   │
│                                                                        │
│   ┌─────────────────────┐    ┌─────────────────────────────────────┐   │
│   │   Control Plane     │    │        Effect Compiler              │   │
│   │  • Kill Switches    │    │  • Risk Assessment (0.05 - 0.95)    │   │
│   │  • Fail-Closed      │    │  • Approval Gates                   │   │
│   │  • Mode Resolution  │    │  • Cryptographic Commitments        │   │
│   └──────────┬──────────┘    └──────────────────┬──────────────────┘   │
│              │                                  │                      │
│              ▼                                  ▼                      │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │                      Causal Event Fabric                       │   │
│   │  • Hybrid Logical Clock (HLC)     • Idempotent Ingestion       │   │
│   │  • Causal DAG & Lineage           • Canonical SHA-256 Hashing  │   │
│   └──────────────────────────────┬─────────────────────────────────┘   │
│                                  │                                     │
│                                  ▼                                     │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │               Autobiography & Continuity                       │   │
│   │  • Verified Narrative Chains      • Identity Snapshots         │   │
│   └────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### 1. Independent Control Plane (*Fail-Closed Safety*)
Features 6 independent kill switches (`global`, `tenant`, `runtime_influence`, `memory_writes`, `ontology`, `hirt_advisory`). Upon database unavailability, malformed payloads, or internal errors, the system fails closed to the safe, engaged state.

### 2. Deterministic Effect Compilation & Risk Scoring
Prior to tool execution, each proposed action is compiled with deterministic risk assessment (ranging from `0.05` for safe reads to `0.95` for irreversible external side-effects). Critical operations require immutable cryptographic authority commitments and approval gates.

### 3. Causal Event Fabric with Hybrid Logical Clock (HLC)
Tracks all agent runtime events within a causal directed acyclic graph (DAG). Uses Hybrid Logical Clocks to enforce strict causal ordering, concurrent event detection, and deduplication via byte-exact canonical SHA-256 hashing.

### 4. Identity Continuity & Autobiographical Memory
Maintains the agent's historical trajectory in hash-chained autobiographical ledgers with cryptographic signatures, enabling export of formal, tamper-evident proofs of policy alignment.

### 5. Zero External Dependencies & Universal Interoperability
Zero `pip` packages. All cryptographic primitives (HMAC-SHA256, JWT encoding/decoding, canonical JSON serialization, big-endian wire framing) are implemented directly in Python standard library.

---

## Getting Started

### Prerequisites
- Python 3.9+ (No `pip install` or virtual environment required)

### Configuration
Copy the environment template:
```bash
cp .env.example .env
```

### Quick Start in Python

```python
from aporia.client import AporiaClient
from aporia.infrastructure.db import get_connection
from aporia.infrastructure.schema import SCHEMA_SQL

# 1. Connect to database (in-memory SQLite or persistent file)
conn = get_connection()  # Reads APORIA_DB_PATH or defaults to :memory:
conn.executescript(SCHEMA_SQL)

# 2. Initialize APORIA client
client = AporiaClient(pdo=conn, secret="your-bridge-secret")
assert client.is_healthy()

# 3. Query independent control plane
snapshot = client.controls.snapshot(tenant_id=1)
print(snapshot)
# {'global': False, 'tenant': False, 'runtime_influence': False, ...}
```

### Universal Agent Authentication (Any Framework)

Any autonomous agent framework can authenticate with APORIA using standard HMAC-SHA256 JWT tokens:

```python
from aporia.crypto import jwt_encode
import time

token = jwt_encode(
    {
        "iss": "my-agent-framework",  # e.g., "langchain", "crewai", "antigravity"
        "aud": "aporia-runtime",
        "tenant_id": 1,
        "agent_session_id": "session-001",
        "exp": int(time.time()) + 3600,
    },
    key="your-bridge-secret",
)
```

---

## CLI Automation Tools

APORIA ships with production-ready command-line utilities:

| Tool | Purpose | Example |
| :--- | :--- | :--- |
| **`aporia_health.py`** | Database health check and control plane snapshot | `python3 .agents/plugins/aporia/scripts/aporia_health.py` |
| **`aporia_controls.py`** | Query kill switches and runtime mode per tenant | `python3 .agents/plugins/aporia/scripts/aporia_controls.py --tenant-id 1` |
| **`aporia_ingest.py`** | Ingest causal events with canonical SHA-256 and HLC | `python3 .agents/plugins/aporia/scripts/aporia_ingest.py --tenant-id 1 --event-kind turn.started` |
| **`aporia_export.py`** | Export cryptographically verified autobiography chain | `python3 .agents/plugins/aporia/scripts/aporia_export.py --tenant-id 1` |

---

## Antigravity Plugin & Skills

APORIA is packaged as a native Antigravity agent plugin under `.agents/plugins/aporia/`, providing 3 specialized skills with **Progressive Disclosure**:

- **`aporia`**: Main router skill for initial setup, architecture guidance, and health checks.
- **`aporia-governance`**: Control plane queries, kill switches, runtime mode resolution (`guarded_reversible`), and tool effect compilation.
- **`aporia-observe`**: Event telemetry ingestion, causal DAG exploration, and autobiographical audit trails.

---

## Repository Layout

```text
aporia/
├── aporia/                      # Core cognitive architecture & governance package
│   ├── api/                     # HTTP endpoint adapters (AporiaEventApiEndpoint)
│   ├── application/             # Runtime mode resolution & autobiography export
│   ├── crypto.py                # Pure standard library cryptography (SHA-256, HMAC, JWT)
│   ├── harness/                 # Pluggable framework adapters (GenericHarness, Registry)
│   ├── infrastructure/          # Control plane, event fabric, effect compilers, SQLite
│   ├── client.py                # High-level AporiaClient interface
│   └── config.py                # Environment configuration loader
├── .agents/plugins/aporia/      # Antigravity plugin (Skills, Scripts, Rules)
├── tests/                       # Comprehensive unit test suite (162 tests)
├── experiments/                 # Empirical protocols & formal verification
├── platform/                    # Database migrations & qualification pipelines
├── AGENTS.md                    # Mandatory agent instructions & invariants
├── CLAUDE.md                    # Claude Code operational directives
└── README.md                    # Project documentation
```

---

## Verification & Testing

Every invariant, state transition, and cryptographic commitment is verified by **162 automated unit tests**:

```bash
python3 -m unittest discover tests -p "test_*.py"
```

```text
Ran 162 tests in 6.563s
OK (skipped=2)
```

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
