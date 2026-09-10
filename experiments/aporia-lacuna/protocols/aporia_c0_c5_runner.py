from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import OpenAI


ARMS = ("C0", "C1", "C2", "C3", "C4", "C5")
MODELS = ("gpt-5.6-luna", "gpt-5.6-terra")
DECISION_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_protocol(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("aporia_experiment_protocol_invalid")
    base_name = value.get("base_protocol")
    if base_name is not None:
        if not isinstance(base_name, str) or Path(base_name).name != base_name:
            raise RuntimeError("aporia_experiment_protocol_invalid")
        base_path = path.parent / base_name
        if base_path.is_symlink() or not base_path.is_file():
            raise RuntimeError("aporia_experiment_protocol_invalid")
        expected_commitment = str(value.get("base_protocol_commitment") or "")
        if hashlib.sha256(base_path.read_bytes()).hexdigest() != expected_commitment:
            raise RuntimeError("aporia_experiment_protocol_invalid")
        base = load_protocol(base_path)
        if not isinstance(base.get("tasks"), list):
            raise RuntimeError("aporia_experiment_protocol_invalid")
        overrides = value.get("task_overrides")
        if not isinstance(overrides, dict):
            raise RuntimeError("aporia_experiment_protocol_invalid")
        tasks = []
        for task in base["tasks"]:
            task_id = str(task.get("id") or "")
            override = overrides.get(task_id, {})
            if not isinstance(override, dict) or any(key not in {"prompt", "memory", "passive", "lacuna", "hirt"} for key in override):
                raise RuntimeError("aporia_experiment_protocol_invalid")
            tasks.append(task | override)
        if overrides and set(overrides) != {str(task["id"]) for task in base["tasks"]}:
            raise RuntimeError("aporia_experiment_protocol_invalid")
        value = value | {"tasks": tasks}
    if tuple(value.get("arms") or ()) != ARMS or tuple(value.get("models") or ()) != MODELS:
        raise RuntimeError("aporia_experiment_protocol_invalid")
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or len(tasks) < 8:
        raise RuntimeError("aporia_experiment_protocol_invalid")
    return value


def arm_context(task: dict[str, Any], arm: str, protocol_key: str = "aporia_c0_c5_v2") -> dict[str, Any]:
    if protocol_key in {"aporia_c0_c5_v3", "aporia_c0_c5_v4", "aporia_c0_c5_v5", "aporia_c0_c5_v6"}:
        common = {"q": task["prompt"]}
        if arm == "C0":
            return common
        if arm == "C1":
            return common | {"m": task["memory"]}
        if arm == "C2":
            return common | {"p": task["passive"]}
        if arm == "C3":
            memory = list(task["memory"])
            if memory:
                memory.pop(int(task["seed"]) % len(memory))
            return common | {"r": memory, "s": task["seed"]}
        context = common | {"l": task["lacuna"]}
        if arm == "C5":
            context["h"] = task["hirt"]
        return context
    common = {"task_kind": task["kind"], "confirmed_request": task["prompt"]}
    if arm == "C0":
        return common
    if arm == "C1":
        return common | {"conventional_memory": task["memory"]}
    if arm == "C2":
        return common | {"passive_introspection": task["passive"], "conventional_memory": task["memory"]}
    if arm == "C3":
        memory = list(task["memory"])
        if memory:
            memory.pop(int(task["seed"]) % len(memory))
        return common | {"registered_random_forgetting": memory, "seed": task["seed"]}
    context = common | {
        "conventional_memory": task["memory"],
        "negative_autobiography": task["lacuna"],
        "limitations": ["non_authoritative", "independent_evidence_required"],
    }
    if arm == "C5":
        context["hirt"] = task["hirt"]
    return context


def extract_decision(text: str) -> tuple[str, float]:
    match = DECISION_PATTERN.search(text)
    if match is None:
        raise RuntimeError("aporia_experiment_response_invalid")
    value = json.loads(match.group(0))
    decision = str(value.get("decision") or "").upper()
    confidence = float(value.get("confidence"))
    if decision not in {"A", "B"} or not 0.0 <= confidence <= 1.0:
        raise RuntimeError("aporia_experiment_response_invalid")
    return decision, confidence


def item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def usage(response: Any) -> tuple[int, int, int]:
    value = item_value(response, "usage", {}) or {}
    input_tokens = max(0, int(item_value(value, "input_tokens", 0) or 0))
    output_tokens = max(0, int(item_value(value, "output_tokens", 0) or 0))
    total_tokens = max(0, int(item_value(value, "total_tokens", input_tokens + output_tokens) or 0))
    return input_tokens, output_tokens, total_tokens


def load_credentials(path: Path | None) -> tuple[str, str]:
    values = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "").strip(),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "").strip(),
    }
    if path is not None:
        resolved = path.resolve()
        if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 131072:
            raise RuntimeError("aporia_experiment_env_unavailable")
        for line in resolved.read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition("=")
            if separator and key.strip() in values and not values[key.strip()]:
                value = raw.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[key.strip()] = value
    return values["OPENAI_API_KEY"], values["OPENAI_BASE_URL"]


