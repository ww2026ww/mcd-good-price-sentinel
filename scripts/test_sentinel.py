#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_sentinel.py —— 差异化层自测

覆盖：个人基准、漂移检测、四条件门禁（notify/silent 各分支）、端到端编排。
与 vendored 优化器自测(selftest.py)正交：它验证「算得对」，本文件验证「何时该打扰」。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from baseline import compute_baseline, default_state, register_alert, alert_signature
from drift import diff_snapshots
from opportunity import assess, _match_favorites
from daily_sentinel import run
import argparse

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


# --------------------------------------------------------------------------- #
# 测试夹具
# --------------------------------------------------------------------------- #
def make_menu():
    return {
        "store_code": "SZ0001", "currency": "CNY",
        "items": [
            {"code": "BIGMAC", "name": "巨无霸", "category": "burger", "price": 26.0, "calories": 550, "tags": ["beef"]},
            {"code": "FRIES_M", "name": "薯条(中)", "category": "side", "price": 12.0, "calories": 320, "tags": []},
            {"code": "COKE_M", "name": "可乐(中)", "category": "drink", "price": 10.0, "calories": 150, "tags": []},
            {"code": "SALAD", "name": "田园沙拉", "category": "salad", "price": 18.0, "calories": 120, "tags": ["light"]},
        ],
    }


def make_new_coupon():
    return {"coupons": [
        {"id": "C_NEW", "type": "item_price", "title": "巨无霸立减", "item_codes": ["BIGMAC"],
         "override_price": 16.0, "stackable": False}
    ]}


def make_state(prefs, with_baseline=True, last_snapshot=None):
    st = default_state(prefs)
    if with_baseline:
        st["baseline"] = compute_baseline(make_menu(), prefs)
    st["last_snapshot"] = last_snapshot
    return st


def make_snapshot(coupons, campaigns=None):
    return {
        "store_code": "SZ0001", "currency": "CNY", "dining_mode": "pickup",
        "menu": make_menu(),
        "coupons": coupons,
        "campaigns": campaigns or [],
    }


FAV = {"favorite_items": ["巨无霸", "薯条(中)", "可乐(中)"], "dining_mode": "pickup", "alert_threshold": 6.0}


# --------------------------------------------------------------------------- #
# 1) 个人基准
# --------------------------------------------------------------------------- #
def test_baseline():
    print("== 1. 个人基准 ==")
    b = compute_baseline(make_menu(), FAV)
    check("基准典型花费 > 0", b["typical_total"] > 0, str(b))
    check("基准含常点项", set(["巨无霸", "薯条(中)", "可乐(中)"]).issubset(set(b["items"])), str(b))
    check("个性化阈值在 [5,20]", 5.0 <= b["personal_threshold"] <= 20.0, str(b["personal_threshold"]))
    check("computed_from=preferences", b["computed_from"] == "preferences")


# --------------------------------------------------------------------------- #
# 2) 漂移检测
# --------------------------------------------------------------------------- #
def test_drift():
    print("== 2. 漂移检测 ==")
    menu = make_menu()
    prev = {"menu": menu, "coupons": {"coupons": []}, "campaigns": []}
    # 今日新增一张适用于常点项的券
    cur = {"menu": menu, "coupons": make_new_coupon(), "campaigns": []}
    drifts = diff_snapshots(prev, cur, favorites=["巨无霸", "薯条(中)", "可乐(中)"])
    kinds = [d["kind"] for d in drifts]
    check("检测到 new_coupon", "new_coupon" in kinds, str(kinds))
    personal = [d for d in drifts if d.get("personal")]
    check("new_coupon 标记为个人相关", any(d["kind"] == "new_coupon" and d["personal"] for d in personal))

    # 价格下跌
    menu2 = make_menu()
    for it in menu2["items"]:
        if it["code"] == "BIGMAC":
            it["price"] = 20.0
    drifts2 = diff_snapshots(prev, {"menu": menu2, "coupons": {"coupons": []}, "campaigns": []},
                             favorites=["巨无霸", "薯条(中)", "可乐(中)"])
    check("检测到 price_drop", any(d["kind"] == "price_drop" for d in drifts2), str(drifts2))

    # 无变动 -> 空漂移
    drifts3 = diff_snapshots(prev, prev, favorites=["巨无霸"])
    check("无变动则漂移为空", drifts3 == [])


# --------------------------------------------------------------------------- #
# 3) 机会门禁：notify
# --------------------------------------------------------------------------- #
def test_notify():
    print("== 3. 机会门禁: 应通知 ==")
    snap = make_snapshot(make_new_coupon())
    # last_snapshot 为空券 -> 今天出现新券（成因）
    st = make_state(FAV, last_snapshot={"menu": make_menu(), "coupons": {"coupons": []}, "campaigns": []})
    d = assess(snap, st, today="2026-10-18")
    check("action == notify", d["action"] == "notify", json.dumps(d, ensure_ascii=False)[:200])
    check("成因存在", d["cause"] is not None)
    check("个人差额 >= 阈值", (d["personal_gap"] or 0) >= d["personal_threshold"], str(d.get("personal_gap")))
    check("理由非空", bool(d["reason"]))
    check("门禁全绿", all([d["gates"]["gap_ok"], d["gates"]["preference_match"],
                          d["gates"]["not_duplicate"], d["gates"]["has_cause"]]))


