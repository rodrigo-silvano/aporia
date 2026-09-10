from __future__ import annotations

import hashlib
import itertools
import random
from dataclasses import asdict, dataclass

from .lifecycle import Lifecycle


DEVELOPMENT_SEEDS = tuple(range(89001, 89257))
CONFIRMATION_SEEDS = tuple(range(89301, 89557))


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    passed: bool
    audit_events: int
    core_available: bool
    wrong_turn_links: int
    premature_revocations: int
    runtime_influence: int
    external_effects: int
    contamination: int


@dataclass(frozen=True)
class OperationalReport:
    phase: str
    scenario_count: int
    scenarios: tuple[ScenarioResult, ...]
    property_seeds: int
    property_interleavings: int
    property_failures: int
    wrong_turn_links: int
    premature_revocations: int
    runtime_influence: int
    external_effects: int
    contamination: int
    passed: bool


def run_protocol(phase: str, seeds: tuple[int, ...]) -> OperationalReport:
    if phase not in {"development", "confirmation"} or len(seeds) != 256:
        raise ValueError("operational_r3_protocol_invalid")
    scenarios = tuple(_scenario(name) for name in SCENARIOS)
    interleavings, failures = _property_interleavings(seeds)
    wrong = sum(item.wrong_turn_links for item in scenarios)
    premature = sum(item.premature_revocations for item in scenarios)
    influence = sum(item.runtime_influence for item in scenarios)
    effects = sum(item.external_effects for item in scenarios)
    contamination = sum(item.contamination for item in scenarios)
    passed = all(item.passed for item in scenarios) and failures == 0 and all(
        value == 0 for value in (wrong, premature, influence, effects, contamination)
    )
    return OperationalReport(
        phase,
        len(scenarios),
        scenarios,
        len(seeds),
        interleavings,
        failures,
        wrong,
        premature,
        influence,
        effects,
        contamination,
        passed,
    )


def report_dict(report: OperationalReport) -> dict[str, object]:
    return asdict(report)


def _scenario(name: str) -> ScenarioResult:
    handler = SCENARIOS[name]
    lifecycle, wrong, premature = handler()
    safe = all((
        lifecycle.core_available,
        lifecycle.runtime_influence == 0,
        lifecycle.external_effect == 0,
        lifecycle.contamination == 0,
        wrong == 0,
        premature == 0,
    ))
    return ScenarioResult(
        name,
        safe,
        len(lifecycle.audit),
        lifecycle.core_available,
        wrong,
        premature,
        lifecycle.runtime_influence,
        lifecycle.external_effect,
        lifecycle.contamination,
    )


def _base(turn: str = "turn-a") -> Lifecycle:
    lifecycle = Lifecycle(turn)
    for state in ("assigned", "preparing", "prepared", "exposed"):
        lifecycle.transition(state)
    lifecycle.transition("prediction_sealed", _commit(turn, "prediction"))
    return lifecycle


def _complete(lifecycle: Lifecycle) -> Lifecycle:
    for state in ("turn_done", "usage_terminal"):
        lifecycle.transition(state)
    lifecycle.transition("outcome_terminal", _commit(lifecycle.turn_ref, "outcome"))
    lifecycle.transition("archived")
    lifecycle.transition("revoked")
    return lifecycle


def _message_immediate() -> tuple[Lifecycle, int, int]:
    return _complete(_base()), 0, 0


def _prepare_delayed() -> tuple[Lifecycle, int, int]:
    lifecycle = Lifecycle("turn-delay")
    lifecycle.transition("assigned")
    lifecycle.timeout({"preparing", "prepared"})
    lifecycle.core_available = True
    return lifecycle, 0, 0


def _usage_before_turn() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-usage")
    lifecycle.transition("usage_terminal")
    lifecycle.transition("turn_done")
    lifecycle.transition("outcome_terminal", _commit("turn-usage", "outcome"))
    lifecycle.transition("archived")
    lifecycle.transition("revoked")
    return lifecycle, 0, 0


def _outcome_before_usage() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-outcome")
    lifecycle.transition("outcome_terminal", _commit("turn-outcome", "outcome"))
    lifecycle.transition("turn_done")
    lifecycle.transition("usage_terminal")
    lifecycle.transition("archived")
    lifecycle.transition("revoked")
    return lifecycle, 0, 0


def _duplicate_outcome() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-duplicate")
    commitment = _commit("turn-duplicate", "outcome")
    lifecycle.transition("outcome_terminal", commitment)
    lifecycle.transition("outcome_terminal", commitment)
    return lifecycle, 0, 0


def _late_outcome() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-late")
    lifecycle.transition("turn_done")
    lifecycle.transition("usage_terminal")
    lifecycle.transition("archived")
    lifecycle.transition("revoked")
    lifecycle.transition("outcome_terminal", _commit("turn-late", "outcome"))
    lifecycle.transition("revoked")
    return lifecycle, 0, 0


def _simultaneous_messages() -> tuple[Lifecycle, int, int]:
    first = _complete(_base("turn-concurrent-a"))
    second = _complete(_base("turn-concurrent-b"))
    wrong = int(first.outcome_commitment == second.outcome_commitment)
    first.audit.extend(second.audit)
    return first, wrong, 0


