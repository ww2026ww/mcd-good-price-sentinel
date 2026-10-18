#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享导入助手：把上游 vendored 优化器（hujjun/mcd-saver-strategist, MIT）挂到 sys.path 并导入。

本项目复用该优化器作为「算」的引擎（确定性组合优化 / 计价 / 帕累托），
本文件只负责把它的模块安全导入，不做任何算法改动。算法之上的
「个人基准 / 机会门禁 / 漂移检测 / 每日编排」由本项目脚本提供（见 baseline/drift/opportunity/daily_sentinel）。
"""
import os
import sys

_VENDOR_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "vendor", "mcd-saver-strategist")
)


def load_optimizer():
    """导入 vendored 优化器模块，返回 module 对象。"""
    if not os.path.isdir(_VENDOR_DIR):
        raise FileNotFoundError(
            f"未找到 vendored 优化器目录: {_VENDOR_DIR}\n"
            "请先执行: 拉取 hujjun/mcd-saver-strategist 的 optimizer.py/selftest.py/LICENSE 到 vendor/"
        )
    if _VENDOR_DIR not in sys.path:
        sys.path.insert(0, _VENDOR_DIR)
    import optimizer as mcd_optim  # vendored, MIT
    return mcd_optim


if __name__ == "__main__":
    m = load_optimizer()
    print("vendored optimizer loaded:", getattr(m, "__name__", m))
    print("has optimize():", hasattr(m, "optimize"))
