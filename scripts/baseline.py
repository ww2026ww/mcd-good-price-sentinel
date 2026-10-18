#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
baseline.py —— 用户的「个人基准」与反馈闭环（本项目差异化的核心资产之一）

为什么需要个人基准？
--------------------
竞品大多回答：「现在怎么点最便宜？」（每次独立求全局最优）
麦麦好价哨回答：「今天是否出现了一个**对你而言**值得采取行动的省钱机会？」
要回答后者，必须有一个「你平时大概花多少钱、常吃哪些」的参照系 —— 这就是个人基准。

基准由两部分构成：
  (1) 陈述偏好：用户常点的餐品(favorite_items)、常用方式、预算、热量上限
  (2) 历史订单：MCP 的 order-list（若可用）作为真实消费画像
冷启动（两者皆无）时，用偏好里的常点项构建一个「典型一餐」作为基线，并保守地保持静默。

反馈闭环：
  每次推送后记录用户是否采纳；据此自适应地微调「打扰阈值」，避免反复推小便宜。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from _imports import load_optimizer

mcd_optim = load_optimizer()

# 阈值自适应参数
THRESHOLD_FLOOR = 5.0       # 最低打扰阈值（元），低于此绝不打扰
THRESHOLD_PCT = 0.15        # 默认阈值 = 典型花费的 15%
THRESHOLD_CAP = 20.0        # 阈值上限
FEEDBACK_WINDOW = 10        # 参考最近 N 条反馈
RECENT_ALERT_DAYS = 7       # 去重窗口（天）


# --------------------------------------------------------------------------- #
# 基础 IO
# --------------------------------------------------------------------------- #
def default_state(preferences: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "version": 1,
        "preferences": dict(preferences or {}),
        "baseline": None,
        "feedback": [],          # [{date, cause, gap, accepted, plan_sig}]
        "recent_alerts": [],     # [{date, sig, cause}]
        "last_snapshot": None,
        "last_run": None,
    }


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return default_state()
    with open(path, "r", encoding="utf-8") as f:
        st = json.load(f)
    st.setdefault("preferences", {})
    st.setdefault("feedback", [])
    st.setdefault("recent_alerts", [])
    return st


