# memdex

[English](README.md) | **简体中文**

Memdex 是面向 agent 的语义定位器，用于本地项目、代码仓库、vault 和源码集。

它帮助 AI agent 从“不知道去哪找”推进到“有本地精确证据”。它用 NotebookLM
做宽语义召回，用 repomix 做确定性源码快照，用 freshness 检查暴露滞后边界，
再用本地文件或命令作为最终事实来源。

> [!IMPORTANT]
> NotebookLM 是 locator，不是 authority。精确路径、行号、实现状态、测试结果和
> 完成声明必须来自本地文件、测试、命令输出或项目权威文档。

## 为什么需要

大型项目和参考源码集对冷启动 agent 不友好。`rg` 很精确，但前提是 agent
已经知道该搜什么。NotebookLM 能找到相关概念和可能文件，但回答可能滞后、不完整，
也可能给出错误行号。

Memdex 把两种模式接起来：

```text
语义召回 -> 本地校验 -> 有证据的回答
```

目标不是替代本地搜索，而是让本地搜索从更好的候选开始，然后把精确结论压回当前
checkout 里验证。

## Locator 类比

把 Memdex 看成 agent 的地图读图器。

一开始，agent 可能只有一个模糊问题。NotebookLM 先扩大搜索空间，给出概念、文件、
符号、测试或命令候选。Memdex 再把这些候选收窄到本地 checkout，直到 agent 拿到
能安全引用的路径和证据。

```text
模糊问题 -> 语义候选 -> 本地路径 -> 已验证行引用
```

如果语义索引可能落后于 worktree，Memdex 会暴露 freshness 状态，而不是假装
provider 已经最新。

## Agent-first，而不是 Index-first

传统检索工具经常要求调用方先管理索引：

```text
status -> ensure -> ask
```

Memdex 把 `ask` 和 `locate` 做成正常入口。它们内部执行 freshness preflight，
策略允许时自动刷新，缺少用户授权时停止，并在 blocked 时打印可执行的下一条命令。

使用规则：

- `ask` 用于架构、设计、文档、模块关系和宽项目问题。
- `locate` 用于文件、符号、测试、命令、配置和本地行号。
- `status`、`pack`、`ensure`、`refresh` 用于维护或排障，不是日常 agent Q&A 入口。

## 核心对象

| 术语 | 人话 |
| --- | --- |
| Source Set | 被索引的本地项目、repo、vault 或参考语料 |
| `.memdex/config.json` | 本地 source scope、provider、notebook 和策略配置 |
| Source Scope | 生成快照时的 include roots 和安全排除项 |
| Repomix Bundle | 从 source scope 生成的确定性文本快照 |
| Chunk | 上传到 NotebookLM 的稳定 whole-file bundle 单元 |
| Notebook Source | Memdex 创建并记录的 provider 侧 source |
| Freshness Preflight | 使用 provider 前的 TTL 和 fingerprint 检查 |
| Provider Answer | NotebookLM 输出，只用于 discovery，不是最终事实 |
| Local Verification | 证明精确结论的 `rg`、`sed`、`nl`、测试、命令或文件读取 |
| Temporary Source | 带 origin 记录和可选 TTL 的 NotebookLM 派生临时材料 |

## 工作方式

Memdex 每次查询先做一个路由判断：这是语义问答，还是要定位精确文件和行号？

```text
repo checkout
  -> .memdex/config.json
  -> repomix bundle chunks
  -> NotebookLM source set
  -> ask / locate provider query
  -> local path and line verification
```

`ask` 和 `locate` 都先执行 freshness preflight：

```text
未初始化或需要授权 -> 停止并给出下一条命令
fresh 或已刷新 -> 调 provider
stale 但允许继续 -> 带 warning 调 provider
provider 候选 -> 精确结论需要本地校验
```

涉及实现结论时，provider answer 只是线索。最终回答前必须打开本地文件、运行相关命令，
或检查测试。

## 安装

安装 npm package：

```bash
npm install -g memdex
memdex --help
```

从本 monorepo checkout 本地开发：

```bash
bun install
bun run memdex -- --help
```

Memdex 还需要它编排的外部工具：

- Node.js 20+
- Bun 1.2+，用于本地开发和 package build
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

## 如何使用

初始化目标 source set：

```bash
memdex init --repo /path/to/repo --create-notebook
```

询问架构或文档问题：

```bash
memdex ask --repo /path/to/repo "Where is retry/backfill documented?"
```

定位可能的实现文件和本地行号：

```bash
memdex locate --repo /path/to/repo "invoice export retry command"
```

从本 monorepo checkout 运行时，命令前加 `bun run memdex --`：

```bash
bun run memdex -- ask --repo /path/to/repo "Where is retry/backfill documented?"
```

首次 broad upload 默认被阻止。先检查 `.memdex/config.json`，再显式授权：

```bash
memdex ask --repo /path/to/repo --yes "Where is retry/backfill documented?"
```

