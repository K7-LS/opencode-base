from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opencode_final_evidence",
    ROOT / "tools" / "final_evidence.py",
)
assert SPEC and SPEC.loader
finalizer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = finalizer
SPEC.loader.exec_module(finalizer)


def _binding() -> dict[str, object]:
    return {
        "target": "opencode",
        "version": "0.1.0",
        "asset": {"sha256": "a" * 64, "bytes": 123},
    }


def _evidence(kind: str, binding: dict[str, object]) -> dict[str, object]:
    if kind == "candidate":
        value = {
            "schema_version": 1,
            "target": "opencode",
            "CANDIDATE_OFFLINE": "PASS",
            "CLIENT_BINARY_ACCEPTANCE": "PASS",
            "release_binding": binding,
        }
    elif kind == "marker":
        value = {
            "schema_version": 1,
            "target": "opencode",
            "client": {"id": "opencode", "version": "1.18.7"},
            "provider": "openai",
            "authentication": "chatgpt-plus-pro-oauth",
            "provider_documentation": "https://example.test/providers",
            "model": "openai/gpt-5.6-terra",
            "variant": "low",
            "pure": True,
            "permissions": "deny-all",
            "tool_events": 0,
            "calls_authorized": 1,
            "calls_completed": 1,
            "usage": {
                "total": 20,
                "input": 12,
                "output": 8,
                "reasoning": 0,
                "cache": {"read": 0, "write": 0},
            },
            "result_sha256": "b" * 64,
            "OPENCODE_PROVIDER_MARKER": "PASS",
            "privacy": {
                "prompt_text_included": False,
                "response_text_included": False,
                "credentials_included": False,
                "personal_data_included": False,
                "session_identifier_included": False,
            },
        }
    else:
        value = {
            "schema_version": 1,
            "target": "opencode",
            "version": "0.1.0",
            "release_binding": binding,
            "client": {"id": "opencode", "version": "1.18.7"},
            "OPENCODE_CANARY": "PASS",
            "model_requests": 0,
            "lifecycle": {
                "plan": "READY",
                "install": "INSTALLED",
                "doctor": "HEALTHY",
                "inventory": "INSTALLED",
                "rollback": "ROLLED_BACK",
            },
            "network": "offline-local-files-only",
            "credentials_included": False,
            "personal_data_included": False,
            "rollback": {
                "byte_identical": True,
                "preserved_data": "PASS",
                "unknown_discovery_restored": "PASS",
                "environment_restored": "PASS",
            },
            "discovery": {
                "agents": 16,
                "skills": 37,
                "control_skills": 1,
            },
        }
    value["evidence_body_sha256"] = finalizer.evidence_body_sha256(value)
    return value


def test_final_evidence_requires_candidate_marker_and_canary():
    binding = _binding()
    final = finalizer.compose_final_evidence(
        candidate=_evidence("candidate", binding),
        provider_marker=_evidence("marker", binding),
        canary=_evidence("canary", binding),
    )

    assert final["verdicts"]["FULL_RELEASE_OPENCODE"] == "PASS"
    assert final["verdicts"]["PROVIDER_NEUTRAL_ACCEPTANCE"] == "PASS"
    assert final["verdicts"]["RELEASE_INTEGRITY"] == (
        "PENDING_PUBLICATION"
    )
    assert final["release_binding"] == binding


def test_final_evidence_rejects_tool_event_marker():
    binding = _binding()
    provider = _evidence("marker", binding)
    provider["tool_events"] = 1
    provider["evidence_body_sha256"] = finalizer.evidence_body_sha256(
        provider
    )
    with pytest.raises(ValueError, match="evidence"):
        finalizer.compose_final_evidence(
            candidate=_evidence("candidate", binding),
            provider_marker=provider,
            canary=_evidence("canary", binding),
        )
