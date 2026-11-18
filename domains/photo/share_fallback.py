# -*- coding: utf-8 -*-
"""照片分享失败/安全重试的纯判定：只做布尔与文案选择，不碰 IO。

根目录 photo_share.py 的 _share 负责调用生成/写统计/发消息，判定集中在这里，
便于单测覆盖（域内只依赖标准库）。
"""
from __future__ import annotations


def retry_decision(first_item, selfie):
    """首张没出图且是自拍 → 用去身体化安全 prompt 重试一次。"""
    return bool(not first_item and selfie)


def policy_flag(policy_hit):
    """日志里统一用 "1"/"0" 表示是否命中内容政策。"""
    return "1" if policy_hit else "0"


def merged_failure_reason(first_reason, retry_reason=""):
    """重试仍失败时优先记重试的原因，没有就沿用首张的。"""
    return str(retry_reason or first_reason or "")


def retry_stat(retry_item):
    """重试后的统计键：成功/失败各一个。"""
    return "policy_fallback_ok" if retry_item else "policy_fallback_fail"
