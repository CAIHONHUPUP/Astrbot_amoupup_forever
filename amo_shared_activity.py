"""聊天里约好的共同活动：识别 → 写当前小时日程 → 到点/中断收尾 → 场景事实。

设计要点（2026-11-15 用户要求）：
- 只有「真的要看」才覆盖日程；「好啊，不过我可能看一会儿就去睡」这类口嗨不覆盖。
- 覆盖不粗暴：保留这一小时原有的动作，写成「前半段…，后半段和老公一起…」。
- 有生命周期：到点写「剧看完了，关电视去洗漱」；他说「我不看了/去忙了」立刻打断并写清楚。
- 回复要带场景感：注入「你们看的是同一段剧情、讨论同一段」的事实。
"""
from typing import TYPE_CHECKING, Any

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import datetime as _dt
import re

try:
    from .amo_current_action import set_action
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_current_action import set_action

try:
    from .amo_observability import note as _note_swallowed
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed


def _now():
    return _amo_clock.now()


KV_KEY = "shared_activity"

PATTERNS = (
    ("watch", r"(一起|一块|咱们|我们|陪你|跟我|和我|和你)看(个|点)?(电视|剧|电影|综艺|直播|视频|球赛)"),
    ("watch", r"连麦看(剧|电影|电视)"),
    ("game", r"(一起|一块|咱们|我们|陪你|和我|和你)(打|玩)(游戏|一把|排位|王者)"),
    ("game", r"开黑"),
    ("music", r"(一起|一块|咱们|我们|陪你|和我|和你)听歌"),
    ("study", r"(一起|一块|咱们|我们|陪你)(学习|看书|写作业)"),
)

HEDGE_WORDS = ("可能", "再说", "看一会儿", "玩一会儿", "不过", "要是", "如果",
               "先看看", "不一定", "困了", "睡了", "明天", "改天", "看情况", "顶多")

STOP_WORDS = ("不看了", "不玩了", "不看啦", "不玩啦", "我去忙", "去忙了", "先睡了",
              "不聊了", "下线了", "关电视", "退出", "不听了", "没空看")

LABELS = {"watch": "看电视", "game": "打游戏", "music": "听歌", "study": "学习"}

DEFAULT_MINUTES = 90
MAX_MINUTES = 180


def _minutes_of(text):
    text = str(text or "")
    m = re.search(r"(\d+)\s*(个)?\s*小时", text)
    if m:
        return max(10, min(MAX_MINUTES, int(m.group(1)) * 60))
    m = re.search(r"[两二三四]\s*个?\s*小时", text)
    if m:
        table = {"两": 2, "二": 2, "三": 3, "四": 4}
        return min(MAX_MINUTES, table.get(m.group(0)[0], 2) * 60)
    m = re.search(r"半\s*个?\s*小时", text)
    if m:
        return 30
    m = re.search(r"(\d+)\s*分钟", text)
    if m:
        return max(10, min(MAX_MINUTES, int(m.group(1))))
    return DEFAULT_MINUTES


_TOGETHER = re.compile(r"(一起|一块|咱们|我们|陪你|跟我|和我|和你)")
#: 明确的观看/娱乐对象（光一个「看」不算，避免把「好看」当约定）
_ACT_OBJECT = re.compile(
    r"(?:看[^，。！？\n]{0,8}(?:电视|剧|电影|综艺|直播|视频|球赛|节目)"
    r"|(?:打|玩)(?:游戏|一把|排位|王者)|开黑|听歌|(?:学习|看书|写作业))"
)
#: 句首的答应（「好，那看…」「好啊，不过我可能看一会儿」）；「好看」这种形容词不算
_AGREE = re.compile(
    r"^(?:好|行|可以|嗯)[啊呀]?(?![看着的不很真太])(?:[，,、。！!\s].{0,8})?"
    r"(?:那|就|咱们|我们|一起)?\s*(?:看|打|玩|听|学习|看书)"
)
_KIND_RULES = (
    ("game", r"(游戏|排位|开黑|王者|打一把|玩一把)"),
    ("music", r"(听歌|音乐|连麦听)"),
    ("study", r"(学习|看书|写作业)"),
)


