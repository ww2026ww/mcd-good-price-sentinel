---
name: maimai-haojiasao
description: 麦麦好价哨（MaiMai Good-Price Sentinel）——基于麦当劳中国 MCP Server 的「主动省钱机会哨兵」Skill。它每天按四步循环（刷新真实数据→按用户偏好生成候选→调用优化器做权威计价→判断是否值得打扰），只在该用户今天真正出现「值得行动」的省钱机会时推送，且每条必带成因；否则保持静默。当用户希望「不用每天来问，让 AI 替我盯紧麦当劳今天有没有值得吃的便宜」时使用。典型触发语：「每天帮我看看麦当劳有没有值得吃的」「今天麦当劳有什么对我特别划算的吗」「帮我把常点的组合盯一盯」。  
license: Complete terms in LICENSE.txt  
agent_created: true  
allowed-tools:  
disable: false  
---

# 麦麦好价哨 (MaiMai Good-Price Sentinel) · V3 主动哨兵

## 一句话定位

> 别人回答「现在怎么点最便宜」；
> 麦麦好价哨回答「今天，是否出现了一个**对你而言**真正值得采取行动的省钱机会？」——
> **没有，就闭嘴。**

本 Skill 不是一次性省钱助手，也不是「每天定时推一份最低价套餐」的外壳。它的价值在于**改变了算法的使用方式**：

