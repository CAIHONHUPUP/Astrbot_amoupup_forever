# -*- coding: utf-8 -*-
"""穿搭衣柜：每日一套、面板增删、礼物自动入库。

数据落在 ``plugin_data/Astrbot_amoupup_forever/wardrobe.json``：

- ``items``   每件衣服，分三类：``top``（上衣（外套））/ ``bottom``（裤子）/ ``shoes``（鞋子）；
- ``outfits`` 搭配模板（至少 30 套），每套记 top / bottom / shoes 三个 item id；
- ``daily``   每天锁定的那一套，同一天所有自拍/照片都用它，换场景也不换。

另外会把当天这套写到生图插件的 ``plugin_data/Astrbot_amoupup_photo/amo_outfit.json``
（旧目录只读回退，legacy fallback，见 amo_compat.py），聊天里直接调 ``generate_selfie`` 时
也能锁住衣服（生图插件那边会把这份当「今日着装」）。
"""

# 门面说明（2026-11-17 拆分）：实现按职责拆到 wardrobe_data / wardrobe_seed、wardrobe_daily、wardrobe_queries，公开名字与调用路径不变。

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # 包模式（AstrBot 加载插件）
    from .wardrobe_data import (  # noqa: F401  旧模块级名字原样可用（显式列名，便于 pyflakes 静态检查）
        BOTTOM_WORDS, CATEGORY_KEYS, CATEGORY_LABELS, CATEGORY_NAMES, COAT_SEED_VERSION,
        COAT_WORDS, COLOR_FAMILIES, COOL_BOTTOM_WORDS, DAY_SEED_VERSION, DAY_TARGET_OUTFITS,
        DRESS_SEED_VERSION, DRESS_WORDS, HOME_BOTTOMS, HOME_EXTRA, HOME_PLACE_WORDS,
        HOME_SEED_VERSION, HOME_SHOES, HOME_TOPS, HOTEL_PLACE_WORDS, HOTEL_SEED,
        HOTEL_SEED_VERSION, MATERIAL_WORDS, MAX_OUTFITS, NEUTRAL_SHOE_WORDS,
        OUTFIT_CHANGE_WORDS, OUTFIT_CONTEXT_WORDS, OUTSIDE_PLACE_WORDS, QUANTIFIER_RE,
        SEED_COATS, SEED_OUTFITS, SEED_SLEEP, SEED_SOCKS, SEED_SWIM_SETS, SHOE_WORDS, SOCKS_SEED_VERSION,
        SOCKS_WORDS, SLEEP_BOTTOM_WORDS,
        SLEEP_EXTRA_ITEMS, SLEEP_HOUR_FROM, SLEEP_HOUR_TO, SLEEP_SEED_VERSION,
        SLEEP_SHOE_WORDS, SLEEP_TARGET_OUTFITS, SLEEP_TOP_WORDS, SUMMER_BAD_WORDS,
        SUMMER_DRESS_WORDS, SWIM_NEGATIVE_WORDS, SWIM_PLACE_WORDS, SWIM_SEED_VERSION,
        SWIM_SPOTS, SWIM_STRONG_WORDS, SWIM_WORDS, TOP_WORDS, TYPE_CHECKING, WARM_WORDS,
        WORK_SCENE_WORDS, _amo_clock, _clock_now, _combo_candidates, _day_item_pools,
        _item_ref_path, _new_id, _note_swallowed, _outfit_slots, _wardrobe_category_list,
        annotations, atomic_write_json, classify_item, classify_sleep_item, clean_name,
        color_family, combo_compatible, date, fabric_match, is_day_item, is_day_outfit,
        is_home_socks, is_long_socks, is_short_socks, is_tights, is_tights,
        is_dress, is_outfit_change_request, is_pajama_change_request, is_swim_item,
        is_swim_outfit, json, logger, material_set, os, random, re, shoe_matches, threading,
        time, timedelta, today_str, uuid,
    )
    from .wardrobe_seed import WardrobeSeedMixin
    from .wardrobe_daily import WardrobeDailyMixin
    from .wardrobe_queries import WardrobeQueriesMixin
    from . import wardrobe_seed as _mod_wardrobe_seed
    from . import wardrobe_daily as _mod_wardrobe_daily
    from . import wardrobe_queries as _mod_wardrobe_queries
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from wardrobe_data import (  # noqa: F401  直接按文件加载时
            BOTTOM_WORDS, CATEGORY_KEYS, CATEGORY_LABELS, CATEGORY_NAMES, COAT_SEED_VERSION,
            COAT_WORDS, COLOR_FAMILIES, COOL_BOTTOM_WORDS, DAY_SEED_VERSION, DAY_TARGET_OUTFITS,
            DRESS_SEED_VERSION, DRESS_WORDS, HOME_BOTTOMS, HOME_EXTRA, HOME_PLACE_WORDS,
            HOME_SEED_VERSION, HOME_SHOES, HOME_TOPS, HOTEL_PLACE_WORDS, HOTEL_SEED,
            HOTEL_SEED_VERSION, MATERIAL_WORDS, MAX_OUTFITS, NEUTRAL_SHOE_WORDS,
            OUTFIT_CHANGE_WORDS, OUTFIT_CONTEXT_WORDS, OUTSIDE_PLACE_WORDS, QUANTIFIER_RE,
            SEED_COATS, SEED_OUTFITS, SEED_SLEEP, SEED_SWIM_SETS, SHOE_WORDS, SLEEP_BOTTOM_WORDS,
            SLEEP_EXTRA_ITEMS, SLEEP_HOUR_FROM, SLEEP_HOUR_TO, SLEEP_SEED_VERSION,
            SLEEP_SHOE_WORDS, SLEEP_TARGET_OUTFITS, SLEEP_TOP_WORDS, SUMMER_BAD_WORDS,
            SUMMER_DRESS_WORDS, SWIM_NEGATIVE_WORDS, SWIM_PLACE_WORDS, SWIM_SEED_VERSION,
            SWIM_SPOTS, SWIM_STRONG_WORDS, SWIM_WORDS, TOP_WORDS, TYPE_CHECKING, WARM_WORDS,
            WORK_SCENE_WORDS, _amo_clock, _clock_now, _combo_candidates, _day_item_pools,
            _item_ref_path, _new_id, _note_swallowed, _outfit_slots, _wardrobe_category_list,
            annotations, atomic_write_json, classify_item, classify_sleep_item, clean_name,
            color_family, combo_compatible, date, fabric_match, is_day_item, is_day_outfit,
        is_home_socks, is_long_socks, is_short_socks, is_tights,
            is_dress, is_outfit_change_request, is_pajama_change_request, is_swim_item,
            is_swim_outfit, json, logger, material_set, os, random, re, shoe_matches, threading,
            time, timedelta, today_str, uuid,
        )
        from wardrobe_seed import WardrobeSeedMixin
        from wardrobe_daily import WardrobeDailyMixin
        from wardrobe_queries import WardrobeQueriesMixin
        import wardrobe_seed as _mod_wardrobe_seed
        import wardrobe_daily as _mod_wardrobe_daily
        import wardrobe_queries as _mod_wardrobe_queries


