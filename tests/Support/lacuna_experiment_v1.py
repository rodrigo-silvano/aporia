"""
APORIA Logical Lacuna Experiment V1 Support.
Replicates AporiaLogicalLacunaExperimentV1.
"""

from __future__ import annotations
import math
import json
import hashlib
from typing import Any, Callable

from aporia.infrastructure.lacuna_kernel import AporiaLacunaKernel
from tests.Support.splitmix64 import SplitMix64V1


class AporiaLogicalLacunaExperimentV1:
    CORPUS_SEED = "4C4143554E415631"
    LABEL_SEED = "4C4143554E414C31"
    BOOTSTRAP_SEED = "4C4143554E414231"
    FIXTURE_COUNT = 100
    DIMENSIONS = 32
    BOOTSTRAP_REPETITIONS = 10000
    EFFECT_THRESHOLD = 0.125

    def fixtures(self) -> list[dict[str, Any]]:
        generator = SplitMix64V1(self.CORPUS_SEED)
        families = ["P0"] * 25 + ["P1"] * 25 + ["N0"] * 25 + ["N1"] * 25
        label_generator = SplitMix64V1(self.LABEL_SEED)
        for index in range(len(families) - 1, 0, -1):
            swap = label_generator.nextBelow(index + 1)
            families[index], families[swap] = families[swap], families[index]

        fixtures = []
        for index in range(self.FIXTURE_COUNT):
            family = families[index]
            state = []
            for _ in range(self.DIMENSIONS):
                state.append(generator.nextUnit())
            positive = family in ["P0", "P1"]
            unknown = family in ["P1", "N1"]
            state[0] = 1.0 / 6.0 if unknown else 0.0
            state[2] = 0.2
            state[3] = 0.0
            state[7] = 0.0
            for dim in range(1, 16):
                state[dim] = 0.0
            state[28] = 0.0
            program = {
                "q": {"operation": "activate", "marker": 28},
                "r": (
                    {"operation": "route_targets", "marker": 28, "active_targets": list(range(0, 8)), "inactive_targets": list(range(8, 16))}
                    if positive
                    else {"operation": "add_targets", "targets": list(range(0, 8))}
                ),
            }
            fixtures.append({
                "id": f"lacuna-v1-{index + 1:03d}",
                "index": index,
                "family": family,
                "positive": positive,
                "state": state,
                "program": program,
            })
        return fixtures

    def run(self, candidate: Callable[[list[float], list[str]], list[float]]) -> dict[str, Any]:
        return self._execute(candidate, False)

    def run_reference(self) -> dict[str, Any]:
        return self._execute(lambda state, order: state, True)

    def runReference(self) -> dict[str, Any]:
        return self.run_reference()

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

    @staticmethod
    def current_candidate(state: list[float], order: list[str]) -> list[float]:
        return AporiaLacunaKernel.sequence(state, order)

    currentCandidate = current_candidate

    @staticmethod
    def commutative_candidate(state: list[float], order: list[str]) -> list[float]:
        return state

    commutativeCandidate = commutative_candidate

    @staticmethod
    def reference_candidate(state: list[float], order: list[str], program: dict[str, Any]) -> list[float]:
        st = list(state)
        for instrument in order:
            key = "q" if instrument == AporiaLacunaKernel.ATTENTION else "r"
            op = program.get(key)
            if not isinstance(op, dict):
                raise RuntimeError("aporia_lacuna_experiment_program_invalid")
            kind = op.get("operation")
            if kind == "add_targets":
                for target in op["targets"]:
                    st[int(target)] += 1.0
                continue
            if kind == "activate":
                st[int(op["marker"])] = 1.0
                continue
            if kind == "route_targets":
                targets = op["active_targets"] if st[int(op["marker"])] > 0.0 else op["inactive_targets"]
                for target in targets:
                    st[int(target)] += 1.0
                continue
            raise RuntimeError("aporia_lacuna_experiment_operation_invalid")
        return st

    def _score(self, fixture: dict[str, Any], candidate: Callable[..., list[float]], reference: bool) -> dict[str, Any]:
        inp = list(fixture["state"])
        prog = fixture["program"]
        qr_order = [AporiaLacunaKernel.ATTENTION, AporiaLacunaKernel.CAUSAL]
        rq_order = list(reversed(qr_order))

        if reference:
            execute = lambda s, o: self.reference_candidate(s, o, prog)
        else:
            execute = lambda s, o: self._merge_candidate_output(s, candidate(s[:8], o))

        lacuna_qr = execute(inp, qr_order)
        lacuna_rq = execute(inp, rq_order)
        fork_qr_a = execute(inp, qr_order)
        fork_qr_b = execute(inp, qr_order)
        fork_rq_a = execute(inp, rq_order)
        fork_rq_b = execute(inp, rq_order)

        self._assert_state(inp, lacuna_qr)
        self._assert_state(inp, lacuna_rq)

        changed_idx = sorted(list(set(self._changed_indexes(inp, lacuna_qr) + self._changed_indexes(inp, lacuna_rq))))
        random_qr = self._random_matched(1, inp, lacuna_qr, changed_idx)
        random_rq = self._random_matched(2, inp, lacuna_rq, changed_idx)
        order_only_qr = self._order_only(inp, qr_order, prog)
        order_only_rq = self._order_only(inp, rq_order, prog)

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
            self._matching_valid(inp, lacuna_qr, random_qr, changed_idx)
            and self._matching_valid(inp, lacuna_rq, random_rq, changed_idx)
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
            "order_only_processed": len(self._changed_indexes(inp, order_only_qr)) > 0,
            "d_random": d_random,
            "d_forked": d_forked,
            "effect": effect,
            "marked": 1 if effect >= self.EFFECT_THRESHOLD else 0,
        }

    def _random_matched(self, branch: int, inp: list[float], candidate: list[float], excluded: list[int]) -> list[float]:
        deltas = []
        for i, val in enumerate(inp):
            delta = candidate[i] - val
            if abs(delta) > 1.0e-12:
                deltas.append(delta)

        available = [i for i in range(self.DIMENSIONS) if i not in excluded]
        if len(available) < len(deltas):
            raise RuntimeError("aporia_lacuna_experiment_random_space_invalid")

        canonical_inp = json.dumps(inp, separators=(",", ":"))
        seed_hash = hashlib.sha256(canonical_inp.encode("utf-8")).hexdigest()[:16]
        seed = SplitMix64V1.addSmallHex(seed_hash, branch)
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

    def _order_only(self, inp: list[float], order: list[str], program: dict[str, Any]) -> list[float]:
        res = list(inp)
        first = order[0] if order else None
        dim = 29 if first == AporiaLacunaKernel.ATTENTION else 30
        key = "q" if first == AporiaLacunaKernel.ATTENTION else "r"
        p_json = json.dumps(program[key], separators=(",", ":"))
        val_hex = hashlib.sha256(p_json.encode("utf-8")).hexdigest()[:8]
        res[dim] = int(val_hex, 16) / 4294967295.0
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

    def _assert_state(self, inp: list[float], output: list[float]) -> None:
        if len(inp) != self.DIMENSIONS or len(output) != self.DIMENSIONS:
            raise RuntimeError("aporia_lacuna_experiment_dimension_invalid")
        for val in output:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise RuntimeError("aporia_lacuna_experiment_state_invalid")

    def _merge_candidate_output(self, state: list[float], candidate_output: Any) -> list[float]:
        if isinstance(candidate_output, dict):
            if list(candidate_output.keys()) != list(range(8)):
                raise RuntimeError("aporia_lacuna_experiment_candidate_dimension_invalid")
            items = [candidate_output[i] for i in range(8)]
        elif isinstance(candidate_output, (list, tuple)):
            if len(candidate_output) != 8:
                raise RuntimeError("aporia_lacuna_experiment_candidate_dimension_invalid")
            items = list(candidate_output)
        else:
            raise RuntimeError("aporia_lacuna_experiment_candidate_dimension_invalid")

        res = list(state)
        for idx, val in enumerate(items):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise RuntimeError("aporia_lacuna_experiment_candidate_state_invalid")
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
