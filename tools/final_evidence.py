from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


TARGET = "opencode"
VERSIONED_CLIENT = {
    "id": "opencode",
    "version": "1.18.13",
}
EXPECTED_LIFECYCLE = {
    "plan": "READY",
    "install": "CANONICAL",
    "doctor": "CANONICAL",
    "inventory": "INSTALLED",
    "rollback": "ROLLED_BACK",
}
EXPECTED_DISCOVERY = {
    "agents": 16,
    "skills": 38,
    "control_skills": 1,
}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_body(evidence: dict[str, Any]) -> bool:
    return evidence.get("evidence_body_sha256") == evidence_body_sha256(
        evidence
    )


def _source_record(evidence: dict[str, Any]) -> dict[str, object]:
    payload = _json_bytes(evidence)
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _validate_binding(binding: object) -> dict[str, Any]:
    if not isinstance(binding, dict):
        raise ValueError("candidate evidence release binding is invalid")
    asset = binding.get("asset")
    if (
        binding.get("target") != TARGET
        or not isinstance(binding.get("version"), str)
        or not binding["version"]
        or not isinstance(asset, dict)
        or not _valid_sha256(asset.get("sha256"))
        or not isinstance(asset.get("bytes"), int)
        or isinstance(asset.get("bytes"), bool)
        or asset["bytes"] <= 0
    ):
        raise ValueError("candidate evidence release binding is invalid")
    return binding


def _validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    binding = _validate_binding(candidate.get("release_binding"))
    if (
        candidate.get("schema_version") != 1
        or candidate.get("target") != TARGET
        or candidate.get("CANDIDATE_OFFLINE") != "PASS"
        or candidate.get("CLIENT_BINARY_ACCEPTANCE") != "PASS"
        or not _valid_body(candidate)
    ):
        raise ValueError("candidate evidence is invalid")
    return binding


def _valid_usage(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    cache = value.get("cache")
    numeric = ("total", "input", "output", "reasoning")
    return (
        set(value) == {*numeric, "cache"}
        and isinstance(cache, dict)
        and set(cache) == {"read", "write"}
        and all(
            isinstance(value[name], (int, float))
            and not isinstance(value[name], bool)
            and value[name] >= 0
            for name in numeric
        )
        and all(
            isinstance(cache[name], (int, float))
            and not isinstance(cache[name], bool)
            and cache[name] >= 0
            for name in ("read", "write")
        )
    )


def _validate_provider_marker(marker: dict[str, Any]) -> None:
    privacy = marker.get("privacy")
    valid = (
        marker.get("schema_version") == 1
        and marker.get("target") == TARGET
        and marker.get("client") == VERSIONED_CLIENT
        and marker.get("provider") == "openai"
        and marker.get("authentication") == "chatgpt-plus-pro-oauth"
        and isinstance(marker.get("provider_documentation"), str)
        and marker.get("model") == "openai/gpt-5.6-terra"
        and marker.get("variant") == "low"
        and marker.get("pure") is True
        and marker.get("permissions") == "deny-all"
        and marker.get("tool_events") == 0
        and marker.get("calls_authorized") == 1
        and marker.get("calls_completed") == 1
        and marker.get("OPENCODE_PROVIDER_MARKER") == "PASS"
        and _valid_usage(marker.get("usage"))
        and _valid_sha256(marker.get("result_sha256"))
        and isinstance(privacy, dict)
        and privacy.get("prompt_text_included") is False
        and privacy.get("response_text_included") is False
        and privacy.get("credentials_included") is False
        and privacy.get("personal_data_included") is False
        and privacy.get("session_identifier_included") is False
        and _valid_body(marker)
    )
    if not valid:
        raise ValueError("OpenCode provider marker evidence is invalid")


def _validate_canary(
    canary: dict[str, Any],
    binding: dict[str, Any],
) -> None:
    rollback = canary.get("rollback")
    valid = (
        canary.get("schema_version") == 1
        and canary.get("target") == TARGET
        and canary.get("version") == binding.get("version")
        and canary.get("release_binding") == binding
        and canary.get("client") == VERSIONED_CLIENT
        and canary.get("OPENCODE_CANARY") == "PASS"
        and canary.get("model_requests") == 0
        and canary.get("lifecycle") == EXPECTED_LIFECYCLE
        and canary.get("discovery") == EXPECTED_DISCOVERY
        and canary.get("network") == "offline-local-files-only"
        and canary.get("credentials_included") is False
        and canary.get("personal_data_included") is False
        and isinstance(rollback, dict)
        and rollback.get("byte_identical") is True
        and rollback.get("preserved_data") == "PASS"
        and rollback.get("unknown_discovery_restored") == "PASS"
        and rollback.get("environment_restored") == "PASS"
        and _valid_body(canary)
    )
    if not valid:
        raise ValueError("OpenCode canary evidence is invalid or unbound")


def compose_final_evidence(
    *,
    candidate: dict[str, Any],
    provider_marker: dict[str, Any],
    canary: dict[str, Any],
) -> dict[str, Any]:
    """Compose fail-closed pre-publication OpenCode FULL evidence."""

    binding = _validate_candidate(candidate)
    _validate_provider_marker(provider_marker)
    _validate_canary(canary, binding)
    final: dict[str, Any] = {
        "schema_version": 1,
        "target": TARGET,
        "version": binding["version"],
        "release_binding": binding,
        "asset_sha256": binding["asset"]["sha256"],
        "verdicts": {
            "CLIENT_BINARY_ACCEPTANCE": "PASS",
            "CANDIDATE_OFFLINE": "PASS",
            "PROVIDER_NEUTRAL_ACCEPTANCE": "PASS",
            "OPENCODE_PROVIDER_MARKER": "PASS",
            "OPENCODE_CANARY": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
        },
        "evidence_sources": {
            "candidate_offline": _source_record(candidate),
            "provider_marker": _source_record(provider_marker),
            "canary": _source_record(canary),
        },
        "limitations": [
            "Release integrity is pending immutable publication and GitHub attestation verification.",
            "The provider marker proves only the accepted OpenAI OAuth no-tools scenario.",
            "package-acceptance.json requires separate post-publication release-verification.json.",
        ],
    }
    final["evidence_body_sha256"] = evidence_body_sha256(final)
    return final


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _write_new(path: Path, value: object) -> None:
    if path.exists():
        raise RuntimeError("final evidence exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compose pre-publication OpenCode FULL evidence from an accepted "
            "candidate, the one approved marker, and a live canary."
        )
    )
    parser.add_argument("--candidate-evidence", required=True, type=Path)
    parser.add_argument("--provider-marker-evidence", required=True, type=Path)
    parser.add_argument("--canary-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    final = compose_final_evidence(
        candidate=_load(arguments.candidate_evidence.resolve()),
        provider_marker=_load(
            arguments.provider_marker_evidence.resolve()
        ),
        canary=_load(arguments.canary_evidence.resolve()),
    )
    _write_new(arguments.output.resolve(), final)
    print(
        json.dumps(
            {
                "FULL_RELEASE_OPENCODE": "PASS",
                "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
