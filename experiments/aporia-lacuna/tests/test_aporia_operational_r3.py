from __future__ import annotations

from pathlib import Path

from operational_r3.protocol import DEVELOPMENT_SEEDS, run_protocol
from operational_r3.sealing import assert_current_seal


ROOT = Path(__file__).resolve().parents[3]


def test_operational_r3_seal_and_all_required_scenarios() -> None:
    assert_current_seal(ROOT)
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
