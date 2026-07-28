from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import release_builder  # noqa: E402


SYNTHETIC_CLIENT_VERSION = "0.0.0-offline"
SYNTHETIC_PACKAGE_VERSION = "0.0.0"
FULL_VERDICTS = {
    "claude": "FULL_RELEASE_CLAUDE",
    "opencode": "FULL_RELEASE_OPENCODE",
}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")


def evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def make_synthetic_contract(
    contract: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(contract)
    result["client"]["supported_version"] = SYNTHETIC_CLIENT_VERSION
    result["client"]["acceptance"] = "PASS"
    return result


def make_synthetic_identity(
    identity: dict[str, str],
) -> dict[str, str]:
    result = dict(identity)
    result["transformation"] = (
        identity["transformation"] + "-offline-contract-overlay"
    )
    return result


def acceptance_workspace_parent(output_root: Path) -> Path:
    parent = output_root.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def build_acceptance_report(
    *,
    target: str,
    source: dict[str, str],
    foundation: dict[str, object],
    asset: dict[str, object],
    matrix: dict[str, object],
    synthetic: bool = True,
    client_version: str = SYNTHETIC_CLIENT_VERSION,
    release_binding: dict[str, object] | None = None,
) -> dict[str, object]:
    verdict = FULL_VERDICTS[target]
    matrix_passed = bool(matrix) and all(
        (
            value == "PASS"
            if isinstance(value, str)
            else value.get("status") == "PASS"
        )
        for value in matrix.values()
    )
    report = {
        "schema_version": 1,
        "target": target,
        "NON_RELEASABLE": True,
        "CLIENT_CONTRACT": (
            "SYNTHETIC_ONLY" if synthetic else "ACCEPTED_BINARY"
        ),
        "FOUNDATION_FAKE_HOME": (
            "PASS" if matrix_passed else "NOT_PASS"
        ),
        "DETERMINISTIC_PACKAGE": "PASS",
        verdict: "NOT_PASS",
        "PROGRAM_RELEASE": "0/3",
        "source": source,
        "foundation": foundation,
        "asset": asset,
        "matrix": matrix,
        "limitations": (
            [
                "The client version is a synthetic offline contract overlay.",
                "The package transformation intentionally differs from release policy.",
                "No live client, user home, network, paid model, or stable release was used.",
                "This evidence cannot create package-acceptance.json.",
            ]
            if synthetic
            else [
                f"The exact client binary {client_version} is accepted, but no live base canary ran.",
                "No provider login, model request, paid A/B, immutable release, or employee rollout ran.",
                "This candidate evidence cannot create package-acceptance.json.",
            ]
        ),
    }
    if not synthetic:
        report["CLIENT_BINARY_ACCEPTANCE"] = "PASS"
        report["CANDIDATE_OFFLINE"] = (
            "PASS" if matrix_passed else "NOT_PASS"
        )
        if release_binding is not None:
            report["release_binding"] = release_binding
    report["evidence_body_sha256"] = evidence_body_sha256(report)
    return report


def _validate_foundation(
    foundation_root: Path,
    evidence_path: Path,
) -> dict[str, object]:
    required = {
        name: foundation_root / name
        for name in ("VERSION", "foundation.ps1", "engine-manifest.json")
    }
    if not all(path.is_file() for path in required.values()):
        raise ValueError("Foundation engine bundle is incomplete")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    version = required["VERSION"].read_text(encoding="utf-8").strip()
    hashes = {
        name: _file_sha256(path)
        for name, path in required.items()
    }
    builds = evidence.get("engine_builds", {})
    matching = [
        row
        for row in builds.values()
        if row.get("status") == "PASS" and row.get("files") == hashes
    ]
    if (
        evidence.get("FOUNDATION_SYNTHETIC") != "PASS"
        or evidence.get("engine_version") != version
        or not matching
    ):
        raise ValueError(
            "Foundation evidence does not bind this engine bundle"
        )
    return {
        "version": version,
        "evidence_sha256": _file_sha256(evidence_path),
        "source": evidence.get("source"),
    }


def _run_foundation(
    *,
    executable: str,
    foundation_script: Path,
    command: str,
    target: str,
    client_id: str,
    client_version: str,
    package: Path,
    home: Path,
) -> dict[str, object]:
    arguments = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(foundation_script),
        command,
        "-TargetHome",
        str(home),
        "-Target",
        target,
        "-Json",
    ]
    if command in {"plan", "install"}:
        arguments.extend(["-Package", str(package)])
    if command in {"plan", "install", "doctor"}:
        arguments.extend(
            [
                "-ClientId",
                client_id,
                "-ClientVersion",
                client_version,
            ]
        )
    environment = os.environ.copy()
    environment["FOUNDATION_ACCEPTANCE_MODE"] = "1"
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=180,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Foundation {command} failed under {executable}: "
            + (result.stderr or result.stdout).strip()
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"Foundation {command} returned no JSON")
    return json.loads(lines[-1])


