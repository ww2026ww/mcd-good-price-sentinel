#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opportunity.py —— 个人省钱机会评分 + 四条件门禁 + 必带成因 + 默认静默
（本项目差异化的核心资产之三，也是与「普通省钱助手 / 定时外壳」的根本分野）

别人回答：「现在怎么点最便宜？」—— 每次求全局最优就推一份。
麦麦好价哨回答：「今天，是否出现了一个**对你而言**真正值得采取行动的省钱机会？」
  —— 没有，就闭嘴（结构化静默日志，不刷屏）。

判定流程
--------
1. 用个人基准(你平时花多少) 与 今日最优方案(optimizer 算得) 求「个人差额」personal_gap。
2. 用 drift 找出「今天相对你常态的变动」作为成因候选。
3. 四条件**全部满足**才推送（否则静默）：
   ① 个人差额 >= 个性化阈值
   ② 方案匹配偏好（含你常点项 / 方式一致 / 热量在限内）
   ③ 近 N 天未重复推送同一机会
   ④ 存在具体成因（新券 / 常点项降价 / 限时活动）—— 没有成因的便宜价 = 常态，不打扰
4. 每条推送**必带成因与理由**；静默则返回结构化 silence_reason（供日志/复盘）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from _imports import load_optimizer
from mcp_adapter import build_request
from baseline import is_recent_duplicate, alert_signature
from drift import diff_snapshots

mcd_optim = load_optimizer()


def _plan_item_keys(plan: Dict[str, Any]) -> List[str]:
    return [it.get("code") or it.get("name") for it in plan.get("items", [])]


def _match_favorites(plan: Dict[str, Any], favorites: Sequence[str]) -> List[str]:
    keys = set(_plan_item_keys(plan))
    names = {it.get("name") for it in plan.get("items", [])}
    matched = [f for f in favorites if f in keys or f in names]
    return matched


