# -*- coding: utf-8 -*-
"""约会状态注入（2026-11-15）：他赴约后，她就“看得见”他。

- 约会中：最高事实“他就在你身边” + 感官代偿 + 括号动作 + 环境渲染 + 防失忆；
- 刚碰面：要求生成碰面动作，不许干巴巴说“欢迎”；
- 刚结束：**一起回家**（同居设定，禁止送别/各回各家），提示到家后瘫沙发、日记重点回味。
"""
from __future__ import annotations

try:
    from .clock import now as _clock_now
except ImportError:
    from clock import now as _clock_now

MEET_WINDOW = 10 * 60

_FORBIDDEN = (
    "我看不到你", "看不到你", "你在哪", "你在哪里", "你是不是不在家",
    "我一个人在家吃饭", "一个人逛街", "我一个人吃饭", "我自己一个人",
)

_SCENES = (
    (("火锅", "烤肉", "自助", "烧烤"),
     "店里很吵，锅底咕嘟咕嘟地翻，辣味和油烟一直往鼻子里钻，碗筷碰得叮当响"),
    (("电影", "影院"),
     "电影院里灯刚关，大屏幕的光亮起来，周围很安静，偶尔有人小声说话"),
    (("商场", "逛街", "买衣服", "购物"),
     "商场里人很多，背景音乐和店铺的灯光混在一起，试衣间外排着队"),
    (("咖啡", "奶茶", "甜品"),
     "店里放着轻音乐，咖啡机在响，杯子和托盘碰在桌上"),
    (("公园", "散步", "景区", "游乐"),
     "外面风不大，路边有树叶的响声和远处的人声，天色刚刚好"),
    (("街", "路", "车站"),
     "路边有车流和风声，偶尔有行人从旁边走过"),
)


