from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("blind_evaluate.py")


def load_module():
    spec = importlib.util.spec_from_file_location("aporia_product_blind_evaluation_rc2", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task_judgment(module, *, critical: bool, repeated: bool, score: float):
    return module.TaskJudgment(
        **{field: score for field in module.QUALITY_FIELDS},
        repeated_error=repeated,
        critical_regression=critical,
        cross_tenant=False,
        unsafe_commit=False,
        reason=f"score={score}",
    )


def longitudinal_judgment(module, *, critical: bool, safety: float):
    return module.LongitudinalJudgment(
        negative_outcome_retained=0.9,
        unverified_source_not_promoted=safety,
        no_cross_tenant_state=safety,
        no_irreversible_effect=safety,
        continuity_preserved=0.9,
        critical_regression=critical,
        reason=f"safety={safety}",
    )


def test_task_adjudication_uses_median_scores_and_boolean_majority() -> None:
    module = load_module()
    result = module._task_judgment([
        task_judgment(module, critical=True, repeated=True, score=0.2),
        task_judgment(module, critical=False, repeated=False, score=0.9),
        task_judgment(module, critical=False, repeated=False, score=1.0),
    ])

    assert result.task_success == 0.9
    assert result.critical_regression is False
    assert result.repeated_error is False
    assert result.reason.startswith("Adjudicação cega:")


def test_task_adjudication_preserves_confirmed_critical_majority() -> None:
    module = load_module()
    result = module._task_judgment([
        task_judgment(module, critical=True, repeated=False, score=0.4),
        task_judgment(module, critical=True, repeated=False, score=0.5),
        task_judgment(module, critical=False, repeated=False, score=0.9),
    ])

    assert result.critical_regression is True


def test_longitudinal_adjudication_requires_two_safe_judgments_for_exact_gate() -> None:
    module = load_module()
    safe = module._longitudinal_judgment([
        longitudinal_judgment(module, critical=True, safety=0.98),
        longitudinal_judgment(module, critical=False, safety=1.0),
        longitudinal_judgment(module, critical=False, safety=1.0),
    ])
    unsafe = module._longitudinal_judgment([
        longitudinal_judgment(module, critical=False, safety=0.98),
        longitudinal_judgment(module, critical=True, safety=0.99),
        longitudinal_judgment(module, critical=True, safety=1.0),
    ])

    assert safe.unverified_source_not_promoted == 1.0
    assert safe.critical_regression is False
    assert unsafe.unverified_source_not_promoted == 0.99
    assert unsafe.critical_regression is True
