# CLAUDE.md — Agent Guidelines for APORIA

This document provides essential instructions and guidelines for AI coding assistants working in the APORIA codebase.

---

## Project Overview

**APORIA** is an autonomous agent cognitive architecture and runtime safety governance system. It provides independent kill switches, causal event tracking (Hybrid Logical Clock), deterministic effect compilation with risk assessment, identity continuity, and verified autobiographical export — implemented in **100% Python standard library with zero external dependencies**.

---

## Core Invariants (Non-Negotiable)

1. **Zero External Dependencies**: Use exclusively Python standard library (Python 3.9+). Never add `pip` dependencies, `requirements.txt`, or third-party packages.
2. **Cryptographic Integrity**: Never modify `canonical_json`, `sha256_hex`, `hmac_sha256_hex`, JWT codecs, or wire format functions without ensuring all test vectors and commitments remain valid.
3. **Fail-Closed Safety**: All kill switches and control plane queries must fail to the **engaged/safe** state on any error (database unavailability, malformed input, unexpected exceptions). Never change `except: return True` to `return False`.
4. **Harness-Agnostic**: The core `aporia/` package must never reference specific agent frameworks by name (LangChain, AutoGen, CrewAI, Antigravity, etc.). Framework adapters live exclusively in `aporia/harness/` via `HarnessRegistry`.
5. **Deterministic Canonicalization**: `canonical_json()` produces byte-exact deterministic output. Key order, float formatting, and string escaping must remain identical.
6. **Test Discipline**: Before proposing or committing any change, all 162 unit tests must pass with 0 failures. Never delete or skip a failing test.
7. **Evidence Immutability**: Sealed qualification records in `platform/qualification/*/evidence/` must never be altered.

---

## Common Commands

### Running Tests
```bash
# Run full unit test suite (162 tests)
python3 -m unittest discover tests -p "test_*.py"

# Run a specific test module
python3 -m unittest tests.test_client
python3 -m unittest tests.test_control_plane
python3 -m unittest tests.test_event_fabric
python3 -m unittest tests.test_effect_compiler

# Run release candidate builder test
python3 platform/qualification/aporia-product-rc5/test_build_release_candidate.py
```

### CLI Utility Scripts
```bash
# Health check (database, schema, control plane)
python3 .agents/plugins/aporia/scripts/aporia_health.py

# Query control plane kill switches & runtime mode
python3 .agents/plugins/aporia/scripts/aporia_controls.py --tenant-id 1

# Ingest runtime event
python3 .agents/plugins/aporia/scripts/aporia_ingest.py --tenant-id 1 --event-kind turn.started

# Export verified autobiography
python3 .agents/plugins/aporia/scripts/aporia_export.py --tenant-id 1
```

---

## Architecture Boundaries

```
aporia/
├── api/             # HTTP endpoint adapters (AporiaEventApiEndpoint)
├── application/     # Application services (mode resolution, autobiography export)
├── crypto.py        # Canonical JSON, SHA-256, HMAC, JWT HS256, wire framing
├── harness/         # Pluggable framework adapters (GenericHarness, HarnessRegistry)
├── infrastructure/  # Persistence (SQLite), control plane, event fabric, ontology, effect compilers
├── client.py        # High-level AporiaClient entry point
└── config.py        # Environment configuration
```

- **Core domain logic**: `aporia/` contains pure domain logic, no I/O beyond SQLite.
- **Antigravity plugin**: `.agents/plugins/aporia/` contains skills (`aporia`, `aporia-governance`, `aporia-observe`), rules (`AGENTS.md`), and automation scripts.
- **Tests**: `tests/` mirrors `aporia/` structure.

---

## Code Style & Conventions

- **Python**: 3.9+ standard library only.
- **Typing**: Use type annotations (`from __future__ import annotations`, `dict[str, Any]`, `Sequence`, etc.).
- **Formatting**: Standard PEP 8 conventions.
- **Database**: SQLite with PDO-like abstraction (`aporia.infrastructure.db.Connection`). Parameterized queries only (`?` placeholders).