def _pick_cause(drifts: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从个人相关漂移中选最具说服力的成因：常点项降价 > 适用新券 > 限时活动。"""
    personal = [d for d in drifts if d.get("personal")]
    order = {"price_drop": 0, "new_coupon": 1, "limited_campaign_active": 2}
    personal.sort(key=lambda d: order.get(d.get("kind"), 9))
    for d in personal:
        if d.get("kind") in order:
            return d
    return None


def assess(
    snapshot: Dict[str, Any],
    state: Dict[str, Any],
    today: str = "",
) -> Dict[str, Any]:
    """核心判定：返回 decision（notify / silent）。"""
    menu = snapshot["menu"]
    coupons = snapshot.get("coupons", {"coupons": []})
    prefs = state.get("preferences", {})
    favorites = list(prefs.get("favorite_items", []) or [])
    baseline = state.get("baseline") or {}

    decision: Dict[str, Any] = {
        "action": "silent",
        "date": today,
        "store_code": snapshot.get("store_code", ""),
        "currency": snapshot.get("currency", "CNY"),
        "personal_threshold": baseline.get("personal_threshold", 0.0),
        "baseline_total": baseline.get("typical_total", 0.0),
        "best_total": None,
        "personal_gap": None,
        "best_plan": None,
        "cause": None,
        "gates": {},
        "reason": "",
        "silence_reason": "",
        "drifts": [],
    }

    # 冷启动：尚未建立基准
    if not baseline or baseline.get("typical_total", 0) <= 0:
        decision["silence_reason"] = "cold_start_no_baseline"
        return decision

    # 漂移检测（成因来源）
    drifts = diff_snapshots(state.get("last_snapshot"), snapshot, favorites, today)
    decision["drifts"] = drifts
    cause = _pick_cause(drifts)

    # 求今日最优方案
    req = build_request(prefs)
    res = mcd_optim.optimize(menu, coupons, req)
    plans = res.get("plans", [])
    if not plans:
        decision["silence_reason"] = "no_plan"
        return decision
    best = plans[0]
    best_total = float(best.get("total", 0.0))
    baseline_total = float(baseline.get("typical_total", 0.0))
    personal_gap = round(baseline_total - best_total, 2)
    decision["best_plan"] = best
    decision["best_total"] = best_total
    decision["personal_gap"] = personal_gap

    threshold = float(baseline.get("personal_threshold", 0.0))
    matched = _match_favorites(best, favorites)
    dining_ok = (best.get("dining_mode") or snapshot.get("dining_mode") or "pickup") == req.get("dining_mode", "pickup")
    cal_ok = True
    if prefs.get("max_calories") is not None:
        cal_ok = int(best.get("calories", 0)) <= int(prefs["max_calories"])

    opp_for_dedupe = {"plan": best, "cause": cause["kind"] if cause else ""}
    not_dup = not is_recent_duplicate(state, opp_for_dedupe)

    gates = {
        "gap_ok": personal_gap >= threshold and personal_gap > 0,
        "preference_match": bool(matched) and dining_ok and cal_ok,
        "not_duplicate": not_dup,
        "has_cause": cause is not None,
        "matched_favorites": matched,
        "dining_ok": dining_ok,
        "cal_ok": cal_ok,
        "threshold": threshold,
    }
    decision["gates"] = gates
    decision["cause"] = cause

    # 四条件门禁
    if not gates["gap_ok"]:
        decision["silence_reason"] = "gap_below_threshold"
    elif not gates["preference_match"]:
        decision["silence_reason"] = "preference_mismatch"
    elif not gates["not_duplicate"]:
        decision["silence_reason"] = "recent_duplicate"
    elif not gates["has_cause"]:
        decision["silence_reason"] = "no_personal_drift"
    else:
        decision["action"] = "notify"
        decision["reason"] = _build_reason(decision, matched, cause)

    return decision


def _build_reason(decision: Dict[str, Any], matched: Sequence[str], cause: Optional[Dict[str, Any]]) -> str:
    best = decision["best_plan"]
    items = "、".join(it.get("name", it.get("code", "")) for it in best.get("items", []))
    cur = decision["currency"]
    sym = "¥" if cur == "CNY" else cur + " "
    gap = decision["personal_gap"]
    base = decision["baseline_total"]
    threshold = decision["personal_threshold"]
    cause_text = cause["note"] if cause else "出现更优组合"
    # 成因补充：命中了常点项
    fav_part = f"，且包含你常点的「{('、'.join(matched))}」" if matched else ""
    return (
        f"你常点的组合平时约 {sym}{base:.2f}，今天用券叠算后到手 {sym}{decision['best_total']:.2f}"
        f"{fav_part}，省 {sym}{gap:.2f}（达到你的 {sym}{threshold:.2f} 提醒阈值）。"
        f"成因：{cause_text}。建议今天下单。"
    )


def opportunity_for_state(decision: Dict[str, Any]) -> Dict[str, Any]:
    """把 decision 中的 best_plan 抽出，用于反馈去重指纹。"""
    return {"plan": decision.get("best_plan") or {}, "cause": (decision.get("cause") or {}).get("kind", "")}


if __name__ == "__main__":
    import os
    sd = os.path.join(os.path.dirname(__file__), "..", "vendor", "mcd-saver-strategist", "sample_data")
    menu = json.load(open(os.path.join(sd, "menu.json"), encoding="utf-8"))
    coupons = json.load(open(os.path.join(sd, "coupons.json"), encoding="utf-8"))
    snap = {"store_code": "SZ0001", "currency": "CNY", "dining_mode": "pickup",
            "menu": menu, "coupons": coupons, "campaigns": []}
    prefs = {"favorite_items": ["巨无霸", "薯条(中)", "可乐(中)"], "dining_mode": "pickup", "typical_budget": 40}
    from baseline import compute_baseline, default_state
    st = default_state(prefs)
    st["baseline"] = compute_baseline(menu, prefs)
    # 人为制造一个「个人相关成因」：把一张券标记为新增
    st["last_snapshot"] = {"menu": menu, "coupons": {"coupons": []}, "campaigns": []}
    st["last_snapshot"]["menu"] = menu
    st["last_snapshot"]["coupons"] = {"coupons": []}
    d = assess(snap, st, today="2026-10-18")
    print(json.dumps(d, ensure_ascii=False, indent=2))
