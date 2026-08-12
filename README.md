# OpenCode Base

Native, provider-neutral OpenCode base with a clean history and pinned
migration provenance.

## Runtime shape

- HOT: compact global `~/.config/opencode/AGENTS.md`.
- WARM: native discovery metadata for 16 subagents, 39 capability skills, and
  one `sync-base` control skill.
- COLD: full instructions, scripts, templates, references, 3 chains, and 3
  commands loaded only when selected.

The package uses native OpenCode paths for `opencode.json`, agents, skills, and
commands. Claude compatibility is disabled through
`OPENCODE_DISABLE_CLAUDE_CODE=1`, so `.claude` never becomes a hidden runtime
dependency. The base does not choose a provider, model, or reasoning level.
OpenAI GPT and other providers remain selectable through normal OpenCode
configuration.

Updates are strictly hub-to-consumer and use the same verified
`plan/install/doctor/rollback` Foundation protocol. OpenCode sharing is
disabled, and no telemetry, feedback, sessions, documents, credentials, or
local changes are uploaded by the base.

Static startup/discovery estimation is 4,513 tokens versus the 24,026-token
legacy baseline, an 81.22% reduction. This is not provider billing; matched A/B
has not run.

Current verdict: `FULL_RELEASE_OPENCODE: NOT_PASS`.

OpenCode `1.18.7` is pinned to the official Windows release assets. The CLI
archive SHA-256 is
`54598e262c0744e6c3b9ddba85764917a48d366a9aa6c817c2feb9d34b3f1105`;
the Desktop installer SHA-256 is
`d44d535d4f3ac0dafcca8cbbf2bad6e0baefb089352a795fc57268337bdea378`.
Both accepted binaries have a valid Authenticode signer
`Anomaly Innovations, Inc https://anoma.ly/`, followed by zero-model
`--version`/`--help` smoke. npm is not the release acceptance source. This is
only `CLIENT_BINARY_ACCEPTANCE: PASS`; provider login, model runtime and the
reversible live base canary remain required.

## Offline candidate acceptance

With an accepted exact client binary, the runner exports a clean commit,
builds the candidate twice, and runs the real pinned Foundation engine through
`plan/install/doctor/inventory/rollback` in PowerShell 7 and 5.1, including
current-user environment apply/restore:

```powershell
py -3.12 .\tools\run_offline_acceptance.py `
  --foundation ..\llm-foundation-installer\.work\acceptance\engine-ps7 `
  --foundation-evidence ..\llm-foundation-installer\dist\foundation-acceptance.json `
  --candidate-version 0.1.0 `
  --output .\dist\candidate-0.1.0
```

This proves deterministic candidate packaging and fake-home preservation, but
it remains non-releasable and can never create `package-acceptance.json` or
replace provider/client canaries.

## OpenAI OAuth and controlled release

OpenAI authorization is performed interactively by the employee through
OpenCode `/connect` and ChatGPT Plus/Pro OAuth. The base never reads, copies,
or publishes the resulting token. The one approved provider marker uses
`openai/gpt-5.6-terra` through `--pure`; all control scripts are dry-run by
default.

```powershell
py -3.12 .\tools\provider_marker.py
py -3.12 .\tools\live_canary.py
```

After the explicitly approved marker and canary pass:

```powershell
py -3.12 .\tools\final_evidence.py `
  --candidate-evidence .\dist\candidate-0.1.0\candidate-acceptance.json `
  --provider-marker-evidence <provider-marker.json> `
  --canary-evidence <opencode-canary.json> `
  --output <opencode-final-evidence.json>

py -3.12 .\tools\promote_candidate.py `
  --candidate .\dist\candidate-0.1.0 `
  --final-evidence <opencode-final-evidence.json> `
  --output .\dist\stable-0.1.0
```

Post-publication `release_verifier.py` checks the immutable GitHub release and
exact asset. `create_package_acceptance.py` is the only path from that
verification to an installer-consumable package record.
