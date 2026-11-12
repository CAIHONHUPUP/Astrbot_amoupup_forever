# -*- coding: utf-8 -*-
"""测试辅助：读拆分后的全部插件模块源码。"""
import os

MODULES = ("main.py", "amo_shared.py", "amo_observability.py", "amo_db.py",
           "amo_human_state.py", "amo_llm_service.py", "amo_memory_data.py",
           "amo_memory_legacy.py", "amo_memory_bridge.py", "amo_memory_context.py", "amo_api.py",
           "amo_proactive.py", "amo_schedule.py", "amo_llm.py", "amo_memory.py",
           "amo_commands.py", "amo_config.py", "amo_message.py", "amo_social.py",
           "amo_travel.py", "amo_runtime.py", "amo_kb.py", "amo_usage.py",
           "draw_engine.py", "photo_engine.py", "photo_view.py")


def module_paths(root):
    return [os.path.join(root, name) for name in MODULES
            if os.path.exists(os.path.join(root, name))]


def all_source(root):
    return "\n\n".join(open(path, encoding="utf-8").read() for path in module_paths(root))
