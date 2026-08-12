from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import session_tools


FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
CLIENT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
FULL_VERDICTS = {
    "claude": "FULL_RELEASE_CLAUDE",
    "opencode": "FULL_RELEASE_OPENCODE",
}


@dataclass(frozen=True)
class ReleaseBuild:
    zip_path: Path
    manifest_path: Path
    component_lock_path: Path
    manifest: dict[str, object]


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _build_desired_state(repo_root: Path, contract: dict[str, object]) -> dict[str, object]:
    agents = _load_json(repo_root / "catalog" / "agents.json")
    skills = _load_json(repo_root / "catalog" / "skills.json")
    install_root = str(contract["paths"]["install_root"])
    return {
        "schema_version": 1,
        "client": "opencode",
        "unknown_policy": "prompt-every-run",
        "skills": sorted(str(row["id"]) for row in skills),
        "agents": sorted(str(row["id"]) for row in agents),
        "hooks": ["startup:check-release"],
        "managed_files": [
            str(contract["paths"]["hot_destination"]),
            str(contract["paths"]["config_destination"]),
        ],
        "inventory_roots": [
            f"{install_root}/agents",
            f"{install_root}/commands",
            f"{install_root}/skills",
        ],
        "mcp": [],
        "plugins": [],
        "marketplaces": [],
        "shared_tools": {
            "officecli": "1.0.143",
            "officecli_pdf_exporter": "1.0.0",
        },
        "platform_owned": [],
        "protected_state": [
            ".config/opencode/auth.json",
            ".config/opencode/plugins",
            ".config/opencode/themes",
            ".config/opencode/tools",
            ".local/share/opencode",
            "projects",
        ],
        "retired_ids": [],
        "migrations": [],
    }


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _evidence_body_sha256(value: dict[str, object]) -> str:
    body = dict(value)
    body.pop("evidence_body_sha256", None)
    return _sha256(_json_bytes(body))


def release_binding_from_manifest(
    manifest: dict[str, object],
) -> dict[str, object]:
    required = (
        "target",
        "version",
        "tag",
        "client",
        "asset",
        "package_manifest_sha256",
        "components_lock_sha256",
        "source",
        "foundation_engine_version",
        "foundation_engine_manifest_sha256",
    )
    missing = [name for name in required if name not in manifest]
    if missing:
        raise ValueError(
            "release manifest lacks binding fields: " + ", ".join(missing)
        )
    binding = {name: manifest[name] for name in required}
    if "session_tools_asset" in manifest:
        asset = session_tools.validate_session_tools_asset_record(
            manifest["session_tools_asset"]
        )
        if asset["name"] != f"session-tools-opencode-{manifest['version']}.zip":
            raise ValueError("session tools asset name does not match release version")
        binding["session_tools_asset"] = asset
    return binding


def bind_candidate_acceptance(
    manifest_path: Path,
    evidence_path: Path,
) -> dict[str, object]:
    manifest = _load_json(manifest_path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("channel") != "candidate"
    ):
        raise ValueError("only candidate manifests can bind acceptance")
    manifest["acceptance_evidence_sha256"] = _file_sha256(evidence_path)
    manifest_path.write_bytes(_json_bytes(manifest))
    return manifest


