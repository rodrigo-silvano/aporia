"""
APORIA Logical Lacuna Experiment V2 Support.
Replicates AporiaLogicalLacunaExperimentV2.
"""

from __future__ import annotations
import math
import json
import hashlib
from typing import Any, Callable, Sequence

from aporia.infrastructure.lacuna_kernel import AporiaLacunaKernelV2
from tests.Support.splitmix64 import SplitMix64V1


class AporiaLogicalLacunaExperimentV2:
    CORPUS_SEED = "4C4143554E415632"
    LABEL_SEED = "4C4143554E414C32"
    BOOTSTRAP_SEED = "4C4143554E414232"
    FIXTURE_COUNT = 100
    DIMENSIONS = 32
    CANDIDATE_DIMENSIONS = 12
    BOOTSTRAP_REPETITIONS = 10000
    EFFECT_THRESHOLD = 0.125

    def fixtures(self) -> list[dict[str, Any]]:
        generator = SplitMix64V1(self.CORPUS_SEED)
        families = ["P0"] * 25 + ["P1"] * 25 + ["N0"] * 25 + ["N1"] * 25
        label_generator = SplitMix64V1(self.LABEL_SEED)
        for index in range(len(families) - 1, 0, -1):
            swap = label_generator.nextBelow(index + 1)
            families[index], families[swap] = families[swap], families[index]

        family_offsets = {"P0": 0, "P1": 0, "N0": 0, "N1": 0}
        fixtures = []
        for index in range(self.FIXTURE_COUNT):
            state = []
            for _ in range(self.DIMENSIONS):
                state.append(generator.nextUnit())
            family = families[index]
            offset = family_offsets[family]
            family_offsets[family] += 1
            positive = family in ["P0", "P1"]
            unknown = family in ["P1", "N1"]
            produces_mask = 1 << (offset % 4)
            requires_mask = (
                produces_mask | (1 << ((offset + 1) % 4))
                if positive
                else (1 << ((offset + 1) % 4)) | (1 << ((offset + 2) % 4))
            )
            state[0] = 1.0 / 6.0 if unknown else 0.0
            state[1] = float(produces_mask)
            state[2] = float(requires_mask)
            state[3] = 1.0
            state[4] = ((offset % 10) + 1) / 10.0
            state[5] = ((offset % 9) + 1) / 10.0
            state[6] = (((offset * 3) % 9) + 1) / 10.0
            for dim in range(7, 12):
                state[dim] = 0.0

            fixtures.append({
                "id": f"lacuna-v2-{index + 1:03d}",
                "family": family,
                "positive": positive,
                "state": state,
            })
        return fixtures

    def run(self, candidate: Callable[[list[float], list[str]], list[float]]) -> dict[str, Any]:
        return self._execute(candidate, False)

    def run_reference(self) -> dict[str, Any]:
        return self._execute(lambda state, order: state, True)

    def runReference(self) -> dict[str, Any]:
        return self.run_reference()

    @staticmethod
    def current_candidate(state: list[float], order: list[str]) -> list[float]:
        return AporiaLacunaKernelV2.sequence(state, order)

    currentCandidate = current_candidate

    @staticmethod
    def commutative_candidate(state: list[float], order: list[str]) -> list[float]:
        return state

    commutativeCandidate = commutative_candidate

    @staticmethod
    def unknown_candidate(state: list[float], order: list[str]) -> list[float]:
        st = list(state)
        for instrument in order:
            if instrument == AporiaLacunaKernelV2.REQUIREMENT_FOCUS:
                st[7] = 1.0 if st[0] > 0.0 else 0.0
                continue
            if st[7] > 0.0:
                st[8] -= 1.0
                st[9] -= 1.0
                continue
            st[10] -= 1.0
            st[11] -= 1.0
        return st

    unknownCandidate = unknown_candidate

    @staticmethod
    def missing_mask_candidate(state: list[float], order: list[str]) -> list[float]:
        st = list(state)
        st[1] = float(int(st[1]) & 14)
        return AporiaLacunaKernelV2.sequence(st, order)

    missingMaskCandidate = missing_mask_candidate

    def _execute(self, candidate: Callable[..., list[float]], reference: bool) -> dict[str, Any]:
        scores = []
        for fixture in self.fixtures():
            scores.append(self._score(fixture, candidate, reference))

        positives = [s for s in scores if s["positive"]]
        negatives = [s for s in scores if not s["positive"]]
        n_positive = sum(s["marked"] for s in positives)
        n_negative = sum(s["marked"] for s in negatives)
        delta = (n_positive / 50.0) - (n_negative / 50.0)
        lower, upper = self._bootstrap(positives, negatives)
        valid = (
            len(scores) == self.FIXTURE_COUNT
            and all(s["valid"] for s in scores)
            and len({s["fixture_id"] for s in scores}) == self.FIXTURE_COUNT
        )
        return {
            "valid": valid,
            "passed": valid and n_positive >= 45 and n_negative <= 5 and lower > 0.60,
            "fixture_count": len(scores),
            "n_lacuna": n_positive + n_negative,
            "n_positive": n_positive,
            "n_negative": n_negative,
            "delta": delta,
            "bootstrap_lower_95": lower,
            "bootstrap_upper_95": upper,
            "scores": scores,
        }

    def _score(self, fixture: dict[str, Any], candidate: Callable[..., list[float]], reference: bool) -> dict[str, Any]:
        inp = list(fixture["state"])
        qr_order = [AporiaLacunaKernelV2.REQUIREMENT_FOCUS, AporiaLacunaKernelV2.DEPENDENCY_PROJECTION]
        rq_order = list(reversed(qr_order))

        if reference:
            execute = lambda s, o: self._reference(s, o, fixture["positive"])
        else:
            execute = lambda s, o: self._merge_candidate_output(s, candidate(s[:self.CANDIDATE_DIMENSIONS], o))

        lacuna_qr = execute(inp, qr_order)
        lacuna_rq = execute(inp, rq_order)
        fork_qr_a = execute(inp, qr_order)
        fork_qr_b = execute(inp, qr_order)
        fork_rq_a = execute(inp, rq_order)
        fork_rq_b = execute(inp, rq_order)

        self._assert_state(lacuna_qr)
        self._assert_state(lacuna_rq)

        target_dimensions = sorted(list(set(self._changed_indexes(inp, lacuna_qr) + self._changed_indexes(inp, lacuna_rq))))
        random_qr = self._random_matched(inp, lacuna_qr, target_dimensions)
        random_rq = self._random_matched(inp, lacuna_rq, target_dimensions)
        order_only_qr = self._order_only(inp, qr_order)
        order_only_rq = self._order_only(inp, rq_order)

        d_lacuna = self._distance(inp, lacuna_qr, lacuna_rq)
        d_passive = self._distance(inp, inp, inp)
        d_order_only = self._distance(inp, order_only_qr, order_only_rq)
        d_random = self._distance(inp, random_qr, random_rq)
        d_forked = max(
            self._distance(inp, fork_qr_a, fork_qr_b),
            self._distance(inp, fork_rq_a, fork_rq_b),
        )
        effect = d_lacuna - max(d_passive, d_order_only, d_random, d_forked)
        valid = (
            self._matching_valid(inp, lacuna_qr, random_qr, target_dimensions)
            and self._matching_valid(inp, lacuna_rq, random_rq, target_dimensions)
            and d_forked == 0.0
            and d_passive == 0.0
            and len(self._changed_indexes(inp, order_only_qr)) > 0
            and len(self._changed_indexes(inp, order_only_rq)) > 0
        )
        return {
            "fixture_id": fixture["id"],
            "family": fixture["family"],
            "positive": fixture["positive"],
            "valid": valid,
            "d_lacuna": d_lacuna,
            "d_passive": d_passive,
            "d_order_only": d_order_only,
            "d_random": d_random,
            "d_forked": d_forked,
            "effect": effect,
            "marked": 1 if effect >= self.EFFECT_THRESHOLD else 0,
        }

    def _reference(self, state: list[float], order: list[str], positive: bool) -> list[float]:
        st = list(state)
        for instrument in order:
            if instrument == AporiaLacunaKernelV2.REQUIREMENT_FOCUS:
                st[7] = 1.0 if positive else 0.0
                continue
            if st[7] > 0.0:
                st[8] -= 1.0
                st[9] -= 1.0
                continue
            st[10] -= 1.0
            st[11] -= 1.0
        return st

    def _random_matched(self, inp: list[float], candidate: list[float], excluded: list[int]) -> list[float]:
        deltas = sorted(self._signed_differences(inp, candidate))
        available = [i for i in range(self.DIMENSIONS) if i not in excluded]
        if len(available) < len(deltas):
            raise RuntimeError("aporia_lacuna_v2_random_space_invalid")

        canonical_inp = json.dumps(inp, separators=(",", ":"))
        seed = hashlib.sha256(canonical_inp.encode("utf-8")).hexdigest()[:16]
        generator = SplitMix64V1(seed)

        ranked = {}
        for idx in available:
            ranked[idx] = generator.nextHex()

        sorted_indices = sorted(ranked.keys(), key=lambda k: ranked[k])
        output = list(inp)
        for offset, delta in enumerate(deltas):
            target = sorted_indices[offset]
            output[target] += delta
        return output

    def _matching_valid(self, inp: list[float], candidate: list[float], random_out: list[float], excluded: list[int]) -> bool:
        candidate_deltas = sorted(self._signed_differences(inp, candidate))
        random_deltas = sorted(self._signed_differences(inp, random_out))
        if len(candidate_deltas) != len(random_deltas):
            return False
        for idx, d in enumerate(candidate_deltas):
            if abs(d - random_deltas[idx]) > 1.0e-12:
                return False
        changed = set(self._changed_indexes(inp, random_out))
        return len(changed.intersection(excluded)) == 0

    def _signed_differences(self, inp: list[float], output: list[float]) -> list[float]:
        res = []
        for i, val in enumerate(inp):
            diff = output[i] - val
            if abs(diff) > 1.0e-12:
                res.append(diff)
        return res

    def _changed_indexes(self, inp: list[float], output: list[float]) -> list[int]:
        res = []
        for i, val in enumerate(inp):
            if abs(val - output[i]) > 1.0e-12:
                res.append(i)
        return res

    def _order_only(self, inp: list[float], order: list[str]) -> list[float]:
        res = list(inp)
        first = order[0] if order else None
        dim = 29 if first == AporiaLacunaKernelV2.REQUIREMENT_FOCUS else 30
        res[dim] = 0.25 if first == AporiaLacunaKernelV2.REQUIREMENT_FOCUS else 0.75
        return res

    def _distance(self, inp: list[float], left: list[float], right: list[float]) -> float:
        left_probe = self._probe(inp, left)
        right_probe = self._probe(inp, right)
        diff = sum(1 for i in range(len(left_probe)) if left_probe[i] != right_probe[i])
        return diff / 16.0

    def _probe(self, inp: list[float], state: list[float]) -> list[bool]:
        probe = []
        for idx in range(16):
            left_changed = abs(state[idx] - inp[idx]) > 1.0e-12
            right_changed = abs(state[idx + 16] - inp[idx + 16]) > 1.0e-12
            probe.append(left_changed != right_changed)
        return probe

    def _assert_state(self, state: list[float]) -> None:
        if len(state) != self.DIMENSIONS:
            raise RuntimeError("aporia_lacuna_v2_dimension_invalid")
        for val in state:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise RuntimeError("aporia_lacuna_v2_state_invalid")

    def _merge_candidate_output(self, state: list[float], candidate_output: Any) -> list[float]:
        if isinstance(candidate_output, dict):
            if list(candidate_output.keys()) != list(range(self.CANDIDATE_DIMENSIONS)):
                raise RuntimeError("aporia_lacuna_v2_candidate_dimension_invalid")
            items = [candidate_output[i] for i in range(self.CANDIDATE_DIMENSIONS)]
        elif isinstance(candidate_output, (list, tuple)):
            if len(candidate_output) != self.CANDIDATE_DIMENSIONS:
                raise RuntimeError("aporia_lacuna_v2_candidate_dimension_invalid")
            items = list(candidate_output)
        else:
            raise RuntimeError("aporia_lacuna_v2_candidate_dimension_invalid")

        res = list(state)
        for idx, val in enumerate(items):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise RuntimeError("aporia_lacuna_v2_candidate_state_invalid")
            res[idx] = float(val)
        return res

    def _bootstrap(self, positives: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> tuple[float, float]:
        generator = SplitMix64V1(self.BOOTSTRAP_SEED)
        deltas = []
        for _ in range(self.BOOTSTRAP_REPETITIONS):
            pos_marks = 0
            neg_marks = 0
            for _ in range(50):
                pos_marks += positives[generator.nextBelow(50)]["marked"]
                neg_marks += negatives[generator.nextBelow(50)]["marked"]
            deltas.append((pos_marks / 50.0) - (neg_marks / 50.0))
        deltas.sort()
        return deltas[249], deltas[9749]
