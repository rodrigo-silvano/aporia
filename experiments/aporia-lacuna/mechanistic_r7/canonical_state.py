from __future__ import annotations

import base64
import hashlib
import json
import random
import zlib
from copy import deepcopy
from typing import Any


REPRESENTATION_FORMATS = (
    "causal_graph",
    "relational_tables",
    "event_log",
    "adjacency_list",
    "canonical_text",
    "binary_bundle",
    "constraints",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def commitment(value: Any) -> str:
    payload = value if isinstance(value, bytes) else canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def build_state(seed: int, current_lineage: str | None = None) -> dict[str, Any]:
    generator = random.Random(seed)
    lineages = (f"lineage-{commitment(['A', seed])[:16]}", f"lineage-{commitment(['B', seed])[:16]}")
    active_lineage = current_lineage or lineages[seed % 2]
    if active_lineage not in lineages:
        raise ValueError("mechanistic_r7_binding_invalid")
    histories = []
    candidates = []
    for lineage_index, lineage in enumerate(lineages):
        action_refs = (
            f"action-{commitment([lineage, 'primary'])[:16]}",
            f"action-{commitment([lineage, 'secondary'])[:16]}",
        )
        selected_parent = action_refs[(seed + lineage_index) % 2]
        events = [
            {
                "event_ref": f"event-{commitment([lineage, 0])[:16]}",
                "timestamp": 1_700_000_000 + seed * 10,
                "causal_parent": "origin",
                "action_ref": action_refs[0],
                "outcome": "commitment_created",
                "prediction": round(0.45 + generator.random() * 0.1, 6),
                "uncertainty": round(0.2 + generator.random() * 0.1, 6),
            },
            {
                "event_ref": f"event-{commitment([lineage, 1])[:16]}",
                "timestamp": 1_700_000_001 + seed * 10,
                "causal_parent": action_refs[0],
                "action_ref": action_refs[1],
                "outcome": "commitment_revised",
                "prediction": round(0.55 + generator.random() * 0.1, 6),
                "uncertainty": round(0.1 + generator.random() * 0.1, 6),
            },
            {
                "event_ref": f"event-{commitment([lineage, 2])[:16]}",
                "timestamp": 1_700_000_002 + seed * 10,
                "causal_parent": selected_parent,
                "action_ref": "decision_pending",
                "outcome": "obligation_open",
                "prediction": round(0.65 + generator.random() * 0.1, 6),
                "uncertainty": round(0.05 + generator.random() * 0.05, 6),
            },
        ]
        histories.append({
            "lineage_ref": lineage,
            "history_commitment": commitment(events),
            "events": events,
        })
        candidates.extend([
            {
                "candidate_id": f"candidate-{commitment([lineage, action_ref])[:16]}",
                "lineage_ref": lineage,
                "source_action_ref": action_ref,
                "operation": "honour_obligation",
            }
            for action_ref in action_refs
        ])
    bindings = [
        {"identity_ref": "current_identity", "lineage_ref": active_lineage},
        {"identity_ref": "observed_identity", "lineage_ref": next(item for item in lineages if item != active_lineage)},
    ]
    expected = _expected_candidate(histories, bindings, candidates)
    distractors = [item["candidate_id"] for item in candidates if item["candidate_id"] != expected]
    visible_signal = expected if seed % 5 in {0, 1, 2} else distractors[seed % len(distractors)]
    return {
        "schema": "aporia_information_isomorphic_state_v1",
        "world_ref": f"world-{commitment(seed)[:20]}",
        "events": [event for history in histories for event in history["events"]],
        "outcomes": [event["outcome"] for history in histories for event in history["events"]],
        "predictions": [event["prediction"] for history in histories for event in history["events"]],
        "timestamps": [event["timestamp"] for history in histories for event in history["events"]],
        "causal_parents": [event["causal_parent"] for history in histories for event in history["events"]],
        "histories": histories,
        "commitments": [history["history_commitment"] for history in histories],
        "uncertainty": [event["uncertainty"] for history in histories for event in history["events"]],
        "facts": [
            {"key": "all_histories_visible", "value": True},
            {"key": "all_bindings_visible", "value": True},
            {"key": "world_seed_commitment", "value": commitment(seed)},
        ],
        "relations": [
            {"subject": item["identity_ref"], "predicate": "bound_to", "object": item["lineage_ref"]}
            for item in bindings
        ],
        "provenance": {
            "source": "synthetic_r7",
            "world_commitment": commitment([seed, histories]),
            "real_payload": False,
        },
        "bindings": bindings,
        "candidates": candidates,
        "visible_signal": visible_signal,
    }


def expected_candidate(state: dict[str, Any]) -> str:
    return _expected_candidate(state["histories"], state["bindings"], state["candidates"])


def binding_swap(state: dict[str, Any]) -> dict[str, Any]:
    swapped = deepcopy(state)
    current = next(item for item in swapped["bindings"] if item["identity_ref"] == "current_identity")
    observed = next(item for item in swapped["bindings"] if item["identity_ref"] == "observed_identity")
    current["lineage_ref"], observed["lineage_ref"] = observed["lineage_ref"], current["lineage_ref"]
    for relation in swapped["relations"]:
        if relation["subject"] == "current_identity":
            relation["object"] = current["lineage_ref"]
        elif relation["subject"] == "observed_identity":
            relation["object"] = observed["lineage_ref"]
    return swapped


def trajectory_swap(state: dict[str, Any]) -> dict[str, Any]:
    swapped = deepcopy(state)
    current_lineage = next(
        item["lineage_ref"] for item in swapped["bindings"] if item["identity_ref"] == "current_identity"
    )
    history = next(item for item in swapped["histories"] if item["lineage_ref"] == current_lineage)
    action_refs = [event["action_ref"] for event in history["events"] if event["action_ref"] != "decision_pending"]
    terminal = history["events"][-1]
    terminal["causal_parent"] = next(item for item in action_refs if item != terminal["causal_parent"])
    history["history_commitment"] = commitment(history["events"])
    swapped["events"] = [event for item in swapped["histories"] for event in item["events"]]
    swapped["causal_parents"] = [event["causal_parent"] for item in swapped["histories"] for event in item["events"]]
    swapped["commitments"] = [item["history_commitment"] for item in swapped["histories"]]
    swapped["provenance"]["world_commitment"] = commitment(swapped["histories"])
    return swapped


def state_without_binding_hash(state: dict[str, Any]) -> str:
    normalized = deepcopy(state)
    normalized["bindings"] = sorted(item["identity_ref"] for item in normalized["bindings"])
    normalized["relations"] = sorted((item["subject"], item["predicate"]) for item in normalized["relations"])
    return commitment(normalized)


def state_without_trajectory_hash(state: dict[str, Any]) -> str:
    normalized = deepcopy(state)
    for history in normalized["histories"]:
        for event in history["events"]:
            event["causal_parent"] = "intervened"
        history["history_commitment"] = "intervened"
    for event in normalized["events"]:
        event["causal_parent"] = "intervened"
    normalized["causal_parents"] = ["intervened"] * len(normalized["causal_parents"])
    normalized["commitments"] = ["intervened"] * len(normalized["commitments"])
    normalized["provenance"]["world_commitment"] = "intervened"
    return commitment(normalized)


def encode_state(state: dict[str, Any], representation: str) -> dict[str, Any]:
    records = _records(state)
    if representation == "causal_graph":
        nodes = []
        edges = []
        for index, record in enumerate(records):
            node_id = commitment(record["path"])[:20]
            nodes.append({"node_id": node_id, **record})
            if record["path"]:
                edges.append({"source": commitment(record["path"][:-1])[:20], "target": node_id})
        return {"schema": "aporia_causal_graph_v1", "nodes": nodes, "edges": edges}
    if representation == "relational_tables":
        return {
            "schema": "zombie_relational_tables_v1",
            "paths": [item["path"] for item in records],
            "kinds": [item["kind"] for item in records],
            "values": [item.get("value") for item in records],
        }
    if representation == "event_log":
        return {
            "schema": "state_event_log_v1",
            "events": [{"sequence": index, "operation": "assign", **item} for index, item in enumerate(records)],
        }
    if representation == "adjacency_list":
        return {
            "schema": "state_adjacency_list_v1",
            "nodes": [
                {
                    "node_id": commitment(item["path"])[:20],
                    "parent_id": commitment(item["path"][:-1])[:20] if item["path"] else None,
                    **item,
                }
                for item in records
            ],
        }
    if representation == "canonical_text":
        return {
            "schema": "state_canonical_text_v1",
            "text": "\n".join(base64.b64encode(canonical_bytes(item)).decode("ascii") for item in records),
        }
    if representation == "binary_bundle":
        return {
            "schema": "state_binary_bundle_v1",
            "encoding": "zlib+base64",
            "payload": base64.b64encode(zlib.compress(canonical_bytes(records), level=9)).decode("ascii"),
        }
    if representation == "constraints":
        return {
            "schema": "state_constraints_v1",
            "constraints": [
                f"{commitment(item['path'])}=={base64.b64encode(canonical_bytes(item)).decode('ascii')}"
                for item in records
            ],
        }
    raise ValueError("mechanistic_r7_representation_invalid")


def decode_state(encoded: dict[str, Any]) -> dict[str, Any]:
    schema = str(encoded.get("schema") or "")
    if schema == "aporia_causal_graph_v1":
        records = [{key: value for key, value in item.items() if key != "node_id"} for item in encoded["nodes"]]
    elif schema == "zombie_relational_tables_v1":
        records = [
            {"path": path, "kind": kind, **({"value": value} if kind == "scalar" else {})}
            for path, kind, value in zip(encoded["paths"], encoded["kinds"], encoded["values"])
        ]
    elif schema == "state_event_log_v1":
        records = [
            {key: value for key, value in item.items() if key not in {"sequence", "operation"}}
            for item in sorted(encoded["events"], key=lambda item: item["sequence"])
        ]
    elif schema == "state_adjacency_list_v1":
        records = [
            {key: value for key, value in item.items() if key not in {"node_id", "parent_id"}}
            for item in encoded["nodes"]
        ]
    elif schema == "state_canonical_text_v1":
        records = [json.loads(base64.b64decode(line)) for line in encoded["text"].splitlines() if line]
    elif schema == "state_binary_bundle_v1":
        records = json.loads(zlib.decompress(base64.b64decode(encoded["payload"])))
    elif schema == "state_constraints_v1":
        records = [json.loads(base64.b64decode(item.split("==", 1)[1])) for item in encoded["constraints"]]
    else:
        raise ValueError("mechanistic_r7_representation_schema_invalid")
    value = _restore(records)
    if not isinstance(value, dict):
        raise ValueError("mechanistic_r7_decoded_state_invalid")
    return value


def cross_kernel(encoded: dict[str, Any], target_representation: str) -> dict[str, Any]:
    return encode_state(decode_state(encoded), target_representation)


def _expected_candidate(histories: list[dict[str, Any]], bindings: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> str:
    lineage = next(item["lineage_ref"] for item in bindings if item["identity_ref"] == "current_identity")
    history = next(item for item in histories if item["lineage_ref"] == lineage)
    causal_parent = history["events"][-1]["causal_parent"]
    return next(
        item["candidate_id"]
        for item in candidates
        if item["lineage_ref"] == lineage and item["source_action_ref"] == causal_parent
    )


def _records(value: Any, path: tuple[str | int, ...] = ()) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        records = [{"path": list(path), "kind": "dict"}]
        for key in sorted(value):
            records.extend(_records(value[key], path + (key,)))
        return records
    if isinstance(value, list):
        records = [{"path": list(path), "kind": "list"}]
        for index, item in enumerate(value):
            records.extend(_records(item, path + (index,)))
        return records
    return [{"path": list(path), "kind": "scalar", "value": value}]


def _restore(records: list[dict[str, Any]]) -> Any:
    indexed = {tuple(item["path"]): item for item in records}
    if () not in indexed:
        raise ValueError("mechanistic_r7_root_missing")

    def restore(path: tuple[str | int, ...]) -> Any:
        record = indexed.get(path)
        if record is None:
            raise ValueError("mechanistic_r7_path_missing")
        kind = record["kind"]
        children = sorted(
            (candidate for candidate in indexed if len(candidate) == len(path) + 1 and candidate[:-1] == path),
            key=lambda item: (isinstance(item[-1], int), item[-1]),
        )
        if kind == "dict":
            return {str(child[-1]): restore(child) for child in children}
        if kind == "list":
            if [child[-1] for child in children] != list(range(len(children))):
                raise ValueError("mechanistic_r7_list_path_invalid")
            return [restore(child) for child in children]
        if kind == "scalar" and not children:
            return record.get("value")
        raise ValueError("mechanistic_r7_record_invalid")

    return restore(())
