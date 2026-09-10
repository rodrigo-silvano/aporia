from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DecoderRow:
    features: tuple[float, ...]
    answer: int
    arm: int
    causal_class: int
    criticality: int
    template_family: int
    group: int
    template: int


@dataclass(frozen=True)
class DecoderMetric:
    name: str
    aporia_auc: float
    zombie_plus_auc: float
    delta_auc: float
    aporia_accuracy: float
    zombie_plus_accuracy: float
    delta_accuracy: float


@dataclass(frozen=True)
class DecoderReport:
    models: tuple[DecoderMetric, ...]
    answer_delta_auc_interval: tuple[float, float]
    answer_permutation_p: float
    answer_equivalence_margin: float
    answer_equivalent: bool
    arm_auc: float
    arm_accuracy: float
    target_probe_accuracy: dict[str, float]
    grouped_cross_fitting: bool
    classification: str


def run_decoders(aporia: tuple[DecoderRow, ...], zombie: tuple[DecoderRow, ...]) -> DecoderReport:
    if len(aporia) != len(zombie) or len(aporia) < 64:
        raise ValueError("mechanistic_r4_decoder_rows_invalid")
    factories: tuple[tuple[str, Callable[[], object]], ...] = (
        ("frequency", FrequencyDecoder),
        ("template", TemplateDecoder),
        ("logistic", LogisticDecoder),
        ("decision_tree", DecisionTreeDecoder),
        ("gradient_boosting", GradientBoostingDecoder),
        ("neural_network", NeuralDecoder),
    )
    metrics = []
    logistic_aporia_scores: list[float] | None = None
    logistic_zombie_scores: list[float] | None = None
    for name, factory in factories:
        aporia_scores = _cross_fit(aporia, factory, "answer")
        zombie_scores = _cross_fit(zombie, factory, "answer")
        if name == "logistic":
            logistic_aporia_scores = aporia_scores
            logistic_zombie_scores = zombie_scores
        aporia_labels = [item.answer for item in aporia]
        zombie_labels = [item.answer for item in zombie]
        aporia_auc = auc(aporia_labels, aporia_scores)
        zombie_auc = auc(zombie_labels, zombie_scores)
        aporia_accuracy = accuracy(aporia_labels, aporia_scores)
        zombie_accuracy = accuracy(zombie_labels, zombie_scores)
        metrics.append(DecoderMetric(
            name,
            aporia_auc,
            zombie_auc,
            aporia_auc - zombie_auc,
            aporia_accuracy,
            zombie_accuracy,
            aporia_accuracy - zombie_accuracy,
        ))
    strongest = max(metrics, key=lambda item: item.delta_auc)
    if logistic_aporia_scores is None or logistic_zombie_scores is None:
        raise RuntimeError("mechanistic_r4_logistic_scores_missing")
    logistic_metric = next(item for item in metrics if item.name == "logistic")
    interval = _group_bootstrap_delta(
        aporia,
        zombie,
        logistic_aporia_scores,
        logistic_zombie_scores,
        400,
        84001,
    )
    permutation_p = _permutation_delta(
        aporia,
        zombie,
        logistic_aporia_scores,
        logistic_zombie_scores,
        logistic_metric.delta_auc,
        199,
        84002,
    )
    combined = tuple(
        DecoderRow(item.features, item.answer, 1, item.causal_class, item.criticality, item.template_family, item.group, item.template)
        for item in aporia
    ) + tuple(
        DecoderRow(item.features, item.answer, 0, item.causal_class, item.criticality, item.template_family, item.group, item.template)
        for item in zombie
    )
    arm_scores = _cross_fit(combined, LogisticDecoder, "arm")
    arm_labels = [item.arm for item in combined]
    probes = {}
    for target in ("answer", "causal_class", "criticality", "template_family"):
        scores = _cross_fit(combined, LogisticDecoder, target)
        probes[target] = accuracy([int(getattr(item, target)) for item in combined], scores)
    margin = 0.05
    equivalent = interval[0] >= -margin and interval[1] <= margin
    if strongest.delta_auc > margin and permutation_p <= 0.01:
        classification = "informational_or_structural_advantage"
    elif equivalent:
        classification = "no_detectable_answer_leakage_within_margin"
    else:
        classification = "inconclusive"
    return DecoderReport(
        tuple(metrics),
        interval,
        permutation_p,
        margin,
        equivalent,
        auc(arm_labels, arm_scores),
        accuracy(arm_labels, arm_scores),
        probes,
        True,
        classification,
    )


class FrequencyDecoder:
    def fit(self, rows: tuple[DecoderRow, ...], target: str) -> None:
        self.value = sum(int(getattr(item, target)) for item in rows) / max(1, len(rows))

    def predict(self, row: DecoderRow) -> float:
        return self.value


class TemplateDecoder:
    def fit(self, rows: tuple[DecoderRow, ...], target: str) -> None:
        self.default = sum(int(getattr(item, target)) for item in rows) / max(1, len(rows))
        grouped: dict[int, list[int]] = {}
        for row in rows:
            grouped.setdefault(row.template, []).append(int(getattr(row, target)))
        self.values = {key: sum(values) / len(values) for key, values in grouped.items()}

    def predict(self, row: DecoderRow) -> float:
        return self.values.get(row.template, self.default)


