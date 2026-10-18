#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_sentinel.py —— 麦麦好价哨每日编排（四步循环）

把「每天主动替用户寻找值得吃的一餐」拆成四步，并落到可复现、可测试的脚本上：

  步骤1  刷新真实数据   从 MCP 连接器取回 menu/coupons/campaigns（经 mcp_adapter 归一化），
                        组装成 snapshot；或直接从离线 snapshot.json 读取（演示/测试）。
  步骤2  生成候选组合   用个人偏好构造 request，调用 vendored 优化器求今日最优方案。
  步骤3  真实价格核验   以优化器算得的到手价为基线；若提供了 MCP calculate-price 的复核结果，
                        做一致性校验（不一致则标记 discrepancy，避免假低价）。离线模式跳过。
  步骤4  判断是否打扰   运行机会门禁（个人差额 / 偏好匹配 / 去重 / 成因），决定 notify 或 silent。

产物：
  - 决策 decision.json（notify/silent + 成因 + 理由 + 漂移）
  - 更新 state.json（基线、去重窗口、昨日快照、反馈）
  - 控制台摘要（供 Agent 转述给用户）

真实环境中，本脚本由 WorkBuddy 定时任务调用：Agent 先通过 mcd-mcp 连接器拉取实时数据，
写入 snapshot.json，再运行本脚本；推送动作（企微/小程序/对话）由 Agent 按 decision.action 执行。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from _imports import load_optimizer
from mcp_adapter import build_snapshot, load_snapshot as load_snap_file
from baseline import (
    load_state, save_state, default_state, compute_baseline,
    establish_baseline_if_needed, register_alert,
)
from opportunity import assess, opportunity_for_state

mcd_optim = load_optimizer()


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _load_preferences(args, state: Dict[str, Any]) -> Dict[str, Any]:
    if args.preferences and os.path.exists(args.preferences):
        with open(args.preferences, "r", encoding="utf-8") as f:
            p = json.load(f)
        state["preferences"].update(p)
    return state["preferences"]


def _build_snapshot(args) -> Dict[str, Any]:
    if args.snapshot:
        return load_snap_file(args.snapshot)
    if args.build_from_mcp:
        with open(args.build_from_mcp, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # raw 形态：{"menu":..., "coupon_sources":[...], "campaigns":..., "store_code":..., "dining_mode":...}
        return build_snapshot(
            raw.get("menu"), raw.get("coupon_sources", []), raw.get("campaigns", []),
            {}, raw.get("store_code", ""), raw.get("dining_mode", "pickup"),
        )
    if args.demo:
        sd = os.path.join(os.path.dirname(__file__), "..", "vendor", "mcd-saver-strategist", "sample_data")
        menu = json.load(open(os.path.join(sd, "menu.json"), encoding="utf-8"))
        coupons = json.load(open(os.path.join(sd, "coupons.json"), encoding="utf-8"))
        return build_snapshot(menu, [coupons], [], {}, store_code="SZ0001")
    raise SystemExit("必须提供 --snapshot / --build-from-mcp / --demo 之一")


def _reconcile_price(decision: Dict[str, Any], price_check: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """步骤3：若提供 MCP calculate-price 复核结果，与优化器到手价比对。"""
    if not price_check or decision.get("action") != "notify":
        return decision
    best = decision.get("best_plan") or {}
    sig = tuple(sorted(it.get("code", it.get("name", "")) for it in best.get("items", [])))
    auth = price_check.get("plans", {}).get(json.dumps(list(sig), ensure_ascii=False))
    if auth is not None:
        diff = round(float(auth) - float(best.get("total", 0.0)), 2)
        decision["price_check"] = {"authoritative": auth, "optimizer": best.get("total"), "diff": diff}
        if abs(diff) > 0.5:
            decision["price_check"]["discrepancy"] = True
    return decision


def run(args) -> Dict[str, Any]:
    today = args.today or _now_iso()[:10]
    state = load_state(args.state) if (args.state and os.path.exists(args.state)) else default_state()
    prefs = _load_preferences(args, state)
    snapshot = _build_snapshot(args)

    # 步骤1+2：建立/更新个人基准 + 求今日最优（在 assess 内完成）
    order_history = None
    if args.order_history and os.path.exists(args.order_history):
        with open(args.order_history, "r", encoding="utf-8") as f:
            order_history = json.load(f)
    establish_baseline_if_needed(state, snapshot["menu"], order_history)

    # 步骤4：机会门禁
    decision = assess(snapshot, state, today=today)

    # 步骤3：价格复核（可选）
    if args.price_check and os.path.exists(args.price_check):
        with open(args.price_check, "r", encoding="utf-8") as f:
            decision = _reconcile_price(decision, json.load(f))
    else:
        decision = _reconcile_price(decision, None)

    # 持久化
    state["baseline"] = state["baseline"]
    state["last_snapshot"] = {
        "menu": snapshot["menu"],
        "coupons": snapshot["coupons"],
        "campaigns": snapshot.get("campaigns", []),
    }
    state["last_run"] = _now_iso()
    if decision["action"] == "notify":
        state = register_alert(state, opportunity_for_state(decision), today)
    if args.save_state and args.state:
        save_state(state, args.state)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(decision, f, ensure_ascii=False, indent=2)
    return decision


def _print_summary(d: Dict[str, Any]) -> None:
    if d["action"] == "notify":
        print("🔔 通知（值得行动）：")
        print("  ", d["reason"])
    else:
        print("🔕 静默（不打扰）：", d.get("silence_reason"))
        dr = d.get("drifts") or []
        if dr:
            print("   今日变动：", "；".join(x["note"] for x in dr))
        else:
            print("   今日无个人相关变动")
    if d.get("personal_gap") is not None:
        print(f"   个人差额={d['personal_gap']} 阈值={d['personal_threshold']} 到手价={d['best_total']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="麦麦好价哨每日编排")
    ap.add_argument("--snapshot", help="已归一化的 snapshot.json（离线/演示）")
    ap.add_argument("--build-from-mcp", help="原始 MCP 聚合 JSON，经 mcp_adapter 归一化")
    ap.add_argument("--demo", action="store_true", help="用 vendored 示例数据跑通流程")
    ap.add_argument("--state", default="state.json", help="状态文件路径")
    ap.add_argument("--preferences", help="用户偏好 JSON（favorite_items 等）")
    ap.add_argument("--order-history", help="历史订单 JSON，用于构建真实消费画像")
    ap.add_argument("--price-check", help="MCP calculate-price 复核结果 JSON（可选）")
    ap.add_argument("--out", default="decision.json", help="决策输出 JSON 路径")
    ap.add_argument("--today", help="指定日期 YYYY-MM-DD（覆盖当天）")
    ap.add_argument("--no-save-state", dest="save_state", action="store_false", help="不写回 state")
    args = ap.parse_args()
    d = run(args)
    _print_summary(d)


if __name__ == "__main__":
    main()
