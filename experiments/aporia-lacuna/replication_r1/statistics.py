from __future__ import annotations

import random
from statistics import NormalDist, mean, variance
from typing import Callable, Sequence

from .contracts import EndpointDecision


def interval(values: Sequence[float], alpha: float) -> tuple[float, float]:
    if len(values) < 2 or not 0 < alpha < 0.5:
        raise ValueError("interval_design_invalid")
    standard_error = (variance(values) / len(values)) ** 0.5
    critical = NormalDist().inv_cdf(1.0 - alpha)
    estimate = mean(values)
    return estimate - critical * standard_error, estimate + critical * standard_error


def superiority(endpoint: str, values: Sequence[float], alpha: float) -> EndpointDecision:
    bounds = interval(values, alpha)
    estimate = mean(values)
    passed = bounds[0] > 0.0
    return EndpointDecision(
        endpoint,
        estimate,
        bounds,
        0.0,
        alpha,
        passed,
        "superiority_established" if passed else "superiority_not_established",
    )


def noninferiority(
    endpoint: str,
    treatment_minus_control: Sequence[float],
    margin: float,
    alpha: float,
) -> EndpointDecision:
    if margin <= 0:
        raise ValueError("noninferiority_margin_invalid")
    bounds = interval(treatment_minus_control, alpha)
    estimate = mean(treatment_minus_control)
    passed = bounds[1] < margin
    return EndpointDecision(
        endpoint,
        estimate,
        bounds,
        margin,
        alpha,
        passed,
        "noninferiority_established" if passed else "noninferiority_not_established",
    )


def equivalence(
    endpoint: str,
    treatment_minus_control: Sequence[float],
    margin: float,
    alpha: float,
) -> EndpointDecision:
    if margin <= 0:
        raise ValueError("equivalence_margin_invalid")
    bounds = interval(treatment_minus_control, alpha)
    estimate = mean(treatment_minus_control)
    passed = bounds[0] > -margin and bounds[1] < margin
    return EndpointDecision(
        endpoint,
        estimate,
        bounds,
        margin,
        alpha,
        passed,
        "equivalence_established" if passed else "equivalence_not_established",
    )


def empirical_power(
    blocks: Sequence[float],
    sample_size: int,
    simulations: int,
    seed: int,
    decision: Callable[[Sequence[float]], bool],
) -> float:
    if len(blocks) < 2 or sample_size < 2 or simulations < 1000:
        raise ValueError("empirical_power_design_invalid")
    generator = random.Random(seed)
    detected = 0
    values = tuple(float(item) for item in blocks)
    for _ in range(simulations):
        draw = tuple(generator.choice(values) for _ in range(sample_size))
        detected += int(decision(draw))
    return detected / simulations


def mutual_information(binary_signal: Sequence[int], binary_outcome: Sequence[int]) -> float:
    if len(binary_signal) != len(binary_outcome) or not binary_signal:
        raise ValueError("mutual_information_input_invalid")
    if any(item not in {0, 1} for item in tuple(binary_signal) + tuple(binary_outcome)):
        raise ValueError("mutual_information_binary_required")
    total = len(binary_signal)
    joint = {(left, right): 0 for left in (0, 1) for right in (0, 1)}
    for left, right in zip(binary_signal, binary_outcome):
        joint[(left, right)] += 1
    left_counts = {value: sum(count for (left, _), count in joint.items() if left == value) for value in (0, 1)}
    right_counts = {value: sum(count for (_, right), count in joint.items() if right == value) for value in (0, 1)}
    result = 0.0
    for (left, right), count in joint.items():
        if count == 0:
            continue
        probability = count / total
        result += probability * __import__("math").log2(
            probability / ((left_counts[left] / total) * (right_counts[right] / total))
        )
    return result
