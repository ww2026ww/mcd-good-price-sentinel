#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦门省钱精算师 · 确定性优惠组合优化引擎 v2
==========================================

一个大模型不擅长、但代码很擅长的事情: 在「菜单 x 优惠券」的组合空间里,
穷举出**总价最低**的合规点餐方案, 并回答三个衍生问题:

    · 这些券里, 哪张是真便宜, 哪张是"看起来很美"?   -> audit_coupons()
    · 我还差几块钱到下一档满减, 加购划不划算?        -> suggest_gap()
    · 便宜和低卡不可兼得时, 边界在哪?                 -> pareto_frontier()

设计原则
--------
1. 纯标准库, 零第三方依赖, 可直接在 WorkBuddy 沙箱里跑。
2. 确定性: 相同输入永远得到相同输出, 便于复核与写测试。
3. 数据与逻辑分离: 输入是 MCP 返回结果经归一化后的 JSON, 输出是结构化方案,
   负责「算」, 不负责「说」。文案由 Agent 依据本结果生成。
4. 保守计价: 报价永不低于真实可付价。券商未标明可叠加时按不可叠处理;
   多人拼单只计一次满减, 不放大优惠。

输入 JSON 契约
--------------
menu.json
    {
      "store_code": "SZ0001",
      "currency": "CNY",
      "items": [
        {"code": "BIGMAC", "name": "巨无霸", "category": "burger",
         "price": 25.0, "calories": 550, "tags": ["beef", "classic"]}
      ]
    }

coupons.json  (所有券统一放在 coupons 数组里, 用 type 区分)
    {
      "coupons": [
        # 1) 单品改价券: 把某商品价格改成 override_price
        {"id": "C1", "type": "item_price", "title": "巨无霸立减",
         "item_codes": ["BIGMAC"], "override_price": 18.0, "stackable": false},
        # 2) 单品折扣券: 对匹配商品打 discount_pct 折 (0.8 = 8折)
        {"id": "C2", "type": "percent", "title": "小食 8 折",
         "item_codes": ["FRIES_M", "NUGGETS_5"], "discount_pct": 0.8, "stackable": false},
        # 3) 满减券: 小计 >= min_spend 时减 discount
        {"id": "C3", "type": "threshold", "title": "满 30 减 5",
         "min_spend": 30.0, "discount": 5.0, "stackable": true},
        # 4) 组合价券: 每组各挑 1 个, 打包价 price (如 1+1随心配)
        {"id": "C4", "type": "bundle_price", "title": "1+1 随心配",
         "groups": [["BIGMAC", "MCFLOWER"], ["FRIES_M", "COKE_M"]],
         "price": 12.9, "stackable": false},
        # 5) 赠品券: 满足 min_spend 后送 gift_codes
        {"id": "C5", "type": "free_gift", "title": "满 20 送派",
         "min_spend": 20.0, "gift_codes": ["PIE"], "stackable": true}
      ]
    }

  可选字段 channel 控制券的适用场景, 取值 "any"(默认) / "pickup" / "delivery"。
  例: 门店券通常仅到店可用 {"id": "S1", ..., "channel": "pickup"};
      外送券 {"id": "D1", ..., "channel": "delivery"}。

request.json
    {
      "wants": ["巨无霸", "薯条", "可乐"],   # 用户想吃的东西 (按名称模糊匹配)
      "budget": 45.0,                       # 可选, 总价上限 (含外送费)
      "max_calories": 1000,                 # 可选, 热量上限 (每人)
      "max_items": 5,                       # 可选, 最多几件
      "allow_extras": true,                 # 是否允许为了用券而加购
      "dining_mode": "pickup",              # 可选, "pickup"(默认) / "delivery"
      "delivery_fee": 9.0,                  # 可选, dining_mode=delivery 时计入总价
      "objective": "cheapest",              # 可选, 见下
      "party_size": 1                       # 可选, 几人份 (多人拼单)
    }

  objective 四种目标 (都先以「最低到手价」为锚, 再在价格容忍带内做次级优选):
      cheapest  总价最低 (默认)
      balanced  最均衡: 到手价 <= 最低价 x 1.15 的方案里, 优先"想吃的都吃到"、件数少
      lightest  最低卡: 到手价 <= 最低价 x 1.30 的方案里, 热量最低
      fewest    最精简: 到手价 <= 最低价 x 1.10 的方案里, 件数最少

输出
----
{"plans": [...], "baseline": {...}, "best": {...}, "pareto": [...], "gap": {...}}
每个 plan:
    {"items": [...], "lines": [...], "subtotal": 42.0, "coupon_saving": 12.6,
     "total": 29.4, "calories": 980, "coupons": [...], "gifts": [...],
     "saving_pct": 0.30, "wants_fulfilled": 3, "notes": [...]}
"""

from __future__ import annotations

import json
import os
import sys
from itertools import combinations, product
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_COMBO_COUPONS = 3          # 单方案最多叠加几张券, 控制搜索规模
MAX_CANDIDATES_PER_WANT = 4    # 每个"想要的"最多保留几个候选商品
TOP_N_PLANS = 5
OBJECTIVES = ("cheapest", "balanced", "lightest", "fewest")

# 各目标的"价格容忍带": 允许比绝对最低价贵多少倍, 换取次级目标更优
PRICE_SLACK = {"balanced": 1.15, "lightest": 1.30, "fewest": 1.10}

# 券体检的判定阈值 (元)
MARGINAL_SAVING = 2.0

# --------------------------------------------------------------------------- #
# 载入与工具函数
# --------------------------------------------------------------------------- #
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def item_index(items: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {it["code"]: it for it in items}


def normalize_name(text: str) -> str:
    return (text or "").strip().lower().replace(" ", "")


def match_want(want: str, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按名称/标签模糊匹配用户想要的东西, 返回候选商品列表。"""
    key = normalize_name(want)
    hits: List[Tuple[int, Dict[str, Any]]] = []
    for it in items:
        name = normalize_name(it.get("name", ""))
        tags = [normalize_name(t) for t in it.get("tags", [])]
        code = normalize_name(it.get("code", ""))
        score = -1
        if key == name or key == code:
            score = 100
        elif key and (key in name or name in key):
            score = 80
        elif any(key in t or t in key for t in tags):
            score = 60
        if score >= 0:
            hits.append((score, it))
    hits.sort(key=lambda x: (-x[0], x[1]["price"]))
    return [it for _, it in hits]


