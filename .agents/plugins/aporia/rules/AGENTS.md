# APORIA — Project Rules & Instructions for Agents

These rules and guidelines are mandatory for any autonomous agent (Antigravity, Codex, Claude, etc.) working within the APORIA codebase.

---

## Core Invariants (Non-Negotiable)

1. **Zero External Dependencies**: APORIA uses exclusively Python standard library (Python 3.9+). Never add `pip` packages, `requirements.txt`, or third-party imports. All cryptographic primitives, serialization, and networking are implemented in-tree.

2. **Cryptographic Integrity**: Never modify hashing algorithms (`sha256_hex`, `hmac_sha256_hex`), canonical JSON serialization (`canonical_json`), JWT encoding/decoding, or wire format functions without formal verification that all existing test vectors and commitments remain valid.

3. **Fail-Closed Safety**: All kill switches and control plane queries must fail to the **engaged/safe** state on any error (database unavailability, malformed input, unexpected exceptions). Never change an `except: return True` guard to `return False`.

4. **Harness-Agnostic**: The core `aporia/` package must never reference specific agent frameworks (LangChain, AutoGen, CrewAI, Antigravity, etc.) by name. All framework-specific logic lives exclusively in `aporia/harness/` adapters registered via `HarnessRegistry`.

5. **Test Discipline**: Before proposing any code change, run `python3 -m unittest discover tests -p "test_*.py"`. All 162 tests must pass with 0 failures. Never skip or delete a failing test without explicit user authorization.

6. **Evidence Immutability**: Files under `platform/qualification/*/evidence/` are sealed qualification records. They must never be modified except during an authorized re-qualification process.

7. **Deterministic Canonicalization**: The `canonical_json()` function produces byte-exact deterministic output. Any change to its serialization rules invalidates every stored commitment, hash, and signature in the system.

---

## Architecture Boundaries

- **`aporia/`**: Core cognitive architecture. Pure domain logic, no I/O beyond SQLite.
- **`aporia/api/`**: HTTP endpoint adapters. Thin layer calling core services.
- **`aporia/harness/`**: Pluggable agent framework adapters. Each adapter is independent.
- **`aporia/infrastructure/`**: Persistence, event fabric, ontology, and ecological programs.
- **`aporia/application/`**: Application services (mode resolution, context, export).
- **`.agents/plugins/aporia/`**: Agent integration layer with skills, rules, and CLI automation scripts.
- **`tests/`**: Unit and integration tests. Mirror the `aporia/` structure.
- **`experiments/`**: Research protocols. Sealed experiments must not be modified.
- **`platform/`**: Deployment, migrations, and qualification. Operational scripts.

---

## Key Commands for Agents

### Running Tests
```bash
# Full test suite (162 tests)
python3 -m unittest discover tests -p "test_*.py"

# Individual test modules
python3 -m unittest tests.test_client
python3 -m unittest tests.test_control_plane
python3 -m unittest tests.test_event_fabric
```

### CLI Automation Scripts
```bash
# Health check
python3 .agents/plugins/aporia/scripts/aporia_health.py

# Query control plane kill switches & runtime mode
python3 .agents/plugins/aporia/scripts/aporia_controls.py --tenant-id 1

# Ingest event into causal fabric
python3 .agents/plugins/aporia/scripts/aporia_ingest.py --tenant-id 1 --event-kind turn.started

# Export verified autobiography
python3 .agents/plugins/aporia/scripts/aporia_export.py --tenant-id 1
```

---

## Environment Variables

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `APORIA_DB_PATH` | SQLite database path | `:memory:` |
| `APORIA_BRIDGE_SECRET` | HMAC-SHA256 secret for JWT verification | (required for production) |
| `APORIA_MODE` | Global runtime mode (`disabled`, `advisory`, `guarded_reversible`) | `guarded_reversible` |
| `APORIA_KILL_GLOBAL` | Global kill switch | `0` |
| `APORIA_KILL_RUNTIME_INFLUENCE` | Runtime influence kill switch | `0` |
| `APORIA_KILL_MEMORY_WRITES` | Memory writes kill switch | `0` |
| `APORIA_KILL_ONTOLOGY` | Ontology kill switch | `0` |
| `APORIA_KILL_HIRT_ADVISORY` | HIRT advisory kill switch | `0` |
| `APORIA_ENV` | Deployment environment | `production` |
