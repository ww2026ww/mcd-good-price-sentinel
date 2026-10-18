# MCP 集成说明 (MCP Integration)

本文说明「麦麦好价哨」实际使用的麦当劳 MCP Server、具体 Tool、调用流程与业务价值。

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

## 2. 实际使用的 Tool 与调用流程

本 Skill 真实使用以下 9 个麦当劳 MCP 工具，按四大工作流编排：

### W1 好价速览
```
now-time-info
  └─（并行）campaign-calendar + available-coupons + query-meals
        └─（指定餐品时）query-meal-detail → calculate-price（叠券算到手价）
```
**业务价值**：一次对话看清"当前最值得买什么 + 到手价 + 可用券 + 活动叠加"。

### W2 阈值预警（哨兵核心）
```
用户设阈值 → 会话记忆
  └─ 每次查询：query-meal-detail → calculate-price → 与阈值比对
        └─ 达标：⚠️ 告警；未达标：报告价差
```
**业务价值**：把"用户手动刷价"变成"AI 替你盯盘"，命中阈值才打扰。

### W3 智能领券
```
available-coupons → query-my-coupons（查重）→ 排序高价值券
  └─ 二次确认 → auto-bind-coupons → 回报结果
```
**业务价值**：避免重复领券、误领；领前强制确认，杜绝误操作。

### W4 活动蹲守
```
campaign-calendar → now-time-info（标注进行中/即将/已结束）→ 解读力度 →（有券则衔接 W3）
```
**业务价值**：把当月活动翻译成"现在买什么最划算"的可行动建议。

## 3. 工具职责对照表

| Tool | 在好价哨中的职责 |
|------|------------------|
| `now-time-info` | 当前时间，判断活动是否在期、券是否临期 |
| `campaign-calendar` | 当月营销活动日历，发现新限时优惠 |
| `available-coupons` | 麦麦省可领券列表，发现高价值券 |
| `query-my-coupons` | 查询已持有券，避免重复领取 |
| `auto-bind-coupons` | 一键领取可领券（需二次确认） |
| `query-store-coupons` | 当前门店可用券，用于叠加 |
| `query-meals` | 当前可售餐品与价格 |
| `query-meal-detail` | 套餐组成，判断是否含目标餐品 |
| `calculate-price` | 叠券计算到手价，判断是否达阈值 |

## 4. 业务价值总结

- **对用户**：在麦当劳"动态定价 + 海量券池"中，用最低认知成本抓住最优价格点。
- **差异化**：相比一次性"省钱助手"，好价哨是**哨兵模式**——设阈值得告警，而非每次手动问。
- **安全**：只做查询与领券（`auto-bind-coupons` 强制二次确认），**不调用 `create-order`/`cancel-order` 等交易类工具**，无真实下单风险。

## 5. 真实使用验证状态

- [x] Skill 已编写并接入 McDonald's MCP 工具编排逻辑
- [x] `mcp-config.example.json` 已按官方格式脱敏配置
- [ ] **真实联调验证**：待参赛者提供 MCP Token 后，对 `now-time-info` / `campaign-calendar` / `available-coupons` 等进行端到端调用验证，结果回填本文件与 `workbuddy.md`

> 验证记录将补充于下方：
>
> _（待 Token 联调后填写：调用了哪些 Tool、返回了什么、阈值预警/领券流程是否跑通）_
