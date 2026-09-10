from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("empty_values")
    return sum(values) / len(values)


def variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    center = mean(values)
    return sum((value - center) ** 2 for value in values) / (len(values) - 1)


def covariance(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Inputs must have the same length")
    if len(left) < 2:
        return 0.0
    left_center = mean(left)
    right_center = mean(right)
    return sum((a - left_center) * (b - right_center) for a, b in zip(left, right)) / (len(left) - 1)


def correlation(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = math.sqrt(variance(left) * variance(right))
    return 0.0 if denominator == 0.0 else covariance(left, right) / denominator


def slope(predictor: Sequence[float], outcome: Sequence[float]) -> float:
    predictor_variance = variance(predictor)
    return 0.0 if predictor_variance == 0.0 else covariance(predictor, outcome) / predictor_variance


def clustered_bootstrap(
    lineages: Sequence[Sequence[float]],
    statistic: Callable[[Sequence[Sequence[float]]], float],
    samples: int,
    seed: int,
) -> tuple[float, float]:
    if not lineages or samples < 100:
        raise ValueError("bootstrap_configuration_invalid")
    generator = random.Random(seed)
    values = []
    for _ in range(samples):
        draw = [lineages[generator.randrange(len(lineages))] for _ in lineages]
        values.append(statistic(draw))
    values.sort()
    return values[int(samples * 0.025)], values[min(samples - 1, int(samples * 0.975))]


def effective_rank(matrix: Sequence[Sequence[float]]) -> float:
    if not matrix or any(len(row) != len(matrix) for row in matrix):
        raise ValueError("square_matrix_required")
    gram = [
        [sum(matrix[k][i] * matrix[k][j] for k in range(len(matrix))) for j in range(len(matrix))]
        for i in range(len(matrix))
    ]
    eigenvalues = _jacobi_eigenvalues(gram)
    singular = [math.sqrt(max(0.0, value)) for value in eigenvalues if value > 1e-12]
    total = sum(singular)
    if total == 0.0:
        return 0.0
    probabilities = [value / total for value in singular]
    return math.exp(-sum(value * math.log(value) for value in probabilities))


def _jacobi_eigenvalues(matrix: Sequence[Sequence[float]]) -> list[float]:
    values = [list(row) for row in matrix]
    size = len(values)
    for _ in range(size * size * 20):
        p, q = max(
            ((i, j) for i in range(size) for j in range(i + 1, size)),
            key=lambda pair: abs(values[pair[0]][pair[1]]),
        )
        if abs(values[p][q]) < 1e-12:
            break
        angle = 0.5 * math.atan2(2.0 * values[p][q], values[q][q] - values[p][p])
        cosine = math.cos(angle)
        sine = math.sin(angle)
        for index in range(size):
            if index in {p, q}:
                continue
            left = values[index][p]
            right = values[index][q]
            values[index][p] = values[p][index] = cosine * left - sine * right
            values[index][q] = values[q][index] = sine * left + cosine * right
        pp = values[p][p]
        qq = values[q][q]
        pq = values[p][q]
        values[p][p] = cosine * cosine * pp - 2.0 * sine * cosine * pq + sine * sine * qq
        values[q][q] = sine * sine * pp + 2.0 * sine * cosine * pq + cosine * cosine * qq
        values[p][q] = values[q][p] = 0.0
    return [values[index][index] for index in range(size)]
