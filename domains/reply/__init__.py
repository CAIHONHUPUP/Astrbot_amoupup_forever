# -*- coding: utf-8 -*-
"""回复域（pilot）：只定义「回复流水线」契约，不 import 插件模块。

回复守卫是一条条 Stage（id / phase / priority / run / when / …），按 phase 排序执行。
契约测试保证 Stage 字段不缩水、生产入口还在，避免「改流水线顺手删字段」。
"""
from __future__ import annotations

#: Stage 必有的字段（amo_reply_pipeline.Stage 的 dataclass 字段子集）
STAGE_FIELDS = (
    "id", "phase", "priority", "run", "when", "replace", "side_effect",
    "wired", "target", "ctx_keys", "note", "swallow", "log_msg", "note_scope",
)
#: 生产调用点（amo_reply_pipeline 模块级函数名）
ENTRY_POINTS = ("apply_fact_guard", "apply_fact_guard_async", "apply_proactive_dedup", "apply_tone_guard")
