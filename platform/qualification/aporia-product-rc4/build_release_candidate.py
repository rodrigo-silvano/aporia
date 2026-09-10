#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXPECTED_BRANCH = "aporia/product-readiness-rc"
LOCKS = ("corpus.lock.json", "run.lock.json", "evaluation.lock.json")
REQUIRED_EVIDENCE = {
    "product_evaluation": "PASS",
    "e2e": "PASS",
    "load": "PASS",
    "soak": "PASS",
    "control_switches": "PASS",
    "rollback_restore": "PASS",
    "tenant_health": "healthy",
    "global_health": "healthy",
    "services_docroots": "PASS",
    "final_mode": "PASS",
}
LOCKFILES = (
    "composer.lock",
    "platform/frontend/builder-canvas/package-lock.json",
    "services/ai-gateway/requirements.lock",
    "services/agent-runtime/requirements.lock",
    "services/agent-runtime/ui/package-lock.json",
    "services/partners/package-lock.json",
    "services/profile-analyzer/requirements.lock",
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"rc_json_object_required:{path.name}")
    return value


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"rc_git_failed:{args[0]}")
    return result.stdout.strip()


def validate_locked_plane() -> dict[str, Any]:
    corpus = load_json(HERE / "corpus.json")
    corpus_lock = load_json(HERE / "corpus.lock.json")
    run_manifest = load_json(HERE / "run-manifest.json")
    run_lock = load_json(HERE / "run.lock.json")
    evaluation_manifest = load_json(HERE / "evaluation-manifest.json")
    evaluation_lock = load_json(HERE / "evaluation.lock.json")
    operational = load_json(HERE / "operational-report.json")

    if sha_bytes(canonical(corpus)) != corpus_lock.get("corpus_sha256"):
        raise RuntimeError("rc_corpus_commitment_mismatch")
    if sha_bytes(canonical(run_manifest)) != run_lock.get("manifest_sha256"):
        raise RuntimeError("rc_run_commitment_mismatch")
    participant_runner_sha256 = sha_file(HERE / "run_product_evaluation.py")
    if participant_runner_sha256 != run_manifest.get("participant_runner_sha256") \
        or participant_runner_sha256 != run_lock.get("participant_runner_sha256"):
        raise RuntimeError("rc_participant_runner_changed_after_seal")
    if sha_bytes(canonical(evaluation_manifest)) != evaluation_lock.get("manifest_sha256"):
        raise RuntimeError("rc_evaluation_commitment_mismatch")
    if sha_file(HERE / "blind_evaluate.py") != evaluation_lock.get("evaluator_sha256"):
        raise RuntimeError("rc_blind_evaluator_changed_after_seal")
    if operational.get("status") != "PASS":
        raise RuntimeError("rc_operational_gate_failed")
    if any(value.get("g8_consumed") is not False for value in (corpus_lock, run_lock, evaluation_lock, operational)):
        raise RuntimeError("rc_g8_boundary_invalid")
    return {
        "corpus_sha256": corpus_lock["corpus_sha256"],
        "run_manifest_sha256": run_lock["manifest_sha256"],
        "participant_runner_sha256": participant_runner_sha256,
        "evaluation_manifest_sha256": evaluation_lock["manifest_sha256"],
        "evaluator_sha256": evaluation_lock["evaluator_sha256"],
        "operational_report_sha256": sha_file(HERE / "operational-report.json"),
    }


def parse_evidence(values: list[str]) -> dict[str, Path]:
    evidence: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or name not in REQUIRED_EVIDENCE or name in evidence:
            raise RuntimeError("rc_evidence_argument_invalid")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"rc_evidence_missing:{name}")
        evidence[name] = path
    missing = sorted(set(REQUIRED_EVIDENCE) - set(evidence))
    if missing:
        raise RuntimeError("rc_evidence_incomplete:" + ",".join(missing))
    return evidence


