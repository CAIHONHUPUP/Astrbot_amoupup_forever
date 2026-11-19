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


_CN_HOURS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11,
             "十二": 12, "二十": 20, "二十一": 21}
_CLOCK_RE = re.compile(
    r"(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|今早|明晚|明天)?\s*"
    r"([0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*(?:[:：]|点)\s*"
    r"([0-9]{1,2}|半)?\s*分?")
_SOON_RE = re.compile(r"(晚点|晚一点儿|待会|待会儿|等会|等会儿|过会儿|一会儿|等下)")


def _hour_of(token):
    token = str(token or "")
    if token.isdigit():
        return int(token)
    return _CN_HOURS.get(token)


def start_at_ts(text, now):
    """解析聊天里约好的开始时间；解析不到就是现在（立即开始）。

    2026-11-19 用户要求：说好「八点」就八点，中间只说约好了，不能一到消息就开演。
    支持「今晚八点 / 晚上8点 / 20:00 / 八点半 / 明晚八点 / 待会 / 晚点」；
    只说「今晚」不带钟点、或带「可能/看一会儿/再说」的口嗨，仍按现在或 hedged 处理。
    """
    if not isinstance(now, _dt.datetime):
        now = _now()
    text = str(text or "")
    match = _CLOCK_RE.search(text)
    if match:
        prefix = str(match.group(1) or "")
        hour = _hour_of(match.group(2))
        minute_token = str(match.group(3) or "0")
        minute = 30 if minute_token == "半" else (_hour_of(minute_token) or 0)
        if hour is not None and 0 <= hour <= 24:
            if prefix in ("晚上", "今晚", "明晚", "傍晚") and hour < 12:
                hour += 12
            elif prefix == "下午" and hour < 12:
                hour += 12
            elif prefix == "中午" and hour < 11:
                hour += 12
            elif not prefix and hour < 12 and now.hour >= 12:
                hour += 12
            day = now.date()
            if prefix in ("明晚", "明天"):
                day = day + _dt.timedelta(days=1)
            target = _dt.datetime.combine(day, _dt.time(hour % 24, minute))
            if target <= now:
                return now.timestamp()
            return target.timestamp()
    if _SOON_RE.search(text):
        return (now + _dt.timedelta(minutes=30)).timestamp()
    return now.timestamp()


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
    hedged = any(word in text for word in HEDGE_WORDS)
    together = bool(_TOGETHER.search(text)) and bool(_ACT_OBJECT.search(text))
    # 2026-11-19 修：「可以啊，你看看有什么好吃的」句首答应 + 8 字内出现「看」，
    # 被当成约好一起看电视：17:00 的「在工位上忙工作」被改写成「和老公连麦看电视」，
    # 还补了一段 17:00-17:29 的通勤块，她会从工位瞬移回家、40 秒后又窝在沙发上。
    # 单独答应分支也必须带明确的观看/游戏/听歌对象；只有带含糊词的口嗨
    #（看一会儿/可能/再说…）才放行，由调用方按 hedged 处理，不写日程。
    loose_agree = bool(_AGREE.match(text)) and (bool(_ACT_OBJECT.search(text)) or hedged)
    if not (together or loose_agree):
        return None
    kind = "watch"
    for name, pattern in _KIND_RULES:
        if re.search(pattern, text):
            kind = name
            break
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


