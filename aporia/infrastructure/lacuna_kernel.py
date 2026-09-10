"""
Aporia Lacuna Kernels (V1 and V2).
Mathematical and deterministic state-probe execution models.
"""

from __future__ import annotations
import math
from typing import Any, Sequence


class AporiaLacunaKernel:
    """Lacuna Kernel V1."""

    ATTENTION = "attention_blindspot_probe_v1"
    CAUSAL = "causal_context_probe_v1"

    @classmethod
    def sequence(cls, state: Sequence[float], instruments: Sequence[str]) -> list[float]:
        curr = list(state)
        for instrument in instruments:
            curr = cls.apply(curr, str(instrument))
        return curr

    @classmethod
    def apply(cls, state: Sequence[float], instrument: str) -> list[float]:
        if len(state) < 8:
            raise ValueError("aporia_lacuna_state_invalid")
        for val in state:
            if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                raise ValueError("aporia_lacuna_state_invalid")

        res = [float(x) for x in state]
        if instrument == cls.ATTENTION:
            res[7] = 1.0 if res[0] > 0.0 else 0.0
            return res
        if instrument == cls.CAUSAL:
            if res[7] > 0.0:
                res[2] = -1.0
                res[3] = -1.0
            return res

        raise ValueError("aporia_lacuna_instrument_unsupported")

    @classmethod
    def order_score(cls, left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or len(left) == 0:
            raise ValueError("aporia_lacuna_dimension_mismatch")

        diff = 0.0
        left_norm = 0.0
        right_norm = 0.0
        for i, val in enumerate(left):
            diff += (val - right[i]) ** 2
            left_norm += val ** 2
            right_norm += right[i] ** 2

        denom = max(1.0, math.sqrt(left_norm), math.sqrt(right_norm))
        score = math.sqrt(diff) / denom
        if not math.isfinite(score):
            raise ValueError("aporia_lacuna_metric_non_finite")
        return min(1.0, score)

    @classmethod
    def orderScore(cls, left: Sequence[float], right: Sequence[float]) -> float:
        return cls.order_score(left, right)

    @classmethod
    def differences(cls, input_state: Sequence[float], output_state: Sequence[float]) -> list[float]:
        if len(input_state) != len(output_state):
            raise ValueError("aporia_lacuna_dimension_mismatch")
        diffs = []
        for i, val in enumerate(input_state):
            d = abs(val - output_state[i])
            if d > 1e-12:
                diffs.append(d)
        return diffs

    @classmethod
    def changed_dimensions(cls, input_state: Sequence[float], output_state: Sequence[float]) -> int:
        return len(cls.differences(input_state, output_state))

    @classmethod
    def changedDimensions(cls, input_state: Sequence[float], output_state: Sequence[float]) -> int:
        return cls.changed_dimensions(input_state, output_state)

    @classmethod
    def magnitude(cls, input_state: Sequence[float], output_state: Sequence[float]) -> float:
        return sum(cls.differences(input_state, output_state))

    @classmethod
    def loss(cls, input_state: Sequence[float], output_state: Sequence[float]) -> float:
        return cls.changed_dimensions(input_state, output_state) / max(1, len(input_state))


class AporiaLacunaKernelV2:
    """Lacuna Kernel V2."""

    REQUIREMENT_FOCUS = "causal_requirement_focus_v2"
    DEPENDENCY_PROJECTION = "dependency_context_projection_v2"

    @classmethod
    def sequence(cls, state: Sequence[float], instruments: Sequence[str]) -> list[float]:
        curr = list(state)
        for instrument in instruments:
            curr = cls.apply(curr, str(instrument))
        return curr

    @classmethod
    def apply(cls, state: Sequence[float], instrument: str) -> list[float]:
        cls._assert_state(state)
        res = [float(x) for x in state]
        if instrument == cls.REQUIREMENT_FOCUS:
            produces_mask = int(res[1])
            requires_mask = int(res[2])
            res[7] = 1.0 if (int(res[3]) == 1 and (produces_mask & requires_mask) != 0) else 0.0
            return res
        if instrument == cls.DEPENDENCY_PROJECTION:
            if res[7] > 0.0:
                res[8] -= 1.0
                res[9] -= 1.0
            else:
                res[10] -= 1.0
                res[11] -= 1.0
            return res

        raise ValueError("aporia_lacuna_v2_instrument_unsupported")

    @classmethod
    def _assert_state(cls, state: Sequence[float]) -> None:
        if len(state) != 12:
            raise RuntimeError("aporia_lacuna_v2_state_invalid")
        for val in state:
            if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                raise RuntimeError("aporia_lacuna_v2_state_invalid")

        s1 = float(state[1])
        s2 = float(state[2])
        if s1 != float(int(s1)) or s2 != float(int(s2)) or int(s1) < 0 or int(s1) > 15 or int(s2) < 0 or int(s2) > 15:
            raise RuntimeError("aporia_lacuna_v2_mask_invalid")

        s3 = float(state[3])
        if s3 != float(int(s3)) or int(s3) not in (0, 1):
            raise RuntimeError("aporia_lacuna_v2_parent_count_invalid")
