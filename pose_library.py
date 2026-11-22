# -*- coding: utf-8 -*-
"""自拍姿势库（2026-11-12）。

她发自拍时从姿势库里随机挑一个姿势（托腮、比耶、撩头发…），不再每次都「随手拍」。
面板「姿势库」可以加/删姿势、保存，并能逐张生成「姿势参考图」给生图当参考。

数据：``plugin_data/Astrbot_amoupup_forever/poses.json``
参考图：``plugin_data/Astrbot_amoupup_forever/outfit_refs/姿势库/<姿势名>.png``
（和衣服参考图放在一起，备份 plugin_data 时统一带走。）
"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

from typing import Any

import asyncio
import json
import os
import random
import re
import threading
import time

from astrbot.api import logger

try:  # 原子写 JSON（tmp + fsync + os.replace），三个模块共用一份
    from .amo_fileio import atomic_write_json
except ImportError:  # pragma: no cover - 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_fileio import atomic_write_json

try:  # 复用服装参考图的解码/转 PNG，避免两份实现漂移
    from .outfit_refs import _as_png, _decode_item
except Exception:  # pragma: no cover
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from outfit_refs import _as_png, _decode_item
        except Exception:
            _as_png = _decode_item = None  # type: ignore[assignment]

try:
    from .amo_image_common import find_omni
except Exception:  # pragma: no cover
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from amo_image_common import find_omni
        except Exception:
            find_omni = None  # type: ignore[assignment]

OUTFIT_REF_DIR_NAME = "outfit_refs"
REF_DIR_NAME = "姿势库"
#: 老版本把姿势图放在 plugin_data/pose_refs 下，启动时自动搬到 outfit_refs/姿势库
LEGACY_REF_DIR_NAME = "pose_refs"
MAX_POSES = 60
_SAFE_RE = re.compile(r'[\\/:*?"<>|\r\n]+')

#: 姿势白名单（2026-11-22 task-23）：只有这张表里的姿势会进自动随机池，
#: 名字 → 房间池（bedroom/living/bath/any）。来源是 11-12 用户选的 9+3 个种子
#: 里安全的那些，加上从参考图库批量导入的 300 条里审核通过的「可爱/标准」系；
#: 成人向（挤胸/性感/开腿/顶胯/缠绵…）和杂技（倒立/桥式/团身/劈叉…）一律不进池。
#: MAX_POSES=60 是硬上限：加载/导入/保存都按它截断。
APPROVED_POSES = {
    # ---- 卧室：坐在/侧坐在床上的日常自拍（种子） ----
    "手放腿上坐正": "bedroom",
    "跪坐背挺直": "bedroom",
    "正坐看手机": "bedroom",
    "背坐看镜头": "bedroom",
    "双脚交叉坐": "bedroom",
    "单手撑床": "bedroom",
    "侧坐伸腿拍脚": "bedroom",
    "趴床翘脚": "bedroom",
    # ---- 客厅：坐着抬脚（种子） ----
    "沙发抬脚": "living",
    # ---- 上半身可爱系手势：不挑房间 ----
    "胸前双手比心": "any",
    "头顶双臂比心": "any",
    "脸侧手指比心": "any",
    "下巴下比双V": "any",
    "辣妹V（手背朝外比V）": "any",
    "握拳贴脸（撒娇）": "any",
    "兔耳手势": "any",
    "招财猫猫爪手势": "any",
    "咆哮手势（嗷呜）": "any",
    "嘘——（食指抵唇）": "any",
    "歪头＋手遮单眼（眨眼风）": "any",
    "飞吻": "any",
    "双掌托腮（花苞手势）": "any",
    "食指戳脸": "any",
    "萌袖风・双手掩口": "any",
    "拜托手势（双手合十）": "any",
    "抱紧自己・害羞姿势": "any",
    "手指取景框": "any",
    "将发丝挽至耳后": "any",
    "双手捧脸": "any",
    "敬礼姿势": "any",
    "手指镜头": "any",
    "打哈欠伸懒腰（单手掩口）": "any",
    "从双手指缝间窥视": "any",
    # ---- 标准系坐/站/蹲：客厅与日常居家 ----
    "站立・双手扶腰": "living",
    "站立・回眸顾盼": "living",
    "站立・双臂上举伸展": "living",
    "站立・单手抚后脑": "living",
    "站立・发丝飘扬": "living",
    "站立・踮脚双臂V字": "living",
    "蹲姿・手扶膝": "living",
    "蹲姿・托腮": "living",
    "蹲姿・手抚发": "living",
    "蹲姿・回头": "living",
    "蹲姿・踮脚双手触地": "living",
    "女孩坐・双手扶腿": "living",
    "侧坐・双腿斜向右侧": "living",
    "侧坐・手扶脸颊": "living",
    "女孩坐・手抚颈侧": "living",
    "女孩坐・双手后撑": "living",
    "后仰坐・单膝立起": "living",
    "双腿斜伸・手扶腰": "living",
    "双肘支撑斜倚坐": "living",
    "手抚脚踝": "living",
    "握脚踝・单膝立起": "living",
    "单手支撑・双腿斜向前伸": "living",
    "伸腿・侧肘支撑坐": "living",
    "正坐・撩发": "living",
    "单膝立起・手抚发": "living",
}

#: 成人向/杂技/私密词：名字或描述里出现就永远不进自动池（白名单之外的双保险）。
BANNED_WORDS = (
    "挤胸", "事业线", "性感", "大胆", "揉胸", "捧胸", "抚胸", "托胸", "遮胸",
    "胸伏", "胸贴", "沟壑", "臀", "胯", "开腿", "M字", "V字开腿", "劈叉",
    "横叉", "倒立", "桥式", "团身", "仰翻", "猫式", "四肢着地", "骑乘", "后入",
    "传教士", "缠绵", "双人", "贴身", "肩带", "大腿内侧", "腋下", "解挂钩",
    "遮掩", "顶胯", "邀约", "勾引", "撩拨", "自慰", "真空", "透视",
    "含住", "舔", "特殊", "欲",
)

#: zone → 允许的姿势房间池。空/公共场景不在表里 = 不用姿势库（规则姿势更贴场景）。
ZONE_ROOMS = {
    "home_bed": ("bedroom",),
    "home_sofa": ("living",),
    "home_meal": ("living",),
    "home_desk": ("living",),
    "home_generic": ("living",),
    "home": ("living",),
    "home_bath": ("bath",),
    "home_kitchen": ("bath",),
}
#: 房间中文名（日志用）。
ROOM_LABELS = {"bedroom": "卧室", "living": "客厅", "bath": "浴室",
               "kitchen": "厨房", "any": "通用"}

#: 内置姿势：(名字, 画面描述)。描述会直接进自拍提示词，写得越具体越不容易走形。
#: 2026-11-12 用户指定：只保留这 9 个（对着参考图里的 9 个姿势）。
SEED_POSES = (
    ("手放腿上坐正", "坐在床上，双腿并拢弯在身前，两只手放在腿上，背挺直，正对镜头"),
    ("背对抬腿", "背对镜头趴坐着，一条腿向后高高抬起，扭头回头看镜头"),
    ("跪姿翘臀叉腰", "单膝跪在床上，腰往下压、臀部微微翘起，一只手叉腰，另一只手拿手机自拍"),
    ("蹲坐手叉腰", "蹲坐在床上，双腿收在身前，一只手叉腰，另一只手举手机自拍"),
    ("跪坐背挺直", "跪坐在床上，背挺直，一只手撑在腿边，另一只手举手机自拍"),
    ("正坐看手机", "正坐在床上，双腿并拢，低头看着手机屏幕"),
    ("背坐看镜头", "背对镜头坐着，扭过头从肩膀上方看向镜头"),
    ("双脚交叉坐", "坐在床上，双腿交叉，一只手撑在身侧，另一只手举手机自拍"),
    ("单手撑床", "侧坐在床上，一只手撑在身后的床面，身体微微后仰，另一只手举手机自拍"),
    # 2026-11-21：用户要拍脚趾；俯拍脚部最容易多趾/融合，姿势本身先避免极端透视。
    # 全部按「斜侧角度 + 脚完整入镜」设计，自拍也能直接用。
    ("侧坐伸腿拍脚", "侧身坐在沙发或床上，双腿并拢向前伸直，脚背自然放松，"
                      "脚趾朝向镜头侧面，小腿到脚完整入镜，斜侧面角度，不要俯拍脚部特写"),
    ("趴床翘脚", "趴在床上，小腿向上弯起、脚背放松，回头从肩膀上方看向镜头，"
                  "双脚完整入镜，侧后方角度，不要俯拍脚部特写"),
    ("沙发抬脚", "坐在沙发边缘，双腿向前伸直并微微抬起，脚背自然绷直，"
                  "两只脚一起入镜，斜侧面角度，不要俯拍脚部特写"),
)

#: 拍脚专用姿势：拍脚/拍腿时只允许从这里选（2026-11-21 用户要求）
FOOT_POSE_NAMES = (
    "侧坐伸腿拍脚", "趴床翘脚", "沙发抬脚",          # 内置
    "手抚脚踝", "握脚踝・单膝立起",                  # 导入：脚踝正面入镜
    "站立・踮脚双臂V字", "蹲姿・踮脚双手触地",        # 导入：踮脚露脚趾
    # 导入：坐/跪/蹲位，脚心或脚趾明确朝外（按参考图逐张看过）
    "M字开腿（坐位・双手扶膝）", "坐姿・单腿高抬・手握脚踝", "伸腿・侧肘支撑坐",
    "坐姿・抱腿打开", "鸭子坐・双手前撑挤胸", "坐姿・单腿大幅外开（单M字）",
    "跪立・上体后仰・双手抱后脑", "双膝大幅外开的跪立・压胯下沉", "正坐・撩发",
    "骑乘风・开膝双手前撑・沉胯", "骑乘风・开膝沉胯・撩发", "仰卧・双膝抱至胸前",
    "踮脚蹲姿开腿・双手触地", "蹲姿开腿・双手抱头后", "单手支撑・双腿斜向前伸",
    "三角坐・双膝左倾", "单膝立起・手抚发",
)


def is_foot_pose(name):
    return str(name or "") in FOOT_POSE_NAMES


def foot_poses(poses):
    return [item for item in (poses or []) if is_foot_pose((item or {}).get("name"))]


#: 这些词说明动作需要双手/背面/躺卧/腾空，前置自拍做不出来 → 用虚空相机
_VOID_WORDS = ("双手", "背对", "背坐", "趴", "俯卧", "仰卧", "躺", "倒立", "桥式",
               "劈叉", "团身", "悬空", "后仰", "撑地", "触地", "前撑", "抱腿", "举腿")
_SELFIE_WORDS = ("手机", "自拍")


def camera_mode(pose):
    """这个姿势用前置自拍还是虚空相机（第三人称全身抓拍）。"""
    data = pose or {}
    text = str(data.get("name") or "") + str(data.get("note") or "")
    if any(word in text for word in _SELFIE_WORDS):
        return "selfie"
    if any(word in text for word in ("第三人称", "虚空", "全身抓拍")):
        return "void"
    return "void" if any(word in text for word in _VOID_WORDS) else "selfie"


def camera_line(pose):
    """按姿势返回相机视角描述：虚空相机 / 手机前置自拍。"""
    if camera_mode(pose) == "void":
        return ("第三人称虚空相机，全身构图，从头到脚完整入镜，"
                "不出现手机、不出现拿手机的手")
    return "手机前置自拍视角，近景半身，画面边缘可以有拿手机的手臂"


def match_pose(library, text, usage="selfie"):
    """用户明确说了某个姿势名（或近似词）时，返回那个姿势（默认单人）。"""
    body = str(text or "")
    if not body or library is None:
        return None
    best, best_score = None, 0
    for pose in library.poses():
        if usage_of(pose) not in (str(usage or "selfie"), "both"):
            continue
        name = str(pose.get("name") or "")
        if not name:
            continue
        if name in body:
            return pose
        score = sum(1 for index in range(len(name) - 1) if name[index:index + 2] in body)
        if score > best_score:
            best, best_score = pose, score
    return best if best_score >= 2 else None


def usage_of(pose):
    """这个姿势给谁用：selfie=单人自拍，couple=双人合拍，both=都行。"""
    data = pose or {}
    value = str(data.get("usage") or "").strip()
    if value in ("selfie", "couple", "both"):
        return value
    return "couple" if "双人" in str(data.get("note") or "") else "selfie"


# ---------------------------------------------------------------- 白名单/房间池

#: 参考图库批量导入时带的分类前缀（"上半身单人·挤胸・强调事业线系：" 之类）。
#: 它只是图库目录名，不是动作描述；判断内容安全/写提示词前先剥掉，避免把
#: "性感系/挤胸系" 这种分类词带进提示词。
_CATALOG_PREFIX_RE = re.compile(r"^（[^）]*）")
_CATALOG_HEADS = ("系", "单人", "双人", "缠绵", "特殊")


def clean_pose_note(note):
    """去掉图库分类前缀，只留真正的姿势描述（旧格式/自由文本原样返回）。"""
    raw = str(note or "").strip()
    text = _CATALOG_PREFIX_RE.sub("", raw).strip()
    head, sep, tail = text.partition("：")
    if sep and any(word in head for word in _CATALOG_HEADS):
        text = tail.strip()
    return text or raw


def pose_text_of(pose):
    """名字 + 清洗后的描述拼一起（白名单/房间判定与提示词同口径）。"""
    data = pose if isinstance(pose, dict) else {}
    return str(data.get("name") or "") + "：" + clean_pose_note(data.get("note"))


def is_banned_pose(pose):
    """成人向/杂技姿势：名字或描述命中黑名单词就不许进自动池。"""
    body = pose_text_of(pose)
    return any(word in body for word in BANNED_WORDS)


def room_of_text(pose):
    """按词判断姿势属于哪个房间（只给面板新加的、没有 whitelist 标签的姿势兜底）。"""
    body = pose_text_of(pose)
    if any(word in body for word in ("床", "被窝", "枕", "躺", "趴", "卧")):
        return "bedroom"
    if any(word in body for word in ("沙发", "客厅", "茶几")):
        return "living"
    if any(word in body for word in ("浴室", "洗手台", "镜子", "洗澡", "洗漱")):
        return "bath"
    if any(word in body for word in ("厨房", "灶台", "做饭")):
        return "kitchen"
    if any(word in body for word in ("街", "户外", "地铁", "公园", "景点")):
        return "outdoor"
    return "any"


def pose_room(pose):
    """姿势所属房间池：白名单优先，其次显式 approved 的按描述分，其余 None。"""
    data = pose if isinstance(pose, dict) else {}
    name = str(data.get("name") or "").strip()
    if name in APPROVED_POSES:
        return APPROVED_POSES[name]
    if data.get("approved") and not is_banned_pose(data):
        return str(data.get("room") or "") or room_of_text(data)
    return ""


def zone_rooms(zone):
    """zone → 允许的姿势房间池；公共/未知场景返回空 tuple（不用姿势库）。"""
    return tuple(ZONE_ROOMS.get(str(zone or "").strip(), ()))


def approved_poses(poses, limit=MAX_POSES):
    """把姿势列表过一遍白名单 + 黑名单，返回 ≤MAX_POSES 的自动池副本（带 room）。

    库里一条白名单姿势都没有时（测试桩/极简库）退回「非黑名单」旧行为，
    否则全新安装只有种子、没有白名单标签时会一张都选不出来。
    """
    items = []
    explicit = []
    fallback = []
    for pose in list(poses or []):
        if not isinstance(pose, dict):
            continue
        name = str(pose.get("name") or "").strip()
        if not name:
            continue
        # 先剥图库分类前缀再判黑名单：分类名里的「性感系」不算姿势本身性感。
        item = dict(pose)
        item["note"] = clean_pose_note(item.get("note"))
        if is_banned_pose(item):
            continue
        room = pose_room(item)
        if not room:
            # 没有白名单标签、也不是面板显式批准的：只在「一个白名单都没有」的
            # 极简库（单测桩/全新安装）里按描述分房间兜底，绝不混进生产池。
            fallback_room = room_of_text(item)
            item["room"] = fallback_room
            item["approved"] = False
            fallback.append(item)
            continue
        item["room"] = room
        item["approved"] = True
        items.append(item)
        if str(name) in APPROVED_POSES:
            explicit.append(item)
    if explicit:
        return explicit[:max(1, int(limit or MAX_POSES))]
    if items:
        return items[:max(1, int(limit or MAX_POSES))]
    return fallback[:max(1, int(limit or MAX_POSES))]


def pose_fits_zone(pose, zone):
    """这个姿势能不能用在这个 zone（合拍/显式点姿势时校验）。"""
    room = pose_room(pose)
    if not room:
        return False
    rooms = zone_rooms(zone)
    if not rooms:
        return False
    return room == "any" or room in rooms


def allowed_foot_poses(poses, limit=MAX_POSES):
    """拍脚专用池同样只留白名单里的姿势（M字/开腿/挤胸这些不再进）。"""
    approved = {str(item.get("name") or "") for item in approved_poses(poses, limit)}
    return [dict(pose) for pose in list(poses or [])
            if isinstance(pose, dict) and str(pose.get("name") or "") in approved
            and is_foot_pose(pose.get("name"))]


def trim_poses_data(data, limit=MAX_POSES):
    """把整份 poses.json 收敛成白名单 ≤limit 条（迁移脚本用；纯函数不改磁盘）。

    返回 (新 data, 被移除的姿势名列表)。白名单按 APPROVED_POSES 顺序排，
    保留原有 id/created_at/usage 等字段，并写入 approved/room 便于面板展示。
    """
    payload = dict(data or {})
    original = [pose for pose in (payload.get("poses") or []) if isinstance(pose, dict)]
    by_name = {str(pose.get("name") or "").strip(): pose for pose in original}
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for name, room in APPROVED_POSES.items():
        if len(kept) >= max(1, int(limit or MAX_POSES)):
            break
        pose = by_name.get(name)
        if pose is None:
            continue
        item = dict(pose)
        item["note"] = clean_pose_note(item.get("note"))
        item["approved"] = True
        item["room"] = room
        kept.append(item)
    kept_names = {str(pose.get("name") or "").strip() for pose in kept}
    for pose in original:
        name = str(pose.get("name") or "").strip()
        if name and name not in kept_names:
            removed.append(name)
    payload["poses"] = kept
    payload.setdefault("version", 1)
    return payload, removed


#: 旧种子（2026-11-12 之前那批）；升级时从库里删掉，同时删掉它们的参考图
OLD_SEED_NAMES = (
    "比耶", "托腮", "撩头发", "歪头笑", "抿嘴笑", "眯眼笑", "嘟嘴", "咬唇", "回眸",
    "侧脸", "低头看镜头", "手机挡半张脸", "单手撑脸", "靠墙", "抱膝坐", "趴桌",
    "伸懒腰", "整理头发", "喝奶茶", "抱着花", "抱枕头", "戴耳机", "眨眼", "比心",
    "探头", "抬手挡阳光",
    # 2026-11-21 用户要求删掉的三张（参考图畸形）：升级时从库和文件夹一起清掉
    "盘腿露脚心", "单脚抬起看镜头", "脚搭矮凳",
)

def safe_name(name):
    text = _SAFE_RE.sub("_", str(name or "").strip())
    return text[:40].strip(" .") or "pose"


def _now_ts():
    return _amo_clock.now_ts()


def _new_id():
    return "p-%x" % random.randrange(1 << 40)


class PoseLibrary:
    VERSION = 1
    SEED_VERSION = 5

    def __init__(self, plugin):
        self.plugin = plugin
        self._lock = threading.RLock()
        self._data = None
        base = ""
        try:
            base = os.path.dirname(str(getattr(plugin.db, "path", "") or ""))
        except Exception:
            base = ""
        if not base:
            base = str(getattr(plugin, "plugin_dir", "") or ".")
        self.base = base
        self.path = os.path.join(base, "poses.json")
        # 姿势图跟衣服参考图放一起：outfit_refs/姿势库/<姿势名>.png
        self.ref_dir = os.path.join(base, OUTFIT_REF_DIR_NAME, REF_DIR_NAME)
        self.legacy_ref_dir = os.path.join(base, LEGACY_REF_DIR_NAME)
        self._migrate_legacy_refs()
        logger.info("[amo] 姿势库已加载（%s）" % self.path)

    def _migrate_legacy_refs(self):
        """把旧版 plugin_data/pose_refs/*.png 搬进 outfit_refs/姿势库/（只搬一次）。"""
        try:
            if not os.path.isdir(self.legacy_ref_dir):
                return
            for name in os.listdir(self.legacy_ref_dir):
                if not name.lower().endswith(".png"):
                    continue
                source = os.path.join(self.legacy_ref_dir, name)
                target = os.path.join(self.ref_dir, name)
                if not os.path.isfile(source) or os.path.isfile(target):
                    continue
                os.makedirs(self.ref_dir, exist_ok=True)
                os.replace(source, target)
                logger.info("[amo] 姿势参考图已搬到 outfit_refs/姿势库：%s" % name)
            try:
                if not os.listdir(self.legacy_ref_dir):
                    os.rmdir(self.legacy_ref_dir)
            except OSError as exc:
                logger.debug("[amo] optional step failed: %r", exc)
        except Exception as exc:
            logger.warning("[amo] 姿势参考图搬迁失败: %s" % exc)

    # ---------- 存储 ----------

    def _empty(self):
        return {"version": self.VERSION, "seed_version": 0, "poses": []}

    def _limit_poses(self, poses):
        """MAX_POSES 硬上限：白名单姿势优先保留，其余按原顺序补到 60 条。"""
        items = [dict(pose) for pose in (poses or []) if isinstance(pose, dict)
                 and str(pose.get("name") or "").strip()]
        preferred = [pose for pose in items
                     if str(pose.get("name") or "").strip() in APPROVED_POSES]
        preferred_names = {str(pose.get("name") or "").strip() for pose in preferred}
        rest = [pose for pose in items
                if str(pose.get("name") or "").strip() not in preferred_names]
        kept, seen = [], set()
        for pose in preferred + rest:
            name = str(pose.get("name") or "").strip()
            if name in seen:
                continue
            seen.add(name)
            kept.append(pose)
            if len(kept) >= MAX_POSES:
                break
        return kept

    def allowed_poses(self):
        """自动随机池：白名单 + 黑名单过滤后的姿势（≤MAX_POSES）。"""
        return approved_poses(self.poses(), MAX_POSES)

    def allowed_foot_poses(self):
        """拍脚专用池（同样只留白名单里的姿势）。"""
        return allowed_foot_poses(self.poses(), MAX_POSES)

    def _load(self):
        with self._lock:
            if self._data is not None:
                return self._data
            data = None
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except FileNotFoundError:
                data = None
            except Exception as exc:
                logger.warning("[amo] 姿势库读取失败: %s" % exc)
                data = None
            if not isinstance(data, dict) or not isinstance(data.get("poses"), list):
                data = self._empty()
            data.setdefault("version", self.VERSION)
            data.setdefault("poses", [])
            if int(data.get("seed_version") or 0) < self.SEED_VERSION:
                # 只在版本升级时补种子：用户手动删过的种子姿势不能复活
                data = self._merge_seed(data)
                data["seed_version"] = self.SEED_VERSION
                self._save(data)
            # 2026-11-22 task-23：MAX_POSES 在加载处强制执行（以前只有 add/save_all
            # 截断，批量导入的 300 条照样全进 home 池）。白名单姿势排前面优先保留。
            before = len(data.get("poses") or [])
            data["poses"] = self._limit_poses(data.get("poses") or [])
            if before > MAX_POSES:
                logger.warning("[amo] 姿势库超过上限：%d → %d（MAX_POSES=%d，"
                               "白名单外的多余条目本次加载不再进池）",
                               before, len(data["poses"]), MAX_POSES)
            self._data = data
            return data

    def _merge_seed(self, data):
        """升级种子：删掉旧种子（连同参考图），补上新的这 9 个姿势。"""
        keep = {name for name, _ in SEED_POSES}
        kept = []
        removed = []
        for pose in data.get("poses") or []:
            name = str(pose.get("name") or "").strip()
            if name in OLD_SEED_NAMES and name not in keep:
                removed.append(pose)
                continue
            kept.append(pose)
        data["poses"] = kept
        for pose in removed:
            self._remove_ref(pose)
        existing = {str(p.get("name") or "").strip() for p in kept}
        for name, note in SEED_POSES:
            if name in existing:
                continue
            data["poses"].append({
                "id": _new_id(), "name": name, "note": note, "created_at": _now_ts(),
            })
        return data

    def _remove_ref(self, pose):
        """删除某个姿势的参考图（删姿势时一起删掉，别留孤儿文件）。"""
        try:
            path = self.ref_path(pose)
            if os.path.isfile(path):
                os.remove(path)
                logger.info("[amo] 姿势参考图已删除：%s" % path)
                return True
        except Exception as exc:
            logger.warning("[amo] 姿势参考图删除失败: %s" % exc)
        return False

    def _save(self, data=None):
        data = data if isinstance(data, dict) else self._load()
        with self._lock:
            try:
                atomic_write_json(self.path, data)
                self._data = data
                return True
            except Exception as exc:
                logger.warning("[amo] 姿势库保存失败: %s" % exc)
                return False

    # ---------- 查询 ----------

    def poses(self):
        return list(self._load().get("poses") or [])

    def has_ref(self, pose):
        try:
            return os.path.isfile(self.ref_path(pose)) and os.path.getsize(self.ref_path(pose)) > 0
        except Exception:
            return False

    def ref_path(self, pose):
        pose = pose if isinstance(pose, dict) else {}
        return os.path.join(self.ref_dir, safe_name(pose.get("name")) + ".png")

    def ref_for(self, pose):
        """已有的姿势参考图路径；没有就空串（不在这里现生成，避免拖慢出图）。"""
        path = self.ref_path(pose)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        return ""

    def snapshot(self):
        poses = []
        for pose in self.poses():
            room = pose_room(pose)
            poses.append({
                "id": str(pose.get("id") or ""),
                "name": str(pose.get("name") or ""),
                "note": str(pose.get("note") or ""),
                "has_ref": self.has_ref(pose),
                "room": room,
                "approved": bool(room),
            })
        with_ref = len([p for p in poses if p["has_ref"]])
        return {"poses": poses, "count": len(poses), "with_ref": with_ref,
                "max": MAX_POSES}

    #: 描述里带这些词 = 床/跪/抬腿/翘臀类私密姿势，只能在卧室/客厅沙发用
    BED_WORDS = ("床", "被窝", "枕", "趴坐", "跪", "抬腿", "翘臀", "蹲坐")
    #: 允许上面那 9 种床姿的私密居家 zone；浴室/厨房/户外一律剔除
    PRIVATE_ZONES = ("home", "home_bed", "home_sofa")

    def is_private_pose(self, pose):
        """这个姿势是不是床/跪/抬腿类（只能在私密居家用）。"""
        text = str((pose or {}).get("note") or "") + str((pose or {}).get("name") or "")
        return any(word in text for word in self.BED_WORDS)

    def random_pose(self, zone=None, hint="", usage="selfie"):
        """从「当前房间池」随机挑一个姿势（2026-11-22 task-23 白名单版）。

        - 只用 APPROVED_POSES 白名单里、且没命中成人/杂技黑名单的姿势；
        - 按 zone 分池：home_bed→卧室，home_sofa/home_meal/home_desk→客厅，
          home_bath→浴室，通用手势（any）到哪都用；
        - 公共/未知场景（road/mall/office/…）返回 None，由场景规则给姿势，
          不再把床姿套到地铁/商场；
        - 同一天不连续用同一个姿势：先剔掉上一次用过的，再随机。
        """
        approved = self.allowed_poses()
        if not approved:
            logger.info("[amo] 姿势库没有可用的白名单姿势，本次用通用姿势文字")
            return None
        key = str(zone or "").strip()
        if not key:
            # 没有场景信息（面板/显式调用）：全白名单池
            pool = approved
        else:
            rooms = zone_rooms(key)
            if not rooms:
                logger.info("[amo] 场景 %s 不用姿势库（公共/未知场景，按规则姿势）", key)
                return None
            pool = [pose for pose in approved
                    if pose.get("room") == "any" or pose.get("room") in rooms]
        usage = str(usage or "selfie")
        pool = [pose for pose in pool if usage_of(pose) in (usage, "both")]
        if not pool:
            logger.info("[amo] 场景 %s 没有合适的白名单姿势（room=%s），本次用通用姿势",
                        key or "未知", ",".join(zone_rooms(key)) or "-")
            return None
        _hint = str(hint or "").strip()
        if _hint:
            words = [w for w in re.split(r"[，,。！？\s、/]+", _hint) if len(w) >= 2]
            words += [w for w in ("躺", "坐", "站", "蹲", "趴", "跪", "走", "靠", "侧身",
                                  "回眸", "托腮", "抱", "伸", "抬") if w in _hint]
            def _score(pose):
                text = str(pose.get("name") or "") + str(pose.get("note") or "")
                return sum(1 for word in words if word in text)
            scored = [(_score(pose), pose) for pose in pool]
            best = max((score for score, _pose in scored), default=0)
            if best > 0:
                pool = [pose for score, pose in scored if score == best]
        last = str(getattr(self, "_last_pose_name", "") or "")
        pick_from = [p for p in pool if str(p.get("name") or "") != last] or pool
        chosen = dict(random.choice(pick_from))
        self._last_pose_name = str(chosen.get("name") or "")
        logger.info("[amo] 姿势选择：%s（zone=%s room=%s，候选 %d 个，白名单 %d 条%s）",
                    self._last_pose_name, key or "-", chosen.get("room") or "-",
                    len(pick_from), len(approved),
                    "，避开上一次" if last and last != self._last_pose_name else "")
        return chosen

    # ---------- 修改 ----------

    def add(self, name, note=""):
        name = str(name or "").strip()[:40]
        note = str(note or "").strip()[:120]
        if not name:
            return {"ok": False, "msg": "先写个姿势名字，比如：托腮笑"}
        with self._lock:
            data = self._load()
            if len(data.get("poses") or []) >= MAX_POSES:
                return {"ok": False, "msg": "姿势最多 %d 个，先删几个" % MAX_POSES}
            if any(str(p.get("name") or "").strip() == name for p in data.get("poses") or []):
                return {"ok": False, "msg": "已经有「%s」这个姿势了" % name}
            candidate = {"id": _new_id(), "name": name, "note": note,
                         "created_at": _now_ts()}
            if is_banned_pose(candidate):
                return {"ok": False, "msg": "这个姿势的描述不适合放进自动池"}
            candidate["approved"] = True
            candidate["room"] = APPROVED_POSES.get(name) or room_of_text(candidate)
            data["poses"].append(candidate)
            if not self._save(data):
                return {"ok": False, "msg": "保存失败，看日志"}
            return {"ok": True, "msg": "已添加"}

    def delete(self, pose_id):
        pose_id = str(pose_id or "").strip()
        with self._lock:
            data = self._load()
            poses = list(data.get("poses") or [])
            gone = [p for p in poses if str(p.get("id") or "") == pose_id]
            keep = [p for p in poses if str(p.get("id") or "") != pose_id]
            if len(keep) == len(poses):
                return {"ok": False, "msg": "没找到这个姿势"}
            data["poses"] = keep
            if not self._save(data):
                return {"ok": False, "msg": "保存失败，看日志"}
            # 姿势删了，参考图也一起删掉
            for pose in gone:
                self._remove_ref(pose)
            return {"ok": True, "msg": "已删除"}

    def save_all(self, items):
        """面板「保存」：整份替换（按名字保留原来的 id / 创建时间）。"""
        items = items if isinstance(items, list) else []
        cleaned = []
        seen = set()
        for item in items[:MAX_POSES]:
            if isinstance(item, str):
                name, note = item, ""
            elif isinstance(item, dict):
                name, note = str(item.get("name") or ""), str(item.get("note") or "")
            else:
                continue
            name = name.strip()[:40]
            note = note.strip()[:120]
            if not name or name in seen:
                continue
            seen.add(name)
            cleaned.append((name, note))
        if not cleaned:
            return {"ok": False, "msg": "至少要留一个姿势"}
        with self._lock:
            data = self._load()
            old = {str(p.get("name") or "").strip(): p for p in data.get("poses") or []}
            poses = []
            for name, note in cleaned:
                base = old.get(name) or {}
                item = {
                    "id": str(base.get("id") or _new_id()),
                    "name": name,
                    "note": note or str(base.get("note") or ""),
                    "created_at": float(base.get("created_at") or _now_ts()),
                }
                # 2026-11-22 task-23：面板保存也过白名单——命中成人/杂技词的条目不进自动池。
                if is_banned_pose(item):
                    item["approved"] = False
                else:
                    item["approved"] = True
                    item["room"] = APPROVED_POSES.get(name) or room_of_text(item)
                poses.append(item)
            data["poses"] = poses
            if not self._save(data):
                return {"ok": False, "msg": "保存失败，看日志"}
            # 被删掉的姿势，参考图一起清掉
            keep = {pose["name"] for pose in poses}
            for name, pose in old.items():
                if name not in keep:
                    self._remove_ref(pose)
            return {"ok": True, "msg": "已保存 %d 个姿势" % len(poses)}


# ---------------------------------------------------------------- 参考图

_ref_lock = asyncio.Lock()
_ref_state: dict[str, Any] = {"running": False, "total": 0, "done": 0, "current": "", "failed": []}


#: 姿势参考图的取景：脚/腿相关的姿势必须拍到脚，否则参考图对生图没用
_FOOT_POSE_WORDS = ("脚", "脚趾", "脚背", "赤足", "光脚", "腿", "足")


def pose_ref_framing(note):
    """姿势参考图的取景范围：拍脚/腿的姿势要全身，其它胸口以上。"""
    body = str(note or "")
    if any(word in body for word in _FOOT_POSE_WORDS):
        return "全身入镜，从头顶到脚完整取景，脚和腿不要被裁掉"
    return "从胸口以上取景"


#: 拍脚参考图的解剖硬约束（2026-11-21：生成的参考图本身就多趾/融合趾）
FOOT_REF_GUARD = (
    "解剖严格：每只脚正好 5 根脚趾，脚趾分明、不融合、不多趾、不少趾；"
    "脚踝、脚跟、脚掌结构自然；脚不要正对镜头大特写，脚在画面里只占一小部分；"
    "画面里只有两只脚、两只手；不要出现畸形脚、脚趾粘连、多余脚趾。"
)


def pose_ref_prompt(name, note):
    """姿势参考图提示词：拍脚姿势加解剖约束，避免参考图本身畸形。"""
    body = str(note or "")
    guard = FOOT_REF_GUARD if any(word in body for word in _FOOT_POSE_WORDS) else ""
    return ("自拍姿势参考图：%s。%s。%s。同一个人，手机前置自拍构图，%s，"
            "动作和手的位置清楚，浅灰色纯色背景，自然光，不出现文字、不出现第二个人。"
            % (name, body, guard, pose_ref_framing(body)))


def batch_status():
    return dict(_ref_state)


def pending_poses(library):
    return [pose for pose in library.poses() if not library.has_ref(pose)]


async def ensure_pose_ref(plugin, pose, force=False):
    """生成一张「她做这个姿势」的参考图，返回路径；失败返回空串。"""
    pose = pose if isinstance(pose, dict) else {}
    name = str(pose.get("name") or "").strip()
    if not name:
        return ""
    library = getattr(plugin, "pose", None)
    if library is None:
        return ""
    path = library.ref_path(pose)
    if os.path.isfile(path) and os.path.getsize(path) > 0 and not force:
        return path
    omni = find_omni(plugin, plugin, "pose_library.ensure_pose_ref") if find_omni is not None else None
    if omni is None:
        logger.warning("[amo] 姿势参考图：没找到我能永远定格你")
        return ""
    refs = []
    photo = getattr(plugin, "photo", None)
    getter = getattr(photo, "persona_refs", None) if photo is not None else None
    if callable(getter):
        try:
            refs = [str(item) for item in (getter() or []) if item]
        except Exception:
            refs = []
    note = str(pose.get("note") or "").strip()
    prompt = pose_ref_prompt(name, note)
    args = {"prompt": prompt, "count": 1, "record_usage": False}
    if refs:
        args["refs"] = refs
        args["allow_local_refs"] = True
    try:
        result = await asyncio.wait_for(omni.generate_images_for_plugin(**args), timeout=120)
    except asyncio.TimeoutError:
        logger.warning("[amo] 姿势参考图生成超时（%s）" % name)
        return ""
    except Exception as exc:
        logger.warning("[amo] 姿势参考图生成失败（%s）: %s" % (name, exc))
        return ""
    images = result.get("images") if isinstance(result, dict) else None
    if not images:
        return ""
    data = _decode_item(images[0]) if _decode_item is not None else b""
    if not data:
        url = str(images[0].get("url") or images[0].get("image_url") or "")
        if url:
            try:
                import aiohttp

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
            except Exception:
                data = b""
    data = _as_png(data) if _as_png is not None else data
    if not data:
        logger.warning("[amo] 姿势参考图内容为空（%s）" % name)
        return ""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        logger.info("[amo] 姿势参考图已生成：%s" % path)
        return path
    except Exception as exc:
        logger.warning("[amo] 姿势参考图保存失败（%s）: %s" % (name, exc))
        return ""


async def ensure_batch(plugin, poses, force=False):
    """逐张生成，确认落盘后再做下一张；已有文件跳过。"""
    async with _ref_lock:
        if _ref_state["running"]:
            return {"ok": False, "msg": "已经在生成了", "state": batch_status()}
        _ref_state.update({"running": True, "total": len(poses), "done": 0,
                           "current": "", "failed": []})
    try:
        done = 0
        for index, pose in enumerate(poses, start=1):
            name = str(pose.get("name") or "")
            _ref_state["current"] = name
            path = await ensure_pose_ref(plugin, pose, force=force)
            if path:
                done += 1
                logger.info("[amo] 姿势参考图进度 %d/%d：%s" % (index, len(poses), name))
            else:
                _ref_state["failed"] = list(_ref_state["failed"]) + [name]
            _ref_state["done"] = done
            await asyncio.sleep(0.3)
        return {"ok": True, "done": done, "failed": list(_ref_state["failed"]),
                "total": len(poses)}
    finally:
        async with _ref_lock:
            _ref_state["running"] = False
            _ref_state["current"] = ""


def pose_line(pose):
    """姿势 → 提示词里的一小段（图库分类前缀先剥掉，只留动作）。"""
    pose = pose if isinstance(pose, dict) else {}
    name = str(pose.get("name") or "").strip()
    note = clean_pose_note(pose.get("note"))
    if not name:
        return ""
    return "姿势：" + name + (("（" + note + "）") if note else "")
