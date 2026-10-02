# Runtime reference

The independently installable skill contains `scripts/run.py` and `settings.json`. It needs Linux, Python 3.10+, and the existing authenticated Codex CLI. It uses only Python's standard library and invokes the CLI as one supervised root session. It does not install an SDK, database, message bus, or daemon, and does not claim compatibility with arbitrary providers. Keep global Codex configuration and authentication under the user's control.

Prefer current native agents when they can complete the task. If the packaged runner is needed for orchestration or recovery, start it once from the authorized root. Never have a running root recursively invoke another runner or Codex CLI. Start runs from the intended workspace and choose a sandbox no broader than authorized.

## Settings and allocation

`load_settings(path)` reads a JSON object. `models` and `reasoning_effort` provide values for `astra`, `sol`, and `luna`; each model must be a nonempty ID without whitespace, and each effort must be one of `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`. `allocation` accepts `sols` (0–3), `lunas_per_sol` (positive integer), `direct_lunas` (positive integer), and optional `max_agents` (2–15). It also accepts `profile` as null or a nonempty existing Codex profile name, and `sandbox` as null, `read-only`, or `workspace-write`. Settings are requests and limits; actual native capacity can be lower.

The packaged defaults request one Sol with five Lunas, or seven agents including Astra. A direct small roster can use `sols: 0` and `direct_lunas: 1`. `allocation(settings, sols, lunas, direct_lunas)` accepts optional count overrides and returns the normalized counts plus `native_children` and `total_agents`. It rejects invalid counts and trees exceeding the lower of the configured cap and the hard 15-agent maximum. Counts and role models are configurable; examples are not universal availability guarantees. Default model IDs are `gpt-6-astra`, `gpt-6.1-sol`, and `gpt-6-luna`. They identify requested defaults only, not independently verified effective identity.

## Commands and recovery

Use the installed path, commonly `${CODEX_HOME:-$HOME/.codex}/skills/mozglo-orchestrator/scripts/run.py`:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/mozglo-orchestrator/scripts/run.py" start --workspace "$PWD" \
  --sols 0 --direct-lunas 1 --sandbox workspace-write \
  --prompt "Describe the bounded task"
python3 "${CODEX_HOME:-$HOME/.codex}/skills/mozglo-orchestrator/scripts/run.py" status --workspace "$PWD"
python3 "${CODEX_HOME:-$HOME/.codex}/skills/mozglo-orchestrator/scripts/run.py" resume --workspace "$PWD"
python3 "${CODEX_HOME:-$HOME/.codex}/skills/mozglo-orchestrator/scripts/run.py" stop --workspace "$PWD"
```

`start` supports `--settings`, `--state-dir`, `--codex`, `--sandbox`, `--sols`, `--lunas`, `--direct-lunas`, `--model-astra`, `--model-sol`, `--model-luna`, and per-role reasoning effort options. It requires a nonempty `--prompt` or stdin; recovery uses the separate `resume` command. `status` reports saved state and event/result paths. `resume` uses the saved explicit session UUID and saved workspace, settings, allocation, profile, and sandbox; it refuses a changed Codex configuration fingerprint and does not select `--last`. `stop` signals the verified local Codex process group. This does not prove remote or delegated work has stopped.

Run state is private and outside the repository: `$XDG_STATE_HOME/mozglo/<workspace-hash>` or, when unset, `$HOME/.local/state/mozglo/<workspace-hash>`. `--state-dir` overrides this location. Before retrying, inspect `status`, `.mozglo/context.md`, and existing workers. Keep checkpoints compact, retain accepted authorization and verified evidence, and ensure an earlier writer has finished before assigning its incomplete task again. See [report guidance](report.md).
