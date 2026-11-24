# -*- coding: utf-8 -*-
"""画图引擎（接「我能永远定格你」OmniDraw）。

玩法：她正常回完话之后，按配置判断这轮要不要顺手画一张给她发过去。

- 触发方式（``draw_trigger_mode``）：
  - ``topic``：他这句话里出现 ``draw_topic_keywords`` 里的词（默认：吃什么/吃啥/在干嘛…），
    就用她刚才的回复当画面描述；
  - ``explicit``：他明确说「画一张 / 给我画 / 发张图 / 自拍」才画，用他这句话当描述；
  - ``both``：两种都算。
- 生成走 OmniDraw 的 ``generate_images_for_plugin()``（只返回结果，不自己发图），
  拿到图之后再通过 ``event.send()`` 补一条图片消息，所以她的文字一定先到，图后到。
- 每次生成都在后台任务里跑，不阻塞她说话；失败只记日志，不会把回复吞掉。

和主脑的关系：
- ``maybe_after_reply()`` 由 ``on_decorating_result()`` 调用（只负责判断 + 起任务）；
- ``/amo draw 描述`` 是给测试用的手动入口（不依赖触发词）。

数据只在内存里（冷却、每小时/每天计数），重启清零；不写数据库。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import asyncio
import datetime as _dt
import hashlib
import os
import random
import re
import time

from astrbot.api import logger

try:  # Phase 17：结构化事件日志（自动脱敏）
    from .observability import log_event
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from observability import log_event

from typing import Any

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import logger as _amo_logger
    from .amo_shared import today_str as _today_str
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed
    try:
        from .amo_shared import today_str as _today_str
    except Exception:  # pragma: no cover
        try:
            from amo_shared import today_str as _today_str
        except Exception:

            def _today_str():
                return _amo_clock.now().date().isoformat()
    try:  # 正常包模式
        from .amo_shared import logger as _amo_logger  # noqa: F401  (保持和主模块同一 logger)
    except Exception:  # pragma: no cover - 只在极端导入方式下走到
        _amo_logger = logger

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_image_common import (
        MessageChain,
        cfg,
        clean_place,
        find_omni,
        image_component,
        spawn,
        split_keywords,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_image_common import (
            MessageChain,
            cfg,
            clean_place,
            find_omni,
            image_component,
            spawn,
            split_keywords,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_image_common import (
                MessageChain,
                cfg,
                clean_place,
                find_omni,
                image_component,
                spawn,
                split_keywords,
            )

try:  # 统一时钟（测试会钉死时间；不要在本文件里散落裸时间调用）
    from .amo_shared import _now as _amo_now
except Exception:  # pragma: no cover
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from amo_shared import _now as _amo_now
        except Exception:
            def _amo_now():
                return _dt.datetime.fromtimestamp(_amo_clock.now_ts())

try:  # 视角后缀：她出镜=自拍视角，她拍环境=她的拍摄视角（空镜）
    from .photo_view import (FOOT_CLOSEUP_NEGATIVE, HEADWEAR_LOCK, SCENERY_ONLY, SELFIE_FRAMING, SELFIE_FRAMING_FOOT, SELFIE_NEGATIVE,
                             SELFIE_STYLE, VIEW_FOOT, looks_like_her, view_suffix)
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_view import (FOOT_CLOSEUP_NEGATIVE, HEADWEAR_LOCK, SCENERY_ONLY, SELFIE_FRAMING, SELFIE_FRAMING_FOOT, SELFIE_NEGATIVE,
                                SELFIE_STYLE, VIEW_FOOT, looks_like_her, view_suffix)

try:  # 场景：日程地点 → 背景（在家=家里、逛街=商场、旅游=景区）
    from .photo_scene import PLACE_HINTS, scene_for
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_scene import PLACE_HINTS, scene_for

try:  # 自拍姿势库：随机挑一个姿势
    from .pose_library import pose_line
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from pose_library import pose_line
        except Exception:  # pragma: no cover
            def pose_line(pose):
                return ""

try:  # 生图计划：规则表在 photo_rules，计划对象和入口在 photo_plan
    from .photo_plan import build_plan, locks_for_plan, pick_pose_for_plan
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_plan import build_plan, locks_for_plan, pick_pose_for_plan

#: 兼容：老名字叫 resolve_plan（draw_engine 里的调用点仍按这个名字）
resolve_plan = build_plan

try:  # 单一媒体出口/账本（2026-11-22 task-23）：send_photo 工具链路也必须记账/占 cap
    from . import amo_media as _media
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_media as _media

# 生图可能卡死；最多等 3 分钟就放弃这一次
GENERATE_TIMEOUT_SECONDS = 180
# data_url 是 base64，超过这个长度就不进 kv（否则数据库会被撑大）
_MAX_CACHED_DATA_URL = 200000

# 真实感/安全负面词（2026-11-15 用户要求：去 AI 塑料感 + 防封号）
REALISM_POSITIVE = (
    "第一人称手机实拍视角（first-person smartphone shot），手机镜头随手拍，"
    "轻微噪点（slight film grain），随意的构图（casual composition），生活感（lifestyle），"
    "光线不完美，不精修"
)
REALISM_NEGATIVE = (
    "精修，CG，商业摄影，影棚打光，过度打光，完美构图，塑料感，3D渲染，"
    "文字，水印，签名，NSFW，裸露，暴露，走光，suggestive，provocative"
)
#: 说了这些就等于「已经在拍照/要发照片」，没真发就必须由系统补一张
PHOTO_CLAIM_WORDS = (
    "拍好", "拍到了", "拍下", "按下快门", "快门", "咔嚓", "发你", "发给你", "给你看", "你看这张",
    "拍一张", "拍个照", "拍张照", "拍拍照", "自拍", "合影", "照片", "拍了", "镜头",
    "笑一个", "好看吗", "留念", "三二一", "三、二、一", "321", "比个耶",
    "拍进去", "我拍", "拍给你", "拍一下", "拍个", "拍张", "发张", "给你发",
)

#: 世界观硬约束：他不在物理现场，所谓合影就是她举着手机自拍
SELFIE_WORLDVIEW = (
    "（重要：他不在画面里，你们隔着屏幕；所谓合影就是她举着手机自己拍，"
    "画面里绝对不能出现第二个人、男人的手或肩膀）"
)
PHOTO_WORLDVIEW_NEGATIVE = (
    "男人，男生，男性，第二个人，另一个人，情侣合影，双人合影，多人，旁观者，第三人称视角，"
    "多出来的手，别人的肩膀，男人的手，男人的肩膀，两个女孩，两个人物，"
    "man, male, second person, group photo, couple photo, third-person view, "
    "extra arms, human shoulder, two people, other person"
)
SHOP_WORDS = ("火锅店", "烧烤店", "餐厅", "饭馆", "海底捞", "商场", "步行街", "咖啡馆",
              "奶茶店", "店面", "店内", "店里", "门店", "摊")
FOOD_WORDS = ("火锅", "烧烤", "烤肉", "自助", "海底捞", "麻辣烫", "砂锅", "串串")


def _light_line(activity, now=None):
    """按当前时刻 + 室内/室外给出光线，避免半夜画出大太阳。"""
    now = now or _amo_now()
    zone = str((activity or {}).get("zone") or "")
    indoor = zone in ("home", "office", "restaurant", "hotel", "mall", "hospital")
    hour = int(now.hour)
    if hour >= 22 or hour < 6:
        return "深夜暗光（nighttime dim lighting），" + ("室内暖光灯（warm indoor lighting）" if indoor else "街边路灯和车灯")
    if hour < 8:
        return "清晨的柔光"
    if hour >= 18:
        return "室内暖光灯（warm indoor lighting）" if indoor else "傍晚的暖色天光"
    if hour >= 17:
        return "傍晚偏暖的光线"
    return "自然日光，室内窗边的光" if indoor else "自然日光"


def _weather_line(state, activity):
    """外面下雨/下雪时，室内要能看见窗外；拿不到天气就返回空。"""
    try:
        brief = state.weather_brief() if callable(getattr(state, "weather_brief", None)) else ""
        desc = str(brief or "")
    except Exception:
        desc = ""
    if not desc:
        return ""
    zone = str((activity or {}).get("zone") or "")
    if any(word in desc for word in ("雨", "雪", "雨夹雪")):
        if zone in ("home", "office", "hotel", "mall", "restaurant", "hospital"):
            return "窗外在下雨（rain outside window），室内光线偏暗"
        return "外面在下雨，路面湿漉漉"
    return ""


def _ground_topic(subject, activity):
    """话题配图必须落回她此刻的真实环境；落不回去就给个跳过理由。"""
    subject = str(subject or "").strip()
    zone = str((activity or {}).get("zone") or "")
    category = str((activity or {}).get("category") or "")
    if not subject:
        return "", "没有可画的画面"
    if zone == "home" or category in ("rest", "sleep", "health", "meal"):
        if any(word in subject for word in FOOD_WORDS):
            return ("在家里的餐桌或茶几前，桌上放着外卖盒和手机，手机屏幕里是%s的画面，"
                    "还没开吃，家里是暖黄的小灯" % next((w for w in FOOD_WORDS if w in subject), "吃的")), ""
        if any(word in subject for word in SHOP_WORDS):
            return "", "她在家里，画不了店里的场景（这轮不配图）"
        if "家" not in subject and "窗外" not in subject:
            return "在家里，" + subject, ""
        return subject, ""
    if category in ("work", "commute") and any(word in subject for word in FOOD_WORDS):
        return "在工位/路上，手里拿着没吃完的外卖或者便利店的袋子，周围是工作环境", ""
    return subject, ""


PLAN_HINTS = ("今晚", "明天", "后天", "周末", "等会", "待会", "回头", "约好", "约了",
              "计划", "下次", "改天", "一起去", "到时候")


def _topic_is_plan(text):
    """「今晚去吃火锅」是约定/期待，不是此刻的场景，不该自动配图。"""
    body = str(text or "")
    return any(word in body for word in PLAN_HINTS)


def _topic_mood_ok(state):
    """只有情绪很高或很低（很想分享/很难受）才配图，平淡聊天就纯文字。"""
    try:
        score = float(state.mood_score())
    except Exception:
        return True
    return score >= 70 or score <= 30


SEND_PHOTO_TOOL_NAME = "send_photo"
SEND_PHOTO_TOOL_ARGS = [
    {"type": "string", "name": "description",
     "description": "想给他看的画面，一句话，比如「火锅店咕嘟冒泡的红油锅底」"},
    {"type": "string", "name": "kind",
     "description": "web=网图/网上截图（只是在聊计划或期待）；real=你自己的实拍（真的在吃/在家/在外面）"},
]


def make_send_photo_tool(plugin):
    async def send_photo(event=None, description="", kind="web"):
        engine = getattr(plugin, "draw", None)
        if engine is None:
            return "画图没开"
        return engine.send_photo_now(event, description, kind=kind)
    return send_photo


def register_send_photo(plugin, manager):
    """幂等注册 send_photo 工具：由大模型自己决定要不要发图。"""
    if manager is None:
        return False
    try:
        if manager.get_func(SEND_PHOTO_TOOL_NAME) is not None:
            manager.remove_func(SEND_PHOTO_TOOL_NAME)
        manager.add_func(
            SEND_PHOTO_TOOL_NAME, SEND_PHOTO_TOOL_ARGS,
            "发一张图给他：聊计划/期待时发网图（kind=web），自己真的在吃/在家时发实拍（kind=real）。"
            "只有觉得发图更好玩/更浪漫/更馋人才调用，平时纯文字。",
            make_send_photo_tool(plugin))
        return True
    except Exception:
        return False


def _topic_state_skip(activity):
    """睡觉/生病时不接话题配图；上班只在明确要图时才画。"""
    category = str((activity or {}).get("category") or "")
    if category in ("sleep", "health"):
        return "她在%s，话题不配图" % ("睡觉" if category == "sleep" else "生病休息")
    return ""


DEFAULT_TOPIC_KEYWORDS = (
    "吃什么,吃啥,吃点什么,想吃什么,去哪吃,在干嘛,在做什么,做什么,好饿,饿了,"
    "想喝,喝什么,喝啥,想玩,在忙什么"
)

def swim_outside_request(text, place):
    """他在外面点名要泳装/比基尼：不生成、不答应，回家再说。"""
    body = str(text or "")
    if not any(w in body for w in ("比基尼", "泳衣", "泳装", "泳裤", "三点式")):
        return False
    where = str(place or "")
    home = any(w in where for w in ("家", "客厅", "卧室", "沙发", "床", "阳台", "浴室", "洗手间"))
    return not home

DEFAULT_EXPLICIT_KEYWORDS = (
    "画一张,画个,画一下,画一幅,画张,给我画,帮我画,替我画,来一张,来张,发张图,发个图,"
    "发一张,拍张,拍一张,拍照给我看看,拍照给我看,拍给我看,照片给我看看,发张照片,自拍,画我,来点图,"
    "拍张照,拍个照,拍张照片,来张照片,发个自拍,来张自拍,拍一张照片,发张自拍"
)

_EXPLICIT_CLEAN_RE = re.compile(
    r"(请|麻烦)?(你)?(给我|帮我|替我)?"
    r"(画一张|画一个|画一幅|画一下|画张|画个|来一张|来张|发张图|发个图|发一张|"
    r"拍照给我看看|拍给我看|照片给我看看|发张照片|拍张|拍一张|自拍|给我看看|画|发张|发个|来张)"
)
_TAIL_PUNCT_RE = re.compile(r"[\s，,。.！!？?~～…]+$")
_FILLER_TAIL_RE = re.compile(r"(吧|呀|啊|呢|啦|哦|哈|嘛|咯)+$")

QUESTION_WORDS = (
    "吗", "呢", "怎么样", "如何", "在干吗", "干嘛", "在做什么", "做什么", "开心",
    "好不好", "是不是", "在不在", "在哪", "玩得", "有没有", "什么", "哪里",
)

FILLER_WORDS = (
    "开心", "在呀", "好呀", "是呀", "对呀", "还行", "还好", "哈哈", "嘿嘿",
    "呀", "啊", "啦", "哦", "嗯", "呢", "吧", "嘛",
)

# 用来做「同一场景」缓存的词；命中越多越容易被当成同一张图
SCENE_KEYWORDS = (
    "公园", "狗", "猫", "咖啡", "奶茶", "面条", "火锅", "烧烤", "蛋糕", "海边", "沙滩",
    "山", "古镇", "街", "商场", "书店", "花", "樱花", "雨", "雪", "夕阳", "夜景",
    "电影院", "地铁", "公交", "办公室", "家", "厨房", "阳台", "床", "餐桌", "餐厅",
    "学校", "操场", "湖", "河", "跑步", "散步", "蛋糕", "冰淇淋", "水果", "衣服",
    "裙子", "毛衣", "围巾", "帽子", "电脑", "书", "电影", "音乐", "旅行",
)


def _split_keywords(text):
    return split_keywords(text)


def clean_explicit_prompt(text):
    """「给我画一碗面」→「一碗面」。"""
    text = str(text or "").strip()
    cleaned = _EXPLICIT_CLEAN_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _TAIL_PUNCT_RE.sub("", cleaned)


def clean_topic_prompt(text, max_chars=80):
    """她刚说的话 → 画面描述。去掉换行/引号/末尾语气词，截断到 max_chars。"""
    text = str(text or "").replace("\n", " ").strip()
    text = text.strip("\"'“”‘’「」『』 ")
    text = _TAIL_PUNCT_RE.sub("", text)
    text = _FILLER_TAIL_RE.sub("", text).strip()
    max_chars = max(12, int(max_chars or 80))
    if len(text) > max_chars:
        text = text[:max_chars].rstrip("，,。.、 ")
    return text


#: action 里点名这些词 = 他自己定了衣服，不再拼「今日穿搭」（B2c 同源）。
#: 参考图只借长相：三视图里的白衬衫/藏青领结/米色开衫是模板衣服，绝不能漏进画面。
TEMPLATE_OUTFIT_GUARD = (
    "参考图只借长相、发型、体型和头饰；三视图里的白衬衫、藏青领结、米色开衫等模板服装不要出现在画面里，"
    "衣服只按上面的描述/今日穿搭，不要照搬模板里的衣服"
)

EXPLICIT_OUTFIT_WORDS = (
    "穿", "衣服", "裙", "衬衫", "衬衣", "毛衣", "外套", "大衣", "卫衣", "裤",
    "鞋", "靴", "T恤", "泳衣", "泳装", "泳裤", "比基尼", "三点式",
    "睡衣", "睡裙", "吊带", "背心", "西装", "内衣", "围巾", "帽子",
)


def action_names_outfit(text):
    body = str(text or "")
    return any(word in body for word in EXPLICIT_OUTFIT_WORDS)


def build_selfie_prompt(scene, outfit, activity, state, extra="", light="", pose_text=""):
    """动态拼装自拍提示词：环境/穿搭/体感/光线全部来自当前事实，不写死任何场景。"""
    parts = ["第一人称手机前置镜头自拍（hand-held front camera selfie），画面里只有她一个人"]
    scene = str(scene or "").strip()
    if scene:
        parts.append("背景是" + scene + "，要能看出这个地方，不要换成别的场景")
    outfit = str(outfit or "").strip()
    extra = str(extra or "").strip()
    explicit = action_names_outfit(extra)
    if outfit and not explicit:
        parts.append("女孩穿着" + outfit)
    elif explicit:
        # 他点名了衣服（比基尼/睡衣/某条裙子）：以动作为准，别用今日穿搭覆盖
        parts.append("衣服以上面这句动作里的描述为准，不要换成今日穿搭")
    if extra:
        parts.append(extra)
    pose_text = str(pose_text or "").strip()
    if pose_text:
        parts.append(pose_text)
    weather = _weather_line(state, activity)
    if weather:
        parts.append(weather)
    parts.append("看着镜头微笑（looking at camera, smiling）")
    parts.append(light or _light_line(activity))
    parts.append("真实的手机抓拍画质，轻微噪点，随手拍不摆拍")
    parts.append(HEADWEAR_LOCK)
    parts.append(TEMPLATE_OUTFIT_GUARD)
    parts.append(SELFIE_WORLDVIEW)
    return "，".join(part for part in parts if part)


#: 他要求重拍 / 她声称失败的口头禅（2026-11-19：挡住「假装试过」）
def _photo_turn_key(event):
    """同一轮消息的标记：两个插件共用，用来判断「这一轮到底试没试过」。"""
    try:
        mid = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        if mid:
            return mid
    except Exception as exc:  # cleanup: 取不到 message_id 就退回 id(event)
        logger.debug("[draw_engine._photo_turn_key] optional step failed: %s", exc)
    return str(id(event))


PHOTO_RETRY_WORDS = ("再试", "再拍", "再发", "拍一张", "拍张", "发张", "再来一张")
PHOTO_FAIL_WORDS = ("不行", "出不来", "没动静", "失败", "卡住", "打不开", "拍不出",
                    "断了", "超时", "连不上", "没网")


class DrawEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._last_draw = {}      # uid -> monotonic
        self._draw_times = {}     # uid -> [monotonic, ...]
        self._inflight = set()    # umo
        self._last_prompt = {}    # uid -> (prompt, monotonic)
        self._omni = None
        self._omni_checked = 0.0
        self._stats = {"scheduled": 0, "sent": 0, "failed": 0, "skipped": 0, "cached": 0}
        self._last_error = ""
        self._missing_notice_at = {}   # umo -> monotonic：缺我能永远定格你时多久提醒一次

    # ---------- 配置 ----------

    def _cfg_bool(self, key, default=False):
        return cfg(self.plugin, key, default, bool, "draw_engine.DrawEngine._cfg_bool")

    def _cfg_int(self, key, default=0):
        return cfg(self.plugin, key, default, int, "draw_engine.DrawEngine._cfg_int")

    def _cfg_float(self, key, default=0.0):
        return cfg(self.plugin, key, default, float, "draw_engine.DrawEngine._cfg_float")

    def _cfg_str(self, key, default=""):
        return cfg(self.plugin, key, default, str, "draw_engine.DrawEngine._cfg_str")

    @property
    def enabled(self):
        return self._cfg_bool("draw_enable", False)

    @property
    def dry_run(self):
        return self._cfg_bool("draw_dry_run", False)

    @property
    def debug(self):
        return self._cfg_bool("draw_debug", False)

    @property
    def debug_prompt(self):
        return bool(self._cfg_bool("debug_prompt", False) or self.debug)

    def _debug_log(self, activity, plan, final_prompt, pose_text=""):
        if not self.debug_prompt:
            return
        data = activity if isinstance(activity, dict) else {}
        act_text = " | ".join(filter(None, [
            str(data.get("activity") or ""),
            str(data.get("place") or ""),
            str(data.get("category") or ""),
            str(data.get("transit") or ""),
            str(data.get("vehicle") or ""),
        ]))
        logger.info(
            "[PHOTO_DEBUG] 日程=%s zone=%s pose_rule=%s guard=%s final_prompt=%s"
            % (act_text or "-", plan.zone, plan.pose_rule or pose_text or "-",
               plan.guard, final_prompt)
        )
        if plan.source not in ("schedule",) or plan.explicit_conflict or plan.repairs:
            logger.info(
                "[PHOTO_DEBUG] source=%s reason=%s explicit_place=%s conflict=%s repairs=%s"
                % (plan.source, plan.reason or "-", plan.explicit_place or "-",
                   plan.explicit_conflict, plan.repairs or [])
            )

    def _mode(self):
        mode = self._cfg_str("draw_trigger_mode", "topic").strip().lower()
        return mode if mode in ("topic", "explicit", "both") else "topic"

    def _topic_keywords(self):
        raw = self._cfg_str("draw_topic_keywords", DEFAULT_TOPIC_KEYWORDS)
        return _split_keywords(raw) or _split_keywords(DEFAULT_TOPIC_KEYWORDS)

    def _explicit_keywords(self):
        raw = self._cfg_str("draw_explicit_keywords", DEFAULT_EXPLICIT_KEYWORDS)
        base = _split_keywords(raw) or _split_keywords(DEFAULT_EXPLICIT_KEYWORDS)
        # 脚/脚趾特写：老配置没有这些词也照样识别（他明确点了才走）
        extra = ("脚趾", "脚背", "看脚", "拍脚", "脚部", "小脚")
        return tuple(dict.fromkeys(list(base) + list(extra)))

    # ---------- 权限 / 限额 ----------

    def is_owner(self, event):
        """谁能触发画图：draw_owner_ids（配了只认名单）> 管理员 > 关系「相恋」。"""
        raw = self._cfg_str("draw_owner_ids", "")
        owners = {item for item in _split_keywords(raw.replace(" ", ","))}
        uid = ""
        try:
            uid = str(self.plugin._canonical_uid(event) or "")
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.is_owner", _amo_exc)
        if owners:
            return bool(uid) and uid in owners
        try:
            if self.plugin._is_admin(event):
                return True
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.is_owner", _amo_exc)
        if uid:
            try:
                profile = self.plugin.db.get_profile(uid) or {}
                if str(profile.get("relation") or "") == "相恋":
                    return True
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine.is_owner", _amo_exc)
        return False

    def _check_limits(self, uid):
        """返回 None 表示可以画；否则返回跳过原因。"""
        now = time.monotonic()
        cooldown = max(0, self._cfg_int("draw_cooldown_minutes", 30)) * 60
        last = self._last_draw.get(uid, 0.0)
        if cooldown and last and now - last < cooldown:
            return "冷却中（%d 秒）" % int(cooldown - (now - last))
        times = list(self._draw_times.get(uid, []))
        hour = [ts for ts in times if now - ts < 3600]
        max_hour = max(0, self._cfg_int("draw_max_per_hour", 3))
        if max_hour <= 0:
            return "画图限额为 0（已关闭）"
        if len(hour) >= max_hour:
            return "这一小时画满了"
        # 以前这里从 hour 里再筛 86400，等于"每天"只看最近一小时，日限额根本拦不住
        day = [ts for ts in times if now - ts < 86400]
        max_day = max(0, self._cfg_int("draw_max_per_day", 10))
        if max_day <= 0:
            return "画图限额为 0（已关闭）"
        if len(day) >= max_day:
            return "今天画满了"
        return None

    def _record(self, uid):
        now = time.monotonic()
        self._last_draw[uid] = now
        times = [ts for ts in self._draw_times.get(uid, []) if now - ts < 86400]
        times.append(now)
        self._draw_times[uid] = times

    # ---------- 触发判断 ----------

    # ---------- 场景缓存（同一场景在哪个平台问，都发同一张） ----------

    @staticmethod
    def _cache_key(prompt, keywords=None, selfie=False, residual=""):
        parts = [_today_str(), "selfie" if selfie else "scene"]
        if keywords:
            parts.append(",".join(sorted(set(keywords))))
        if residual:
            parts.append(hashlib.sha1(str(residual).encode("utf-8")).hexdigest()[:8])
        if not keywords and not residual:
            parts.append(hashlib.sha1(str(prompt or "").encode("utf-8")).hexdigest())
        return "draw_cache:" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

    def _cache_get(self, key):
        minutes = self._cfg_int("draw_cache_minutes", 1440)
        if minutes <= 0:
            return None
        try:
            item = self.plugin.db.kv_get(key, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._cache_get", _amo_exc)
            return None
        if not isinstance(item, dict):
            return None
        try:
            ts = float(item.get("ts") or 0)
        except Exception:
            ts = 0.0
        if not ts or _amo_clock.now_ts() - ts > minutes * 60:
            return None
        path = str(item.get("file_path") or "")
        if path and not os.path.exists(path):
            path = ""
        url = str(item.get("url") or "")
        data_url = str(item.get("data_url") or "")
        if not path and not url and not data_url:
            return None
        return {"file_path": path, "url": url, "data_url": data_url}

    def _cache_put(self, key, item):
        if self._cfg_int("draw_cache_minutes", 1440) <= 0 or not isinstance(item, dict):
            return
        file_path = str(item.get("file_path") or "")
        url = str(item.get("url") or item.get("image_url") or "")
        data_url = str(item.get("data_url") or "")
        if not (file_path or url or data_url):
            return
        # 有本地文件/外链就够了；data_url 是 base64，存进 kv 会把库撑到几百 MB
        if data_url and (file_path or url or len(data_url) > _MAX_CACHED_DATA_URL):
            data_url = ""
        if not (file_path or url or data_url):
            return
        payload = {
            "ts": _amo_clock.now_ts(),
            "file_path": file_path,
            "url": url,
            "data_url": data_url,
        }
        try:
            self.plugin.db.kv_set(key, payload)
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._cache_put", _amo_exc)

    @staticmethod
    def _concrete_len(text):
        """去掉问句词和语气词后还剩多少“实词”，用来判断哪句话更有画面。"""
        body = str(text or "")
        for word in QUESTION_WORDS + FILLER_WORDS:
            body = body.replace(word, "")
        body = re.sub(r"[\s，,。.！!？?~～…]+", "", body)
        return len(body)

    def _is_vague_request(self, text):
        """「你在公园吗」「公园玩的开心吗」这类问句，不能拿它本身当画面。"""
        cleaned = clean_explicit_prompt(text)
        if not cleaned:
            return True
        return any(word in text for word in QUESTION_WORDS)

    def _richer(self, *texts):
        best = ""
        for text in texts:
            text = str(text or "").strip()
            if self._concrete_len(text) > self._concrete_len(best):
                best = text
        return best

    def _activity(self):
        """她此刻在日程里那条（拿不到就空 dict）。"""
        try:
            return self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._activity", _amo_exc)
            return {}

    def _place_text(self):
        activity = self._activity()
        return "%s %s" % (activity.get("place") or "", activity.get("activity") or "")

    @staticmethod
    def _clean_place(place):
        return clean_place(place)

    def _current_scene(self):
        """她此刻真实在哪、在干嘛（来自日程），地点换成一整句具体场景。"""
        activity = self._activity()
        where = scene_for(activity)
        act = str(activity.get("activity") or "").strip()
        if act and act not in where:
            return "%s，%s" % (where, act) if where else act
        return where or act

    def _scene_keywords(self, text):
        text = str(text or "")
        words = list(SCENE_KEYWORDS)
        extra = self._cfg_str("draw_scene_keywords", "")
        for item in extra.replace("，", ",").split(","):
            item = item.strip()
            if item and item not in words:
                words.append(item)
        found = []
        for word in words:
            if word in text and word not in found:
                found.append(word)
        return found

    @staticmethod
    def _scene_residual(text, keywords):
        """场景词以外的具体细节（喂鸽子/放风筝…），有就参与缓存键，避免被当成同一张。"""
        body = str(text or "")
        for word in keywords:
            body = body.replace(word, "")
        for word in QUESTION_WORDS + FILLER_WORDS:
            body = body.replace(word, "")
        for word in ("在", "了", "着", "和", "跟", "我", "你", "他", "她", "它", "的",
                     "是", "有", "去", "来", "那边", "这里", "那里", "一起", "看到",
                     "看见", "今天", "现在", "还", "就", "也", "都", "很", "一个",
                     "到", "玩", "逛", "走", "看", "坐", "吃", "喝", "聊", "待", "呆",
                     "转", "跑", "睡", "躺", "站", "拍", "买"):
            body = body.replace(word, "")
        body = re.sub(r"[\s，,。.！!？?~～…]+", "", body)
        return body if len(body) >= 3 else ""

    def _last_bot_line(self, event, reply_text):
        """她上一条说过的话（「拍照给我看看」时拿它当画面描述）。

        先按 uid 跨平台找（QQ 说过的话，飞书那边也认），再退回当前会话。
        """
        current = str(reply_text or "").strip()
        rows: list[Any] = []
        uid = ""
        try:
            uid = str(self.plugin._canonical_uid(event) or "")
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._last_bot_line", _amo_exc)
        if uid:
            try:
                rows = self.plugin.db.messages_by_uid(uid, 12) or []
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine._last_bot_line", _amo_exc)
                rows = []
        if not rows:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if umo:
                try:
                    rows = self.plugin.db.recent_messages(umo, 8) or []
                except Exception as _amo_exc:
                    _note_swallowed("draw_engine.DrawEngine._last_bot_line", _amo_exc)
                    rows = []
        for row in reversed(rows):
            if str(row.get("role") or "") != "bot":
                continue
            content = str(row.get("content") or "").strip()
            if not content or content == current:
                continue
            return content
        return ""

    def _skip(self, reason):
        logger.debug("[DRAW] skip reason=%s" % reason)
        return None

    def wants_photo(self, event):
        """他这轮是不是明确在要照片（给主脑提示用：别一口拒绝）。"""
        if not self.enabled:
            return False
        try:
            if self._cfg_bool("draw_private_only", True) and not event.is_private_chat():
                return False
            if not self.is_owner(event):
                return False
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.wants_photo", _amo_exc)
            return False
        text = str(getattr(event, "message_str", "") or "")
        if any(kw and kw.lower() in text.lower() for kw in self._explicit_keywords()):
            return True
        # 2026-11-19：泳装/比基尼请求常常不带「拍照」两个字（「穿比基尼给我看看」
        # 「拍照给我看，我要看你穿比基尼」）。漏判时不会注入泳装口径，她会自己编
        # 「隔着屏幕我不发」这种边界；这里按「泳装词 + 看/拍/发」认成照片请求。
        return (any(word in text for word in ("比基尼", "泳衣", "泳装", "泳裤", "三点式"))
                and any(word in text for word in ("看看", "给我看", "看你穿", "拍", "照片", "发张")))

    @staticmethod
    def _is_foot_request(text):
        try:
            from .photo_view import wants_foot_closeup
        except ImportError:
            from photo_view import wants_foot_closeup
        return bool(wants_foot_closeup(str(text or "")))

    def decide(self, event, reply_text):
        """返回 dict(prompt, mode, reason) 或 None。"""
        if not self.enabled:
            return self._skip("draw_enable=false")
        if self._cfg_bool("draw_private_only", True) and not event.is_private_chat():
            return self._skip("不是私聊")
        if not self.is_owner(event):
            return self._skip("不是老公/相恋关系")
        text = str(getattr(event, "message_str", "") or "").strip()
        if self._is_foot_request(text):
            return self._skip("foot-closeup-handled-by-send-photo")
        if any(w in text for w in ("比基尼", "泳衣", "泳装", "泳裤", "三点式")):
            # 泳装请求只走“本人实拍（在家才允许）”，绝不能被 decide 当成环境空镜
            return self._skip("swim-request-handled-by-send-photo")
        reply_text = str(reply_text or "").strip()
        if not text:
            return self._skip("空消息")
        selfie = "自拍" in text
        mode = self._mode()
        lowered = text.lower()
        # 他直接说「拍张照/发张自拍」→ 无论触发模式都 100% 执行，不吃概率
        explicit_hit = any(
            kw and kw.lower() in lowered for kw in self._explicit_keywords()
        )
        topic_hit = mode in ("topic", "both") and any(
            kw and kw in text for kw in self._topic_keywords()
        )
        if not explicit_hit and not topic_hit:
            return self._skip("没命中触发词")
        if not reply_text:
            return self._skip("没有回复文本")
        subject, source = self._decide_subject(event, text, reply_text, explicit_hit, selfie)
        if subject is None:
            return self._skip(source)
        if explicit_hit and self._is_vague_request(text):
            # 「你在公园吗/拍照给我看看」：拍她自己，用固定形象参考图
            selfie = True
        if not selfie and looks_like_her(text):
            # 「拍张你在工位的照片」也要拍她本人 → 自拍视角、带固定形象；
            # 「你看到的海/你眼里的窗外」是她的视角，人不出镜，保持空镜
            selfie = True
        # 物理状态校验：睡觉/生病不接话题配图；居家不能凭空画店里的场景
        activity = self._activity()
        if not explicit_hit:
            skip = _topic_state_skip(activity)
            if skip:
                return self._skip(skip)
            # 话题不强制配图：约定/计划、情绪平淡、概率没中 → 纯文字
            if _topic_is_plan(text):
                return self._skip("这是计划/约定，等真吃真去再拍")
            if not _topic_mood_ok(getattr(self.plugin, "state", None)):
                return self._skip("情绪平淡，纯文字聊天")
            chance = max(0, min(100, self._cfg_int("draw_topic_prob", 20)))
            if chance <= 0 or random.random() * 100 >= chance:
                return self._skip("话题配图概率没中")
            if not selfie:
                subject, skip = _ground_topic(subject, activity)
                if skip:
                    return self._skip(skip)
        photo_plan = None
        if selfie:
            prompt, photo_plan = self._selfie_prompt(event, text, subject)
        else:
            # 她拍的环境：空镜 + 她的视角，画面里不出现她本人
            prefix = self._cfg_str("photo_prompt_prefix", "自然生活照，手机随手拍，")
            extras = "，".join(item for item in (
                _light_line(self._activity()),
                _weather_line(getattr(self.plugin, "state", None), self._activity()),
                SCENERY_ONLY, REALISM_POSITIVE, view_suffix(False),
            ) if item)
            prompt = (prefix + subject + "，" + extras).strip("，")
        scene_text = " ".join(filter(None, [subject, reply_text, self._place_text()]))
        keywords = self._scene_keywords(scene_text)
        residual = self._scene_residual(scene_text, keywords)
        cache_key = self._cache_key(prompt, keywords, selfie, residual)
        uid = str(self.plugin._canonical_uid(event) or "")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        cached = self._cache_get(cache_key)
        if cached:
            # 同一个场景在别的平台再问：直接发同一张，不重新画、也不吃冷却
            return {"prompt": prompt, "mode": source, "reason": "cache",
                    "cached": cached, "cache_key": cache_key}
        if umo and umo in self._inflight:
            return self._skip("上一张还在画")
        # 他自己点名要「照片/自拍」→ 不吃每小时/每天限额；纯画图与话题配图仍然受限
        photo_direct = explicit_hit and any(
            word in lowered for word in ("拍", "照片", "自拍", "合影", "留念", "镜头", "咔嚓"))
        reason = "" if photo_direct else self._check_limits(uid)
        if reason:
            if self.debug:
                log_event("info", "draw.skip", reason=reason, uid=uid)
            return None
        last = self._last_prompt.get(uid)
        if last and last[0] == prompt and time.monotonic() - last[1] < 120:
            return self._skip("120 秒内同一句")
        return {"prompt": prompt, "mode": source, "reason": "explicit" if explicit_hit else "topic",
                "cache_key": cache_key, "selfie": selfie, "plan": photo_plan}

    def _decide_subject(self, event, text, reply_text, explicit_hit, selfie):
        # 显式画图：清理/问句兜底；话题画图：直接用回复文本
        if explicit_hit and any(word in reply_text for word in (
            "画好", "画了", "给你画", "这就画", "马上画", "稍等", "等我画", "发给你",
        )):
            # 模型已经自己调了画图工具，别重复画
            return None, "模型回复里已经在画"
        if explicit_hit:
            source = "explicit"
            cleaned = clean_explicit_prompt(text)
            max_chars = self._cfg_int("draw_prompt_max_chars", 80)
            if self._is_vague_request(text) or len(cleaned) < 2:
                # 问「你在公园吗/你在干嘛」：她到底在哪由日程决定，图也按日程里的真实地点画
                scene = self._current_scene()
                if scene:
                    subject = scene
                else:
                    previous = self._last_bot_line(event, reply_text)
                    best = self._richer(reply_text, previous)
                    subject = clean_topic_prompt(best, max_chars) if best else cleaned
            else:
                subject = cleaned
                if len(subject) < 4:
                    previous = self._last_bot_line(event, reply_text)
                    if previous:
                        subject = clean_topic_prompt(previous, max_chars)
        else:
            subject = clean_topic_prompt(reply_text, self._cfg_int("draw_prompt_max_chars", 80))
            source = "topic"
        if not subject:
            subject = clean_topic_prompt(reply_text, self._cfg_int("draw_prompt_max_chars", 80))
        if not subject and selfie:
            subject = "自拍"
        if not subject:
            return None, "没有可画的画面"
        return subject, source

    def _selfie_prompt(self, event, text, subject):
        # 自拍：日程计划决定地点/姿势/守卫，衣服默认锁今日穿搭；返回 (prompt, plan)
        # 照片语义：地点/姿势/守卫全部由日程计划决定；他说的地点只在日程不明时兜底
        activity = self._activity()
        explicit_place = ""
        # 只认他这句话里说的地点；不要从她上一条/日程场景句里抠（「石板路」不是地点冲突）
        for word in PLACE_HINTS:
            if word and word in str(text or ""):
                explicit_place = word
                break
        plan = resolve_plan(activity, mode="photo", explicit_place=explicit_place)
        photo_plan = plan
        if plan.explicit_conflict and self.debug_prompt:
            logger.info(
                "[PHOTO_DEBUG] conflict=explicit_place:%s schedule_zone:%s → schedule_wins"
                % (explicit_place, plan.base_zone)
            )
        where = scene_for(activity) or (explicit_place if plan.source == "explicit_place" else "")
        # 「发张自拍」：默认穿今天固定的那一套；他点名要别的衣服才按他的来
        parts = ["第一人称手机前置镜头自拍（hand-held front camera selfie），画面里只有她一个人"]
        outfit = ""
        photo = getattr(self.plugin, "photo", None)
        getter = getattr(photo, "outfit_today", None) if photo is not None else None
        if callable(getter):
            try:
                outfit = str(getter() or "")
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine.decide", _amo_exc)
                outfit = ""
        explicit_clothes = any(word in str(subject or "") for word in (
            "穿", "衣服", "裙", "衬衫", "衬衣", "毛衣", "外套", "大衣", "卫衣", "裤",
            "鞋", "靴", "T恤", "泳衣", "泳装", "睡衣", "吊带", "背心", "西装",
            "围巾", "帽子",
        ))
        if plan is not None:
            # B2b：action 点名衣服时，_run 生成期间要让 omnidraw 的 OUTFIT LOCK 失效。
            plan.debug["explicit_outfit"] = bool(explicit_clothes)
        locked_today = bool(outfit) and not explicit_clothes
        if locked_today:
            parts.append("女孩穿着" + outfit)
        if subject and subject != "自拍" and not plan.explicit_conflict:
            # 他说的细节保留（衣服/话题），但和日程冲突的地点不写进画面
            parts.append(subject)
        if where:
            parts.append("背景是" + where + "，要能看出这个地方、不要换成别的场景")
        # 姿势：由场景规则决定（居家才允许用姿势库；公共场合不会随机到床姿）
        library = getattr(self.plugin, "pose", None)
        _pose, pose_text = pick_pose_for_plan(plan, library)
        if pose_text and "姿势" not in str(subject or ""):
            parts.append(pose_text)
        state_line = ""
        photo_engine = getattr(self.plugin, "photo", None)
        hint_fn = getattr(photo_engine, "_selfie_state_hint", None)
        if callable(hint_fn):
            try:
                state_line = hint_fn(activity, plan)
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine.decide.selfie_state", _amo_exc)
                state_line = ""
        parts.append(plan.guard)
        try:
            from .photo_view import wants_foot_closeup as _wants_foot
        except ImportError:
            from photo_view import wants_foot_closeup as _wants_foot
        _foot = bool(_wants_foot(str(subject or "")))
        _framing = SELFIE_FRAMING_FOOT if _foot else SELFIE_FRAMING
        if _pose and not _foot:
            try:
                try:
                    from .pose_library import camera_line
                except ImportError:
                    from pose_library import camera_line
                parts.append(camera_line(_pose))
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine.decide.camera_line", _amo_exc)
        parts.extend([item for item in (state_line, SELFIE_WORLDVIEW, _framing, SELFIE_STYLE) if item])
        parts.extend([_weather_line(getattr(self.plugin, "state", None), activity),
                      _light_line(activity),
                      "" if _foot else "看着镜头微笑（looking at camera, smiling）",
                      REALISM_POSITIVE, (VIEW_FOOT if _foot else view_suffix(True)),
                      "真实的手机抓拍画质", "生活感"])
        if locked_today:
            parts.append("人物长相参考模板，衣服锁定为上面的今日穿搭，不要换别的衣服，也不要照搬模板里的衣服")
        else:
            parts.append("人物长相参考模板，衣服颜色和款式以文字为准，不要照搬模板里的衣服")
        prompt = "，".join(part for part in parts if part)
        self._debug_log(activity, plan, prompt, pose_text)
        return prompt, photo_plan


    def maybe_after_reply(self, event, reply_text):
        """on_decorating_result 里调用：判断 + 起一个后台任务，不阻塞。"""
        # 这一轮她已经自己发过图（LLM 用了 generate_selfie）→ 别再补一张
        try:
            photo = getattr(self.plugin, "photo", None)
            if photo is not None:
                blocked = getattr(photo, "proactive_blocked", None)
                if callable(blocked) and blocked():
                    logger.debug("[amo] 这一轮已经发过照片，跳过自动补图")
                    return None
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.maybe_after_reply.suppress", _amo_exc)
        plan = self.decide(event, reply_text)
        if not plan:
            return None
        # 明确要图、但本机根本没装我能永远定格你：别一声不吭（用户只会以为她不理人），
        # 回一句人话说明缺什么；同一会话 10 分钟只提醒一次。
        notice = self.missing_omni_notice(event)
        if notice:
            self._spawn(self._send_text(event, notice))
            return None
        # 先占位：生成要几十秒，期间别让定时分享再补一张
        try:
            photo = getattr(self.plugin, "photo", None)
            note = getattr(photo, "note_sent", None)
            if callable(note):
                note()
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.maybe_after_reply.claim", _amo_exc)
        self._schedule(event, plan["prompt"], plan["reason"], plan.get("cached"),
                       plan.get("cache_key", ""), plan.get("selfie", False),
                       plan.get("plan"))
        return plan

    def missing_omni_notice(self, event):
        """这一轮该不该解释「没装画图插件」。返回要说的话，或空字符串。"""
        try:
            if not self.wants_photo(event):
                return ""
            if self._find_omni() is not None:
                return ""
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            store = getattr(self, "_missing_notice_at", None)
            if not isinstance(store, dict):
                store = {}
                self._missing_notice_at = store
            now = time.monotonic()
            last = store.get(umo)
            if last is not None and now - last < 600:
                return ""
            store[umo] = now
            logger.info("[amo] 他这轮要照片，但本机没装我能永远定格你：已回复说明")
            return "我这台还没装画图插件（我能永远定格你），装了才能给你拍照片；等我弄好再给你拍。"
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.missing_omni_notice", _amo_exc)
            return ""

    async def _notify_failure(self, event, reason="", explicit=False):
        """明确要图但生成/发送失败时，给一句人话；话题自动配图失败就只记日志。

        2026-11-15 用户反馈：他只是聊到吃的（话题自动配图），图没生成出来却收到
        「这次没拍成，等会儿再给你拍」，像是她凭空承诺；自动配图失败不该出声。
        """
        try:
            if event is None:
                return
            if not explicit:
                logger.warning("[DRAW] failure_silent reason=%s（话题自动配图，不打扰）",
                               str(reason or "")[:80])
                return
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            store = getattr(self, "_failure_notice_at", None)
            if not isinstance(store, dict):
                store = {}
                self._failure_notice_at = store
            now = time.monotonic()
            last = store.get(umo) if umo else None
            if umo and last is not None and now - last < 600:
                return
            if umo:
                store[umo] = now
            logger.warning("[DRAW] failure_notice=1 reason=%s", str(reason or "")[:80])
            # 2026-11-22 task-23：失败话术统一出口（技术词一律不出现；"没拍成"固定句
            # 与旧行为逐字一致，测试/用户都认这句）。
            await self._send_text(event, _media.failure_line(fixed=True))
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine._notify_failure", _amo_exc)

    async def _send_text(self, event, text):
        """补一句纯文字（缺插件时用；发送失败只记日志，不影响正常回复）。"""
        if MessageChain is None or not text:
            return False
        try:
            try:
                from astrbot.api.message_components import Plain
            except Exception:
                from astrbot.api.event.components import Plain
        except Exception as exc:
            logger.warning("[amo] 缺画图插件提示发不出去（拿不到 Plain）: %s" % exc)
            return False
        # 2026-11-22 task-16：补话也走统一出口（source=photo），不再直发 event.send。
        plugin = getattr(self, "plugin", None)
        sender = getattr(plugin, "_send_to_umo", None)
        if callable(sender):
            try:
                return bool(await sender(str(getattr(event, "unified_msg_origin", "") or ""),
                                         str(text), source="photo", kind="draw_text"))
            except Exception as exc:
                logger.warning("[amo] 缺画图插件提示发送失败: %s" % exc)
                return False
        try:
            try:
                try:
                    from .amo_message import strip_media_markers as _strip_media
                except ImportError:
                    from amo_message import strip_media_markers as _strip_media
                text = _strip_media(text)
            except Exception as exc:  # cleanup: 兜底/降级，保持原行为
                _note_swallowed("draw_engine._send_text", exc)
            if not str(text or "").strip():
                return False
            await event.send(MessageChain([Plain(text)]))
            return True
        except Exception as exc:
            logger.warning("[amo] 缺画图插件提示发送失败: %s" % exc)
            return False

    def _foot_refs(self):
        """脚趾特写也要戴参考图：房间背景图 + 今天这套衣服的平铺图（三视图由生图插件自动加）。"""
        photo = getattr(self.plugin, "photo", None)
        if photo is None:
            return []
        try:
            # 脚趾图不带房间参考图：家/餐厅的房间图会把桌子、拖鞋带进构图，场景交给提示词和当前日程
            refs, _pose, _outfit, _scope = photo._selfie_reference_refs(
                None, {"outfit": True, "pose": False, "room": False}, "")
            return list(refs or [])
        except Exception as exc:
            _note_swallowed("draw_engine.DrawEngine._foot_refs", exc)
            return []

    def _couple_block_reason(self):
        """合拍被拦的事实原因（给模型自己组织话，不写死台词）。"""
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            scene = _hh.scene_now(self.plugin)
            return str(getattr(scene, "reason", "") or "他不在她身边")
        except Exception as exc:
            logger.debug("[amo] 合拍在场判定失败: %r", exc)
            return "他不在她身边"

    def _can_couple_photo(self):
        """合拍门禁：同居/半同居他在家，或正在约会见面，才可能两个人同框。"""
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            if not _hh.enabled(self.plugin):
                return True
            scene = _hh.scene_now(self.plugin)
            return bool(getattr(scene, "partner", False))
        except Exception as exc:
            logger.debug("[amo] 合拍在场判定失败，按可拍处理: %r", exc)
            return True

    def send_photo_now(self, event, description, kind="web", force=False, caption=""):
        """大模型主动决定发图：web=网图/截图分享，real=她自己的实拍。"""
        if not self.enabled:
            return "这会儿不方便拍，等会儿再说"
        if not self.is_owner(event):
            return "这个只拍给你一个人看"
        text = str(description or "").strip() or "随手拍"

        try:
            _place = str((self._activity() or {}).get("place") or "")
        except Exception:
            _place = ""
        if swim_outside_request(text, _place):
            if "家" in _place:
                # 在爸妈家/自己家：不能谎称「在外面」，只是这会儿不方便拍
                return "拍不了：她现在在家，这会儿不方便拍这个"
            return "拍不了：她现在在外面，等回家再拍"
        # 「自拍/合影/拍照」这类必须走她本人的实拍，不能被当成网图
        if any(word in text for word in ("自拍", "合影", "拍照", "照片", "拍一张")):
            kind = "real"
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        self._attempt_turn = _photo_turn_key(event)
        if umo and umo in self._inflight:
            return "上一张还在拍，等它出来"
        uid = str(self.plugin._canonical_uid(event) or "")
        if not force and self._check_limits(uid):
            return "今天拍得够多啦，先歇会儿"
        if str(kind or "web").lower() == "real":
            activity = self._activity()
            scene = scene_for(activity) if callable(scene_for) else ""
            try:
                # 2026-11-22 task-23：工具链路也生成一份计划——账本要 scene/pose，
                # 发送前要用同一份计划做单 zone 断言（和自动分享同口径）。
                photo_plan = build_plan(activity, mode="photo", scene=str(scene or ""))
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.send_photo_now.plan", _amo_exc)
                photo_plan = None
            outfit = ""
            try:
                getter = getattr(getattr(self.plugin, "photo", None), "outfit_today", None)
                outfit = str(getter() or "") if callable(getter) else ""
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine.send_photo_now.outfit", _amo_exc)
                outfit = ""
            detail = text if (text and text != "随手拍") else ""
            try:
                try:
                    from .amo_photo_intent import is_couple_photo_request
                except ImportError:
                    from amo_photo_intent import is_couple_photo_request
                _couple = is_couple_photo_request(text)
            except Exception:
                _couple = False
            if _couple:
                if not self._can_couple_photo():
                    # 只给事实，不替模型写死台词；由她自己按此刻上下文开口
                    return "这会儿拍不了合照：%s，他不在你身边，没法同框。" % self._couple_block_reason()
                engine = getattr(self.plugin, "photo", None)
                if engine is not None:
                    pose = None
                    try:
                        try:
                            from .pose_library import match_pose
                        except ImportError:
                            from pose_library import match_pose
                        _lib = getattr(self.plugin, "pose", None)
                        pose = match_pose(_lib, text, usage="couple")
                    except Exception:
                        pose = None
                    if pose is None:
                        pose = engine.couple_pose(scene)
                    prompt = engine.couple_prompt(scene, pose, text)
                    refs = engine.couple_refs(pose)
                    self._schedule(event, prompt, "tool", selfie=False, refs=refs, caption=caption)
                    self._last_tool_photo_at = time.monotonic()
                    return "已安排：双人合拍，正在生成"
            try:
                from .photo_view import wants_foot_closeup, foot_closeup_prompt
            except ImportError:
                from photo_view import wants_foot_closeup, foot_closeup_prompt
            logger.info("[DRAW_DIAG] send_photo_now real text=%s kind=%s force=%s",
                        text[:40], kind, force)
            if wants_foot_closeup(text):
                try:
                    _scene = scene_for(self._activity())
                except Exception:
                    _scene = ""
                try:
                    _shoes = str((self.plugin.wardrobe.current() or {}).get("shoes") or "")
                except Exception:
                    _shoes = ""
                outfit_line = ("她现在的穿搭是%s；画面里若出现上半身，衣服必须按这套，" % outfit) if outfit else ""
                prompt = (foot_closeup_prompt(_scene, _shoes) + "。"
                          + outfit_line +
                          "画面里不出现上半身、衣领、开衫、白衬衫；参考图只借长相，不要照搬模板里的衣服。"
                          + REALISM_POSITIVE)
                _refs = self._foot_refs()
                logger.info("[DRAW_DIAG] foot_refs=%d", len(_refs))
                if _refs:
                    prompt += "\n（参考图：三视图是长相；服装平铺图只参考裙摆/裤子和颜色，鞋子按今天的鞋脱在脚边；脚上不穿鞋袜；正常手机距离，不是微距特写。）"
            else:
                _pose_refs = None
                _pose = None
                try:
                    try:
                        from .pose_library import match_pose
                    except ImportError:
                        from pose_library import match_pose
                    _library = getattr(self.plugin, "pose", None)
                    _pose = match_pose(_library, text)
                    _zone = str(getattr(photo_plan, "zone", "") or "")
                    if _pose and _zone:
                        try:
                            try:
                                from .pose_library import pose_fits_zone as _fits
                            except ImportError:
                                from pose_library import pose_fits_zone as _fits
                            if not _fits(_pose, _zone):
                                logger.info("[DRAW] 点名的姿势不适合当前场景 %s，本次不用", _zone)
                                _pose = None
                        except Exception as exc:  # cleanup: 兜底/降级，保持原行为
                            _note_swallowed("draw_engine.send_photo_now", exc)
                    if _pose:
                        detail = (detail + "。" + pose_line(_pose)).strip("。")
                        try:
                            try:
                                from .pose_library import camera_mode
                            except ImportError:
                                from pose_library import camera_mode
                            if camera_mode(_pose) == "void":
                                detail += ("。镜头：第三人称虚空相机，全身构图，不出现手机、"
                                           "不出现拿手机的手，像有人顺手抓拍")
                        except Exception as _amo_exc:
                            _note_swallowed("draw_engine.send_photo_now.camera", _amo_exc)
                        _path = _library.ref_path(_pose) if _library is not None else ""
                        if _path and os.path.isfile(_path):
                            _pose_refs = [_path]
                except Exception as _amo_exc:
                    _note_swallowed("draw_engine.send_photo_now.pose", _amo_exc)
                prompt = build_selfie_prompt(scene, outfit, activity,
                                             getattr(self.plugin, "state", None),
                                             extra=detail) + "。" + REALISM_POSITIVE
            # 发送前单 zone 断言：工具链路和他明确点单的图也要和当前日程对齐
            # （不通过就清洗；仍不通过只记日志，用户明确要图不静默丢弃）。
            try:
                try:
                    from .photo_prompt import enforce_photo_media_prompt as _enforce_prompt
                except ImportError:
                    from photo_prompt import enforce_photo_media_prompt as _enforce_prompt
                prompt, _prompt_ok, _prompt_reasons = _enforce_prompt(
                    prompt, plan=photo_plan,
                    pose=None if wants_foot_closeup(text) else _pose,
                    activity=activity)
                if not _prompt_ok:
                    logger.warning("[DRAW] prompt 单场景断言不通过：%s",
                                   "；".join(_prompt_reasons) or "-")
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.send_photo_now.prompt_verify", _amo_exc)
            self._schedule(event, prompt, "tool", selfie=True,
                           refs=_refs if wants_foot_closeup(text) else _pose_refs,
                           caption=caption, photo_plan=photo_plan)
            try:
                self._last_tool_photo_at = time.monotonic()
            except Exception as exc:  # cleanup: 时间戳失败不影响发图
                _note_swallowed("draw_engine.send_photo_now", exc)
            return "已安排：她的实拍，正在生成"
        prompt = ("网上找的图片，聊天里分享的网图/截图，不是她本人拍的，"
                  "可以是店铺招牌、菜品图或梗图风格，画面里不出现她自己；" + text)
        self._schedule(event, prompt, "tool", selfie=False, caption=caption)
        try:
            self._last_tool_photo_at = time.monotonic()
        except Exception as exc:  # cleanup: 时间戳失败不影响发图
            _note_swallowed("draw_engine.send_photo_now", exc)
        return "已安排：找一张图分享，正在生成"

    def enforce_photo_retry(self, event, his_words, reply):
        """他说「再试一次 / 拍一张」，她却回「不行 / 出不来」且本轮没真拍 → 真跑一次。

        2026-11-19：12:53 那轮她只演了一句「又试了一下，没动静」，日志里根本没有调用，
        生图通道其实是好的。这里补一道兜底：没真试过就不许说「不行」，先替她真试一次。

        注意：只保证「试过」，不保证出图，也不承诺成功——文案保持中性，
        免得她嘴上说拍好了、图却没出来。
        """
        body = str(reply or "")
        words = str(his_words or "")
        if not body or not words:
            return body
        if not any(word in words for word in PHOTO_RETRY_WORDS):
            return body
        if not any(word in body for word in PHOTO_FAIL_WORDS):
            return body
        key = _photo_turn_key(event)
        if key:
            if key == getattr(self, "_attempt_turn", None):
                return body
            try:
                omni = self._find_omni()
            except Exception:
                omni = None
            if omni is not None and key == getattr(omni, "_attempt_turn", None):
                return body
        try:
            result = self.send_photo_now(event, words, kind="real", force=True,
                                         caption="喏，这次真出来了，看吧。")
        except Exception as exc:
            logger.warning("[DRAW] 补拍失败: %s", exc)
            return body
        _blocked = ("画图功能未开启", "画图没开", "权限不足", "拍不了", "上一张还在生成",
                    "上一张还在拍", "这次不发图", "合拍失败", "这会儿拍不了",
                    "今天拍得够多", "这个只拍给", "没找到")
        if result and not any(word in str(result) for word in _blocked):
            logger.info("[DRAW] 他说要重拍但她只想演，已真试一次（不保证出图）")
            return "我试试，等我一下。"
        return body

    def enforce_photo_claim(self, event, text):
        """兜底：回话说「拍好了/按下快门」却没调 send_photo → 真补一张自拍。

        返回 (可能被改写的文本, 是否补了图)。
        """
        body = str(text or "")
        if not body:
            return body, False
        if not any(word in body for word in PHOTO_CLAIM_WORDS):
            return body, False
        try:
            last = float(getattr(self, "_last_tool_photo_at", 0) or 0)
        except Exception:
            last = 0.0
        if last and time.monotonic() - last < 60:
            return body, False
        if not self.enabled:
            return body, False
        result = self.send_photo_now(event, "拍一张自拍发给他", kind="real", force=True)
        return body, any(word in result for word in ("拍一张", "发你", "已安排", "已收到"))

    def manual(self, event, text):
        """``/amo draw 描述``：跳过触发词，直接画（给测试用）。"""
        if not self.enabled:
            return "这会儿不方便拍，等会儿再说。"
        if not self.is_owner(event):
            return "这个只拍给你一个人看。"
        text = str(text or "").strip()
        if not text:
            return self.diagnostics()
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if umo and umo in self._inflight:
            return "上一张还在拍，等它出来。"
        selfie = looks_like_her(text)
        prefix = self._cfg_str("photo_prompt_prefix", "自然生活照，手机随手拍，")
        body = text if ("自拍" in text or not selfie) else "自拍，" + text
        view = view_suffix(selfie) if selfie else (SCENERY_ONLY + "，" + view_suffix(False))
        prompt = (prefix + body + "，" + view).strip("，")
        self._schedule(event, prompt, "manual", selfie=selfie)
        tail = "（这次只记提示词）" if self.dry_run else ""
        return "🎨 开始画：%s%s（画好会单独发一张）" % (text, tail)

    # ---------- 任务 ----------

    def _spawn(self, coro):
        return spawn(self.plugin, coro, "draw_engine.DrawEngine._spawn")

    def _schedule(self, event, prompt, reason, cached=None, cache_key="", selfie=False, photo_plan=None, refs=None, caption=""):
        uid = str(self.plugin._canonical_uid(event) or "")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if umo:
            self._inflight.add(umo)
        self._last_prompt[uid] = (prompt, time.monotonic())
        self._stats["scheduled"] += 1
        if self.debug:
            logger.info("[amo] 画图排队（%s%s）：%s" % (reason, "/缓存" if cached else "", prompt))
        if self._spawn(self._run(event, prompt, uid, umo, cached, cache_key, selfie,
                                 photo_plan, explicit=reason in ("explicit", "manual", "tool"),
                                 refs=refs, caption=caption)) is None:
            if umo:
                self._inflight.discard(umo)

    async def _run(self, event, prompt, uid, umo, cached=None, cache_key="", selfie=False,
                   photo_plan=None, explicit=False, refs=None, caption=""):
        try:
            delay = max(0.0, self._cfg_float("draw_delay_seconds", 3.0))
            if delay:
                await asyncio.sleep(delay)
            if not self.enabled:
                return
            if cached:
                if await self._send_image(event, cached, caption=caption, plan=photo_plan):
                    self._stats["cached"] += 1
                return
            if self.dry_run:
                logger.info("[amo] 画图（dry-run）不调用模型（%s）：%s" % (
                    "自拍视角" if selfie else "她的拍摄视角", prompt))
                self._stats["skipped"] += 1
                return
            wardrobe = getattr(self.plugin, "wardrobe", None)
            suppress_lock = bool(
                photo_plan is not None
                and getattr(photo_plan, "debug", {}).get("explicit_outfit")
            )
            if (suppress_lock and wardrobe is not None
                    and hasattr(wardrobe, "outfit_lock_suspended")):
                # B2b/B2c：他点名了衣服，本次请求显式跳过 OUTFIT LOCK；
                # ContextVar 标记只作用于本 task，并发会话照常上锁。
                with wardrobe.outfit_lock_suspended("explicit_outfit"):
                    image = await self._generate(event, prompt, selfie, plan=photo_plan,
                                                skip_outfit_lock=True, refs=refs)
            else:
                image = await self._generate(event, prompt, selfie, plan=photo_plan,
                                             skip_outfit_lock=suppress_lock, refs=refs)
            if not image:
                self._stats["failed"] += 1
                await self._notify_failure(event, self._last_error or "生成失败", explicit=explicit)
                return
            photo_engine = getattr(self.plugin, "photo", None)
            localizer = getattr(photo_engine, "_localize", None)
            if callable(localizer):
                try:
                    image = await localizer(image)
                except Exception as _amo_exc:
                    _note_swallowed("draw_engine.DrawEngine._run", _amo_exc)
            self._cache_put(cache_key, image)
            if await self._send_image(event, image, caption=caption, plan=photo_plan):
                self._record(uid)
                self._stats["sent"] += 1
            else:
                self._stats["failed"] += 1
                await self._notify_failure(event, "发送失败", explicit=explicit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._stats["failed"] += 1
            self._last_error = str(exc)
            logger.warning("[amo] 画图失败: %s" % exc)
            await self._notify_failure(event, str(exc), explicit=explicit)
        finally:
            if umo:
                self._inflight.discard(umo)

    # ---------- OmniDraw ----------

    def _find_omni(self):
        """拿我能永远定格你插件实例；没装/没启用返回 None。"""
        return find_omni(self.plugin, self, "draw_engine.DrawEngine._find_omni")

    async def _generate(self, event, prompt, selfie=False, plan=None, skip_outfit_lock=False, refs=None):
        omni = self._find_omni()
        if omni is None:
            logger.warning("[amo] 没找到「我能永远定格你」（Astrbot_amoupup_photo），画不了。")
            return None
        args = {"prompt": prompt, "count": 1, "event": event, "record_usage": True}
        if refs:
            args["refs"] = list(refs)
            args["allow_local_refs"] = True
        if skip_outfit_lock:
            # B2c：显式按请求告诉 omnidraw 本次不用 OUTFIT LOCK（旧版本会 TypeError，
            # 下面的回退会把未知参数摘掉后重试）。
            args["skip_outfit_lock"] = True
        if selfie:
            args["mode"] = "selfie"
            try:
                from .photo_view import wants_foot_closeup as _wants_foot_neg
            except ImportError:
                from photo_view import wants_foot_closeup as _wants_foot_neg
            _foot = bool(_wants_foot_neg(prompt))
            args["negative_prompt"] = (FOOT_CLOSEUP_NEGATIVE if _foot else SELFIE_NEGATIVE) + "，" + REALISM_NEGATIVE
            if _foot:
                # 脚部特写不要套「举在脸前的自拍」模板（万象画卷 selfie 模式会硬加）
                args["skip_selfie_template"] = True
        else:
            args["negative_prompt"] = "人物，人类，插画，二次元，" + REALISM_NEGATIVE
        if plan is not None:
            try:
                scene_lock, pose_lock = locks_for_plan(plan)
            except Exception as _amo_exc:
                _note_swallowed("draw_engine.DrawEngine._generate.locks", _amo_exc)
                scene_lock = pose_lock = ""
            if scene_lock:
                args["scene_lock"] = scene_lock
            if pose_lock:
                args["pose_lock"] = pose_lock
        try:
            result = await asyncio.wait_for(
                omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
        except TypeError as exc:
            had_skip = args.pop("skip_outfit_lock", None) is not None
            had_scene = args.pop("scene_lock", None) is not None
            had_pose = args.pop("pose_lock", None) is not None
            had_foot = args.pop("skip_selfie_template", None) is not None
            if had_skip or had_scene or had_pose or had_foot:
                logger.warning("[amo] 我能永远定格你不支持场景锁，已退回普通提示词: %s" % exc)
                result = await asyncio.wait_for(
                    omni.generate_images_for_plugin(**args), timeout=GENERATE_TIMEOUT_SECONDS)
            else:
                raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._last_error = "生成超时（%d 秒）" % GENERATE_TIMEOUT_SECONDS
            logger.warning("[amo] 我能永远定格你生成超时（%d 秒）" % GENERATE_TIMEOUT_SECONDS)
            return None
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("[amo] 我能永远定格你生成失败: %s" % exc)
            return None
        if not isinstance(result, dict) or not result.get("success"):
            message = ""
            if isinstance(result, dict):
                message = str(result.get("message") or "")
            self._last_error = message or "我能永远定格你没出图"
            logger.warning("[amo] 我能永远定格你没出图：%s" % (message or "未知原因"))
            return None
        images = result.get("images") or []
        if not images:
            return None
        return images[0]

    # ---------- 发送 ----------

    @staticmethod
    def _image_component(item):
        return image_component(item)

    async def _send_image(self, event, item, caption="", plan=None):
        """画图结果发送：唯一入口 amo_media.send_photo（MediaGateway）。

        2026-11-22 task-23：send_photo 工具链路以前直接 event.send，不记账、不占
        每日配额、不 claim 事件、不写 memory，于是「发过照片」在聊天/记忆里查不到、
        同一天可远超 cap。现在和自动分享走同一条账本/出口：source=tool，
        用户明确要图不因 cap 拦截（计数但不在 CAP_ENFORCED_SOURCES 里）。
        """
        component = self._image_component(item)
        if component is None:
            logger.warning("[amo] 画图结果没有可发送的图片字段: %s" % list(item.keys()))
            return False
        if MessageChain is None:
            logger.warning("[amo] 画图发送失败：拿不到 MessageChain")
            return False
        # task-26：照片说明也过技术词黑名单；命中就只发图，不把半句后台话带出去。
        if caption:
            try:
                try:
                    from .amo_shared import sanitize_caption as _sanitize_caption
                except ImportError:
                    from amo_shared import sanitize_caption as _sanitize_caption
                caption = _sanitize_caption(caption)
            except Exception as exc:  # cleanup: 兜底/降级，保持原行为
                _note_swallowed("draw_engine._send_image", exc)
        plugin = getattr(self, "plugin", None)
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        activity = self._activity() if callable(getattr(self, "_activity", None)) else {}
        scene = {
            "event_id": str((activity or {}).get("event_id") or ""),
            "zone": str(getattr(plan, "zone", "") or (activity or {}).get("zone") or ""),
            "place": str(getattr(plan, "where", "") or (activity or {}).get("place") or ""),
        }
        pose = getattr(plan, "pose", None) if plan is not None else None
        outfit = ""
        try:
            getter = getattr(getattr(plugin, "photo", None), "outfit_today", None)
            outfit = str(getter() or "") if callable(getter) else ""
        except Exception as _amo_exc:
            _note_swallowed("draw_engine._send_image.outfit", _amo_exc)

        async def _transport():
            """宿主没有 _send_media_to_umo（单测/旧宿主）时的直接发送。"""
            parts = []
            if caption:
                try:
                    from astrbot.api.message_components import Plain
                except Exception:
                    try:
                        from astrbot.api.event.components import Plain
                    except Exception:  # cleanup: 拿不到 Plain 就只发图
                        Plain = None
                if Plain is not None:
                    parts.append(Plain(text=str(caption)))
            parts.append(component)
            await event.send(MessageChain(parts))

        try:
            result = await _media.send_photo(
                plugin, umo, item,
                caption=caption,
                origin="tool",
                kind="draw",
                event_id=_photo_turn_key(event),
                scene=scene,
                pose=pose,
                outfit=outfit,
                claim_event=True,
                dedupe=False,
                enforce_cap=False,
                count=True,
                transport=_transport,
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("[amo] 画图发送失败: %s" % exc)
            return False
        if not result.get("ok"):
            reason = str(result.get("reason") or "")
            self._last_error = reason or "send_failed"
            if reason not in ("dup_event", "send_blocked"):
                logger.warning("[amo] 画图未发出：%s", reason)
            return False
        return True

    # ---------- 状态 ----------

    def diagnostics(self):
        """没收到图时用来自查：开关、冷却、节点、上次报错。"""
        omni = self._find_omni()
        node_count = 0
        model = ""
        base = ""
        try:
            cfg = getattr(omni, "plugin_config", None) if omni is not None else None
            nodes = list(getattr(cfg, "providers", []) or [])
            node_count = len(nodes)
            if nodes:
                model = str(getattr(nodes[0], "model", "") or "")
                base = str(getattr(nodes[0], "base_url", "") or "")
        except Exception as _amo_exc:
            _note_swallowed("draw_engine.DrawEngine.diagnostics", _amo_exc)
        lines = [self.status_text()]
        lines.append("我能永远定格你：" + ("已连接" if omni is not None else "没找到（插件没装/没启用）"))
        lines.append("生图节点：%d 个%s" % (
            node_count,
            ("｜%s @ %s" % (model, base)) if node_count else "（还没配 key）",
        ))
        lines.append("上次错误：" + (self._last_error or "无"))
        return "\n".join(lines)

    def status_text(self):
        omni = self._find_omni()
        mode = self._mode()
        mode_text = {"topic": "话题触发", "explicit": "明确要图", "both": "话题+明确"}.get(mode, mode)
        return "\n".join([
            "🎨 画图：%s%s" % ("开" if self.enabled else "关", "（dry-run）" if self.dry_run else ""),
            "触发：" + mode_text,
            "冷却：%d 分钟｜上限 %d/小时、%d/天" % (
                self._cfg_int("draw_cooldown_minutes", 30),
                self._cfg_int("draw_max_per_hour", 3),
                self._cfg_int("draw_max_per_day", 10),
            ),
            "我能永远定格你：" + ("已连接" if omni is not None else "没找到（插件没装/没启用）"),
            "本轮统计：排 %d｜发 %d｜复用 %d｜失败 %d｜跳过 %d" % (
                self._stats["scheduled"], self._stats["sent"], self._stats["cached"],
                self._stats["failed"], self._stats["skipped"],
            ),
        ])