def _write_user_sentinels(
    home: Path,
    target: str,
) -> tuple[dict[Path, bytes], Path]:
    if target == "claude":
        preserved = {
            home / ".claude.json": b'{"auth":"preserve"}\n',
            home
            / ".claude"
            / "projects"
            / "session.json": b'{"session":"preserve"}\n',
            home / "projects" / "work.txt": b"preserve project\n",
        }
        unknown = (
            home
            / ".claude"
            / "skills"
            / "local-unknown"
            / "SKILL.md"
        )
    elif target == "opencode":
        preserved = {
            home
            / ".config"
            / "opencode"
            / "auth.json": b'{"auth":"preserve"}\n',
            home
            / ".local"
            / "share"
            / "opencode"
            / "session.json": b'{"session":"preserve"}\n',
            home / "projects" / "work.txt": b"preserve project\n",
        }
        unknown = (
            home
            / ".config"
            / "opencode"
            / "skills"
            / "local-unknown"
            / "SKILL.md"
        )
    else:
        raise ValueError("Unsupported native target")
    for path, payload in preserved.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_text("# local unknown\n", encoding="utf-8")
    return preserved, unknown


def _assert_sentinels(sentinels: dict[Path, bytes]) -> None:
    for path, expected in sentinels.items():
        if not path.is_file() or path.read_bytes() != expected:
            raise RuntimeError(f"Preserved user data changed: {path}")


def _read_acceptance_environment(home: Path) -> dict[str, str]:
    path = (
        home
        / ".llm-foundation"
        / "acceptance-user-environment.json"
    )
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["name"]: row["value"]
        for row in payload.get("values", [])
    }


def _run_matrix_case(
    *,
    executable: str,
    foundation_script: Path,
    package: Path,
    target: str,
    client_id: str,
    client_version: str,
    root: Path,
) -> dict[str, object]:
    home = root / Path(executable).stem
    home.mkdir(parents=True)
    sentinels, unknown = _write_user_sentinels(home, target)

    plan = _run_foundation(
        executable=executable,
        foundation_script=foundation_script,
        command="plan",
        target=target,
        client_id=client_id,
        client_version=client_version,
        package=package,
        home=home,
    )
    install = _run_foundation(
        executable=executable,
        foundation_script=foundation_script,
        command="install",
        target=target,
        client_id=client_id,
        client_version=client_version,
        package=package,
        home=home,
    )
    _assert_sentinels(sentinels)
    if unknown.exists():
        raise RuntimeError("Unknown skill remained in active discovery")
    doctor = _run_foundation(
        executable=executable,
        foundation_script=foundation_script,
        command="doctor",
        target=target,
        client_id=client_id,
        client_version=client_version,
        package=package,
        home=home,
    )
    inventory = _run_foundation(
        executable=executable,
        foundation_script=foundation_script,
        command="inventory",
        target=target,
        client_id=client_id,
        client_version=client_version,
        package=package,
        home=home,
    )
    installed_environment = _read_acceptance_environment(home)
    expected_environment = (
        {"OPENCODE_DISABLE_CLAUDE_CODE": "1"}
        if target == "opencode"
        else {}
    )
    if installed_environment != expected_environment:
        raise RuntimeError("Installed environment contract differs")
    rollback = _run_foundation(
        executable=executable,
        foundation_script=foundation_script,
        command="rollback",
        target=target,
        client_id=client_id,
        client_version=client_version,
        package=package,
        home=home,
    )
    _assert_sentinels(sentinels)
    if not unknown.is_file():
        raise RuntimeError("Rollback did not restore the unknown skill")
    if _read_acceptance_environment(home):
        raise RuntimeError("Rollback did not restore user environment")
    statuses = {
        "plan": plan.get("status"),
        "install": install.get("status"),
        "doctor": doctor.get("status"),
        "inventory": inventory.get("status"),
        "rollback": rollback.get("status"),
    }
    expected = {
        "plan": "READY",
        "install": "INSTALLED",
        "doctor": "HEALTHY",
        "inventory": "INSTALLED",
        "rollback": "ROLLED_BACK",
    }
    if statuses != expected:
        raise RuntimeError(
            f"Foundation lifecycle statuses differ: {statuses}"
        )
    return {
        "status": "PASS",
        "executable": executable,
        "lifecycle": statuses,
        "preserved_data": "PASS",
        "unknown_discovery_quarantine": "PASS",
        "environment_apply_and_restore": "PASS",
    }


