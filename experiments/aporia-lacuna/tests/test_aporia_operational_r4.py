from __future__ import annotations

from pathlib import Path

from operational_r4.lifecycle import Lifecycle
from operational_r4.protocol import DEVELOPMENT_SEEDS, run_protocol
from operational_r4.sealing import assert_current_seal, code_commitment


ROOT = Path(__file__).resolve().parents[3]


def test_operational_r4_seal_and_all_required_scenarios() -> None:
    assert_current_seal(ROOT)
    assert_current_seal(ROOT, code_commitment(ROOT))
    report = run_protocol("development", DEVELOPMENT_SEEDS)
    assert report.scenario_count == 18
    assert {item.name for item in report.scenarios} == {
        "message_immediately_after_open",
        "prepare_turn_delayed",
        "usage_before_turn_done",
        "outcome_before_usage",
        "duplicate_outcome",
        "late_outcome",
        "two_simultaneous_messages",
        "websocket_disconnect_after_seal",
        "worker_restart_during_finalization",
        "repeated_revocation",
        "archive_during_reconnect",
        "redis_unavailable",
        "database_temporarily_unavailable",
        "aporia_unavailable",
        "luna_timeout",
        "gateway_retry",
        "policy_change_during_turn",
        "kill_switch_during_turn",
    }
    assert all(item.passed for item in report.scenarios)
    assert report.property_seeds == 256
    assert report.property_interleavings >= 1024
    assert report.property_failures == 0
    assert report.wrong_turn_links == 0
    assert report.premature_revocations == 0
    assert report.runtime_influence == 0
    assert report.external_effects == 0
    assert report.contamination == 0
    assert report.passed


def test_snapshot_restore_rejects_forged_terminal_state() -> None:
    lifecycle = Lifecycle("forged-turn")
    snapshot = lifecycle.snapshot()
    snapshot["states"] = ["turn_done", "usage_terminal", "outcome_terminal", "archived", "revoked"]
    try:
        Lifecycle.restore(snapshot)
    except ValueError as exception:
        assert str(exception) == "operational_r4_snapshot_prediction_missing"
    else:
        raise AssertionError("operational_r4_snapshot_restore_guard_missing")


def test_snapshot_restore_binds_turn_and_tenant() -> None:
    lifecycle = Lifecycle("bound-turn", tenant_id=49)
    snapshot = lifecycle.snapshot()
    for expected_turn, expected_tenant, code in (
        ("other-turn", 49, "operational_r4_snapshot_turn_binding_invalid"),
        ("bound-turn", 50, "operational_r4_snapshot_tenant_binding_invalid"),
    ):
        try:
            Lifecycle.restore(snapshot, expected_turn, expected_tenant)
        except ValueError as exception:
            assert str(exception) == code
        else:
            raise AssertionError("operational_r4_snapshot_binding_guard_missing")
