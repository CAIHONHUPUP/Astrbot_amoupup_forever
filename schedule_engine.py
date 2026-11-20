# -*- coding: utf-8 -*-
"""日程主干生成引擎。

设计目标（对应 5.1.0 重构）：

1. 每天只生成一次“主干”：24 个整点槽位，每个槽位都有
   start/end/activity/place/zone/category/transit/vehicle/mood。
2. 主干先由确定性规则保证物理连贯（不能瞬移、不能情绪突变），
   再让大模型只改写“这一小时在做什么”，不允许改时间、地点和类别。
3. 三级 10 分钟细分彻底废弃；点击二级卡片时，由 main.py 调用
   generate_diary() 实时生成这一小时的第一人称小日记。
4. 休息日天气好时优先读取 scenic_spots.json 里的贵阳/贵州景区；
   周末开车出行固定绑定角色资产（黑色奔驰 C 级），并带堵车/找车位细节。
5. 突发事件：
   - 休息日：老板临时叫去公司改 Bug / 朋友临时叫出去聚餐；
   - 工作日：被公司外派到插件内置的随机省份。
"""

# 门面说明（2026-11-17 拆分）：实现按职责拆到 schedule_plan / schedule_skeleton、schedule_slots、schedule_diary，公开名字与调用路径不变。

from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # 包模式（AstrBot 加载插件）
    from .schedule_plan import (  # noqa: F401  旧模块级名字原样可用（显式列名，便于 pyflakes 静态检查）
        ADMIN_BANK, Any, BREAKFAST_BANK, CAR_BRANDS, CAR_POOL, CATEGORY_MOOD, CITY_POOL,
        COMMUTE_BANK, COMPANY_POOL, CREATIVE_BANK, DINNER_BANK, DISTRICT_MAP, HOME_LIFE_BANK,
        HOME_STYLES, HOTEL_BANK, INDOOR_BANK, JIT_HOURS, LUNCH_BANK, MED_BANK, MOOD_BASE,
        MOOD_KEYS, NEGATIVE_LABELS, NIGHT_LIFE_BANK, OCCUPATION_BANKS, OFFICE_WORK_BANK,
        POSITIVE_LABELS, PROVINCE_CITIES, REST_BANK, ROAD_BANK, SAFE_EVENT, SALES_BANK,
        SCENIC_BANK, SERVICE_BANK, STATION_BANK, STUDENT_BANK, SWIM_DETAILS, SWIM_PLACES,
        TAXI_BANK, TEACHER_BANK, TECH_WORK_BANK, TRAVEL_KEYWORDS, TYPE_CHECKING,
        _FOOD_ACTIVITY_WORDS, _MEETING_DETAIL_WORDS, _SLEEP_ACTIVITY_WORDS, _WORK_DETAIL_WORDS,
        _amo_clock, _balanced, _brief_world, _city_of, _clock_now, _clock_today,
        _diary_material_lines, _mood_text, _note_swallowed, _place_of, _scenic_text,
        _strip_fences, _weather_text, _with_city, amusement_segments, annotations, asyncio,
        business_trip_segments, clamp, date, datetime, detail_conflicts, diary_presence_rules,
        fmt_hhmm, friend_dinner_segments, hashlib, homebody_segments, indoor_rest_segments,
        is_long_holiday, jit_plan, json, json_extract, logger, md5_int, occupation_kind,
        outing_segments, overtime_segments, parse_hhmm, period_rest_segments,
        pick_amusement_spot, pick_holiday_plan, random, re, scenic_segments, shopping_segments,
        sick_rest_segments, swim_segments, timedelta, travels_for_work, work_bank_for,
        workday_segments,
    )
    from .schedule_skeleton import ScheduleSkeletonMixin
    from .schedule_slots import ScheduleSlotsMixin
    from .schedule_diary import ScheduleDiaryMixin
    from . import schedule_skeleton as _mod_schedule_skeleton
    from . import schedule_slots as _mod_schedule_slots
    from . import schedule_diary as _mod_schedule_diary
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from schedule_plan import (  # noqa: F401  直接按文件加载时
            ADMIN_BANK, Any, BREAKFAST_BANK, CAR_BRANDS, CAR_POOL, CATEGORY_MOOD, CITY_POOL,
            COMMUTE_BANK, COMPANY_POOL, CREATIVE_BANK, DINNER_BANK, DISTRICT_MAP, HOME_LIFE_BANK,
            HOME_STYLES, HOTEL_BANK, INDOOR_BANK, JIT_HOURS, LUNCH_BANK, MED_BANK, MOOD_BASE,
            MOOD_KEYS, NEGATIVE_LABELS, NIGHT_LIFE_BANK, OCCUPATION_BANKS, OFFICE_WORK_BANK,
            POSITIVE_LABELS, PROVINCE_CITIES, REST_BANK, ROAD_BANK, SAFE_EVENT, SALES_BANK,
            SCENIC_BANK, SERVICE_BANK, STATION_BANK, STUDENT_BANK, SWIM_DETAILS, SWIM_PLACES,
            TAXI_BANK, TEACHER_BANK, TECH_WORK_BANK, TRAVEL_KEYWORDS, TYPE_CHECKING,
            _FOOD_ACTIVITY_WORDS, _MEETING_DETAIL_WORDS, _SLEEP_ACTIVITY_WORDS, _WORK_DETAIL_WORDS,
            _amo_clock, _balanced, _brief_world, _city_of, _clock_now, _clock_today,
            _diary_material_lines, _mood_text, _note_swallowed, _place_of, _scenic_text,
            _strip_fences, _weather_text, _with_city, amusement_segments, annotations, asyncio,
            business_trip_segments, clamp, date, datetime, detail_conflicts, diary_presence_rules,
            fmt_hhmm, friend_dinner_segments, hashlib, homebody_segments, indoor_rest_segments,
            is_long_holiday, jit_plan, json, json_extract, logger, md5_int, occupation_kind,
            outing_segments, overtime_segments, parse_hhmm, period_rest_segments,
            pick_amusement_spot, pick_holiday_plan, random, re, scenic_segments, shopping_segments,
            sick_rest_segments, swim_segments, timedelta, travels_for_work, work_bank_for,
            workday_segments,
        )
        from schedule_skeleton import ScheduleSkeletonMixin
        from schedule_slots import ScheduleSlotsMixin
        from schedule_diary import ScheduleDiaryMixin
        import schedule_skeleton as _mod_schedule_skeleton
        import schedule_slots as _mod_schedule_slots
        import schedule_diary as _mod_schedule_diary