def detect(text):
    """聊天里是否真的约了共同活动；返回 dict(kind/label/minutes/hedged)。

    2026-11-17 修：以前「好|行|可以」+ 8 个字符内出现「看」就算约定，
    「很好看」「谁让我老婆好看」这类夸奖也会写成「和老公连麦看电视」。现在：
    - 「一起/我们/陪你…」分支必须带明确的观看/游戏/听歌对象；
    - 单独答应分支必须出现在句首，且不能是「好看/不好看/很/真/太」这类形容词。
    """
    text = str(text or "").strip()
    if not text:
        return None
    together = bool(_TOGETHER.search(text)) and bool(_ACT_OBJECT.search(text))
    loose_agree = bool(_AGREE.match(text))
    if not (together or loose_agree):
        return None
    kind = "watch"
    for name, pattern in _KIND_RULES:
        if re.search(pattern, text):
            kind = name
            break
    hedged = any(word in text for word in HEDGE_WORDS)
    return {"kind": kind, "label": LABELS.get(kind, "一起玩"),
            "minutes": _minutes_of(text), "hedged": bool(hedged)}

def stop_detected(text):
    text = str(text or "")
    return any(word in text for word in STOP_WORDS)


def expired(activity, now_ts=None):
    try:
        ends = float((activity or {}).get("ends_at") or 0)
    except (TypeError, ValueError):
        return False
    if not ends:
        return False
    if now_ts is None:
        now_ts = _now().timestamp()
    return float(now_ts) >= ends


def entry_activity(label):
    return "和老公连麦" + str(label or "看电视")


def hour_detail(old_activity, label, at_hhmm=""):
    """不粗暴覆盖：把这一小时写成「前半段…，后半段…」。"""
    old = str(old_activity or "").strip()
    new = entry_activity(label)
    if not old or old in ("", new):
        return ("%s 开始，" % at_hhmm if at_hhmm else "") + new
    return "前半段%s，后半段%s%s" % (old, new, ("（%s 开始）" % at_hhmm) if at_hhmm else "")


def prompt_block(activity, now_ts=None):
    label = str((activity or {}).get("label") or "看电视")
    return (
        "【正在一起" + label + "】你和老公正一起" + label + "（线上连麦/投影，看的是同一段、进度一样）。"
        "回复要带场景感（窝在沙发、手边零食、随口吐槽剧情），跟他聊同一段内容；"
        "不要跳出来做旁白，也不要说自己没在看。"
    )


def finish_text(label, interrupted=False):
    label = str(label or "看电视")
    if interrupted:
        return "老公去忙了，我一个人把剩下的" + label + "看完"
    return label + "看完了，关掉电视去洗漱"