def pending_block(activity, now_ts=None):
    """还没到约定时间：只说她「约好了几点」，不能按「正在一起」演。"""
    label = str((activity or {}).get("label") or "看电视")
    try:
        start = float((activity or {}).get("starts_at") or 0)
    except (TypeError, ValueError):
        start = 0
    if not start:
        return ""
    at = _dt.datetime.fromtimestamp(start).strftime("%H:%M")
    return (
        "【约好了 %s 一起%s】现在还没到点，别当成已经在%s；"
        "可以期待、问他要不要提前，到点再连。到点会自动开始，不用说「正在看」。"
        % (at, label, label)
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
        now = _now()
        try:
            items = self.state.schedule.get(now.date().isoformat()) or []
        except Exception:
            return True
        item = None
        prev = None
        cur = now.hour * 60 + now.minute
        for index, row in enumerate(items):
            if not isinstance(row, dict):
                continue
            start, end = str(row.get("start") or ""), str(row.get("end") or "")
            try:
                sh, sm = [int(x) for x in start[:5].split(":")]
                eh, em = (24, 0) if end.startswith("24") else [int(x) for x in end[:5].split(":")]
            except Exception:
                continue
            if sh * 60 + sm <= cur < eh * 60 + em:
                item = row
                prev = items[index - 1] if index else None
                break
        if item is None:
            return True  # 没有当前小时行时按在家处理（旧测试/首启）；有行就必须是 home
        zone = str(item.get("zone") or "")
        category = str(item.get("category") or "")
        if not (zone == "home" and category not in ("commute", "work")):
            return False
        # 2026-11-19 修：上一块还在公司/外面、时间上却与当前「在家」块首尾相接，
        # 说明日程本身是瞬移（还没补通勤）。这种日程不能当「她已在家」，
        # 否则聊天里一句「一起看电视」就会把这段错误放大成真的回家。
        if isinstance(prev, dict):
            prev_zone = str(prev.get("zone") or "")
            prev_place = str(prev.get("place") or "")
            prev_end = str(prev.get("end") or "")[:5]
            cur_start = str(item.get("start") or "")[:5]
            if (prev_place and prev_zone not in ("home", "road")
                    and prev_end and prev_end == cur_start):
                return False
        return True

    def shared_activity_active(self):
        act = self._sa_kv()
        if act.get("status") != "active":
            return None
        if not self._sa_at_home():
            return None
        return act

    def shared_activity_prompt(self):
        act = self._sa_kv()
        if act.get("status") == "pending":
            return pending_block(act)
        act = self.shared_activity_active()
        return prompt_block(act) if act else ""

    def shared_activity_on_message(self, text):
        text = str(text or "")
        act = self._sa_kv()
        status = str(act.get("status") or "")
        if status == "pending":
            # 已约好但还没到点：不写日程、不演「正在看」；改口/取消/换时间才动。
            if stop_detected(text):
                self._sa_cancel(act)
                return {"action": "cancelled"}
            hit = detect(text)
            if not hit:
                return None
            if hit.get("hedged"):
                return {"action": "hedged", "label": hit["label"]}
            return self._sa_start(hit, text)
        if status == "active":
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
        return self._sa_start(hit, text)

    def _sa_start(self, hit, text=""):
        now = _now()
        start_ts = start_at_ts(text, now)
        future = start_ts > now.timestamp() + 60
        if not future and not self._sa_at_home():
            # 她在路上/公司/外面：不能把当前小时改写成“和老公看电视”
            return {"action": "ignored", "reason": "not_home"}
        label = hit["label"]
        minutes = int(hit["minutes"])
        act = {
            "kind": hit["kind"], "label": label, "minutes": minutes,
            "created_at": now.timestamp(),
            "ends_at": start_ts + minutes * 60,
            "status": "pending" if future else "active",
            "interrupted": False,
            "day": _dt.datetime.fromtimestamp(start_ts).date().isoformat(),
        }
        if future:
            act["starts_at"] = start_ts
        else:
            act["started_at"] = now.timestamp()
        try:
            self.db.kv_set(KV_KEY, act)
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.set", _amo_exc)
        if future:
            return {"action": "scheduled", "label": label,
                    "at": _dt.datetime.fromtimestamp(start_ts).strftime("%H:%M")}
        set_action(self, entry_activity(label), until_ts=act["ends_at"],
                   place="家", category="life", source="shared_activity")
        self._sa_commit_schedule(act, now)
        return {"action": "start", "label": label}

    def _sa_commit_schedule(self, act, now):
        day = now.date().isoformat()
        hour = int(now.hour)
        def _mutate(rows):
            items = [dict(item) for item in rows if isinstance(item, dict)]
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
                          "activity": "", "place": "家", "zone": "home",
                          "category": "life"}
                items.append(target)
            else:
                old_activity = str(target.get("activity") or "")
            target["activity"] = entry_activity(act["label"])
            target["detail"] = hour_detail(old_activity, act["label"],
                                           now.strftime("%H:%M"))
            target["place"] = str(target.get("place") or "家")
            target["zone"] = "home"
            target["category"] = "life"
            target["happened"] = True
            return items

        try:
            apply = getattr(self.state, "apply_schedule", None)
            if callable(apply):
                apply(day, _mutate, source="chat", reason="shared_activity_start")
            else:
                self.state.commit_schedule_change(
                    day, _mutate(self.state.schedule.get(day) or []),
                    source="chat", reason="shared_activity_start")
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.commit", _amo_exc)

    def shared_activity_tick(self):
        act = self._sa_kv()
        status = str(act.get("status") or "")
        if status == "pending":
            try:
                starts = float(act.get("starts_at") or 0)
            except (TypeError, ValueError):
                starts = 0
            if not starts or _now().timestamp() < starts:
                return None
            if not self._sa_at_home():
                # 到点了但人还在外面：先不演，下一分钟再试。
                return None
            return self._sa_activate(act)
        if status != "active":
            return None
        if not expired(act):
            return None
        self._sa_finish(act, interrupted=False)
        return "finished"

    def _sa_activate(self, act):
        now = _now()
        active = dict(act)
        active["status"] = "active"
        active["started_at"] = now.timestamp()
        active.pop("starts_at", None)
        label = str(active.get("label") or "看电视")
        set_action(self, entry_activity(label),
                   until_ts=float(active.get("ends_at") or 0),
                   place="家", category="life", source="shared_activity")
        try:
            self.db.kv_set(KV_KEY, active)
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.activate", _amo_exc)
        self._sa_commit_schedule(active, now)
        return "started"

    def _sa_cancel(self, act):
        done = dict(act)
        done.update({"status": "cancelled", "ended_at": _now().timestamp()})
        done.pop("starts_at", None)
        try:
            self.db.kv_set(KV_KEY, done)
        except Exception as _amo_exc:
            _note_swallowed("amo_shared_activity.cancel", _amo_exc)

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
