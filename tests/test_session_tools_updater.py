from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import time
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "runtime" / "update-session-tools.ps1"
SESSION_TOOLS_SPEC = importlib.util.spec_from_file_location(
    "opencode_session_tools_updater_fixture",
    ROOT / "tools" / "session_tools.py",
)
assert SESSION_TOOLS_SPEC and SESSION_TOOLS_SPEC.loader
session_tools = importlib.util.module_from_spec(SESSION_TOOLS_SPEC)
sys.modules[SESSION_TOOLS_SPEC.name] = session_tools
SESSION_TOOLS_SPEC.loader.exec_module(session_tools)


def _powershells() -> list[str]:
    return [
        executable
        for executable in ("pwsh.exe", "powershell.exe")
        if shutil.which(executable)
    ]


def _find_csharp_compiler() -> Path | None:
    candidates: list[Path] = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidates.extend(
                Path(root).glob(
                    "Microsoft Visual Studio/*/*/MSBuild/Current/Bin/Roslyn/csc.exe"
                )
            )
    framework = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    if framework.is_file():
        candidates.append(framework)
    return sorted(candidates)[0] if candidates else None


def _compile_fake_gh(path: Path) -> None:
    compiler = _find_csharp_compiler()
    assert compiler is not None, "C# compiler is unavailable"
    source = path.with_suffix(".cs")
    source.write_text(
        textwrap.dedent(
            r'''
            using System;
            using System.IO;
            using System.Linq;
            class FakeGh
            {
                static int Main(string[] args)
                {
                    string root = Environment.GetEnvironmentVariable("FAKE_GH_ROOT");
                    string log = Environment.GetEnvironmentVariable("FAKE_GH_LOG");
                    File.AppendAllText(log, String.Join("\u001f", args) + "\n");
                    if (Environment.GetEnvironmentVariable("FAKE_GH_OFFLINE") == "1")
                    {
                        Console.Error.WriteLine("offline");
                        return 1;
                    }
                    if (args.Length >= 1 && args[0] == "api")
                    {
                        Console.Write(File.ReadAllText(Path.Combine(root, "releases.json")));
                        return 0;
                    }
                    if (args.Length >= 2 && args[0] == "release" && args[1] == "download")
                    {
                        string pattern = args[Array.IndexOf(args, "-p") + 1];
                        string directory = args[Array.IndexOf(args, "-D") + 1];
                        Directory.CreateDirectory(directory);
                        File.Copy(Path.Combine(root, pattern), Path.Combine(directory, pattern), true);
                        return 0;
                    }
                    if (args.Length >= 3 && args[0] == "attestation" && args[1] == "verify")
                    {
                        string name = Path.GetFileName(args[2]);
                        if ((name == "release-manifest.json" &&
                             Environment.GetEnvironmentVariable("FAKE_GH_SWAP_MANIFEST_AFTER_ATTESTATION") == "1") ||
                            (name.EndsWith(".zip") &&
                             Environment.GetEnvironmentVariable("FAKE_GH_SWAP_ASSET_AFTER_ATTESTATION") == "1"))
                        {
                            File.AppendAllText(args[2], " ");
                        }
                    }
                    if (Environment.GetEnvironmentVariable("FAKE_GH_LARGE_JSON") == "1")
                    {
                        Console.Write("{\"verified\":true,\"padding\":\"" + new String('x', 262144) + "\"}");
                        return 0;
                    }
                    Console.Write("{\"verified\":true}");
                    return 0;
                }
            }
            '''
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(compiler), "/nologo", "/target:exe", f"/out:{path}", str(source)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> str:
    if not path.exists():
        return "absent"
    if path.is_file():
        return _sha256(path)
    canonical = b"".join(
        relative.as_posix().encode("utf-8")
        + b"\0"
        + _sha256(path / relative).encode("ascii")
        + b"\n"
        for relative in sorted(
            (item.relative_to(path) for item in path.rglob("*") if item.is_file()),
            key=lambda item: item.as_posix(),
        )
    )
    return hashlib.sha256(canonical).hexdigest()


def _tick_contract(age_seconds: float = 0.0) -> tuple[int, int, int, int, int]:
    frequency = ctypes.c_longlong()
    counter = ctypes.c_longlong()
    assert ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(frequency))
    assert ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
    start = counter.value - int(frequency.value * age_seconds)
    return (
        start,
        start + 22 * frequency.value,
        start + 25 * frequency.value,
        start + 30 * frequency.value,
        frequency.value,
    )


def _strict_stable_manifest(bundle, version: str) -> dict[str, object]:
    asset = session_tools.session_tools_asset_record(bundle)
    return {
        "schema_version": 1,
        "target": "opencode",
        "version": version,
        "tag": f"opencode-v{version}",
        "channel": "stable",
        "client": {"id": "opencode", "supported_version": "1.18.13"},
        "foundation_engine_version": "0.3.0",
        "foundation_engine_manifest_sha256": "1" * 64,
        "source": {
            "repository": "https://github.com/K7-LS/opencode-base",
            "commit": "2" * 40,
            "tree": "3" * 40,
            "transformation": "opencode-native-v1",
        },
        "asset": {
            "name": f"opencode-base-{version}.zip",
            "sha256": "4" * 64,
            "bytes": 1,
        },
        "session_tools_asset": asset,
        "package_manifest_sha256": "5" * 64,
        "components_lock_sha256": "6" * 64,
        "requires": {
            "immutable_release": True,
            "release_attestation": True,
            "verification_commands": [
                f"gh release verify opencode-v{version} -R K7-LS/opencode-base",
                f"gh release verify-asset opencode-v{version} opencode-base-{version}.zip -R K7-LS/opencode-base",
            ],
        },
        "acceptance_evidence_sha256": "7" * 64,
        "promoted_from_candidate_manifest_sha256": "8" * 64,
    }


def _fixture(tmp_path: Path, *, version: str = "0.1.3") -> dict[str, object]:
    profile = tmp_path / "профиль"
    release = tmp_path / "release"
    release.mkdir(parents=True)
    bundle = session_tools.build_session_tools_bundle(ROOT, release, version)
    manifest = _strict_stable_manifest(bundle, version)
    (release / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (release / "releases.json").write_text(
        json.dumps(
            [
                {
                    "tag_name": f"opencode-v{version}",
                    "draft": False,
                    "prerelease": False,
                    "immutable": True,
                    "published_at": "2026-08-10T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _compile_fake_gh(fake_bin / "gh.exe")
    gh_log = tmp_path / "gh.log"
    receipt = profile / ".llm-foundation" / "bin" / "opencode-managed.receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"target":"opencode"}\n', encoding="utf-8")
    base_runtime = profile / ".config" / "opencode" / "base" / "runtime"
    base_runtime.mkdir(parents=True)
    (base_runtime.parent / "VERSION").write_text("0.1.2\n", encoding="utf-8")
    baseline_manifest = dict(bundle.manifest)
    baseline_manifest["release_tag"] = "opencode-v0.1.2"
    baseline_manifest["base_version"] = "0.1.2"
    (base_runtime / "session-tools-baseline.json").write_text(
        json.dumps(baseline_manifest, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "USERPROFILE": str(profile),
            "FAKE_GH_ROOT": str(release),
            "FAKE_GH_LOG": str(gh_log),
            "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        }
    )
    return {
        "profile": profile,
        "release": release,
        "bundle": bundle,
        "manifest": manifest,
        "gh_log": gh_log,
        "environment": environment,
    }


def _run_updater(
    host: str,
    fixture: dict[str, object],
    *,
    age_seconds: float = 0.0,
    transaction_id: str | None = None,
    environment: dict[str, str] | None = None,
    mutation_tick_delta: int = 0,
) -> subprocess.CompletedProcess[str]:
    start, mutation, kill, deadline, frequency = _tick_contract(age_seconds)
    mutation += mutation_tick_delta
    return subprocess.run(
        [
            host,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(UPDATER),
            "-ManagedPreflight",
            "-TransactionId",
            transaction_id or str(uuid.uuid4()),
            "-StartTick",
            str(start),
            "-MutationCutoffTick",
            str(mutation),
            "-KillTick",
            str(kill),
            "-HardDeadlineTick",
            str(deadline),
            "-StopwatchFrequency",
            str(frequency),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment or fixture["environment"],
        timeout=35,
    )


@pytest.mark.parametrize("host", _powershells())
def test_managed_updater_verifies_immutable_release_and_installs_unicode_skill(
    tmp_path: Path, host: str
) -> None:
    """Removing any trust command, UTF-8-safe apply, or exact state binding must fail."""
    fixture = _fixture(tmp_path)
    profile = fixture["profile"]
    neighbor = profile / ".config" / "opencode" / "skills" / "local-neighbor"
    neighbor.mkdir(parents=True)
    (neighbor / "SKILL.md").write_text("локальный", encoding="utf-8")

    result = _run_updater(host, fixture)

    assert result.returncode == 0, result.stdout + result.stderr
    destination = profile / ".config" / "opencode" / "skills" / "ru-writing-style"
    assert (destination / "SKILL.md").read_bytes() == (
        ROOT / "skills" / "ru-writing-style" / "SKILL.md"
    ).read_bytes()
    assert (neighbor / "SKILL.md").read_text(encoding="utf-8") == "локальный"
    state_path = (
        profile
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state) == {
        "schema_version",
        "target",
        "release_tag",
        "release_version",
        "release_manifest_sha256",
        "session_manifest_sha256",
        "verified_at",
        "tools",
    }
    assert state["release_tag"] == "opencode-v0.1.3"
    assert state["release_manifest_sha256"] == _sha256(
        fixture["release"] / "release-manifest.json"
    )
    assert state["tools"][0]["destination"] == str(destination)
    assert state["tools"][0]["ownership_marker"] == (
        "session-tools-v1:opencode:ru-writing-style"
    )
    log = fixture["gh_log"].read_text(encoding="utf-8")
    assert "api\u001frepos/K7-LS/opencode-base/releases?per_page=20\u001f--jq" in log
    assert "release\u001fverify\u001fopencode-v0.1.3" in log
    assert log.count("release\u001fverify-asset") == 2
    assert log.count("attestation\u001fverify") == 2


@pytest.mark.parametrize("defect", ["guid", "ticks"])
def test_updater_rejects_malformed_launcher_contract_before_staging(
    tmp_path: Path, defect: str
) -> None:
    """A non-canonical GUID or wrong 22/25/30 contract must create no transaction state."""
    fixture = _fixture(tmp_path)
    host = _powershells()[0]
    result = _run_updater(
        host,
        fixture,
        transaction_id=(
            "{12345678-1234-1234-1234-123456789abc}"
            if defect == "guid"
            else None
        ),
        mutation_tick_delta=1 if defect == "ticks" else 0,
    )
    assert result.returncode != 0
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    assert not state_root.exists()
    assert not fixture["gh_log"].exists()


@pytest.mark.parametrize("tool_count", [0, 2])
def test_protocol_one_runtime_rejects_non_singular_session_assets(
    tmp_path: Path, tool_count: int
) -> None:
    """Protocol 1 must fail closed instead of pretending multi-destination atomicity."""
    fixture = _fixture(tmp_path)
    manifest_path = fixture["release"] / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["session_tools_asset"]["tool_count"] = tool_count
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_updater(_powershells()[0], fixture)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "BLOCKED_MULTI_TOOL_ASSET" in result.stdout
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    assert not (state_root / "active-transaction.json").exists()
    assert not (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    ).exists()


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "mutable", "asset-hash", "manifest-toctou", "asset-toctou"],
)
def test_updater_rejects_untrusted_release_without_touching_destination(
    tmp_path: Path, mutation: str
) -> None:
    """Strict JSON, immutability, and asset binding failures must remain pre-mutation."""
    fixture = _fixture(tmp_path)
    releases = fixture["release"] / "releases.json"
    manifest_path = fixture["release"] / "release-manifest.json"
    environment = dict(fixture["environment"])
    if mutation == "duplicate":
        releases.write_text(
            '[{"tag_name":"opencode-v0.1.3","tag_name":"opencode-v0.1.4",'
            '"draft":false,"prerelease":false,"immutable":true,'
            '"published_at":"2026-08-10T00:00:00Z"}]',
            encoding="utf-8",
        )
    elif mutation == "mutable":
        value = json.loads(releases.read_text(encoding="utf-8"))
        value[0]["immutable"] = False
        releases.write_text(json.dumps(value), encoding="utf-8")
    elif mutation == "asset-hash":
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        value["session_tools_asset"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(value), encoding="utf-8")
    elif mutation == "manifest-toctou":
        environment["FAKE_GH_SWAP_MANIFEST_AFTER_ATTESTATION"] = "1"
    else:
        environment["FAKE_GH_SWAP_ASSET_AFTER_ATTESTATION"] = "1"

    result = _run_updater(_powershells()[0], fixture, environment=environment)

    assert result.returncode == 0, result.stdout + result.stderr
    destination = (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    )
    assert not destination.exists()
    assert not (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "active-transaction.json"
    ).exists()


@pytest.mark.parametrize("host", _powershells())
def test_duplicate_key_active_journal_is_a_hard_recovery_block(
    tmp_path: Path, host: str
) -> None:
    """An untrusted recovery journal must not fail open or reach the network."""
    fixture = _fixture(tmp_path)
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    state_root.mkdir(parents=True)
    journal = state_root / "active-transaction.json"
    journal.write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )

    result = _run_updater(host, fixture)

    assert result.returncode != 0
    assert "BLOCKED_SESSION_RECOVERY" in result.stdout
    assert journal.is_file()
    assert not fixture["gh_log"].exists()


def test_updater_preserves_unmanaged_collision_and_busy_lock(tmp_path: Path) -> None:
    """Neither an unmanaged tool nor another updater's exclusive lock may be overwritten."""
    fixture = _fixture(tmp_path)
    destination = (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    )
    destination.mkdir(parents=True)
    marker = destination / "SKILL.md"
    marker.write_text("local", encoding="utf-8")

    collision = _run_updater(_powershells()[0], fixture)
    assert collision.returncode == 0, collision.stdout + collision.stderr
    assert marker.read_text(encoding="utf-8") == "local"

    shutil.rmtree(destination)
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    state_root.mkdir(parents=True, exist_ok=True)
    lock = state_root / "update.lock"
    with lock.open("w+b") as handle:
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        started = time.monotonic()
        busy = _run_updater(
            _powershells()[0], fixture, age_seconds=21.8
        )
        elapsed = time.monotonic() - started
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    assert busy.returncode == 0, busy.stdout + busy.stderr
    assert elapsed < 2
    assert not destination.exists()


def test_updater_accepts_exact_baseline_ownership_and_creates_state(tmp_path: Path) -> None:
    """A first managed update may adopt only the exact installed baseline bytes."""
    fixture = _fixture(tmp_path)
    destination = (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    )
    shutil.copytree(ROOT / "skills" / "ru-writing-style", destination)

    result = _run_updater(_powershells()[0], fixture)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result=UPDATED" in result.stdout
    assert (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "state.json"
    ).is_file()


def test_updater_does_not_downgrade_a_newer_installed_base(tmp_path: Path) -> None:
    """A stale stable channel may not replace a newer baseline or create older ownership state."""
    fixture = _fixture(tmp_path)
    base = fixture["profile"] / ".config" / "opencode" / "base"
    (base / "VERSION").write_text("0.1.4\n", encoding="utf-8")
    baseline = json.loads(
        (base / "runtime" / "session-tools-baseline.json").read_text(encoding="utf-8")
    )
    baseline["release_tag"] = "opencode-v0.1.4"
    baseline["base_version"] = "0.1.4"
    (base / "runtime" / "session-tools-baseline.json").write_text(
        json.dumps(baseline), encoding="utf-8"
    )
    destination = base.parent / "skills" / "ru-writing-style"
    shutil.copytree(ROOT / "skills" / "ru-writing-style", destination)

    result = _run_updater(_powershells()[0], fixture)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result=NO_UPDATE_BASE_NEWER" in result.stdout
    assert (destination / "SKILL.md").read_bytes() == (
        ROOT / "skills" / "ru-writing-style" / "SKILL.md"
    ).read_bytes()
    assert not (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "state.json"
    ).exists()


def test_offline_before_mutation_keeps_last_layout_and_fails_open(tmp_path: Path) -> None:
    """Network failure without an active journal must leave disk unchanged and return success."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["FAKE_GH_OFFLINE"] = "1"

    result = _run_updater(_powershells()[0], fixture, environment=environment)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result=SKIPPED_OFFLINE" in result.stdout
    assert not (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    ).exists()


def test_missing_gh_reports_required_dependency_and_keeps_layout(tmp_path: Path) -> None:
    """An absent trust executable is a configuration blocker, not an offline network event."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    environment["PATH"] = str(empty_path)
    host = shutil.which(_powershells()[0])
    assert host is not None

    result = _run_updater(host, fixture, environment=environment)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result=BLOCKED_GH_REQUIRED" in result.stdout
    assert not (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    ).exists()


def test_current_verified_tag_is_a_cheap_api_only_noop(tmp_path: Path) -> None:
    """A current managed snapshot must not repeat release verification or downloads."""
    fixture = _fixture(tmp_path)
    first = _run_updater(_powershells()[0], fixture)
    assert first.returncode == 0, first.stdout + first.stderr
    fixture["gh_log"].write_text("", encoding="utf-8")

    second = _run_updater(_powershells()[0], fixture)

    assert second.returncode == 0, second.stdout + second.stderr
    assert "result=NO_UPDATE" in second.stdout
    calls = fixture["gh_log"].read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert calls[0].startswith("api\u001f")


def test_large_gh_json_is_drained_concurrently_without_pipe_deadlock(tmp_path: Path) -> None:
    """Attestation-sized JSON must not block the child process before WaitForExit."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["FAKE_GH_LARGE_JSON"] = "1"

    result = _run_updater(
        _powershells()[0], fixture, age_seconds=18.0, environment=environment
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "result=UPDATED" in result.stdout


@pytest.mark.parametrize(
    "phase",
    [
        "created",
        "staged",
        "move_destination_intent",
        "move_destination_applied",
        "move_staging_intent",
        "move_staging_applied",
        "state_write_intent",
        "state_write_applied",
    ],
)
def test_killed_transaction_recovers_before_next_network_check(
    tmp_path: Path, phase: str
) -> None:
    """Every durable pre-commit phase must recover to old or fully committed verified bytes."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_AFTER_PHASE"] = phase
    killed = _run_updater(_powershells()[0], fixture, environment=environment)
    assert killed.returncode != 0
    journal = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "active-transaction.json"
    )
    assert journal.is_file()

    fixture["gh_log"].unlink(missing_ok=True)
    offline = dict(fixture["environment"])
    offline["FAKE_GH_OFFLINE"] = "1"
    recovered = _run_updater(_powershells()[0], fixture, environment=offline)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert not journal.exists()
    assert not any(journal.parent.joinpath("transactions").glob("*"))
    destination = (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    )
    state = journal.parent / "state.json"
    assert destination.exists() == state.exists()


def test_created_journal_matches_launcher_absent_staging_recovery_layout(
    tmp_path: Path,
) -> None:
    """The first durable journal must bind an absent staging path accepted by launcher recovery."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_AFTER_PHASE"] = "created"
    killed = _run_updater(_powershells()[0], fixture, environment=environment)
    assert killed.returncode != 0
    journal_path = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
        / "active-transaction.json"
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == "created"
    assert all(
        record == {"intent": False, "applied": False}
        for record in journal["operations"].values()
    )
    assert _fingerprint(Path(journal["staging_path"])) == "absent"
    assert _fingerprint(Path(journal["previous_path"])) == "absent"
    assert _fingerprint(Path(journal["destination_path"])) == journal[
        "previous_destination_sha256"
    ]
    assert _fingerprint(Path(journal["state_path"])) == journal["previous_state_sha256"]


def test_created_journal_regular_file_staging_leaf_blocks_and_is_preserved(
    tmp_path: Path,
) -> None:
    """The exact staging leaf must be absent or a real directory, never a regular file."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_AFTER_PHASE"] = "created"
    killed = _run_updater(_powershells()[0], fixture, environment=environment)
    assert killed.returncode != 0
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    journal_path = state_root / "active-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    staging = Path(journal["staging_path"])
    staging.parent.mkdir(parents=True)
    staging.write_text("not-a-directory", encoding="utf-8")

    offline = dict(fixture["environment"])
    offline["FAKE_GH_OFFLINE"] = "1"
    blocked = _run_updater(_powershells()[0], fixture, environment=offline)
    assert blocked.returncode != 0
    assert "BLOCKED_SESSION_RECOVERY" in blocked.stdout
    assert journal_path.is_file()
    assert staging.read_text(encoding="utf-8") == "not-a-directory"
    assert _fingerprint(Path(journal["destination_path"])) == journal[
        "previous_destination_sha256"
    ]
    assert _fingerprint(Path(journal["state_path"])) == journal["previous_state_sha256"]


@pytest.mark.parametrize("host", _powershells())
def test_created_journal_recovers_transaction_owned_partial_staging(
    tmp_path: Path, host: str
) -> None:
    """A kill during population may leave only an exact non-reparse transaction directory."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_DURING_STAGING"] = "partial-bytes"
    killed = _run_updater(host, fixture, environment=environment)
    assert killed.returncode != 0
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    journal_path = state_root / "active-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    staging = Path(journal["staging_path"])
    assert journal["phase"] == "created"
    assert staging.is_dir()
    assert any(item.is_file() for item in staging.rglob("*"))
    assert _fingerprint(staging) not in {
        "absent",
        journal["expected_staging_sha256"],
    }

    offline = dict(fixture["environment"])
    offline["FAKE_GH_OFFLINE"] = "1"
    recovered = _run_updater(host, fixture, environment=offline)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert not journal_path.exists()
    assert not staging.exists()
    assert not any(state_root.joinpath("transactions").glob("*"))


def test_partial_created_staging_with_nested_reparse_blocks_without_cleanup(
    tmp_path: Path,
) -> None:
    """Recovery must preserve its journal and never recurse through a nested junction."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_DURING_STAGING"] = "partial-bytes"
    killed = _run_updater(_powershells()[0], fixture, environment=environment)
    assert killed.returncode != 0
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    journal_path = state_root / "active-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    staging = Path(journal["staging_path"])
    neighbor = tmp_path / "preserved-neighbor"
    neighbor.mkdir()
    marker = neighbor / "marker.txt"
    marker.write_text("preserve", encoding="utf-8")
    junction = staging / "redirect"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(neighbor)],
        check=False,
        capture_output=True,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")

    offline = dict(fixture["environment"])
    offline["FAKE_GH_OFFLINE"] = "1"
    blocked = _run_updater(_powershells()[0], fixture, environment=offline)
    assert blocked.returncode != 0
    assert "BLOCKED_SESSION_RECOVERY" in blocked.stdout
    assert journal_path.is_file()
    assert junction.exists()
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_recovery_rechecks_for_reparse_immediately_before_recursive_delete(
    tmp_path: Path,
) -> None:
    """A junction introduced after journal validation must block the pending cleanup."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_DURING_STAGING"] = "partial-bytes"
    killed = _run_updater(_powershells()[0], fixture, environment=environment)
    assert killed.returncode != 0
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    journal_path = state_root / "active-transaction.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    staging = Path(journal["staging_path"])
    neighbor = tmp_path / "late-preserved-neighbor"
    neighbor.mkdir()
    marker = neighbor / "marker.txt"
    marker.write_text("preserve", encoding="utf-8")

    recovery = dict(fixture["environment"])
    recovery["FAKE_GH_OFFLINE"] = "1"
    recovery["LLM_FOUNDATION_TEST_PRE_DELETE_BARRIER"] = "1"
    start, mutation, kill, deadline, frequency = _tick_contract()
    process = subprocess.Popen(
        [
            _powershells()[0],
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(UPDATER),
            "-ManagedPreflight",
            "-TransactionId",
            str(uuid.uuid4()),
            "-StartTick",
            str(start),
            "-MutationCutoffTick",
            str(mutation),
            "-KillTick",
            str(kill),
            "-HardDeadlineTick",
            str(deadline),
            "-StopwatchFrequency",
            str(frequency),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=recovery,
    )
    barrier_prefix = f".test-pre-delete.{journal['transaction_id']}"
    ready = state_root / f"{barrier_prefix}.ready"
    resume = state_root / f"{barrier_prefix}.continue"
    wait_deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < wait_deadline:
        time.sleep(0.02)
    assert ready.is_file(), process.communicate(timeout=2)
    junction = staging / "late-reparse"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(neighbor)],
        check=False,
        capture_output=True,
    )
    if created.returncode != 0:
        process.kill()
        process.communicate(timeout=2)
        pytest.skip("junction creation is unavailable")
    resume.write_bytes(b"continue")
    stdout, stderr = process.communicate(timeout=10)
    blocked = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    assert blocked.returncode != 0
    assert "BLOCKED_SESSION_RECOVERY" in blocked.stdout
    assert journal_path.is_file()
    assert marker.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("mutation", ["move_destination", "move_staging", "write_state"])
def test_kill_between_filesystem_mutation_and_applied_reconciles_actual_layout(
    tmp_path: Path, mutation: str
) -> None:
    """Intent plus actual fingerprints must recover a move/write killed before durable applied."""
    fixture = _fixture(tmp_path)
    environment = dict(fixture["environment"])
    environment["LLM_FOUNDATION_TEST_STOP_AFTER_MUTATION"] = mutation
    killed = _run_updater(_powershells()[0], fixture, environment=environment)
    assert killed.returncode != 0
    state_root = (
        fixture["profile"]
        / ".llm-foundation"
        / "state"
        / "session-tools"
        / "opencode"
    )
    journal = state_root / "active-transaction.json"
    assert journal.is_file()

    fixture["gh_log"].unlink(missing_ok=True)
    offline = dict(fixture["environment"])
    offline["FAKE_GH_OFFLINE"] = "1"
    recovered = _run_updater(_powershells()[0], fixture, environment=offline)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert not journal.exists()
    destination = (
        fixture["profile"]
        / ".config"
        / "opencode"
        / "skills"
        / "ru-writing-style"
    )
    state = state_root / "state.json"
    assert destination.exists() == state.exists()
    assert not any(state_root.joinpath("transactions").glob("*"))


def test_direct_lifecycle_remains_not_pass_without_invented_stable_hook() -> None:
    """Official research evidence must not be converted into unsupported OpenCode wiring."""
    evidence = json.loads(
        (ROOT / "runtime" / "session-tools-lifecycle.json").read_text(encoding="utf-8")
    )
    config = json.loads((ROOT / "runtime" / "opencode.json").read_text(encoding="utf-8"))
    assert evidence["checked_client_version"] == "1.18.13"
    assert evidence["managed_prelaunch"]["status"] == "PASS"
    assert evidence["direct_launch"] == {
        "status": "NOT_PASS",
        "reason": "No stable documented pre-discovery SessionStart command hook is confirmed.",
        "opencode_config_changed": False,
        "beta_plugin_reload_used": False,
    }
    assert "plugin" not in config
    assert "plugins" not in config
    assert "hooks" not in config
