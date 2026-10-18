# 第三方依赖说明 (Third-Party Notices)

本项目以 **MIT License** 复用了上游开源项目的确定性优化器作为「算」的引擎，
并在此之上构建了差异化的「个人省钱机会哨兵」能力。以下为合规标注。

---

## 1. 复用的上游项目

| 项目 | 许可 | 用途 | 引入方式 |
|------|------|------|----------|
| [hujjun/mcd-saver-strategist](https://github.com/hujjun/mcd-saver-strategist) | **MIT** | 确定性优惠组合优化引擎（最低价组合 / 多目标 / 凑单 / 拼单 / 券体检 / 帕累托前沿），作为权威计价「神谕」 | vendored（原样引入，未改算法） |

### 引入的文件（位于 `vendor/mcd-saver-strategist/`）
- `optimizer.py` —— 优化引擎核心（零依赖，纯标准库）
- `selftest.py` —— 上游自测（46 项，本仓库验证 46/46 通过）
- `coupon-strategy.md` —— 5 类券建模与省钱策略说明
- `LICENSE` —— 上游 MIT 许可原文（一字未改）
- `sample_data/` —— 上游示例数据（仅用于本地测试，不进入 Skill 运行路径）

### 上游版权声明（原文保留）
> Copyright (c) 2026 mcd-saver-strategist contributors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy …
> （完整条款见 `vendor/mcd-saver-strategist/LICENSE`）

---

## 2. 本项目新增 / 修改的部分

我们**未修改**上游优化器的算法代码，仅原样引入。以下为本项目**独立编写**的核心资产：

| 文件 | 说明 |
|------|------|
| `scripts/mcp_adapter.py` | 把麦当劳 MCP 工具返回归一化为优化器契约（menu / coupons / request），解耦 MCP 与算法 |
| `scripts/baseline.py` | **个人基准**：基于用户常点项 + 可选订单历史建立「典型一餐」参照系，并含反馈闭环自适应阈值 |
| `scripts/drift.py` | **漂移检测**：比对昨日/今日快照，识别对「该用户」有意义的变动（常点项降价 / 新券 / 限时活动） |
| `scripts/opportunity.py` | **机会门禁**：个人差额评分 + 四条件门禁（差额≥阈值 / 偏好匹配 / 近期去重 / 有具体成因）+ 必带成因 + 默认静默 |
| `scripts/daily_sentinel.py` | **每日编排**：四步循环（刷新数据 → 生成候选 → 价格核验 → 判断是否打扰）的可复现 CLI |
| `scripts/test_sentinel.py` | 差异化层自测（26 项） |

### 差异化定位（为何不是「上游优化器的定时外壳」）
上游项目回答：**「现在怎么点最便宜？」**（每次独立求全局最优）。
麦麦好价哨回答：**「今天，是否出现了一个对你而言真正值得采取行动的省钱机会？」** —— 没有就闭默。

关键差异不在算法本身，而在算法的**使用方式**：
1. **复用**成熟的确定性优化器（避免重复造轮子，遵守 MIT）；
2. 建立**用户个人基准**（含历史反馈），以「你平时花多少」而非「菜单最低价」为比对锚；
3. 只推送**有意义**的机会——必须带具体成因（新券 / 降价 / 限时活动），否则保持静默。

---

## 3. 许可合规
- 上游 MIT 许可原文完整保留于 `vendor/mcd-saver-strategist/LICENSE`。
- 本仓库自有代码以 MIT 许可发布（见 `LICENSE.txt`）。
- 如需移除 vendored 依赖，删除 `vendor/mcd-saver-strategist/` 即可，本项目差异化层不依赖其运行之外的任何私有接口。
