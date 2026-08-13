from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = ROOT / "tools" / "run_offline_acceptance.py"
    spec = importlib.util.spec_from_file_location(
        "native_offline_acceptance",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_contract_overlay_is_explicit_and_non_releasable():
    runner = _load_runner()
    contract = json.loads(
        (ROOT / "runtime" / "release-contract.json").read_text(
            encoding="utf-8"
        )
    )
    original = json.loads(json.dumps(contract))
    identity = {
        "repository": contract["repository"],
        "commit": "a" * 40,
        "tree": "b" * 40,
        "transformation": contract["transformation"],
    }

    synthetic = runner.make_synthetic_contract(contract)
    synthetic_identity = runner.make_synthetic_identity(identity)
    report = runner.build_acceptance_report(
        target=contract["target"],
        source=synthetic_identity,
        foundation={"version": "0.2.0", "evidence_sha256": "c" * 64},
        asset={"sha256": "d" * 64, "bytes": 123},
        matrix={"pwsh": "PASS", "powershell": "PASS"},
    )

    assert contract == original
    assert original["client"]["acceptance"] == "PASS"
    assert original["client"]["supported_version"] == "1.18.13"
    assert synthetic["client"] == {
        "id": original["client"]["id"],
        "supported_version": "0.0.0-offline",
        "acceptance": "PASS",
    }
    assert synthetic_identity["transformation"].endswith(
        "-offline-contract-overlay"
    )
    assert report["channel"] == "InternalUnsigned"
    assert report["TECHNICAL_READY"] == "NOT_PASS"
    assert report["CLIENT_CONTRACT"] == "SYNTHETIC_ONLY"
    assert report["FOUNDATION_FAKE_HOME"] == "PASS"
    assert "package_acceptance" not in report
    assert report["evidence_body_sha256"] == runner.evidence_body_sha256(
        report
    )


def test_offline_workspace_is_adjacent_to_evidence(tmp_path):
    runner = _load_runner()
    output = tmp_path / "dist" / "offline-acceptance"

    parent = runner.acceptance_workspace_parent(output)

    assert parent == output.parent.resolve()
    assert parent.is_dir()


def test_accepted_client_candidate_report_remains_non_stable():
    runner = _load_runner()
    report = runner.build_acceptance_report(
        target="opencode",
        source={
            "repository": "https://github.com/example/opencode-base",
            "commit": "a" * 40,
            "tree": "b" * 40,
            "transformation": "opencode-native-v1",
        },
        foundation={"version": "0.2.1", "evidence_sha256": "c" * 64},
        asset={"sha256": "d" * 64, "bytes": 123},
        matrix={"pwsh": "PASS", "powershell": "PASS"},
        synthetic=False,
        client_version="1.18.13",
    )

    assert report["CLIENT_CONTRACT"] == "ACCEPTED_BINARY"
    assert report["CLIENT_BINARY_ACCEPTANCE"] == "PASS"
    assert report["CANDIDATE_OFFLINE"] == "PASS"
    assert report["TECHNICAL_READY"] == "PASS"
    assert report["INTERNAL_UNSIGNED_RELEASE"] == "PASS"
    assert "package_acceptance" not in report
    assert report["evidence_body_sha256"] == runner.evidence_body_sha256(
        report
    )


def test_foundation_commands_allow_slow_windows_install(
    monkeypatch, tmp_path
):
    runner = _load_runner()
    observed: dict[str, object] = {}

    def fake_run(arguments, **kwargs):
        observed["arguments"] = arguments
        observed["timeout"] = kwargs["timeout"]
        return SimpleNamespace(
            returncode=0,
            stdout='{"status":"PASS"}\n',
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner._run_foundation(
        executable="pwsh",
        foundation_script=tmp_path / "foundation.ps1",
        command="install",
        target="opencode",
        client_id="opencode",
        client_version="1.18.13",
        package=tmp_path / "candidate.zip",
        home=tmp_path / "home",
    )

    assert result == {"status": "PASS"}
    assert observed["timeout"] >= 180
