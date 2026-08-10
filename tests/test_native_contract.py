from __future__ import annotations

import json
import importlib.util
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_AGENTS = {
    "audit-rd-section",
    "auditor",
    "designer",
    "excel-validator",
    "expertiza-responder",
    "id-engineer",
    "kp-writer",
    "letter-writer",
    "norm-lookup",
    "pdf-reviewer",
    "pto-engineer",
    "pyrevit-engineer",
    "rd-coordinator",
    "smetchik",
    "snabzhenets",
    "word-checker",
}


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    marker = text.find("\n---\n", 4)
    assert marker > 0, path
    return text[4:marker]


def _scalar(frontmatter: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", frontmatter)
    assert match, f"missing {key}"
    return match.group(1).strip().strip("\"'")


def test_opencode_hot_layer_is_native_compact_and_provider_neutral():
    hot = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert len(hot.encode("utf-8")) <= 4500
    assert ".claude" not in hot.lower()
    assert ".codex" not in hot.lower()
    assert "kimi" not in hot.lower()
    assert "простой разговор" in hot.lower()
    assert "provider" in hot.lower()

    config = json.loads(
        (ROOT / "runtime" / "opencode.json").read_text(encoding="utf-8")
    )
    assert config["share"] == "disabled"
    assert config["permission"]["skill"] == "allow"
    assert "model" not in config
    assert "small_model" not in config
    assert "provider" not in config


def test_opencode_runtime_surfaces_do_not_use_other_client_tool_vocabulary():
    surfaces = [
        ROOT / "AGENTS.md",
        *(ROOT / "agents").glob("*.md"),
        *(ROOT / "commands").glob("*.md"),
        *(ROOT / "control-skills").rglob("*"),
        *(ROOT / "runtime").rglob("*"),
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in surfaces
        if path.is_file()
    ).lower()
    for forbidden in ("askuserquestion", ".claude", ".codex", "kimi"):
        assert forbidden not in text


def test_skill_development_uses_material_learning_not_session_ritual():
    text = (
        ROOT / "skills" / "skill-development" / "SKILL.md"
    ).read_text(encoding="utf-8")
    lowered = text.lower()
    assert "обновляй скиллы каждую сессию" not in lowered
    assert "конец сессии — спросил" not in lowered
    assert "material reusable learning" in lowered
    assert len(text.split()) <= 500


def test_opencode_has_exact_native_agent_and_skill_catalogs():
    agents = {path.stem for path in (ROOT / "agents").glob("*.md")}
    skills = {
        path.parent.name
        for path in (ROOT / "skills").glob("*/SKILL.md")
    }
    assert agents == EXPECTED_AGENTS
    assert len(skills) == 38

    for path in sorted((ROOT / "agents").glob("*.md")):
        frontmatter = _frontmatter(path)
        assert _scalar(frontmatter, "mode") == "subagent"
        assert len(_scalar(frontmatter, "description")) <= 240, path
        assert not re.search(r"(?m)^model:", frontmatter), path

    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        frontmatter = _frontmatter(path)
        assert _scalar(frontmatter, "name") == path.parent.name
        description_length = len(_scalar(frontmatter, "description"))
        if path.parent.name == "ru-writing-style":
            assert description_length == 531
        else:
            assert 1 <= description_length <= 180, path

    catalog = json.loads(
        (ROOT / "catalog" / "agents.json").read_text(encoding="utf-8")
    )
    assert all((ROOT / row["source"]).is_file() for row in catalog)


def test_opencode_migration_provenance_names_every_ported_component():
    migration = json.loads(
        (ROOT / "MIGRATION-SOURCE.json").read_text(encoding="utf-8")
    )
    inventory = migration["inventory"]
    assert set(inventory["agents"]) == EXPECTED_AGENTS
    assert set(inventory["skills"]) == {
        path.parent.name
        for path in (ROOT / "skills").glob("*/SKILL.md")
    }
    assert len(inventory["cold"]) == 23
    assert all((ROOT / "cold" / path).is_file() for path in inventory["cold"])
    assert set(inventory["commands"]) == {
        path.stem for path in (ROOT / "commands").glob("*.md")
    }
    assert inventory["control_skills"] == ["sync-base"]


def test_opencode_managed_surface_is_native_and_preserves_state():
    managed = json.loads(
        (ROOT / "runtime" / "managed-surface.json").read_text(encoding="utf-8")
    )
    all_paths = managed["replace_files"] + managed["exact_directories"]
    assert all(path.startswith(".config/opencode/") for path in all_paths)
    assert not [path for path in all_paths if ".claude" in path or ".codex" in path]

    preserved = set(managed["preserved_paths"])
    assert ".local/share/opencode" in preserved
    assert ".config/opencode/auth.json" in preserved
    assert ".config/opencode/plugins" in preserved
    assert ".config/opencode/skills" not in managed["exact_directories"]
    assert ".config/opencode/skills/ru-writing-style" not in managed["exact_directories"]
    expected_managed_skills = {
        f".config/opencode/skills/{path.parent.name}"
        for path in (ROOT / "skills").glob("*/SKILL.md")
        if path.parent.name != "ru-writing-style"
    }
    expected_managed_skills.add(".config/opencode/skills/sync-base")
    assert expected_managed_skills <= set(managed["exact_directories"])

    runtime_text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (ROOT / "runtime").rglob("*")
        if path.is_file()
    )
    for forbidden in ("auto-push", "feedback-pending", "session-report", "http.post"):
        assert forbidden not in runtime_text

    release = json.loads(
        (ROOT / "runtime" / "release-contract.json").read_text(encoding="utf-8")
    )
    assert release["client"]["acceptance"] == "PASS"
    assert release["client"]["supported_version"] == "1.18.7"
    client_evidence = json.loads(
        (ROOT / "runtime" / "client-acceptance.json").read_text(
            encoding="utf-8"
        )
    )
    assert client_evidence["verdict"] == "PASS"
    assert client_evidence["client"] == {
        "id": "opencode",
        "version": "1.18.7",
    }
    assert client_evidence["binary"]["authenticode_status"] == "Valid"
    assert client_evidence["distribution"]["method"] == (
        "official-release-assets"
    )
    assert client_evidence["download"] == {
        "name": "opencode-windows-x64.zip",
        "url": "https://github.com/anomalyco/opencode/releases/download/v1.18.7/opencode-windows-x64.zip",
        "sha256": "54598e262c0744e6c3b9ddba85764917a48d366a9aa6c817c2feb9d34b3f1105",
        "bytes": 59436082,
        "archive_entry": "opencode.exe",
    }
    assert client_evidence["binary"]["sha256"] == (
        "b7b469b83cc3561e5129a1803b746f7e2c1974297909f5b346398dc9c56a477e"
    )
    assert client_evidence["binary"]["signer"] == (
        "Anomaly Innovations, Inc https://anoma.ly/"
    )
    assert client_evidence["desktop"]["sha256"] == (
        "d44d535d4f3ac0dafcca8cbbf2bad6e0baefb089352a795fc57268337bdea378"
    )
    assert client_evidence["runtime_smoke"]["model_requests"] == 0
    assert release["environment"] == {
        "scope": "current-user",
        "set": [
            {
                "name": "OPENCODE_DISABLE_CLAUDE_CODE",
                "value": "1",
            }
        ],
    }
    connection = ROOT / "runtime" / "connection.ps1"
    assert connection.is_file()
    hook = (
        ROOT / "runtime" / "hooks" / "check-release.ps1"
    ).read_text(encoding="utf-8")
    assert "connection.ps1" in hook
    assert "Invoke-WithLlmConnection" in hook