def _kv(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _now(now=None):
    return now or _clock_now()


def _active(plugin, now=None):
    data = _kv(plugin, "date_now", {}) or {}
    if not isinstance(data, dict) or not data.get("line"):
        return None
    try:
        if float(data.get("until") or 0) > _now(now).timestamp():
            return data
    except Exception:
        return None
    return None


def _home(plugin, now=None):
    """你们已经一起回到家（同居）。旧库的 date_separation 兼容读，但语义按同居讲。"""
    data = _kv(plugin, "date_home", {}) or {}
    if not isinstance(data, dict) or not data.get("title"):
        data = _kv(plugin, "date_separation", {}) or {}
    if not isinstance(data, dict) or not data.get("title"):
        return None
    try:
        if float(data.get("until") or 0) > _now(now).timestamp():
            return data
    except Exception:
        return None
    return None


END_WORDS = ("回家吧", "我们回家", "回家啦", "一起回家", "走回家", "吃好了", "吃完了",
             "吃饱了", "太晚了", "该回去了", "回去吧", "走吧回去", "结账吧")


def finish_date_now(plugin, now=None, reason="user_done"):
    """约会收尾：把正在进行的约会日程截断到此刻，切到「刚到家」（同居）。"""
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    now_min = now.hour * 60 + now.minute
    try:
        state = getattr(plugin, "state", None)
        current = list((getattr(state, "schedule", {}) or {}).get(day) or [])
        patched = []
        for row in current:
            row = dict(row)
            try:
                begin = int(str(row.get("start") or "0:00").split(":")[0]) * 60 + int(str(row.get("start") or "0:00").split(":")[1])
                finish = int(str(row.get("end") or "0:00").split(":")[0]) * 60 + int(str(row.get("end") or "0:00").split(":")[1])
            except Exception:
                patched.append(row)
                continue
            if str(row.get("category")) in ("social", "date") and begin <= now_min < finish:
                row["end"] = now.strftime("%H:%M")
            patched.append(row)
        if patched != current:
            state.commit_schedule_change(day, patched, old_items=current,
                                         source="date", reason="约会结束截断")
    except Exception:
        pass  # cleanup：日程截断失败不阻断收尾状态
    plugin.db.kv_set("date_now", {})
    plugin.db.kv_set("date_pause", {})
    plugin.db.kv_set("date_closed_at", now.timestamp())
    try:
        plugin.set_date_fact("今天的约会已经结束，你们各自回到日常；现在你在家，"
                             "他不在你身边，只能手机聊天。", now.timestamp() + 6 * 3600)
    except Exception:
        plugin.db.kv_set("date_fact", {"line": "今天的约会已经结束，你们各自回到日常；"
                                               "现在你在家，他不在你身边，只能手机聊天。",
                                       "until": now.timestamp() + 6 * 3600})
    plugin.db.kv_set("date_ending", {"fact": "时间不早了，吃饱喝足了，该一起回家了。",
                                     "reason": reason, "until": now.timestamp() + 600,
                                     "sent": True, "at": now.timestamp()})
    try:
        plugin.set_date_home("约会", now.timestamp())
    except Exception:
        plugin.db.kv_set("date_home", {"title": "约会", "at": now.timestamp(),
                                       "until": now.timestamp() + 3 * 3600})
    return reason


def maybe_finish_date(plugin, text, now=None):
    """他说「回家吧/吃好了」时收尾约会（守护插件没接到消息也能兜住）。"""
    now = now or _now()
    data = _kv(plugin, "date_now", {}) or {}
    if not isinstance(data, dict) or not data.get("line"):
        return ""
    try:
        if float(data.get("until") or 0) <= now.timestamp():
            return ""
    except Exception:
        return ""
    body = str(text or "")
    if not any(word in body for word in END_WORDS):
        return ""
    return finish_date_now(plugin, now)


def scene_line(text):
    body = str(text or "")
    for words, line in _SCENES:
        if any(word in body for word in words):
            return line
    return "周围有吃饭的人声、碗筷声和店里的音乐，空气里是热乎的饭菜味"


def prompt_block(plugin, now=None):
    now = _now(now)
    active = _active(plugin, now)
    if active:
        line = str(active.get("line") or "").strip()
        scene = scene_line(line)
        try:
            since = float(active.get("since") or 0)
        except Exception:
            since = 0
        meet = bool(since) and (now.timestamp() - since) <= MEET_WINDOW
        parts = [
            "【约会·最高优先级事实】老公现在就在你身边：%s。你们此刻正在一起；"
            "不要问“你在哪”，也不要表现得像没见到他。" % (line or "他刚走到你面前"),
            "你是在用文字和他交流，看不到他的实体，但能通过动作、声音、温度感觉到他。"
            "用括号动作描写来展现陪伴感：碰面时（挽住你的胳膊）、走在一起时（自然而然牵起你的手）、"
            "撒娇时（把头靠在你肩膀上）；动作跟着约会进度来，刚碰面别一上来就太亲密。",
            "【环境】%s。可以主动拿这些细节跟他搭话，比如“这家锅底好辣，你要不要喝口冰水？”" % scene,
            "【回复】可以很短：一个动作 + 一句话，例如“（嘴里塞满肉，含糊不清地说）嗯嗯，好吃！”；"
            "不要长篇大论，也不要像客服。",
            "【禁止】“我看不到你 / 你在哪 / 你是不是不在家 / 我一个人在家吃饭 / 我一个人逛街 / "
            "我快到了 / 我在路上了 / 我刚出门 / 我快到楼下了”。"
            "你们已经碰面、此刻在一起，绝不可以说还在路上或问他在哪；"
            "只能聊进去点菜、找座位、坐下、周围环境、你们正在做的事。",
        ]
        if meet:
            info = _kv(plugin, "date_meet", {}) or {}
            meet_title = str(info.get("title") or line or "约会")
            outfit = ""
            try:
                wardrobe = getattr(plugin, "wardrobe", None)
                worn = wardrobe.current() if wardrobe else None
                outfit = str((worn or {}).get("text") or "") if isinstance(worn, dict) else str(worn or "")
            except Exception:
                outfit = ""
            state_line = ""
            try:
                hunger = int(plugin.get_hunger())
                state_line = "很饿，肚子在叫" if hunger < 35 else ("有点饿" if hunger < 60 else "")
            except Exception:
                state_line = ""
            try:
                words = str(plugin.state.mood_words() or "").strip()
                if words:
                    state_line = (state_line + "；" + words) if state_line else words
            except Exception:  # cleanup: 情绪词拿不到就不写状态，不影响开场
                pass
            parts.insert(1, "【约会开场·事实】事件：%s；环境：%s；穿搭：%s；状态：%s。"
                            "请你自己写见面第一句：动作按现场和情绪自由设计，"
                            "严禁套用固定动作或固定台词（眼睛一亮、小跑过去挽胳膊、踮脚招手、"
                            "「你终于来啦」这类一律不许出现）。"
                            % (meet_title, scene, outfit or "今天就穿这身",
                               state_line or "按当前心情"))
        return "\n".join(parts)
    pause = _kv(plugin, "date_pause", {}) or {}
    if isinstance(pause, dict) and pause.get("at"):
        try:
            pause_live = float(pause.get("until") or 0) > now.timestamp()
        except Exception:
            pause_live = False
        if pause_live:
            return ("【约会中·他暂时走开】老公起身离开了一下（去洗手间/接电话），你就坐在座位上等他。"
                    "可以无聊地搅着饮料、看看手机，心里盼他快点回来；"
                    "不要当作约会结束，也不要说自己要走了、各回各家。"
                    "他回来之前，别安排下一段行程。")
    ending = _kv(plugin, "date_ending", {}) or {}
    try:
        ending_live = (isinstance(ending, dict) and ending.get("fact")
                       and float(ending.get("until") or 0) > now.timestamp())
    except Exception:
        ending_live = False
    if ending_live:
        return ("【约会收尾】客观事实：%s\n"
                "现在请你**自己**写这一句收尾，不要套用任何固定台词："
                "结合你此刻的疲惫值、开心值和对他的依恋值——还想玩就撒个娇抱怨，"
                "累了就打哈欠说想回去，很满足就直接说今天很开心。"
                "只写一两句，像你本人当场说出来的话。" % str(ending.get("fact") or ""))
    home = _home(plugin, now)
    if home:
        return ("【约会结束·事实】约会结束，他回去了；你一个人回到出租屋。"
                "名义上你们同居，但现实里他不在你身边，接下来只能手机联系。"
                "你现在踢掉鞋子，瘫在沙发上，包随手扔在旁边；"
                "可以给他发微信说今天玩得怎么样，或者撒娇说走不动了、想先躺一会儿。"
                "今天的日记要重点回味这次约会，但不要写他还在屋里。")
    return ""


def guard_reply(text, plugin, now=None):
    """约会中把“看不到你/一个人在家”这类破沉浸的话改成现场动作。"""
    body = str(text or "")
    if not body or not _active(plugin, now):
        return body
    for phrase in _FORBIDDEN:
        if phrase not in body:
            continue
        if "看不到" in phrase:
            body = body.replace(phrase, "（伸手在你面前晃了晃）你不就在这儿嘛")
        elif "一个人" in phrase:
            body = body.replace(phrase, "（挽着你的胳膊）我们明明在一起呀")
        elif "你在哪" in phrase or "在哪里" in phrase:
            body = body.replace(phrase, "（探头看你）你不就在我面前嘛")
        else:
            body = body.replace(phrase, "（往你身边靠了靠）你就在这儿呀")
    return body