def validate_evidence(paths: dict[str, Path]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in sorted(paths):
        path = paths[name]
        payload = load_json(path)
        if payload.get("status") != REQUIRED_EVIDENCE[name]:
            raise RuntimeError(f"rc_evidence_gate_failed:{name}")
        if payload.get("g8_consumed") not in (None, False):
            raise RuntimeError(f"rc_evidence_g8_invalid:{name}")
        if name == "final_mode" and payload.get("effective_mode") != "guarded_reversible":
            raise RuntimeError("rc_final_mode_invalid")
        if name in {"load", "soak"}:
            if payload.get("content_persisted") is not False or payload.get("secrets_persisted") is not False:
                raise RuntimeError(f"rc_runtime_probe_privacy_invalid:{name}")
        result[name] = {
            "sha256": sha_file(path),
            "status": payload["status"],
            "report_commitment": payload.get("report_commitment"),
        }
    return result


def component(ecosystem: str, name: str, version: str, source: str) -> dict[str, str]:
    return {"ecosystem": ecosystem, "name": name, "version": version, "source": source}


def build_sbom(commit: str) -> dict[str, Any]:
    components: list[dict[str, str]] = []
    composer = load_json(ROOT / "composer.lock")
    for section in ("packages", "packages-dev"):
        for package in composer.get(section, []):
            if isinstance(package, dict) and package.get("name") and package.get("version"):
                components.append(component("composer", str(package["name"]), str(package["version"]), "composer.lock"))
    for relative in LOCKFILES:
        path = ROOT / relative
        if path.name != "package-lock.json":
            continue
        lock = load_json(path)
        for package_path, package in lock.get("packages", {}).items():
            if not package_path or not isinstance(package, dict) or not package.get("version"):
                continue
            name = package.get("name") or package_path.rsplit("node_modules/", 1)[-1]
            components.append(component("npm", str(name), str(package["version"]), relative))
    for relative in LOCKFILES:
        path = ROOT / relative
        if path.name != "requirements.lock":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;]+)", line.strip())
            if match:
                components.append(component("pypi", match.group(1).lower(), match.group(2), relative))
    unique = {(item["ecosystem"], item["name"], item["version"], item["source"]): item for item in components}
    return {
        "schema_version": 1,
        "format": "aporia-lockfile-inventory-v1",
        "commit": commit,
        "components": sorted(unique.values(), key=lambda item: (item["ecosystem"], item["name"], item["version"], item["source"])),
        "lockfiles": {relative: sha_file(ROOT / relative) for relative in LOCKFILES},
        "limitations": [
            "Lockfile inventory; not a CycloneDX or SPDX attestation.",
            "Operating-system and container-base packages require image-level scanning after build.",
        ],
    }


def validate_git_for_seal() -> tuple[str, str]:
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise RuntimeError("rc_branch_invalid")
    if (ROOT / ".codex/hooks").is_symlink():
        raise RuntimeError("rc_temporary_hook_must_be_removed_by_coordination")
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT, check=False).returncode != 0:
        raise RuntimeError("rc_tracked_worktree_dirty")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False).returncode != 0:
        raise RuntimeError("rc_index_dirty")
    staging_ref = git("rev-parse", "--verify", "staging", check=False)
    if staging_ref:
        changed = git(
            "diff", "--name-only", "staging...HEAD", "--",
            "experiments/aporia-lacuna", "aporia/infrastructure/effect_compiler.py"
        )
        if changed:
            raise RuntimeError("rc_scientific_artifact_changed")
    return branch, git("rev-parse", "HEAD")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--seal", action="store_true")
    parser.add_argument("--release-id")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--manifest-output", type=Path, default=HERE / "release-candidate-manifest.json")
    parser.add_argument("--sbom-output", type=Path, default=HERE / "release-candidate-sbom.json")
    args = parser.parse_args()
    if args.validate_only == args.seal:
        raise SystemExit("choose_exactly_one_of_validate_only_or_seal")
    locks = validate_locked_plane()
    if args.validate_only:
        branch = git("branch", "--show-current")
        print(json.dumps({
            "status": "VALID",
            "branch": branch,
            "expected_branch": EXPECTED_BRANCH,
            "locked_plane": locks,
            "staging_integration_blocked_by_temporary_hook": (ROOT / ".codex/hooks").is_symlink(),
            "g8_consumed": False,
        }, sort_keys=True))
        return
    if not args.release_id or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.release_id) is None:
        raise SystemExit("rc_release_id_invalid")
    branch, commit = validate_git_for_seal()
    evidence = validate_evidence(parse_evidence(args.evidence))
    sbom = build_sbom(commit)
    sbom["document_commitment"] = sha_bytes(canonical(sbom))
    args.sbom_output.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "release_candidate": "APORIA_PRODUCT_RC4_DIAGNOSTIC",
        "status": "QUALIFIED",
        "release_id": args.release_id,
        "branch": branch,
        "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "locked_plane": locks,
        "evidence": evidence,
        "sbom_sha256": sha_file(args.sbom_output),
        "g8_consumed": False,
        "historical_r4_r7_reused": False,
        "scientific_confirmation": False,
        "production_authorized": False,
        "qualified_modes": ["advisory", "guarded_reversible"],
        "final_staging_mode": "guarded_reversible",
    }
    manifest["manifest_commitment"] = sha_bytes(canonical(manifest))
    args.manifest_output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "QUALIFIED", "manifest_commitment": manifest["manifest_commitment"]}, sort_keys=True))


if __name__ == "__main__":
    main()