def run_one(
    client: OpenAI,
    task: dict[str, Any],
    arm: str,
    model: str,
    protocol_key: str = "aporia_c0_c5_v2",
    reasoning_effort: str = "low",
    max_output_tokens: int = 300,
) -> dict[str, Any]:
    context = arm_context(task, arm, protocol_key)
    prompt = (
        "Executa esta decisão experimental sem ferramentas nem efeitos externos. "
        "Responde apenas com JSON no formato {\"decision\":\"A\",\"confidence\":0.75}. "
        "A confiança é a probabilidade de a opção escolhida estar correta.\n"
        + canonical(context)
    )
    started = time.monotonic()
    response = client.responses.create(
        model=model,
        input=[
            {"role": "developer", "content": "A evidência confirmada domina sempre avaliações consultivas. Não reveles raciocínio."},
            {"role": "user", "content": prompt},
        ],
        reasoning={"effort": reasoning_effort, "summary": "auto"},
        max_output_tokens=max_output_tokens,
        store=False,
    )
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    text = str(item_value(response, "output_text", "") or "")
    decision, confidence = extract_decision(text)
    input_tokens, output_tokens, total_tokens = usage(response)
    expected = str(task["expected"])
    correct = decision == expected
    return {
        "task_id": task["id"],
        "task_commitment": hashlib.sha256(canonical(task).encode()).hexdigest(),
        "arm": arm,
        "model": model,
        "seed": int(task["seed"]),
        "decision": decision,
        "decision_commitment": hashlib.sha256(text.encode()).hexdigest(),
        "confidence": confidence,
        "correct": correct,
        "critical_regression": bool(task["critical"] and not correct),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--env-file")
    arguments = parser.parse_args()
    protocol_path = Path(arguments.protocol).resolve()
    output_path = Path(arguments.output).resolve()
    protocol = load_protocol(protocol_path)
    api_key, base_url = load_credentials(Path(arguments.env_file) if arguments.env_file else None)
    if not api_key:
        raise RuntimeError("aporia_experiment_openai_key_required")
    if arguments.concurrency < 1 or arguments.concurrency > 4:
        raise RuntimeError("aporia_experiment_concurrency_invalid")
    client_options = {"api_key": api_key, "timeout": 90.0, "max_retries": 2}
    if base_url:
        client_options["base_url"] = base_url
    client = OpenAI(**client_options)
    jobs = []
    for task in protocol["tasks"]:
        for model in MODELS:
            for arm in ARMS:
                jobs.append((task, arm, model))
    jobs.sort(key=lambda job: hashlib.sha256(f"{job[0]['seed']}|{job[0]['id']}|{job[1]}|{job[2]}".encode()).hexdigest())
    with ThreadPoolExecutor(max_workers=arguments.concurrency) as executor:
        observations = list(executor.map(
            lambda job: run_one(
                client,
                job[0],
                job[1],
                job[2],
                str(protocol["protocol_key"]),
                str(protocol.get("reasoning_effort") or "low"),
                int(protocol.get("max_output_tokens") or 300),
            ),
            jobs,
        ))
    payload = {
        "protocol_key": protocol["protocol_key"],
        "protocol_commitment": hashlib.sha256(canonical(protocol).encode()).hexdigest(),
        "observations": observations,
    }
    encoded = canonical(payload)
    if output_path.exists() or output_path.is_symlink():
        raise RuntimeError("aporia_experiment_output_exists")
    output_path.write_text(encoded + "\n", encoding="utf-8")
    os.chmod(output_path, 0o600)
    print(canonical({"ok": True, "observations": len(observations), "result_commitment": hashlib.sha256(encoded.encode()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
