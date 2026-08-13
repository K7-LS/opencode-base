from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load_tool(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release_builder = _load_tool(
    "opencode_canary_release_builder",
    ROOT / "tools" / "release_builder.py",
)
offline_runner = _load_tool(
    "opencode_canary_offline_runner",
    ROOT / "tools" / "run_offline_acceptance.py",
)

TARGET = "opencode"
CLIENT_ID = "opencode"
CLIENT_VERSION = "1.18.13"
CLIENT_VERSION_OUTPUT = "1.18.13"
CANARY_GATE = "OPENCODE_CANARY"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def build_canary_evidence(
    *,
    release_binding: dict[str, Any],
    client_version: str,
    lifecycle: dict[str, Any],
    component_counts: dict[str, int],
) -> dict[str, Any]:
    expected_lifecycle = {
        "plan": "READY",
        "install": "INSTALLED",
        "doctor": "HEALTHY",
        "inventory": "INSTALLED",
        "rollback": "ROLLED_BACK",
    }
    valid = (
        release_binding.get("target") == TARGET
        and isinstance(release_binding.get("version"), str)
        and client_version == CLIENT_VERSION
        and lifecycle.get("status") == "PASS"
        and lifecycle.get("lifecycle") == expected_lifecycle
        and lifecycle.get("preserved_data") == "PASS"
        and lifecycle.get("unknown_discovery_quarantine") == "PASS"
        and lifecycle.get("environment_apply_and_restore") == "PASS"
        and component_counts
        == {"agents": 16, "skills": 38, "control_skills": 1}
    )
    if not valid:
        raise ValueError("OpenCode live canary did not satisfy contract")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "target": TARGET,
        "version": release_binding["version"],
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "release_binding": release_binding,
        "client": {"id": CLIENT_ID, "version": client_version},
        "lifecycle": lifecycle["lifecycle"],
        "discovery": component_counts,
        "rollback": {
            "byte_identical": True,
            "preserved_data": "PASS",
            "unknown_discovery_restored": "PASS",
            "environment_restored": "PASS",
        },
        "network": "offline-local-files-only",
        "model_requests": 0,
        "credentials_included": False,
        "personal_data_included": False,
        CANARY_GATE: "PASS",
    }
    evidence["evidence_body_sha256"] = evidence_body_sha256(evidence)
    return evidence


def _load_candidate(candidate_dir: Path):
    manifest_path = candidate_dir / "release-manifest.json"
    evidence_path = candidate_dir / "candidate-acceptance.json"
    lock_path = candidate_dir / "components.lock.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    binding = release_builder.release_binding_from_manifest(manifest)
    asset = manifest.get("asset")
    package = candidate_dir / str(asset.get("name", ""))
    if (
        manifest.get("target") != TARGET
        or manifest.get("channel") != "candidate"
        or manifest.get("tag") != f"opencode-v{manifest.get('version')}"
        or manifest.get("acceptance_evidence_sha256")
        != hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        or evidence.get("target") != TARGET
        or evidence.get("CANDIDATE_OFFLINE") != "PASS"
        or evidence.get("release_binding") != binding
        or evidence.get("evidence_body_sha256")
        != offline_runner.evidence_body_sha256(evidence)
        or not package.is_file()
        or hashlib.sha256(package.read_bytes()).hexdigest()
        != asset.get("sha256")
        or package.stat().st_size != asset.get("bytes")
    ):
        raise ValueError("OpenCode candidate evidence is invalid")
    components = lock.get("components")
    counts = {
        "agents": len(components.get("agents", [])),
        "skills": len(components.get("skills", [])),
        "control_skills": len(components.get("control_skills", [])),
    }
    if counts != {"agents": 16, "skills": 38, "control_skills": 1}:
        raise ValueError("OpenCode candidate component closure differs")
    return binding, package, counts


def _write_new(path: Path, value: object) -> None:
    if path.exists():
        raise RuntimeError("live canary evidence exists; refusing overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-approved-live-canary", action="store_true")
    parser.add_argument("--candidate-dir", type=Path)
    parser.add_argument("--foundation", type=Path)
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument("--powershell", default=shutil.which("pwsh") or "pwsh")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/opencode-live-canary.json"),
    )
    arguments = parser.parse_args()
    plan = {
        "schema_version": 1,
        "would_execute": bool(arguments.execute_approved_live_canary),
        "target": TARGET,
        "client_version": CLIENT_VERSION,
        "lifecycle": ["plan", "install", "doctor", "inventory", "rollback"],
        "model_requests": 0,
        "home": "isolated-temporary",
    }
    if not arguments.execute_approved_live_canary:
        print(json.dumps(plan, sort_keys=True, indent=2))
        return 0
    if arguments.candidate_dir is None or arguments.foundation is None:
        raise SystemExit("--candidate-dir and --foundation are required")
    foundation = arguments.foundation.resolve()
    if not foundation.is_file():
        raise SystemExit("Foundation script is missing")
    binding, package, counts = _load_candidate(
        arguments.candidate_dir.resolve()
    )
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
        or version.stdout.strip() != CLIENT_VERSION_OUTPUT
    ):
        raise SystemExit(f"OpenCode must be exactly {CLIENT_VERSION}")
    with tempfile.TemporaryDirectory(prefix="opencode-live-canary-") as raw:
        lifecycle = offline_runner._run_matrix_case(
            executable=arguments.powershell,
            foundation_script=foundation,
            package=package,
            target=TARGET,
            client_id=CLIENT_ID,
            client_version=CLIENT_VERSION,
            root=Path(raw),
        )
    evidence = build_canary_evidence(
        release_binding=binding,
        client_version=CLIENT_VERSION,
        lifecycle=lifecycle,
        component_counts=counts,
    )
    _write_new(arguments.output.resolve(), evidence)
    print(json.dumps({CANARY_GATE: "PASS", "output": str(arguments.output.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
