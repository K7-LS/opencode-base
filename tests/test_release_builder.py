from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_release_builder",
    ROOT / "tools" / "release_builder.py",
)
assert SPEC and SPEC.loader
release_builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_builder
SPEC.loader.exec_module(release_builder)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _fake_foundation(root: Path) -> Path:
    root.mkdir()
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    script = root / "foundation.ps1"
    script.write_text("param([string]$Command)\n", encoding="utf-8")
    _json(
        root / "engine-manifest.json",
        {
            "schema_version": 1,
            "engine_version": "0.1.0",
            "protocol_version": 1,
            "network": "offline",
            "commands": ["doctor", "install", "inventory", "plan", "rollback"],
            "supported_powershell": ["5.1", "7"],
            "foundation_ps1_sha256": _sha256(script),
        },
    )
    return root


def _accepted_source(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "reports"),
    )
    path = source / "runtime" / "release-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    return source, contract


def test_release_builder_refuses_unaccepted_client_contract(tmp_path: Path):
    source, _ = _accepted_source(tmp_path)
    path = source / "runtime" / "release-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["client"]["supported_version"] = None
    contract["client"]["acceptance"] = "NOT_ACCEPTED"
    _json(path, contract)
    with pytest.raises(ValueError, match="binary contract"):
        release_builder.load_release_contract(source)


@pytest.mark.parametrize("tamper", ["version", "signature", "model_request"])
def test_release_builder_rejects_tampered_client_binary_evidence(
    tmp_path: Path,
    tamper: str,
):
    source, _ = _accepted_source(tmp_path)
    path = source / "runtime" / "client-acceptance.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "version":
        evidence["client"]["version"] = "9.9.9"
    elif tamper == "signature":
        evidence["binary"]["authenticode_status"] = "NotSigned"
    else:
        evidence["runtime_smoke"]["model_requests"] = 1
    _json(path, evidence)

    with pytest.raises(ValueError, match="binary acceptance evidence"):
        release_builder.load_release_contract(source)


def test_native_release_is_deterministic_complete_and_one_way(tmp_path: Path):
    source, contract = _accepted_source(tmp_path)
    foundation = _fake_foundation(tmp_path / "foundation")
    identity = {
        "repository": contract["repository"],
        "commit": "a" * 40,
        "tree": "b" * 40,
        "transformation": contract["transformation"],
    }
    first = release_builder.build_release_from_source(
        source,
        tmp_path / "one",
        "1.2.3",
        foundation,
        identity,
    )
    second = release_builder.build_release_from_source(
        source,
        tmp_path / "two",
        "1.2.3",
        foundation,
        identity,
    )
    assert _sha256(first.zip_path) == _sha256(second.zip_path)
    assert first.manifest == second.manifest
    assert first.manifest["channel"] == "candidate"
    assert first.manifest["client"] == {
        "id": contract["client"]["id"],
        "supported_version": "1.18.7",
    }

    root = contract["paths"]["install_root"]
    hot = contract["paths"]["hot_destination"]
    config = contract["paths"]["config_destination"]
    with zipfile.ZipFile(first.zip_path) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert hot in names
        assert config in names
        assert f"{root}/base/VERSION" in names
        assert f"{root}/base/components.lock.json" in names
        assert f"{root}/base/foundation/0.1.0/foundation.ps1" in names
        assert f"{root}/skills/sync-base/SKILL.md" in names
        assert len(
            [
                name
                for name in names
                if name.startswith(f"{root}/agents/") and name.endswith(".md")
            ]
        ) == 16
        assert len(
            [
                name
                for name in names
                if name.startswith(f"{root}/skills/") and name.endswith("/SKILL.md")
            ]
        ) == 38
        assert len(
            [
                name
                for name in names
                if name.startswith(f"{root}/commands/") and name.endswith(".md")
            ]
        ) == 3
        assert not any("/tests/" in name or "__pycache__" in name for name in names)

        package = json.loads(archive.read("package-manifest.json"))
        managed = json.loads(
            (source / "runtime" / "managed-surface.json").read_text(encoding="utf-8")
        )
        assert package["managed_surface"] == {
            "exact_directories": managed["exact_directories"],
            "replace_files": managed["replace_files"],
            "preserved_paths": managed["preserved_paths"],
        }
        assert package["environment"] == contract["environment"]
        assert package["sync_policy"] == {
            "direction": "hub-to-consumer",
            "consumer_feedback_upload": False,
            "consumer_push": False,
            "consumer_session_upload": False,
            "credentials_included": False,
        }

    lock = json.loads(first.component_lock_path.read_text(encoding="utf-8"))
    assert len(lock["components"]["agents"]) == 16
    assert len(lock["components"]["skills"]) == 37
    assert len(lock["components"]["control_skills"]) == 1
    assert len(lock["components"]["commands"]) == 3
    assert len(lock["components"]["cold"]) == 22


def test_package_acceptance_requires_stable_attested_full_pass(tmp_path: Path):
    source, contract = _accepted_source(tmp_path)
    foundation = _fake_foundation(tmp_path / "foundation")
    identity = {
        "repository": contract["repository"],
        "commit": "a" * 40,
        "tree": "b" * 40,
        "transformation": contract["transformation"],
    }
    built = release_builder.build_release_from_source(
        source,
        tmp_path / "release",
        "1.2.3",
        foundation,
        identity,
    )
    stable = dict(built.manifest)
    stable["channel"] = "stable"
    _json(built.manifest_path, stable)
    evidence = {
        "schema_version": 1,
        "target": contract["target"],
        "verdicts": {
            release_builder.FULL_VERDICTS[contract["target"]]: "PASS",
            "RELEASE_INTEGRITY": "PASS",
        },
        "asset_sha256": stable["asset"]["sha256"],
        "release_manifest_sha256": _sha256(built.manifest_path),
    }
    evidence_path = built.manifest_path.parent / "acceptance-evidence.json"
    _json(evidence_path, evidence)
    output = built.manifest_path.parent / "package-acceptance.json"

    accepted = release_builder.create_package_acceptance(
        built.manifest_path,
        evidence_path,
        output,
    )
    assert accepted["package_acceptance"] == "PASS"
    assert accepted["asset"]["sha256"] == _sha256(built.zip_path)

    evidence["verdicts"]["RELEASE_INTEGRITY"] = "NOT_PASS"
    _json(evidence_path, evidence)
    with pytest.raises(ValueError, match="incomplete"):
        release_builder.create_package_acceptance(
            built.manifest_path,
            evidence_path,
            output,
        )
