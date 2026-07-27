from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


TEXT_SUFFIXES = {
    ".json",
    ".lsp",
    ".md",
    ".patch",
    ".ps1",
    ".py",
    ".tmpl",
    ".txt",
    ".yaml",
    ".yml",
}
WRITE_AGENTS = {
    "designer",
    "expertiza-responder",
    "id-engineer",
    "kp-writer",
    "letter-writer",
    "pto-engineer",
    "pyrevit-engineer",
    "smetchik",
    "snabzhenets",
}
LEGACY_AGENT_NAMES = {
    "smetchik": "сметчик.md",
    "snabzhenets": "снабженец.md",
}


def _frontmatter_body(text: str, path: Path) -> str:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError(f"frontmatter missing: {path}")
    marker = normalized.find("\n---\n", 4)
    if marker < 0:
        raise ValueError(f"frontmatter not closed: {path}")
    return normalized[marker + 5 :].lstrip("\n")


def _copytree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".pytest_cache",
            "*.pyc",
            "tests",
        ),
    )


def _native_opencode_text(text: str) -> str:
    replacements = (
        ("~/.claude", "~/.config/opencode"),
        ("~\\.claude", "~\\.config\\opencode"),
        ("~/.Codex", "~/.config/opencode"),
        ("~\\.Codex", "~\\.config\\opencode"),
        ("~/.codex", "~/.config/opencode"),
        ("~\\.codex", "~\\.config\\opencode"),
        (".claude", ".config/opencode"),
        (".codex", ".config/opencode"),
        ("CODEX_HOME", "OPENCODE_CONFIG_DIR"),
        ("`AskUserQuestion`", "явный вопрос пользователю"),
        ("**AskUserQuestion**", "**явный вопрос пользователю**"),
        ("AskUserQuestion", "явный вопрос пользователю"),
        ("CLAUDE.md", "AGENTS.md"),
        ("Claude Code", "OpenCode"),
        ("claude code", "opencode"),
        ("Claude", "OpenCode"),
        ("claude", "opencode"),
        ("Codex CLI", "OpenCode"),
        ("Codex desktop", "OpenCode desktop"),
        ("Codex", "OpenCode"),
        ("codex", "opencode"),
    )
    for before, after in replacements:
        text = text.replace(before, after)
    return text


def _rewrite_opencode_tree(skill_root: Path, skill_id: str) -> None:
    if skill_id == "llm-interop":
        return
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        path.write_text(_native_opencode_text(text), encoding="utf-8", newline="\n")

    for relative in (
        Path("references") / "codex.md",
        Path("references") / "Codex.md",
    ):
        path = skill_root / relative
        if path.exists():
            renamed = path.with_name("opencode.md")
            if renamed.exists():
                renamed.unlink()
            path.rename(renamed)


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _write_components_lock(
    target_root: Path,
    target: str,
    source_repository: str,
    source_commit: str,
) -> None:
    components: list[dict[str, object]] = []
    for kind, directory, pattern in (
        ("agent", target_root / "agents", "*.md"),
        ("skill", target_root / "skills", "*/SKILL.md"),
    ):
        for entry in sorted(directory.glob(pattern)):
            component_root = entry if kind == "agent" else entry.parent
            components.append(
                {
                    "kind": kind,
                    "id": entry.stem if kind == "agent" else entry.parent.name,
                    "path": component_root.relative_to(target_root).as_posix(),
                    "sha256": (
                        hashlib.sha256(entry.read_bytes()).hexdigest()
                        if kind == "agent"
                        else _directory_digest(component_root)
                    ),
                    "source_repository": source_repository,
                    "source_commit": source_commit,
                }
            )
    payload = {
        "schema_version": 1,
        "target": target,
        "components": components,
    }
    (target_root / "components.lock.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("claude", "opencode"), required=True)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rewrite-opencode-existing", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    agent_catalog = json.loads(
        (args.codex / "catalog" / "agents.json").read_text(encoding="utf-8")
    )
    skill_catalog = json.loads(
        (args.codex / "catalog" / "skills.json").read_text(encoding="utf-8")
    )
    descriptions = {row["id"]: row["description"] for row in agent_catalog}

    if args.rewrite_opencode_existing:
        if args.target != "opencode":
            raise SystemExit("--rewrite-opencode-existing requires --target opencode")
        for path in sorted((output / "agents").glob("*.md")):
            path.write_text(
                _native_opencode_text(path.read_text(encoding="utf-8")),
                encoding="utf-8",
                newline="\n",
            )
        for skill_root in sorted((output / "skills").iterdir()):
            if skill_root.is_dir():
                _rewrite_opencode_tree(skill_root, skill_root.name)
        _write_components_lock(
            output,
            args.target,
            "https://github.com/daniileliseev1337/claude-base",
            "d263065d902000a032c87bd31175889168f616bc",
        )
        return 0

    if (output / "agents").exists() or (output / "skills").exists():
        raise SystemExit("agents/ or skills/ already exists")

    (output / "agents").mkdir(parents=True)
    for row in agent_catalog:
        agent_id = row["id"]
        source_name = LEGACY_AGENT_NAMES.get(agent_id, f"{agent_id}.md")
        source = args.legacy / "agents" / source_name
        body = _frontmatter_body(source.read_text(encoding="utf-8"), source)
        if args.target == "opencode":
            body = _native_opencode_text(body)
            permission = (
                "permission:\n"
                f"  edit: {'ask' if agent_id in WRITE_AGENTS else 'deny'}\n"
                "  bash: ask\n"
                "  task: deny\n"
                "  webfetch: ask\n"
                "  websearch: ask\n"
                "  skill: allow\n"
            )
            frontmatter = (
                "---\n"
                f"description: {descriptions[agent_id]}\n"
                "mode: subagent\n"
                f"{permission}"
                "---\n\n"
            )
        else:
            tools = (
                "Read, Write, Edit, Glob, Grep, Bash, WebFetch"
                if agent_id in WRITE_AGENTS
                else "Read, Glob, Grep, Bash, WebFetch"
            )
            frontmatter = (
                "---\n"
                f"name: {agent_id}\n"
                f"description: {descriptions[agent_id]}\n"
                f"tools: {tools}\n"
                "---\n\n"
            )
        (output / "agents" / f"{agent_id}.md").write_text(
            frontmatter + body,
            encoding="utf-8",
            newline="\n",
        )

    (output / "skills").mkdir(parents=True)
    skill_source_root = args.legacy / "skills" if args.target == "claude" else args.codex / "skills"
    for row in skill_catalog:
        skill_id = row["id"]
        source = skill_source_root / skill_id
        destination = output / "skills" / skill_id
        _copytree(source, destination)
        if args.target == "opencode":
            _rewrite_opencode_tree(destination, skill_id)
        skill_path = destination / "SKILL.md"
        body = _frontmatter_body(skill_path.read_text(encoding="utf-8"), skill_path)
        skill_path.write_text(
            (
                "---\n"
                f"name: {skill_id}\n"
                f"description: {row['description']}\n"
                "---\n\n"
                f"{body}"
            ),
            encoding="utf-8",
            newline="\n",
        )

    shutil.copy2(args.codex / "catalog" / "agents.json", output / "catalog-agents.json")
    shutil.copy2(args.codex / "catalog" / "skills.json", output / "catalog-skills.json")
    _write_components_lock(
        output,
        args.target,
        "https://github.com/daniileliseev1337/claude-base",
        "d263065d902000a032c87bd31175889168f616bc",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
