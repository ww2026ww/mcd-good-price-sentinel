#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_adapter.py —— 把麦当劳中国 MCP Server 的原始返回，归一化为优化器（vendor 优化器）期望的 JSON。

设计要点
--------
1. 本项目「算」的引擎是 vendored 优化器（hujjun/mcd-saver-strategist, MIT）。它只认
   一种标准 JSON 契约：menu / coupons / request。本文件负责把 MCP 工具的各种返回形状
   映射到该契约，使优化器与 MCP 解耦。
2. MCP 各工具的真实字段名随服务端版本变化；这里用「别名表」做健壮映射，
   拿不到的字段用合理默认兜底，绝不编造价格/券/活动数据。
3. 本文件是纯函数集合（无副作用、无网络），可单测；真正发网络请求的是 Skill/编排层
   通过 WorkBuddy 的 mcd-mcp 连接器完成，再把原始 JSON 喂给这里的 normalize_*。

输入形状（来自 MCP 连接器，经 Agent 转交 JSON）：
- query-meals / 在售餐品     -> normalize_menu()
- available-coupons / query-my-coupons / query-store-coupons -> normalize_coupons()
- campaign-calendar          -> normalize_campaigns()
输出形状：snapshot（见 build_snapshot），供 daily_sentinel 直接使用。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# 通用别名映射
# --------------------------------------------------------------------------- #
_ITEM_ALIASES = {
    "code": ["code", "itemCode", "item_code", "id", "sku", "productCode"],
    "name": ["name", "itemName", "item_name", "title", "productName", "dishName"],
    "category": ["category", "cat", "type", "kind"],
    "price": ["price", "amount", "unitPrice", "unit_price", "salePrice", "sale_price"],
    "calories": ["calories", "cal", "kcal", "energy"],
    "tags": ["tags", "labels", "flag", "flags"],
}
_COUPON_ALIASES = {
    "id": ["id", "couponId", "coupon_id", "code", "couponCode"],
    "type": ["type", "kind", "couponType", "coupon_type"],
    "title": ["title", "name", "couponName", "coupon_name", "desc", "description"],
    "item_codes": ["item_codes", "itemCodes", "applicableItems", "items", "productCodes"],
    "override_price": ["override_price", "overridePrice", "price", "newPrice"],
    "discount_pct": ["discount_pct", "discountPct", "rate", "discountRate", "pct"],
    "min_spend": ["min_spend", "minSpend", "threshold", "fullAmount", "minAmount"],
    "discount": ["discount", "amount", "reduce", "minus", "value"],
    "groups": ["groups", "combos", "combinations"],
    "gift_codes": ["gift_codes", "giftCodes", "gifts", "giftItems"],
    "stackable": ["stackable", "canStack", "combinable", "叠加"],
    "channel": ["channel", "scene", "applicableScene", "scope"],
}
_CAMPAIGN_ALIASES = {
    "id": ["id", "campaignId", "campaign_id", "activityId"],
    "title": ["title", "name", "campaignName", "activityName"],
    "start": ["start", "startTime", "start_time", "begin", "beginDate"],
    "end": ["end", "endTime", "end_time", "expire", "endDate"],
    "desc": ["desc", "description", "detail", "summary"],
    "tags": ["tags", "labels", "themes"],
}


def _pick(d: Dict[str, Any], aliases: Sequence[str], default: Any = None) -> Any:
    """按别名顺序从 dict 取值，命中第一个真值（或空值也接受，除非显式判断）。"""
    for a in aliases:
        if a in d and d[a] is not None:
            return d[a]
    return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "可", "是")
    return default


