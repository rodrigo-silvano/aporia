from __future__ import annotations

import importlib.util
import math
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("score_results.py")


def load_module():
    spec = importlib.util.spec_from_file_location("aporia_product_score_results_rc3", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zero_repeated_errors_in_both_arms_is_not_an_undefined_failure() -> None:
    module = load_module()

    assert module.rate_reduction(0.0, 0.0) == 0.0


def test_candidate_error_with_zero_baseline_is_a_regression() -> None:
    module = load_module()

    assert module.rate_reduction(0.01, 0.0) == -math.inf


def test_nonzero_baseline_preserves_reduction_threshold_semantics() -> None:
    module = load_module()

    assert math.isclose(module.rate_reduction(0.075, 0.1), 0.25)