class LogisticDecoder:
    def fit(self, rows: tuple[DecoderRow, ...], target: str) -> None:
        width = len(rows[0].features)
        self.weights = [0.0] * (width + 1)
        for epoch in range(260):
            rate = 0.35 / (1.0 + epoch / 80.0)
            gradient = [0.0] * len(self.weights)
            for row in rows:
                values = (1.0, *row.features)
                prediction = _sigmoid(sum(weight * value for weight, value in zip(self.weights, values)))
                error = prediction - int(getattr(row, target))
                for index, value in enumerate(values):
                    gradient[index] += error * value
            for index in range(len(self.weights)):
                penalty = 0.001 * self.weights[index] if index else 0.0
                self.weights[index] -= rate * (gradient[index] / len(rows) + penalty)

    def predict(self, row: DecoderRow) -> float:
        return _sigmoid(sum(weight * value for weight, value in zip(self.weights, (1.0, *row.features))))


class DecisionTreeDecoder:
    def fit(self, rows: tuple[DecoderRow, ...], target: str) -> None:
        self.target = target
        self.tree = self._node(rows, 0)

    def predict(self, row: DecoderRow) -> float:
        node = self.tree
        while "feature" in node:
            node = node["left"] if row.features[node["feature"]] <= node["threshold"] else node["right"]
        return node["value"]

    def _node(self, rows: tuple[DecoderRow, ...], depth: int) -> dict[str, object]:
        value = sum(int(getattr(item, self.target)) for item in rows) / len(rows)
        if depth >= 3 or value in {0.0, 1.0} or len(rows) < 8:
            return {"value": value}
        split = _best_split(rows, self.target)
        if split is None:
            return {"value": value}
        feature, threshold = split
        left = tuple(item for item in rows if item.features[feature] <= threshold)
        right = tuple(item for item in rows if item.features[feature] > threshold)
        if not left or not right:
            return {"value": value}
        return {
            "feature": feature,
            "threshold": threshold,
            "left": self._node(left, depth + 1),
            "right": self._node(right, depth + 1),
        }


class GradientBoostingDecoder:
    def fit(self, rows: tuple[DecoderRow, ...], target: str) -> None:
        mean = min(0.999, max(0.001, sum(int(getattr(item, target)) for item in rows) / len(rows)))
        self.base = math.log(mean / (1.0 - mean))
        scores = [self.base] * len(rows)
        self.stumps: list[tuple[int, float, float, float]] = []
        for _ in range(24):
            residuals = [int(getattr(row, target)) - _sigmoid(score) for row, score in zip(rows, scores)]
            stump = _best_residual_stump(rows, residuals)
            self.stumps.append(stump)
            feature, threshold, left, right = stump
            for index, row in enumerate(rows):
                scores[index] += 0.25 * (left if row.features[feature] <= threshold else right)

    def predict(self, row: DecoderRow) -> float:
        score = self.base
        for feature, threshold, left, right in self.stumps:
            score += 0.25 * (left if row.features[feature] <= threshold else right)
        return _sigmoid(score)


class NeuralDecoder:
    def fit(self, rows: tuple[DecoderRow, ...], target: str) -> None:
        width = len(rows[0].features)
        hidden = 6
        generator = random.Random(84003)
        self.first = [[generator.uniform(-0.2, 0.2) for _ in range(width + 1)] for _ in range(hidden)]
        self.second = [generator.uniform(-0.2, 0.2) for _ in range(hidden + 1)]
        for epoch in range(220):
            rate = 0.18 / (1.0 + epoch / 90.0)
            for row in rows:
                values = (1.0, *row.features)
                hidden_values = [_sigmoid(sum(weight * value for weight, value in zip(weights, values))) for weights in self.first]
                output = _sigmoid(sum(weight * value for weight, value in zip(self.second, (1.0, *hidden_values))))
                output_error = output - int(getattr(row, target))
                previous_second = list(self.second)
                for index, value in enumerate((1.0, *hidden_values)):
                    self.second[index] -= rate * output_error * value
                for hidden_index, hidden_value in enumerate(hidden_values):
                    hidden_error = output_error * previous_second[hidden_index + 1] * hidden_value * (1.0 - hidden_value)
                    for feature_index, value in enumerate(values):
                        self.first[hidden_index][feature_index] -= rate * hidden_error * value

    def predict(self, row: DecoderRow) -> float:
        values = (1.0, *row.features)
        hidden_values = [_sigmoid(sum(weight * value for weight, value in zip(weights, values))) for weights in self.first]
        return _sigmoid(sum(weight * value for weight, value in zip(self.second, (1.0, *hidden_values))))


def _cross_fit(rows: tuple[DecoderRow, ...], factory: Callable[[], object], target: str) -> list[float]:
    groups = sorted({item.group for item in rows})
    scores = [0.0] * len(rows)
    for group in groups:
        training = tuple(item for item in rows if item.group != group)
        testing = [(index, item) for index, item in enumerate(rows) if item.group == group]
        model = factory()
        model.fit(training, target)
        for index, item in testing:
            scores[index] = float(model.predict(item))
    return scores


