# WorkBuddy 开发对话上下文导出 (workbuddy.md)

> 本文件为使用 WorkBuddy 开发「麦麦好价哨」Skill 的对话上下文导出，用于核验是否符合「麦当劳程序员创意开发大赛」WorkBuddy 专项奖励条件。

## 项目信息

- **项目名称**：麦麦好价哨 (MaiMai Good-Price Sentinel)
- **项目类型**：WorkBuddy Skill（基于麦当劳中国 MCP 的「主动省钱机会哨兵」）
- **开发工具**：WorkBuddy（智能体环境，含代码执行、文件读写、Web 调研）
- **开发时间**：2026-10-18

## 开发过程概述（含产品迭代）

1. **需求与活动调研**
   - 通过 WebFetch 研读活动主仓库 `M-China-Official/mcd-developer-innovation-challenge`：活动为「麦当劳程序员创意开发大赛」，报名时间 2026/10/9–10/25，排行榜以 2026/10/26 00:00 的 GitHub Star 快照定榜；WorkBuddy 为官方合作伙伴，提交 `workbuddy.md` 可获专项积分。
   - 研读麦当劳 MCP Server 接入指南，确认 `https://mcp.mcd.cn`、Streamable HTTP、Bearer Token、600 次/分钟限流。

2. **V1 初版（被动哨兵）**
   - 方向：麦麦好价哨——差异化于普通省钱助手的「哨兵/盯盘」模式。
   - 交付：仅 WorkBuddy Skill。编写 SKILL.md（9 个 MCP 工具映射 + W1 好价速览 / W2 阈值预警 / W3 智能领券 / W4 活动蹲守）+ 全部必交文件。提交 `6c4d5ce`。

3. **真实联调（经 mcd-mcp 连接器）**
   - 用户建好 `mcd-mcp` 连接器后实测：`now-time-info`、`campaign-calendar`、`query-my-coupons` 成功；`available-coupons` 报服务端 schema bug `-32602`（重试两次均失败，判定服务端故障，已降级用 `query-my-coupons`）。提交 `87432fc`。

4. **V2 产品重构（每日主动）**
   - 用户提出从「用户想省钱时才用」改造为「每天主动替用户寻找值得吃的一餐」：四步循环（刷新真实数据 → 按偏好生成候选 → `calculate-price` 验真排序 → 判断值不值得打扰）。

5. **V3 差异化重构（当前目标）— 个人省钱机会哨兵**
   - 竞品扫描发现 `mcd-savings-radar`（已报名）也做优惠聚合 + 个性化策略。用户明确：若只做「优化器 + 定时器 + 推送」= 无竞争优势。
   - 与用户确认架构：**复用**成熟确定性优化器 + **自研**个人基准 / 机会门禁 / 漂移检测。
   - 算法取舍：用户要求尽量复用已有 MIT 许可优化算法，避免重复造轮子。确认复用头部项目 `hujjun/mcd-saver-strategist`（MIT）优化器，原样 vendored 于 `vendor/`，保留其版权与许可原文，未改算法。
   - 核心思路三支柱：① 复用优化器；② 建立用户个人基准（含历史反馈）；③ 只推送有意义的机会（带成因，否则静默）。

## 使用的 WorkBuddy 能力

- WebFetch：调研活动仓库、MCP 接入指南、上游优化器源码（逐文件取回）
- WebSearch / 竞品扫描：确认同类项目现状，定位差异化
- 代码执行（Bash + Python）：拉取上游优化器（GitHub API base64）、运行 vendored 自测、编写并运行差异化层测试
- 文件编写：在当前仓库 `E:/ai-coding-study/MMM` 创建全部参赛文件与 `scripts/`、`vendor/`
- Git：本地仓库（创建于 2026-10-18，处于活动要求的 2025/12/25–2026/10/25 窗口内）

## 最终架构（V3）

```
麦当劳 MCP (mcd-mcp)
  → mcp_adapter.py（归一化为优化器契约）
  → optimizer.py（vendored, MIT：确定性最优组合 + 到手价）
  → baseline.py / drift.py / opportunity.py（本 Skill 自研：个人基准·漂移·机会门禁·静默）
  → daily_sentinel.py（四步编排 CLI）
  → decision.json（notify 带成因 / silent 结构化日志）
```

- **复用**：`vendor/mcd-saver-strategist/optimizer.py`（MIT，原样引入，未改算法）
- **自研**：`scripts/mcp_adapter.py`、`baseline.py`、`drift.py`、`opportunity.py`、`daily_sentinel.py`、`test_sentinel.py`
- **门禁四条件**（全满足才推送）：个人差额 ≥ 个性化阈值；匹配偏好；近 7 天未重复；有具体成因（新券/降价/限时活动）。默认静默。

## 验证记录（2026-10-18）

| 类别 | 验证动作 | 结果 |
|------|----------|------|
| MCP 联通 | now-time-info / campaign-calendar / query-my-coupons | ✅ 真实返回（时间/活动/账户数据） |
| MCP 异常 | available-coupons | ❌ 服务端 `-32602` schema bug（已降级 + 文档化） |
| 算法复用 | vendored `selftest.py` | ✅ 46/46 passed |
| 差异化层 | `scripts/test_sentinel.py`（基准/漂移/门禁/编排） | ✅ 26/26 passed |
| 端到端 | `daily_sentinel.py --demo` | ✅ 产出 notify 决策并带成因与理由 |

> 结论：已真实使用麦当劳 MCP 的只读工具，证明与 `mcp.mcd.cn` 联通与鉴权有效，满足活动「真实使用麦当劳 MCP 能力」要求；算法与差异化逻辑均通过自动化测试。

## 合规与说明

- `CONTEST_DECLARATION.md`：官方原文一字未改。
- 第三方优化器以 MIT 许可复用，版权与许可原文保留于 `vendor/mcd-saver-strategist/LICENSE`；复用标注见 `THIRD_PARTY.md` 与 `LICENSE.txt`。
- 所有 MCP 凭证仅以 `${MCD_MCP_TOKEN}` 环境变量占位符出现，不含真实 Token。
- 本文件为开发上下文记录，非用户隐私数据。
