# -*- coding: utf-8 -*-
"""跨 mixin 的公共接口（只服务类型检查，运行时用 object 顶替，不参与 MRO）。

13 个 mixin 由 HumanCorePlugin 组合成插件，方法之间大量互相调用（`self.db`、
`self._ensure_ready()` …）。单独看某个 mixin，这些成员并不在它自己的类上，mypy 会
报 attr-defined。这里用 Protocol 把"组合后的接口"声明出来，每个 mixin：

    if TYPE_CHECKING:
        from .amo_types import PluginLike as _MixinBase
    else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
        _MixinBase = object

    class CommandMixin(_MixinBase):

方法签名由 tools/gen_types.py 从真实实现抓取，改了实现要同步这里（CI 的 mypy 会检查）。
"""
from __future__ import annotations

from typing import Any, Protocol


class PluginLike(Protocol):
    """HumanCorePlugin 组合完成后的公共接口。"""

    # ---- 组合后的共享状态 ----
    db: Any
    state: Any
    llm: Any
    gift: Any
    draw: Any
    photo: Any
    heartflow: Any
    segment_engine: Any
    _ready: bool
    _last_reply_at: dict[str, float]

    # ---- 跨 mixin 调用的方法（签名与真实实现一致）----
    def _core_decor_flags(self, umo=""):
        ...
    async def _ensure_ready(self):
        ...
    def _spawn(self, coro):
        ...
    def _diary_lock(self, day, start):
        ...
    def _cancel_diary_tasks(self, day, from_hour):
        ...
    async def _diary_scheduler_tick(self):
        ...
    def _canonical_uid(self, event):
        ...
    def cfg_str(self, key, default=""):
        ...
    def cfg_bool(self, key, default=False):
        ...
    def cfg_list(self, key, default=None):
        ...
    async def _apply_user_schedule_change(self, event, user_text):
        ...
    def _schedule_change_allowed(self, event):
        ...
    def _remember_sent_reply(self, event, text):
        ...
    def _trim_reply(self, text):
        ...
    def _tone_with_continuity(self, umo, tone):
        ...
    async def _send_now(self, event, text, quote_id=None):
        ...
    def _quote_allowed(self, umo, group=None):
        ...
    def _looks_like_command(self, text):
        ...
    def _is_talking_to_me(self, event):
        ...
    def _is_admin(self, event):
        ...
    async def _handle_message(self, event):
        ...
    def _recent(self, *args, **kwargs):
        ...
    def _maybe_own_offwork_sync(self, *args, **kwargs):
        ...
    def _strip_repeated_food_question(self, *args, **kwargs):
        ...
    def _guard_self_location(self, text, place):
        ...
    def _external_memory_write(self, uid, content, kind="fact", tier=1,
                               scope="private", group_id="", subject="user", platform=""):
        ...
    async def _all_memories(self, uid, keyword="", limit=50):
        ...
    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    scope="private", group_id="", subject="user", platform=""):
        ...
    async def _data_memory_write(self, uid, content, kind="fact", tier=1,
                                 scope="private", group_id="", subject="user", platform=""):
        ...
    async def _data_mem_list(self, uid, keyword="", limit=50):
        ...
    def _data_connected(self):
        ...
    def _data_bot_id(self):
        ...
    def _normalize_memories(self, rows):
        ...
    def _plugin_by_names(self, names):
        ...
    async def _dynamic_context(self, event):
        ...

    # ---- 跨 mixin 成员（严格模式 --check-untyped-defs 补齐）----
    CONTEXT_BUFFER_LIMIT: Any
    DATA_PLUGIN_NAMES: Any
    FOLLOWUP_TEMPLATES: Any
    LEAN_PARTS: Any
    RITUAL_SPECS: Any
    SENSITIVE_KEYS: Any
    _apply_disturbance: Any
    _apply_runtime_config: Any
    _auto_learn: Any
    _balance_cached: Any
    _body: Any
    _bot_initiated: Any
    _check_proactive_pending: Any
    _city_lookup: Any
    _city_module: Any
    _clear_proactive_pending: Any
    _coerce_config_value: Any
    _compose_reply: Any
    _config_schema_items: Any
    _cross_platform_context: Any
    _ctx_remember: Any
    _data_call: Any
    _data_mem: Any
    _data_memory_recall: Any
    _data_plugin: Any
    _data_store: Any
    _data_sync_lock: Any
    _data_synced: Any
    _decay_hate_relations: Any
    _depressed_active: Any
    _detect_impersonation: Any
    _dialect_active: Any
    _dialect_kb_context: Any
    _dialect_kb_name: Any
    _dialect_region: Any
    _dialect_rows: Any
    _dialect_style: Any
    _diary_dir: Any
    _disturb_active: Any
    _disturb_cooldown: Any
    _disturb_last: Any
    _disturb_reset_at: Any
    _disturb_until: Any
    _ensure_dialect_kb: Any
    _ensure_scenic_kb: Any
    _event_message_id: Any
    _first_private_umo: Any
    _forced_sent_this_hour: Any
    _generate_daily_diary: Any
    _group_reply_guide: Any
    _group_roster_text: Any
    _group_speaker_count: Any
    _group_times: Any
    _hydrate_recent: Any
    _is_registered_lover: Any
    _judge_provider_id: Any
    _last_affect: Any
    _last_bot_saved: Any
    _last_ritual: Any
    _last_umo: Any
    _learn: Any
    _lover_aliases: Any
    _mark_bot_initiated: Any
    _mark_forced_hour: Any
    _mask_secret: Any
    _maybe_daily_diary: Any
    _maybe_follow_city: Any
    _maybe_proactive: Any
    _maybe_rituals: Any
    _maybe_solo_return: Any
    _maybe_wander: Any
    _maybe_weather_alert: Any
    _memory_hub: Any
    _note_data_missing: Any
    _pending_key: Any
    _persona_name_cache: Any
    _pick_proactive_umo: Any
    _platform_label: Any
    _platform_of: Any
    _private_buffers: Any
    _provider_balances_cached: Any
    _provider_inventory: Any
    _purge_profile_kv: Any
    _push_profile_to_data: Any
    _push_relation_to_data: Any
    _q: Any
    _q_int: Any
    _recall: Any
    _recent_bot_initiated: Any
    _recent_by_uid: Any
    _record_channel: Any
    _register_lover: Any
    _relation_network_text: Any
    _reload_config_if_changed: Any
    _remember_bot_message: Any
    _restore_lover_relation: Any
    _retry_web_api_routes: Any
    _ritual_umos: Any
    _running: Any
    _safe_register: Any
    _scenic_kb_context: Any
    _scenic_kb_name: Any
    _schedule_delayed_reply: Any
    _schema_map: Any
    _sediment_relations: Any
    _segment: Any
    _self_identity: Any
    _send_to_umo: Any
    _set_depressed: Any
    _set_proactive_pending: Any
    _short_place: Any
    _should_disturb: Any
    _status_data: Any
    _strip_place_marks: Any
    _switch_weather_city: Any
    _sync_persona_name: Any
    _sync_profiles_to_data: Any
    _sync_relations_from_data: Any
    _sync_weather_city_config: Any
    _sync_with_data_plugin: Any
    _time_recall_context: Any
    _try_soothe: Any
    _usage_db_path: Any
    _usage_prices: Any
    _usage_start_of_today: Any
    _using_provider_id: Any
    _weather_query_context: Any
    admin_uids: Any
    bot_aliases: Any
    bot_name: Any
    cfg: Any
    cfg_float: Any
    cfg_int: Any
    config: Any
    context: Any
    gate: Any
    persona_name: Any
    plugin_dir: Any
    scenic_spots: Any
    wardrobe: Any

    _data_found_logged: Any
    _scenic_cache: Any
    _config_mtime: Any
    _last_relation_decay: Any
    _last_relation_sediment: Any
    _last_purge: Any
    _last_auto_learn: Any
    _last_pending_check: Any
    _last_schedule_save: Any
    _proactive_last: Any