def auc(labels: list[int], scores: list[float]) -> float:
    positive = [score for label, score in zip(labels, scores) if label == 1]
    negative = [score for label, score in zip(labels, scores) if label == 0]
    if not positive or not negative:
        return 0.5
    wins = 0.0
    for left in positive:
        for right in negative:
            wins += 1.0 if left > right else 0.5 if left == right else 0.0
    return wins / (len(positive) * len(negative))


def accuracy(labels: list[int], scores: list[float]) -> float:
    return sum(int((score >= 0.5) == bool(label)) for label, score in zip(labels, scores)) / len(labels)


def _best_split(rows: tuple[DecoderRow, ...], target: str) -> tuple[int, float] | None:
    baseline = _gini([int(getattr(item, target)) for item in rows])
    best: tuple[float, int, float] | None = None
    for feature in range(len(rows[0].features)):
        values = sorted({item.features[feature] for item in rows})
        for left_value, right_value in zip(values, values[1:]):
            threshold = (left_value + right_value) / 2.0
            left = [int(getattr(item, target)) for item in rows if item.features[feature] <= threshold]
            right = [int(getattr(item, target)) for item in rows if item.features[feature] > threshold]
            if not left or not right:
                continue
            impurity = (len(left) * _gini(left) + len(right) * _gini(right)) / len(rows)
            gain = baseline - impurity
            if best is None or gain > best[0]:
                best = (gain, feature, threshold)
    return None if best is None or best[0] <= 0 else (best[1], best[2])


def _best_residual_stump(rows: tuple[DecoderRow, ...], residuals: list[float]) -> tuple[int, float, float, float]:
    best: tuple[float, int, float, float, float] | None = None
    for feature in range(len(rows[0].features)):
        values = sorted({item.features[feature] for item in rows})
        thresholds = [(left + right) / 2.0 for left, right in zip(values, values[1:])] or values
        for threshold in thresholds:
            left_indexes = [index for index, item in enumerate(rows) if item.features[feature] <= threshold]
            right_indexes = [index for index, item in enumerate(rows) if item.features[feature] > threshold]
            if not left_indexes or not right_indexes:
                continue
            left_value = sum(residuals[index] for index in left_indexes) / len(left_indexes)
            right_value = sum(residuals[index] for index in right_indexes) / len(right_indexes)
            error = sum(
                (residuals[index] - (left_value if index in left_indexes else right_value)) ** 2
                for index in range(len(rows))
            )
            if best is None or error < best[0]:
                best = (error, feature, threshold, left_value, right_value)
    if best is None:
        return 0, 0.0, 0.0, 0.0
    return best[1], best[2], best[3], best[4]


def _gini(values: list[int]) -> float:
    if not values:
        return 0.0
    rate = sum(values) / len(values)
    return 2.0 * rate * (1.0 - rate)


def _sigmoid(value: float) -> float:
    bounded = min(35.0, max(-35.0, value))
    return 1.0 / (1.0 + math.exp(-bounded))


def _group_bootstrap_delta(
    aporia: tuple[DecoderRow, ...],
    zombie: tuple[DecoderRow, ...],
    aporia_scores: list[float],
    zombie_scores: list[float],
    samples: int,
    seed: int,
) -> tuple[float, float]:
    generator = random.Random(seed)
    groups = sorted({item.group for item in aporia})
    values = []
    for _ in range(samples):
        selected = [generator.choice(groups) for _ in groups]
        indexes = [index for group in selected for index, item in enumerate(aporia) if item.group == group]
        left_labels = [aporia[index].answer for index in indexes]
        right_labels = [zombie[index].answer for index in indexes]
        left_scores = [aporia_scores[index] for index in indexes]
        right_scores = [zombie_scores[index] for index in indexes]
        values.append(auc(left_labels, left_scores) - auc(right_labels, right_scores))
    values.sort()
    return values[int(samples * 0.025)], values[min(samples - 1, int(samples * 0.975))]


def _permutation_delta(
    aporia: tuple[DecoderRow, ...],
    zombie: tuple[DecoderRow, ...],
    aporia_scores: list[float],
    zombie_scores: list[float],
    observed: float,
    permutations: int,
    seed: int,
) -> float:
    generator = random.Random(seed)
    exceed = 0
    for _ in range(permutations):
        left_labels = []
        right_labels = []
        left_scores = []
        right_scores = []
        for index, (left, right) in enumerate(zip(aporia, zombie)):
            if generator.random() < 0.5:
                left_labels.append(left.answer)
                right_labels.append(right.answer)
                left_scores.append(aporia_scores[index])
                right_scores.append(zombie_scores[index])
            else:
                left_labels.append(right.answer)
                right_labels.append(left.answer)
                left_scores.append(zombie_scores[index])
                right_scores.append(aporia_scores[index])
        delta = auc(left_labels, left_scores) - auc(right_labels, right_scores)
        exceed += int(abs(delta) >= abs(observed))
    return (exceed + 1) / (permutations + 1)
