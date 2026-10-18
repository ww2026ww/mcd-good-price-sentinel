# 麦麦好价哨 · 每日自动化规范 (SCHEDULE)

本文件定义「每日主动哨兵」如何在 WorkBuddy 定时任务中运行。目标：用户**无需每天来问**，
由定时任务每天自动跑四步循环，只在「对该用户真正出现值得行动的机会」时打扰。

---

## 1. 触发方式

WorkBuddy 支持定时执行任务、选择 Skill 与连接器、并把结果推送到对话 / 企业微信 / 小程序。
建议配置一条**每日循环**自动化：

- **频率**：每天 1 次（建议 09:30–10:30，覆盖午/晚市决策窗口）
- **绑定**：Skill `maimai-haojiasao` + 连接器 `mcd-mcp`
- **推送目标**：用户对话；若已配置企业微信/小程序，可一并推送 notify 结果

### 自动化 Prompt 模板（直接可粘贴）

```
执行「麦麦好价哨」每日哨兵循环：
1. 通过 mcd-mcp 连接器拉取今日真实数据：now-time-info、campaign-calendar、
   query-my-coupons（available-coupons 若可用也一并拉取），并按需
   query-nearby-stores → query-meals 取得在售菜单。
2. 用 scripts/mcp_adapter 把上述结果归一化为 snapshot.json（见 SKILL.md / README）。
3. 运行：python scripts/daily_sentinel.py --snapshot snapshot.json \
      --state state.json --preferences preferences.json --out decision.json
4. 读取 decision.json：
   - 若 action=="notify"：把 decision.reason 与最优组合明细推送给用户（企微/小程序/对话），
     结尾附「以上以麦当劳官方实时数据为准，仅供参考」。
   - 若 action=="silent"：**不主动打扰**，仅在用户问起时简短说明今日无针对其的新机会。
5. 不调用 create-order / cancel-order；auto-bind-coupons 仅在用户确认后调用。
```

> 说明：企微/小程序推送在本环境未实测（连接器为 OFF），故此处以「规范」形式给出，
> 待用户接入对应连接器后即可按上述 Prompt 直接运行。

---

## 2. 四步循环（与代码对应）

| 步骤 | 动作 | 对应脚本 | 产物 |
|------|------|----------|------|
| 1 刷新数据 | MCP 拉取 menu/coupons/campaigns → 归一化 | `mcp_adapter.py` | `snapshot.json` |
| 2 生成候选 | 个人偏好 → request → 优化器求最优 | `vendor/.../optimizer.py` | 今日最优方案 |
| 3 价格核验 | 以优化器到手价为基线；可选 calculate-price 复核 | `daily_sentinel.py` | 一致性标记 |
| 4 判断打扰 | 个人基准 + 漂移 + 四条件门禁 | `baseline.py`/`drift.py`/`opportunity.py` | `decision.json` (notify/silent) |

---

## 3. 状态与持久化

- `state.json`：个人基准、反馈样本、去重窗口、昨日快照、最后运行时间。
  - 每日循环**写回** `state.json`（更新昨日快照 + 去重指纹 + 自适应阈值）。
  - 用户采纳/忽略推送后，调用 `baseline.record_feedback` 更新阈值。
- `preferences.json`：用户常点项与阈值（首次由 W1 收集），变更时重建基准。
- `snapshot.json` / `decision.json`：每日临时产物，可留存供复盘，不入版本库。

---

## 4. 门禁四条件（全满足才推送）

1. **个人差额 ≥ 个性化阈值**：`个人基准花费 − 今日最优到手价 ≥ threshold`
2. **匹配偏好**：最优方案含用户常点项、方式一致、热量在限内
3. **近期去重**：同一机会近 7 天未推送过（按内容指纹）
4. **有具体成因**：出现新券 / 常点项降价 / 限时活动——**没有成因的便宜价＝常态，静默**

任一不满足即 `silent`，返回结构化 `silence_reason`（cold_start / no_personal_drift /
gap_below_threshold / preference_mismatch / recent_duplicate），供日志与复盘。

---

## 5. 静默纪律

- 无事可报时**绝不刷屏**。绝大多数日子应保持静默——这是本 Skill 区别于「每日最低价推送」的核心。
- 静默不等于失效：它说明「今天对你没有比平时更划算的新机会」，恰是用户想要的「省心」。

---

## 6. 反馈闭环

- 用户忽略小额提醒较多 → `adapt_threshold` 上调阈值（更克制）。
- 用户高采纳 → 阈值适度下调（更灵敏），但不低于 ¥5 下限、不高于 ¥20 上限。
- 闭环使哨兵越用越「懂」该用户的打扰边界。