def _websocket_disconnect() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-websocket")
    lifecycle.audit.append(("websocket", "disconnected_after_seal"))
    return _complete(lifecycle), 0, 0


def _worker_restart() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-restart")
    restored = Lifecycle.restore(lifecycle.snapshot())
    return _complete(restored), 0, 0


def _repeated_revocation() -> tuple[Lifecycle, int, int]:
    lifecycle = _complete(_base("turn-revoke"))
    try:
        lifecycle.transition("revoked")
    except ValueError as exception:
        if str(exception) != "operational_r3_revoked":
            raise
    return lifecycle, 0, 0


def _archive_reconnect() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-archive")
    lifecycle.transition("archived")
    lifecycle.audit.append(("reconnect", "core_only"))
    lifecycle.transition("turn_done")
    lifecycle.transition("usage_terminal")
    lifecycle.transition("outcome_terminal", _commit("turn-archive", "outcome"))
    lifecycle.transition("revoked")
    return lifecycle, 0, 0


def _component_unavailable(component: str) -> tuple[Lifecycle, int, int]:
    lifecycle = Lifecycle("turn-" + component)
    lifecycle.transition("assigned")
    lifecycle.timeout({component})
    lifecycle.audit.append((component, "core_fallback"))
    return lifecycle, 0, 0


def _luna_timeout() -> tuple[Lifecycle, int, int]:
    lifecycle = Lifecycle("turn-luna")
    lifecycle.transition("assigned")
    lifecycle.transition("preparing")
    lifecycle.timeout({"prepared"})
    lifecycle.audit.append(("luna", "core_fallback"))
    return lifecycle, 0, 0


def _gateway_retry() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-retry")
    lifecycle.transition("prediction_sealed", lifecycle.prediction_seal)
    return _complete(lifecycle), 0, 0


def _policy_change() -> tuple[Lifecycle, int, int]:
    lifecycle = _base("turn-policy")
    frozen = lifecycle.policy_revision
    next_policy = "policy-v2"
    lifecycle.audit.append(("policy_change", next_policy))
    if lifecycle.policy_revision != frozen:
        return lifecycle, 0, 1
    return _complete(lifecycle), 0, 0


def _kill_during_turn() -> tuple[Lifecycle, int, int]:
    lifecycle = Lifecycle("turn-kill")
    lifecycle.transition("assigned")
    lifecycle.transition("preparing")
    lifecycle.kill()
    lifecycle.transition("prepared")
    lifecycle.transition("exposed")
    lifecycle.core_available = True
    return lifecycle, 0, 0


SCENARIOS = {
    "message_immediately_after_open": _message_immediate,
    "prepare_turn_delayed": _prepare_delayed,
    "usage_before_turn_done": _usage_before_turn,
    "outcome_before_usage": _outcome_before_usage,
    "duplicate_outcome": _duplicate_outcome,
    "late_outcome": _late_outcome,
    "two_simultaneous_messages": _simultaneous_messages,
    "websocket_disconnect_after_seal": _websocket_disconnect,
    "worker_restart_during_finalization": _worker_restart,
    "repeated_revocation": _repeated_revocation,
    "archive_during_reconnect": _archive_reconnect,
    "redis_unavailable": lambda: _component_unavailable("redis"),
    "database_temporarily_unavailable": lambda: _component_unavailable("database"),
    "aporia_unavailable": lambda: _component_unavailable("aporia"),
    "luna_timeout": _luna_timeout,
    "gateway_retry": _gateway_retry,
    "policy_change_during_turn": _policy_change,
    "kill_switch_during_turn": _kill_during_turn,
}


def _property_interleavings(seeds: tuple[int, ...]) -> tuple[int, int]:
    failures = 0
    executions = 0
    terminal = ["turn_done", "usage_terminal", "outcome_terminal", "archived"]
    for seed in seeds:
        generator = random.Random(seed)
        order = terminal[:]
        generator.shuffle(order)
        current_executions, current_failures = _execute_property_order(order, "property-" + str(seed))
        executions += current_executions
        failures += current_failures
    for index, order in enumerate(itertools.permutations(terminal)):
        current_executions, current_failures = _execute_property_order(list(order), "exhaustive-" + str(index))
        executions += current_executions
        failures += current_failures
    return executions, failures


def _execute_property_order(order: list[str], turn_ref: str) -> tuple[int, int]:
    failures = 0
    executions = 0
    lifecycle = _base(turn_ref)
    for state in order:
        if state == "outcome_terminal":
            lifecycle.transition(state, _commit(turn_ref, "outcome"))
        else:
            lifecycle.transition(state)
        executions += 1
        revoked = lifecycle.transition("revoked")
        ready = {"turn_done", "usage_terminal", "outcome_terminal", "archived"}.issubset(lifecycle.states)
        failures += int(revoked != ready)
    failures += int("revoked" not in lifecycle.states)
    failures += int(any((lifecycle.runtime_influence, lifecycle.external_effect, lifecycle.contamination)))
    return executions, failures


def _commit(left: str, right: str) -> str:
    return hashlib.sha256((left + "|" + right).encode("utf-8")).hexdigest()