def _tree_files(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and "tests" not in path.parts
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_client_acceptance(
    repo_root: Path,
    contract: dict[str, object],
) -> dict[str, object]:
    evidence = _load_json(repo_root / "runtime" / "client-acceptance.json")
    required = {
        "schema_version",
        "target",
        "verdict",
        "client",
        "distribution",
        "download",
        "binary",
        "desktop",
        "runtime_smoke",
        "limitations",
    }
    client = contract["client"]
    distribution = evidence.get("distribution")
    download = evidence.get("download")
    binary = evidence.get("binary")
    desktop = evidence.get("desktop")
    smoke = evidence.get("runtime_smoke")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != required
        or evidence.get("schema_version") != 1
        or evidence.get("target") != contract["target"]
        or evidence.get("verdict") != "PASS"
        or evidence.get("client")
        != {
            "id": client["id"],
            "version": client["supported_version"],
        }
        or not isinstance(distribution, dict)
        or set(distribution)
        != {
            "method",
            "package_id",
            "package_version",
            "scope",
            "source",
        }
        or distribution["method"] != "official-release-assets"
        or distribution["package_version"] != client["supported_version"]
        or distribution["scope"] != "current-user"
        or not all(
            isinstance(distribution[name], str) and distribution[name]
            for name in ("package_id", "source")
        )
        or not isinstance(download, dict)
        or set(download)
        != {"name", "url", "sha256", "bytes", "archive_entry"}
        or download["name"] != distribution["package_id"]
        or download["url"] != distribution["source"]
        or re.fullmatch(r"[0-9a-f]{64}", str(download["sha256"]))
        is None
        or not isinstance(download["bytes"], int)
        or download["bytes"] <= 0
        or download["archive_entry"] != "opencode.exe"
        or not isinstance(binary, dict)
        or set(binary)
        != {
            "sha256",
            "bytes",
            "authenticode_status",
            "signer",
            "issuer",
            "timestamped",
        }
        or not isinstance(binary["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", binary["sha256"]) is None
        or not isinstance(binary["bytes"], int)
        or binary["bytes"] <= 0
        or binary["authenticode_status"] != "Valid"
        or not isinstance(binary["signer"], str)
        or not binary["signer"]
        or not isinstance(binary["issuer"], str)
        or not binary["issuer"]
        or binary["timestamped"] is not True
        or not isinstance(desktop, dict)
        or set(desktop)
        != {
            "name",
            "url",
            "sha256",
            "bytes",
            "authenticode_status",
            "signer",
            "issuer",
            "timestamped",
            "file_version",
        }
        or desktop["name"] != "opencode-desktop-win-x64.exe"
        or re.fullmatch(r"[0-9a-f]{64}", str(desktop["sha256"]))
        is None
        or not isinstance(desktop["bytes"], int)
        or desktop["bytes"] <= 0
        or desktop["authenticode_status"] != "Valid"
        or not isinstance(desktop["signer"], str)
        or not desktop["signer"]
        or not isinstance(desktop["issuer"], str)
        or not desktop["issuer"]
        or desktop["timestamped"] is not True
        or desktop["file_version"] != client["supported_version"]
        or not isinstance(smoke, dict)
        or set(smoke)
        != {
            "version_command",
            "version_output",
            "version_exit_code",
            "help_exit_code",
            "model_requests",
            "provider_login",
        }
        or not isinstance(smoke["version_command"], list)
        or not smoke["version_command"]
        or smoke["version_exit_code"] != 0
        or smoke["help_exit_code"] != 0
        or smoke["model_requests"] != 0
        or smoke["provider_login"] != "NOT_RUN"
        or not isinstance(evidence["limitations"], list)
        or not evidence["limitations"]
    ):
        raise ValueError("client binary acceptance evidence differs")
    return evidence


def load_release_contract(repo_root: Path) -> dict[str, object]:
    contract = _load_json(repo_root / "runtime" / "release-contract.json")
    required = {
        "schema_version",
        "target",
        "repository",
        "transformation",
        "tag_prefix",
        "client",
        "paths",
        "environment",
    }
    if (
        not isinstance(contract, dict)
        or set(contract) != required
        or contract["schema_version"] != 1
    ):
        raise ValueError("release contract schema differs")
    target = str(contract["target"])
    if target not in FULL_VERDICTS:
        raise ValueError("unsupported target release contract")
    client = contract["client"]
    if not isinstance(client, dict) or set(client) != {
        "id",
        "supported_version",
        "acceptance",
    }:
        raise ValueError("client release contract schema differs")
    if client["acceptance"] != "PASS":
        raise ValueError(
            f"{target} client binary contract is not accepted"
        )
    supported = client["supported_version"]
    if not isinstance(supported, str) or not CLIENT_VERSION.fullmatch(supported):
        raise ValueError("accepted client version is invalid")
    environment = contract["environment"]
    if not isinstance(environment, dict) or set(environment) != {"scope", "set"}:
        raise ValueError("environment release contract schema differs")
    if environment["scope"] != "current-user" or not isinstance(
        environment["set"], list
    ):
        raise ValueError("environment release contract is invalid")
    previous = ""
    for row in environment["set"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"name", "value"}
            or row["name"] <= previous
            or row["name"] != "OPENCODE_DISABLE_CLAUDE_CODE"
            or not isinstance(row["value"], str)
        ):
            raise ValueError("environment release row is invalid")
        previous = row["name"]
    if target == "opencode" and environment["set"] != [
        {"name": "OPENCODE_DISABLE_CLAUDE_CODE", "value": "1"}
    ]:
        raise ValueError("OpenCode isolation environment is required")
    if target == "claude" and environment["set"]:
        raise ValueError("Claude release must not mutate user environment")
    load_client_acceptance(repo_root, contract)
    return contract


