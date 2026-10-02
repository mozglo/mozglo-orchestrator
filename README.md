<p align="center"><img src="assets/banner.png" alt="MOZGLO Orchestrator — Astra plans, Sol coordinates, Luna implements" width="100%"></p>

<p align="center"><a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-79b9c2.svg"></a> <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg"> <img alt="Codex skill" src="https://img.shields.io/badge/Codex-skill-20272b.svg"></p>

<p align="center"><strong>Authorized, recoverable Codex work with focused agent roles.</strong><br>Created and maintained by <a href="https://github.com/mozglo">mozglo</a></p>
<p align="center"><a href="README.ru.md">Русский</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="skills/mozglo-orchestrator/SKILL.md">Skill</a> · <a href="LICENSE">MIT</a></p>

MOZGLO is an independently installable Codex skill. Prefer current native agents when they can complete the task; use native messages for live coordination. Start the packaged runner once only when orchestration or recovery is needed. A running root never starts another runner or Codex CLI. Astra leads and integrates, Sol coordinates and reviews, and Luna implements every product change.

## Install and use

Install the skill in Codex with `skill-installer` from:

```text
https://github.com/mozglo/mozglo-orchestrator/tree/main/skills/mozglo-orchestrator
```

Then start a new Codex session and give it a bounded task:

```text
$mozglo-orchestrator <task>
```

## Roles and setup

The defaults request one Astra, one Sol, and five Lunas (seven agents total); a small task can use Astra and one Luna. The hard cap is 15 agents including Astra, and actual capacity may be lower. Settings can customize role model IDs, reasoning effort, allocation, sandbox, and an existing Codex profile. Default model IDs are `gpt-6-astra`, `gpt-6.1-sol`, and `gpt-6-luna`; they are requests, not guarantees of effective model identity or capacity. The portable runner requires Linux, Python 3.10+, and the existing authenticated Codex CLI. It uses only the standard library.

Keep one compact project record in `.mozglo/context.md`, including roster, task ownership, decisions, evidence, and next steps. Share live updates through native messages. Final user reports use one to three useful technical images and two to four concise lines or links; internal handoffs stay text-only.

See the [runtime reference](skills/mozglo-orchestrator/references/runtime.md) for runner commands, [architecture](docs/architecture.md), and [report guidance](skills/mozglo-orchestrator/references/report.md). Validate package files with `python3 scripts/validate.py`.

MOZGLO Orchestrator is an independent project by [mozglo](https://github.com/mozglo). [MIT license](LICENSE).
