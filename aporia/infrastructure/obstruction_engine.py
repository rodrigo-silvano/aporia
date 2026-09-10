"""Linear diagnostic obstruction engine for Aporia."""
from __future__ import annotations

import math
import re
from typing import Any


class AporiaObstructionEngine:
    RIDGE = 0.000001
    EPSILON = 0.000000000001

    def evaluate(
        self,
        observations: list[dict[str, Any]],
        overlaps: list[dict[str, Any]],
        boolean_constraints: list[dict[str, Any]],
        self_referential: bool,
        seeds: list[int],
    ) -> dict[str, Any]:
        obs = self._observations(observations)
        dimension = len(obs[0]["vector"])
        ovl = self._overlaps(overlaps, len(obs), dimension)
        sd = self._seeds(seeds)

        world_rows = [row for row in obs if row["scope"] == "world"]
        self_rows = [row for row in obs if row["scope"] == "self"]

        world = self._section(world_rows)
        self_sec = self._section(self_rows)
        cycle = self._cycle(obs, ovl)
        sat = self._satisfiability(boolean_constraints)

        multi_start = []
        for seed in sd:
            multi_start.append(self._corrected_score(self_rows, seed))

        corrected_self = self_sec["score"] if not multi_start else min(multi_start)

        classification = "globally_consistent"
        reason_codes = ["global_section_approximated", "linear_diagnostic_only"]

        if not sat["satisfiable"] or world["score"] >= 0.25:
            classification = "world_conflict"
            reason_codes.append("boolean_unsat" if not sat["satisfiable"] else "world_obstruction_high")
        elif self_sec["score"] >= 0.25 and corrected_self < 0.10:
            classification = "transport_error"
            reason_codes.append("obstruction_removed_by_transport_correction")
        elif self_referential and corrected_self >= 0.25 and cycle["score"] >= 0.25:
            classification = "self_obstruction_candidate"
            reason_codes.append("localized_reproducible_self_residual")
        elif self_sec["score"] >= 0.25:
            classification = "incomplete_or_unlocalized"
            reason_codes.append("self_claim_not_supported")

        unique_reasons = []
        for r in reason_codes:
            if r not in unique_reasons:
                unique_reasons.append(r)

        return {
            "world_score": world["score"],
            "self_score": self_sec["score"],
            "corrected_self_score": corrected_self,
            "cycle_score": cycle["score"],
            "classification": classification,
            "satisfiable": sat["satisfiable"],
            "unsat_constraint_hashes": sat["unsat_constraint_hashes"],
            "multi_start_scores": multi_start,
            "self_referential": self_referential,
            "reason_codes": unique_reasons,
            "version": 1,
        }

    def _observations(self, rows: list[Any]) -> list[dict[str, Any]]:
        if not isinstance(rows, list) or len(rows) < 2 or len(rows) > 32:
            raise RuntimeError("aporia_obstruction_observations_invalid")
        dimension = None
        observations = []
        for row in rows:
            if not isinstance(row, dict) or row.get("scope") not in ("world", "self"):
                raise RuntimeError("aporia_obstruction_observations_invalid")
            vector = self._vector(row.get("vector"))
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension:
                raise RuntimeError("aporia_obstruction_dimension_mismatch")
            weight = self._number(row.get("weight", 1.0))
            if weight <= 0.0 or weight > 1.0:
                raise RuntimeError("aporia_obstruction_weight_invalid")
            if "transport_correctable" in row and not isinstance(row["transport_correctable"], bool):
                raise RuntimeError("aporia_obstruction_transport_invalid")
            observations.append({
                "scope": row["scope"],
                "vector": vector,
                "weight": weight,
                "transport_correctable": bool(row.get("transport_correctable", False)),
            })
        return observations

    def _section(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        if not rows:
            return {"score": 0.0}
        dimension = len(rows[0]["vector"])
        global_vec = [0.0] * dimension
        weight_sum = 0.0
        for row in rows:
            weight_sum += row["weight"]
            for idx, val in enumerate(row["vector"]):
                global_vec[idx] += row["weight"] * val

        for idx in range(dimension):
            global_vec[idx] = global_vec[idx] / (weight_sum + self.RIDGE)

        residual = 0.0
        baseline = 0.0
        for row in rows:
            for idx, val in enumerate(row["vector"]):
                residual += row["weight"] * ((global_vec[idx] - val) ** 2)
                baseline += row["weight"] * (val ** 2)

        return {"score": math.sqrt(residual) / (math.sqrt(baseline) + self.EPSILON)}

    def _corrected_score(self, rows: list[dict[str, Any]], seed: int) -> float:
        if not rows:
            return 0.0
        if not any(row.get("transport_correctable") for row in rows):
            return self._section(rows)["score"]

        dimension = len(rows[0]["vector"])
        global_vec = [0.0] * dimension
        weight_sum = 0.0
        for row in rows:
            weight_sum += row["weight"]
            for idx, val in enumerate(row["vector"]):
                global_vec[idx] += row["weight"] * val

        for idx in range(dimension):
            global_vec[idx] = global_vec[idx] / (weight_sum + self.RIDGE)

        corrected = []
        residual_scale = 0.05 + ((seed % 17) / 1000.0)
        for row in rows:
            vector = []
            for idx, val in enumerate(row["vector"]):
                if row.get("transport_correctable"):
                    v = global_vec[idx] + (val - global_vec[idx]) * residual_scale
                else:
                    v = val
                vector.append(v)
            corrected.append({
                "scope": row["scope"],
                "vector": vector,
                "weight": row["weight"],
                "transport_correctable": row.get("transport_correctable", False),
            })
        return self._section(corrected)["score"]

    def _overlaps(self, edges: list[Any], count: int, dimension: int) -> list[tuple[int, int, list[float] | None]]:
        if not isinstance(edges, list) or len(edges) > 128:
            raise RuntimeError("aporia_obstruction_overlaps_invalid")
        result = []
        for edge in edges:
            if not isinstance(edge, dict):
                raise RuntimeError("aporia_obstruction_overlaps_invalid")
            try:
                left = int(edge.get("left", -1))
                right = int(edge.get("right", -1))
            except (ValueError, TypeError):
                raise RuntimeError("aporia_obstruction_overlaps_invalid")

            if left < 0 or right < 0 or left >= count or right >= count or left == right:
                raise RuntimeError("aporia_obstruction_overlaps_invalid")

            delta = self._vector(edge["delta"]) if "delta" in edge and edge["delta"] is not None else None
            if delta is not None and len(delta) != dimension:
                raise RuntimeError("aporia_obstruction_dimension_mismatch")
            result.append((left, right, delta))
        return result

    def _cycle(self, observations: list[dict[str, Any]], edges: list[tuple[int, int, list[float] | None]]) -> dict[str, float]:
        if not edges:
            return {"score": 0.0}
        n = len(observations)
        dimension = len(observations[0]["vector"])
        score_numerator = 0.0
        score_denominator = 0.0

        for coordinate in range(dimension):
            laplacian = [[0.0] * n for _ in range(n)]
            rhs = [0.0] * n
            deltas = []

            for left, right, transport_delta in edges:
                if transport_delta is None:
                    delta = observations[left]["vector"][coordinate] - observations[right]["vector"][coordinate]
                else:
                    delta = transport_delta[coordinate]
                deltas.append((left, right, delta))
                laplacian[left][left] += 1.0
                laplacian[right][right] += 1.0
                laplacian[left][right] -= 1.0
                laplacian[right][left] -= 1.0
                rhs[left] += delta
                rhs[right] -= delta

            for idx in range(n):
                laplacian[idx][idx] += self.RIDGE

            potential = self._solve(laplacian, rhs)
            for left, right, delta in deltas:
                residual = delta - (potential[left] - potential[right])
                score_numerator += residual ** 2
                score_denominator += delta ** 2

        return {"score": math.sqrt(score_numerator) / (math.sqrt(score_denominator) + self.EPSILON)}

    def _satisfiability(self, constraints: list[Any]) -> dict[str, Any]:
        if not isinstance(constraints, list) or len(constraints) > 64:
            raise RuntimeError("aporia_obstruction_constraints_invalid")
        variables: dict[str, dict[int, list[str]]] = {}
        var_regex = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
        commit_regex = re.compile(r"^[0-9a-f]{64}$")

        for constraint in constraints:
            if not isinstance(constraint, dict) or "variable" not in constraint or "value" not in constraint or "commitment" not in constraint:
                raise RuntimeError("aporia_obstruction_constraints_invalid")
            variable = str(constraint["variable"])
            value = constraint["value"]
            commitment = str(constraint["commitment"])

            if not var_regex.match(variable) or not isinstance(value, bool) or not commit_regex.match(commitment):
                raise RuntimeError("aporia_obstruction_constraints_invalid")

            val_int = int(value)
            if variable not in variables:
                variables[variable] = {}
            if val_int not in variables[variable]:
                variables[variable][val_int] = []
            variables[variable][val_int].append(commitment)

        unsat = []
        for values in variables.values():
            if 0 in values and 1 in values:
                unsat.extend(values[0])
                unsat.extend(values[1])

        unsat = sorted(list(set(unsat)))
        return {"satisfiable": len(unsat) == 0, "unsat_constraint_hashes": unsat}

    def _solve(self, matrix: list[list[float]], rhs: list[float]) -> list[float]:
        count = len(rhs)
        # Deep copy matrix and rhs to avoid in-place side effects
        a = [row[:] for row in matrix]
        b = rhs[:]

        for pivot in range(count):
            best = pivot
            for row in range(pivot + 1, count):
                if abs(a[row][pivot]) > abs(a[best][pivot]):
                    best = row
            a[pivot], a[best] = a[best], a[pivot]
            b[pivot], b[best] = b[best], b[pivot]

            divisor = self.RIDGE if abs(a[pivot][pivot]) < self.EPSILON else a[pivot][pivot]
            for column in range(pivot, count):
                a[pivot][column] /= divisor
            b[pivot] /= divisor

            for row in range(count):
                if row == pivot:
                    continue
                factor = a[row][pivot]
                for column in range(pivot, count):
                    a[row][column] -= factor * a[pivot][column]
                b[row] -= factor * b[pivot]

        return b

    def _vector(self, values: Any) -> list[float]:
        if not isinstance(values, (list, tuple)) or len(values) == 0 or len(values) > 32:
            raise RuntimeError("aporia_obstruction_vector_invalid")
        return [self._number(v) for v in values]

    def _number(self, value: Any) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RuntimeError("aporia_obstruction_numeric_invalid")
        val = float(value)
        if not math.isfinite(val):
            raise RuntimeError("aporia_obstruction_numeric_invalid")
        return val

    def _seeds(self, seeds: list[Any]) -> list[int]:
        if not isinstance(seeds, list) or len(seeds) < 3 or len(seeds) > 32:
            raise RuntimeError("aporia_obstruction_seeds_invalid")
        result = []
        for seed in seeds:
            if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
                raise RuntimeError("aporia_obstruction_seeds_invalid")
            result.append(seed)
        unique = []
        for s in result:
            if s not in unique:
                unique.append(s)
        return unique