class SharedActivityMixin:
    """挂在 HumanCorePlugin 上：识别、写日程、收尾、注入事实。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        db: Any
        state: Any

    def _sa_kv(self):
        try:
            return dict(self.db.kv_get(KV_KEY, {}) or {})
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity._sa_kv", _amo_exc)
            return {}

    def _sa_current_item(self):
        now = _now()
        day = now.date().isoformat()
        try:
            items = self.state.schedule.get(day) or []
        except Exception:
            return None
        for item in items:
            start, end = str(item.get("start") or ""), str(item.get("end") or "")
            try:
                sh, sm = [int(x) for x in start[:5].split(":")]
                eh, em = (24, 0) if end.startswith("24") else [int(x) for x in end[:5].split(":")]
            except Exception:
                continue
            if sh * 60 + sm <= now.hour * 60 + now.minute < eh * 60 + em:
                return item
        return None

    def _sa_at_home(self):
        item = self._sa_current_item()
        if item is None:
            return True  # 没有当前小时行时按在家处理（旧测试/首启）；有行就必须是 home
        zone = str(item.get("zone") or "")
        category = str(item.get("category") or "")
        return zone == "home" and category not in ("commute", "work")

    def shared_activity_active(self):
        act = self._sa_kv()
        if act.get("status") != "active":
            return None
        if not self._sa_at_home():
            return None
        return act

    def shared_activity_prompt(self):
        act = self.shared_activity_active()
        return prompt_block(act) if act else ""

    def shared_activity_on_message(self, text):
        text = str(text or "")
        act = self._sa_kv()
        if act.get("status") == "active":
            if stop_detected(text):
                self._sa_finish(act, interrupted=True)
                return {"action": "finish", "interrupted": True}
            if expired(act):
                self._sa_finish(act, interrupted=False)
        hit = detect(text)
        if not hit:
            return None
        if not self._sa_at_home():
            # 她在路上/公司/外面：不能把当前小时改写成“和老公看电视”
            return {"action": "ignored", "reason": "not_home"}
        if hit.get("hedged"):
            try:
                self.db.kv_set("shared_activity_hedged", {"label": hit["label"], "at": _now().timestamp()})
            except Exception as _amo_exc:
                _note_swallowed("amo_shared_activity.hedged", _amo_exc)
            return {"action": "hedged", "label": hit["label"]}
        self._sa_start(hit)
        return {"action": "start", "label": hit["label"]}

    def _sa_start(self, hit):
        now = _now()
        label = hit["label"]
        act = {
            "kind": hit["kind"], "label": label,
            "started_at": now.timestamp(),
            "ends_at": now.timestamp() + int(hit["minutes"]) * 60,
            "status": "active", "interrupted": False,
            "day": now.date().isoformat(),
        }
        set_action(self, entry_activity(label), until_ts=act["ends_at"],
                   place="家", category="life", source="shared_activity")
        try:
            self.db.kv_set(KV_KEY, act)
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.set", _amo_exc)
        self._sa_commit_schedule(act, now)

    def _sa_commit_schedule(self, act, now):
        day = now.date().isoformat()
        hour = int(now.hour)
        try:
            items = [dict(item) for item in (self.state.schedule.get(day) or [])]
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.schedule_load", _amo_exc)
            return
        target = None
        for item in items:
            start = str(item.get("start") or "")
            end = str(item.get("end") or "")
            if len(start) < 5 or len(end) < 2:
                continue
            try:
                sh = int(start[:2])
                eh = 24 if end.startswith("24") else int(end[:2])
            except ValueError:
                continue
            if sh <= hour < eh:
                target = item
                break
        old_activity = ""
        if target is None:
            target = {"start": "%02d:00" % hour,
                      "end": ("24:00" if hour >= 23 else "%02d:00" % (hour + 1)),
                      "activity": "", "place": "家", "zone": "home", "category": "life"}
            items.append(target)
        else:
            old_activity = str(target.get("activity") or "")
        target["activity"] = entry_activity(act["label"])
        target["detail"] = hour_detail(old_activity, act["label"], now.strftime("%H:%M"))
        target["place"] = str(target.get("place") or "家")
        target["zone"] = "home"
        target["category"] = "life"
        target["happened"] = True
        try:
            self.state.commit_schedule_change(day, items, source="chat",
                                              reason="shared_activity_start")
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.commit", _amo_exc)

    def shared_activity_tick(self):
        act = self._sa_kv()
        if act.get("status") != "active":
            return None
        if not expired(act):
            return None
        self._sa_finish(act, interrupted=False)
        return "finished"

    def _sa_finish(self, act, interrupted=False):
        now = _now()
        label = str(act.get("label") or "看电视")
        text = finish_text(label, interrupted)
        set_action(self, text, until_ts=now.timestamp() + 300, place="家",
                   category="life", source="shared_activity_done")
        done = dict(act)
        done.update({"status": "done", "interrupted": bool(interrupted),
                     "ended_at": now.timestamp()})
        done.pop("active", None)
        try:
            self.db.kv_set(KV_KEY, done)
            self.db.kv_set("shared_activity_last", {
                "label": label, "text": text, "at": now.timestamp(),
                "interrupted": bool(interrupted)})
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.finish", _amo_exc)