def _git(repo_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or "git failed").strip())
    return result.stdout.strip()


def git_source_identity(repo_root: Path) -> dict[str, str]:
    contract = _load_json(repo_root / "runtime" / "release-contract.json")
    return {
        "repository": str(contract["repository"]),
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "tree": _git(repo_root, "rev-parse", "HEAD^{tree}"),
        "transformation": str(contract["transformation"]),
    }


def assert_clean_git_source(repo_root: Path) -> dict[str, str]:
    for name in (
        "agents",
        "catalog",
        "cold",
        "commands",
        "control-skills",
        "runtime",
        "skills",
    ):
        root = repo_root / name
        if not root.exists():
            raise ValueError(f"release source is missing: {name}")
        for path in [root, *root.rglob("*")]:
            if path.is_symlink() or bool(
                getattr(path.lstat(), "st_file_attributes", 0) & 0x400
            ):
                raise ValueError(f"release source contains a reparse point: {path}")
    if _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ):
        raise ValueError("release acceptance requires a clean Git worktree")
    return git_source_identity(repo_root)


@contextmanager
def _export_committed_tree(
    repo_root: Path,
    identity: dict[str, str],
) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="llm-base-release-") as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / "source.zip"
        result = subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                f"--output={archive_path}",
                identity["commit"],
            ],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ValueError("Git source export failed")
        source = temporary_root / "source"
        source.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                pure = PurePosixPath(info.filename)
                if pure.is_absolute() or ".." in pure.parts or "\\" in info.filename:
                    raise ValueError("Git archive contains an unsafe path")
                destination = source.joinpath(*pure.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
        yield source


def _component(
    repo_root: Path,
    component_id: str,
    files: list[Path],
    provenance: dict[str, object],
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(repo_root).as_posix()):
        relative = path.relative_to(repo_root).as_posix()
        file_hash = _file_sha256(path)
        rows.append(
            {"path": relative, "sha256": file_hash, "bytes": path.stat().st_size}
        )
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    if not rows:
        raise ValueError(f"component has no files: {component_id}")
    return {
        "id": component_id,
        "source": provenance,
        "sha256": digest.hexdigest(),
        "files": rows,
    }


def build_component_lock(
    repo_root: Path,
    version: str,
    identity: dict[str, str],
) -> dict[str, object]:
    provenance = {
        "native_repository": identity,
    }
    agents = _load_json(repo_root / "catalog" / "agents.json")
    skills = _load_json(repo_root / "catalog" / "skills.json")
    cold = _load_json(repo_root / "catalog" / "cold.json")
    if not isinstance(agents, list) or not isinstance(skills, list):
        raise ValueError("component catalogs are invalid")
    managed_skills = [row for row in skills if row["id"] != "ru-writing-style"]
    groups = {
        "hot": [
            _component(
                repo_root,
                "hot",
                [repo_root / str(load_release_contract(repo_root)["paths"]["hot_source"])],
                provenance,
            )
        ],
        "agents": [
            _component(
                repo_root,
                str(row["id"]),
                [repo_root / str(row["source"])],
                provenance,
            )
            for row in agents
        ],
        "skills": [
            _component(
                repo_root,
                str(row["id"]),
                _tree_files(repo_root / "skills" / str(row["id"])),
                provenance,
            )
            for row in managed_skills
        ],
        "control_skills": [
            _component(repo_root, path.name, _tree_files(path), provenance)
            for path in sorted((repo_root / "control-skills").iterdir())
            if path.is_dir()
        ],
        "commands": [
            _component(repo_root, path.stem, [path], provenance)
            for path in sorted((repo_root / "commands").glob("*.md"))
        ],
        "cold": [
            _component(repo_root, value, [repo_root / "cold" / value], provenance)
            for group in ("memory", "chains")
            for value in cold[group]
        ],
        "runtime": [
            _component(repo_root, "runtime", _tree_files(repo_root / "runtime"), provenance)
        ],
    }
    contract = load_release_contract(repo_root)
    return {
        "schema_version": 1,
        "target": contract["target"],
        "version": version,
        "provenance": provenance,
        "components": groups,
    }


def _validate_foundation(root: Path) -> tuple[str, str]:
    required = [root / "VERSION", root / "foundation.ps1", root / "engine-manifest.json"]
    if not all(path.is_file() for path in required):
        raise ValueError("Foundation engine is missing accepted files")
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    manifest = _load_json(root / "engine-manifest.json")
    if (
        manifest.get("engine_version") != version
        or manifest.get("network") != "offline"
        or manifest.get("commands")
        != ["doctor", "install", "inventory", "plan", "rollback"]
        or manifest.get("supported_powershell") != ["5.1", "7"]
        or manifest.get("foundation_ps1_sha256")
        != _file_sha256(root / "foundation.ps1")
    ):
        raise ValueError("Foundation engine contract differs")
    return version, _file_sha256(root / "engine-manifest.json")


def _add(entries: dict[str, bytes], destination: str, payload: bytes) -> None:
    pure = PurePosixPath(destination)
    if pure.is_absolute() or ".." in pure.parts or "\\" in destination:
        raise ValueError(f"unsafe package path: {destination}")
    if destination in entries:
        raise ValueError(f"duplicate package path: {destination}")
    entries[destination] = payload


def _add_tree(
    entries: dict[str, bytes],
    source: Path,
    destination: str,
    *,
    exclude: set[Path] | None = None,
) -> None:
    excluded = {path.resolve() for path in (exclude or set())}
    for path in _tree_files(source):
        if path.resolve() in excluded:
            continue
        _add(
            entries,
            str(PurePosixPath(destination) / path.relative_to(source).as_posix()),
            path.read_bytes(),
        )


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entries[name])


