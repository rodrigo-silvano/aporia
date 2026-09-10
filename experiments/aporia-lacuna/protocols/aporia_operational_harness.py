from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


RUNTIME_ROOT = Path(__file__).parents[3] / "services" / "agent-runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from gateway.consciousness.adapter import ShadowConsciousnessAdapter
from gateway.consciousness.contracts import ConsciousDelivery
from gateway.consciousness.mirror import ConsciousnessMirror
from gateway.consciousness.sanitize import build_conscious_event
from gateway.journal import EventJournal


class MetricsLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def write(self, *args: Any, **kwargs: Any) -> None:
        with self.lock:
            self.events.append({"event": str(args[3]), "context": kwargs.get("context") or {}})

    def count(self, event: str) -> int:
        with self.lock:
            return sum(1 for row in self.events if row["event"] == event)


class TimedAdapter:
    mode = "shadow"

    def __init__(self, delegate: ShadowConsciousnessAdapter) -> None:
        self.delegate = delegate
        self.latencies_ms: list[float] = []

    async def observe_event(self, event: Any, authorization: str = "") -> None:
        started = time.monotonic()
        await self.delegate.observe_event(event, authorization)
        self.latencies_ms.append((time.monotonic() - started) * 1000)


class ReceiverHandler(BaseHTTPRequestHandler):
    database_path = ""

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        event_id = str(payload.get("event_id") or "")
        if self.path != "/aporia-events" or not event_id or not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_error(400)
            return
        with closing(sqlite3.connect(self.database_path, timeout=5)) as connection:
            existing = connection.execute("SELECT attempts FROM deliveries WHERE event_id = ?", (event_id,)).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO deliveries (event_id, attempts, received_at) VALUES (?, 1, ?)",
                    (event_id, time.time()),
                )
                deduplicated = False
            else:
                connection.execute("UPDATE deliveries SET attempts = attempts + 1 WHERE event_id = ?", (event_id,))
                deduplicated = True
            connection.commit()
        if Path(self.database_path).with_suffix(".delay").exists():
            time.sleep(0.5)
        body = json.dumps({"data": {"deduplicated": deduplicated, "projected": True}}, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(port: int, database_path: Path) -> int:
    ReceiverHandler.database_path = str(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS deliveries (event_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, received_at REAL NOT NULL)"
        )
    server = ThreadingHTTPServer(("127.0.0.1", port), ReceiverHandler)
    server.serve_forever()
    return 0


def available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start_receiver(port: int, database_path: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--receiver", str(port), "--database", str(database_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("aporia_operational_receiver_failed")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return process
        except OSError:
            time.sleep(0.02)
    process.terminate()
    process.wait(timeout=2)
    raise RuntimeError("aporia_operational_receiver_timeout")


def stop_receiver(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def receiver_metrics(database_path: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute("SELECT COUNT(*), COALESCE(SUM(attempts), 0) FROM deliveries").fetchone()
    return int(row[0]), int(row[1])


async def append_events(journal: EventJournal, tenant_id: int, prefix: str, count: int) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    for index in range(count):
        session_id = f"{prefix}-{index}"
        await journal.append(tenant_id, session_id, {
            "event_id": str(uuid.uuid4()),
            "session_id": session_id,
            "turn_id": f"turn-{index}",
            "timestamp": timestamp,
            "type": "turn_done",
            "data": {},
        })


def build_mirror(
    journal: EventJournal,
    logger: MetricsLogger,
    endpoint: str,
    tenant_id: int,
) -> tuple[ConsciousnessMirror, TimedAdapter]:
    adapter = TimedAdapter(ShadowConsciousnessAdapter(logger, endpoint))

    async def recover(limit: int) -> list[ConsciousDelivery]:
        deliveries = []
        for owner_user_id, session_id, envelope, offset in await journal.pending_aporia(limit):
            event = build_conscious_event(envelope, owner_user_id, "operational-secret", "operational-request")
            if event is not None:
                deliveries.append(ConsciousDelivery(event, "operational-token", owner_user_id, session_id, offset))
        return deliveries

    async def acknowledge(delivery: ConsciousDelivery) -> None:
        await journal.acknowledge_aporia(
            delivery.owner_user_id,
            delivery.session_id,
            delivery.event.journal_sequence,
            delivery.checkpoint_offset,
        )

    return ConsciousnessMirror(adapter, logger, 64, recover, acknowledge), adapter


async def wait_for_convergence(journal: EventJournal, database_path: Path, expected: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        unique, _ = receiver_metrics(database_path)
        pending = await journal.pending_aporia(1)
        if unique == expected and not pending:
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("aporia_operational_convergence_timeout")


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))]


async def run_load(events: int, minimum_throughput: float, maximum_p95_ms: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="aporia-operational-load-") as directory:
        root = Path(directory)
        database_path = root / "receiver.sqlite"
        port = available_port()
        receiver = start_receiver(port, database_path)
        journal = EventJournal(root / "state", "isolated")
        logger = MetricsLogger()
        mirror, adapter = build_mirror(journal, logger, f"http://127.0.0.1:{port}/aporia-events", 9000000000000000001)
        try:
            await append_events(journal, 9000000000000000001, "load", events)
            started = time.monotonic()
            await mirror.start()
            await wait_for_convergence(journal, database_path, events)
            elapsed = time.monotonic() - started
            unique, attempts = receiver_metrics(database_path)
            throughput = unique / elapsed
            p95 = percentile(adapter.latencies_ms, 0.95)
            retry_contexts = [row["context"] for row in logger.events if row["event"] == "aporia_event_delivery_retry_scheduled"]
            result = {
                "events": events,
                "unique_deliveries": unique,
                "delivery_attempts": attempts,
                "duplicates": attempts - unique,
                "backlog": len(await journal.pending_aporia(events)),
                "throughput_per_second": round(throughput, 3),
                "p95_delivery_ms": round(p95, 3),
                "observed_failure_modes": sorted({str(row.get("reason_code") or "") for row in retry_contexts}),
                "slo": {
                    "minimum_throughput_per_second": minimum_throughput,
                    "maximum_p95_delivery_ms": maximum_p95_ms,
                },
            }
            result["passed"] = (
                unique == events
                and result["duplicates"] == 0
                and result["backlog"] == 0
                and throughput >= minimum_throughput
                and p95 <= maximum_p95_ms
            )
            return result
        finally:
            await mirror.stop()
            stop_receiver(receiver)


async def run_chaos(events: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="aporia-operational-chaos-") as directory:
        root = Path(directory)
        database_path = root / "receiver.sqlite"
        port = available_port()
        receiver = start_receiver(port, database_path)
        journal = EventJournal(root / "state", "isolated")
        logger = MetricsLogger()
        tenant_id = 9000000000000000001
        mirror, _ = build_mirror(journal, logger, f"http://127.0.0.1:{port}/aporia-events", tenant_id)
        checkpoint_root = root / "state" / "isolated" / "tenants" / str(tenant_id) / ".agent_state" / "gateway-journal-aporia"
        checkpoint_backup = checkpoint_root.with_name("gateway-journal-aporia-unavailable")
        receiver_delay = database_path.with_suffix(".delay")
        checkpoint_restored = False
        try:
            await mirror.start()
            await append_events(journal, tenant_id, "warmup", 2)
            await wait_for_convergence(journal, database_path, 2)
            stop_receiver(receiver)
            receiver = None
            await append_events(journal, tenant_id, "bridge-down", events)
            deadline = time.monotonic() + 4
            while logger.count("aporia_event_delivery_retry_scheduled") < 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            bridge_backlog = len(await journal.pending_aporia(events + 2))
            receiver = start_receiver(port, database_path)
            await wait_for_convergence(journal, database_path, events + 2)
            receiver_delay.touch(mode=0o600)
            await append_events(journal, tenant_id, "checkpoint-down", events)
            expected_before_checkpoint = events + 2
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                unique, _ = receiver_metrics(database_path)
                if unique > expected_before_checkpoint:
                    break
                await asyncio.sleep(0.01)
            checkpoint_root.rename(checkpoint_backup)
            checkpoint_root.symlink_to(checkpoint_backup, target_is_directory=True)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                contexts = [row["context"] for row in logger.events if row["event"] == "aporia_event_delivery_retry_scheduled"]
                if any(row.get("reason_code") == "checkpoint_failure" for row in contexts):
                    break
                await asyncio.sleep(0.05)
            checkpoint_root.unlink()
            checkpoint_backup.rename(checkpoint_root)
            receiver_delay.unlink(missing_ok=True)
            checkpoint_restored = True
            await wait_for_convergence(journal, database_path, (events * 2) + 2)
            unique, attempts = receiver_metrics(database_path)
            contexts = [row["context"] for row in logger.events if row["event"] == "aporia_event_delivery_retry_scheduled"]
            reasons = sorted({str(row.get("reason_code") or "") for row in contexts})
            expected = (events * 2) + 2
            result = {
                "events": expected,
                "unique_deliveries": unique,
                "delivery_attempts": attempts,
                "deduplicated_attempts": attempts - unique,
                "backlog_during_bridge_outage": bridge_backlog,
                "final_backlog": len(await journal.pending_aporia(expected)),
                "observed_failure_modes": reasons,
            }
            result["passed"] = (
                unique == expected
                and result["final_backlog"] == 0
                and bridge_backlog > 0
                and "adapter_failure" in reasons
                and "checkpoint_failure" in reasons
                and attempts > unique
            )
            return result
        finally:
            if not checkpoint_restored and checkpoint_root.is_symlink():
                checkpoint_root.unlink()
                checkpoint_backup.rename(checkpoint_root)
            receiver_delay.unlink(missing_ok=True)
            await mirror.stop()
            stop_receiver(receiver)


async def execute(arguments: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {"environment": "isolated", "passed": True}
    if arguments.mode in {"load", "all"}:
        result["load"] = await run_load(arguments.events, arguments.minimum_throughput, arguments.maximum_p95_ms)
        result["passed"] = result["passed"] and result["load"]["passed"]
    if arguments.mode in {"chaos", "all"}:
        result["chaos"] = await run_chaos(max(2, arguments.events // 10))
        result["passed"] = result["passed"] and result["chaos"]["passed"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receiver", type=int)
    parser.add_argument("--database")
    parser.add_argument("--mode", choices=("load", "chaos", "all"), default="all")
    parser.add_argument("--events", type=int, default=200)
    parser.add_argument("--minimum-throughput", type=float, default=20.0)
    parser.add_argument("--maximum-p95-ms", type=float, default=500.0)
    arguments = parser.parse_args()
    if arguments.receiver is not None:
        if not arguments.database:
            raise RuntimeError("aporia_operational_database_required")
        return serve(arguments.receiver, Path(arguments.database))
    if arguments.events < 10 or arguments.events > 10000:
        raise RuntimeError("aporia_operational_events_invalid")
    result = asyncio.run(execute(arguments))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
