# -*- coding: utf-8 -*-
"""照片/画图域（pilot）：只定义 PhotoPlan 契约，不 import 插件模块。

PhotoPlan（photo_plan.py）是「这一张图怎么拍」的唯一决策结果：场景 / 姿势 / 守卫 /
参考图策略 / 场景锁。契约测试保证字段不缩水（消费方按字段读，删字段会先在这里红）。
"""
from __future__ import annotations

#: PhotoPlan 的全部字段（顺序与 dataclass 一致；字段名变更必须同步契约）
PLAN_FIELDS = (
    "scene_key", "base_zone", "scene", "pose_rule", "pose_line", "guard",
    "props", "banned", "allow_library_pose", "source", "reason", "activity",
    "explicit_place", "explicit_conflict", "repairs", "pose", "pose_ready",
    "where", "light", "refs_policy", "locks", "debug",
)
#: 消费方（提示词/参考图/面板）可以依赖的最小字段集
PLAN_REQUIRED_FIELDS = ("scene_key", "scene", "pose_rule", "guard", "source")
#: 决策入口（photo_plan 模块级函数）
PLAN_ENTRY_POINTS = ("resolve_plan", "locks_for_plan")
