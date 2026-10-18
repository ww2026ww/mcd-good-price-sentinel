#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
drift.py —— 快照漂移检测（本项目差异化的核心资产之二）

「每天主动寻找值得吃的一餐」最怕变成「每天都推一份最低价套餐」（那只是定时外壳）。
关键在于：**只有当今天相对用户的「常态」出现了某个变化，才值得打扰**。

drift 负责比对 昨日快照 与 今日快照，找出对「该用户」有意义的变动：
  - 常点项价格下跌 / 上涨
  - 出现了新券（尤其是能用于常点项的券）
  - 有券到期消失
  - 限时活动开始 / 进行中

输出按「个人相关性」标注，供 opportunity 引擎用作「具体成因(cause)」——
没有成因的便宜价=常态，应当静默。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

# 限时活动判定：跨度 <= 该天数视为"限时"，它本身的出现即可作为打扰成因
LIMIT_CAMPAIGN_DAYS = 14
PRICE_EPS = 0.01


def _menu_price_map(snapshot: Dict[str, Any]) -> Dict[str, float]:
    return {it["code"]: float(it["price"]) for it in snapshot["menu"]["items"]}


def _menu_item_name(snapshot: Dict[str, Any], code: str) -> str:
    for it in snapshot["menu"]["items"]:
        if it["code"] == code:
            return it["name"]
    return code


def _coupon_map(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["id"]: c for c in snapshot.get("coupons", {}).get("coupons", [])}


def _favorite_codes(snapshot: Dict[str, Any], favorites: Sequence[str]) -> List[str]:
    idx = {it["name"]: it["code"] for it in snapshot["menu"]["items"]}
    out = [idx[n] for n in favorites if n in idx]
    # 也接受 code 直接给出
    out += [c for c in favorites if c in _menu_price_map(snapshot) and c not in out]
    return out


def diff_snapshots(
    prev: Optional[Dict[str, Any]],
    cur: Dict[str, Any],
    favorites: Sequence[str] = (),
    today: str = "",
) -> List[Dict[str, Any]]:
    """比对两日快照，返回漂移事件列表（已标注个人相关性）。"""
    if not prev:
        # 冷启动：无昨日基线，仅从今日是否有「限时活动进行中」判断
        return _cold_start_drifts(cur, favorites, today)

    prev_prices = _menu_price_map(prev)
    cur_prices = _menu_price_map(cur)
    fav = set(_favorite_codes(cur, favorites))
    drifts: List[Dict[str, Any]] = []

    # 1) 价格变动（聚焦常点项）
    for code in fav:
        if code in prev_prices and code in cur_prices:
            d = cur_prices[code] - prev_prices[code]
            if abs(d) <= PRICE_EPS:
                continue
            drifts.append({
                "kind": "price_drop" if d < 0 else "price_up",
                "item_code": code,
                "item_name": _menu_item_name(cur, code),
                "prev_price": round(prev_prices[code], 2),
                "cur_price": round(cur_prices[code], 2),
                "delta": round(d, 2),
                "personal": True,
                "note": f"{_menu_item_name(cur, code)} 现价 ¥{cur_prices[code]:.2f}（{'降' if d<0 else '涨'} ¥{abs(d):.2f}）",
            })

    # 2) 新券 / 失效券
    prev_c = _coupon_map(prev)
    cur_c = _coupon_map(cur)
    for cid, cp in cur_c.items():
        if cid not in prev_c:
            applies_fav = bool(set(cp.get("item_codes", []) or []) & fav) or cp.get("type") in ("threshold", "free_gift", "bundle_price")
            drifts.append({
                "kind": "new_coupon",
                "coupon_id": cid,
                "coupon_title": cp.get("title", cid),
                "coupon_type": cp.get("type"),
                "personal": applies_fav,
                "note": f"出现新券「{cp.get('title', cid)}」（{'与你的常点项相关' if applies_fav else '通用'}）",
            })
    for cid, cp in prev_c.items():
        if cid not in cur_c:
            drifts.append({
                "kind": "expired_coupon",
                "coupon_id": cid,
                "coupon_title": cp.get("title", cid),
                "personal": False,
                "note": f"券「{cp.get('title', cid)}」已失效",
            })

    # 3) 活动：新增且为限时
    prev_camps = {c["id"] for c in prev.get("campaigns", [])}
    for c in cur.get("campaigns", []):
        is_new = c["id"] not in prev_camps
        is_limited = _is_limited(c)
        if is_new and is_limited:
            drifts.append({
                "kind": "limited_campaign_active",
                "campaign_id": c["id"],
                "campaign_title": c.get("title", c["id"]),
                "personal": True,
                "note": f"限时活动「{c.get('title', c['id'])}」进行中",
            })
    return drifts


def _cold_start_drifts(cur: Dict[str, Any], favorites: Sequence[str], today: str) -> List[Dict[str, Any]]:
    drifts: List[Dict[str, Any]] = []
    for c in cur.get("campaigns", []):
        if _is_limited(c):
            drifts.append({
                "kind": "limited_campaign_active",
                "campaign_id": c["id"],
                "campaign_title": c.get("title", c["id"]),
                "personal": True,
                "note": f"限时活动「{c.get('title', c['id'])}」进行中（首次建立基准）",
            })
    return drifts


def _is_limited(campaign: Dict[str, Any]) -> bool:
    s, e = campaign.get("start"), campaign.get("end")
    if not s or not e:
        return False
    try:
        from datetime import datetime
        def _p(x):
            x = str(x).replace("Z", "")
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(x[:19] if "T" in x or " " in x else x[:10], fmt)
                except Exception:
                    continue
            return None
        a, b = _p(s), _p(e)
        if a and b:
            return 0 < (b - a).total_seconds() / 86400.0 <= LIMIT_CAMPAIGN_DAYS
    except Exception:
        return False
    return False


def summarize(drifts: Sequence[Dict[str, Any]]) -> str:
    personal = [d for d in drifts if d.get("personal")]
    if not personal:
        return "无明显与个人相关的变动" if drifts else "无变动"
    return "；".join(d["note"] for d in personal)


if __name__ == "__main__":
    import os
    sd = os.path.join(os.path.dirname(__file__), "..", "vendor", "mcd-saver-strategist", "sample_data")
    menu = json.load(open(os.path.join(sd, "menu.json"), encoding="utf-8"))
    coupons = json.load(open(os.path.join(sd, "coupons.json"), encoding="utf-8"))
    prev = {"menu": menu, "coupons": {"coupons": []}, "campaigns": []}
    cur = {"menu": menu, "coupons": coupons, "campaigns": [{"id": "C1", "title": "9.9 早餐", "start": "2026-10-01", "end": "2026-10-20"}]}
    d = diff_snapshots(prev, cur, favorites=["巨无霸"])
    print(json.dumps(d, ensure_ascii=False, indent=2))
