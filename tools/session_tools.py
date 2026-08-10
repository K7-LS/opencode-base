from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MANIFEST_NAME = "session-tools-manifest.json"
MAX_TOOLS = 32
MAX_FILES = 256
MAX_FILE_BYTES = 1024 * 1024
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_ZIP_BYTES
ALLOWED_EXTENSIONS = {".json", ".md", ".toml", ".txt", ".yaml", ".yml"}
TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
ASSET_NAME = re.compile(r"^session-tools-opencode-([0-9]+\.[0-9]+\.[0-9]+)\.zip$")


@dataclass(frozen=True)
class SessionToolsBuild:
    zip_path: Path
    manifest_bytes: bytes
    manifest: dict[str, object]


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json(payload: bytes) -> object:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicates)
    except UnicodeDecodeError as error:
        raise ValueError("session tools manifest must be UTF-8") from error
    except json.JSONDecodeError as error:
        raise ValueError("session tools manifest JSON is invalid") from error


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("unsafe session tool path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe session tool path")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("session tool extension is not allowed")
    return path.as_posix()


def _validate_tool_id(value: object) -> str:
    if not isinstance(value, str) or TOOL_ID.fullmatch(value) is None:
        raise ValueError("session tool id is invalid")
    return value


def _validate_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "target",
        "release_tag",
        "base_version",
        "tools",
    }:
        raise ValueError("session tools manifest schema differs")
    if (
        not isinstance(value["schema_version"], int)
        or isinstance(value["schema_version"], bool)
        or value["schema_version"] != 1
        or value["target"] != "opencode"
        or not isinstance(value["release_tag"], str)
        or not isinstance(value["base_version"], str)
        or VERSION.fullmatch(value["base_version"]) is None
        or value["release_tag"] != f"opencode-v{value['base_version']}"
        or not isinstance(value["tools"], list)
        or not value["tools"]
        or len(value["tools"]) > MAX_TOOLS
    ):
        raise ValueError("session tools manifest identity or tool count differs")

    previous_tool = ""
    tool_ids: set[str] = set()
    paths: set[str] = set()
    file_count = 0
    total_bytes = 0
    tools: list[dict[str, object]] = []
    for tool in value["tools"]:
        if not isinstance(tool, dict) or set(tool) != {"id", "files"}:
            raise ValueError("session tool schema differs")
        tool_id = _validate_tool_id(tool["id"])
        if tool_id <= previous_tool or tool_id.casefold() in tool_ids:
            raise ValueError("duplicate or unsorted session tool id")
        previous_tool = tool_id
        tool_ids.add(tool_id.casefold())
        files_value = tool["files"]
        if not isinstance(files_value, list) or not files_value:
            raise ValueError("session tool files differ")
        previous_path = ""
        files: list[dict[str, object]] = []
        for file in files_value:
            if not isinstance(file, dict) or set(file) != {"path", "sha256", "bytes"}:
                raise ValueError("session tool file schema differs")
            relative = _safe_relative_path(file["path"])
            file_key = f"{tool_id}/{relative}".casefold()
            if relative <= previous_path or file_key in paths:
                raise ValueError("duplicate or unsorted session tool file path")
            previous_path = relative
            paths.add(file_key)
            digest = file["sha256"]
            size = file["bytes"]
            if (
                not isinstance(digest, str)
                or SHA256.fullmatch(digest) is None
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or size > MAX_FILE_BYTES
            ):
                raise ValueError("session tool file hash or file size differs")
            file_count += 1
            total_bytes += size
            files.append({"path": relative, "sha256": digest, "bytes": size})
        tools.append({"id": tool_id, "files": files})
    if file_count > MAX_FILES:
        raise ValueError("session tool file count exceeds limit")
    if total_bytes > MAX_EXPANDED_BYTES:
        raise ValueError("session tool expanded size exceeds limit")
    return {
        "schema_version": 1,
        "target": "opencode",
        "release_tag": value["release_tag"],
        "base_version": value["base_version"],
        "tools": tools,
    }


