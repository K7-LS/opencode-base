# OpenCode Session Tools and OfficeCLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Use superpowers:test-driven-development, superpowers:writing-skills for the imported skill contract, and superpowers:verification-before-completion.

**Goal:** подготовить OpenCode Base `0.1.2` с managed prelaunch auto-pull `ru-writing-style` и Foundation-managed OfficeCLI, сохранив provider-neutral границы и честно отделив неподтверждённый direct SessionStart API.

**Architecture:** immutable session asset/baseline and shared OfficeCLI contract mirror Codex/Claude. `opencode-managed.exe` and Launch Center use one verified prelaunch path. Direct vendor SessionStart remains a release blocker unless an official stable OpenCode lifecycle API is found and live-tested; beta plugin reload is not used as a gate.

**Tech Stack:** Python 3.12 + pytest, PowerShell 7/5.1, OpenCode CLI, Foundation protocol 1, GitHub all-asset attestations.

## Global Constraints

- Start from `origin/main` `e1b4fb75cffce2fbe1bcb9c2897bc87eeedf0de7`.
- Approved skill source: `C:/Users/Даниил/.claude/skills/ru-writing-style/SKILL.md`, SHA-256 `a20f25a852eaff976c9db90929c94f5658acb3a71eb264479f6d354e04a10938`, 20003 bytes.
- Require accepted Foundation `0.3.0`; copy no OfficeCLI bytes from a local unverified install.
- Preserve `.config/opencode/plugins`, auth/session/project state and every unmanaged skill.
- Keep `OPENCODE_DISABLE_CLAUDE_CODE=1`, `OFFICECLI_NO_AUTO_INSTALL=1`, `OFFICECLI_SKIP_UPDATE=1` in managed child environment.
- Use only official OpenCode documentation/source for lifecycle API decisions. Do not invent a plugin or treat beta `ctx.skill.reload` as stable.
- Do not publish automatically; keep `FULL_RELEASE_OPENCODE=NOT_PASS` until provider, immutable and direct-fallback gates are factual PASS.

## File Map

- `skills/ru-writing-style/SKILL.md`, `cold/memory/reference_officecli.md`, `catalog/*.json`, `MIGRATION-SOURCE.json`.
- `tools/session_tools.py`, `tools/release_builder.py`, `release_verifier.py`, `promotion.py`.
- `runtime/update-session-tools.ps1`, `runtime/hooks/check-release.ps1`, `runtime/opencode.json`, `runtime/managed-surface.json`.
- `tools/run_offline_acceptance.py`, `live_canary.py`, `final_evidence.py`.
- `tests/test_session_tools.py`, `tests/test_session_tools_updater.py`, `tests/test_release_builder.py`, `tests/test_offline_acceptance.py`, `tests/test_live_canary.py`.

---

### Task 1: Import approved skill and OfficeCLI reference

- [ ] Add RED native-contract tests for 38 capability skills, 23 cold records and exact approved skill SHA-256/size.
- [ ] Copy approved bytes, add catalogs/migration/reference and recompute reports/counts. Keep OfficeCLI as cold reference/shared tool only.
- [ ] Run native/token tests to GREEN. Commit `feat: add approved Russian writing skill and OfficeCLI reference`.

### Task 2: Session asset, baseline and granular ownership

- [ ] Add RED strict/deterministic asset tests with duplicate/path/collision/hash/symlink/size limits from the audited design.
- [ ] Create `tools/session_tools.py`; extend builder for `session-tools-opencode-0.1.2.zip`, `session_tools_asset`, package baseline and backward-compatible release binding.
- [ ] Replace broad `.config/opencode/skills` ownership with granular package skill directories including `sync-base`, excluding `ru-writing-style` and preserving local skills/plugins.
- [ ] Run session/release/native tests to GREEN. Commit `feat: build OpenCode session tool assets`.

### Task 3: Managed updater and official direct-lifecycle decision

- [ ] Add RED updater tests for immutable `gh`, strict JSON, launcher clock, durable pre-staging journal, kill/recovery, baseline/unmanaged collisions, offline/locks and Cyrillic.
- [ ] Implement `runtime/update-session-tools.ps1`; wire only the verified `opencode-managed.exe` prelaunch contract first.
- [ ] Search current official OpenCode docs/source for a stable pre-discovery SessionStart/lifecycle hook. Record exact URL/version/API and add a failing wiring test before implementation if such API exists.
- [ ] If no stable API is confirmed, leave `runtime/opencode.json` without invented hook wiring, retain direct fallback as `NOT_PASS`, and document the blocker in status/evidence. Do not use beta plugin reload.
- [ ] Run updater tests in PowerShell 7 and 5.1. Commit `feat: update OpenCode session tools through managed launch`.

### Task 4: Foundation shared tools and old sync compatibility

- [ ] Add RED tests requiring accepted Foundation `0.3.0` and exact OfficeCLI/shim/policy/launcher binding; reject tamper/source mismatch.
- [ ] Extend builder/promotion/verifier for main ZIP, session ZIP and manifest. Keep existing wildcard attestation workflow and strengthen its contract test for all assets.
- [ ] Add clean, legacy broad and broad-plus-local homes plus actual legacy `$sync-base` protocol-1 compatibility; preserve local skills/plugins byte-for-byte.
- [ ] Run release/offline/promotion/verifier suites to GREEN. Commit `feat: bind OpenCode package to Foundation shared tools`.

### Task 5: Managed canary and release evidence

- [ ] Add RED canary/final-evidence tests requiring `opencode-managed.exe`, updater evidence and skill discovery before process start.
- [ ] Update counts, offline matrix, final evidence and child environment. Keep provider marker tool-use rejection and PII-free evidence.
- [ ] Run full `python -m pytest -q`, build candidate `0.1.2` against Foundation `0.3.0`, run both PowerShell acceptances and managed-launch live canary.
- [ ] Run independent whole-branch audit. Keep full release `NOT_PASS` if official direct lifecycle, provider or immutable verification remains absent; do not represent managed-only canary as full direct-launch coverage.

## Plan Self-Review

- Managed same-session path is implementable now; direct lifecycle uncertainty is an explicit gate, not hidden.
- User plugins/local skills and cross-provider environment remain preserved.
- Publication and provider calls remain outside automatic implementation steps.