如果你包装或移动了 CLI，设置 `MEMDEX_CMD`，让工具生成的下一步命令指向你的 wrapper。

## Worktree 复用

在轻量 Git worktree 中工作时，可以复用已索引的分支 worktree，避免给每个 feature
checkout 单独上传：

```bash
memdex ask --repo-worktree main "Where is retry/backfill documented?"
memdex locate --repo-worktree main "invoice export retry command"
```

`--repo-worktree` 必须从某个 Git worktree 内运行。它解析已 checkout 的分支
worktree，复用那里的 `.memdex` config 和 source state，默认不自动刷新。

只有明确想更新已索引分支 worktree 时才使用 `--force-refresh`。如果目标分支没有
checkout 成 worktree，用 `--repo` 显式传入已索引路径。

## 和 agent 对齐

人类负责上传授权、source scope、安全边界和最终验收。Memdex 负责机械检索路径：
snapshot、freshness preflight、provider query、候选提取和本地校验支持。

推荐 agent 流程：

```text
需要解释 -> memdex ask -> 精确结论补本地证据
需要位置 -> memdex locate -> 打开返回文件 -> 引用本地行号
需要当前索引状态 -> memdex status
需要强制重建 -> memdex refresh --force
```

不要每次问题前先跑 `status` 或 `ensure`。`ask` 和 `locate` 已经包含日常工作需要的
preflight。

## 滚动检索

Memdex 面向长工作会话中的重复 agent 使用。每一轮都应该让 agent 比上一轮拥有更清楚的
本地证据。

```text
question -> preflight -> provider recall -> local verification -> answer | next command
```

每轮结束时，agent 应该知道：

- provider 对 discovery 是否足够新；
- 哪些结论来自本地证据；
- 哪些路径是 stale、missing，或只是语义线索；
- 下一步该跑什么命令或读什么本地文件。

如果 freshness 被阻塞、缺少上传授权，或 provider 输出无法本地验证，agent 应该说明边界，
而不是把线索升级成事实。

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
  删除只针对 Memdex 记录的 source ID。
- stale 或 blocked freshness 状态必须进入回答边界。

## NotebookLM 边界

本项目与 Google 无关联。项目依赖社区 `notebooklm-py` CLI，它通过非官方接口自动化
NotebookLM。Google 可能随时改变 NotebookLM 行为、限制、认证或内部 API。用户需要
自行遵守 Google 和 NotebookLM 条款，并且只上传自己有权在 NotebookLM 中处理的内容。

## 快速查看

```bash
memdex --help
memdex ask --help
memdex locate --help
memdex status --repo /path/to/repo
memdex pack --repo /path/to/repo --dry-run --include-files
memdex ensure --repo /path/to/repo --yes
memdex refresh --repo /path/to/repo --force
memdex temp-source list --repo /path/to/repo
```

## CLI

```bash
memdex ask [--repo <repo> | --repo-worktree <branch>] [--yes] [--force-refresh] [--json] [--verbose] <question>
memdex locate [--repo <repo> | --repo-worktree <branch>] [--yes] [--force-refresh] [--include-provider-answer] [--json] [--verbose] <query>
memdex init [--repo <repo>] [--notebook-id <id>] [--project-name <name>] [--create-notebook] [--reuse-existing-notebook] [--include <specs>] [--force]
memdex status [--repo <repo>] [--json]
memdex pack [--repo <repo>] [--set-id <id>] [--dry-run] [--include-files] [--json]
memdex ensure [--repo <repo>] [--force] [--yes] [--json]
memdex refresh [--repo <repo>] [--force] [--json]
memdex temp-source upload --repo <repo> --kind <kind> --title <title> --file <file> [--ttl-seconds <seconds>] [--json]
memdex temp-source list [--repo <repo>] [--kind <kind>] [--json]
memdex temp-source cleanup [--repo <repo>] [--kind <kind>] [--set-id <id>] [--expired] [--include-untracked-prefix] --yes [--json]
```

`ask` 和 `locate` 是 agent 路径。`status`、`pack`、`ensure`、`refresh` 和
`temp-source` 是维护命令。

## 仓库结构

```text
packages/memdex/        npm package 和 CLI 实现
skills/memdex/          项目检索 agent skill
skills/notebooklm/      NotebookLM 自动化辅助说明
docs/                   设计说明和发布文档
```

CLI package 发布名是 `memdex`。

## 开发

```bash
bun install
bun run test
bun run check
```

CLI 使用 TypeScript 实现，通过 Commander 组织命令面，并用 Bun 打包用于
npm 发布。Provider、打包和搜索能力仍通过 subprocess 调用外部工具完成。

CI 和 npm 发布流程见 [docs/release.md](docs/release.md)。

## 致谢

本项目基于 Teng Lin 的社区项目 `notebooklm-py` 完成 NotebookLM 自动化：
https://github.com/teng-lin/notebooklm-py

本项目也使用 Repomix 生成适合 AI 消费的仓库快照：
https://github.com/yamadashy/repomix

## License

MIT
