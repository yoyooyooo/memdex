# memdex Agent-First 终局提案

日期：2026-05-20

状态：accepted / Phase 1 implemented

## 背景

`memdex` 的目标不是让用户或 agent 管理索引，而是让一个 repo 在新会话里像语义知识库一样可问、可定位、可校验。

当前命令面保留了管理视角：

```text
status -> ensure -> ask / locate
```

这条链路对人类调试清楚，但对 agent-first 不理想。agent 接到自然语言问题时，真实意图通常是：

```text
问代码库
找代码位置
```

不是先查看状态、再确保索引、再执行问题。因此终局应把 `ask` 和 `locate` 做成主入口，把 freshness 诊断和引导内收进主入口。

## 终局判断

默认入口：

```bash
memdex ask --repo . "question"
memdex locate --repo . "thing to find"
```

`status` / `ensure` / `refresh` 不删除，但下沉为维护命令：

```text
status          调试当前配置、本地状态、fingerprint、source 记录
ensure --yes    预热索引、批处理、CI、用户明确授权首次上传
refresh --force 明确强制刷新 provider source
```

高频 agent 路径应从三次调用降为一次调用。只有遇到 blocked 状态时，才展开下一步引导。

## 命令职责

### `ask`

职责：回答架构、实现、文档、模块关系等语义问题。

内部流程：

```text
freshness preflight
-> 必要时刷新 provider source
-> provider Q&A
-> 输出答案和必要 freshness warning
```

`ask` 不承诺本地行号准确。涉及文件、符号、测试、命令位置时，应转用 `locate` 或在回答后本地打开文件验证。

### `locate`

职责：定位文件、函数、测试、命令、配置落点。

内部流程：

```text
freshness preflight
-> provider 召回候选 paths / symbols / tests / keywords
-> 本地验证 path 是否存在
-> 本地 rg 定位 line refs
-> 输出 local_line_refs 和 stale paths
```

可信边界：

```text
provider = semantic recall
local checkout = exact path / line / evidence authority
```

### `status`

职责：诊断，不是主路径。

适合：

- 用户明确问索引状态
- agent 遇到异常后调试
- 检查 config/state/source/fingerprint

不适合：

- 每次 `ask` 前置调用
- 新会话默认第一步

### `ensure`

职责：维护 freshness，不是问答入口。

适合：

- CI / cron 预热
- 人类明确要先上传
- 首次 broad upload 已授权
- 批处理前降低后续延迟

不适合：

- agent 每次问答前显式调用

### `refresh --force`

职责：显式强制重建和替换 source。

适合：

- 用户要求强刷新
- freshness critical
- provider 明显滞后且 throttling 不应等待

## Freshness 状态机

`ask` / `locate` 必须内置 freshness preflight，并按状态决定是否继续 provider 调用。

```text
not-initialized
  -> 不调用 provider
  -> 输出 init / reuse 引导

needs-first-upload-approval
  -> 不调用 provider
  -> 输出 ask --yes / ensure --yes 引导

fresh-ttl / fresh-fingerprint / fresh-bundle-hash / uploaded
  -> 调用 provider

stale-throttled
  -> 可调用 provider
  -> 输出 warning：provider answer may lag local changes

auto-refresh-disabled
  -> 可调用 provider
  -> 输出 warning：auto refresh disabled

provider error
  -> 返回 provider stderr/stdout 摘要
  -> locate 可降级到 local rg fallback
```

关键规则：

- blocked 状态不继续 provider，避免浪费调用和误导答案。
- warning 状态可以继续 provider，但必须明确 freshness 边界。
- plain output 默认短；`--verbose` / `--json` 才暴露完整 freshness。

## 新会话 Agent 策略

当用户问 repo 相关问题：

```text
解释/架构/设计/关系 -> ask
找文件/函数/测试/命令 -> locate
不确定是解释还是定位 -> locate 先找落点，再本地读文件
```

默认不跑：

```text
status
ensure
refresh
```

只有这些条件触发额外命令：

```text
用户明确问状态 -> status
用户明确授权首次上传 -> ask --yes 或 ensure --yes
用户明确要求强刷新 -> ask --force-refresh / locate --force-refresh / refresh --force
provider 输出 stale path -> locate 本地校验或 fallback rg
```

