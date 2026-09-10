#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from websockets.asyncio.client import connect


ROOT = Path(__file__).resolve().parents[3]
ACCESS_HELPER = ROOT / "platform/deployment/scripts/aporia_product_qualification_access.py"
DEFAULT_REPORT = Path(__file__).resolve().parent / "staging-runtime-report.json"
READ_ONLY_PROMPT = "Confirma numa frase curta que o Sync está disponível, sem usar ferramentas nem executar ações."


def percentile(values: list[float], value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(value * len(ordered)) - 1)], 3)


def access_material(tenant_id: int, session_id: str | None = None) -> dict[str, Any]:
    command = [
        "python3",
        str(ACCESS_HELPER),
        f"--base-dir={ROOT}",
        f"--tenant-id={tenant_id}",
        "--operation=mint",
    ]
    if session_id:
        command.append(f"--session-id={session_id}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr.strip() or "qualification_access_failed").splitlines()[-1][:160])
    payload = json.loads(result.stdout)
    if not all(payload.get(key) for key in ("session_id", "ticket", "csrf")):
        raise RuntimeError("qualification_access_invalid")
    return payload


def archive_session(tenant_id: int, session_id: str) -> None:
    result = subprocess.run(
        [
            "python3",
            str(ACCESS_HELPER),
            f"--base-dir={ROOT}",
            f"--tenant-id={tenant_id}",
            "--operation=archive",
            f"--session-id={session_id}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr.strip() or "qualification_archive_failed").splitlines()[-1][:160])


@dataclass
class TurnMeasurement:
    completed: bool
    error: str | None
    total_ms: float
    ttft_ms: float | None
    event_count: int
    duplicate_events: int
    response_hash: str
    response_chars: int
    tool_proposals: int
    tool_started: int
    permission_requests: int


@dataclass
class RuntimeSession:
    tenant_id: int
    origin: str
    session_id: str | None = None
    csrf: str = ""
    cookie: str = ""
    websocket: Any = None
    reconnects: int = 0
    last_sequence: int = 0
    seen_event_ids: set[str] = field(default_factory=set)

    async def open(self) -> None:
        material = await asyncio.to_thread(access_material, self.tenant_id, self.session_id)
        self.session_id = str(material["session_id"])
        self.csrf = str(material["csrf"])
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                f"{self.origin}/assistant-runtime/auth/exchange",
                headers={"Origin": self.origin, "X-CSRF": self.csrf},
                json={"ticket": material["ticket"]},
            )
            response.raise_for_status()
            cookies = [f"{cookie.name}={cookie.value}" for cookie in response.cookies.jar]
        if len(cookies) != 1:
            raise RuntimeError("qualification_runtime_cookie_invalid")
        self.cookie = cookies[0]
        parsed = urlparse(self.origin)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        url = f"{scheme}://{parsed.netloc}/assistant-runtime/ws/session/{self.session_id}?after_sequence={self.last_sequence}"
        self.websocket = await connect(
            url,
            origin=self.origin,
            additional_headers={"Cookie": self.cookie},
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,
        )
        self.reconnects += 1

    async def refresh(self) -> None:
        if self.websocket is not None:
            await self.websocket.close()
        await self.open()

    async def turn(self, prompt: str = READ_ONLY_PROMPT, timeout: float = 180.0) -> TurnMeasurement:
        if self.websocket is None:
            raise RuntimeError("qualification_runtime_not_connected")
        turn_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        await self.websocket.send(json.dumps({
            "type": "prepare_turn",
            "turn_id": turn_id,
            "request_id": request_id,
            "csrf": self.csrf,
        }))
        await asyncio.sleep(0.1)
        started = time.perf_counter()
        await self.websocket.send(json.dumps({
            "type": "user_message",
            "text": prompt,
            "attachments": [],
            "reasoning_effort": "auto",
            "turn_id": turn_id,
            "request_id": request_id,
            "client_t0_ms": round(time.time() * 1000),
            "csrf": self.csrf,
        }))
        first_output: float | None = None
        response_parts: list[str] = []
        event_count = 0
        duplicates = 0
        tool_proposals = 0
        tool_started = 0
        permission_requests = 0
        error: str | None = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(self.websocket.recv(), timeout=max(0.1, deadline - time.monotonic()))
            event = json.loads(raw)
            sequence = event.get("sequence")
            if isinstance(sequence, int) and sequence > self.last_sequence:
                self.last_sequence = sequence
            event_id = str(event.get("event_id") or "")
            if event_id:
                if event_id in self.seen_event_ids:
                    duplicates += 1
                self.seen_event_ids.add(event_id)
            if event.get("turn_id") not in {None, turn_id}:
                continue
            event_count += 1
            kind = str(event.get("type") or "")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            tool_proposals += int(kind == "tool_proposed")
            tool_started += int(kind == "tool_started")
            permission_requests += int(kind == "permission_required")
            if kind in {"assistant_delta", "assistant_message", "tool_proposed", "permission_required", "question", "plan_presented"} and first_output is None:
                first_output = time.perf_counter()
            if kind in {"assistant_delta", "assistant_message"}:
                response_parts.append(str(data.get("text") or ""))
            if kind == "error":
                error = str(data.get("error") or "runtime_error")[:120]
            if kind == "turn_done":
                completed = time.perf_counter()
                response = "".join(response_parts).strip()
                return TurnMeasurement(
                    completed=error is None,
                    error=error,
                    total_ms=round((completed - started) * 1000, 3),
                    ttft_ms=round((first_output - started) * 1000, 3) if first_output is not None else None,
                    event_count=event_count,
                    duplicate_events=duplicates,
                    response_hash=hashlib.sha256(response.encode()).hexdigest() if response else "",
                    response_chars=len(response),
                    tool_proposals=tool_proposals,
                    tool_started=tool_started,
                    permission_requests=permission_requests,
                )
        raise TimeoutError("qualification_turn_timeout")

    async def close(self, archive: bool = True) -> None:
        if self.websocket is not None:
            await self.websocket.close()
            self.websocket = None
        if archive and self.session_id:
            await asyncio.to_thread(archive_session, self.tenant_id, self.session_id)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    minimum_sessions = max(20, 2 * args.predicted_peak_sessions)
    if args.sessions < minimum_sessions:
        raise RuntimeError(f"qualification_sessions_below_gate:{minimum_sessions}")
    if args.phase == "soak" and args.duration_seconds < 7200 and not args.allow_short_local:
        raise RuntimeError("qualification_soak_below_two_hours")
    sessions = [RuntimeSession(args.tenant_id, args.origin.rstrip("/")) for _ in range(args.sessions)]
    measurements: list[TurnMeasurement] = []
    connection_errors: list[str] = []
    started = time.monotonic()
    turn_slots = asyncio.Semaphore(args.active_turns)

    async def measured_turn(session: RuntimeSession) -> TurnMeasurement:
        async with turn_slots:
            return await session.turn(timeout=args.turn_timeout)

    try:
        opened = await asyncio.gather(*(session.open() for session in sessions), return_exceptions=True)
        for result in opened:
            if isinstance(result, BaseException):
                connection_errors.append(result.__class__.__name__)
        active = [session for session, result in zip(sessions, opened) if not isinstance(result, BaseException)]
        if len(active) < minimum_sessions:
            raise RuntimeError("qualification_concurrent_session_gate_failed")
        while True:
            batch = await asyncio.gather(*(measured_turn(session) for session in active), return_exceptions=True)
            for result in batch:
                if isinstance(result, BaseException):
                    measurements.append(TurnMeasurement(False, result.__class__.__name__, 0.0, None, 0, 0, "", 0, 0, 0, 0))
                else:
                    measurements.append(result)
            if args.phase == "load" or time.monotonic() - started >= args.duration_seconds:
                break
            await asyncio.sleep(min(args.interval_seconds, max(0.0, args.duration_seconds - (time.monotonic() - started))))
            refreshed = await asyncio.gather(*(session.refresh() for session in active), return_exceptions=True)
            connection_errors.extend(result.__class__.__name__ for result in refreshed if isinstance(result, BaseException))
            active = [session for session, result in zip(active, refreshed) if not isinstance(result, BaseException)]
            if not active:
                break
    finally:
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
    elapsed = max(0.001, time.monotonic() - started)
    completed = [item for item in measurements if item.completed]
    failures = len(measurements) - len(completed)
    total_latencies = [item.total_ms for item in completed]
    ttft = [item.ttft_ms for item in completed if item.ttft_ms is not None]
    report = {
        "schema_version": 1,
        "phase": args.phase,
        "origin": args.origin,
        "tenant_id": args.tenant_id,
        "configured_sessions": args.sessions,
        "configured_active_turns": args.active_turns,
        "minimum_required_sessions": minimum_sessions,
        "duration_seconds": round(elapsed, 3),
        "turns": len(measurements),
        "completed_turns": len(completed),
        "unrecovered_errors": failures,
        "unrecovered_error_rate": round(failures / len(measurements), 6) if measurements else 1.0,
        "connection_errors": len(connection_errors),
        "reconnects": sum(session.reconnects for session in sessions),
        "lost_events": sum(1 for item in measurements if not item.completed),
        "duplicate_events": sum(item.duplicate_events for item in measurements),
        "tool_proposals": sum(item.tool_proposals for item in measurements),
        "tool_started": sum(item.tool_started for item in measurements),
        "permission_requests": sum(item.permission_requests for item in measurements),
        "throughput_turns_per_second": round(len(completed) / elapsed, 5),
        "latency_ms": {
            "p50": percentile(total_latencies, 0.50),
            "p95": percentile(total_latencies, 0.95),
            "p99": percentile(total_latencies, 0.99),
        },
        "ttft_ms": {
            "p50": percentile(ttft, 0.50),
            "p95": percentile(ttft, 0.95),
            "p99": percentile(ttft, 0.99),
        },
        "response_commitments": sorted(item.response_hash for item in completed if item.response_hash),
        "failure_classes": sorted({item.error for item in measurements if item.error}),
        "content_persisted": False,
        "secrets_persisted": False,
        "gate": {
            "concurrent_sessions": len(connection_errors) == 0,
            "unrecovered_error_rate_below_0_5_percent": bool(measurements) and failures / len(measurements) < 0.005,
            "lost_events_zero": all(item.completed for item in measurements),
            "duplicate_events_zero": all(item.duplicate_events == 0 for item in measurements),
        },
    }
    report["status"] = "PASS" if all(report["gate"].values()) else "FAIL"
    report["report_commitment"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("load", "soak"), required=True)
    parser.add_argument("--origin", default="https://staging.aporia.local")
    parser.add_argument("--tenant-id", type=int, default=49)
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--predicted-peak-sessions", type=int, default=10)
    parser.add_argument("--active-turns", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--duration-seconds", type=int, default=7200)
    parser.add_argument("--interval-seconds", type=int, default=600)
    parser.add_argument("--turn-timeout", type=float, default=180.0)
    parser.add_argument("--allow-short-local", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    operator_id = os.environ.get("PLS_OPERATOR_ADMIN_ID", "").strip()
    if not operator_id.isdigit() or int(operator_id) < 1:
        raise SystemExit("PLS_OPERATOR_ADMIN_ID_must_identify_active_admin")
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report_commitment": report["report_commitment"]}, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