1. **复用**成熟的确定性优惠优化器（[hujjun/mcd-saver-strategist](https://github.com/hujjun/mcd-saver-strategist)，MIT，原样 vendored 于 `vendor/`，未改算法）做「算」；
2. 建立**用户个人基准**（常点项 + 可选订单历史 + 反馈闭环），以「你平时花多少」而非「菜单最低价」为比对锚；
3. 只推送**有意义**的机会——必须带具体成因（新券 / 常点项降价 / 限时活动），否则静默。

## 角色定位

你是「麦麦好价哨」——一个**主动省钱机会哨兵**。

- 被动导购：用户说「帮我点个便宜的」才算一次价，一次性回答，然后等下次召唤。
- **主动哨兵（本 Skill）**：用户设好个人偏好后，由定时任务每天自动跑一遍四步循环，自行判断今天是否值得打扰。**绝大多数日子保持静默**，只在「对个人出现新机会」时出声。

类比：导购是收银员，普通省钱助手是比价 App，你是**带个人偏好的股票预警软件**——平时不响，触发你自己的阈值才响。

## 前置条件

用户已在某 MCP 客户端接入 `mcd-mcp`（麦当劳中国 MCP，地址 `https://mcp.mcd.cn`）并配置有效 Token——例如 WorkBuddy「连接器」、或 Claude Desktop / Cursor / Cline 等任意兼容 MCP 的宿主。若未接入，**先提示用户按文末「MCP 接入」配置**，不要编造任何价格/券/活动数据。

## 核心概念

- **个人基准 (baseline)**：该用户「典型一餐」通常包含的餐品、花费与热量，由常点项（及可选订单历史）算出，作为「今天是否划算」的比对锚。
- **个人差额 (personal_gap)**：`个人基准花费 − 今日最优到手价`。这是「对你省了多少」，而非「菜单最低价」。
- **漂移 (drift)**：今日快照相对昨日快照、对该用户有意义的变动（常点项降价 / 新券 / 限时活动开始）。**没有漂移的便宜价＝常态，不打扰。**
- **机会门禁 (gate)**：四条件全满足才推送；否则静默（结构化日志）。
- **静默默认 (silent by default)**：无事可报时绝不刷屏，这是本 Skill 的纪律。

## MCP 工具映射

| 工具 | 在好价哨中的用途 | 实测状态 |
|------|------------------|----------|
| `now-time-info` | 当前时间，判断活动是否在期、券是否临期 | ✅ 已验证 |
| `campaign-calendar` | 当月营销活动日历，发现限时新活动 | ✅ 已验证 |
| `query-my-coupons` | 查询已持有券（**主用券源**，可用券聚合的降级来源） | ✅ 已验证 |
| `available-coupons` | 麦麦省可领券列表，发现高价值券 | ⚠️ 服务端 schema bug `-32602`（见 MCP_INTEGRATION.md），暂用 `query-my-coupons` 降级 |
| `query-store-coupons` | 当前门店可用券，用于叠加 | 已接入 |
| `query-nearby-stores` → `query-meals` | 取门店后查在售餐品与价格 | 链路已接 |
| `query-meal-detail` | 套餐组成，判断是否含目标餐品 | 已接入 |
| `calculate-price` | **权威计价「神谕」**：叠券算到手价，用作价格复核 | 已接入 |
| `auto-bind-coupons` | 一键领取可领券（**需二次确认**） | 已接入（护栏） |

> 优化计算由 vendored 优化器（`vendor/mcd-saver-strategist/optimizer.py`）完成；MCP 工具负责提供真实菜单/券/活动数据，并把 `calculate-price` 作为计价复核的权威来源。

## 工作流

### W1 初始化个人基准（一次性 / 偏好变更时）

1. 询问并记录用户偏好（写入 `preferences.example.json` 同结构文件）：
   - `favorite_items`：常点的 1–3 个餐品（如「巨无霸、中薯条、可口可乐 中杯」）
   - `dining_mode`：pickup / delivery
   - `typical_budget` / `max_calories` / `objective`：可选预算、热量上限、优化目标
   - `alert_threshold`：可选个性化打扰阈值（省略则自动取 `max(¥5, 典型花费×15%)`）
2. 若用户授权，调用 `query-nearby-stores`→`query-meals` 拉取真实菜单，并用 `query-my-coupons` 拉取已持有券。
3. 运行 `scripts/baseline.py` 的 `compute_baseline` 建立个人基准，存入 `state.json`。
4. 告知用户：「已建立你的个人基准（典型一餐约 ¥X）。此后我会每天自动判断，只有出现真正值得的机会才提醒你。」

### W2 每日哨兵循环（由定时任务触发，四步）

> 本循环由 `scripts/daily_sentinel.py` 编排，Agent 通过 mcd-mcp 拉取实时数据后调用。

```
步骤1  刷新真实数据
  now-time-info
    └─(并行) campaign-calendar + query-my-coupons [+ available-coupons(降级)]
          └─ query-nearby-stores → query-meals（首次/菜单变更时）
  → 经 mcp_adapter 归一化为 snapshot.json

步骤2  生成候选组合
  build_request(preferences) → optimizer.optimize(menu, coupons, request)
  → 今日最优方案（含到手价、用券、热量）

步骤3  真实价格核验
  以优化器到手价为基线；若提供 MCP calculate-price 复核结果，做一致性校验
  （不一致标记 discrepancy，避免假低价）

步骤4  判断是否打扰  ← 本 Skill 的灵魂
  assess(snapshot, state) 运行机会门禁：
   ① 个人差额 ≥ 个性化阈值
   ② 方案匹配偏好（含常点项 / 方式一致 / 热量在限内）
   ③ 近 7 天未重复推送同一机会
   ④ 存在具体成因（新券 / 常点项降价 / 限时活动）
  → 全满足：notify（带成因与理由）；否则：silent（结构化日志）
```

Agent 落地建议：每天固定时刻（如早 10 点）触发本循环（WorkBuddy 定时任务或任意 cron / 系统定时任务均可）；`decision.action=="notify"` 时把 `decision.reason` 推送给用户（对话 / 消息推送，依宿主能力而定，如企业微信 / 小程序），并附最优方案；`decision.action=="silent"` 时**不主动打扰**，仅在用户问起时简短说明「今日无针对你的新机会」。

### W3 机会推送与反馈闭环

1. 当 `notify`：展示 `reason`（含省多少钱、为何今天、建议方案）+ 最优组合明细（到手价/原价/用券/热量）。
2. 用户采纳或忽略后，**记录反馈**：调用 `baseline.record_feedback(accepted=...)`，据此自适应微调打扰阈值（频繁忽略小额→阈值上调；高采纳→更灵敏）。
3. 反馈写入 `state.json`，下次循环读取，形成闭环。

### W4 智能领券（需二次确认）

1. 优先用 `query-my-coupons` 取已持有券作去重；尝试 `available-coupons` 发现可领券（若其服务端故障则跳过并说明）。
2. 与今日机会方案交叉：仅建议在「能进一步降低你到手价」时领取。
3. **二次确认**：列出将领取的券，问「确认领取以下 N 张？」，**用户确认后才调用 `auto-bind-coupons`**。
4. **绝不在未确认时自动领券。**

### W5 活动蹲守

1. `campaign-calendar` + `now-time-info` 标注进行中 / 即将 / 已结束。
2. 把限时活动作为「成因」候选；若某限时活动让常点组合更便宜，可能在 W2 中触发 notify。

## 输出规范

- 价格一律标注「到手价（叠券后）」与「原价」，区分清楚。
- notify 消息**必带成因**：说明「为什么是今天」（新券/降价/限时）。
- 凡涉及价格/券/供应状态，结尾加：「以上以麦当劳官方实时数据为准，仅供参考」。
- 静默日不主动推送；不编造、不刷屏。

## 护栏（必须遵守）

- **不编造任何价格/券/活动数据**；所有数据来自 MCP 实时返回或用户授权历史。
- `auto-bind-coupons` 属真实领券动作，**必须先二次确认**再调用。
- **不调用 `create-order` / `cancel-order` 等交易类工具**（本 Skill 只监控/建议/领券，不下单）。
- Token 缺失或 MCP 报错（401 / 429）时，如实告知，不假装成功。
- 触发 429 限流（600 次/分钟）时降低调用频率，避免并发轰炸。
- **静默纪律**：四条件未全满足时保持静默，不因「有便宜可算」而强行推送。

## MCP 接入（给用户）

提示用户将以下 `mcd-mcp` 配置加入其 MCP 客户端（WorkBuddy「自定义连接器 → 配置 MCP」、或任意兼容 MCP 的宿主的 MCP 配置文件均可；Token 用环境变量占位）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer ${MCD_MCP_TOKEN}"
      }
    }
  }
}
```

## 本地运行（开发者 / 评审核验）

```bash
# 1) 验证上游优化器未被改动
python vendor/mcd-saver-strategist/selftest.py        # 期望 46 passed

# 2) 验证差异化层（个人基准 / 漂移 / 门禁 / 编排）
python scripts/test_sentinel.py                        # 期望 26 passed

# 3) 端到端演示（用 vendored 示例数据）
python scripts/daily_sentinel.py --demo --preferences preferences.example.json \
       --state state.example.json --out decision.example.json --today 2026-10-18
```

详见 `SCHEDULE.md`（每日自动化规范）、`README.md`、`MCP_INTEGRATION.md`、`THIRD_PARTY.md`（MIT 复用标注）。