def filter_coupons_for_mode(
    coupons: Sequence[Dict[str, Any]], dining_mode: str
) -> List[Dict[str, Any]]:
    """按用餐场景过滤券: channel 为 any 或与当前场景一致者可用。

    该字段是可选扩展; 未标 channel 的券视为 any, 保持向后兼容。
    """
    mode = (dining_mode or "pickup").strip().lower()
    out: List[Dict[str, Any]] = []
    for c in coupons:
        ch = str(c.get("channel", "any")).strip().lower() or "any"
        if ch in ("any", mode):
            out.append(c)
    return out


def _distinct_codes(basket: Sequence[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for it in basket:
        if it["code"] not in seen:
            seen.append(it["code"])
    return seen


def _baseline_basket(
    wants: Sequence[str], items: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """无券基线: 对每个"想吃的", 取最匹配且最便宜的那个, 同款只留一份。"""
    out: List[Dict[str, Any]] = []
    used: set = set()
    for w in wants:
        for cand in match_want(w, items):
            if cand["code"] not in used:
                out.append(cand)
                used.add(cand["code"])
                break
    return out


# --------------------------------------------------------------------------- #
# 券的可适用性 & 计价
# --------------------------------------------------------------------------- #
def _bucket_codes(items: Sequence[Dict[str, Any]]) -> List[str]:
    return [it["code"] for it in items]


def coupon_conflict(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """判断两张券是否互斥 (不可同时使用)。"""
    if not a.get("stackable", False) or not b.get("stackable", False):
        return True
    # 两张满减/赠品类券可以叠加(一般不同门槛), 但针对同一商品的券互斥
    ta, tb = a["type"], b["type"]
    if ta in ("threshold", "free_gift") and tb in ("threshold", "free_gift"):
        return False
    ca = set(a.get("item_codes", [])) | {c for g in a.get("groups", []) for c in g}
    cb = set(b.get("item_codes", [])) | {c for g in b.get("groups", []) for c in g}
    return bool(ca & cb)


def price_with_basket(
    basket: Sequence[Dict[str, Any]],
    coupon_ids: Sequence[str],
    coupons_by_id: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """给定菜篮 + 一组券, 计算合法则返回明细, 非法返回 None。"""
    basket_codes = _bucket_codes(basket)
    code2item = {it["code"]: it for it in basket}

    price_overrides: Dict[str, float] = {}
    percent_overrides: Dict[str, float] = {}
    bundle_lines: List[Dict[str, Any]] = []
    consumed: set = set()

    for cid in coupon_ids:
        cp = coupons_by_id[cid]
        ct = cp["type"]

        if ct == "item_price":
            usable = [c for c in cp["item_codes"] if c in code2item]
            if not usable:
                return None
            for c in usable:
                price_overrides[c] = min(
                    price_overrides.get(c, float("inf")), float(cp["override_price"])
                )

        elif ct == "percent":
            usable = [c for c in cp["item_codes"] if c in code2item]
            if not usable:
                return None
            for c in usable:
                percent_overrides[c] = min(
                    percent_overrides.get(c, 1.0), float(cp["discount_pct"])
                )

        elif ct == "bundle_price":
            picked: List[str] = []
            for group in cp["groups"]:
                cand = [c for c in group if c in code2item and c not in consumed]
                if not cand:
                    return None
                cand.sort(key=lambda c: code2item[c]["price"], reverse=True)
                picked.append(cand[0])
                consumed.add(cand[0])
            bundle_lines.append({"coupon": cid, "title": cp.get("title", cid),
                                 "codes": picked, "price": float(cp["price"])})

        elif ct == "threshold":
            pass  # 满减在商品小计算完后统一处理

        elif ct == "free_gift":
            pass  # 赠品在门槛校验后统一处理

    # 先算商品小计
    lines: List[Dict[str, Any]] = []
    subtotal = 0.0
    for it in basket:
        code = it["code"]
        if code in consumed:
            continue  # 已被组合价券打包
        base = float(it["price"])
        eff = base
        tag = ""
        if code in price_overrides:
            eff = min(eff, price_overrides[code]); tag = "单品券价"
        if code in percent_overrides:
            eff = min(eff, round(base * percent_overrides[code], 2)); tag = "折扣"
        subtotal += eff
        lines.append({"code": code, "name": it["name"], "price": eff, "tag": tag})

    for bl in bundle_lines:
        subtotal += bl["price"]
        lines.append({"code": "+".join(bl["codes"]), "name": bl["title"],
                      "price": bl["price"], "tag": "组合价"})

    # 满减: 基于当前小计, 取满足门槛的最大抵扣
    applied_threshold = None
    best_discount = 0.0
    for cid in coupon_ids:
        cp = coupons_by_id[cid]
        if cp["type"] == "threshold" and subtotal >= float(cp["min_spend"]):
            if float(cp["discount"]) > best_discount:
                best_discount = float(cp["discount"])
                applied_threshold = cid
    threshold_discount = best_discount

    # 赠品: 门槛校验
    valid_gifts: List[str] = []
    for cid in coupon_ids:
        cp = coupons_by_id[cid]
        if cp["type"] == "free_gift" and subtotal >= float(cp.get("min_spend", 0)):
            valid_gifts.extend(cp.get("gift_codes", []))

    total = round(max(subtotal - threshold_discount, 0.0), 2)
    return {
        "lines": lines,
        "subtotal": round(subtotal, 2),
        "threshold_coupon": applied_threshold,
        "threshold_discount": round(threshold_discount, 2),
        "total": total,
        "gifts": sorted(set(valid_gifts)),
    }


def best_coupon_plan(
    basket: Sequence[Dict[str, Any]],
    coupons: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """在菜篮上搜索最优用券组合。"""
    coupons_by_id = {c["id"]: c for c in coupons}
    ids = list(coupons_by_id.keys())

    # 先筛出至少可能适用的券, 减少组合规模
    basket_codes = set(_bucket_codes(basket))
    pool: List[str] = []
    for cid in ids:
        cp = coupons_by_id[cid]
        if cp["type"] == "threshold":
            pool.append(cid)
        elif cp["type"] == "free_gift":
            pool.append(cid)
        elif set(cp.get("item_codes", [])) & basket_codes:
            pool.append(cid)
        elif cp["type"] == "bundle_price":
            if all(set(g) & basket_codes for g in cp.get("groups", [])):
                pool.append(cid)

    best: Optional[Dict[str, Any]] = None
    best_total = float("inf")

    for r in range(0, min(MAX_COMBO_COUPONS, len(pool)) + 1):
        for combo in combinations(pool, r):
            # 互斥检查
            ok = True
            for a, b in combinations(combo, 2):
                if coupon_conflict(coupons_by_id[a], coupons_by_id[b]):
                    ok = False
                    break
            if not ok:
                continue
            res = price_with_basket(basket, combo, coupons_by_id)
            if res is None:
                continue
            # 同价时优先"带赠品"的组合: 赠品券不涨价, 白得的东西没理由不要
            better = res["total"] < best_total - 1e-9 or (
                best is not None
                and abs(res["total"] - best_total) < 1e-9
                and len(res.get("gifts", [])) > len(best.get("gifts", []))
            )
            if better:
                best_total = res["total"]
                best = res
                best["coupon_ids"] = list(combo)

    if best is None:
        best = price_with_basket(basket, [], coupons_by_id)
        best["coupon_ids"] = []
    return best


# --------------------------------------------------------------------------- #
# 菜篮生成
# --------------------------------------------------------------------------- #
def build_baskets(
    request: Dict[str, Any],
    items: Sequence[Dict[str, Any]],
) -> Iterable[List[Dict[str, Any]]]:
    """根据用户"想要的"生成候选菜篮。"""
    wants: List[str] = request.get("wants", []) or []
    max_items = int(request.get("max_items", 5))
    allow_extras = bool(request.get("allow_extras", True))

    want_candidates: List[List[Dict[str, Any]]] = []
    locked_codes: set = set()
    for w in wants:
        cands = match_want(w, items)[:MAX_CANDIDATES_PER_WANT]
        if not cands:
            continue
        want_candidates.append(cands)
        locked_codes.update(it["code"] for it in cands)

    if not want_candidates:
        # 没给明确想吃的, 就用最便宜的几件兜底
        cheapest = sorted(items, key=lambda x: x["price"])[:MAX_CANDIDATES_PER_WANT]
        yield from ([it] for it in cheapest)
        return

    extras_pool = [it for it in sorted(items, key=lambda x: x["price"])
                   if it["code"] not in locked_codes][:6] if allow_extras else []

    for combo in product(*want_candidates):
        codes = [it["code"] for it in combo]
        if len(set(codes)) != len(codes):
            continue
        if len(combo) > max_items:
            continue
        # 基础菜篮
        yield list(combo)
        # 加购 1 件: 往往是为了凑满减门槛
        if allow_extras and len(combo) < max_items:
            for extra in extras_pool:
                yield list(combo) + [extra]
        # 加购 2 件
        if allow_extras and len(combo) + 1 < max_items:
            for e1, e2 in combinations(extras_pool, 2):
                if {e1["code"], e2["code"]} & set(codes):
                    continue
                yield list(combo) + [e1, e2]


# --------------------------------------------------------------------------- #
# 帕累托前沿: 价格与热量不可兼得时的边界
# --------------------------------------------------------------------------- #
def pareto_frontier(
    plans: Sequence[Dict[str, Any]], limit: int = 8
) -> List[Dict[str, Any]]:
    """在 (到手价, 热量) 二维上取非支配解集。

    一个方案被支配 <=> 存在另一个方案「更便宜且热量不更高」。
    返回结果按价格升序, 热量严格递增 —— 即"想吃得更便宜就得吃更多热量"的边界曲线。
    用排序 + 扫描实现, O(n log n); 相同 (价, 卡) 的点只保留一个。
    """
    uniq: Dict[Tuple[float, int], Dict[str, Any]] = {}
    for p in plans:
        key = (round(float(p["total"]), 2), int(p.get("calories", 0)))
        if key not in uniq:
            uniq[key] = p
    front: List[Dict[str, Any]] = []
    best_cal = float("inf")
    for p in sorted(uniq.values(), key=lambda x: (x["total"], x["calories"])):
        cal = int(p.get("calories", 0))
        if cal < best_cal:
            front.append(p)
            best_cal = cal
    return front[:limit]


# --------------------------------------------------------------------------- #
# 券体检: 名义面额 vs 真实节省
# --------------------------------------------------------------------------- #
def _nominal_value(cp: Dict[str, Any], by_code: Dict[str, Dict[str, Any]]) -> float:
    """券的名义优惠额 (营销口径), 用它来对照真实节省。"""
    ct = cp["type"]
    if ct == "item_price":
        prices = [by_code[c]["price"] for c in cp.get("item_codes", []) if c in by_code]
        return round(max(prices) - float(cp["override_price"]), 2) if prices else 0.0
    if ct == "percent":
        prices = [by_code[c]["price"] for c in cp.get("item_codes", []) if c in by_code]
        if not prices:
            return 0.0
        return round(max(prices) * (1.0 - float(cp["discount_pct"])), 2)
    if ct == "threshold":
        return round(float(cp.get("discount", 0)), 2)
    if ct == "bundle_price":
        total = 0.0
        for g in cp.get("groups", []):
            prices = [by_code[c]["price"] for c in g if c in by_code]
            if prices:
                total += max(prices)
        return round(total - float(cp["price"]), 2)
    if ct == "free_gift":
        return round(sum(by_code[g]["price"] for g in cp.get("gift_codes", []) if g in by_code), 2)
    return 0.0


def audit_coupons(
    menu: Dict[str, Any],
    coupons_payload: Dict[str, Any],
    request: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """逐张体检优惠券, 回答「这张券到底值多少钱」。

    四个口径:
      nominal          营销面额 (券上写的大字)
      on_baseline      不改变点单内容时, 这张券实际能省多少
      real_saving      允许加购时, 全局能达到的最大节省 (可能为负 = 白折腾)
      opportunity_cost 只用这张券 vs 全局最优, 要多花多少

    兑现率 effective_rate = on_baseline / nominal, 越低说明"水分"越大。

    判定: worth > marginal > suboptimal/trap > conditional > useless > invalid。
    其中 trap 是最反直觉的一类 —— 券本身在省钱, 但用了它就吃不到更划算的组合,
    省下的小钱还抵不上放弃的大钱。
    """
    request = dict(request or {})
    items = menu["items"]
    by_code = item_index(items)
    mode = (request.get("dining_mode") or "pickup").strip().lower()
    coupons = filter_coupons_for_mode(coupons_payload.get("coupons", []), mode)
    by_id = {c["id"]: c for c in coupons}

    wants = request.get("wants", []) or []
    base_basket = _baseline_basket(wants, items)
    base_total = round(sum(it["price"] for it in base_basket), 2)

    # 全券全局最优, 用来判断某张券是否出现在最优解里
    global_best = optimize(menu, coupons_payload, request)
    best_plan = global_best["plans"][0] if global_best["plans"] else None
    best_ids = {c["id"] for c in (best_plan["coupons"] if best_plan else [])}
    best_total = round(float(best_plan["total"]), 2) if best_plan else None

    solo_request = {k: v for k, v in request.items()
                    if k not in ("budget", "max_calories", "objective", "party_size")}
    solo_request["allow_extras"] = True

    rows: List[Dict[str, Any]] = []
    for c in coupons:
        nominal = _nominal_value(c, by_code)
        # 赠品券不改变金额, 它的价值等于赠品本身 —— 单独折成钱计入节省
        is_gift = c["type"] == "free_gift"

        # 口径一: 不动点单内容
        on_baseline: Optional[float] = None
        if base_basket:
            res0 = price_with_basket(base_basket, [c["id"]], by_id)
            if res0 is not None:
                bonus = nominal if (is_gift and res0.get("gifts")) else 0.0
                on_baseline = round(base_total - res0["total"] + bonus, 2)

        # 口径二: 允许加购的全局最优
        solo = optimize(menu, {"coupons": [c]}, solo_request)
        solo_plan = solo["plans"][0] if solo["plans"] else None
        if solo_plan is not None:
            bonus = nominal if (is_gift and solo_plan.get("gifts")) else 0.0
            real_saving = round(base_total - solo_plan["total"] + bonus, 2)
        else:
            real_saving = None

        # 券面本身就注水 (改价后不比原价低 / 折扣 >= 100%)
        watered = False
        if c["type"] == "item_price":
            prices = [by_code[x]["price"] for x in c.get("item_codes", []) if x in by_code]
            watered = bool(prices) and float(c["override_price"]) >= max(prices) - 1e-9
        elif c["type"] == "percent":
            watered = float(c.get("discount_pct", 1)) >= 1.0

        # 机会成本: 只用这张券, 会比全局最优组合多花多少
        opportunity = (round(float(solo_plan["total"]) - best_total, 2)
                       if (solo_plan is not None and best_total is not None) else None)

        if watered:
            verdict, reason = "invalid", "券面价不低于原价，等于没有优惠"
        elif on_baseline is None:
            verdict, reason = "conditional", "直选清单里没有适用商品，需要先加购才用得上"
        elif on_baseline <= 0.01 and (real_saving or 0) <= 0.01:
            verdict, reason = "useless", "在当前点单下省不到钱"
        elif opportunity is not None and opportunity > 0.01:
            # 单看它在省钱, 但用了它就吃不到更划算的组合 —— 这才是最大的坑
            if nominal and opportunity > nominal:
                verdict = "trap"
                reason = (f"名义省 {nominal:.2f} 元，但用了它会比最优组合多花 "
                          f"{opportunity:.2f} 元")
            else:
                verdict = "suboptimal"
                reason = f"能省 {on_baseline:.2f} 元，但还有更好的组合(多花 {opportunity:.2f} 元)"
        elif on_baseline < MARGINAL_SAVING:
            verdict, reason = "marginal", f"只能省 {on_baseline:.2f} 元，聊胜于无"
        else:
            verdict, reason = "worth", f"不动点单内容就能省 {on_baseline:.2f} 元"

        rate = round(on_baseline / nominal, 2) if (nominal and nominal > 0 and on_baseline is not None) else None
        in_optimal = c["id"] in best_ids
        if verdict == "worth" and nominal and rate is not None and rate < 0.6:
            reason += f"；但券面写 {nominal:.2f} 元，兑现率仅 {rate*100:.0f}%"
        if verdict == "worth" and not in_optimal:
            reason += "；不过它不在最优解里（叠它会挤掉更划算的券）"

        rows.append({
            "id": c["id"],
            "title": c.get("title", c["id"]),
            "type": c["type"],
            "channel": c.get("channel", "any"),
            "stackable": bool(c.get("stackable", False)),
            "nominal": nominal,
            "on_baseline": on_baseline,
            "real_saving": real_saving,
            "opportunity_cost": opportunity,
            "effective_rate": rate,
            "in_optimal_plan": in_optimal,
            "verdict": verdict,
            "reason": reason,
        })

    rank = {"worth": 0, "marginal": 1, "suboptimal": 2, "trap": 3,
            "conditional": 4, "useless": 5, "invalid": 6}
    rows.sort(key=lambda r: (rank.get(r["verdict"], 9),
                             -(r["real_saving"] if r["real_saving"] is not None else -999), r["id"]))
    return rows


# --------------------------------------------------------------------------- #
# 凑单建议: 差几块钱到下一档, 值不值得凑
# --------------------------------------------------------------------------- #
def cheapest_filler(
    items: Sequence[Dict[str, Any]], gap: float, max_sum: float = 200.0
) -> Optional[Dict[str, Any]]:
    """凑单核心: 用最少钱把差额 gap 补上。

    不是简单挑最便宜的一件 —— 4 元的圆筒补不上 8.5 元的缺口。
    这里做「最小可达总额的子集和」: 在可选商品里找一个子集,
    使总价 >= gap 且尽量小。DP 值域被 gap 与单品最高价封顶, 规模可控。
    """
    gap = round(float(gap), 2)
    prices: List[Tuple[Dict[str, Any], float]] = [
        (it, round(float(it["price"]), 2)) for it in items if float(it.get("price", 0)) > 0
    ]
    if not prices or gap <= 0:
        return None

    cap = min(gap + max(p for _, p in prices), max_sum)
    reachable: Dict[float, Optional[Tuple[float, Dict[str, Any]]]] = {0.0: None}
    for it, pr in prices:
        add: Dict[float, Optional[Tuple[float, Dict[str, Any]]]] = {}
        for s in list(reachable.keys()):
            ns = round(s + pr, 2)
            if ns <= cap + 1e-9 and ns not in reachable and ns not in add:
                add[ns] = (s, it)
        reachable.update(add)

    hits = [s for s in reachable if s >= gap - 1e-9]
    if not hits:
        return {"items": [], "cost": None}
    target = min(hits)

    picked: List[Dict[str, Any]] = []
    cur = target
    while reachable[cur] is not None:
        prev, it = reachable[cur]
        picked.append(it)
        cur = prev
    return {"items": picked, "cost": round(target, 2)}


def suggest_gap(
    plan: Dict[str, Any],
    menu: Dict[str, Any],
    coupons_payload: Dict[str, Any],
    request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """给出「再加一点就能多减」的建议, 并算清净变化。

    核心洞察: 净变化 = 补位商品价格 - 新增抵扣。
    当补位商品比新增抵扣还便宜时, **加购反而更省钱**(负成本加购)。
    """
    request = request or {}
    items = menu["items"]
    mode = (request.get("dining_mode") or "pickup").strip().lower()
    coupons = filter_coupons_for_mode(coupons_payload.get("coupons", []), mode)
    by_id = {c["id"]: c for c in coupons}

    applied = {c["id"] for c in plan.get("coupons", [])}
    applied_cps = [by_id[i] for i in applied if i in by_id]

    def _conflicts(c: Dict[str, Any]) -> bool:
        """已用的券若不可叠加, 就不能再推荐一张"加了也用不了"的券。"""
        return any(coupon_conflict(c, a) for a in applied_cps)

    subtotal = round(float(plan.get("subtotal", 0.0)), 2)
    in_basket = set()
    for it in plan.get("items", []):
        in_basket.add(it["code"])
    for g in plan.get("gifts", []):
        in_basket.add(g["code"])

    out: Dict[str, Any] = {"threshold": None, "gift": None}

    # ---- 下一档满减 ----
    next_th: Optional[Dict[str, Any]] = None
    for c in coupons:
        if c["type"] != "threshold" or c["id"] in applied:
            continue
        if _conflicts(c):
            continue
        gap = round(float(c["min_spend"]) - subtotal, 2)
        if gap <= 0:
            continue
        if next_th is None or gap < next_th["gap"]:
            next_th = {"coupon": c, "gap": gap}

    if next_th is not None:
        c = next_th["coupon"]
        pool = [it for it in items if it["code"] not in in_basket]
        pick = cheapest_filler(pool, next_th["gap"])
        extra = round(float(c["discount"]) - float(plan.get("threshold_discount", 0.0)), 2)
        if pick and pick["cost"] is not None:
            net = round(pick["cost"] - extra, 2)
            names = "、".join(f"{it['name']}(¥{float(it['price']):.2f})" for it in pick["items"])
            out["threshold"] = {
                "coupon_id": c["id"],
                "title": c.get("title", c["id"]),
                "gap": next_th["gap"],
                "filler": {"code": pick["items"][0]["code"], "name": pick["items"][0]["name"],
                           "price": float(pick["items"][0]["price"])} if pick["items"] else None,
                "fillers": [{"code": it["code"], "name": it["name"], "price": float(it["price"])}
                            for it in pick["items"]],
                "filler_cost": pick["cost"],
                "extra_discount": extra,
                "net_change": net,
                "verdict": "worth" if net < 0 else ("even" if net == 0 else "costly"),
                "text": (
                    f"还差 ¥{next_th['gap']:.2f} 到「{c.get('title', c['id'])}」，"
                    f"最省的补位是 {names}，合计 ¥{pick['cost']:.2f}；"
                    + (f"多减 ¥{extra:.2f}，净省 ¥{-net:.2f}" if net < 0
                       else (f"多减 ¥{extra:.2f}，花的钱一分不涨，等于白得一份 {pick['items'][0]['name']}"
                             if net == 0 else
                             f"多减 ¥{extra:.2f}，净多花 ¥{net:.2f}"))
                ),
            }
        else:
            out["threshold"] = {"coupon_id": c["id"], "title": c.get("title", c["id"]),
                                "gap": next_th["gap"], "filler": None, "fillers": [],
                                "filler_cost": None, "extra_discount": extra, "net_change": None,
                                "verdict": "no-filler", "text": "菜单里凑不出这个差额"}

    # ---- 未达成的赠品 ----
    for c in coupons:
        if c["type"] != "free_gift" or c["id"] in applied:
            continue
        if _conflicts(c):
            continue
        gap = round(float(c.get("min_spend", 0)) - subtotal, 2)
        if gap <= 0:
            continue
        by_code = item_index(items)
        gift_names = [by_code[g]["name"] for g in c.get("gift_codes", []) if g in by_code]
        gift_value = round(sum(by_code[g]["price"] for g in c.get("gift_codes", []) if g in by_code), 2)
        pool = [it for it in items if it["code"] not in in_basket]
        pick = cheapest_filler(pool, gap)
        if not pick or pick["cost"] is None:
            continue
        net = round(pick["cost"] - gift_value, 2)
        out["gift"] = {
            "coupon_id": c["id"], "title": c.get("title", c["id"]), "gap": gap,
            "filler": {"code": pick["items"][0]["code"], "name": pick["items"][0]["name"],
                       "price": float(pick["items"][0]["price"])} if pick["items"] else None,
            "fillers": [{"code": it["code"], "name": it["name"], "price": float(it["price"])}
                        for it in pick["items"]],
            "filler_cost": pick["cost"], "gift_value": gift_value, "net_change": net,
            "verdict": "worth" if net < 0 else ("even" if net == 0 else "costly"),
            "text": (
                f"再买 ¥{gap:.2f} 可送「{'、'.join(gift_names) or '赠品'}」(估值 ¥{gift_value:.2f})，"
                f"最省补位合计 ¥{pick['cost']:.2f}，"
                + (f"净省 ¥{-net:.2f}" if net < 0 else f"净多花 ¥{net:.2f}")
            ),
        }
        break

    return out


# --------------------------------------------------------------------------- #
# 目标选择
# --------------------------------------------------------------------------- #
def _pick_plans(
    plans: Sequence[Dict[str, Any]], objective: str, limit: int
) -> List[Dict[str, Any]]:
    """按 objective 排序取前 limit 名。

    所有目标都以「最低到手价」为锚, 只在价格容忍带内做次级优选,
    这样不会为了"更均衡"而报出贵得离谱的方案。
    """
    if not plans:
        return []
    objective = objective if objective in OBJECTIVES else "cheapest"
    cheapest = min(p["total"] for p in plans)

    if objective == "cheapest":
        pool = list(plans)
        pool.sort(key=lambda p: (p["total"], p["calories"], p["item_count"]))
        return pool[:limit]

    cap = cheapest * PRICE_SLACK[objective]
    pool = [p for p in plans if p["total"] <= cap + 1e-9]
    if not pool:
        pool = list(plans)

    if objective == "balanced":
        # 想吃的都吃到 > 件数少 > 价格低 > 热量低
        pool.sort(key=lambda p: (-p["wants_fulfilled"], p["item_count"],
                                 p["total"], p["calories"]))
    elif objective == "lightest":
        pool.sort(key=lambda p: (p["calories"], p["total"]))
    elif objective == "fewest":
        pool.sort(key=lambda p: (p["item_count"], p["total"], p["calories"]))
    return pool[:limit]


def _objective_reason(objective: str, chosen: Dict[str, Any], cheapest: Dict[str, Any]) -> str:
    if objective == "cheapest":
        return "按「到手价最低」选出"
    delta = round(chosen["total"] - cheapest["total"], 2)
    tags = {
        "balanced": "价格容忍 15% 内，优先把想吃的都吃到、件数更少",
        "lightest": "价格容忍 30% 内，优先热量最低",
        "fewest": "价格容忍 10% 内，优先件数最少",
    }
    if objective == "lightest":
        gain = int(cheapest["calories"]) - int(chosen["calories"])
    elif objective == "fewest":
        gain = cheapest["item_count"] - chosen["item_count"]
    else:
        gain = chosen["wants_fulfilled"] - cheapest["wants_fulfilled"]

    if delta <= 0 and gain == 0:
        return tags[objective].split("；")[0] + "；本次最优解同时满足省钱与这个目标，没有取舍"
    tail = "与最低价方案等价" if delta <= 0 else f"比最低价方案多花 ¥{delta:.2f}"
    extra = ""
    if objective == "lightest":
        extra = f"，热量少 {gain} kcal"
    elif objective == "fewest":
        extra = f"，少 {gain} 件"
    elif objective == "balanced":
        extra = f"，想吃的覆盖 {chosen['wants_fulfilled']}/{chosen['wants_total']}"
    return tags[objective] + "；" + tail + extra


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def optimize(
    menu: Dict[str, Any],
    coupons_payload: Dict[str, Any],
    request: Dict[str, Any],
) -> Dict[str, Any]:
    items = menu["items"]
    all_coupons = coupons_payload.get("coupons", [])
    mode = (request.get("dining_mode") or "pickup").strip().lower()
    coupons = filter_coupons_for_mode(all_coupons, mode)
    delivery_fee = float(request.get("delivery_fee", 0) or 0) if mode == "delivery" else 0.0
    idx = item_index(items)

    wants: List[str] = request.get("wants", []) or []
    objective = str(request.get("objective") or "cheapest").strip().lower()
    if objective not in OBJECTIVES:
        objective = "cheapest"
    party = max(1, int(request.get("party_size", 1) or 1))

    # 无券基线: 用户想要的最便宜直选
    baseline_basket = _baseline_basket(wants, items)
    baseline_total = round(sum(it["price"] for it in baseline_basket), 2)
    baseline_cal = sum(int(it.get("calories", 0)) for it in baseline_basket)

    plans: List[Dict[str, Any]] = []
    seen: set = set()

    for basket in build_baskets(request, items):
        codes = tuple(sorted(it["code"] for it in basket))
        res = best_coupon_plan(basket, coupons)

        cal = sum(int(it.get("calories", 0)) for it in basket)
        for gift_code in res.get("gifts", []):
            if gift_code in idx:
                cal += int(idx[gift_code].get("calories", 0))

        raw = round(sum(it["price"] for it in basket), 2)
        unit_total = round(res["total"] + delivery_fee, 2)
        saving = round(raw - res["total"], 2)

        # 多人拼单: 商品按份数计, 但优惠只算一次 (保守, 不会报出假低价)
        if party > 1:
            total = round(raw * party - saving + delivery_fee, 2)
        else:
            total = unit_total

        if request.get("budget") is not None and total > float(request["budget"]):
            continue
        if request.get("max_calories") is not None and cal > int(request["max_calories"]):
            continue

        key = (codes, total)
        if key in seen:
            continue
        seen.add(key)

        fulfilled = sum(1 for w in wants if match_want(w, basket))

        plans.append({
            "items": [{"code": it["code"], "name": it["name"], "category": it.get("category", "")}
                      for it in basket],
            "lines": res["lines"],
            "gifts": [{"code": g, "name": idx[g]["name"]} for g in res.get("gifts", []) if g in idx],
            "coupons": [{"id": cid, "title": next((c["title"] for c in coupons if c["id"] == cid), cid)}
                        for cid in res.get("coupon_ids", [])],
            "subtotal": res["subtotal"],
            "threshold_discount": res.get("threshold_discount", 0.0),
            "delivery_fee": round(delivery_fee, 2),
            "total": total,
            "unit_total": unit_total,
            "party_size": party,
            "per_person": round(total / party, 2),
            "raw_total": raw,
            "coupon_saving": saving,
            "saving_pct": round(saving / raw, 4) if raw else 0.0,
            "calories": cal,
            "item_count": len(basket),
            "wants_fulfilled": fulfilled,
            "wants_total": len(wants),
        })

    ranked = _pick_plans(plans, objective, TOP_N_PLANS)
    for p in ranked:
        p["notes"] = _make_notes(p, baseline_total, request)

    absolutely_cheapest = _pick_plans(plans, "cheapest", 1)
    reason = (_objective_reason(objective, ranked[0], absolutely_cheapest[0])
              if ranked and absolutely_cheapest else "")

    result: Dict[str, Any] = {
        "store_code": menu.get("store_code", ""),
        "currency": menu.get("currency", "CNY"),
        "dining_mode": mode,
        "delivery_fee": round(delivery_fee, 2),
        "objective": objective,
        "objective_reason": reason,
        "party_size": party,
        "baseline": {"items": [it["name"] for it in baseline_basket],
                     "total": baseline_total, "calories": baseline_cal},
        "plans": ranked,
        "plan_pool_size": len(plans),
        "pareto": pareto_frontier(plans),
    }
    result["gap"] = (suggest_gap(ranked[0], menu, coupons_payload, request) if ranked
                     else {"threshold": None, "gift": None})
    return result


def _make_notes(plan: Dict[str, Any], baseline_total: float, request: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    vs_base = round(baseline_total - plan["total"], 2)
    if plan.get("party_size", 1) > 1:
        vs_base = round(baseline_total * plan["party_size"] - plan["total"], 2)
    if vs_base > 0:
        notes.append(f"比「直接按想吃的点」省 {vs_base:.2f} 元")
    elif vs_base < 0:
        notes.append(f"比原意向贵 {abs(vs_base):.2f} 元 (为使用优惠券加购)")
    if plan["saving_pct"] >= 0.25:
        notes.append(f"折扣力度 {plan['saving_pct']*100:.0f}%, 属于强力优惠组合")
    if plan["threshold_discount"] > 0:
        notes.append(f"触发满减, 立减 {plan['threshold_discount']:.2f} 元")
    if plan["gifts"]:
        notes.append("达成赠品条件: " + "、".join(g["name"] for g in plan["gifts"]))
    if plan.get("delivery_fee", 0) > 0:
        notes.append(f"已含外送费 {plan['delivery_fee']:.2f} 元")
    if plan.get("party_size", 1) > 1:
        notes.append(
            f"{plan['party_size']} 人拼单: 商品按份数计, 优惠只计一次(保守), "
            f"人均 ¥{plan['per_person']:.2f}"
        )
    if request.get("budget") is not None:
        notes.append(f"预算 {request['budget']} 元内 ✓")
    return notes


# --------------------------------------------------------------------------- #
# 文本报告
# --------------------------------------------------------------------------- #
def _sym(result: Dict[str, Any]) -> str:
    cur = result.get("currency", "CNY")
    return "¥" if cur == "CNY" else cur + " "


def format_gap(gap: Dict[str, Any]) -> str:
    lines: List[str] = []
    for key, label in (("threshold", "满减"), ("gift", "赠品")):
        item = (gap or {}).get(key)
        if not item:
            continue
        flag = {"worth": "✅", "even": "➖", "costly": "❌"}.get(item.get("verdict"), "·")
        lines.append(f"  {flag} [{label}] {item['text']}")
    return "\n".join(lines)


def format_pareto(front: Sequence[Dict[str, Any]], sym: str = "¥") -> str:
    if not front:
        return "  (无)"
    lines = ["  价格 ← 越左越省   |   热量 ← 越上越低"]
    for p in front:
        names = "、".join(it["name"] for it in p["items"])
        lines.append(f"  {sym}{p['total']:>6.2f}   {p['calories']:>5} kcal   {names}")
    if len(front) == 1:
        lines.append("  · 本次需求下最低价方案同时也是最低卡，没有取舍空间。")
    return "\n".join(lines)


def format_audit(rows: Sequence[Dict[str, Any]], sym: str = "¥") -> str:
    if not rows:
        return "  (无券可体检)"
    mark = {"worth": "✅", "marginal": "🟡", "suboptimal": "🔸",
            "trap": "🪤", "useless": "⚪", "invalid": "⛔", "conditional": "🔒"}
    lines: List[str] = []
    for r in rows:
        m = mark.get(r["verdict"], "·")
        nominal = f"{sym}{r['nominal']:.2f}" if r["nominal"] else "—"
        real = f"{sym}{r['real_saving']:.2f}" if r["real_saving"] is not None else "—"
        rate = f"{r['effective_rate']*100:.0f}%" if r["effective_rate"] is not None else "—"
        lines.append(f"  {m} {r['title']}")
        lines.append(f"      名义 {nominal} / 实际省 {real} / 兑现率 {rate}"
                     + ("  ★在最优解里" if r["in_optimal_plan"] else ""))
        lines.append(f"      {r['reason']}")
    return "\n".join(lines)


def format_report(result: Dict[str, Any], request: Dict[str, Any]) -> str:
    sym = _sym(result)
    out: List[str] = []
    out.append("=" * 64)
    out.append("  麦门省钱精算师 · 优化结果")
    out.append("=" * 64)
    base = result["baseline"]
    mode = result.get("dining_mode", "pickup")
    mode_cn = {"pickup": "到店自取", "delivery": "外送"}.get(mode, mode)
    party = result.get("party_size", 1)
    tail = f"   {party} 人拼单" if party > 1 else ""
    out.append(f"门店: {result.get('store_code','')}   场景: {mode_cn}{tail}   "
               f"目标: {result.get('objective','cheapest')}")
    out.append(f"想吃的: {'、'.join(base['items']) or '(未指定)'}")
    out.append(f"不使用优惠券的话, 直选约 {sym}{base['total']:.2f}"
               f" / 约 {base.get('calories', 0)} kcal")
    if result.get("objective_reason"):
        out.append(f"选型说明: {result['objective_reason']}")
    out.append("-" * 64)
    if not result["plans"]:
        out.append("在给定预算/热量约束下没有可行方案, 建议放宽条件。")
        return "\n".join(out)
    for i, p in enumerate(result["plans"], 1):
        medal = ["🥇", "🥈", "🥉", "④", "⑤"][i - 1] if i <= 5 else str(i)
        pp = f"   人均 {sym}{p['per_person']:.2f}" if p.get("party_size", 1) > 1 else ""
        out.append(f"{medal} 方案 {i}   到手价 {sym}{p['total']:.2f}{pp}   "
                   f"(原价 {sym}{p['raw_total']:.2f}, 省 {sym}{p['coupon_saving']:.2f} / "
                   f"{p['saving_pct']*100:.0f}%)   约 {p['calories']} kcal")
        for ln in p["lines"]:
            tag = f"  [{ln['tag']}]" if ln.get("tag") else ""
            out.append(f"      · {ln['name']}  {sym}{ln['price']:.2f}{tag}")
        if p["threshold_discount"] > 0:
            out.append(f"      · 满减抵扣  -{sym}{p['threshold_discount']:.2f}")
        if p.get("delivery_fee", 0) > 0:
            out.append(f"      · 外送费    +{sym}{p['delivery_fee']:.2f}")
        if p["gifts"]:
            out.append("      🎁 赠品: " + "、".join(g["name"] for g in p["gifts"]))
        if p["coupons"]:
            out.append("      🎟️  用券: " + "、".join(c["title"] for c in p["coupons"]))
        for n in p["notes"]:
            out.append(f"      ℹ️  {n}")
        out.append("")

    if result.get("gap") and (result["gap"].get("threshold") or result["gap"].get("gift")):
        out.append("-" * 64)
        out.append("💡 凑单建议 (针对方案 1)")
        out.append(format_gap(result["gap"]))
        out.append("")

    if result.get("pareto"):
        out.append("-" * 64)
        out.append("📈 帕累托前沿 (省钱的极限在哪)")
        out.append(format_pareto(result["pareto"], sym))
        out.append("")

    out.append("-" * 64)
    out.append(f"共评估 {result['plan_pool_size']} 个可行方案, 展示前 {len(result['plans'])} 名。")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 命令行入口
# --------------------------------------------------------------------------- #
def main(argv: List[str]) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    args = argv[1:]

    base: Optional[str] = None
    flags: Dict[str, Any] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                flags[k] = v
                i += 1
            elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                i += 2
            else:
                flags[key] = True
                i += 1
        else:
            if base is None:
                base = a
            i += 1

    if base is None:
        base = os.path.join(here, "sample_data")  # 默认样例, 支持从任意目录运行

    menu = load_json(os.path.join(base, "menu.json"))
    coupons = load_json(os.path.join(base, "coupons.json"))
    request = load_json(os.path.join(base, "request.json"))

    # 命令行覆盖
    if "mode" in flags:
        request["dining_mode"] = flags["mode"]
    if "fee" in flags:
        request["delivery_fee"] = float(flags["fee"])
    if "budget" in flags:
        request["budget"] = float(flags["budget"])
    if "max-calories" in flags:
        request["max_calories"] = int(flags["max-calories"])
    if "objective" in flags:
        request["objective"] = flags["objective"]
    if "party" in flags:
        request["party_size"] = int(flags["party"])

    result = optimize(menu, coupons, request)
    print(format_report(result, request))

    if flags.get("audit"):
        print("-" * 64)
        print("🩺 券体检 (名义面额 vs 真实节省)")
        print(format_audit(audit_coupons(menu, coupons, request), _sym(result)))
        print()

    if flags.get("json"):
        payload = dict(result)
        if flags.get("audit"):
            payload["coupon_audit"] = audit_coupons(menu, coupons, request)
        print("\n--- JSON ---")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
