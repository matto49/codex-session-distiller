# Codex Session Distiller

[English](README.en.md) · [MIT License](LICENSE)

**先把 Codex 历史任务蒸馏成可继续工作的摘要，再通过官方接口归档旧任务。**

这是一个可安装的 Codex Skill，附带零第三方 Python 依赖的脚本。适合任务列表越来越长、旧调查难以找回，以及归档前需要保留上下文的场景。

```text
只读快照 → 分段蒸馏 → 来源校验与内容审阅 → 保留规则 → 官方归档 → 原文件读回
```

## 提供什么

- 每个任务独立生成目标、结果、决策、证据、未解决事项和下一步摘要。
- 长任务按阶段处理并合并，保留阶段摘要和原始行号；支持缓存与断点续跑。
- 模型命令可配置，不绑定账号、API key、模型或单一提供商。
- 默认保留最近 30 天有活动、置顶、自动化、未完成 Goal、来源变化和内容不完整的任务。
- 父子任务保护：先归档子任务，有受保护后代的父任务也保留。
- 每次归档前重新检查；通过官方 `thread/archive` / `thread/unarchive` 接口操作。
- 保留原始 JSONL，记录请求与结果，用 SHA-256 核对归档/恢复后的原始内容。

摘要需要按需读取，不会自动注入每个新任务。归档主要整理活跃列表，不承诺释放磁盘空间或消除全部卡顿。

## 安装 Skill

把仓库中的 `skills/codex-session-distiller` 文件夹复制到你的 Codex skills 目录：

```sh
git clone https://github.com/matto49/codex-session-distiller.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-session-distiller/skills/codex-session-distiller \
  "${CODEX_HOME:-$HOME/.codex}/skills/"
```

如果同名 Skill 已存在，先检查本地改动，再选择更新方式。也可以把该文件夹交给支持 GitHub Skill 安装的工具。安装后，在能发现新 Skill 的任务中使用：

> 使用 $codex-session-distiller 整理我的 Codex 历史。先生成可追溯摘要，检查结果后，归档符合保留规则的旧任务。

只想生成摘要时，明确说“先不要归档”即可。Skill 会沿用本次任务中已有的授权，不会把摘要请求扩展成删除请求。

## 独立运行

要求 Python 3.11+；归档/恢复需要支持 app-server 协议的官方 Codex 二进制。支持范围为 macOS/Linux，远程机器在源主机上运行同一套命令。

```sh
python3 skills/codex-session-distiller/scripts/session_distiller.py --help
python3 skills/codex-session-distiller/scripts/session_distiller.py snapshot \
  --out "$HOME/codex-session-runs/first-pass" --label workstation
```

后续使用 `distill`、`verify`、`review`、`plan`、`archive` 和 `status`。归档命令不带 `--apply` 只预览。完整示例见 [操作流程](skills/codex-session-distiller/references/workflow.md)。

| 文档 | 内容 |
|---|---|
| [SKILL.md](skills/codex-session-distiller/SKILL.md) | Agent 使用入口与决策规则 |
| [操作流程](skills/codex-session-distiller/references/workflow.md) | 命令、双机处理、断点恢复、取消归档 |
| [摘要适配器](skills/codex-session-distiller/references/summarizer.md) | 模型输入/输出协议与示例 |
| [兼容性](skills/codex-session-distiller/references/compatibility.md) | Codex 数据结构、官方接口与已知边界 |

## 验证与边界

```sh
python3 -m unittest discover -s tests -v
```

测试只使用临时合成任务和模拟 app-server，覆盖来源变化、错误引用、跨任务错配、父子保护、活动 writer、拒绝后不重试、断点对账、归档和恢复。测试服务器要求合成目录标记，不能用于真实 Codex 数据。

结构校验能证明引用指向相应来源，不能证明每句话都正确。归档要求审阅回执，回执必须对应实际完成的内容审阅。图片、完整工具输出和不可读压缩历史不冒充已验读；有提取缺口的任务保留。

公开版从一次真实双机整理流程提炼并重写，仓库只包含通用代码、文档和合成测试。原流程的运行结果不等于这个通用版本已经适配所有 Codex 版本。存储和协议变化时停止检查，详见兼容性文档。

运行目录包含私有对话、摘要、路径和模型错误输出，请放在公开仓库之外。内置脱敏是尽力而为；模型 CLI 仍可能调用远程提供商。脚本不内置上传服务，也不直接修改 Codex 数据库、认证文件或生成记忆。

## 许可证

[MIT](LICENSE)，允许个人和商业使用。
