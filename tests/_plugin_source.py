# -*- coding: utf-8 -*-
"""测试辅助：读拆分后的全部插件模块源码。"""
import os

MODULES = ("main.py", "amo_shared.py", "amo_observability.py", "amo_db.py",
           "amo_human_state.py", "amo_timeline.py",
           "amo_state_common.py", "amo_state_schedule.py", "amo_state_todo.py", "amo_state_jit.py",
           "amo_state_diary.py", "amo_state_date.py",
           "amo_llm_service.py", "amo_memory_data.py",
           "amo_memory_legacy.py", "amo_memory_bridge.py", "amo_memory_context.py", "amo_api.py",
           "amo_proactive.py", "amo_schedule.py", "amo_llm.py", "amo_memory.py",
           "amo_commands.py", "amo_config.py", "amo_message.py", "amo_social.py",
           "amo_travel.py", "amo_runtime.py", "amo_kb.py", "amo_usage.py",
           "amo_route_table.py", "amo_api_diary.py", "amo_api_schedule.py",
           "amo_api_wardrobe.py", "amo_api_photo.py", "amo_voice.py",
           "schedule_holiday.py", "schedule_segments.py",
           "schedule_scenic.py", "schedule_day_segments.py", "schedule_rest_segments.py",
           "schedule_swim.py",
           "draw_engine.py", "photo_engine.py", "photo_view.py",
           "amo_group_guard.py", "amo_state_snapshot.py", "micro_events.py",
           "amo_state_context.py", "amo_date_context.py", "amo_diary_guard.py",
           "amo_fact_pool.py", "amo_schedule_health.py",
           # 2026-11-17 structure-b 拆分：amo_shared 按时间/文本/配置/用量/依赖兜底拆出，
           # amo_runtime 按 tick/context/reply 拆出；源码扫描测试要把子模块也算进来。
           "amo_shared_deps.py", "amo_shared_time.py", "amo_shared_text.py",
           "amo_shared_config.py", "amo_shared_usage.py",
           "amo_runtime_context.py", "amo_runtime_reply.py", "amo_runtime_tick.py",
           "amo_handshake.py", "amo_init.py", "amo_lifecycle.py", "amo_readiness.py",
           "domains/schedule/health.py", "domains/reply/brushoff.py",
           "domains/photo/view.py", "domains/diary/lines.py",
           "domains/reply/refusal.py", "domains/schedule/segments.py",
           "domains/schedule/scenic.py", "domains/schedule/swim.py",
           "domains/schedule/day_segments.py", "domains/schedule/rest_segments.py",
           "domains/schedule/day_types.py",
           "domains/schedule/subject.py", "domains/reply/repetition_guard.py",
           "domains/photo/room_ab.py", "domains/diary/engine.py",
           "domains/reply/status_text.py", "schedule_banks.py",
           # 2026-11-20 居住关系/人物指代
           "amo_household.py", "amo_person_ref.py")


def module_paths(root):
    return [os.path.join(root, name) for name in MODULES
            if os.path.exists(os.path.join(root, name))]


def all_source(root):
    return "\n\n".join(open(path, encoding="utf-8").read() for path in module_paths(root))
