# -*- coding: utf-8 -*-
"""一次生图的计划对象与唯一入口 build_plan()（2026-11-16 Phase 9）。

改造前 photo_rules 同时装着规则表和"计划"，photo_engine 的 _share /
smart_prompt / _prompt_for 又各自 resolve_plan 一次：一次生图里同一个计划可能被
解析两遍，英文锁 / 姿势 / 参考图策略的来源散在三个函数里，改一处规则要满仓库
找引用。

现在职责切开：

- photo_rules：只留规则表（SCENE_RULES / SCENE_EN / POSE_EN / PLACE_ZONE_RULES …）
  和纯映射函数（zone_for_place / clean_place / _scene_key / _guard_line /
  pose_text）；它用模块级 __getattr__ 惰性 re-export 本模块的旧名字，既有
  from photo_rules import resolve_plan 不断；
- photo_plan（本模块）：PhotoPlan + build_plan() 唯一入口，外加
  pick_pose_for_plan() / locks_for_plan()——它们只读写 plan，不碰引擎。

字段分两类（见 PhotoPlan 注释）：

- build_plan 定型：scene_key / scene / pose_rule / pose_line / guard / props /
  banned / allow_library_pose / source / reason / explicit_place /
  explicit_conflict / repairs；
- 下游写回：pose + pose_ready + pose_line（pick_pose_for_plan 写）、locks
  （locks_for_plan 写）、where / light（photo_engine 拿到 scene_for 和当前时刻的
  光线后写）、debug（生图链路的调试补充）。

refs_policy 只描述「这类场景允许哪些参考图」，张数仍由 photo_engine 决定：
这次迁移只收敛"计划"的来源，不改变出图行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

try:  # 包内导入（插件运行态）
    from .photo_rules import (
        EXPLICIT_PLACE_ZONES,
        POSE_EN,
        SCENE_EN,
        SCENE_RULES,
        _guard_line,
        _scene_key,
        clean_place,
        pose_text,
        resolve_zone,
        zone_for_place,
    )
except ImportError:  # 直接按文件加载时（单测 / work 下的工具脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_rules import (
            EXPLICIT_PLACE_ZONES,
            POSE_EN,
            SCENE_EN,
            SCENE_RULES,
            _guard_line,
            _scene_key,
            clean_place,
            pose_text,
            resolve_zone,
            zone_for_place,
        )


#: 自拍类场景允许的参考图：只描述"允许哪些"，张数由 photo_engine 决定。
#: room 恒 False —— 房间参考图会把构图往"空房间"带（2026-11-11 那次"床+一只手"）。
REFS_POLICY_SELFIE = {"outfit": True, "pose": True, "room": False}


def _jsonable(value: Any) -> Any:
    """把计划里的字段变成纯 JSON（tuple→list，未知类型→str）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


