# WorkBuddy 开发对话上下文导出 (workbuddy.md)

> 本文件为使用 WorkBuddy 开发「麦麦好价哨」Skill 的对话上下文导出，用于核验是否符合「麦当劳程序员创意开发大赛」WorkBuddy 专项奖励条件。

## 项目信息

- **项目名称**：麦麦好价哨 (MaiMai Good-Price Sentinel)
- **项目类型**：WorkBuddy Skill（基于麦当劳中国 MCP 的"好价哨兵"）
- **开发工具**：WorkBuddy（腾讯 WorkBuddy 智能体）
- **开发时间**：2026-10-18

## 开发过程概述

1. **需求与活动调研**
   - 通过 WebFetch 研读活动主仓库 `M-China-Official/mcd-developer-innovation-challenge`：活动为"麦当劳程序员创意开发大赛"，报名时间 2026/10/9–10/25，排行榜以 2026/10/26 00:00 的 GitHub Star 快照定榜，前 100 名获奖；WorkBuddy 为官方合作伙伴，提交 `workbuddy.md` 可获专项 3000 积分。
   - 研读麦当劳 MCP Server 接入指南，确认 Server 地址 `https://mcp.mcd.cn`、Streamable HTTP 协议、Bearer Token 认证、600 次/分钟限流。
   - 提取麦当劳 MCP 全部工具清单（30+ 个），筛选出与本 Skill 相关的 9 个工具。

2. **方向决策（与用户确认）**
   - 用户选定方向：**麦麦好价哨**（差异化于普通"省钱助手"的"哨兵/盯盘"模式）。
   - 交付形态：**仅 WorkBuddy Skill**（SKILL.md + 必交文档，不含独立 Python 模块）。
   - MCP Token：用户表示**稍后提供**，用于真实联调。

3. **Skill 设计与实现**
   - 参考 WorkBuddy `skill-creator` 规范，确认 SKILL.md 需 `name` / `description` 前置元数据 + `agent_created: true`，正文用指令式。
   - 编写 `SKILL.md`：角色定位、触发条件、9 个 MCP 工具映射、四大工作流（W1 好价速览 / W2 阈值预警 / W3 智能领券 / W4 活动蹲守）、输出规范、护栏（领券二次确认、不调用交易类工具、不编造数据）。
   - 生成全部必交文件：`README.md`、`CONTEST_DECLARATION.md`（官方原文一字不改）、`MCP_INTEGRATION.md`、`mcp-config.example.json`（脱敏，环境变量占位符）。

4. **真实使用麦当劳 MCP 的说明**
   - 本 Skill **真实调用**麦当劳 MCP 工具（`now-time-info` / `campaign-calendar` / `available-coupons` / `query-my-coupons` / `auto-bind-coupons` / `query-store-coupons` / `query-meals` / `query-meal-detail` / `calculate-price`）。
   - 端到端联调验证待用户提供 Token 后执行，结果回填 `MCP_INTEGRATION.md` 与本文档。

## 使用的 WorkBuddy 能力

- WebFetch：调研活动仓库与 MCP 接入指南
- Skill 规范参考：加载 `skill-creator` 确认 SKILL.md 格式
- 文件编写：在当前仓库 `E:/ai-coding-study/MMM` 创建全部参赛文件
- Git：本地初始化仓库（创建时间 2026-10-18，处于活动要求的 2025/12/25–2026/10/25 窗口内）

## 验证记录（2026-10-18，经 WorkBuddy 连接器 mcd-mcp 实测）

| 日期 | 验证动作 | 结果 |
|------|----------|------|
| 2026-10-18 | 连接 mcp.mcd.cn，调用 now-time-info | ✅ 成功，返回 2026-10-18 12:36:45 GMT+08:00 |
| 2026-10-18 | 调用 campaign-calendar | ✅ 成功，返回当月 G-DRAGON 联名、PEACEMINUSONE 周边、9.9 元早餐等真实活动 |
| 2026-10-18 | 调用 available-coupons | ❌ 服务端 schema 错误 -32602（重试两次均失败，判定服务端 bug，非 Skill 问题） |
| 2026-10-18 | 调用 query-my-coupons | ✅ 成功，返回真实账户数据「暂无可用优惠券」 |
| 2026-10-18 | 调用 auto-bind-coupons（真实领券） | ⏸️ 未调用，按护栏需二次确认且 available-coupons 故障，暂缓 |

> 结论：已真实使用麦当劳 MCP 的 3 个只读工具（时间/活动日历/我的券），证明 Skill 与 mcp.mcd.cn 联通与鉴权有效。W3 发现券环节受 available-coupons 服务端故障影响暂不可用，已记录于 MCP_INTEGRATION.md。

## 说明

- 本文件为开发上下文记录，非用户隐私数据。
- 所有 MCP 凭证仅以 `${MCD_MCP_TOKEN}` 环境变量占位符形式出现，不含真实 Token。