def run_acceptance(
    *,
    repo_root: Path,
    foundation_root: Path,
    foundation_evidence: Path,
    output_root: Path,
    candidate_version: str | None = None,
) -> dict[str, object]:
    if output_root.exists():
        raise ValueError("Output root must not exist")
    foundation = _validate_foundation(
        foundation_root,
        foundation_evidence,
    )
    identity = release_builder.assert_clean_git_source(repo_root)
    contract = json.loads(
        (repo_root / "runtime" / "release-contract.json").read_text(
            encoding="utf-8"
        )
    )
    target = str(contract["target"])
    client_id = str(contract["client"]["id"])
    accepted_client = contract["client"]["acceptance"] == "PASS"
    if accepted_client:
        if (
            candidate_version is None
            or release_builder.VERSION.fullmatch(candidate_version) is None
        ):
            raise ValueError(
                "Accepted client requires a canonical candidate version"
            )
        client_version = str(contract["client"]["supported_version"])
        release_identity = identity
        package_version = candidate_version
    elif contract["client"]["acceptance"] == "NOT_ACCEPTED":
        if candidate_version is not None:
            raise ValueError(
                "Unaccepted client cannot build a real candidate"
            )
        client_version = SYNTHETIC_CLIENT_VERSION
        release_identity = make_synthetic_identity(identity)
        package_version = SYNTHETIC_PACKAGE_VERSION
    else:
        raise ValueError(
            "Client acceptance state is invalid"
        )
    synthetic = not accepted_client

    with tempfile.TemporaryDirectory(
        prefix=f"{target}-offline-acceptance-",
        dir=acceptance_workspace_parent(output_root),
    ) as temporary:
        temporary_root = Path(temporary)
        with release_builder._export_committed_tree(
            repo_root,
            identity,
        ) as source:
            if synthetic:
                synthetic_contract = make_synthetic_contract(
                    json.loads(
                        (
                            source
                            / "runtime"
                            / "release-contract.json"
                        ).read_text(encoding="utf-8")
                    )
                )
                (
                    source
                    / "runtime"
                    / "release-contract.json"
                ).write_bytes(_json_bytes(synthetic_contract))
            first = release_builder.build_release_from_source(
                source,
                temporary_root / "build-a",
                package_version,
                foundation_root,
                release_identity,
            )
            second = release_builder.build_release_from_source(
                source,
                temporary_root / "build-b",
                package_version,
                foundation_root,
                release_identity,
            )
        for left, right in (
            (first.zip_path, second.zip_path),
            (first.manifest_path, second.manifest_path),
            (first.component_lock_path, second.component_lock_path),
        ):
            if left.read_bytes() != right.read_bytes():
                raise RuntimeError("Synthetic package build is not deterministic")

        executables = {
            "pwsh": shutil.which("pwsh"),
            "powershell": shutil.which("powershell.exe"),
        }
        if not all(executables.values()):
            raise RuntimeError(
                "PowerShell 7 and Windows PowerShell 5.1 are required"
            )
        matrix = {
            name: _run_matrix_case(
                executable=str(executable),
                foundation_script=foundation_root / "foundation.ps1",
                package=first.zip_path,
                target=target,
                client_id=client_id,
                client_version=client_version,
                root=temporary_root / "fake-homes",
            )
            for name, executable in executables.items()
        }
        shutil.copytree(first.zip_path.parent, output_root)

    marker = (
        (
            "NON-RELEASABLE OFFLINE SYNTHETIC PACKAGE\n"
            "Client contract: 0.0.0-offline\n"
        )
        if synthetic
        else (
            "OFFLINE-ACCEPTED CANDIDATE; NOT A STABLE RELEASE\n"
            f"Client contract: {client_version}\n"
        )
    ) + "Stable promotion and employee distribution are forbidden.\n"
    (output_root / "NON_RELEASABLE.txt").write_text(
        marker,
        encoding="utf-8",
    )
    asset = {
        "name": first.zip_path.name,
        "sha256": _file_sha256(output_root / first.zip_path.name),
        "bytes": (output_root / first.zip_path.name).stat().st_size,
    }
    report = build_acceptance_report(
        target=target,
        source=release_identity,
        foundation=foundation,
        asset=asset,
        matrix=matrix,
        synthetic=synthetic,
        client_version=client_version,
        release_binding=release_builder.release_binding_from_manifest(
            first.manifest
        ),
    )
    evidence_name = (
        "offline-acceptance.json"
        if synthetic
        else "candidate-acceptance.json"
    )
    evidence_path = output_root / evidence_name
    evidence_path.write_bytes(_json_bytes(report))
    if not synthetic:
        release_builder.bind_candidate_acceptance(
            output_root / "release-manifest.json",
            evidence_path,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--foundation", type=Path, required=True)
    parser.add_argument(
        "--foundation-evidence",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-version")
    arguments = parser.parse_args()
    report = run_acceptance(
        repo_root=arguments.repo.resolve(),
        foundation_root=arguments.foundation.resolve(),
        foundation_evidence=arguments.foundation_evidence.resolve(),
        output_root=arguments.output.resolve(),
        candidate_version=arguments.candidate_version,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
