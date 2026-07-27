from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


def _metadata(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        raise ValueError(f"missing frontmatter: {path}")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    name = values.get("name", path.stem if path.name != "SKILL.md" else path.parent.name)
    return name, values["description"]


def _surface(payload: bytes, **extra: object) -> dict[str, object]:
    return {
        **extra,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _candidate_surfaces(
    repo_root: Path,
    target: str,
) -> dict[str, dict[str, object]]:
    if target == "claude":
        hot_path = repo_root / "CLAUDE.md"
        hot_logical = "~/.claude/CLAUDE.md"
        skill_root = "~/.claude/skills"
        agent_root = "~/.claude/agents"
    elif target == "opencode":
        hot_path = repo_root / "AGENTS.md"
        hot_logical = "~/.config/opencode/AGENTS.md"
        skill_root = "~/.config/opencode/skills"
        agent_root = "~/.config/opencode/agents"
    else:
        raise ValueError(f"unsupported target: {target}")

    skill_paths = [
        *sorted((repo_root / "skills").glob("*/SKILL.md")),
        *sorted((repo_root / "control-skills").glob("*/SKILL.md")),
    ]
    skill_rows = []
    for path in skill_paths:
        name, description = _metadata(path)
        skill_rows.append(
            f"{name}|{description}|{skill_root}/{path.parent.name}/SKILL.md"
        )
    skills = "\n".join(skill_rows).encode("utf-8")

    agent_paths = sorted((repo_root / "agents").glob("*.md"))
    agent_rows = []
    for path in agent_paths:
        name, description = _metadata(path)
        agent_rows.append(f"{name}|{description}|{agent_root}/{path.name}")
    agents = "\n".join(agent_rows).encode("utf-8")

    return {
        "hot": _surface(hot_path.read_bytes(), logical_path=hot_logical),
        "skills_discovery": _surface(
            skills,
            logical_root=skill_root,
            count=len(skill_paths),
            capability_skills=37,
            control_skills=1,
        ),
        "agents_discovery": _surface(
            agents,
            logical_root=agent_root,
            count=len(agent_paths),
        ),
    }


def audit_static_context(
    repo_root: Path,
    target: str,
    baseline_path: Path | None = None,
) -> dict[str, object]:
    baseline_path = baseline_path or (
        repo_root / "baselines" / "legacy-hub-2026-07-26.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = _candidate_surfaces(repo_root, target)
    candidate_bytes = sum(int(value["bytes"]) for value in candidate.values())
    legacy_bytes = int(baseline["total_bytes"])
    reduction = 1.0 - (candidate_bytes / legacy_bytes)
    return {
        "schema_version": 1,
        "target": target,
        "method": baseline["method"],
        "legacy": baseline,
        "candidate": {
            "surfaces": candidate,
            "total_bytes": candidate_bytes,
            "estimated_tokens": math.ceil(candidate_bytes / 3),
            "cold_payload_in_startup": False,
        },
        "thresholds": {
            "base_controlled_startup_reduction_min": 0.70,
            "matched_ab_total_input_reduction_min": 0.25,
        },
        "results": {
            "base_controlled_startup_reduction": reduction,
            "STATIC_TOKEN_ACCEPTANCE": "PASS" if reduction >= 0.70 else "NOT_PASS",
            "MATCHED_AB": "NOT_RUN",
        },
        "limitations": [
            "Static tokens are a conservative UTF-8 byte estimate, not provider billing.",
            "Matched A/B requires owner approval and identical client, model, reasoning and prompts.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("claude", "opencode"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output = args.output or repo_root / "reports" / "static-token-audit.json"
    report = audit_static_context(repo_root, args.target)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report["results"], ensure_ascii=False, sort_keys=True))
    return 0 if report["results"]["STATIC_TOKEN_ACCEPTANCE"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
