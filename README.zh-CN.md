# memdex

[English](README.md) | [简体中文](README.zh-CN.md)

面向 AI agent 的项目语义检索工具。它使用 NotebookLM、repomix 快照、
freshness 检查和本地证据校验，帮助 agent 在大型 repo、vault、文档集或外部
参考源码中先找到方向，再回到本地 checkout 确认证据。

`memdex` 的目标不是把 LLM 回答当成事实，而是把 NotebookLM
作为语义定位器：先召回相关概念、文件和关键词，再用本地文件、测试、命令输出或
项目权威文档确认精确路径、行号和结论。

> [!IMPORTANT]
> NotebookLM 只作为 discovery/locator 使用，不是 authority。精确文件路径、行号、
> 实现状态、测试结果和完成声明必须来自本地文件、测试、命令输出或项目权威文档。

## 为什么需要

大型项目、vault 和参考源码集对冷启动 agent 不友好。`rg` 很精确，但前提是
agent 已经知道应该搜什么。NotebookLM 能找到相关概念和可能的文件，但回答可能滞后，
也经常不能提供可靠行号。

这个项目把两者组合起来：

- NotebookLM 负责宽召回和语义定位。
- Repomix 负责生成可复现、可审查的仓库快照。
- 本地 `rg` / `sed` / `nl` 负责精确证据校验。
- Freshness 检查让 agent 知道 provider 结果是否可能落后于当前 worktree。

## 功能

- `ask`：面向项目 notebook 的语义问答。
- `locate`：先用 provider 召回文件/符号候选，再输出本地行号引用。
- 每次查询前执行 TTL 和 fingerprint preflight。
- 首次 broad upload 需要显式授权。
- 增量 chunk 上传，保持稳定的 whole-file chunk 规划。
- 只清理由本工具记录的 NotebookLM source ID。
- 支持临时 NotebookLM source，用于笔记、学习材料等派生内容。
- 提供带 `memdex` CLI 的 npm package。
- `skills/` 下提供 Codex/OpenAI 风格 skill 文件。

## 工作方式

```text
repo checkout
  -> .memdex/config.json
  -> repomix bundle chunks
  -> NotebookLM source set
  -> ask / locate provider query
  -> local path and line verification
```

`ask` 和 `locate` 是主入口。`status`、`ensure`、`refresh` 是维护命令；
正常 agent 工作流应直接调用 `ask` 或 `locate`。

## 仓库结构

```text
packages/memdex/ npm package 和 CLI 实现
skills/memdex/   Project retrieval agent skill
skills/notebooklm/          NotebookLM 自动化辅助说明
docs/                       设计说明和决策背景
```

在 monorepo checkout 中，使用 Bun workspace script：

```bash
bun run memdex -- --help
```

通过 npm package 安装后，CLI 命令是 `memdex`。如果你包装或移动了脚本，
可设置 `MEMDEX_CMD`，让工具生成的下一步命令指向你的 wrapper。

## 环境要求

- Python 3.10+
- `git`
- `rg`，用于本地行号校验
- `repomix` 或 `npx repomix`
- 来自 `notebooklm-py` 的 `notebooklm` CLI

安装并认证 NotebookLM CLI：

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
notebooklm login
notebooklm auth check --test
```

## 快速开始

初始化目标仓库：

```bash
bun run memdex -- init \
  --repo /path/to/repo \
  --create-notebook
```

询问架构或文档问题：

```bash
bun run memdex -- ask \
  --repo /path/to/repo \
  "Where is retry/backfill documented?"
```

定位可能的实现文件和本地行号：

```bash
bun run memdex -- locate \
  --repo /path/to/repo \
  "invoice export retry command"
```

首次 broad upload 默认会被阻止。确认 source scope 后，用 `--yes` 显式批准：

```bash
bun run memdex -- ask \
  --repo /path/to/repo \
  --yes \
  "Where is retry/backfill documented?"
```

## Source Scope

默认 include roots 覆盖常见源码、测试、文档和命令锚点：

```text
src, crates, packages, apps, bins, docs, scripts, tests, xtask,
AGENTS.md, CLAUDE.md, README.md, Cargo.toml, package.json, justfile
```

默认安全排除项会拦截常见敏感或噪声路径：

```text
.env*, credentials, .git, node_modules, target, dist, build, coverage,
generated caches, public assets, large binary/media/archive files
```

批准首次 broad upload 前，应先检查 `.memdex/config.json`。

## 安全模型

- 不上传凭据、原始私有日志、生产导出、私有用户数据、依赖目录、构建产物、
  生成缓存或未经审查的数据。
- 不信任 NotebookLM 给出的行号或文件存在性结论，必须本地校验。
- 不按 title prefix 删除 NotebookLM sources。除非用户显式选择更宽的清理范围，
  删除只针对本工具记录的 source ID。
- stale 或 blocked freshness 状态必须进入回答边界。

## NotebookLM 边界

本项目与 Google 无关联。项目依赖社区 `notebooklm-py` CLI，它通过非官方接口自动化
NotebookLM。Google 可能随时改变 NotebookLM 行为、限制、认证或内部 API。用户需要
自行遵守 Google 和 NotebookLM 条款，并且只上传自己有权在 NotebookLM 中处理的内容。

## 开发

运行本地检查：

```bash
bun install
bun run test
bun run check
```

控制面脚本刻意只依赖 Python 标准库。Provider、打包和搜索能力通过 subprocess
调用外部工具完成。

CI 和 npm 发布流程见 [docs/release.md](docs/release.md)。

## 致谢

本项目基于 Teng Lin 的社区项目 `notebooklm-py` 完成 NotebookLM 自动化：
https://github.com/teng-lin/notebooklm-py

本项目也使用 Repomix 生成适合 AI 消费的仓库快照：
https://github.com/yamadashy/repomix