@dataclass
class PhotoPlan:
    """一次生图的完整计划：地点、场景、姿势、守卫、英文锁、参考图策略都在这里。

    读写约定（谁写哪几个字段，别乱改）：

    - build_plan() 写：scene_key/base_zone/scene/pose_rule/pose_line/guard/
      props/banned/allow_library_pose/source/reason/activity/explicit_place/
      explicit_conflict/repairs/refs_policy/locks/debug；
    - pick_pose_for_plan() 写：pose/pose_ready/pose_line/pose_rule/locks；
    - locks_for_plan() 写：locks；
    - photo_engine 写：where/light/debug（需要 scene_for / 当前时刻 / 参考图张数
      这些运行期信息，规则表算不出来）；
    - 其余一律只读。
    """

    #: 规则表 key（老名字 zone 是它的读写别名）
    scene_key: str = "unknown"
    #: 日程/显式地点给的原始 zone（road / home / …）
    base_zone: str = ""
    #: 中文场景描述（进提示词）
    scene: str = ""
    #: 规则里的姿势 key（home_* 用姿势库时变 "library"）
    pose_rule: str = "stand"
    #: 中文姿势句（进提示词）
    pose_line: str = ""
    #: 中文守卫句（只存在什么 + 不要什么）
    guard: str = ""
    #: 允许出现的陈设
    props: tuple = ()
    #: 禁止项 token
    banned: tuple = ()
    #: 这类场景是否允许用姿势库
    allow_library_pose: bool = False
    #: 计划来源：schedule / explicit_place / no_schedule / unknown_zone
    source: str = "unknown"
    #: 来源的人话解释（进 [PHOTO_DEBUG] / 面板）
    reason: str = ""
    #: 纠偏后的日程原件
    activity: dict = field(default_factory=dict)
    #: 他嘴里的地点（只做兜底和冲突记录）
    explicit_place: str = ""
    #: 他说的地方和日程打架（日程赢）
    explicit_conflict: bool = False
    #: normalize_activity 修过的 (旧 zone, 新 zone)
    repairs: list = field(default_factory=list)

    # ---- 下游写回（默认值 = 还没选 / 还没算） ----
    #: 选中的姿势库条目（dict；没用库就是 None）
    pose: Any = None
    #: 姿势是否已经定过（pick_pose_for_plan 只写一次，重试不再重摇）
    pose_ready: bool = False
    #: 这次生图的地点文本（photo_engine 写）
    where: str = ""
    #: 这个时刻的光线文本（photo_engine 写）
    light: str = ""
    #: 参考图策略：{"outfit": bool, "pose": bool, "room": bool}
    refs_policy: dict = field(default_factory=lambda: dict(REFS_POLICY_SELFIE))
    #: 英文锁 (scene_lock, pose_lock)，由 locks_for_plan 写
    locks: tuple = ("", "")
    #: 运行期调试补充（photo_engine 写）
    debug: dict = field(default_factory=dict)

    @property
    def zone(self) -> str:
        """老名字：plan.zone 就是 plan.scene_key，读和写都落到同一份。"""
        return self.scene_key

    @zone.setter
    def zone(self, value: Any) -> None:
        self.scene_key = str(value or "")

    @property
    def bed_scene(self) -> bool:
        """床/沙发场景（穿搭参考图和负向词都按它走，photo_engine 不再自己判 zone）。"""
        return self.scene_key in ("home_bed", "home_sofa")

    def outfit_keys(self) -> tuple:
        """这套场景该带哪几件衣服做参考：床/沙发场景去掉鞋和硬外套。"""
        if self.bed_scene:
            return ("top", "skirt", "bottom")
        return ("coat", "top", "skirt", "bottom", "shoes")

    def refresh_locks(self) -> tuple:
        """按当前 scene_key + pose_rule 重算英文锁并写回 plan.locks。"""
        return locks_for_plan(self)

    def debug_dict(self) -> dict:
        """给 [PHOTO_DEBUG] / 面板用：字段定死、可以直接 json.dumps。"""
        scene_lock, pose_lock = locks_for_plan(self)
        return {
            "scene_key": self.scene_key,
            "base_zone": self.base_zone,
            "scene": self.scene,
            "pose": _jsonable(self.pose),
            "pose_rule": self.pose_rule,
            "pose_line": self.pose_line,
            "pose_ready": bool(self.pose_ready),
            "guard": self.guard,
            "props": list(self.props),
            "banned": list(self.banned),
            "allow_library_pose": bool(self.allow_library_pose),
            "source": self.source,
            "reason": self.reason,
            "activity": _jsonable(self.activity),
            "explicit_place": self.explicit_place,
            "explicit_conflict": bool(self.explicit_conflict),
            "repairs": _jsonable(self.repairs),
            "where": self.where,
            "light": self.light,
            "refs_policy": _jsonable(self.refs_policy),
            "locks": {"scene_lock": scene_lock, "pose_lock": pose_lock},
            "debug": _jsonable(self.debug),
        }


