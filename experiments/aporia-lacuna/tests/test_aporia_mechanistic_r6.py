from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import os

from mechanistic_r6.mechanisms import ScarAuthority
from mechanistic_r6.protocol import (
    DEVELOPMENT_SEEDS,
    DEVELOPMENT_SYNTHETIC_SEEDS,
    SyntheticProposalProvider,
    run_protocol,
    simulate_power,
)
from mechanistic_r6.runner import CONFIRMATORY_MAX_RETRIES, _confirmatory_paths, _reserve, _validated_base_url
from mechanistic_r6.sealing import assert_current_seal, code_commitment


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


def test_seal_and_power_are_preserved() -> None:
    assert_current_seal(ROOT)
    assert CONFIRMATORY_MAX_RETRIES == 0
    assert simulate_power(48, 0.55, 0.90, 4000, 108991) >= 0.8
    assert_current_seal(ROOT, code_commitment(ROOT))


def test_development_localizes_effect_and_retains_negative_information_result() -> None:
    report, provider = development_execution()
    assert len(provider.calls) == 48
    assert report.mechanistic_localization == "PASS"
    assert report.factorial.pre_model_state_path == "structurally_absent_in_model_mediation_r1"
    assert report.factorial.post_model_gateway_path == "causal_gateway_replay_present"
    assert report.factorial.request_equivalence_rate == 1.0
    assert report.factorial.aporia_normalized_correctness == 1.0
    assert report.factorial.raw_proposal_correctness == report.factorial.zombie_plus_normalized_correctness
    assert report.factorial.paired_gateway_interval[0] > 0
    assert report.factorial.randomization_p <= 0.01
    assert report.information_parity_state == "FAIL"
    assert report.information_parity.classification == "informational_or_structural_advantage"
    assert report.information_parity.answer_permutation_p <= 0.01
    assert report.production_eligible is False
    assert report.phenomenology_established is False


def test_provenance_isomorphism_history_and_poisoning_gates() -> None:
    report, _ = development_execution()
    assert report.provenance.condition_performance["A"] == 1.0
    assert report.provenance.condition_performance["A"] > max(
        value for name, value in report.provenance.condition_performance.items() if name != "A"
    )
    assert report.provenance.lesion_effect > 0.2
    assert report.provenance.graft_effect > 0.2
    assert report.provenance.reconstruction_accuracy <= 0.30
    assert report.provenance.matched_vector_dimensions
    assert report.provenance.matched_prompt_hashes
    assert report.provenance.matched_request_hashes
    assert report.isomorphism.passed
    assert report.historical_update.passed
    assert report.poisoning.passed
    assert report.poisoning.rejected == report.poisoning.attempts
    assert report.poisoning.persisted_attack_fields == 0
    assert 0 < report.poisoning.one_sided_risk_upper_95 < 0.01


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
        assert str(exception) == "mechanistic_r6_confirmatory_already_executed"
    else:
        raise AssertionError("mechanistic_r6_freshness_guard_missing")


def test_custom_provider_endpoint_must_be_official_https() -> None:
    assert _validated_base_url("") == ""
    assert _validated_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    for value in ("http://api.openai.com/v1", "https://example.com/v1", "https://api.openai.com/v1?x=1"):
        try:
            _validated_base_url(value)
        except RuntimeError as exception:
            assert str(exception) == "mechanistic_r6_base_url_invalid"
        else:
            raise AssertionError("mechanistic_r6_base_url_guard_missing")


def test_authorized_graft_rejects_counterfeit_source() -> None:
    authority = ScarAuthority("r6-test-authority")
    scar = authority.issue("lineage", "agent", "episode", "action", "action", "outcome", "content", 10)
    forged = authority.counterfeit(scar, "causal_parent", "external")
    try:
        authority.authorized_graft(forged, "new-lineage", "new-agent", 11)
    except ValueError as exception:
        assert str(exception) == "mechanistic_r6_graft_source_invalid"
    else:
        raise AssertionError("mechanistic_r6_graft_source_guard_missing")