class ScheduleEngine(ScheduleSkeletonMixin, ScheduleSlotsMixin, ScheduleDiaryMixin):
    """把“状态 -> 主干 -> 可选润色 -> 动态日记”拆开。"""

    OVERTIME_NOTES = {
        "tech": ("临时加班", "线上出问题了，被叫去公司处理"),
        "student": ("临时加课", "老师临时加了两节课，晚上才回"),
        "teacher": ("临时代课", "学校临时安排代课/监考，走不开"),
        "med": ("临时排班", "科室临时排了班，得顶上去"),
        "office": ("临时加班", "公司临时有事，被叫过去帮忙"),
    }

    TRAVEL_WORDS = (
        "地铁", "公交", "车厢", "车上", "车站", "站台", "打车", "出租车", "开车",
        "骑车", "骑行", "路上", "通勤", "换乘", "高铁", "火车", "飞机", "赶去", "赶往",
    )

    AT_HOME_WORDS = ("到家", "在家", "家里", "进门", "躺沙发", "被窝")


# 拆分出去的方法里仍写着 ScheduleEngine.xxx(self, ...) 这类老写法；把类对象回填到子模块
# 的命名空间即可（子模块不 import 门面，依赖仍单向）。
for _split_mod in (_mod_schedule_skeleton, _mod_schedule_slots, _mod_schedule_diary):
    setattr(_split_mod, "ScheduleEngine", ScheduleEngine)

__all__ = [name for name in (
    'ADMIN_BANK',
    'Any',
    'BREAKFAST_BANK',
    'CAR_BRANDS',
    'CAR_POOL',
    'CATEGORY_MOOD',
    'CITY_POOL',
    'COMMUTE_BANK',
    'COMPANY_POOL',
    'CREATIVE_BANK',
    'DINNER_BANK',
    'DISTRICT_MAP',
    'HOME_LIFE_BANK',
    'HOME_STYLES',
    'HOTEL_BANK',
    'INDOOR_BANK',
    'JIT_HOURS',
    'LUNCH_BANK',
    'MED_BANK',
    'MOOD_BASE',
    'MOOD_KEYS',
    'NEGATIVE_LABELS',
    'NIGHT_LIFE_BANK',
    'OCCUPATION_BANKS',
    'OFFICE_WORK_BANK',
    'POSITIVE_LABELS',
    'PROVINCE_CITIES',
    'REST_BANK',
    'ROAD_BANK',
    'SAFE_EVENT',
    'SALES_BANK',
    'SCENIC_BANK',
    'SERVICE_BANK',
    'STATION_BANK',
    'STUDENT_BANK',
    'SWIM_DETAILS',
    'SWIM_PLACES',
    'ScheduleEngine',
    'TAXI_BANK',
    'TEACHER_BANK',
    'TECH_WORK_BANK',
    'TRAVEL_KEYWORDS',
    'TYPE_CHECKING',
    '_FOOD_ACTIVITY_WORDS',
    '_MEETING_DETAIL_WORDS',
    '_SLEEP_ACTIVITY_WORDS',
    '_WORK_DETAIL_WORDS',
    '_amo_clock',
    '_balanced',
    '_brief_world',
    '_city_of',
    '_clock_now',
    '_clock_today',
    '_diary_material_lines',
    '_mood_text',
    '_note_swallowed',
    '_place_of',
    '_scenic_text',
    '_strip_fences',
    '_weather_text',
    '_with_city',
    'amusement_segments',
    'annotations',
    'asyncio',
    'business_trip_segments',
    'clamp',
    'date',
    'datetime',
    'detail_conflicts',
    'diary_presence_rules',
    'fmt_hhmm',
    'friend_dinner_segments',
    'hashlib',
    'homebody_segments',
    'indoor_rest_segments',
    'is_long_holiday',
    'jit_plan',
    'json',
    'json_extract',
    'logger',
    'md5_int',
    'occupation_kind',
    'outing_segments',
    'overtime_segments',
    'parse_hhmm',
    'period_rest_segments',
    'pick_amusement_spot',
    'pick_holiday_plan',
    'random',
    're',
    'scenic_segments',
    'schedule_diary.py',
    'schedule_skeleton.py',
    'schedule_slots.py',
    'shopping_segments',
    'sick_rest_segments',
    'swim_segments',
    'timedelta',
    'travels_for_work',
    'work_bank_for',
    'workday_segments',
) if name in globals()]
