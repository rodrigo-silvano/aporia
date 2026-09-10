#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from staging_runtime_probe import RuntimeSession


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "staging-e2e-report.json"
FLOWS = (
    ("normal", "Responde numa frase: estou a validar o funcionamento normal do Sync. Não uses ferramentas."),
    ("continuity", "Mantém o contexto desta validação e indica apenas o objetivo que acabei de referir. Não uses ferramentas."),
    ("contradiction", "Corrijo a instrução anterior: responde agora em duas frases, não numa. Reconhece a correção sem inventar dados."),
    ("uncertainty", "Sem dados adicionais, diz se sabes quantos contactos tenho hoje. Expõe a incerteza e não uses ferramentas."),
    ("negative_outcome", "Regista apenas nesta conversa que uma tentativa anterior falhou e que não deve ser repetida automaticamente."),
    ("safe_action", "Diz qual seria a próxima ação segura para preparar um email, sem o escrever, enviar ou executar qualquer ferramenta."),
)


def commitment(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    sessions = [RuntimeSession(args.tenant_id, args.origin.rstrip("/")) for _ in range(args.sessions)]
    rows: list[dict[str, Any]] = []
    try:
        opened = await asyncio.gather(*(session.open() for session in sessions), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in opened):
            raise RuntimeError("e2e_session_open_failed")
        for session_index, session in enumerate(sessions, start=1):
            for category, prompt in FLOWS:
                try:
                    measurement = await session.turn(prompt=prompt, timeout=args.turn_timeout)
                    row = {
                        "completed": measurement.completed,
                        "error": measurement.error,
                        "response_nonempty": measurement.response_chars > 0,
                        "response_commitment": measurement.response_hash,
                        "total_ms": measurement.total_ms,
                        "ttft_ms": measurement.ttft_ms,
                        "duplicate_events": measurement.duplicate_events,
                        "tool_proposals": measurement.tool_proposals,
                        "tool_started": measurement.tool_started,
                        "permission_requests": measurement.permission_requests,
                    }
                except Exception as error:
                    row = {
                        "completed": False,
                        "error": error.__class__.__name__,
                        "response_nonempty": False,
                        "response_commitment": "",
                        "total_ms": 0.0,
                        "ttft_ms": None,
                        "duplicate_events": 0,
                        "tool_proposals": 0,
                        "tool_started": 0,
                        "permission_requests": 0,
                    }
                    try:
                        await session.refresh()
                    except Exception:
                        pass
                rows.append({
                    "case_id": f"E2E-{session_index:02d}-{category}",
                    "category": category,
                    **row,
                })
    finally:
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    required_count = args.sessions * len(FLOWS)
    gates = {
        "at_least_30_messages": len(rows) >= 30 and required_count >= 30,
        "all_completed": len(rows) == required_count and all(row["completed"] for row in rows),
        "all_responses_nonempty": all(row["response_nonempty"] for row in rows),
        "no_duplicate_events": all(row["duplicate_events"] == 0 for row in rows),
        "no_tool_execution": all(row["tool_started"] == 0 for row in rows),
        "no_permission_or_external_effect_request": all(row["permission_requests"] == 0 for row in rows),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "PASS" if all(gates.values()) else "FAIL",
        "origin": args.origin,
        "tenant_id": args.tenant_id,
        "session_count": args.sessions,
        "message_count": len(rows),
        "category_counts": {category: sum(row["category"] == category for row in rows) for category, _ in FLOWS},
        "gates": gates,
        "cases": rows,
        "content_persisted": False,
        "secrets_persisted": False,
        "g8_consumed": False,
    }
    report["report_commitment"] = commitment(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="https://staging.aporia.local")
    parser.add_argument("--tenant-id", type=int, default=49)
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--turn-timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.sessions < 5 or args.sessions > 20:
        raise SystemExit("e2e_sessions_must_be_between_5_and_20")
    operator_id = os.environ.get("PLS_OPERATOR_ADMIN_ID", "").strip()
    if not operator_id.isdigit() or int(operator_id) < 1:
        raise SystemExit("PLS_OPERATOR_ADMIN_ID_must_identify_active_admin")
    report = asyncio.run(execute(args))
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report_commitment": report["report_commitment"]}, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
