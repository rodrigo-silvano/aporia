"""Ontology topology, experiments, ledger, and residual collector for Aporia."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from typing import Any

from aporia.infrastructure.db import Connection


class AporiaOntologyTopology:
    def filtration(self, vectors: list[list[float]], epsilons: list[float]) -> dict[str, Any]:
        norm_vectors = self._vectors(vectors)
        norm_epsilons = self._epsilons(epsilons)
        result = []
        n = len(norm_vectors)

        for epsilon in norm_epsilons:
            edges = []
            parents = list(range(n))
            for left in range(n):
                for right in range(left + 1, n):
                    if self._distance(norm_vectors[left], norm_vectors[right]) <= epsilon + 1e-12:
                        edges.append((left, right))
                        self._union(parents, left, right)

            components = {}
            for vertex in range(n):
                components[self._find(parents, vertex)] = True

            triangles = []
            edge_lookup = {f"{left}:{right}": idx for idx, (left, right) in enumerate(edges)}

            for a in range(n):
                for b in range(a + 1, n):
                    for c in range(b + 1, n):
                        k0 = f"{a}:{b}"
                        k1 = f"{a}:{c}"
                        k2 = f"{b}:{c}"
                        if k0 in edge_lookup and k1 in edge_lookup and k2 in edge_lookup:
                            triangles.append([
                                edge_lookup[k0],
                                edge_lookup[k1],
                                edge_lookup[k2],
                            ])

            boundary_rank = self._boundary_rank(len(edges), triangles)
            h0 = len(components)
            h1 = max(0, len(edges) - n + h0 - boundary_rank)
            result.append({
                "epsilon": epsilon,
                "h0": h0,
                "h1": h1,
                "vertices": n,
                "edges": len(edges),
                "triangles": len(triangles),
                "boundary_rank_2": boundary_rank,
            })

        return {
            "method": "vietoris_rips_betti_gf2",
            "dimensions": ["h0", "h1"],
            "filtration": result,
            "version": 1,
        }

    def compare(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        before_rows = before.get("filtration")
        after_rows = after.get("filtration")
        if not isinstance(before_rows, list) or not isinstance(after_rows, list) or len(before_rows) != len(after_rows) or not before_rows:
            raise RuntimeError("aporia_ontology_topology_comparison_invalid")

        distance = 0.0
        for idx, row in enumerate(before_rows):
            other = after_rows[idx]
            if not isinstance(row, dict) or not isinstance(other, dict) or abs(float(row.get("epsilon", 0.0)) - float(other.get("epsilon", 0.0))) > 1e-12:
                raise RuntimeError("aporia_ontology_topology_comparison_invalid")
            distance += abs(int(row["h0"]) - int(other["h0"]))
            distance += abs(int(row["h1"]) - int(other["h1"]))

        return {
            "betti_curve_l1": distance / len(before_rows),
            "method": "discrete_betti_curve_l1",
            "not_bottleneck_distance": True,
            "version": 1,
        }

    def _boundary_rank(self, edge_count: int, triangles: list[list[int]]) -> int:
        if edge_count == 0 or not triangles:
            return 0
        rows: list[dict[int, int]] = [{} for _ in range(edge_count)]
        for col, edges in enumerate(triangles):
            for edge in edges:
                rows[edge][col] = 1

        rank = 0
        column_count = len(triangles)
        for col in range(column_count):
            if rank >= edge_count:
                break
            pivot = None
            for r in range(rank, edge_count):
                if rows[r].get(col, 0) == 1:
                    pivot = r
                    break
            if pivot is None:
                continue

            rows[rank], rows[pivot] = rows[pivot], rows[rank]
            for r in range(edge_count):
                if r == rank or rows[r].get(col, 0) != 1:
                    continue
                for k, v in rows[rank].items():
                    val = rows[r].get(k, 0) ^ v
                    if val == 0:
                        rows[r].pop(k, None)
                    else:
                        rows[r][k] = val
            rank += 1

        return rank

    def _distance(self, left: list[float], right: list[float]) -> float:
        dot = 0.0
        left_norm = 0.0
        right_norm = 0.0
        for idx, val in enumerate(left):
            dot += val * right[idx]
            left_norm += val * val
            right_norm += right[idx] * right[idx]

        if left_norm <= 0.0 or right_norm <= 0.0:
            raise RuntimeError("aporia_ontology_vector_zero")
        cos_val = dot / math.sqrt(left_norm * right_norm)
        cos_clamped = max(-1.0, min(1.0, cos_val))
        return 1.0 - cos_clamped

    def _vectors(self, vectors: list[Any]) -> list[list[float]]:
        if not vectors or len(vectors) > 128:
            raise RuntimeError("aporia_ontology_vectors_invalid")
        dimension = None
        result = []
        for vec in vectors:
            if not isinstance(vec, (list, tuple)) or len(vec) == 0 or len(vec) > 64:
                raise RuntimeError("aporia_ontology_vectors_invalid")
            clean_vec = []
            for v in vec:
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise RuntimeError("aporia_ontology_vectors_invalid")
                fv = float(v)
                if not math.isfinite(fv):
                    raise RuntimeError("aporia_ontology_vectors_invalid")
                clean_vec.append(fv)
            if dimension is None:
                dimension = len(clean_vec)
            if len(clean_vec) != dimension:
                raise RuntimeError("aporia_ontology_vector_dimension_mismatch")
            result.append(clean_vec)
        return result

    def _epsilons(self, epsilons: list[Any]) -> list[float]:
        if not epsilons or len(epsilons) > 256:
            raise RuntimeError("aporia_ontology_epsilons_invalid")
        values = []
        for eps in epsilons:
            if not isinstance(eps, (int, float)) or isinstance(eps, bool):
                raise RuntimeError("aporia_ontology_epsilons_invalid")
            fe = float(eps)
            if not math.isfinite(fe) or fe < 0.0 or fe > 2.0:
                raise RuntimeError("aporia_ontology_epsilons_invalid")
            values.append(fe)
        unique = sorted(list(set(values)))
        return unique

    def _find(self, parents: list[int], vertex: int) -> int:
        while parents[vertex] != vertex:
            parents[vertex] = parents[parents[vertex]]
            vertex = parents[vertex]
        return vertex

    def _union(self, parents: list[int], left: int, right: int) -> None:
        left_root = self._find(parents, left)
        right_root = self._find(parents, right)
        if left_root != right_root:
            parents[right_root] = left_root


class AporiaOntologyExperiment:
    INADEQUACY_THRESHOLD = 0.25
    MDL_THRESHOLD = 0.05
    HOLDOUT_THRESHOLD = 0.02
    DUPLICATE_THRESHOLD = 0.98
    BOOTSTRAP_SAMPLES = 2000
    BOOTSTRAP_SEED = 1280262991

    def evaluate(self, proposal: dict[str, Any]) -> dict[str, Any]:
        required = [
            "definition_commitment",
            "positive_example_commitments",
            "negative_example_commitments",
            "candidate_vector",
            "existing_vectors",
            "training_before",
            "training_after",
            "holdout_before",
            "holdout_after",
            "complexity_before",
            "complexity_after",
            "lambda",
        ]
        keys = sorted(list(proposal.keys()))
        if keys != sorted(required) or not re.match(r"^[0-9a-f]{64}$", str(proposal.get("definition_commitment", ""))):
            raise RuntimeError("aporia_ontology_proposal_invalid")

        pos_examples = self._commitments(proposal["positive_example_commitments"])
        neg_examples = self._commitments(proposal["negative_example_commitments"])
        candidate = self._vector(proposal["candidate_vector"])
        if not isinstance(proposal.get("existing_vectors"), list):
            raise RuntimeError("aporia_ontology_proposal_invalid")
        existing = [self._vector(v) for v in proposal["existing_vectors"]]

        tr_before = self._losses(proposal["training_before"])
        tr_after = self._losses(proposal["training_after"])
        ho_before = self._losses(proposal["holdout_before"])
        ho_after = self._losses(proposal["holdout_after"])
        self._assert_paired(tr_before, tr_after)
        self._assert_paired(ho_before, ho_after)

        comp_before = self._non_negative(proposal["complexity_before"])
        comp_after = self._non_negative(proposal["complexity_after"])
        lam = self._non_negative(proposal["lambda"])

        max_sim = 0.0
        for vec in existing:
            if len(vec) != len(candidate):
                raise RuntimeError("aporia_ontology_vector_dimension_mismatch")
            max_sim = max(max_sim, self.cosine_similarity(candidate, vec))

        mdl_before = self._mean(tr_before) + lam * comp_before
        mdl_after = self._mean(tr_after) + lam * comp_after
        mdl_improvement = mdl_before - mdl_after

        ho_improvements = [ho_before[i] - ho_after[i] for i in range(len(ho_before))]
        ho_improvement = self._mean(ho_improvements)
        bootstrap_lower = self._bootstrap_lower(ho_improvements)

        inadequacy_detected = len(tr_before) >= 3 and self._mean(tr_before) >= self.INADEQUACY_THRESHOLD
        duplicate = max_sim >= self.DUPLICATE_THRESHOLD

        state = "proposed"
        reason_codes = ["out_of_sample_improvement_pending"]
        if not inadequacy_detected:
            state = "rejected"
            reason_codes = ["ontology_inadequacy_not_detected"]
        elif duplicate:
            state = "rejected"
            reason_codes = ["ontology_duplicate_detected"]
        elif (
            mdl_improvement > self.MDL_THRESHOLD
            and ho_improvement > self.HOLDOUT_THRESHOLD
            and bootstrap_lower > 0.0
        ):
            state = "shadow"
            reason_codes = ["mdl_improved", "holdout_improved", "bootstrap_lower_positive", "activation_forbidden"]

        return {
            "state": state,
            "inadequacy_detected": inadequacy_detected,
            "duplicate_detected": duplicate,
            "maximum_cosine_similarity": max_sim,
            "mdl_before": mdl_before,
            "mdl_after": mdl_after,
            "mdl_improvement": mdl_improvement,
            "holdout_improvement": ho_improvement,
            "bootstrap_lower_95": bootstrap_lower,
            "promotion_allowed": state == "shadow",
            "automatic_activation_allowed": False,
            "reason_codes": reason_codes,
            "candidate_vector": candidate,
            "definition_commitment": str(proposal["definition_commitment"]).lower(),
            "positive_example_commitments": pos_examples,
            "negative_example_commitments": neg_examples,
            "bootstrap_samples": self.BOOTSTRAP_SAMPLES,
            "bootstrap_seed": self.BOOTSTRAP_SEED,
            "version": 1,
        }

    def cosine_similarity(self, left: list[float], right: list[float]) -> float:
        l_vec = self._vector(left)
        r_vec = self._vector(right)
        if len(l_vec) != len(r_vec):
            raise RuntimeError("aporia_ontology_vector_dimension_mismatch")
        dot = 0.0
        l_norm = 0.0
        r_norm = 0.0
        for idx, val in enumerate(l_vec):
            dot += val * r_vec[idx]
            l_norm += val * val
            r_norm += r_vec[idx] * r_vec[idx]
        if l_norm <= 0.0 or r_norm <= 0.0:
            raise RuntimeError("aporia_ontology_vector_zero")
        cos_val = dot / math.sqrt(l_norm * r_norm)
        return max(-1.0, min(1.0, cos_val))

    def _bootstrap_lower(self, improvements: list[float]) -> float:
        count = len(improvements)
        state = self.BOOTSTRAP_SEED
        means = []
        for _ in range(self.BOOTSTRAP_SAMPLES):
            s = 0.0
            for _ in range(count):
                state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
                s += improvements[state % count]
            means.append(s / count)
        means.sort()
        idx = int(math.floor(0.025 * (self.BOOTSTRAP_SAMPLES - 1)))
        return means[idx]

    def _vector(self, values: Any) -> list[float]:
        if not isinstance(values, (list, tuple)) or len(values) == 0 or len(values) > 64:
            raise RuntimeError("aporia_ontology_vector_invalid")
        result = []
        for val in values:
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise RuntimeError("aporia_ontology_vector_invalid")
            fv = float(val)
            if not math.isfinite(fv):
                raise RuntimeError("aporia_ontology_vector_invalid")
            result.append(fv)
        return result

    def _commitments(self, values: Any) -> list[str]:
        if not isinstance(values, (list, tuple)) or len(values) == 0 or len(values) > 256:
            raise RuntimeError("aporia_ontology_examples_invalid")
        res = []
        for val in values:
            if not isinstance(val, str) or not re.match(r"^[0-9a-f]{64}$", val, re.IGNORECASE):
                raise RuntimeError("aporia_ontology_examples_invalid")
            res.append(val.lower())
        return sorted(list(set(res)))

    def _losses(self, values: Any) -> list[float]:
        if not isinstance(values, (list, tuple)) or len(values) == 0 or len(values) > 4096:
            raise RuntimeError("aporia_ontology_losses_invalid")
        return [self._non_negative(v) for v in values]

    def _non_negative(self, value: Any) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RuntimeError("aporia_ontology_numeric_invalid")
        fv = float(value)
        if not math.isfinite(fv) or fv < 0.0:
            raise RuntimeError("aporia_ontology_numeric_invalid")
        return fv

    def _assert_paired(self, before: list[float], after: list[float]) -> None:
        if len(before) != len(after):
            raise RuntimeError("aporia_ontology_holdout_unpaired")

    def _mean(self, values: list[float]) -> float:
        return sum(values) / len(values)


class AporiaOntologyLedger:
    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_ontology_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def propose(self, tenant_id: int, proposal: dict[str, Any], epsilons: list[float], created_at: str) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_ontology_transaction_required")
        if tenant_id < 1 or not created_at:
            raise RuntimeError("aporia_ontology_scope_invalid")

        self._ensure_head(tenant_id, created_at)
        head_stmt = self.conn.prepare(
            "SELECT current_version_id, version_count FROM aporia_ontology_heads WHERE tenant_id = ? LIMIT 1"
        )
        head_stmt.execute([tenant_id])
        head = head_stmt.fetch()
        if not head:
            raise RuntimeError("aporia_ontology_head_unavailable")

        vectors = self._active_vectors(tenant_id)
        proposal_copy = dict(proposal)
        proposal_copy["existing_vectors"] = vectors
        evaluation = AporiaOntologyExperiment().evaluate(proposal_copy)
        version_number = int(head["version_count"]) + 1

        v_hash = hashlib.sha256(f"{tenant_id}|{version_number}|{evaluation['definition_commitment']}|ontology-v1".encode("utf-8")).hexdigest()
        version_id = self._uuid(v_hash)
        p_hash = hashlib.sha256(f"{tenant_id}|{version_id}|{evaluation['definition_commitment']}|primitive-v1".encode("utf-8")).hexdigest()
        primitive_id = self._uuid(p_hash)

        existing = self.conn.prepare(
            "SELECT primitive_id, state FROM aporia_ontology_primitives WHERE tenant_id = ? AND primitive_id = ? LIMIT 1"
        )
        existing.execute([tenant_id, primitive_id])
        row = existing.fetch()
        if row:
            return {
                "primitive_id": str(row["primitive_id"]),
                "state": str(row["state"]),
                "deduplicated": True,
            }

        topology = AporiaOntologyTopology()
        topology_before = None if not vectors else topology.filtration(vectors, epsilons)
        topology_after = topology.filtration(vectors + [evaluation["candidate_vector"]], epsilons)
        topology_change = None if topology_before is None else topology.compare(topology_before, topology_after)

        parent_version_id = str(head["current_version_id"]) if head["current_version_id"] is not None else None
        version_commitment = hashlib.sha256(
            self._json({
                "tenant_id": tenant_id,
                "version_number": version_number,
                "parent_version_id": parent_version_id,
                "primitive_id": primitive_id,
                "definition_commitment": evaluation["definition_commitment"],
                "positive_example_commitments": evaluation["positive_example_commitments"],
                "negative_example_commitments": evaluation["negative_example_commitments"],
                "state": evaluation["state"],
                "mdl_improvement": evaluation["mdl_improvement"],
                "holdout_improvement": evaluation["holdout_improvement"],
                "bootstrap_lower_95": evaluation["bootstrap_lower_95"],
                "topology_change": topology_change["betti_curve_l1"] if topology_change else None,
            }).encode("utf-8")
        ).hexdigest()

        version_insert = self.conn.prepare(
            """INSERT INTO aporia_ontology_versions
             (version_id, tenant_id, version_number, parent_version_id, status, version_commitment,
              schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)"""
        )
        version_insert.execute([
            version_id,
            tenant_id,
            version_number,
            parent_version_id,
            evaluation["state"],
            version_commitment,
            created_at,
            created_at,
        ])

        reason_code = str(evaluation["reason_codes"][0])
        op_id = hashlib.sha256(f"{tenant_id}|{primitive_id}|ontology-proposal-v1".encode("utf-8")).hexdigest()

        primitive_insert = self.conn.prepare(
            """INSERT INTO aporia_ontology_primitives
             (primitive_id, tenant_id, version_id, definition_commitment, positive_example_commitments_json,
              negative_example_commitments_json, vector_json, state,
              duplicate_similarity, mdl_before, mdl_after, mdl_improvement, holdout_improvement,
              bootstrap_lower_95, topology_before_json, topology_after_json, topology_change,
              automatic_activation_allowed, reason_codes_json, event_name, event_version,
              environment, stream, category, component, operation_id, actor_type, action_name,
              lifecycle_phase, outcome, reason_code, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?,
                     'aporia.ontology.primitive.evaluated', 1, ?, 'system', 'audit',
                     'aporia-ontology-ledger', ?, 'worker', 'evaluate_primitive_proposal',
                     'succeeded', 'succeeded', ?, 1, ?, ?)"""
        )
        primitive_insert.execute([
            primitive_id,
            tenant_id,
            version_id,
            evaluation["definition_commitment"],
            self._json(evaluation["positive_example_commitments"]),
            self._json(evaluation["negative_example_commitments"]),
            self._json(evaluation["candidate_vector"]),
            evaluation["state"],
            f"{evaluation['maximum_cosine_similarity']:.12f}",
            f"{evaluation['mdl_before']:.12f}",
            f"{evaluation['mdl_after']:.12f}",
            f"{evaluation['mdl_improvement']:.12f}",
            f"{evaluation['holdout_improvement']:.12f}",
            f"{evaluation['bootstrap_lower_95']:.12f}",
            None if topology_before is None else self._json(topology_before),
            self._json(topology_after),
            None if topology_change is None else f"{topology_change['betti_curve_l1']:.12f}",
            self._json(evaluation["reason_codes"]),
            self._environment(),
            op_id,
            reason_code,
            created_at,
            created_at,
        ])

        head_update = self.conn.prepare(
            """UPDATE aporia_ontology_heads
             SET current_version_id = ?, version_count = ?, updated_at = ?
             WHERE tenant_id = ? AND version_count = ?"""
        )
        new_curr_v = version_id if evaluation["state"] == "shadow" else parent_version_id
        head_update.execute([
            new_curr_v,
            version_number,
            created_at,
            tenant_id,
            head["version_count"],
        ])
        if head_update.row_count != 1:
            raise RuntimeError("aporia_ontology_head_conflict")

        return {
            "primitive_id": primitive_id,
            "version_id": version_id,
            "version_number": version_number,
            "state": evaluation["state"],
            "promotion_allowed": evaluation["promotion_allowed"],
            "automatic_activation_allowed": False,
            "mdl_improvement": evaluation["mdl_improvement"],
            "holdout_improvement": evaluation["holdout_improvement"],
            "bootstrap_lower_95": evaluation["bootstrap_lower_95"],
            "topology_change": topology_change["betti_curve_l1"] if topology_change else None,
            "deduplicated": False,
        }

    def rollback(self, tenant_id: int, created_at: str) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_ontology_transaction_required")

        head_stmt = self.conn.prepare(
            "SELECT current_version_id, version_count FROM aporia_ontology_heads WHERE tenant_id = ? LIMIT 1"
        )
        head_stmt.execute([tenant_id])
        head = head_stmt.fetch()
        if not head or head["current_version_id"] is None:
            raise RuntimeError("aporia_ontology_rollback_unavailable")

        v_stmt = self.conn.prepare(
            "SELECT version_id, parent_version_id, status FROM aporia_ontology_versions WHERE tenant_id = ? AND version_id = ? LIMIT 1"
        )
        v_stmt.execute([tenant_id, head["current_version_id"]])
        version = v_stmt.fetch()
        if not version or str(version["status"]) != "shadow":
            raise RuntimeError("aporia_ontology_rollback_unavailable")

        update = self.conn.prepare(
            "UPDATE aporia_ontology_versions SET status = 'rolled_back', updated_at = ? WHERE tenant_id = ? AND version_id = ? AND status = 'shadow'"
        )
        update.execute([created_at, tenant_id, version["version_id"]])
        if update.row_count != 1:
            raise RuntimeError("aporia_ontology_rollback_conflict")

        head_update = self.conn.prepare(
            "UPDATE aporia_ontology_heads SET current_version_id = ?, updated_at = ? WHERE tenant_id = ? AND current_version_id = ?"
        )
        head_update.execute([
            version["parent_version_id"],
            created_at,
            tenant_id,
            version["version_id"],
        ])
        if head_update.row_count != 1:
            raise RuntimeError("aporia_ontology_rollback_conflict")

        ev_hash = hashlib.sha256(f"{tenant_id}|{version['version_id']}|rollback-v1".encode("utf-8")).hexdigest()
        event_id = self._uuid(ev_hash)
        op_id = hashlib.sha256(f"{tenant_id}|{event_id}|ontology-rollback".encode("utf-8")).hexdigest()

        event = self.conn.prepare(
            """INSERT INTO aporia_ontology_events
             (ontology_event_id, tenant_id, version_id, target_version_id, event_name, event_version,
              environment, stream, category, component, operation_id, actor_type, action_name,
              lifecycle_phase, outcome, reason_code, schema_version, created_at, updated_at)
             VALUES (?, ?, ?, ?, 'aporia.ontology.version.rollback.succeeded', 1, ?, 'system', 'audit',
                     'aporia-ontology-ledger', ?, 'worker', 'rollback_ontology_version', 'succeeded',
                     'succeeded', 'shadow_version_reverted', 1, ?, ?)"""
        )
        event.execute([
            event_id,
            tenant_id,
            version["version_id"],
            version["parent_version_id"],
            self._environment(),
            op_id,
            created_at,
            created_at,
        ])

        return {
            "rolled_back_version_id": str(version["version_id"]),
            "restored_version_id": None if version["parent_version_id"] is None else str(version["parent_version_id"]),
            "event_id": event_id,
        }

    def current_version(self, tenant_id: int) -> str | None:
        stmt = self.conn.prepare("SELECT current_version_id FROM aporia_ontology_heads WHERE tenant_id = ? LIMIT 1")
        stmt.execute([tenant_id])
        val = stmt.fetch_column()
        return str(val) if val not in (None, False, "") else None

    currentVersion = current_version

    def _active_vectors(self, tenant_id: int) -> list[list[float]]:
        stmt = self.conn.prepare(
            """SELECT primitive.vector_json
             FROM aporia_ontology_primitives primitive
             INNER JOIN aporia_ontology_versions version
               ON version.tenant_id = primitive.tenant_id AND version.version_id = primitive.version_id
             WHERE primitive.tenant_id = ? AND primitive.state IN ('shadow','validated','active')
               AND version.status <> 'rolled_back'
             ORDER BY primitive.id"""
        )
        stmt.execute([tenant_id])
        vectors = []
        for json_val in stmt.fetch_all() or []:
            vec = json.loads(str(json_val["vector_json"]))
            if not isinstance(vec, list):
                raise RuntimeError("aporia_ontology_vector_invalid")
            vectors.append([float(x) for x in vec])
        return vectors

    def _ensure_head(self, tenant_id: int, created_at: str) -> None:
        stmt = self.conn.prepare(
            "INSERT OR IGNORE INTO aporia_ontology_heads (tenant_id, current_version_id, version_count, created_at, updated_at) VALUES (?, NULL, 0, ?, ?)"
        )
        stmt.execute([tenant_id, created_at, created_at])

    def _environment(self) -> str:
        env = os.environ.get("APP_ENV", "production").strip().lower()
        return env if env in ("production", "staging", "development", "test") else "production"

    def _uuid(self, hex_hash: str) -> str:
        hex_val = list(hex_hash.lower()[:32])
        hex_val[12] = "5"
        hex_val[16] = hex((int(hex_val[16], 16) & 0x3) | 0x8)[2:]
        h = "".join(hex_val)
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class AporiaOntologyResidualCollector:
    MIN_TRAIN_EPISODES = 8
    MIN_HOLDOUT_EPISODES = 4
    MIN_NEGATIVE_EXAMPLES = 4
    FEATURE_DIMENSIONS = 12
    EPSILONS = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0]

    def __init__(self, conn: Connection, secret: str):
        if not secret or not secret.strip():
            raise RuntimeError("aporia_ontology_residual_secret_required")
        self.conn = conn
        self.secret = secret.strip()

    def record_and_propose(
        self,
        source: dict[str, Any],
        run_row_id: int,
        run_id: str,
        feature_vector: list[float],
        input_commitment: str,
        created_at: str,
    ) -> dict[str, Any]:
        if not self.conn.in_transaction():
            raise RuntimeError("aporia_ontology_residual_transaction_required")

        tenant_id = int(source.get("tenant_id", 0))
        session_ref = str(source.get("session_ref", ""))
        event_kind = str(source.get("event_kind", ""))

        if tenant_id < 1 or run_row_id < 1 or not session_ref or not event_kind or not created_at:
            raise RuntimeError("aporia_ontology_residual_scope_invalid")
        if not re.match(r"^[0-9a-f-]{36}$", run_id, re.IGNORECASE) or not re.match(r"^[0-9a-f]{64}$", input_commitment):
            raise RuntimeError("aporia_ontology_residual_scope_invalid")

        vector = self._feature_vector(feature_vector)
        residual = self._residual(tenant_id, vector)
        episode_ref = hmac.new(
            self.secret.encode("utf-8"),
            f"{tenant_id}|episode|{session_ref}|v1".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        pred_kind = self._predecessor_kind(tenant_id, int(source["id"]))
        cluster_key = hmac.new(
            self.secret.encode("utf-8"),
            f"{tenant_id}|residual-cluster|{event_kind}|{pred_kind}|v1".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        split_hash = hashlib.sha256(f"{tenant_id}|{episode_ref}|ontology-split-v1".encode("utf-8")).hexdigest()
        split = "holdout" if int(split_hash[:2], 16) % 4 == 0 else "train"

        r_hash = hashlib.sha256(f"{tenant_id}|{run_id}|ontology-residual-v1".encode("utf-8")).hexdigest()
        residual_id = self._uuid(r_hash)

        residual_commitment = hashlib.sha256(
            self._json({
                "tenant_id": tenant_id,
                "run_id": run_id,
                "input_commitment": input_commitment,
                "vector": residual["vector"],
                "split": split,
                "version": 1,
            }).encode("utf-8")
        ).hexdigest()

        existing = self.conn.prepare("SELECT residual_id FROM aporia_ontology_residuals WHERE tenant_id = ? AND run_id = ? LIMIT 1")
        existing.execute([tenant_id, run_row_id])
        existing_id = existing.fetch_column()
        if existing_id not in (None, False, ""):
            return {"residual_id": str(existing_id), "deduplicated": True, "proposal": None}

        self._ensure_cluster(tenant_id, cluster_key, created_at)
        op_id = hashlib.sha256(f"{tenant_id}|{residual_id}|ontology-residual-v1".encode("utf-8")).hexdigest()

        insert = self.conn.prepare(
            """INSERT INTO aporia_ontology_residuals
             (residual_id, tenant_id, run_id, episode_ref, cluster_key, dataset_split,
              residual_vector_json, residual_commitment, baseline_loss, nearest_similarity,
              event_name, event_version, environment, stream, category, component, operation_id,
              actor_type, action_name, lifecycle_phase, outcome, reason_code, schema_version,
              created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'aporia.ontology.residual.observed', 1, ?,
                     'system', 'audit', 'aporia-ontology-residual-collector', ?, 'worker',
                     'record_ontology_residual', 'succeeded', 'succeeded', ?, 1, ?, ?)"""
        )
        insert.execute([
            residual_id,
            tenant_id,
            run_row_id,
            episode_ref,
            cluster_key,
            split,
            self._json(residual["vector"]),
            residual_commitment,
            f"{residual['loss']:.12f}",
            f"{residual['nearest_similarity']:.12f}",
            self._environment(),
            op_id,
            "ontology_inadequacy_candidate_observed" if residual["loss"] >= 0.25 else "ontology_representation_adequate",
            created_at,
            created_at,
        ])

        cluster = self._lock_cluster(tenant_id, cluster_key)
        counts = self._cluster_counts(tenant_id, cluster_key)
        self._update_cluster_counts(tenant_id, cluster_key, counts, created_at)

        if (
            cluster["status"] not in ("collecting", "proposed")
            or counts["train"] < self.MIN_TRAIN_EPISODES
            or counts["holdout"] < self.MIN_HOLDOUT_EPISODES
            or counts["episodes"] < (self.MIN_TRAIN_EPISODES + self.MIN_HOLDOUT_EPISODES)
            or (
                counts["train"] <= int(cluster["last_evaluated_train_count"])
                and counts["holdout"] <= int(cluster["last_evaluated_holdout_count"])
            )
        ):
            return {"residual_id": residual_id, "deduplicated": False, "proposal": None}

        prop_data = self._proposal(tenant_id, cluster_key)
        if prop_data is None:
            return {"residual_id": residual_id, "deduplicated": False, "proposal": None}

        result = AporiaOntologyLedger(self.conn, self.secret).propose(
            tenant_id,
            prop_data,
            self.EPSILONS,
            created_at,
        )

        cluster_status = "proposed" if result["state"] == "proposed" else str(result["state"])
        upd = self.conn.prepare(
            """UPDATE aporia_ontology_clusters
             SET status = ?, proposed_version_id = ?, last_evaluated_train_count = ?,
                 last_evaluated_holdout_count = ?, updated_at = ?
             WHERE tenant_id = ? AND cluster_key = ?"""
        )
        upd.execute([
            cluster_status,
            result["version_id"],
            counts["train"],
            counts["holdout"],
            created_at,
            tenant_id,
            cluster_key,
        ])

        return {"residual_id": residual_id, "deduplicated": False, "proposal": result}

    recordAndPropose = record_and_propose

    def _proposal(self, tenant_id: int, cluster_key: str) -> dict[str, Any] | None:
        pos_stmt = self.conn.prepare(
            """SELECT episode_ref, dataset_split, residual_vector_json, residual_commitment, baseline_loss
             FROM aporia_ontology_residuals WHERE tenant_id = ? AND cluster_key = ? ORDER BY id"""
        )
        pos_stmt.execute([tenant_id, cluster_key])
        rows = pos_stmt.fetch_all() or []

        training_vectors = []
        training_before = []
        holdout_vectors = []
        holdout_before = []
        positive_commitments = []
        seen_episodes = set()

        for row in rows:
            ep_ref = str(row["episode_ref"])
            if ep_ref in seen_episodes:
                continue
            seen_episodes.add(ep_ref)
            vec = self._decoded_vector(str(row["residual_vector_json"]))
            positive_commitments.append(str(row["residual_commitment"]))
            if str(row["dataset_split"]) == "holdout":
                holdout_vectors.append(vec)
                holdout_before.append(float(row["baseline_loss"]))
            else:
                training_vectors.append(vec)
                training_before.append(float(row["baseline_loss"]))

        neg_stmt = self.conn.prepare(
            """SELECT episode_ref, residual_commitment FROM aporia_ontology_residuals
             WHERE tenant_id = ? AND cluster_key <> ? ORDER BY id LIMIT 64"""
        )
        neg_stmt.execute([tenant_id, cluster_key])
        negative_commitments = []
        neg_episodes = set()
        for row in neg_stmt.fetch_all() or []:
            ep_ref = str(row["episode_ref"])
            if ep_ref in neg_episodes:
                continue
            neg_episodes.add(ep_ref)
            negative_commitments.append(str(row["residual_commitment"]))

        if (
            len(training_vectors) < self.MIN_TRAIN_EPISODES
            or len(holdout_vectors) < self.MIN_HOLDOUT_EPISODES
            or len(negative_commitments) < self.MIN_NEGATIVE_EXAMPLES
        ):
            return None

        candidate = self._centroid(training_vectors)
        training_after = [self._unexplained_loss(v, candidate) for v in training_vectors]
        holdout_after = [self._unexplained_loss(v, candidate) for v in holdout_vectors]
        complexity_before = self._ontology_complexity(tenant_id)

        def_comm = hashlib.sha256(
            self._json({
                "tenant_id": tenant_id,
                "cluster_key": cluster_key,
                "feature_space": "lacuna-logical-v2",
                "residual_dimensions": self.FEATURE_DIMENSIONS,
                "unit_normalized": True,
                "independent_episode_split": True,
                "version": 1,
            }).encode("utf-8")
        ).hexdigest()

        uniq_pos = list(dict.fromkeys(positive_commitments))[:64]
        uniq_neg = list(dict.fromkeys(negative_commitments))[:64]

        return {
            "definition_commitment": def_comm,
            "positive_example_commitments": uniq_pos,
            "negative_example_commitments": uniq_neg,
            "candidate_vector": candidate,
            "existing_vectors": self._existing_vectors(tenant_id),
            "training_before": training_before,
            "training_after": training_after,
            "holdout_before": holdout_before,
            "holdout_after": holdout_after,
            "complexity_before": float(complexity_before),
            "complexity_after": float(complexity_before + 1),
            "lambda": 0.01,
        }

    def _residual(self, tenant_id: int, vector: list[float]) -> dict[str, Any]:
        best_similarity = 0.0
        best_vector = None
        stmt = self.conn.prepare(
            """SELECT primitive.vector_json
             FROM aporia_ontology_primitives primitive
             INNER JOIN aporia_ontology_versions version
               ON version.tenant_id = primitive.tenant_id AND version.version_id = primitive.version_id
             WHERE primitive.tenant_id = ? AND primitive.state IN ('shadow','validated','active')
               AND version.status <> 'rolled_back' ORDER BY primitive.id"""
        )
        stmt.execute([tenant_id])
        for row in stmt.fetch_all() or []:
            cand = self._decoded_vector(str(row["vector_json"]))
            if len(cand) != len(vector):
                continue
            sim = self._dot(vector, cand)
            if sim > best_similarity:
                best_similarity = sim
                best_vector = cand

        residual = list(vector)
        if best_vector is not None and best_similarity > 0.0:
            for idx in range(len(residual)):
                residual[idx] = residual[idx] - best_similarity * best_vector[idx]

        return {
            "vector": residual,
            "loss": self._norm(residual),
            "nearest_similarity": best_similarity,
        }

    def _feature_vector(self, vector: list[float]) -> list[float]:
        if len(vector) < self.FEATURE_DIMENSIONS:
            raise RuntimeError("aporia_ontology_feature_vector_invalid")
        values = []
        for idx in range(self.FEATURE_DIMENSIONS):
            val = vector[idx]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise RuntimeError("aporia_ontology_feature_vector_invalid")
            fv = float(val)
            if not math.isfinite(fv):
                raise RuntimeError("aporia_ontology_feature_vector_invalid")
            scale = 15.0 if idx in (1, 2) else 1.0
            values.append(fv / scale)
        return self._unit(values)

    def _centroid(self, vectors: list[list[float]]) -> list[float]:
        c = [0.0] * self.FEATURE_DIMENSIONS
        for vec in vectors:
            for idx, val in enumerate(vec):
                c[idx] += val
        for idx in range(self.FEATURE_DIMENSIONS):
            c[idx] /= len(vectors)
        return self._unit(c)

    def _unexplained_loss(self, vector: list[float], candidate: list[float]) -> float:
        coeff = max(0.0, self._dot(vector, candidate))
        res = [vector[i] - coeff * candidate[i] for i in range(len(vector))]
        return self._norm(res)

    def _unit(self, vector: list[float]) -> list[float]:
        n = self._norm(vector)
        if n <= 1e-12:
            raise RuntimeError("aporia_ontology_feature_vector_zero")
        return [v / n for v in vector]

    def _decoded_vector(self, json_str: str) -> list[float]:
        val = json.loads(json_str)
        if not isinstance(val, list) or len(val) != self.FEATURE_DIMENSIONS:
            raise RuntimeError("aporia_ontology_residual_vector_invalid")
        return [float(v) for v in val]

    def _dot(self, left: list[float], right: list[float]) -> float:
        val = sum(left[i] * right[i] for i in range(len(left)))
        return max(-1.0, min(1.0, val))

    def _norm(self, vector: list[float]) -> float:
        return math.sqrt(sum(v * v for v in vector))

    def _predecessor_kind(self, tenant_id: int, event_row_id: int) -> str:
        stmt = self.conn.prepare(
            """SELECT parent.event_kind FROM aporia_event_parents edge
             INNER JOIN aporia_events parent
               ON parent.tenant_id = edge.tenant_id AND parent.id = edge.parent_event_id
             WHERE edge.tenant_id = ? AND edge.child_event_id = ?
               AND edge.relation_type = 'session_predecessor' LIMIT 1"""
        )
        stmt.execute([tenant_id, event_row_id])
        val = stmt.fetch_column()
        return str(val) if val not in (None, False, "") else "root"

    def _ensure_cluster(self, tenant_id: int, cluster_key: str, created_at: str) -> None:
        c_hash = hashlib.sha256(f"{tenant_id}|{cluster_key}|ontology-cluster-v1".encode("utf-8")).hexdigest()
        cluster_id = self._uuid(c_hash)
        stmt = self.conn.prepare(
            """INSERT OR IGNORE INTO aporia_ontology_clusters
               (cluster_id, tenant_id, cluster_key, status, train_count, holdout_count,
                distinct_episode_count, last_evaluated_train_count, last_evaluated_holdout_count,
                proposed_version_id, schema_version, created_at, updated_at)
               VALUES (?, ?, ?, 'collecting', 0, 0, 0, 0, 0, NULL, 1, ?, ?)"""
        )
        stmt.execute([cluster_id, tenant_id, cluster_key, created_at, created_at])

    def _lock_cluster(self, tenant_id: int, cluster_key: str) -> dict[str, Any]:
        stmt = self.conn.prepare(
            "SELECT status, last_evaluated_train_count, last_evaluated_holdout_count FROM aporia_ontology_clusters WHERE tenant_id = ? AND cluster_key = ? LIMIT 1"
        )
        stmt.execute([tenant_id, cluster_key])
        cluster = stmt.fetch()
        if not cluster:
            raise RuntimeError("aporia_ontology_cluster_unavailable")
        return cluster

    def _cluster_counts(self, tenant_id: int, cluster_key: str) -> dict[str, int]:
        stmt = self.conn.prepare(
            """SELECT COUNT(DISTINCT CASE WHEN dataset_split = 'train' THEN episode_ref END) AS train_count,
                    COUNT(DISTINCT CASE WHEN dataset_split = 'holdout' THEN episode_ref END) AS holdout_count,
                    COUNT(DISTINCT episode_ref) AS episode_count
             FROM aporia_ontology_residuals WHERE tenant_id = ? AND cluster_key = ?"""
        )
        stmt.execute([tenant_id, cluster_key])
        row = stmt.fetch() or {}
        return {
            "train": int(row.get("train_count", 0)),
            "holdout": int(row.get("holdout_count", 0)),
            "episodes": int(row.get("episode_count", 0)),
        }

    def _update_cluster_counts(self, tenant_id: int, cluster_key: str, counts: dict[str, int], created_at: str) -> None:
        stmt = self.conn.prepare(
            "UPDATE aporia_ontology_clusters SET train_count = ?, holdout_count = ?, distinct_episode_count = ?, updated_at = ? WHERE tenant_id = ? AND cluster_key = ?"
        )
        stmt.execute([
            counts["train"],
            counts["holdout"],
            counts["episodes"],
            created_at,
            tenant_id,
            cluster_key,
        ])

    def _ontology_complexity(self, tenant_id: int) -> int:
        stmt = self.conn.prepare(
            "SELECT COUNT(*) FROM aporia_ontology_versions WHERE tenant_id = ? AND status IN ('shadow','validated','active')"
        )
        stmt.execute([tenant_id])
        return int(stmt.fetch_column() or 0)

    def _existing_vectors(self, tenant_id: int) -> list[list[float]]:
        stmt = self.conn.prepare(
            """SELECT primitive.vector_json
             FROM aporia_ontology_primitives primitive
             INNER JOIN aporia_ontology_versions version
               ON version.tenant_id = primitive.tenant_id AND version.version_id = primitive.version_id
             WHERE primitive.tenant_id = ? AND primitive.state IN ('shadow','validated','active')
               AND version.status <> 'rolled_back' ORDER BY primitive.id"""
        )
        stmt.execute([tenant_id])
        vectors = []
        for row in stmt.fetch_all() or []:
            vec = self._decoded_vector(str(row["vector_json"]))
            if len(vec) == self.FEATURE_DIMENSIONS:
                vectors.append(vec)
        return vectors

    def _environment(self) -> str:
        env = os.environ.get("APP_ENV", "production").strip().lower()
        return env if env in ("production", "staging", "development", "test") else "production"

    def _uuid(self, hex_hash: str) -> str:
        hex_val = list(hex_hash.lower()[:32])
        hex_val[12] = "5"
        hex_val[16] = hex((int(hex_val[16], 16) & 0x3) | 0x8)[2:]
        h = "".join(hex_val)
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _json(self, value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
