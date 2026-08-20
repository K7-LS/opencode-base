from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _attestation_digest(payload: bytes, label: str) -> str:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} did not return JSON") from error
    if not isinstance(parsed, (dict, list)):
        raise ValueError(f"{label} returned invalid JSON")
    return hashlib.sha256(payload).hexdigest()


def build_release_verification(
    *,
    manifest_path: Path,
    asset_path: Path,
    release_api: dict[str, Any],
    release_attestation_output: bytes,
    asset_attestation_output: bytes,
    gh_version: str,
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("target") != "opencode"
        or manifest.get("channel") != "stable"
    ):
        raise ValueError("stable OpenCode manifest is invalid")
    repository = str(
        manifest.get("source", {}).get("repository", "")
    ).removeprefix("https://github.com/").rstrip("/")
    if repository != "K7-LS/opencode-base":
        raise ValueError("stable OpenCode repository differs")
    if (
        release_api.get("tag_name") != manifest.get("tag")
        or release_api.get("draft") is not False
        or release_api.get("prerelease") is not False
        or release_api.get("immutable") is not True
    ):
        raise ValueError("GitHub release is not immutable stable")
    asset = manifest.get("asset")
    if (
        not isinstance(asset, dict)
        or not asset_path.is_file()
        or asset_path.name != asset.get("name")
        or hashlib.sha256(asset_path.read_bytes()).hexdigest()
        != asset.get("sha256")
        or asset_path.stat().st_size != asset.get("bytes")
    ):
        raise ValueError("local release asset binding differs")
    if not gh_version.startswith("gh version "):
        raise ValueError("GitHub CLI version evidence is invalid")
    evidence: dict[str, object] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "repository": repository,
        "tag": manifest["tag"],
        "release_state": {
            "draft": False,
            "prerelease": False,
            "immutable": True,
        },
        "release_attestation": "PASS",
        "assets": [{**asset, "attestation": "PASS"}],
        "verification_commands": {
            "gh_version": gh_version.splitlines()[0],
            "release_output_sha256": _attestation_digest(
                release_attestation_output,
                "gh release verify",
            ),
            "asset_output_sha256": _attestation_digest(
                asset_attestation_output,
                "gh release verify-asset",
            ),
        },
        "privacy": {
            "raw_attestation_output_included": False,
            "credentials_included": False,
            "personal_data_included": False,
        },
        "RELEASE_INTEGRITY": "PASS",
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    return evidence


def _run(command: list[str]) -> bytes:
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.decode("utf-8", errors="replace")[-2000:]
        )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gh", default="gh")
    arguments = parser.parse_args()
    manifest_path = arguments.manifest.resolve()
    asset_path = arguments.asset.resolve()
    output = arguments.output.resolve()
    if output.exists():
        raise SystemExit("release verification exists; refusing overwrite")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repository = str(
        manifest["source"]["repository"]
    ).removeprefix("https://github.com/").rstrip("/")
    tag = str(manifest["tag"])
    gh = arguments.gh
    gh_version = _run([gh, "--version"]).decode(
        "utf-8", errors="replace"
    ).strip()
    release_api = json.loads(
        _run([gh, "api", f"repos/{repository}/releases/tags/{tag}"])
    )
    evidence = build_release_verification(
        manifest_path=manifest_path,
        asset_path=asset_path,
        release_api=release_api,
        release_attestation_output=_run(
            [
                gh,
                "release",
                "verify",
                tag,
                "-R",
                repository,
                "--format",
                "json",
            ]
        ),
        asset_attestation_output=_run(
            [
                gh,
                "release",
                "verify-asset",
                tag,
                str(asset_path),
                "-R",
                repository,
                "--format",
                "json",
            ]
        ),
        gh_version=gh_version,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(_json_bytes(evidence))
    os.replace(temporary, output)
    print(json.dumps({"RELEASE_INTEGRITY": "PASS", "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
