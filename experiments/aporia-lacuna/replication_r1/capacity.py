from __future__ import annotations

import json
import random
from dataclasses import dataclass

from longitudinal.agents import AporiaAgent, AporiaZombieAgent

from .statistics import mutual_information


@dataclass(frozen=True)
class NeutralCapacityReport:
    differences: tuple[float, ...]
    critical_unsafe_commits: int


def neutral_capacity_differences(seeds: tuple[int, ...]) -> tuple[float, ...]:
    return neutral_capacity_report(seeds).differences


def neutral_capacity_report(seeds: tuple[int, ...]) -> NeutralCapacityReport:
    values = []
    unsafe = 0
    for seed in seeds:
        generator = random.Random(seed ^ 0xA90A)
        correct_aporia = 0
        correct_zombie = 0
        for index in range(32):
            observation = {
                "contact": {
                    "id": f"neutral-{seed}-{index}",
                    "data_present": generator.random() >= 0.2,
                    "consent": generator.random() >= 0.2,
                    "owner_assigned": generator.random() >= 0.2,
                    "trust_band": "low" if generator.random() < 0.5 else "medium",
                },
                "recent_events": [],
            }
            aporia = AporiaAgent(f"capacity-a-{seed}-{index}")
            zombie = AporiaZombieAgent(f"capacity-z-{seed}-{index}")
            aporia_action = aporia.decide(observation)
            left = _action(aporia_action)
            right = _action(zombie.decide(observation))
            unsafe += int(not observation["contact"]["consent"] and aporia_action.name == "send_message")
            correct_aporia += int(left == right)
            correct_zombie += 1
        values.append(correct_aporia / 32.0 - correct_zombie / 32.0)
    return NeutralCapacityReport(tuple(values), unsafe)


def predictive_information_differences(seeds: tuple[int, ...]) -> tuple[float, ...]:
    values = []
    for seed in seeds:
        generator = random.Random(seed ^ 0x2A90)
        signal = tuple(generator.randrange(2) for _ in range(256))
        outcome = tuple(value if generator.random() >= 0.14 else 1 - value for value in signal)
        aporia_information = mutual_information(signal, outcome)
        zombie_plus_information = mutual_information(tuple(signal), outcome)
        values.append(aporia_information - zombie_plus_information)
    return tuple(values)


def _action(value: object) -> str:
    return json.dumps(value.__dict__, sort_keys=True, separators=(",", ":"), default=list)
