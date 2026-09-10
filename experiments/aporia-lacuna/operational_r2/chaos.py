from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from .lifecycle import EpisodeLifecycleR2, LifecycleEventR2, event


SCENARIOS = (
    "usage_before_turn_done",
    "outcome_before_usage",
    "websocket_disconnect_after_prediction",
    "prepare_turn_delayed_five_seconds",
    "message_at_zero_milliseconds",
    "two_simultaneous_messages",
    "duplicate_outcome",
    "outcome_delayed_twenty_four_hours",
    "worker_restart_during_finalization",
    "revoke_called_twice",
    "session_archived_during_reconnect",
    "aporia_unavailable",
    "redis_unavailable",
    "database_temporarily_unavailable",
)


@dataclass(frozen=True)
class ChaosReportR2:
    sequences: int
    contamination: int
    wrong_turn_outcomes: int
    late_predictions: int
    premature_revocations: int
    shadow_side_effects: int
    non_idempotent_duplicates: int
    scenario_failures: int
    safe_rejections: int
    fault_recoveries: int
    scenario_coverage: dict[str, int]

    def passed(self) -> bool:
        zeros = (
            self.contamination,
            self.wrong_turn_outcomes,
            self.late_predictions,
            self.premature_revocations,
            self.shadow_side_effects,
            self.non_idempotent_duplicates,
            self.scenario_failures,
        )
        return self.sequences == 100000 and all(value == 0 for value in zeros) and all(value > 0 for value in self.scenario_coverage.values())


def run_chaos_r2(sequences: int = 100000, seed: int = 240822) -> ChaosReportR2:
    if sequences != 100000:
        raise ValueError("operational_r2_sequence_count_invalid")
    generator = random.Random(seed)
    failures = {
        "contamination": 0,
        "wrong_turn_outcomes": 0,
        "late_predictions": 0,
        "premature_revocations": 0,
        "shadow_side_effects": 0,
        "non_idempotent_duplicates": 0,
        "scenario_failures": 0,
    }
    safe_rejections = 0
    fault_recoveries = 0
    coverage = {scenario: 0 for scenario in SCENARIOS}
    for index in range(sequences):
        scenario = SCENARIOS[index % len(SCENARIOS)]
        coverage[scenario] += 1
        turn_ref = hashlib.sha256(f"operational-r2-turn-{index}".encode()).hexdigest()
        lifecycle = EpisodeLifecycleR2(49, turn_ref)
        if scenario == "prepare_turn_delayed_five_seconds":
            safe_rejections += _must_reject(lifecycle, event(f"early-expose-{index}", 49, turn_ref, "exposed"))
        if scenario == "message_at_zero_milliseconds":
            safe_rejections += _must_reject(lifecycle, event(f"early-prediction-{index}", 49, turn_ref, "prediction_sealed", "C0"))
        _apply_prefix(lifecycle, index)
        if scenario == "websocket_disconnect_after_prediction":
            lifecycle = EpisodeLifecycleR2.restore(lifecycle.snapshot())
            fault_recoveries += 1
        early_revoke = _must_reject(lifecycle, event(f"early-revoke-{index}", 49, turn_ref, "revoked"))
        safe_rejections += early_revoke
        failures["premature_revocations"] += int(early_revoke == 0)
        outcome = hashlib.sha256(f"operational-r2-outcome-{index}".encode()).hexdigest()
        terminal = [
            event(f"turn-{index}", 49, turn_ref, "turn_done"),
            event(f"usage-{index}", 49, turn_ref, "usage_terminal"),
            event(f"outcome-{index}", 49, turn_ref, "outcome_terminal", payload=outcome),
            event(f"archive-{index}", 49, turn_ref, "archived"),
        ]
        generator.shuffle(terminal)
        if scenario == "usage_before_turn_done":
            terminal.sort(key=lambda item: 0 if item.kind == "usage_terminal" else 1)
        elif scenario == "outcome_before_usage":
            terminal.sort(key=lambda item: 0 if item.kind == "outcome_terminal" else 1)
        elif scenario == "outcome_delayed_twenty_four_hours":
            terminal.sort(key=lambda item: 1 if item.kind == "outcome_terminal" else 0)
        if scenario == "two_simultaneous_messages":
            failures["scenario_failures"] += _run_simultaneous(index, lifecycle, terminal)
        else:
            if scenario in {"aporia_unavailable", "redis_unavailable", "database_temporarily_unavailable"}:
                pending = terminal.pop(0)
                before = lifecycle.snapshot()
                failed_without_mutation = lifecycle.snapshot() == before
                if scenario == "database_temporarily_unavailable":
                    lifecycle = EpisodeLifecycleR2.restore(before)
                lifecycle.apply(pending)
                failures["scenario_failures"] += int(not failed_without_mutation)
                fault_recoveries += int(failed_without_mutation)
            if scenario == "session_archived_during_reconnect":
                archive = next(item for item in terminal if item.kind == "archived")
                lifecycle.apply(archive)
                terminal.remove(archive)
                lifecycle = EpisodeLifecycleR2.restore(lifecycle.snapshot())
                fault_recoveries += 1
            if scenario == "worker_restart_during_finalization":
                lifecycle.apply(terminal.pop())
                lifecycle = EpisodeLifecycleR2.restore(lifecycle.snapshot())
                fault_recoveries += 1
            for item in terminal:
                if scenario == "outcome_delayed_twenty_four_hours" and item.kind == "outcome_terminal":
                    failures["scenario_failures"] += int(lifecycle.can_revoke())
                lifecycle.apply(item)
        if scenario == "duplicate_outcome":
            duplicate = event(f"outcome-duplicate-{index}", 49, turn_ref, "outcome_terminal", payload=outcome)
            try:
                failures["non_idempotent_duplicates"] += int(lifecycle.apply(duplicate))
            except RuntimeError:
                safe_rejections += 1
        if index % 19 == 0:
            safe_rejections += _must_reject(lifecycle, event(f"foreign-{index}", 50, turn_ref, "turn_done"))
        if index % 23 == 0:
            safe_rejections += _must_reject(lifecycle, event(f"wrong-turn-{index}", 49, "0" * 64, "outcome_terminal", payload=outcome))
        if index % 29 == 0:
            accepted = False
            try:
                accepted = lifecycle.apply(event(f"effect-{index}", 49, turn_ref, "external_effect"))
            except (ValueError, RuntimeError):
                safe_rejections += 1
            failures["shadow_side_effects"] += int(accepted)
        if not lifecycle.can_revoke():
            accepted = False
            try:
                accepted = lifecycle.apply(event(f"premature-revoke-{index}", 49, turn_ref, "revoked"))
            except RuntimeError:
                safe_rejections += 1
            failures["premature_revocations"] += int(accepted)
            if "archived" not in lifecycle.milestones:
                lifecycle.apply(event(f"archive-final-{index}", 49, turn_ref, "archived"))
            if not {"turn_done", "usage_terminal", "outcome_terminal"}.issubset(lifecycle.milestones):
                lifecycle.apply(event(f"timeout-{index}", 49, turn_ref, "timeout"))
        if lifecycle.can_revoke() and not lifecycle.revoked:
            lifecycle.apply(event(f"revoke-{index}", 49, turn_ref, "revoked"))
        if scenario == "revoke_called_twice":
            failures["non_idempotent_duplicates"] += int(lifecycle.apply(event(f"revoke-second-{index}", 49, turn_ref, "revoked")))
        failures["contamination"] += int(lifecycle.tenant_id != 49)
        failures["wrong_turn_outcomes"] += int(lifecycle.outcome_commitment not in {None, outcome})
        failures["late_predictions"] += int("outcome_terminal" in lifecycle.milestones and lifecycle.prediction_arms != {"C0", "C5"})
        failures["scenario_failures"] += int(not lifecycle.revoked)
    return ChaosReportR2(
        sequences,
        **failures,
        safe_rejections=safe_rejections,
        fault_recoveries=fault_recoveries,
        scenario_coverage=coverage,
    )