class Wardrobe(WardrobeSeedMixin, WardrobeDailyMixin, WardrobeQueriesMixin):


    VERSION = 1


# 拆分出去的方法里仍写着 Wardrobe.xxx(self, ...) 这类老写法；把类对象回填到子模块
# 的命名空间即可（子模块不 import 门面，依赖仍单向）。
for _split_mod in (_mod_wardrobe_seed, _mod_wardrobe_daily, _mod_wardrobe_queries):
    setattr(_split_mod, "Wardrobe", Wardrobe)

__all__ = [name for name in (
    'BOTTOM_WORDS',
    'CATEGORY_KEYS',
    'CATEGORY_LABELS',
    'CATEGORY_NAMES',
    'COAT_SEED_VERSION',
    'COAT_WORDS',
    'COLOR_FAMILIES',
    'COOL_BOTTOM_WORDS',
    'DAY_SEED_VERSION',
    'DAY_TARGET_OUTFITS',
    'DRESS_SEED_VERSION',
    'DRESS_WORDS',
    'HOME_BOTTOMS',
    'HOME_EXTRA',
    'HOME_PLACE_WORDS',
    'HOME_SEED_VERSION',
    'HOME_SHOES',
    'HOME_TOPS',
    'HOTEL_PLACE_WORDS',
    'HOTEL_SEED',
    'HOTEL_SEED_VERSION',
    'MATERIAL_WORDS',
    'MAX_OUTFITS',
    'NEUTRAL_SHOE_WORDS',
    'OUTFIT_CHANGE_WORDS',
    'OUTFIT_CONTEXT_WORDS',
    'OUTSIDE_PLACE_WORDS',
    'QUANTIFIER_RE',
    'SEED_COATS',
    'SEED_OUTFITS',
    'SEED_SLEEP',
    'SEED_SWIM_SETS',
    'SHOE_WORDS',
    'SLEEP_BOTTOM_WORDS',
    'SLEEP_EXTRA_ITEMS',
    'SLEEP_HOUR_FROM',
    'SLEEP_HOUR_TO',
    'SLEEP_SEED_VERSION',
    'SLEEP_SHOE_WORDS',
    'SLEEP_TARGET_OUTFITS',
    'SLEEP_TOP_WORDS',
    'SUMMER_BAD_WORDS',
    'SUMMER_DRESS_WORDS',
    'SWIM_NEGATIVE_WORDS',
    'SWIM_PLACE_WORDS',
    'SWIM_SEED_VERSION',
    'SWIM_SPOTS',
    'SWIM_STRONG_WORDS',
    'SWIM_WORDS',
    'TOP_WORDS',
    'TYPE_CHECKING',
    'WARM_WORDS',
    'WORK_SCENE_WORDS',
    'Wardrobe',
    '_amo_clock',
    '_clock_now',
    '_combo_candidates',
    '_day_item_pools',
    '_dt',
    '_item_ref_path',
    '_new_id',
    '_note_swallowed',
    '_outfit_slots',
    '_wardrobe_category_list',
    'annotations',
    'atomic_write_json',
    'classify_item',
    'classify_sleep_item',
    'clean_name',
    'color_family',
    'combo_compatible',
    'date',
    'fabric_match',
    'is_day_item',
    'is_day_outfit',
    'is_dress',
    'is_outfit_change_request',
    'is_pajama_change_request',
    'is_swim_item',
    'is_swim_outfit',
    'json',
    'logger',
    'material_set',
    'os',
    'random',
    're',
    'shoe_matches',
    'threading',
    'time',
    'timedelta',
    'today_str',
    'uuid',
    'wardrobe_daily.py',
    'wardrobe_queries.py',
    'wardrobe_seed.py',
) if name in globals()]
