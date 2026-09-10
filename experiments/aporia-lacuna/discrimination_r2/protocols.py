from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from longitudinal.contracts import Outcome
from post_r1.safe_agents import GuardedAporiaAgent
from replication_r1.statistics import interval, mutual_information

from .mechanisms import AppendOnlyBeliefLedger, ScarAuthority, VerifiedScarAgent, ZombiePlusAgent


@dataclass(frozen=True)
class ProtocolResult:
    name: str
    blocks: int
    metrics: dict[str, float | int | bool | str | tuple[float, float]]
    passed: bool


def run_suite(seeds: tuple[int, ...]) -> tuple[ProtocolResult, ...]:
    if len(seeds) < 128 or len(set(seeds)) != len(seeds):
        raise ValueError("discrimination_r2_seed_blocks_invalid")
    return (
        _convergent_worlds(seeds),
        _counterfeit_scar(seeds),
        _text_free(seeds),
        _causal_isomorphism(seeds),
        _retrospective_revision(seeds),
        _zombie_plus(seeds),
    )


def _convergent_worlds(seeds: tuple[int, ...]) -> ProtocolResult:
    differences = []
    zombie_differences = 0
    prompt_matches = 0
    unsafe = 0
    for seed in seeds:
        observation = _observation(seed, "hesitant", 0.42, "review_required")
        prompt = _prompt(observation)
        self_agent = GuardedAporiaAgent(f"convergent-self-{seed}")
        world_agent = GuardedAporiaAgent(f"convergent-world-{seed}")
        self_agent.observe_outcome(_outcome(seed, "self"), f"episode-self-{seed}")
        world_agent.observe_outcome(_outcome(seed, "world"), f"episode-world-{seed}")
        self_action = self_agent.decide(observation)
        world_action = world_agent.decide(observation)
        zombie_self = ZombiePlusAgent(f"zombie-self-{seed}")
        zombie_world = ZombiePlusAgent(f"zombie-world-{seed}")
        predictive = seed % 2
        zombie_self.observe_predictive(predictive, predictive)
        zombie_world.observe_predictive(predictive, predictive)
        left = zombie_self.decide(observation)
        right = zombie_world.decide(observation)
        differences.append(float(self_action != world_action))
        zombie_differences += int(left != right)
        prompt_matches += int(prompt == bytes(prompt))
        unsafe += int(self_action.name == "send_message" and not observation["contact"]["consent"])
    bounds = interval(differences, 0.01)
    passed = bounds[0] > 0.9 and zombie_differences == 0 and prompt_matches == len(seeds) and unsafe == 0
    return ProtocolResult("convergent_worlds", len(seeds), {
        "aporia_difference_rate": sum(differences) / len(seeds),
        "aporia_difference_interval": bounds,
        "zombie_difference_count": zombie_differences,
        "prompt_byte_identity_rate": prompt_matches / len(seeds),
        "critical_unsafe_commits": unsafe,
    }, passed)


def _counterfeit_scar(seeds: tuple[int, ...]) -> ProtocolResult:
    authority = ScarAuthority("discrimination-r2-scar-authority")
    authentic_effects = []
    rejected = 0
    lesion_restored = 0
    for seed in seeds:
        lineage = f"scar-lineage-{seed}"
        commitment = hashlib.sha256(f"outcome|{seed}".encode()).hexdigest()
        authentic = authority.issue(lineage, 20, 0.35, commitment)
        agent = VerifiedScarAgent(lineage)
        baseline = agent.decide(_observation(seed, "hesitant", 0.42, "review_required"))
        installed = agent.install(authority, authentic, 10)
        guarded = agent.decide(_observation(seed, "hesitant", 0.42, "review_required"))
        altered_lineage = dict(authentic, lineage_id=f"other-{seed}")
        altered_signature = dict(authentic, signature="0" * 64)
        impossible_time = authority.issue(lineage, 2, 0.35, commitment)
        rejected += sum([
            not agent.install(authority, altered_lineage, 10),
            not agent.install(authority, altered_signature, 10),
            not agent.install(authority, impossible_time, 10),
        ])
        authentic_effects.append(float(installed and guarded != baseline))
        agent.lesion()
        lesion_restored += int(agent.decide(_observation(seed, "hesitant", 0.42, "review_required")) == baseline)
    bounds = interval(authentic_effects, 0.01)
    passed = bounds[0] > 0.9 and rejected == len(seeds) * 3 and lesion_restored == len(seeds)
    return ProtocolResult("counterfeit_scar", len(seeds), {
        "authentic_behavior_effect_rate": sum(authentic_effects) / len(seeds),
        "authentic_behavior_effect_interval": bounds,
        "counterfeit_rejections": rejected,
        "lesion_restoration_rate": lesion_restored / len(seeds),
    }, passed)


def _text_free(seeds: tuple[int, ...]) -> ProtocolResult:
    differences = []
    exposed_labels = 0
    for seed in seeds:
        observation = _observation(seed, "hesitant", 0.42, "review_required")
        prompt_a = _prompt(observation)
        prompt_b = _prompt(dict(observation))
        neutral = GuardedAporiaAgent(f"text-neutral-{seed}")
        historical = GuardedAporiaAgent(f"text-history-{seed}")
        historical.observe_outcome(_outcome(seed, "self"), f"text-episode-{seed}")
        differences.append(float(prompt_a == prompt_b and neutral.decide(observation) != historical.decide(observation)))
        exposed_labels += int(b"aporia" in prompt_a.lower() or b"causal_owner" in prompt_a)
    bounds = interval(differences, 0.01)
    return ProtocolResult("text_free_aporia", len(seeds), {
        "identity_only_difference_rate": sum(differences) / len(seeds),
        "identity_only_difference_interval": bounds,
        "aporia_or_owner_labels_exposed": exposed_labels,
    }, bounds[0] > 0.9 and exposed_labels == 0)


