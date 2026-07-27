from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_CLIENT = "1.18.7"
MODEL = "openai/gpt-5.6-terra"
VARIANT = "low"
PROMPT = "Ответь ровно: OPENCODE_BASE_CANARY_OK"
EXPECTED_RESPONSE = "OPENCODE_BASE_CANARY_OK"
PROVIDER_DOCS = "https://opencode.ai/docs/providers/#openai"


class MarkerViolation(RuntimeError):
    """The provider marker crossed its one-call/no-tools boundary."""


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def build_command(*, opencode: str, workspace: Path) -> list[str]:
    return [
        opencode,
        "run",
        "--pure",
        "--model",
        MODEL,
        "--variant",
        VARIANT,
        "--format",
        "json",
        "--dir",
        str(workspace.resolve()),
        PROMPT,
    ]


def _validate_usage(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarkerViolation("OpenCode step has no usage")
    cache = value.get("cache")
    if (
        set(value) != {
            "total",
            "input",
            "output",
            "reasoning",
            "cache",
        }
        or not isinstance(cache, dict)
        or set(cache) != {"read", "write"}
        or not all(
            isinstance(value[name], (int, float))
            and not isinstance(value[name], bool)
            and value[name] >= 0
            for name in ("total", "input", "output", "reasoning")
        )
        or not all(
            isinstance(cache[name], (int, float))
            and not isinstance(cache[name], bool)
            and cache[name] >= 0
            for name in ("read", "write")
        )
    ):
        raise MarkerViolation("OpenCode step usage is invalid")
    return {
        "total": value["total"],
        "input": value["input"],
        "output": value["output"],
        "reasoning": value["reasoning"],
        "cache": {
            "read": cache["read"],
            "write": cache["write"],
        },
    }


def inspect_event(event: object) -> dict[str, Any]:
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise MarkerViolation("OpenCode emitted an invalid JSON event")
    event_type = event["type"]
    if event_type == "tool_use":
        raise MarkerViolation("OpenCode provider marker emitted a tool event")
    if event_type == "error":
        raise MarkerViolation("OpenCode provider marker returned an error")
    part = event.get("part")
    if event_type == "step_start":
        if not isinstance(part, dict) or part.get("type") != "step-start":
            raise MarkerViolation("OpenCode step_start event is invalid")
        return {}
    if event_type == "step_finish":
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            raise MarkerViolation("OpenCode step_finish event is invalid")
        return {"usage": _validate_usage(part.get("tokens"))}
    if event_type == "text":
        if (
            not isinstance(part, dict)
            or part.get("type") != "text"
            or not isinstance(part.get("text"), str)
        ):
            raise MarkerViolation("OpenCode text event is invalid")
        return {"message": part["text"].strip()}
    raise MarkerViolation(f"unexpected OpenCode event: {event_type}")


def summarize_marker(
    *,
    client_version: str,
    message: str,
    usage: dict[str, Any],
) -> dict[str, Any]:
    validated_usage = _validate_usage(usage)
    if (
        client_version != SUPPORTED_CLIENT
        or message != EXPECTED_RESPONSE
    ):
        raise ValueError("OpenCode provider marker did not satisfy contract")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "target": "opencode",
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "client": {
            "id": "opencode",
            "version": client_version,
        },
        "provider": "openai",
        "authentication": "chatgpt-plus-pro-oauth",
        "provider_documentation": PROVIDER_DOCS,
        "model": MODEL,
        "variant": VARIANT,
        "pure": True,
        "permissions": "deny-all",
        "tool_events": 0,
        "calls_authorized": 1,
        "calls_completed": 1,
        "usage": validated_usage,
        "result_sha256": hashlib.sha256(
            EXPECTED_RESPONSE.encode("utf-8")
        ).hexdigest(),
        "privacy": {
            "prompt_text_included": False,
            "response_text_included": False,
            "credentials_included": False,
            "personal_data_included": False,
            "session_identifier_included": False,
        },
        "OPENCODE_PROVIDER_MARKER": "PASS",
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    return evidence


def _write_new(path: Path, value: object) -> None:
    if path.exists():
        raise RuntimeError(
            "OpenCode provider evidence exists; repeat requires new approval"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the one approved OpenCode/OpenAI OAuth marker. Default is "
            "a zero-request dry-run."
        )
    )
    parser.add_argument("--execute-approved-marker", action="store_true")
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/opencode-provider-marker.json"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    arguments = parser.parse_args()
    plan = {
        "schema_version": 1,
        "would_execute": bool(arguments.execute_approved_marker),
        "calls_total": 1,
        "client_version": SUPPORTED_CLIENT,
        "provider": "openai",
        "authentication": "chatgpt-plus-pro-oauth",
        "model": MODEL,
        "variant": VARIANT,
        "pure": True,
        "permissions": "deny-all",
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
    }
    if not arguments.execute_approved_marker:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if arguments.timeout_seconds < 30 or arguments.timeout_seconds > 600:
        raise SystemExit("--timeout-seconds must be between 30 and 600")
    version = subprocess.run(
        [arguments.opencode, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=15,
    )
    if (
        version.returncode != 0
        or version.stdout.strip() != SUPPORTED_CLIENT
    ):
        raise SystemExit(f"OpenCode must be exactly {SUPPORTED_CLIENT}")
    with tempfile.TemporaryDirectory(prefix="opencode-provider-marker-") as raw:
        workspace = Path(raw)
        (workspace / "opencode.json").write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "share": "disabled",
                    "permission": "deny",
                    "plugin": [],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"
        process = subprocess.run(
            build_command(
                opencode=arguments.opencode,
                workspace=workspace,
            ),
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=arguments.timeout_seconds,
        )
        if process.returncode != 0:
            raise MarkerViolation(
                "OpenCode provider marker failed: " + process.stderr[-2000:]
            )
        message: str | None = None
        usage: dict[str, Any] | None = None
        for line in process.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise MarkerViolation(
                    "OpenCode emitted non-JSON output in JSON mode"
                ) from error
            observation = inspect_event(event)
            if "message" in observation:
                message = observation["message"]
            if "usage" in observation:
                usage = observation["usage"]
    if message is None or usage is None:
        raise MarkerViolation(
            "OpenCode marker completed without text or usage evidence"
        )
    evidence = summarize_marker(
        client_version=SUPPORTED_CLIENT,
        message=message,
        usage=usage,
    )
    _write_new(arguments.output.resolve(), evidence)
    print(
        json.dumps(
            {
                "OPENCODE_PROVIDER_MARKER": "PASS",
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