def test_opencode_managed_surface_arrays_are_foundation_canonical():
    managed = json.loads(
        (ROOT / "runtime" / "managed-surface.json").read_text(encoding="utf-8")
    )
    for name in (
        "exact_directories",
        "replace_files",
        "preserved_paths",
    ):
        values = managed[name]
        assert values == sorted(values), f"{name} is not ordinal-sorted"
        assert len(values) == len({value.casefold() for value in values})


def test_opencode_release_status_stays_fail_closed_before_canary():
    status = json.loads((ROOT / "STATUS.json").read_text(encoding="utf-8"))
    assert status["TARGET_IMPLEMENTATION"] == "IN_PROGRESS"
    assert status["OPENCODE_CANARY"] == "NOT_RUN"
    assert status["FULL_RELEASE_OPENCODE"] == "NOT_PASS"


def test_opencode_static_token_budget_passes_without_claiming_live_ab():
    path = ROOT / "tools" / "token_audit.py"
    spec = importlib.util.spec_from_file_location("opencode_token_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.audit_static_context(ROOT, "opencode")
    assert report["results"]["STATIC_TOKEN_ACCEPTANCE"] == "PASS"
    assert report["results"]["base_controlled_startup_reduction"] >= 0.70
    assert report["results"]["MATCHED_AB"] == "NOT_RUN"
    assert report["candidate"]["cold_payload_in_startup"] is False
    assert report["candidate"]["surfaces"]["agents_discovery"]["count"] == 16
    assert report["candidate"]["surfaces"]["skills_discovery"] == {
        "bytes": report["candidate"]["surfaces"]["skills_discovery"]["bytes"],
        "sha256": report["candidate"]["surfaces"]["skills_discovery"]["sha256"],
        "logical_root": "~/.config/opencode/skills",
        "count": 39,
        "capability_skills": 38,
        "control_skills": 1,
    }


def test_opencode_imports_exact_approved_russian_writing_skill_and_cold_officecli_reference():
    skill = ROOT / "skills" / "ru-writing-style" / "SKILL.md"
    payload = skill.read_bytes()
    assert len(payload) == 20003
    assert hashlib.sha256(payload).hexdigest() == (
        "a20f25a852eaff976c9db90929c94f5658acb3a71eb264479f6d354e04a10938"
    )

    skills_catalog = json.loads(
        (ROOT / "catalog" / "skills.json").read_text(encoding="utf-8")
    )
    assert any(
        row == {
            "id": "ru-writing-style",
            "name": "ru-writing-style",
            "description": (
                "Use when пишешь или правишь русский текст для человека — письмо, КП, "
                "пояснительную записку, ответ экспертизе, отчёт, ТЗ, статью."
            ),
            "source": "skills/ru-writing-style/SKILL.md",
            "required_capabilities": [],
        }
        for row in skills_catalog
    )

    cold_catalog = json.loads(
        (ROOT / "catalog" / "cold.json").read_text(encoding="utf-8")
    )
    officecli_reference = "memory/reference_officecli.md"
    assert officecli_reference in cold_catalog["memory"]
    reference = ROOT / "cold" / officecli_reference
    assert reference.is_file()
    text = reference.read_text(encoding="utf-8")
    assert "OfficeCLI" in text
    assert "Установка — только вручную" in text
    assert "officecli install" in text
    assert cold_catalog["memory"].count(officecli_reference) == 1
    for directory in ("agents", "runtime", "skills"):
        assert not any(
            "officecli" in path.read_text(encoding="utf-8").lower()
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and path.suffix.lower()
            in {".json", ".md", ".ps1", ".py", ".txt", ".yaml", ".yml"}
        )


def test_opencode_llm_interop_documentation_matches_bridge_cli():
    skill = (
        ROOT / "skills" / "llm-interop" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "--task .llm-interop/task.json" in skill
    assert "references/task.schema.json" in skill
    assert "--custom agent" not in skill
    assert "custom agent.schema.json" not in skill


@pytest.mark.parametrize(
    "executable",
    [
        value
        for value in (
            shutil.which("pwsh"),
            shutil.which("powershell.exe"),
        )
        if value
    ],
)
def test_opencode_sync_runtime_is_native_and_client_version_pinned(
    executable, tmp_path
):
    control = ROOT / "control-skills" / "sync-base"
    policy = json.loads(
        (control / "sync-policy.json").read_text(encoding="utf-8")
    )
    assert policy["target"] == "opencode"
    assert policy["client"]["acceptance"] == "PASS"
    assert policy["client"]["version_pattern"] == (
        r"(?<version>1\.18\.7)"
    )
    script = control / "tools" / "sync_base.ps1"
    assert script.is_file()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-File",
            str(script),
            "-PolicyPath",
            str(control / "sync-policy.json"),
            "-TargetHome",
            str(fake_home),
            "-LibraryMode",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "client release contract is not accepted" not in combined.lower()
    assert "github cli" not in combined.lower()


@pytest.mark.parametrize(
    "executable",
    [
        value
        for value in (
            shutil.which("pwsh"),
            shutil.which("powershell.exe"),
        )
        if value
    ],
)
def test_opencode_session_hook_is_silent_without_an_installed_base(
    executable, tmp_path
):
    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-File",
            str(ROOT / "runtime" / "hooks" / "check-release.ps1"),
        ],
        env={**os.environ, "USERPROFILE": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert not (result.stdout + result.stderr).strip()