def build_release_from_source(
    repo_root: Path,
    dist_root: Path,
    version: str,
    foundation_root: Path,
    identity: dict[str, str],
) -> ReleaseBuild:
    if not VERSION.fullmatch(version):
        raise ValueError("release version is invalid")
    contract = load_release_contract(repo_root)
    foundation_version, foundation_manifest_hash = _validate_foundation(
        foundation_root
    )
    managed = _load_json(repo_root / "runtime" / "managed-surface.json")
    if managed.get("target") != contract["target"]:
        raise ValueError("managed surface target differs")
    paths = contract["paths"]
    install_root = str(paths["install_root"])
    session_bundle = session_tools.build_session_tools_bundle(
        repo_root,
        dist_root,
        version,
    )
    session_asset = session_tools.session_tools_asset_record(session_bundle)
    session_baseline = {
        "manifest_path": "session-tools-baseline/session-tools-manifest.json",
        "manifest_sha256": session_asset["manifest_sha256"],
        "tools": session_bundle.manifest["tools"],
        "retired_tool_ids": [],
    }
    component_lock = build_component_lock(repo_root, version, identity)
    lock_bytes = _json_bytes(component_lock)

    entries: dict[str, bytes] = {}
    _add(entries, str(paths["hot_destination"]), (repo_root / str(paths["hot_source"])).read_bytes())
    _add(entries, str(paths["config_destination"]), (repo_root / str(paths["config_source"])).read_bytes())
    _add(entries, f"{install_root}/base/VERSION", (version + "\n").encode())
    _add(entries, f"{install_root}/base/components.lock.json", lock_bytes)
    _add(
        entries,
        f"{install_root}/base/desired-state.json",
        _json_bytes(_build_desired_state(repo_root, contract)),
    )
    _add_tree(entries, repo_root / "agents", f"{install_root}/agents")
    _add_tree(
        entries,
        repo_root / "skills",
        f"{install_root}/skills",
        exclude=set((repo_root / "skills" / "ru-writing-style").rglob("*")),
    )
    _add_tree(entries, repo_root / "control-skills", f"{install_root}/skills")
    _add(
        entries,
        f"{install_root}/skills/sync-base/runtime/connection.ps1",
        (repo_root / "runtime" / "connection.ps1").read_bytes(),
    )
    _add_tree(entries, repo_root / "commands", f"{install_root}/commands")
    _add_tree(entries, repo_root / "cold", f"{install_root}/base/cold")
    _add_tree(
        entries,
        repo_root / "runtime",
        f"{install_root}/base/runtime",
        exclude={repo_root / str(paths["config_source"])},
    )
    _add(
        entries,
        f"{install_root}/base/runtime/session-tools-baseline.json",
        session_bundle.manifest_bytes,
    )
    _add_tree(
        entries,
        foundation_root,
        f"{install_root}/base/foundation/{foundation_version}",
    )
    _add(
        entries,
        "session-tools-baseline/session-tools-manifest.json",
        session_bundle.manifest_bytes,
    )
    with zipfile.ZipFile(session_bundle.zip_path) as session_archive:
        for name in session_archive.namelist():
            if name != session_tools.MANIFEST_NAME:
                _add(entries, f"session-tools-baseline/{name}", session_archive.read(name))

    files = [
        {"path": name, "sha256": _sha256(payload), "bytes": len(payload)}
        for name, payload in sorted(entries.items())
    ]
    package_manifest = {
        "schema_version": 1,
        "target": contract["target"],
        "version": version,
        "client": {
            "id": contract["client"]["id"],
            "supported_version": contract["client"]["supported_version"],
        },
        "foundation_engine_version": foundation_version,
        "managed_surface": {
            "exact_directories": managed["exact_directories"],
            "replace_files": managed["replace_files"],
            "preserved_paths": managed["preserved_paths"],
        },
        "sync_policy": {
            "direction": "hub-to-consumer",
            "consumer_feedback_upload": False,
            "consumer_push": False,
            "consumer_session_upload": False,
            "credentials_included": False,
        },
        "environment": contract["environment"],
        "desired_state": {
            "schema_version": 1,
            "unknown_policy": "prompt-every-run",
            "local_exceptions": True,
            "strict_doctor": True,
            "inventory_roots": [
                f"{install_root}/agents",
                f"{install_root}/commands",
                f"{install_root}/skills",
            ],
            "platform_owned": [],
            "toml_reconcile": [],
        },
        "session_tools_baseline": session_baseline,
        "files": files,
    }
    package_manifest_bytes = _json_bytes(package_manifest)
    _add(entries, "package-manifest.json", package_manifest_bytes)

    dist_root.mkdir(parents=True, exist_ok=True)
    target = str(contract["target"])
    zip_path = dist_root / f"{target}-base-{version}.zip"
    _write_zip(zip_path, entries)
    manifest = {
        "schema_version": 1,
        "target": target,
        "version": version,
        "tag": f"{contract['tag_prefix']}{version}",
        "channel": "candidate",
        "client": {
            "id": contract["client"]["id"],
            "supported_version": contract["client"]["supported_version"],
        },
        "foundation_engine_version": foundation_version,
        "foundation_engine_manifest_sha256": foundation_manifest_hash,
        "source": identity,
        "asset": {
            "name": zip_path.name,
            "sha256": _file_sha256(zip_path),
            "bytes": zip_path.stat().st_size,
        },
        "session_tools_asset": session_asset,
        "package_manifest_sha256": _sha256(package_manifest_bytes),
        "components_lock_sha256": _sha256(lock_bytes),
        "requires": {
            "immutable_release": True,
            "release_attestation": True,
            "verification_commands": [
                f"gh release verify {contract['tag_prefix']}{version} -R {str(contract['repository']).removeprefix('https://github.com/')}",
                f"gh release verify-asset {contract['tag_prefix']}{version} {zip_path.name} -R {str(contract['repository']).removeprefix('https://github.com/')}",
            ],
        },
    }
    manifest_path = dist_root / "release-manifest.json"
    lock_path = dist_root / "components.lock.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    lock_path.write_bytes(lock_bytes)
    return ReleaseBuild(zip_path, manifest_path, lock_path, manifest)