# --------------------------------------------------------------------------- #
# 归一化：菜单
# --------------------------------------------------------------------------- #
def normalize_menu(raw: Dict[str, Any]) -> Dict[str, Any]:
    """MCP 在售餐品 -> 优化器 menu JSON。

    支持两种常见形状：
      A) {"store_code":.., "currency":.., "items":[{...}]}
      B) {"storeCode":.., "products":[{...}]} / {"dishes":[{...}]}
    """
    store_code = _pick(raw, ["store_code", "storeCode", "storeCode", "shopCode"]) or ""
    currency = _pick(raw, ["currency", "cur"]) or "CNY"
    raw_items = (
        raw.get("items")
        or raw.get("products")
        or raw.get("dishes")
        or raw.get("data")
        or []
    )
    items: List[Dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        code = _pick(it, _ITEM_ALIASES["code"])
        name = _pick(it, _ITEM_ALIASES["name"], default=str(code or "未知餐品"))
        price = _as_float(_pick(it, _ITEM_ALIASES["price"]))
        if code is None:
            continue
        items.append(
            {
                "code": str(code),
                "name": str(name),
                "category": str(_pick(it, _ITEM_ALIASES["category"], default="")),
                "price": round(price, 2),
                "calories": int(_as_float(_pick(it, _ITEM_ALIASES["calories"]), 0)),
                "tags": list(_pick(it, _ITEM_ALIASES["tags"], default=[]) or []),
            }
        )
    return {"store_code": str(store_code), "currency": str(currency), "items": items}


# --------------------------------------------------------------------------- #
# 归一化：优惠券
# --------------------------------------------------------------------------- #
def normalize_coupon(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cid = _pick(raw, _COUPON_ALIASES["id"])
    if cid is None:
        return None
    ctype = _pick(raw, _COUPON_ALIASES["type"], default="threshold")
    cp: Dict[str, Any] = {
        "id": str(cid),
        "type": str(ctype),
        "title": str(_pick(raw, _COUPON_ALIASES["title"], default=str(cid))),
        "stackable": _as_bool(_pick(raw, _COUPON_ALIASES["stackable"]), False),
    }
    ch = _pick(raw, _COUPON_ALIASES["channel"])
    if ch:
        cp["channel"] = str(ch)
    # 按类型填字段（忽略不适用的字段，保持与优化器契约一致）
    if ctype == "item_price":
        cp["item_codes"] = [str(x) for x in (_pick(raw, _COUPON_ALIASES["item_codes"]) or [])]
        cp["override_price"] = _as_float(_pick(raw, _COUPON_ALIASES["override_price"]))
    elif ctype == "percent":
        cp["item_codes"] = [str(x) for x in (_pick(raw, _COUPON_ALIASES["item_codes"]) or [])]
        cp["discount_pct"] = _as_float(_pick(raw, _COUPON_ALIASES["discount_pct"]), 1.0)
    elif ctype == "threshold":
        cp["min_spend"] = _as_float(_pick(raw, _COUPON_ALIASES["min_spend"]))
        cp["discount"] = _as_float(_pick(raw, _COUPON_ALIASES["discount"]))
    elif ctype == "bundle_price":
        cp["groups"] = [list(map(str, g)) for g in (_pick(raw, _COUPON_ALIASES["groups"]) or [])]
        cp["price"] = _as_float(_pick(raw, _COUPON_ALIASES["override_price"]))
        if "price" in raw or "price" in (raw.get("groups") or [{}])[0:0]:
            pass
        if raw.get("price") is not None:
            cp["price"] = _as_float(raw.get("price"))
    elif ctype == "free_gift":
        cp["min_spend"] = _as_float(_pick(raw, _COUPON_ALIASES["min_spend"]))
        cp["gift_codes"] = [str(x) for x in (_pick(raw, _COUPON_ALIASES["gift_codes"]) or [])]
    return cp


def normalize_coupons(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """MCP 券列表 -> {"coupons": [...]}。支持 {coupons:[...]} / {data:[...]} / [...] 三种形状。"""
    if isinstance(raw, list):
        raw_list = raw
    elif isinstance(raw, dict):
        raw_list = raw.get("coupons") or raw.get("data") or raw.get("list") or []
    else:
        raw_list = []
    out: List[Dict[str, Any]] = []
    for c in raw_list:
        if isinstance(c, dict):
            nc = normalize_coupon(c)
            if nc:
                out.append(nc)
    return {"coupons": out}


def merge_coupon_sources(*sources: Any) -> Dict[str, List[Dict[str, Any]]]:
    """合并多个券来源（我的券 + 可领券 + 门店券），按 id 去重；我的券优先（避免被覆盖）。"""
    merged: Dict[str, Dict[str, Any]] = {}
    for src in sources:
        if not src:
            continue
        payload = normalize_coupons(src)
        for c in payload.get("coupons", []):
            merged.setdefault(c["id"], c)
    return {"coupons": list(merged.values())}


# --------------------------------------------------------------------------- #
# 归一化：活动
# --------------------------------------------------------------------------- #
def normalize_campaigns(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        raw_list = raw
    elif isinstance(raw, dict):
        raw_list = raw.get("campaigns") or raw.get("data") or raw.get("list") or []
    else:
        raw_list = []
    out: List[Dict[str, Any]] = []
    for c in raw_list:
        if not isinstance(c, dict):
            continue
        cid = _pick(c, _CAMPAIGN_ALIASES["id"])
        if cid is None:
            continue
        out.append(
            {
                "id": str(cid),
                "title": str(_pick(c, _CAMPAIGN_ALIASES["title"], default=str(cid))),
                "start": _pick(c, _CAMPAIGN_ALIASES["start"]),
                "end": _pick(c, _CAMPAIGN_ALIASES["end"]),
                "desc": str(_pick(c, _CAMPAIGN_ALIASES["desc"], default="")),
                "tags": list(_pick(c, _CAMPAIGN_ALIASES["tags"], default=[]) or []),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 由偏好构造优化器 request
# --------------------------------------------------------------------------- #
def build_request(preferences: Dict[str, Any]) -> Dict[str, Any]:
    """用户偏好 -> 优化器 request JSON。"""
    req: Dict[str, Any] = {
        "wants": list(preferences.get("favorite_items", []) or []),
        "dining_mode": str(preferences.get("dining_mode", "pickup")).strip().lower() or "pickup",
        "objective": str(preferences.get("objective", "balanced")).strip().lower() or "balanced",
        "allow_extras": bool(preferences.get("allow_extras", True)),
    }
    if preferences.get("typical_budget") is not None:
        req["budget"] = _as_float(preferences["typical_budget"])
    if preferences.get("max_calories") is not None:
        req["max_calories"] = int(_as_float(preferences["max_calories"]))
    if preferences.get("max_items") is not None:
        req["max_items"] = int(_as_float(preferences["max_items"]))
    if req["dining_mode"] == "delivery":
        req["delivery_fee"] = _as_float(preferences.get("delivery_fee", 0))
    if preferences.get("party_size"):
        req["party_size"] = int(_as_float(preferences["party_size"]))
    return req


# --------------------------------------------------------------------------- #
# 组装 snapshot
# --------------------------------------------------------------------------- #
def build_snapshot(
    menu_raw: Any,
    coupon_sources: Sequence[Any],
    campaigns_raw: Any,
    preferences: Dict[str, Any],
    fetched_at: str = "",
    store_code: str = "",
    dining_mode: str = "pickup",
) -> Dict[str, Any]:
    """把各 MCP 原始返回拼成 daily_sentinel 消费的 snapshot。"""
    menu = normalize_menu(menu_raw) if not (isinstance(menu_raw, dict) and "items" in menu_raw and "store_code" in menu_raw) else menu_raw
    # 若已经传的是归一化 menu 则直接用；否则归一化
    if "store_code" not in menu or "items" not in menu:
        menu = normalize_menu(menu_raw)
    coupons = merge_coupon_sources(*coupon_sources)
    campaigns = normalize_campaigns(campaigns_raw)
    return {
        "fetched_at": fetched_at,
        "store_code": store_code or menu.get("store_code", ""),
        "currency": menu.get("currency", "CNY"),
        "dining_mode": str(dining_mode).strip().lower() or "pickup",
        "menu": menu,
        "coupons": coupons,
        "campaigns": campaigns,
    }


def coupon_ids_of(snapshot: Dict[str, Any]) -> List[str]:
    return [c["id"] for c in snapshot.get("coupons", {}).get("coupons", [])]


def load_snapshot(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_snapshot(snapshot: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 简单自测：用 vendored sample_data 跑通归一化
    import os
    sd = os.path.join(os.path.dirname(__file__), "..", "vendor", "mcd-saver-strategist", "sample_data")
    if os.path.isdir(sd):
        menu = json.load(open(os.path.join(sd, "menu.json"), encoding="utf-8"))
        coupons = json.load(open(os.path.join(sd, "coupons.json"), encoding="utf-8"))
        snap = build_snapshot(menu, [coupons], [], {}, store_code="SZ0001")
        print("menu items:", len(snap["menu"]["items"]))
        print("coupons:", len(snap["coupons"]["coupons"]))
        print("OK")
