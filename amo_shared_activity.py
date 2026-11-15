"""聊天里约好的共同活动：识别 → 写当前小时日程 → 到点/中断收尾 → 场景事实。

设计要点（2026-11-15 用户要求）：
- 只有「真的要看」才覆盖日程；「好啊，不过我可能看一会儿就去睡」这类口嗨不覆盖。
- 覆盖不粗暴：保留这一小时原有的动作，写成「前半段…，后半段和老公一起…」。
- 有生命周期：到点写「剧看完了，关电视去洗漱」；他说「我不看了/去忙了」立刻打断并写清楚。
- 回复要带场景感：注入「你们看的是同一段剧情、讨论同一段」的事实。
"""

import datetime as _dt
import re

try:
    from .amo_current_action import set_action
except ImportError:
    from amo_current_action import set_action

try:
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed


def _now():
    return _dt.datetime.now()


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
_AGREE = re.compile(r"(好|行|可以)[啊呀]?[，,]?.{0,8}(看|玩|听|打)")
_ACT = re.compile(r"(看|打|玩|听)")
_KIND_RULES = (
    ("game", r"(游戏|排位|开黑|王者|打一把|玩一把)"),
    ("music", r"(听歌|音乐|连麦听)"),
    ("study", r"(学习|看书|写作业)"),
)


def detect(text):
    """聊天里是否约了共同活动；返回 dict(kind/label/minutes/hedged)。

    - 「一起看两小时剧」「陪你一起打游戏」「一块听歌」都算；
    - 「好啊，不过我可能看一会儿就去睡觉」也算，但标 hedged，不覆盖日程。
    """
    text = str(text or "")
    if not text.strip():
        return None
    together = bool(_TOGETHER.search(text)) and bool(_ACT.search(text))
    loose_agree = bool(_AGREE.search(text))
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
    return "和老公一起" + str(label or "看电视")


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

    def _sa_kv(self):
        try:
            return dict(self.db.kv_get(KV_KEY, {}) or {})
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity._sa_kv", _amo_exc)
            return {}

    def shared_activity_active(self):
        act = self._sa_kv()
        if act.get("status") != "active":
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
        target.setdefault("place", "家")
        target.setdefault("zone", "home")
        target["category"] = target.get("category") or "life"
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
