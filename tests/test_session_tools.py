from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opencode_session_tools",
    ROOT / "tools" / "session_tools.py",
)
assert SPEC and SPEC.loader
session_tools = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = session_tools
SPEC.loader.exec_module(session_tools)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_bundle(path: Path, manifest: str | bytes, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("session-tools-manifest.json", manifest)
        for name, payload in entries.items():
            archive.writestr(name, payload)


def _manifest(*, files: list[dict[str, object]], tools: list[dict[str, object]] | None = None) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "target": "opencode",
            "release_tag": "opencode-v0.1.2",
            "base_version": "0.1.2",
            "tools": tools
            if tools is not None
            else [{"id": "ru-writing-style", "files": files}],
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def test_session_tools_bundle_is_deterministic_and_contains_only_approved_skill(
    tmp_path: Path,
):
    first = session_tools.build_session_tools_bundle(ROOT, tmp_path / "one", "0.1.2")
    second = session_tools.build_session_tools_bundle(ROOT, tmp_path / "two", "0.1.2")

    assert first.zip_path.name == "session-tools-opencode-0.1.2.zip"
    assert first.zip_path.read_bytes() == second.zip_path.read_bytes()
    assert first.manifest == second.manifest
    assert first.manifest == {
        "schema_version": 1,
        "target": "opencode",
        "release_tag": "opencode-v0.1.2",
        "base_version": "0.1.2",
        "tools": [
            {
                "id": "ru-writing-style",
                "files": [
                    {
                        "path": "SKILL.md",
                        "sha256": "a20f25a852eaff976c9db90929c94f5658acb3a71eb264479f6d354e04a10938",
                        "bytes": 20003,
                    }
                ],
            }
        ],
    }
    with zipfile.ZipFile(first.zip_path) as archive:
        assert archive.namelist() == [
            "session-tools-manifest.json",
            "tools/ru-writing-style/SKILL.md",
        ]
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert archive.read("tools/ru-writing-style/SKILL.md") == (
            ROOT / "skills" / "ru-writing-style" / "SKILL.md"
        ).read_bytes()
    assert session_tools.validate_session_tools_bundle(first.zip_path) == first.manifest


def test_session_tools_builder_rejects_unsafe_version_and_asset_record_booleans(
    tmp_path: Path,
):
    with pytest.raises(ValueError, match="version"):
        session_tools.build_session_tools_bundle(ROOT, tmp_path, "../0.1.2")
    record = {
        "name": "session-tools-opencode-0.1.2.zip",
        "sha256": "a" * 64,
        "bytes": 1,
        "manifest_sha256": "b" * 64,
        "tool_count": True,
        "file_count": 1,
    }
    with pytest.raises(ValueError, match="record"):
        session_tools.validate_session_tools_asset_record(record)


def test_session_tools_rejects_windows_drive_and_asset_name_traversal():
    payload = b"x"
    with pytest.raises(ValueError, match="unsafe"):
        session_tools.validate_session_tools_manifest(
            _manifest(
                files=[
                    {
                        "path": "C:/SKILL.md",
                        "sha256": _sha256(payload),
                        "bytes": len(payload),
                    }
                ]
            ).encode("utf-8")
        )
    with pytest.raises(ValueError, match="record"):
        session_tools.validate_session_tools_asset_record(
            {
                "name": "session-tools-opencode-../../pwn.zip",
                "sha256": "a" * 64,
                "bytes": 1,
                "manifest_sha256": "b" * 64,
                "tool_count": 1,
                "file_count": 1,
            }
        )


@pytest.mark.parametrize(
    ("name", "manifest", "entries", "error"),
    [
        (
            "duplicate_json_key",
            b'{"schema_version":1,"schema_version":1}',
            {},
            "duplicate JSON key",
        ),
        (
            "unknown_manifest_field",
            _manifest(files=[]).replace('"schema_version": 1,', '"schema_version": 1,\n  "extra": true,'),
            {},
            "schema differs",
        ),
        (
            "path_traversal",
            _manifest(
                files=[{"path": "../SKILL.md", "sha256": _sha256(b"x"), "bytes": 1}]
            ),
            {"tools/ru-writing-style/../SKILL.md": b"x"},
            "unsafe",
        ),
        (
            "executable_extension",
            _manifest(
                files=[{"path": "run.ps1", "sha256": _sha256(b"x"), "bytes": 1}]
            ),
            {"tools/ru-writing-style/run.ps1": b"x"},
            "extension",
        ),
        (
            "windows_case_collision",
            _manifest(
                files=[
                    {"path": "A.md", "sha256": _sha256(b"a"), "bytes": 1},
                    {"path": "a.md", "sha256": _sha256(b"b"), "bytes": 1},
                ]
            ),
            {"tools/ru-writing-style/A.md": b"a", "tools/ru-writing-style/a.md": b"b"},
            "duplicate",
        ),
        (
            "hash_tamper",
            _manifest(
                files=[{"path": "SKILL.md", "sha256": _sha256(b"expected"), "bytes": 8}]
            ),
            {"tools/ru-writing-style/SKILL.md": b"tampered"},
            "hash",
        ),
        (
            "unknown_zip_entry",
            _manifest(files=[]),
            {"unexpected.txt": b"x"},
            "files differ",
        ),
    ],
)
def test_session_tools_validator_rejects_untrusted_or_unsafe_asset(
    tmp_path: Path,
    name: str,
    manifest: str | bytes,
    entries: dict[str, bytes],
    error: str,
):
    path = tmp_path / f"{name}.zip"
    _write_bundle(path, manifest, entries)
    with pytest.raises(ValueError, match=error):
        session_tools.validate_session_tools_bundle(path)


def test_session_tools_validator_rejects_duplicate_zip_names_and_design_limits(tmp_path: Path):
    payload = b"x"
    record = {"path": "SKILL.md", "sha256": _sha256(payload), "bytes": 1}
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("session-tools-manifest.json", _manifest(files=[record]))
            archive.writestr("tools/ru-writing-style/SKILL.md", payload)
            archive.writestr("tools/ru-writing-style/SKILL.md", payload)
    with pytest.raises(ValueError, match="duplicate"):
        session_tools.validate_session_tools_bundle(duplicate)

    too_many_tools = _manifest(
        files=[],
        tools=[{"id": f"tool-{index}", "files": []} for index in range(33)],
    )
    path = tmp_path / "too-many-tools.zip"
    _write_bundle(path, too_many_tools, {})
    with pytest.raises(ValueError, match="tool count"):
        session_tools.validate_session_tools_bundle(path)

    too_large = b"x" * (1024 * 1024 + 1)
    path = tmp_path / "too-large.zip"
    _write_bundle(
        path,
        _manifest(
            files=[
                {"path": "SKILL.md", "sha256": _sha256(too_large), "bytes": len(too_large)}
            ]
        ),
        {"tools/ru-writing-style/SKILL.md": too_large},
    )
    with pytest.raises(ValueError, match="file size"):
        session_tools.validate_session_tools_bundle(path)


def test_session_tools_validator_rejects_symlinks_and_executable_members(tmp_path: Path):
    payload = b"safe"
    manifest = _manifest(
        files=[{"path": "SKILL.md", "sha256": _sha256(payload), "bytes": len(payload)}]
    )
    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr("session-tools-manifest.json", manifest)
        info = zipfile.ZipInfo("tools/ru-writing-style/SKILL.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    with pytest.raises(ValueError):
        session_tools.validate_session_tools_bundle(symlink)

    executable = tmp_path / "executable.zip"
    with zipfile.ZipFile(executable, "w") as archive:
        archive.writestr("session-tools-manifest.json", manifest)
        info = zipfile.ZipInfo("tools/ru-writing-style/SKILL.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(info, payload)
    with pytest.raises(ValueError):
        session_tools.validate_session_tools_bundle(executable)


def test_session_tools_builder_rejects_symlink_and_executable_source(tmp_path: Path):
    source = tmp_path / "source" / "skills" / "ru-writing-style"
    source.mkdir(parents=True)
    skill = source / "SKILL.md"
    skill.write_text("safe\n", encoding="utf-8")
    link = source / "linked.md"
    try:
        os.symlink(skill, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink"):
        session_tools.build_session_tools_bundle(tmp_path / "source", tmp_path / "dist", "0.1.2")
