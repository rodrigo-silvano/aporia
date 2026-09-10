from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import os

from mechanistic_r7.canonical_state import (
    REPRESENTATION_FORMATS,
    binding_swap,
    build_state,
    commitment,
    decode_state,
    encode_state,
    expected_candidate,
    state_without_binding_hash,
    state_without_trajectory_hash,
    trajectory_swap,
)
from mechanistic_r7.protocol import (
    DEVELOPMENT_SEEDS,
    DEVELOPMENT_SYNTHETIC_SEEDS,
    SyntheticProposalProvider,
    run_protocol,
    simulate_power,
)
from mechanistic_r7.runner import CONFIRMATORY_MAX_RETRIES, _confirmatory_paths, _reserve, _validated_base_url
from mechanistic_r7.sealing import assert_current_seal, code_commitment


ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def development_execution() -> tuple[object, object]:
    provider = SyntheticProposalProvider()
    report = run_protocol(
        "development",
        provider,
        DEVELOPMENT_SEEDS,
        DEVELOPMENT_SYNTHETIC_SEEDS,
    )
    return report, provider


def test_seal_power_and_one_call_per_block_are_preserved() -> None:
    assert_current_seal(ROOT)
    assert CONFIRMATORY_MAX_RETRIES == 0
    assert simulate_power(64, 0.60, 0.85, 4000, 112991) >= 0.8
    assert_current_seal(ROOT, code_commitment(ROOT))


def test_all_representations_are_lossless_for_the_same_canonical_state() -> None:
    state = build_state(115001)
    decoded_hashes = {
        commitment(decode_state(encode_state(state, representation)))
        for representation in REPRESENTATION_FORMATS
    }
    assert decoded_hashes == {commitment(state)}
    assert len({commitment(encode_state(state, representation)) for representation in REPRESENTATION_FORMATS}) == len(REPRESENTATION_FORMATS)


def test_development_crossover_localizes_operator_under_information_isomorphism() -> None:
    report, provider = development_execution()
    assert len(provider.calls) == 64
    assert report.information_parity_by_construction == "PASS"
    assert report.information_isomorphism.blackwell_distance == 0.0
    assert report.information_isomorphism.decision_equivalence_rate == 1.0
    assert report.information_isomorphism.representation_arm_distinguishable is True
    assert report.crossover.lossless_roundtrip_rate == 1.0
    assert report.crossover.request_equivalence_rate == 1.0
    assert report.crossover.state_effect == 0.0
    assert report.crossover.state_operator_interaction == 0.0
    assert report.crossover.operator_effect_interval[0] > 0.0
    assert report.operator_crossover == "PASS"
    assert report.production_eligible is False
    assert report.phenomenology_established is False


def test_binding_representation_and_trajectory_interventions_are_separate() -> None:
    report, _ = development_execution()
    assert report.binding.passed
    assert report.binding.matched_except_binding_rate == 1.0
    assert report.binding.follows_intervention_rate == 1.0
    assert report.binding.both_operators_binding_access_rate == 1.0
    assert report.binding.renaming_and_order_invariance_rate == 1.0
    assert report.representations.passed
    assert report.representations.performance_variance == 0.0
    assert report.trajectory.passed
    assert report.trajectory.matched_except_trajectory_rate == 1.0
    assert report.trajectory.follows_causal_parent_intervention_rate == 1.0
    assert report.e_forecast == "ACCRUING"
    assert report.ecological_generalization == "BLOCKED_BY_REAL_DATA"
    assert report.g8 == "SEALED"
    assert report.e_policy == "NOT_ELIGIBLE"


def test_binding_and_trajectory_normalizations_hold_only_the_intervention_apart() -> None:
    state = build_state(115011)
    bound = binding_swap(state)
    trajectory = trajectory_swap(state)
    assert commitment(state) != commitment(bound)
    assert commitment(state) != commitment(trajectory)
    assert state_without_binding_hash(state) == state_without_binding_hash(bound)
    assert state_without_trajectory_hash(state) == state_without_trajectory_hash(trajectory)
    assert expected_candidate(state) != expected_candidate(bound)
    assert expected_candidate(state) != expected_candidate(trajectory)


def test_confirmatory_paths_require_fresh_sibling_files(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    ledger = tmp_path / "ledger.json"
    resolved_output, resolved_ledger = _confirmatory_paths(output, ledger)
    assert resolved_output.parent == resolved_ledger.parent
    descriptor = _reserve(output)
    os.close(descriptor)
    try:
        _reserve(output)
    except RuntimeError as exception:
        assert str(exception) == "mechanistic_r7_confirmatory_already_executed"
    else:
        raise AssertionError("mechanistic_r7_freshness_guard_missing")


def test_custom_provider_endpoint_must_be_official_https() -> None:
    assert _validated_base_url("") == ""
    assert _validated_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    for value in ("http://api.openai.com/v1", "https://example.com/v1", "https://api.openai.com/v1?x=1"):
        try:
            _validated_base_url(value)
        except RuntimeError as exception:
            assert str(exception) == "mechanistic_r7_base_url_invalid"
        else:
            raise AssertionError("mechanistic_r7_base_url_guard_missing")