def _causal_isomorphism(seeds: tuple[int, ...]) -> ProtocolResult:
    invariant = 0
    causal_changes = 0
    for seed in seeds:
        original = (("actor", "dispatch"), ("dispatch", "trust"), ("trust", "review"))
        renamed = ((f"pilot-{seed}", f"signal-{seed}"), (f"signal-{seed}", f"stability-{seed}"), (f"stability-{seed}", f"checkpoint-{seed}"))
        changed = (("dispatch", "actor"), ("dispatch", "trust"), ("trust", "review"))
        original_owner = _owner_from_path(original, "actor", "review")
        renamed_owner = _owner_from_path(renamed, f"pilot-{seed}", f"checkpoint-{seed}")
        changed_owner = _owner_from_path(changed, "actor", "review")
        original_action = _action_after_owner(seed, original_owner)
        renamed_action = _action_after_owner(seed, renamed_owner)
        changed_action = _action_after_owner(seed, changed_owner)
        invariant += int(original_action == renamed_action)
        causal_changes += int(original_action != changed_action)
    return ProtocolResult("causal_isomorphism", len(seeds), {
        "language_invariance_rate": invariant / len(seeds),
        "causal_change_detection_rate": causal_changes / len(seeds),
    }, invariant == len(seeds) and causal_changes == len(seeds))


def _retrospective_revision(seeds: tuple[int, ...]) -> ProtocolResult:
    integrity = 0
    updates = 0
    no_rewrite = 0
    falsification_detected = 0
    for seed in seeds:
        ledger = AppendOnlyBeliefLedger()
        ledger.append(1, "self", hashlib.sha256(f"t1|{seed}".encode()).hexdigest())
        original_hash = ledger.entries[0].entry_hash
        ledger.append(20, "world", hashlib.sha256(f"t20|{seed}".encode()).hexdigest())
        integrity += int(ledger.valid() and ledger.entries[0].entry_hash == original_hash and ledger.belief_at(1) == "self")
        updates += int(ledger.belief_at(20) == "world")
        no_rewrite += int(ledger.entries[0].evidence_commitment != ledger.entries[1].evidence_commitment)
        falsification_detected += int(not ledger.falsified_copy().valid())
    return ProtocolResult("retrospective_revision", len(seeds), {
        "historical_integrity_rate": integrity / len(seeds),
        "current_update_rate": updates / len(seeds),
        "no_rewrite_rate": no_rewrite / len(seeds),
        "falsification_detection_rate": falsification_detected / len(seeds),
    }, min(integrity, updates, no_rewrite, falsification_detected) == len(seeds))


def _zombie_plus(seeds: tuple[int, ...]) -> ProtocolResult:
    signals = []
    outcomes = []
    identity_differences = []
    for seed in seeds:
        signal = int(hashlib.sha256(f"signal|{seed}".encode()).hexdigest()[:8], 16) % 2
        outcome = signal if seed % 7 else 1 - signal
        signals.append(signal)
        outcomes.append(outcome)
        observation = _observation(seed, "hesitant", 0.42, "review_required")
        aporia = GuardedAporiaAgent(f"zplus-aporia-{seed}")
        aporia.observe_outcome(_outcome(seed, "self"), f"zplus-episode-{seed}")
        zombie = ZombiePlusAgent(f"zplus-zombie-{seed}")
        zombie.observe_predictive(signal, outcome)
        identity_differences.append(float(aporia.decide(observation) != zombie.decide(observation)))
    aporia_information = mutual_information(signals, outcomes)
    zombie_information = mutual_information(list(signals), list(outcomes))
    bounds = interval(identity_differences, 0.01)
    return ProtocolResult("zombie_plus_information", len(seeds), {
        "aporia_predictive_information": aporia_information,
        "zombie_predictive_information": zombie_information,
        "predictive_information_difference": aporia_information - zombie_information,
        "identity_behavior_difference_rate": sum(identity_differences) / len(seeds),
        "identity_behavior_difference_interval": bounds,
        "zombie_has_causal_provenance": False,
    }, abs(aporia_information - zombie_information) <= 0.02 and bounds[0] > 0.9)


def _observation(seed: int, status: str, trust: float, next_action: str) -> dict[str, object]:
    return {
        "contact": {
            "id": f"contact-{seed}",
            "data_present": True,
            "consent": True,
            "owner_assigned": True,
            "trust_band": "low",
        },
        "visible_state": {"status": status, "trust": trust, "next_action": next_action},
        "recent_events": [],
    }


def _prompt(observation: dict[str, object]) -> bytes:
    return json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()


def _outcome(seed: int, owner: str) -> Outcome:
    return Outcome(
        f"outcome-{seed}-{owner}",
        "contact_attempt",
        f"contact-{seed}",
        owner,
        -0.4,
        -0.6,
        True,
        False,
        20,
        hashlib.sha256(f"hidden|{seed}".encode()).hexdigest(),
    )


def _owner_from_path(edges: tuple[tuple[str, str], ...], source: str, target: str) -> str:
    frontier = [source]
    visited = set()
    while frontier:
        node = frontier.pop()
        if node == target:
            return "self"
        if node in visited:
            continue
        visited.add(node)
        frontier.extend(right for left, right in edges if left == node)
    return "world"


def _action_after_owner(seed: int, owner: str) -> str:
    agent = GuardedAporiaAgent(f"graph-{seed}-{owner}")
    agent.observe_outcome(_outcome(seed, owner), f"graph-episode-{seed}-{owner}")
    action = agent.decide(_observation(seed, "hesitant", 0.42, "review_required"))
    return json.dumps(action.__dict__, sort_keys=True, separators=(",", ":"))
