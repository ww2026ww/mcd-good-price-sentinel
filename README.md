# 麦麦好价哨 (MaiMai Good-Price Sentinel)

> 基于麦当劳中国 MCP 的「好价哨兵」Skill —— 你设个价格阈值，它替你盯盘。

麦麦好价哨是一个运行在 [WorkBuddy](https://www.workbuddy.cn) 中的 Skill，对接**麦当劳中国 MCP Server**，帮你在麦当劳动态定价 + 海量券池中，用最低认知成本抓住最优价格点。它不只是"算一次最便宜"，而是**哨兵模式**：你设好关注目标，它持续盯价、盯券、盯活动，达标就告警。

> 本仓库为「麦当劳程序员创意开发大赛」参赛作品。项目输出仅供参考，非麦当劳官方产品。

---

## 功能特性

| 能力 | 说明 | 对应 MCP 工具 |
|------|------|---------------|
| **好价速览** | 一句话看清当前最值得买的组合与到手价 | `campaign-calendar` / `available-coupons` / `query-meals` / `calculate-price` |
| **阈值预警** | 设「XX ≤ ¥X 提醒我」，达标主动告警 | `query-meal-detail` / `calculate-price` |
| **智能领券** | 自动发现高价值券、查重、二次确认后一键领取 | `available-coupons` / `query-my-coupons` / `auto-bind-coupons` |
| **活动蹲守** | 当月营销活动力度解读与提醒 | `campaign-calendar` / `now-time-info` |

---

## 快速开始

### 1. 获取麦当劳 MCP Token

前往 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) 用手机号登录，在「控制台」激活并复制 MCP Token。

### 2. 在 WorkBuddy 接入 mcd-mcp

左侧边栏「专家·技能·连接器」→「连接器」→ 右上角「自定义连接器」→「配置 MCP」，填入：

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

> 将 `${MCD_MCP_TOKEN}` 替换为你的真实 Token，或在环境变量中配置后引用。

### 3. 安装本 Skill

将本仓库的 `SKILL.md` 复制到 WorkBuddy 用户技能目录：

```bash
# Windows (PowerShell)
Copy-Item SKILL.md "$env:USERPROFILE\.workbuddy\skills\maimai-haojiasao\SKILL.md" -Force

# macOS / Linux
mkdir -p ~/.workbuddy/skills/maimai-haojiasao
cp SKILL.md ~/.workbuddy/skills/maimai-haojiasao/SKILL.md
```

重启/刷新 WorkBuddy 后，在对话中直接说需求即可触发。

### 4. 开始对话

```
用户：帮我盯着麦辣鸡腿堡，降到 12 块以下就提醒我
用户：现在有什么好价好券？
用户：把能领的券都帮我领了
用户：这个月麦当劳有什么活动值得蹲？
```

---

## 使用示例

**场景：阈值预警**
```
用户：麦辣鸡腿堡 ≤ ¥12 就告诉我
好价哨：已记录关注目标「麦辣鸡腿堡 ≤ ¥12」（阈值保存在本次对话，跨会话需重设）。
        …（后续查询）…
⚠️ 好价预警：麦辣鸡腿堡当前到手 ¥11.5（已叠 ¥2 券），已低于你设定的 ¥12 阈值，建议下单。
        以上以麦当劳官方实时数据为准，仅供参考。
```

**场景：智能领券**
```
用户：能领的券都领了吧
好价哨：发现 3 张高价值券（已排除你已持有的 2 张）：
        1. 麦辣鸡腿堡 ¥2 券（12/31 到期）
        2. 满 30 减 8 券
        3. 任意中杯饮料 ¥3 券
        确认领取这 3 张券？（auto-bind-coupons 将一次性领取）
用户：确认
好价哨：领取结果：成功 3/3。
```

---

## 目标用户

- 高频吃麦当劳、想用最低价拿到心仪餐品的精打细算用户
- 关注优惠活动、但不想每天手动翻券的用户
- 想用 AI + MCP 体验"价格监控自动化"的开发者/参赛者

---

## 项目结构

```
.
├── SKILL.md                  # 主体源码：角色、触发、工具映射、工作流、护栏
├── README.md                 # 本文件
├── CONTEST_DECLARATION.md    # 参赛声明（官方原文，一字不改）
├── MCP_INTEGRATION.md        # MCP Server / Tool / 调用流程 / 业务价值说明
├── mcp-config.example.json   # 脱敏 MCP 配置示例（环境变量占位符）
└── workbuddy.md              # WorkBuddy 开发对话上下文导出（专项奖励核验）
```

---

## 技术说明

- 对接 Server：`https://mcp.mcd.cn`（Streamable HTTP）
- 协议认证：`Authorization: Bearer <TOKEN>`
- 限流：600 次/分钟，超限返回 429
- 真实使用 9 个麦当劳 MCP 工具，详见 `MCP_INTEGRATION.md`

---

## 免责声明

本 Skill 为麦当劳程序员节创意开发大赛参赛作品，由参赛者独立开发，**非麦当劳官方产品**。项目输出仅供参考，不构成医疗、营养或其他专业建议；餐品信息、价格及供应状态**以麦当劳官方渠道的实时结果为准**。
