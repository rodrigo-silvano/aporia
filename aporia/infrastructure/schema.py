"""
Schema creation and migration definitions for APORIA.
Generates all SQLite-compatible tables corresponding to the 23 SQL migrations.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aporia.infrastructure.db import Connection

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS aporia_tenant_clocks (
    tenant_id INTEGER NOT NULL PRIMARY KEY,
    hlc_wall_us INTEGER NOT NULL DEFAULT 0,
    hlc_logical INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assistant_runtime_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    owner_user_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    agent_session_id TEXT NOT NULL,
    profile_id INTEGER,
    workspace_profile_id INTEGER,
    relationship_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    title TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    block_type TEXT NOT NULL DEFAULT 'none'
);

CREATE TABLE IF NOT EXISTS aporia_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    runtime_session_id INTEGER NOT NULL,
    session_ref TEXT NOT NULL,
    task_ref TEXT,
    event_kind TEXT NOT NULL,
    source_service TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    journal_sequence INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    hlc_wall_us INTEGER NOT NULL,
    hlc_logical INTEGER NOT NULL,
    causal_depth INTEGER NOT NULL,
    attributes_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    completeness TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, event_id),
    UNIQUE (tenant_id, source_service, event_id),
    UNIQUE (tenant_id, session_ref, journal_sequence),
    UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS aporia_lacuna_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    tenant_id INTEGER NOT NULL,
    session_ref TEXT NOT NULL,
    task_ref TEXT,
    status TEXT NOT NULL,
    instrument TEXT NOT NULL,
    input_state_json TEXT NOT NULL,
    output_state_json TEXT,
    order_score REAL,
    changed_dimensions INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aporia_lacuna_crypto_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    latent_id TEXT NOT NULL UNIQUE,
    instrument TEXT NOT NULL,
    input_ciphertext BLOB NOT NULL,
    input_nonce TEXT NOT NULL,
    destroyed_ciphertext BLOB NOT NULL,
    destroyed_nonce TEXT NOT NULL,
    input_commitment TEXT NOT NULL,
    output_commitment TEXT NOT NULL,
    capability_hash TEXT NOT NULL UNIQUE,
    destruction_receipt TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    process_exit_code INTEGER NOT NULL,
    core_dumps_disabled INTEGER NOT NULL,
    memory_lock_status TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    environment TEXT NOT NULL,
    stream TEXT NOT NULL,
    category TEXT NOT NULL,
    component TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    action_name TEXT NOT NULL,
    lifecycle_phase TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_code TEXT,
    trace_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, run_id, instrument)
);

CREATE TABLE IF NOT EXISTS aporia_shadow_perspective_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    event_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, event_id)
);

CREATE TABLE IF NOT EXISTS aporia_shadow_perspective_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    perspective_kind TEXT NOT NULL,
    projection_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aporia_autobiography_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    causal_event_id TEXT NOT NULL,
    episode_ref TEXT NOT NULL,
    entry_kind TEXT NOT NULL,
    evaluability TEXT NOT NULL,
    ontology_before_commitment TEXT NOT NULL,
    ontology_after_commitment TEXT,
    transformation_commitment TEXT NOT NULL,
    lost_distinction_hashes_json TEXT NOT NULL,
    excluded_future_hashes_json TEXT NOT NULL,
    irrecoverability REAL,
    causal_efficacy REAL,
    autobiographic_time INTEGER,
    confidence REAL,
    reason_codes_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    previous_signature TEXT NOT NULL,
    entry_signature TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, entry_id)
);

CREATE TABLE IF NOT EXISTS aporia_identity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    autobiography_entry_id TEXT NOT NULL,
    graph_root TEXT NOT NULL,
    behavior_root TEXT NOT NULL,
    commitment_root TEXT NOT NULL,
    autobiography_root TEXT NOT NULL,
    ontology_root TEXT NOT NULL,
    graph_count INTEGER NOT NULL,
    behavior_count INTEGER NOT NULL,
    commitment_count INTEGER NOT NULL,
    autobiography_count INTEGER NOT NULL,
    ontology_count INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    continuity_score REAL NOT NULL,
    model_dependency TEXT NOT NULL,
    previous_signature TEXT NOT NULL,
    snapshot_signature TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS aporia_identity_commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commitment_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    commitment_key TEXT NOT NULL,
    commitment_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    first_causal_event_id TEXT NOT NULL,
    last_causal_event_id TEXT NOT NULL,
    committed_at TEXT,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, commitment_id),
    UNIQUE (tenant_id, commitment_key)
);

CREATE TABLE IF NOT EXISTS aporia_identity_commitment_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    commitment_id INTEGER NOT NULL,
    autobiography_entry_id TEXT NOT NULL,
    causal_event_id TEXT NOT NULL,
    evidence_ordinal INTEGER NOT NULL,
    resulting_status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS aporia_ontology_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    label TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, node_id)
);

CREATE TABLE IF NOT EXISTS aporia_ontology_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, source_node_id, target_node_id, relation_type)
);

CREATE TABLE IF NOT EXISTS aporia_ontology_residuals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    residual_id TEXT NOT NULL,
    description TEXT NOT NULL,
    unresolved_state_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, residual_id)
);

CREATE TABLE IF NOT EXISTS aporia_obstruction_hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    hypothesis_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, hypothesis_id)
);

CREATE TABLE IF NOT EXISTS aporia_empirical_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    protocol TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, run_id)
);

CREATE TABLE IF NOT EXISTS aporia_guarded_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    kill_switch INTEGER NOT NULL DEFAULT 0,
    reversible_only INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id)
);

CREATE TABLE IF NOT EXISTS aporia_ecological_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    turn_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    outcome TEXT NOT NULL,
    observed_metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, outcome_id)
);

CREATE TABLE IF NOT EXISTS aporia_ecological_predictions_r3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    turn_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    predicted_distribution_json TEXT NOT NULL,
    transport_attestation_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, prediction_id)
);

CREATE TABLE IF NOT EXISTS aporia_ecological_transport_attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attestation_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    session_ref TEXT NOT NULL,
    request_ref TEXT NOT NULL,
    event_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    initiator_type TEXT NOT NULL,
    executor_type TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    attestation_basis TEXT NOT NULL,
    transport_attestation_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, attestation_id)
);

CREATE TABLE IF NOT EXISTS aporia_independent_control_switches (
    tenant_id INTEGER NOT NULL,
    switch_name TEXT NOT NULL,
    engaged INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    changed_by_user_id INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, switch_name)
);

CREATE TABLE IF NOT EXISTS aporia_independent_control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    switch_name TEXT NOT NULL,
    state_before INTEGER NOT NULL,
    state_after INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    event_name TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    environment TEXT NOT NULL,
    stream TEXT NOT NULL,
    category TEXT NOT NULL,
    component TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    action_name TEXT NOT NULL,
    lifecycle_phase TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, event_id),
    UNIQUE (tenant_id, operation_id)
);

CREATE TABLE IF NOT EXISTS aporia_longitudinal_shadow_twins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    twin_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    turn_sequence INTEGER NOT NULL,
    state_vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, twin_id)
);

CREATE TABLE IF NOT EXISTS aporia_shadow_lifecycle_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    lifecycle_state TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, item_id)
);

CREATE TABLE IF NOT EXISTS aporia_effect_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    decision TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reversible INTEGER NOT NULL,
    external_effect INTEGER NOT NULL,
    idempotent INTEGER NOT NULL,
    approval_required INTEGER NOT NULL,
    guarded_eligible INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, operation_id)
);

CREATE TABLE IF NOT EXISTS aporia_effect_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, operation_id)
);

CREATE TABLE IF NOT EXISTS aporia_product_state_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    entity_id TEXT NOT NULL,
    state_type TEXT NOT NULL,
    value_json TEXT NOT NULL,
    value_commitment TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    confidence REAL NOT NULL,
    provenance_json TEXT NOT NULL,
    causal_parent_ids_json TEXT NOT NULL,
    ontology_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    verified INTEGER NOT NULL,
    authority_rank INTEGER NOT NULL,
    freshness_half_life_seconds INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    supersedes_state_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, state_id),
    UNIQUE (tenant_id, entity_id, state_type, revision)
);

CREATE TABLE IF NOT EXISTS aporia_product_state_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    source_event_ref TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    envelope_json TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    selected_count INTEGER NOT NULL,
    excluded_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    policy_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    valid_until TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, snapshot_id),
    UNIQUE (tenant_id, source_event_ref)
);

CREATE TABLE IF NOT EXISTS aporia_product_context_exposures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exposure_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    turn_ref TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    mode TEXT NOT NULL,
    envelope_commitment TEXT NOT NULL,
    receipt_signature TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, exposure_id),
    UNIQUE (tenant_id, turn_ref)
);

CREATE TABLE IF NOT EXISTS aporia_product_runtime_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    exposure_id TEXT NOT NULL,
    decision_commitment TEXT NOT NULL,
    helpful INTEGER,
    latency_ms INTEGER NOT NULL,
    cost_units REAL NOT NULL,
    critical_regression INTEGER NOT NULL,
    evaluability TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, outcome_id),
    UNIQUE (tenant_id, exposure_id)
);

CREATE TABLE IF NOT EXISTS aporia_product_lacuna_one_shots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    one_shot_id TEXT NOT NULL,
    tenant_id INTEGER NOT NULL,
    session_binding TEXT NOT NULL,
    turn_binding TEXT NOT NULL,
    instrument TEXT NOT NULL,
    capability_hash TEXT NOT NULL,
    nonce_base64 TEXT NOT NULL,
    ciphertext_base64 TEXT NOT NULL,
    input_commitment TEXT NOT NULL,
    output_commitment TEXT,
    destruction_receipt TEXT,
    schema_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, one_shot_id)
);

CREATE TABLE IF NOT EXISTS ai_provider_response_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    incomplete_reason TEXT,
    error_code TEXT,
    input_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init_schema(conn: Connection) -> None:
    """Execute SQLite schema initialization."""
    conn.raw_connection.executescript(SCHEMA_SQL)


init_db = init_schema
