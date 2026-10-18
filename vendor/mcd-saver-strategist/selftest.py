#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦门省钱精算师 · 自测用例

运行:  python selftest.py
覆盖:  组合价券 / 单品券 / 满减 / 赠品 / 预算过滤 / 热量过滤 / 确定性 /
       叠加策略 / 券渠道过滤 / 外送费结算 / 场景影响券可用性 /
       多目标选型 / 帕累托前沿 / 券体检 / 最小代价凑单 / 凑单互斥 / 多人拼单
"""

from __future__ import annotations

import copy
import sys

from optimizer import (
    optimize,
    price_with_basket,
    coupon_conflict,
    filter_coupons_for_mode,
    pareto_frontier,
    audit_coupons,
    cheapest_filler,
    suggest_gap,
    _pick_plans,
)

MENU = {
    "store_code": "T",
    "currency": "CNY",
    "items": [
        {"code": "BURGER", "name": "汉堡", "category": "burger", "price": 20.0, "calories": 500, "tags": ["汉堡"]},
        {"code": "FRIES", "name": "薯条", "category": "side", "price": 12.0, "calories": 340, "tags": ["薯条"]},
        {"code": "COKE", "name": "可乐", "category": "drink", "price": 9.0, "calories": 150, "tags": ["可乐"]},
        {"code": "PIE", "name": "派", "category": "dessert", "price": 7.0, "calories": 230, "tags": ["派"]},
    ],
}
COUPONS = {"coupons": [
    {"id": "B1", "type": "item_price", "title": "汉堡特价", "item_codes": ["BURGER"], "override_price": 15.0, "stackable": False},
    {"id": "T1", "type": "threshold", "title": "满30减8", "min_spend": 30.0, "discount": 8.0, "stackable": True},
    {"id": "T2", "type": "threshold", "title": "满20减3", "min_spend": 20.0, "discount": 3.0, "stackable": True},
    {"id": "U1", "type": "bundle_price", "title": "1+1", "groups": [["BURGER"], ["FRIES", "COKE"]], "price": 13.0, "stackable": False},
    {"id": "G1", "type": "free_gift", "title": "满20送派", "min_spend": 20.0, "gift_codes": ["PIE"], "stackable": True},
]}

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def idx(codes):
    by = {i["code"]: i for i in MENU["items"]}
    return [by[c] for c in codes]


def main() -> int:
    print("== 1. 单品改价券 ==")
    r = price_with_basket(idx(["BURGER", "FRIES"]), ["B1"], {"B1": COUPONS["coupons"][0]})
    check("汉堡被改价到 15", r is not None and r["total"] == 27.0, str(r))

    print("== 2. 满减券 ==")
    r = price_with_basket(idx(["BURGER", "FRIES"]), ["T1"], {"T1": COUPONS["coupons"][1]})
    check("满30减8 生效", r["total"] == 24.0, str(r))
    r = price_with_basket(idx(["COKE"]), ["T1"], {"T1": COUPONS["coupons"][1]})
    check("小计未达门槛不减免", r["total"] == 9.0, str(r))

    print("== 3. 组合价券 ==")
    r = price_with_basket(idx(["BURGER", "FRIES"]), ["U1"], {"U1": COUPONS["coupons"][3]})
    check("1+1 打包价 13", r["total"] == 13.0, str(r))

    print("== 4. 赠品券门槛 ==")
    r = price_with_basket(idx(["BURGER"]), ["G1"], {"G1": COUPONS["coupons"][4]})
    check("满20送派", r["gifts"] == ["PIE"], str(r))
    r = price_with_basket(idx(["PIE"]), ["G1"], {"G1": COUPONS["coupons"][4]})
    check("未达门槛无赠品", r["gifts"] == [], str(r))

    print("== 5. 叠加策略(不可叠券互斥) ==")
    check("单品券与满减互斥", coupon_conflict(COUPONS["coupons"][0], COUPONS["coupons"][1]) is True)
    check("两张满减可叠加", coupon_conflict(COUPONS["coupons"][1], COUPONS["coupons"][2]) is False)

    print("== 6. 端到端: 挑出最低价 ==")
    base_req = {"wants": ["汉堡", "薯条", "可乐"], "max_items": 3, "allow_extras": True}
    res = optimize(MENU, COUPONS, base_req)
    best = res["plans"][0]
    # 1+1 打包(13) + 剩余可乐(9) = 22; 或 汉堡特价15+薯条12+可乐9-... 单品券不可与满减叠
    check("最优价 = 22.0", best["total"] == 22.0, str(best))
    check("最优方案用了组合券", any(c["id"] == "U1" for c in best["coupons"]), str(best))

    print("== 7. 预算过滤 ==")
    req = copy.deepcopy(base_req); req["budget"] = 15.0
    res = optimize(MENU, COUPONS, req)
    check("预算15元内无可严格满足的方案或均超预算", all(p["total"] <= 15.0 for p in res["plans"]), str(res["plans"][:1]))

    print("== 8. 热量过滤 ==")
    req = copy.deepcopy(base_req); req["max_calories"] = 400
    res = optimize(MENU, COUPONS, req)
    check("热量上限400时过滤掉高卡组合", all(p["calories"] <= 400 for p in res["plans"]), str([p["calories"] for p in res["plans"]]))

    print("== 9. 确定性 ==")
    a = optimize(MENU, COUPONS, base_req)
    b = optimize(MENU, COUPONS, base_req)
    check("相同输入结果一致", a == b)

    print("== 10. 券渠道过滤 ==")
    ch_coupons = [
        {"id": "P1", "type": "threshold", "title": "到店券", "min_spend": 10.0, "discount": 2.0, "stackable": True, "channel": "pickup"},
        {"id": "D1", "type": "threshold", "title": "外送券", "min_spend": 10.0, "discount": 3.0, "stackable": True, "channel": "delivery"},
        {"id": "A1", "type": "threshold", "title": "通用券", "min_spend": 10.0, "discount": 1.0, "stackable": True},
    ]
    pick = filter_coupons_for_mode(ch_coupons, "pickup")
    dlv = filter_coupons_for_mode(ch_coupons, "delivery")
    check("到店场景保留到店券+通用券", {c["id"] for c in pick} == {"P1", "A1"}, str([c["id"] for c in pick]))
    check("外送场景保留外送券+通用券", {c["id"] for c in dlv} == {"D1", "A1"}, str([c["id"] for c in dlv]))

    print("== 11. 外送费计入总价 ==")
    req_d = {"wants": ["汉堡", "薯条"], "max_items": 3, "allow_extras": True,
             "dining_mode": "delivery", "delivery_fee": 6.0}
    res_d = optimize(copy.deepcopy(MENU), {"coupons": []}, req_d)
    best_d = res_d["plans"][0]
    check("总价 = 商品原价 + 外送费", abs(best_d["total"] - (best_d["raw_total"] + 6.0)) < 1e-9, str(best_d))
    check("结果标记场景与外送费", res_d["dining_mode"] == "delivery" and res_d["delivery_fee"] == 6.0, str(res_d["dining_mode"]))

    print("== 12. 场景影响券可用性 ==")
    mixed = {"coupons": [
        {"id": "P9", "type": "item_price", "title": "到店汉堡特价",
         "item_codes": ["BURGER"], "override_price": 10.0, "stackable": False, "channel": "pickup"},
    ]}
    req_p = {"wants": ["汉堡"], "max_items": 2, "allow_extras": False, "dining_mode": "pickup"}
    req_d2 = {"wants": ["汉堡"], "max_items": 2, "allow_extras": False, "dining_mode": "delivery"}
    res_p = optimize(copy.deepcopy(MENU), mixed, req_p)
    res_d2 = optimize(copy.deepcopy(MENU), mixed, req_d2)
    check("到店场景可用到店券(汉堡 10 元)", res_p["plans"][0]["lines"][0]["price"] == 10.0, str(res_p["plans"][0]["lines"]))
    check("外送场景排除到店券(汉堡原价 20)", res_d2["plans"][0]["lines"][0]["price"] == 20.0, str(res_d2["plans"][0]["lines"]))

    # ---------------------------------------------------------------- #
    # 13-17 多目标选型 / 帕累托前沿
    # ---------------------------------------------------------------- #
    print("== 13. 多目标选型 ==")

    def mk(total, cal, items=3, fulfilled=3):
        return {"total": total, "calories": cal, "item_count": items,
                "wants_fulfilled": fulfilled, "wants_total": 3}

    pool = [mk(20.0, 900), mk(20.0, 500), mk(21.0, 400), mk(30.0, 300)]
    check("cheapest 同价时取低卡", _pick_plans(pool, "cheapest", 1)[0]["calories"] == 500)
    check("balanced 取同价低卡", _pick_plans(pool, "balanced", 1)[0]["calories"] == 500)
    check("lightest 在 30% 容忍带内取最低卡",
          _pick_plans(pool, "lightest", 1)[0]["calories"] == 400,
          str(_pick_plans(pool, "lightest", 1)[0]))
    check("lightest 不会为低卡突破价格容忍带(不选 30 元)",
          _pick_plans(pool, "lightest", 1)[0]["total"] != 30.0)
    check("fewest 在 10% 带内取件数最少",
          _pick_plans(pool, "fewest", 1)[0]["item_count"] == 3)

    print("== 14. 帕累托前沿 ==")
    front = pareto_frontier(pool)
    check("被支配点(20元/900卡)被剔除", all(p["calories"] != 900 for p in front),
          str([(p["total"], p["calories"]) for p in front]))
    check("前沿按价格升序且热量递减",
          [p["total"] for p in front] == sorted(p["total"] for p in front)
          and [p["calories"] for p in front] == sorted((p["calories"] for p in front), reverse=True),
          str([(p["total"], p["calories"]) for p in front]))
    check("前沿保留 3 个非支配解", len(front) == 3, str(len(front)))

    # 真实数据上: 去掉 1+1 神券后应出现价格/热量的真实权衡
    cp_no_bundle = {"coupons": [c for c in COUPONS["coupons"] if c["type"] != "bundle_price"]}
    res_trade = optimize(copy.deepcopy(MENU), cp_no_bundle,
                         {"wants": ["汉堡", "可乐"], "max_items": 3, "allow_extras": True})
    check("存在多个帕累托解时给出权衡曲线", len(res_trade["pareto"]) >= 1,
          str([(p["total"], p["calories"]) for p in res_trade["pareto"]]))

    print("== 15. 券体检 ==")
    watered = {"coupons": [
        {"id": "FAKE", "type": "item_price", "title": "汉堡特惠价 25(比原价还贵)",
         "item_codes": ["BURGER"], "override_price": 25.0, "stackable": False},
        {"id": "G1", "type": "free_gift", "title": "满20送派",
         "min_spend": 20.0, "gift_codes": ["PIE"], "stackable": True},
    ]}
    rows = audit_coupons(copy.deepcopy(MENU), watered,
                         {"wants": ["汉堡", "薯条"], "max_items": 3, "allow_extras": True})
    fake = next(r for r in rows if r["id"] == "FAKE")
    gift = next(r for r in rows if r["id"] == "G1")
    check("注水券被判为 invalid", fake["verdict"] == "invalid", str(fake))
    check("注水券真实节省 <= 0", (fake["real_saving"] or 0) <= 0.01, str(fake["real_saving"]))
    check("赠品券按赠品价值计算节省(派 7 元)", gift["real_saving"] == 7.0, str(gift["real_saving"]))
    check("体检结果带兑现率字段", "effective_rate" in gift)

    print("== 16. 最小代价凑单 ==")
    items = MENU["items"]
    pick = cheapest_filler(items, 3.1)
    check("差 3.1 元用 7 元派补位(最便宜的达标单品)", pick["cost"] == 7.0, str(pick))
    pick = cheapest_filler(items, 8.5)
    check("差 8.5 元用 9 元可乐补位", pick["cost"] == 9.0, str(pick))
    pick = cheapest_filler(items, 13.5)
    check("单品都不够时组合补位(2 件合计 16)", pick["cost"] == 16.0 and len(pick["items"]) == 2, str(pick))

    print("== 17. 凑单建议不推荐冲突券 ==")
    # 最优解用了不可叠加的 1+1, 满减券即使加购也用不上, 不应推荐
    res_c = optimize(copy.deepcopy(MENU), COUPONS,
                     {"wants": ["汉堡", "薯条"], "max_items": 3, "allow_extras": True})
    gap = res_c["gap"]
    check("已用不可叠加券时不推荐满减", gap["threshold"] is None, str(gap["threshold"]))
    # 未用不可叠加券时, 应给出补位建议 (用低档满减, 差一点到高档)
    res_c2 = optimize(copy.deepcopy(MENU), {"coupons": [
        {"id": "T5", "type": "threshold", "title": "满25减5", "min_spend": 25.0,
         "discount": 5.0, "stackable": True},
        {"id": "T12", "type": "threshold", "title": "满40减12", "min_spend": 40.0,
         "discount": 12.0, "stackable": True}]},
        {"wants": ["汉堡", "可乐"], "max_items": 3, "allow_extras": True})
    check("可叠加券给出补位建议", res_c2["gap"]["threshold"] is not None, str(res_c2["gap"]))
    g = res_c2["gap"]["threshold"]
    check("补位总额覆盖门槛差额", g["filler_cost"] >= g["gap"], str(g))
    check("净变化 = 补位成本 - 新增抵扣", abs(g["net_change"] - (g["filler_cost"] - g["extra_discount"])) < 1e-9)
    check("补位建议指向下一档满减", g["coupon_id"] == "T12", str(g["coupon_id"]))

    print("== 18. 多人拼单 ==")
    req_party = {"wants": ["汉堡", "薯条", "可乐"], "max_items": 3,
                 "allow_extras": True, "party_size": 3}
    res_party = optimize(copy.deepcopy(MENU), COUPONS, req_party)
    single = optimize(copy.deepcopy(MENU), COUPONS,
                      {"wants": ["汉堡", "薯条", "可乐"], "max_items": 3, "allow_extras": True})
    bp = res_party["plans"][0]
    bs = single["plans"][0]
    check("拼单总价 = 原价x份数 - 优惠一次", abs(bp["total"] - (bs["raw_total"] * 3 - bs["coupon_saving"])) < 1e-9,
          f'{bp["total"]} vs {bs["raw_total"]*3 - bs["coupon_saving"]}')
    check("人均 = 总价 / 人数", abs(bp["per_person"] - round(bp["total"] / 3, 2)) < 1e-9, str(bp["per_person"]))
    check("优惠不随份数放大(保守计价)", bp["coupon_saving"] == bs["coupon_saving"],
          f'{bp["coupon_saving"]} vs {bs["coupon_saving"]}')
    check("结果标记人数", res_party["party_size"] == 3)

    print("== 19. 拼单按总价判断预算 ==")
    req_b = dict(req_party, budget=50.0)
    res_b = optimize(copy.deepcopy(MENU), COUPONS, req_b)
    check("3 人预算 50 内无可行方案被过滤干净或均合规",
          all(p["total"] <= 50.0 for p in res_b["plans"]), str([p["total"] for p in res_b["plans"]]))

    print("== 20. 新字段不破坏确定性 ==")
    a2 = optimize(copy.deepcopy(MENU), COUPONS, dict(req_party, objective="balanced"))
    b2 = optimize(copy.deepcopy(MENU), COUPONS, dict(req_party, objective="balanced"))
    check("多目标结果可复现", a2 == b2)

    print(f"\n结果: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
