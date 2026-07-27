# OpenCode Base

Native, provider-neutral OpenCode base with a clean history and pinned
migration provenance.

## Runtime shape

- HOT: compact global `~/.config/opencode/AGENTS.md`.
- WARM: native discovery metadata for 16 subagents, 37 capability skills, and
  one `sync-base` control skill.
- COLD: full instructions, scripts, templates, references, 3 chains, and 3
  commands loaded only when selected.

The package uses native OpenCode paths for `opencode.json`, agents, skills, and
commands. Claude compatibility is disabled through
`OPENCODE_DISABLE_CLAUDE_CODE=1`, so `.claude` never becomes a hidden runtime
dependency. The base does not choose a provider, model, or reasoning level.
OpenAI GPT and other providers remain selectable through normal OpenCode
configuration. Kimi is not a standalone target.

Updates are strictly hub-to-consumer and use the same verified
`plan/install/doctor/rollback` Foundation protocol. OpenCode sharing is
disabled, and no telemetry, feedback, sessions, documents, credentials, or
local changes are uploaded by the base.

Static startup/discovery estimation is 4,173 tokens versus the 24,026-token
legacy baseline, an 82.63% reduction. This is not provider billing; matched A/B
has not run.

Current verdict: `FULL_RELEASE_OPENCODE: NOT_PASS`.

The exact supported OpenCode version, real Windows/WSL Foundation acceptance,
provider-neutral acceptance, and a live OpenCode canary remain required.

## Offline integration acceptance

The non-releasable runner temporarily overlays client version
`0.0.0-offline` only inside an exported clean commit. Its transformation ID is
deliberately incompatible with stable release policy. It runs the real pinned
Foundation engine through `plan/install/doctor/inventory/rollback` in
PowerShell 7 and 5.1, including current-user environment apply/restore:

```powershell
py -3.12 .\tools\run_offline_acceptance.py `
  --foundation ..\llm-foundation-installer\.work\acceptance\engine-ps7 `
  --foundation-evidence ..\llm-foundation-installer\dist\foundation-acceptance.json `
  --output .\dist\offline-acceptance
```

This can prove package integration and preserved-data behavior, but can never
create `package-acceptance.json` or replace provider/client canaries.