## locate 终局能力

当前 `locate` 是可用 MVP：provider 召回候选，本地 `rg` 校验行号。终局应升级为稳定 code locator。

目标输出：

```text
freshness
query_intent
provider_candidates:
  paths[]
  symbols[]
  tests[]
  commands[]
  keywords[]
local_line_refs:
  path
  line
  text
  score
  matched_by
  context
provider_misses_or_stale_paths[]
claim_boundary
next_local_reads[]
```

核心改进：

1. Provider prompt 要求结构化 JSON，解析失败才回退 regex。
2. 候选类型分层：paths、symbols、tests、commands、keywords。
3. 本地结果打分：候选 path 命中 > symbol 命中 > command/test 命中 > keyword 命中。
4. 输出按文件聚合，避免 80 条散乱 grep 结果。
5. 扩展路径识别：无扩展文件、前端文件、配置文件、脚本文件。
6. `rg` 缺失时返回明确错误和安装/回退建议。
7. 给 2-3 行上下文，减少 agent 后续二次读取成本。
8. 加 fixture 测试覆盖 JSON/prose/空候选/stale path/中文 query/缺 rg。

成熟度目标：

```text
alpha: provider prose + regex + rg
beta: structured candidates + ranked refs + blocked hard-stop + tests
stable: agent 可把 locate 输出作为改代码前的默认落点证据
```

## 输出策略

Plain output 面向 agent 和人类快速读：

```text
warning: ...
answer: ...
```

或：

```text
warning: ...
local_line_refs:
  - path:line score reason text
stale_paths:
  - ...
claim_boundary: line refs come from local checkout
```

JSON output 面向工具链：

```text
freshness
provider_answer / provider_candidates
local_verification
claim_boundary
next
```

默认不要打印完整 freshness 对象。完整 freshness 只在 `--json` 或 `--verbose` 出现。

## 实施顺序

### Phase 1：命令调用链收口（已落地）

- `ask` / `locate` 对 `needs-first-upload-approval` 硬停。
- 未初始化引导从 `status -> ensure -> ask` 改成 `init -> ask`。
- `SKILL.md` Quick Start 改为 ask-first / locate-first。
- Workflow 文档说明 `ask` / `locate` 内置 freshness preflight。
- 新增单元测试覆盖 `ask` / `locate` blocked preflight 不调用 provider。

验收：

```text
新会话 agent 看到 skill 后，会直接调用 ask 或 locate。
首次未授权上传不会继续 provider。
status/ensure 不再出现在默认问答路径。
```

落地文件：

```text
packages/memdex/scripts/memdex.py
packages/memdex/tests/test_memdex_cli.py
skills/memdex/SKILL.md
skills/memdex/references/workflow.md
```

### Phase 2：locate 结构化召回

- provider prompt 改为 JSON contract。
- 增加 JSON parser 和 prose fallback。
- candidate 类型分层。
- stale path 明确输出。

验收：

```text
provider 返回结构化结果时不依赖 regex。
provider 返回 prose 时仍可降级。
空候选时有本地 fallback。
```

### Phase 3：本地排序和上下文

- 本地 rg 结果按文件聚合。
- 加 score / matched_by。
- 加 context。
- 支持更完整 path matcher。

验收：

```text
locate 输出前 5 条结果足够 agent 判断下一步读取文件。
噪声明显低于裸 rg OR 查询。
```

### Phase 4：测试和回归门

- 增加 fixture 测试。
- 覆盖 blocked freshness、provider JSON、provider prose、stale path、中文 query、缺 rg。
- 给出最小 smoke 命令。

验收：

```text
核心解析和本地校验无需真实 NotebookLM 即可测试。
真实 provider 只进入 smoke，不进入单元测试硬依赖。
```

## 非目标

- 不删除 `status` / `ensure`。
- 不把 provider 变成行号权威。
- 不让 `ask` 承担本地代码定位职责。
- 不在 plain output 默认暴露大量 freshness 元数据。
- 不把首次 broad upload 变成隐式行为。

## 最终口径

`memdex` 的 agent-first 终局是：

```text
ask/locate 是用户意图入口。
ensure/status 是维护和诊断入口。
freshness 是主入口内部能力，不是 agent 默认前置步骤。
locate 的可信证据必须来自 local checkout。
```