def normalize_activity(activity: Any) -> tuple[dict, list]:
    """读侧一致性：place 与 zone 打架时，以“整个地点词”为准纠偏。

    只修强改写（家/医院/路上/外面/公司），不碰「公司附近」「酒店附近」这种
    带后缀的地点——边上的餐厅不该被改成公司/酒店。
    """
    data = dict(activity) if isinstance(activity, dict) else {}
    place = clean_place(data.get("place"))
    zone = str(data.get("zone") or "").strip()
    fixes = []
    want = EXPLICIT_PLACE_ZONES.get(place, "")
    if want and zone and want != zone:
        fixes.append((zone, want))
        data["zone"] = want
        if want != "road":
            data["transit"] = ""
            data["vehicle"] = ""
    elif not zone and want:
        data["zone"] = want
        fixes.append(("", want))
    zone = str(data.get("zone") or "").strip()
    if zone == "road" and place:
        # 23:00 事故读侧：旧行 place=楼下小馆、zone=road，按地点纠成 restaurant。
        target = resolve_zone(place, data.get("category"), zone)
        if target and target != "road":
            fixes.append((zone, target))
            data["zone"] = target
            data["transit"] = ""
            data["vehicle"] = ""
    if not data.get("zone"):
        data["zone"] = resolve_zone(place, data.get("category"), "")
    return data, fixes


def build_plan(activity: Any = None, kind: str = "", explicit_place: str = "",
               scene: str = "", mode: str = "photo") -> PhotoPlan:
    """日程 → 生图计划（唯一入口；语义与老 resolve_plan 完全一致）。

    scene = 老 scene_hint：调用方已经知道更具体的地点文本时传进来，进
    plan.scene（提示词的 where 仍以 photo_engine 的 scene_for 为准）。
    kind 只为对齐调用点保留，不参与规则判定（老代码同样没用它）。
    """
    data, repairs = normalize_activity(activity)
    zone = str(data.get("zone") or "").strip()
    source = str(data.get("_source") or "") or "schedule"
    reason = str(data.get("_reason") or "")
    explicit_zone = zone_for_place(explicit_place) if explicit_place else ""

    if zone:
        key = _scene_key(zone, data)
    elif mode == "photo" and explicit_place and explicit_zone:
        key = _scene_key(explicit_zone, data)
        source = "explicit_place"
        reason = reason or "日程里没有可用地点，用他说的地点兜底"
    else:
        key = "unknown"
        if source == "schedule":
            source = "unknown_zone" if data.get("place") else "no_schedule"
        reason = reason or "没有可用的日程地点"

    rule = SCENE_RULES.get(key) or SCENE_RULES["unknown"]
    conflict = bool(
        explicit_place and zone and explicit_zone and explicit_zone != zone
        and mode == "photo"
    )
    plan = PhotoPlan(
        scene_key=key,
        base_zone=zone or explicit_zone,
        scene=str(scene or "") or str(rule.get("scene") or ""),
        pose_rule=str(rule.get("default_pose") or "stand"),
        pose_line=str((rule.get("pose_lines") or {}).get(rule.get("default_pose"), "")),
        guard=_guard_line(rule),
        props=tuple(rule.get("props") or ()),
        banned=tuple(rule.get("banned") or ()),
        allow_library_pose=bool(rule.get("allow_library_pose")),
        source=source,
        reason=reason,
        activity=data,
        explicit_place=str(explicit_place or ""),
        explicit_conflict=conflict,
        repairs=repairs,
        # where 先给规则里的场景描述，photo_engine 拿到 scene_for 的结果会覆盖
        where=str(scene or "") or str(rule.get("scene") or ""),
        refs_policy=dict(REFS_POLICY_SELFIE),
    )
    plan.refresh_locks()
    plan.debug = {
        "kind": str(kind or ""),
        "mode": str(mode or ""),
        "zone_in": zone,
        "explicit_zone": explicit_zone,
    }
    return plan


