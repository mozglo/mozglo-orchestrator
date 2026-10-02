# Repository instructions

This repository packages an independently installable Codex skill and portable Linux Python runner. Read this file, `README.md`, and `docs/architecture.md` before changing behavior; then read the local guide for files in scope. Code and tests define behavior. Keep maintained documentation aligned with verified interfaces.

## Boundaries

- `skills/mozglo-orchestrator/` contains the skill, runner, settings, and references. Keep instructions concise and accurate.
- `docs/` is maintained source guidance, not runtime state, cache, or project memory. Keep the single compact task memory at `.mozglo/context.md`.
- `.github/workflows/` and `scripts/validate.py` check package artifacts. Keep validation deterministic, offline, and based on the runner's real contract.
- Never overwrite user instructions, global Codex settings, or user data during install/setup. Never commit credentials, private messages, or production payloads.

## Changes and recovery

Confirm the repository root and inspect existing changes in target paths before writing. Preserve authorized scope and pre-existing diffs. Coordinate owners and baselines for overlapping work; assignments are advisory, not exclusive locks. Before retrying, inspect checkpoints and worker/process state, preserve completed changes and evidence, and continue unfinished approved work. Do not claim a model, provider, or runtime behavior without evidence. Run relevant checks and report exactly what ran and what remains unverified.
