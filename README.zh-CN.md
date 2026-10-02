<p align="center"><img src="assets/banner.png" alt="MOZGLO Orchestrator — Astra 规划、Sol 协调、Luna 实现" width="100%"></p>

<p align="center"><a href="LICENSE"><img alt="MIT 许可证" src="https://img.shields.io/badge/license-MIT-79b9c2.svg"></a> <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg"> <img alt="Codex skill" src="https://img.shields.io/badge/Codex-skill-20272b.svg"></p>

<p align="center"><strong>授权清晰、可恢复的 Codex 协作与明确分工。</strong><br>作者与维护者：<a href="https://github.com/mozglo">mozglo</a></p>
<p align="center"><a href="README.md">English</a> · <a href="README.ru.md">Русский</a> · <a href="skills/mozglo-orchestrator/SKILL.md">Skill</a> · <a href="LICENSE">MIT</a></p>

MOZGLO 是可独立安装的 Codex skill。原生 agents 足以完成任务时优先使用它们；实时协作通过原生消息进行。只有需要编排或恢复时才启动打包 runner，且只启动一次；运行中的 root 不会再次启动 runner 或 Codex CLI。Astra 负责带领和整合，Sol 负责协调与审查，Luna 负责所有产品实现。

## 安装与使用

通过 Codex 的 `skill-installer` 安装：

```text
https://github.com/mozglo/mozglo-orchestrator/tree/main/skills/mozglo-orchestrator
```

然后开启新的 Codex 会话，提交范围明确的任务：

```text
$mozglo-orchestrator <task>
```

## 角色与配置

默认配置请求一个 Astra、一个 Sol、五个 Luna（共七个 agent）；小任务可只用 Astra 和一个 Luna。总上限为 15 个 agent（包含 Astra），实际容量可能更低。设置可配置角色模型 ID、推理强度、数量、sandbox 和已有 Codex profile。默认请求模型为 `gpt-6-astra`、`gpt-6.1-sol`、`gpt-6-luna`，不保证实际模型身份或可用容量。便携 runner 需要 Linux、Python 3.10+ 和已有认证的 Codex CLI，仅使用 Python 标准库。

在 `.mozglo/context.md` 维护一份简短项目记录，包含 agent 名单、任务负责人、决定、已验证进展和下一步；通过原生消息同步实时状态。给用户的最终报告包含一至三张有用的技术图片和两至四行简短说明或链接；内部交接只用文字。

参阅 [runtime 参考](skills/mozglo-orchestrator/references/runtime.md)、[架构](docs/architecture.md)和[报告规范](skills/mozglo-orchestrator/references/report.md)。运行 `python3 scripts/validate.py` 检查包内容。

MOZGLO Orchestrator 是 [mozglo](https://github.com/mozglo) 的独立项目。[MIT 许可证](LICENSE)。