def validate_session_tools_manifest(payload: bytes) -> dict[str, object]:
    return _validate_manifest(_strict_json(payload))


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def validate_session_tools_bundle(
    path: Path,
    *,
    manifest_sha256: str | None = None,
) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("session tools ZIP size differs")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            folded = [name.casefold() for name in names]
            if len(names) != len(set(names)) or len(folded) != len(set(folded)):
                raise ValueError("duplicate session tools ZIP entry")
            if (
                not infos
                or any(info.is_dir() or _zip_member_is_symlink(info) for info in infos)
                or sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES
            ):
                raise ValueError("session tools ZIP layout differs")
            for info in infos:
                pure = PurePosixPath(info.filename)
                if (
                    pure.is_absolute()
                    or "\\" in info.filename
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or (info.external_attr >> 16) & 0o111
                ):
                    raise ValueError("unsafe session tools ZIP entry")
            if MANIFEST_NAME not in names:
                raise ValueError("session tools ZIP manifest is missing")
            manifest_bytes = archive.read(MANIFEST_NAME)
            if manifest_sha256 is not None and _sha256(manifest_bytes) != manifest_sha256:
                raise ValueError("session tools manifest SHA-256 differs")
            manifest = validate_session_tools_manifest(manifest_bytes)
            expected = {MANIFEST_NAME}
            for tool in manifest["tools"]:
                for file in tool["files"]:
                    expected.add(f"tools/{tool['id']}/{file['path']}")
            if set(names) != expected:
                raise ValueError("session tools ZIP layout differs")
            for tool in manifest["tools"]:
                for file in tool["files"]:
                    name = f"tools/{tool['id']}/{file['path']}"
                    payload = archive.read(name)
                    if len(payload) != file["bytes"] or _sha256(payload) != file["sha256"]:
                        raise ValueError("session tool file hash differs")
            return manifest
    except zipfile.BadZipFile as error:
        raise ValueError("session tools ZIP is invalid") from error


def validate_session_tools_archive(
    path: Path,
    *,
    manifest_sha256: str | None = None,
) -> dict[str, object]:
    return validate_session_tools_bundle(path, manifest_sha256=manifest_sha256)


def build_session_tools_bundle(
    repo_root: Path,
    dist_root: Path,
    version: str,
) -> SessionToolsBuild:
    if VERSION.fullmatch(version) is None:
        raise ValueError("session tools version is invalid")
    source = repo_root / "skills" / "ru-writing-style"
    if not source.is_dir() or source.is_symlink():
        raise ValueError("session tool source is invalid")
    entries: dict[str, bytes] = {}
    files: list[dict[str, object]] = []
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise ValueError("session tool source contains symlink")
            continue
        relative = _safe_relative_path(path.relative_to(source).as_posix())
        if path.stat().st_mode & 0o111:
            raise ValueError("session tool source contains executable")
        payload = path.read_bytes()
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("session tool source file size exceeds limit")
        entries[f"tools/ru-writing-style/{relative}"] = payload
        files.append({"path": relative, "sha256": _sha256(payload), "bytes": len(payload)})
    manifest: dict[str, object] = {
        "schema_version": 1,
        "target": "opencode",
        "release_tag": f"opencode-v{version}",
        "base_version": version,
        "tools": [{"id": "ru-writing-style", "files": files}],
    }
    manifest = _validate_manifest(manifest)
    manifest_bytes = _json_bytes(manifest)
    dist_root.mkdir(parents=True, exist_ok=True)
    zip_path = dist_root / f"session-tools-opencode-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in [(MANIFEST_NAME, manifest_bytes), *sorted(entries.items())]:
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    if validate_session_tools_bundle(zip_path) != manifest:
        raise AssertionError("session tools bundle validation differs")
    return SessionToolsBuild(zip_path, manifest_bytes, manifest)


def session_tools_asset_record(bundle: SessionToolsBuild) -> dict[str, object]:
    tool_count = len(bundle.manifest["tools"])
    file_count = sum(len(tool["files"]) for tool in bundle.manifest["tools"])
    return {
        "name": bundle.zip_path.name,
        "sha256": _sha256(bundle.zip_path.read_bytes()),
        "bytes": bundle.zip_path.stat().st_size,
        "manifest_sha256": _sha256(bundle.manifest_bytes),
        "tool_count": tool_count,
        "file_count": file_count,
    }


def validate_session_tools_asset_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "name",
        "sha256",
        "bytes",
        "manifest_sha256",
        "tool_count",
        "file_count",
    }:
        raise ValueError("session tools asset record schema differs")
    if (
        not isinstance(value["name"], str)
        or ASSET_NAME.fullmatch(value["name"]) is None
        or not isinstance(value["sha256"], str)
        or SHA256.fullmatch(value["sha256"]) is None
        or not isinstance(value["manifest_sha256"], str)
        or SHA256.fullmatch(value["manifest_sha256"]) is None
        or not isinstance(value["bytes"], int)
        or isinstance(value["bytes"], bool)
        or value["bytes"] <= 0
        or value["bytes"] > MAX_ZIP_BYTES
        or not isinstance(value["tool_count"], int)
        or isinstance(value["tool_count"], bool)
        or not 1 <= value["tool_count"] <= MAX_TOOLS
        or not isinstance(value["file_count"], int)
        or isinstance(value["file_count"], bool)
        or not 1 <= value["file_count"] <= MAX_FILES
    ):
        raise ValueError("session tools asset record differs")
    return dict(value)
