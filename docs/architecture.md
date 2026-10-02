# Architecture

MOZGLO is an independently installable Codex skill with a Linux Python 3.10+ runner. It supervises one authenticated Codex CLI root and its authorized work. Prefer native agents when sufficient; start the packaged runner once only when orchestration, recovery, or capacity is needed. A running root never starts a runner or CLI recursively. The runner uses the existing authenticated CLI, not an SDK worker pool or custom message bus.

## Roles and work

Astra leads scope, planning, authorization continuity, allocation, integration, and reporting. It can coordinate up to three Sols. Sol coordinates, investigates, decides, and reviews; Luna implements all product changes and does not delegate. Use bounded tasks and the smallest useful roster. Packaged defaults request one Sol and five Lunas; the hard cap is 15 total agents including Astra. Actual capacity may be lower. Role models and counts are configurable settings and requests; they do not prove effective identity or universal provider compatibility.

Preserve authorization across delegation and recovery. Give each worker its scope, owner, baseline, acceptance evidence, dependencies, and checkpoint. Path assignments guide coordination but are not permission locks. Avoid duplicate writes by reconciling baselines. Before retrying, inspect checkpoint and prior process state, retain completed evidence, and ensure an earlier writer has stopped.

## Memory and references

Keep live project memory only in `.mozglo/context.md`, led by Astra. Maintain concise accepted scope, authorization, roster/ownership, baselines, current decisions, evidence, blockers, and next actions; mark superseded decisions and replace stale notes. Keep secrets and private payloads out of prompts, memory, logs, and reports. Source guidance is maintained in the [installable skill](../skills/mozglo-orchestrator/SKILL.md), its [runtime reference](../skills/mozglo-orchestrator/references/runtime.md), and [checkpoint/report reference](../skills/mozglo-orchestrator/references/report.md).
