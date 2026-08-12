from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release_builder = _load(
    "opencode_promotion_release_builder",
    ROOT / "tools" / "release_builder.py",
)
finalizer = _load(
    "opencode_promotion_finalizer",
    ROOT / "tools" / "final_evidence.py",
)
promotion = _load(
    "opencode_promotion",
    ROOT / "tools" / "promotion.py",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _foundation(root: Path) -> Path:
    root.mkdir()
    script = root / "foundation.ps1"
    script.write_text("exit 0\n", encoding="utf-8")
    (root / "VERSION").write_text("0.2.1\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "protocol_version": 1,
        "engine_version": "0.2.1",
        "network": "offline",
        "commands": [
            "apply",
            "doctor",
            "install",
            "inventory",
            "plan",
            "rollback",
        ],
        "supported_powershell": ["5.1", "7"],
        "foundation_ps1_sha256": hashlib.sha256(
            script.read_bytes()
        ).hexdigest(),
    }
    (root / "engine-manifest.json").write_bytes(_json_bytes(manifest))
    return root


def _candidate(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    contract = release_builder.load_release_contract(source)
    built = release_builder.build_release_from_source(
        source,
        tmp_path / "candidate",
        "0.1.0",
        _foundation(tmp_path / "foundation"),
        {
            "repository": contract["repository"],
            "commit": "a" * 40,
            "tree": "b" * 40,
            "transformation": contract["transformation"],
        },
    )
    binding = release_builder.release_binding_from_manifest(built.manifest)
    evidence = {
        "schema_version": 1,
        "target": "opencode",
        "CANDIDATE_OFFLINE": "PASS",
        "CLIENT_BINARY_ACCEPTANCE": "PASS",
        "release_binding": binding,
    }
    evidence["evidence_body_sha256"] = finalizer.evidence_body_sha256(
        evidence
    )
    evidence_path = built.manifest_path.parent / "candidate-acceptance.json"
    evidence_path.write_bytes(_json_bytes(evidence))
    release_builder.bind_candidate_acceptance(
        built.manifest_path,
        evidence_path,
    )
    return built.manifest_path.parent, binding


def _final(
    path: Path,
    binding: dict[str, object],
    *,
    marker: str = "PASS",
) -> Path:
    evidence = {
        "schema_version": 1,
        "target": "opencode",
        "version": binding["version"],
        "release_binding": binding,
        "asset_sha256": binding["asset"]["sha256"],
        "verdicts": {
            "CLIENT_BINARY_ACCEPTANCE": "PASS",
            "CANDIDATE_OFFLINE": "PASS",
            "PROVIDER_NEUTRAL_ACCEPTANCE": "PASS",
            "OPENCODE_PROVIDER_MARKER": marker,
            "OPENCODE_CANARY": "PASS",
            "FULL_RELEASE_OPENCODE": "PASS",
            "RELEASE_INTEGRITY": "PENDING_PUBLICATION",
        },
    }
    evidence["evidence_body_sha256"] = finalizer.evidence_body_sha256(
        evidence
    )
    path.write_bytes(_json_bytes(evidence))
    return path


def test_promotion_preserves_exact_candidate_zip(tmp_path: Path):
    candidate, binding = _candidate(tmp_path)
    source_zip = candidate / "opencode-base-0.1.0.zip"
    source_bytes = source_zip.read_bytes()

    result = promotion.promote_candidate(
        candidate,
        _final(tmp_path / "final.json", binding),
        tmp_path / "stable",
    )

    assert result.zip_path.read_bytes() == source_bytes
    assert result.zip_sha256 == hashlib.sha256(source_bytes).hexdigest()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["channel"] == "stable"
    assert len(manifest["promoted_from_candidate_manifest_sha256"]) == 64


def test_promotion_fails_closed_on_marker_failure(tmp_path: Path):
    candidate, binding = _candidate(tmp_path)

    with pytest.raises(ValueError, match="OPENCODE_PROVIDER_MARKER"):
        promotion.promote_candidate(
            candidate,
            _final(
                tmp_path / "final.json",
                binding,
                marker="NOT_PASS",
            ),
            tmp_path / "stable",
        )

    assert not (tmp_path / "stable").exists()
