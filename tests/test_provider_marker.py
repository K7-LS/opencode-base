from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opencode_provider_marker",
    ROOT / "tools" / "provider_marker.py",
)
assert SPEC and SPEC.loader
marker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = marker
SPEC.loader.exec_module(marker)


def test_marker_command_is_pure_fixed_model_and_low_variant(tmp_path: Path):
    command = marker.build_command(
        opencode="opencode",
        workspace=tmp_path,
    )

    assert command[:2] == ["opencode", "run"]
    assert "--pure" in command
    assert ["--model", "openai/gpt-5.6-terra"] == command[
        command.index("--model") : command.index("--model") + 2
    ]
    assert ["--variant", "low"] == command[
        command.index("--variant") : command.index("--variant") + 2
    ]
    assert ["--format", "json"] == command[
        command.index("--format") : command.index("--format") + 2
    ]
    assert "--auto" not in command


def test_event_parser_rejects_any_tool_use():
    with pytest.raises(marker.MarkerViolation, match="tool"):
        marker.inspect_event(
            {
                "type": "tool_use",
                "sessionID": "ignored",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"status": "completed"},
                },
            }
        )


def test_event_parser_extracts_text_and_step_usage():
    assert marker.inspect_event(
        {
            "type": "text",
            "sessionID": "ignored",
            "part": {
                "type": "text",
                "text": marker.EXPECTED_RESPONSE,
            },
        }
    ) == {"message": marker.EXPECTED_RESPONSE}
    assert marker.inspect_event(
        {
            "type": "step_finish",
            "part": {
                "type": "step-finish",
                "cost": 0.001,
                "tokens": {
                    "total": 101,
                    "input": 90,
                    "output": 11,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            },
        }
    ) == {
        "usage": {
            "total": 101,
            "input": 90,
            "output": 11,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        }
    }


def test_marker_evidence_contains_no_prompt_response_session_or_auth():
    evidence = marker.summarize_marker(
        client_version="1.18.13",
        message=marker.EXPECTED_RESPONSE,
        usage={
            "total": 101,
            "input": 90,
            "output": 11,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
    )

    serialized = json.dumps(evidence, ensure_ascii=False)
    assert evidence["OPENCODE_PROVIDER_MARKER"] == "PASS"
    assert marker.EXPECTED_RESPONSE not in serialized
    assert marker.PROMPT not in serialized
    assert "sessionID" not in serialized
    assert "auth.json" not in serialized
