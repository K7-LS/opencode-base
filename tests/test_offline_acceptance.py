from __future__ import annotations

import importlib.util
import json
from pathlib import Path


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
    assert original["client"]["acceptance"] == "NOT_ACCEPTED"
    assert synthetic["client"] == {
        "id": original["client"]["id"],
        "supported_version": "0.0.0-offline",
        "acceptance": "PASS",
    }
    assert synthetic_identity["transformation"].endswith(
        "-offline-contract-overlay"
    )
    assert report["NON_RELEASABLE"] is True
    assert report["CLIENT_CONTRACT"] == "SYNTHETIC_ONLY"
    assert report["FOUNDATION_FAKE_HOME"] == "PASS"
    assert report["FULL_RELEASE_OPENCODE"] == "NOT_PASS"
    assert "package_acceptance" not in report


def test_offline_workspace_is_adjacent_to_evidence(tmp_path):
    runner = _load_runner()
    output = tmp_path / "dist" / "offline-acceptance"

    parent = runner.acceptance_workspace_parent(output)

    assert parent == output.parent.resolve()
    assert parent.is_dir()
