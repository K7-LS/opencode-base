from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_release_verifier",
    ROOT / "tools" / "release_verifier.py",
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    asset = tmp_path / "opencode-base-0.1.0.zip"
    asset.write_bytes(b"accepted")
    manifest = {
        "schema_version": 1,
        "target": "opencode",
        "tag": "opencode-v0.1.0",
        "channel": "stable",
        "source": {
            "repository": "https://github.com/daniileliseev1337/opencode-base",
        },
        "asset": {
            "name": asset.name,
            "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
            "bytes": asset.stat().st_size,
        },
    }
    path = tmp_path / "release-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, asset, manifest


def test_build_release_verification_binds_exact_immutable_asset(tmp_path: Path):
    path, asset, manifest = _fixture(tmp_path)
    evidence = verifier.build_release_verification(
        manifest_path=path,
        asset_path=asset,
        release_api={
            "tag_name": manifest["tag"],
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        release_attestation_output=b"{}",
        asset_attestation_output=b"{}",
        gh_version="gh version 2.96.0",
    )

    assert evidence["RELEASE_INTEGRITY"] == "PASS"
    assert evidence["assets"][0]["sha256"] == manifest["asset"]["sha256"]
    assert verifier.evidence_body_sha256(evidence) == (
        evidence["evidence_body_sha256"]
    )


def test_build_release_verification_rejects_mutable_release(tmp_path: Path):
    path, asset, manifest = _fixture(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        verifier.build_release_verification(
            manifest_path=path,
            asset_path=asset,
            release_api={
                "tag_name": manifest["tag"],
                "draft": False,
                "prerelease": False,
                "immutable": False,
            },
            release_attestation_output=b"{}",
            asset_attestation_output=b"{}",
            gh_version="gh version 2.96.0",
        )