def resolve_plan(activity: Any = None, kind: str = "", explicit_place: str = "",
                 mode: str = "photo", scene_hint: str = "") -> PhotoPlan:
    """老入口（保留）：等价于 build_plan(activity, kind, explicit_place, scene_hint, mode)。"""
    return build_plan(activity, kind=kind, explicit_place=explicit_place,
                      scene=scene_hint, mode=mode)


def locks_for_plan(plan: PhotoPlan) -> tuple[str, str]:
    """返回 (英文场景锁, 英文姿势锁)，并写回 plan.locks。

    居家姿势库没有英文条目时只锁场景——pose_rule 变成 "library" 之后
    POSE_EN 里查不到，自然就是空串。
    """
    if plan is None:
        return "", ""
    key = str(getattr(plan, "scene_key", "") or "")
    scene_en = SCENE_EN.get(key, "")
    pose_en = POSE_EN.get((key, str(plan.pose_rule or "")), "")
    plan.locks = (scene_en, pose_en)
    return scene_en, pose_en


def pick_pose_for_plan(plan: PhotoPlan, library: Any = None) -> tuple[Any, str]:
    """姿势只能由场景规则决定：库姿势只在规则允许的居家场景用。

    选中的姿势写回 plan（pose / pose_line / pose_rule / pose_ready），英文姿势锁
    跟着刷新；已经选过就直接返回，重试不会再重摇一次。
    """
    if plan is None:
        return None, ""
    if getattr(plan, "pose_ready", False):
        return plan.pose, plan.pose_line or ""
    rule = SCENE_RULES.get(plan.scene_key) or SCENE_RULES["unknown"]
    if plan.allow_library_pose and library is not None:
        getter = getattr(library, "random_pose", None)
        if callable(getter):
            pose = None
            # 2026-11-23：聊天里点名了姿势就先 match_pose，再随机；
            # 点名的姿势如果不符合当前 zone，仍按随机池走。
            try:
                try:
                    from .pose_library import match_pose as _match_pose, pose_fits_zone as _fits
                except ImportError:
                    from pose_library import match_pose as _match_pose, pose_fits_zone as _fits
                _act = plan.activity if isinstance(plan.activity, dict) else {}
                _text = " ".join([
                    str(plan.scene or ""), str(plan.explicit_place or ""),
                    str(_act.get("activity") or ""), str(_act.get("detail") or ""),
                    str(_act.get("place") or ""),
                ])
                _named = _match_pose(library, _text)
                if _named and _fits(_named, plan.scene_key or plan.base_zone):
                    pose = _named
            except Exception:
                pose = None
            if pose is None:
                try:
                    # 2026-11-22 task-23：按 scene_key（home_bed/home_sofa/home_bath…）
                    # 选房间池，不再用粗粒度的 base_zone="home"（否则客厅能抽到床姿）。
                    try:
                        pose = getter(plan.scene_key or plan.base_zone, str(plan.activity or ""))
                    except TypeError:
                        pose = getter(plan.scene_key or plan.base_zone)
                except TypeError:
                    try:
                        pose = getter()
                    except Exception:
                        pose = None
                except Exception:
                    pose = None
            line = pose_text(pose)
            if line:
                # 用了姿势库的姿势：姿势锁交给库里的描述，不再套规则默认英文姿势
                plan.pose_rule = "library"
                plan.pose = pose
                plan.pose_line = line
                plan.pose_ready = True
                plan.refresh_locks()
                return pose, line
    pose_rule = rule.get("default_pose") or "stand"
    line = str((rule.get("pose_lines") or {}).get(pose_rule) or "")
    plan.pose = None
    plan.pose_rule = pose_rule
    plan.pose_line = line
    plan.pose_ready = True
    plan.refresh_locks()
    return None, line


__all__ = [
    "PhotoPlan",
    "REFS_POLICY_SELFIE",
    "build_plan",
    "locks_for_plan",
    "normalize_activity",
    "pick_pose_for_plan",
    "resolve_plan",
]