def save_state(state: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 计算个人基准
# --------------------------------------------------------------------------- #
def _match_codes_by_names(menu: Dict[str, Any], names: Sequence[str]) -> List[str]:
    idx = {it["name"]: it["code"] for it in menu["items"]}
    codes: List[str] = []
    for n in names:
        if n in idx:
            codes.append(idx[n])
    return codes


def compute_baseline(
    menu: Dict[str, Any],
    preferences: Dict[str, Any],
    order_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """基于菜单 + 用户偏好（可选订单历史）求出个人基准一餐。"""
    favorites = list(preferences.get("favorite_items", []) or [])

    # 若提供了真实订单历史，取最近高频项作为「常点项」
    computed_from = "preferences"
    if order_history:
        freq: Dict[str, int] = {}
        for order in order_history[-30:]:
            for it in order.get("items", []):
                name = it.get("name") or it.get("itemName")
                if name:
                    freq[name] = freq.get(name, 0) + 1
        top = sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:3]
        if top:
            favorites = [n for n, _ in top]
            computed_from = "history"

    # 用 vendored 优化器的 baseline 作为「按常点项直选、不叠券」的花费锚
    req = {"wants": favorites, "allow_extras": False,
           "dining_mode": str(preferences.get("dining_mode", "pickup"))}
    res = mcd_optim.optimize(menu, {"coupons": []}, req)
    base = res.get("baseline", {"items": [], "total": 0.0, "calories": 0})
    # 若常点项在菜单里一个都匹配不到，退化到「最便宜的 3 件」作为典型一餐，
    # 保证基准非空；此时基准仅为兜底，偏好匹配门禁会据此判定 preference_mismatch。
    if not base.get("items"):
        fb = sorted(menu["items"], key=lambda x: x["price"])[:3]
        base = {
            "items": [it["name"] for it in fb],
            "total": round(sum(it["price"] for it in fb), 2),
            "calories": sum(int(it.get("calories", 0)) for it in fb),
        }
        computed_from = "default_cheapest"
    typical_total = round(float(base.get("total", 0.0)), 2)
    typical_calories = int(base.get("calories", 0))
    basket_codes = _match_codes_by_names(menu, base.get("items", []))

    # 个性化阈值：优先用用户显式设定，否则取 典型花费*15% 与下限的较大值，并封顶
    if preferences.get("alert_threshold") is not None:
        threshold = float(preferences["alert_threshold"])
    else:
        threshold = max(THRESHOLD_FLOOR, round(THRESHOLD_PCT * typical_total, 2))
    threshold = min(max(threshold, THRESHOLD_FLOOR), THRESHOLD_CAP)

    return {
        "items": base.get("items", []),
        "basket_codes": basket_codes,
        "typical_total": typical_total,
        "typical_calories": typical_calories,
        "personal_threshold": round(threshold, 2),
        "computed_from": computed_from,
    }


def establish_baseline_if_needed(
    state: Dict[str, Any],
    menu: Dict[str, Any],
    order_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if state.get("baseline") is None or state["baseline"].get("computed_from") == "default_cheapest":
        state["baseline"] = compute_baseline(menu, state.get("preferences", {}), order_history)
    return state


# --------------------------------------------------------------------------- #
# 反馈闭环
# --------------------------------------------------------------------------- #
def alert_signature(opportunity: Dict[str, Any]) -> str:
    """推送内容的稳定指纹，用于去重（避免重复打扰同一机会）。"""
    plan = opportunity.get("plan", {})
    items = tuple(sorted(it.get("code", it.get("name", "")) for it in plan.get("items", [])))
    cause = opportunity.get("cause", "")
    norm = json.dumps({"items": items, "cause": cause}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def record_feedback(
    state: Dict[str, Any],
    *,
    date: str,
    cause: str,
    gap: float,
    accepted: bool,
    opportunity: Dict[str, Any],
) -> Dict[str, Any]:
    """记录用户对已推送机会的反馈（采纳/忽略），并据此自适应微调阈值。

    注意：去重窗口(recent_alerts)由 register_alert 在「推送发生时」写入，
    这里只更新反馈样本与阈值，避免重复写入。
    """
    state.setdefault("feedback", []).append(
        {"date": date, "cause": cause, "gap": round(float(gap), 2), "accepted": bool(accepted)}
    )
    state["feedback"] = state["feedback"][-50:]
    state = adapt_threshold(state)
    return state


def register_alert(state: Dict[str, Any], opportunity: Dict[str, Any], date: str) -> Dict[str, Any]:
    """推送发生时，把该机会的指纹写入去重窗口（用于近 N 天不重复打扰）。"""
    sig = alert_signature(opportunity)
    state.setdefault("recent_alerts", []).append(
        {"date": date, "sig": sig, "cause": opportunity.get("cause", "")}
    )
    state["recent_alerts"] = state["recent_alerts"][-50:]
    return state


def is_recent_duplicate(state: Dict[str, Any], opportunity: Dict[str, Any], window_days: int = RECENT_ALERT_DAYS) -> bool:
    """同一指纹是否在去重窗口内已被推送过。"""
    sig = alert_signature(opportunity)
    for ra in state.get("recent_alerts", []):
        if ra.get("sig") == sig:
            return True
    return False


def _days_between(d1: str, d2: str) -> Optional[float]:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            from datetime import datetime
            a = datetime.strptime(d1[:19], "%Y-%m-%dT%H:%M:%S") if "T" in d1 else datetime.strptime(d1[:10], "%Y-%m-%d")
            b = datetime.strptime(d2[:19], "%Y-%m-%dT%H:%M:%S") if "T" in d2 else datetime.strptime(d2[:10], "%Y-%m-%d")
            return abs((a - b).total_seconds()) / 86400.0
        except Exception:
            continue
    return None


def adapt_threshold(state: Dict[str, Any]) -> Dict[str, Any]:
    """根据最近反馈自适应微调打扰阈值：

    - 若用户频繁**忽略**小额机会(gap 低于阈值但被推过)，把阈值上调 1 元（更克制）。
    - 若用户**采纳**率很高，保持甚至下调 0.5 元（更灵敏），但不超过上限/不低于下限。
    """
    fb = state.get("feedback", [])[-FEEDBACK_WINDOW:]
    if len(fb) < 3:
        return state
    accepted = sum(1 for x in fb if x.get("accepted"))
    dismiss_ratio = 1 - accepted / len(fb)
    base = state["baseline"]["personal_threshold"]
    if dismiss_ratio >= 0.7:
        base = min(THRESHOLD_CAP, base + 1.0)
    elif accepted >= len(fb) * 0.8:
        base = max(THRESHOLD_FLOOR, base - 0.5)
    state["baseline"]["personal_threshold"] = round(base, 2)
    return state


if __name__ == "__main__":
    import sys
    sd = os.path.join(os.path.dirname(__file__), "..", "vendor", "mcd-saver-strategist", "sample_data")
    menu = json.load(open(os.path.join(sd, "menu.json"), encoding="utf-8"))
    prefs = {"favorite_items": ["巨无霸", "薯条(中)", "可乐(中)"], "dining_mode": "pickup", "typical_budget": 40}
    b = compute_baseline(menu, prefs)
    print("baseline:", json.dumps(b, ensure_ascii=False, indent=2))