# --------------------------------------------------------------------------- #
# 4) 机会门禁：silent 各分支
# --------------------------------------------------------------------------- #
def test_silent_no_drift():
    print("== 4. 机会门禁: 静默(无成因) ==")
    # 券一直在，今天与昨日完全相同 -> 没有个人相关变动 -> 即便便宜也是常态
    snap = make_snapshot(make_new_coupon())
    prev = {"menu": make_menu(), "coupons": make_new_coupon(), "campaigns": []}
    st = make_state(FAV, last_snapshot=prev)
    d = assess(snap, st, today="2026-10-18")
    check("action == silent", d["action"] == "silent", str(d.get("silence_reason")))
    check("静默成因=no_personal_drift", d["silence_reason"] == "no_personal_drift", d["silence_reason"])


def test_silent_low_gap():
    print("== 5. 机会门禁: 静默(差额不足) ==")
    snap = make_snapshot(make_new_coupon())
    st = make_state({**FAV, "alert_threshold": 15.0},
                    last_snapshot={"menu": make_menu(), "coupons": {"coupons": []}, "campaigns": []})
    d = assess(snap, st, today="2026-10-18")
    check("action == silent", d["action"] == "silent")
    check("静默成因=gap_below_threshold", d["silence_reason"] == "gap_below_threshold", d["silence_reason"])


def test_silent_mismatch():
    print("== 6. 机会门禁: 静默(偏好不符) ==")
    snap = make_snapshot(make_new_coupon())
    st = make_state({"favorite_items": ["不存在的餐品"], "dining_mode": "pickup", "alert_threshold": 6.0},
                    last_snapshot={"menu": make_menu(), "coupons": {"coupons": []}, "campaigns": []})
    d = assess(snap, st, today="2026-10-18")
    check("action == silent", d["action"] == "silent")
    check("静默成因=preference_mismatch", d["silence_reason"] == "preference_mismatch", d["silence_reason"])


def test_silent_duplicate():
    print("== 7. 机会门禁: 静默(近期重复) ==")
    snap = make_snapshot(make_new_coupon())
    st = make_state(FAV, last_snapshot={"menu": make_menu(), "coupons": {"coupons": []}, "campaigns": []})
    d1 = assess(snap, st, today="2026-10-18")
    # 把第一次的机会指纹写入去重窗口
    from opportunity import opportunity_for_state
    st = register_alert(st, opportunity_for_state(d1), "2026-10-18")
    d2 = assess(snap, st, today="2026-10-19")
    check("首次可通知", d1["action"] == "notify")
    check("重复则静默", d2["action"] == "silent")
    check("静默成因=recent_duplicate", d2["silence_reason"] == "recent_duplicate", d2["silence_reason"])


# --------------------------------------------------------------------------- #
# 8) 端到端编排
# --------------------------------------------------------------------------- #
def test_daily_sentinel_e2e(tmpdir):
    print("== 8. 端到端 daily_sentinel ==")
    snap_path = os.path.join(tmpdir, "snapshot.json")
    state_path = os.path.join(tmpdir, "state.json")
    out_path = os.path.join(tmpdir, "decision.json")
    prefs_path = os.path.join(tmpdir, "prefs.json")
    json.dump(make_snapshot(make_new_coupon()), open(snap_path, "w", encoding="utf-8"))
    json.dump(FAV, open(prefs_path, "w", encoding="utf-8"))
    # 预置 state：基线已建立，且「昨日快照」无券（今天出现的券即为成因）
    st = make_state(FAV, last_snapshot={"menu": make_menu(), "coupons": {"coupons": []}, "campaigns": []})
    json.dump(st, open(state_path, "w", encoding="utf-8"))
    args = argparse.Namespace(snapshot=snap_path, state=state_path, preferences=prefs_path,
                              order_history=None, price_check=None, out=out_path,
                              today="2026-10-18", save_state=True, demo=False, build_from_mcp=None)
    d = run(args)
    check("首跑产出 notify", d["action"] == "notify", d.get("silence_reason", ""))
    check("首跑后写入 state", os.path.exists(state_path))
    check("首跑后写入 decision", os.path.exists(out_path))
    # 次日同快照再跑 -> 同一机会已推送过 -> 静默去重
    args2 = argparse.Namespace(snapshot=snap_path, state=state_path, preferences=prefs_path,
                               order_history=None, price_check=None, out=out_path,
                               today="2026-10-19", save_state=True, demo=False, build_from_mcp=None)
    d2 = run(args2)
    check("次日同机会则静默", d2["action"] == "silent", d2.get("silence_reason", ""))


if __name__ == "__main__":
    import tempfile
    test_baseline()
    test_drift()
    test_notify()
    test_silent_no_drift()
    test_silent_low_gap()
    test_silent_mismatch()
    test_silent_duplicate()
    tmp = tempfile.mkdtemp(prefix="sentinel_test_")
    test_daily_sentinel_e2e(tmp)
    print(f"\n结果: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