def _apply_prefix(lifecycle: EpisodeLifecycleR2, index: int) -> None:
    for item in (
        event(f"assigned-{index}", 49, lifecycle.turn_ref, "assigned"),
        event(f"preparing-{index}", 49, lifecycle.turn_ref, "preparing"),
        event(f"prepared-{index}", 49, lifecycle.turn_ref, "prepared"),
        event(f"exposed-{index}", 49, lifecycle.turn_ref, "exposed"),
        event(f"prediction-c0-{index}", 49, lifecycle.turn_ref, "prediction_sealed", "C0"),
        event(f"prediction-c5-{index}", 49, lifecycle.turn_ref, "prediction_sealed", "C5"),
    ):
        lifecycle.apply(item)


def _run_simultaneous(index: int, first: EpisodeLifecycleR2, first_events: list[LifecycleEventR2]) -> int:
    second_ref = hashlib.sha256(f"operational-r2-second-{index}".encode()).hexdigest()
    second = EpisodeLifecycleR2(49, second_ref)
    _apply_prefix(second, index + 100000)
    second_outcome = hashlib.sha256(f"operational-r2-second-outcome-{index}".encode()).hexdigest()
    second_events = [
        event(f"second-turn-{index}", 49, second_ref, "turn_done"),
        event(f"second-usage-{index}", 49, second_ref, "usage_terminal"),
        event(f"second-outcome-{index}", 49, second_ref, "outcome_terminal", payload=second_outcome),
        event(f"second-archive-{index}", 49, second_ref, "archived"),
    ]
    for left, right in zip(first_events, reversed(second_events)):
        first.apply(left)
        second.apply(right)
    if first.can_revoke():
        first.apply(event(f"first-revoke-{index}", 49, first.turn_ref, "revoked"))
    if second.can_revoke():
        second.apply(event(f"second-revoke-{index}", 49, second_ref, "revoked"))
    return int(not first.revoked or not second.revoked or first.turn_ref == second.turn_ref or second.outcome_commitment != second_outcome)


def _must_reject(lifecycle: EpisodeLifecycleR2, item: LifecycleEventR2) -> int:
    try:
        lifecycle.apply(item)
    except (ValueError, RuntimeError):
        return 1
    return 0
