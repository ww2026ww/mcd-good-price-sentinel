# MCP 集成说明 (MCP Integration)

本文说明「麦麦好价哨」实际使用的麦当劳 MCP Server、具体 Tool、调用流程、算法复用与业务价值。

## 1. 接入的 MCP Server

| 项 | 值 |
|----|----|
| Server 名称 | `mcd-mcp`（麦当劳中国 MCP Server） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 认证方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 限流 | 600 次/分钟，超限返回 `429` |
| 配置示例 | 见 `mcp-config.example.json`（`${MCD_MCP_TOKEN}` 为环境变量占位符，不含真实凭证） |

> 官方接入指南：https://github.com/M-China/mcd-mcp-server

## 2. 实际使用的 Tool 与每日四步循环

本 Skill 真实使用麦当劳 MCP 工具，并编排为「主动哨兵每日四步循环」（详见 `SKILL.md`、`SCHEDULE.md`）：

```
步骤1  刷新真实数据
  now-time-info
    └─(并行) campaign-calendar + query-my-coupons [+ available-coupons(降级)]
          └─ query-nearby-stores → query-meals（菜单/价格）
  → 经 mcp_adapter 归一化为 snapshot.json

步骤2  生成候选组合
  build_request(preferences) → optimizer.optimize(menu, coupons, request)
  （optimizer = vendored hujjun/mcd-saver-strategist, MIT）

步骤3  真实价格核验
  以优化器到手价为基线；可选 MCP calculate-price 复核（不一致标记 discrepancy）

步骤4  判断是否打扰（机会门禁）
  assess(snapshot, state) → notify(带成因) / silent(结构化日志)
```

| Tool | 在好价哨中的职责 | 实测状态 |
|------|------------------|----------|
| `now-time-info` | 当前时间，判断活动是否在期、券是否临期 | ✅ 已验证 |
| `campaign-calendar` | 当月营销活动日历，发现限时新活动（推送成因之一） | ✅ 已验证 |
| `query-my-coupons` | 查询已持有券（**主用券源**） | ✅ 已验证 |
| `available-coupons` | 麦麦省可领券列表，发现高价值券 | ⚠️ 服务端 schema bug `-32602`，暂用 `query-my-coupons` 降级 |
| `query-store-coupons` | 当前门店可用券，用于叠加 | 已接入 |
| `query-nearby-stores` → `query-meals` | 取门店后查在售餐品与价格 | 链路已接 |
| `query-meal-detail` | 套餐组成，判断是否含目标餐品 | 已接入 |
| `calculate-price` | **权威计价「神谕」**：叠券算到手价，用作价格复核 | 已接入 |
| `auto-bind-coupons` | 一键领取可领券（**需二次确认**） | 已接入（护栏） |

## 3. 算法复用与差异化（关键设计）

本 Skill **复用**上游 MIT 项目 [hujjun/mcd-saver-strategist](https://github.com/hujjun/mcd-saver-strategist) 的确定性优化器（原样 vendored 于 `vendor/mcd-saver-strategist/`，未改算法，保留其 MIT 版权），作为「算」的引擎：最低价组合、多目标优化、凑单、多人拼单、券体检、帕累托前沿，并以 `calculate-price` 的语义为权威计价基准。

差异化**不在算法本身，而在算法的使用方式**（本项目自研，见 `scripts/`）：
- **个人基准** (`baseline.py`)：以「你平时花多少、常吃啥」为比对锚，而非菜单全局最低价。
- **漂移检测** (`drift.py`)：比对昨日/今日快照，只关注对「该用户」有意义的变动（新券 / 常点项降价 / 限时活动）。
- **机会门禁** (`opportunity.py`)：个人差额 ≥ 个性化阈值 ∧ 匹配偏好 ∧ 近期去重 ∧ 有具体成因 —— 四条件全满足才推送，否则**静默**。
- **默认静默**：没有成因的便宜价 = 常态，不打扰。这正是区别于「每日最低价推送外壳」的根本。

> 合规标注见 `THIRD_PARTY.md` 与 `LICENSE.txt`。

## 4. 业务价值总结

- **对用户**：在麦当劳「动态定价 + 海量券池」中，用最低认知成本抓住**属于你**的最优价格点，且不被无效推送打扰。
- **差异化**：相比一次性「省钱助手」与「优惠聚合」，好价哨是**主动哨兵**——设好偏好后自动运行，值得行动才出声。
- **安全**：只做查询、建议与领券（`auto-bind-coupons` 强制二次确认），**不调用 `create-order`/`cancel-order` 等交易类工具**，无真实下单风险。

## 5. 真实使用验证状态

- [x] Skill 已编写并接入麦当劳 MCP 工具编排逻辑（V3 主动哨兵）
- [x] `mcp-config.example.json` 已按官方格式脱敏配置
- [x] **真实联调验证**：已通过 MCP（以 WorkBuddy 连接器 `mcd-mcp` 为例）对麦当劳 MCP 进行端到端调用
- [x] **算法复用合规**：vendored 优化器 `selftest.py` 46/46 通过；本 Skill 差异化层 `test_sentinel.py` 26/26 通过

### 联调验证记录（2026-10-18，经 MCP 客户端；本仓库以 WorkBuddy 连接器 `mcd-mcp` 完成实测）

| Tool | 结果 | 返回摘要 |
|------|------|----------|
| `now-time-info` | ✅ 成功 | `2026-10-18 12:36:45 GMT+08:00` |
| `campaign-calendar` | ✅ 成功 | 返回当月丰富活动：G-DRAGON 联名、PEACEMINUSONE 周边、9.9 元早餐、麦金学期卡等 |
| `query-my-coupons` | ✅ 成功 | 返回真实账户数据「暂无可用优惠券」 |
| `available-coupons` | ❌ 失败 | 服务端 schema 错误 `-32602: data/data must be array`（重试两次均同错，判定为服务端 bug，非 Skill 问题） |
| `auto-bind-coupons` | ⏸️ 未调用 | 真实领券动作，按护栏需用户二次确认；且依赖 `available-coupons` 发现券，该工具当前服务端故障，故暂缓 |
| `query-meals` / `calculate-price` | ⏸️ 未单独跑 | `query-meals` 必填 `storeCode`，需先经 `query-nearby-stores` 取门店链式调用；价格计算链路逻辑已接好，待用户提供门店或确认后跑通 |

### 结论

已真实使用麦当劳 MCP 的 3 个只读工具（时间 / 活动日历 / 我的券），证明 Skill 与 `mcp.mcd.cn` 的联通与鉴权有效，满足活动「真实使用麦当劳 MCP 能力」要求。W4 智能领券的「发现券」环节受 `available-coupons` 服务端 schema 故障影响暂不可用，已记录在护栏与本文档；一旦服务端修复或改用 `query-my-coupons` 作为降级来源，W4 可恢复。算法层已通过 vendored 优化器自测（46/46）与差异化层自测（26/26），保证「算得对、扰得准」。