def build_release(
    repo_root: Path,
    dist_root: Path,
    version: str,
    foundation_root: Path,
) -> ReleaseBuild:
    identity = assert_clean_git_source(repo_root)
    with _export_committed_tree(repo_root, identity) as source:
        return build_release_from_source(
            source,
            dist_root,
            version,
            foundation_root,
            identity,
        )


def create_package_acceptance(
    stable_manifest_path: Path,
    evidence_path: Path,
    release_verification_path: Path,
    output_path: Path,
) -> dict[str, object]:
    stable = _load_json(stable_manifest_path)
    evidence = _load_json(evidence_path)
    verification = _load_json(release_verification_path)
    target = str(stable.get("target"))
    verdict = FULL_VERDICTS.get(target)
    binding = release_binding_from_manifest(stable)
    if (
        stable.get("channel") != "stable"
        or verdict is None
        or evidence.get("target") != target
        or evidence.get("version") != stable.get("version")
        or evidence.get("release_binding") != binding
        or evidence.get("evidence_body_sha256")
        != _evidence_body_sha256(evidence)
        or evidence.get("verdicts", {}).get(verdict) != "PASS"
        or evidence.get("verdicts", {}).get("RELEASE_INTEGRITY")
        != "PENDING_PUBLICATION"
        or evidence.get("asset_sha256") != stable.get("asset", {}).get("sha256")
        or stable.get("requires", {}).get("immutable_release") is not True
        or stable.get("requires", {}).get("release_attestation") is not True
    ):
        raise ValueError("stable release acceptance evidence is incomplete")
    asset_path = stable_manifest_path.parent / stable["asset"]["name"]
    if (
        not asset_path.is_file()
        or _file_sha256(asset_path) != stable["asset"]["sha256"]
        or asset_path.stat().st_size != stable["asset"]["bytes"]
    ):
        raise ValueError("stable release asset binding differs")
    expected_repository = str(
        stable.get("source", {}).get("repository", "")
    ).removeprefix("https://github.com/").rstrip("/")
    expected_verified_asset = {
        **stable["asset"],
        "attestation": "PASS",
    }
    if (
        not isinstance(verification, dict)
        or verification.get("schema_version") != 1
        or verification.get("repository") != expected_repository
        or verification.get("tag") != stable.get("tag")
        or verification.get("release_state")
        != {
            "draft": False,
            "prerelease": False,
            "immutable": True,
        }
        or verification.get("release_attestation") != "PASS"
        or verification.get("assets") != [expected_verified_asset]
        or verification.get("RELEASE_INTEGRITY") != "PASS"
        or verification.get("evidence_body_sha256")
        != _evidence_body_sha256(verification)
    ):
        raise ValueError("stable release acceptance evidence is incomplete")
    result = {
        "schema_version": 1,
        "target": target,
        "package_acceptance": "PASS",
        "client": stable["client"],
        "asset": stable["asset"],
        "release_manifest": {
            "name": stable_manifest_path.name,
            "sha256": _file_sha256(stable_manifest_path),
            "bytes": stable_manifest_path.stat().st_size,
        },
        "acceptance_evidence": {
            "name": evidence_path.name,
            "sha256": _file_sha256(evidence_path),
            "bytes": evidence_path.stat().st_size,
        },
        "release_verification": {
            "name": release_verification_path.name,
            "sha256": _file_sha256(release_verification_path),
            "bytes": release_verification_path.stat().st_size,
        },
        "immutable_release": True,
        "release_attestation": True,
    }
    output_path.write_bytes(_json_bytes(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--foundation", type=Path, required=True)
    arguments = parser.parse_args()
    built = build_release(
        arguments.repo.resolve(),
        arguments.dist.resolve(),
        arguments.version,
        arguments.foundation.resolve(),
    )
    print(json.dumps({"status": "BUILT", "asset": str(built.zip_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
