# -*- coding: utf-8 -*-
"""运行时上下文补充块（2026-11-24 第 5 刀，从 amo_runtime_context 拆出）。

穿搭 / 换装 / 归属纠正三块搬到这里；方法体逐行搬运，模块级 helper 通过
_mod() 动态取，保证测试里 patch amo_runtime_context.xxx 仍然生效。
"""
from __future__ import annotations

from typing import Any

try:  # 吞异常留痕
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    from amo_observability import note as _note_swallowed


def _mod():
    try:
        from . import amo_runtime_context as module
    except ImportError:  # 直接按文件加载时
        import amo_runtime_context as module
    return module


class ExtraContextMixin:
    # 宿主（HumanCorePlugin）提供的成员：只在类型层面声明，运行时不创建属性（不改变任何行为）。
    state: Any
    db: Any
    wardrobe: Any
    housing: Any
    micro_events: Any
    _short_place: Any
    _date_fact_line: Any

    def _ctx_outfit(self: Any, c, change=False):
        """【穿搭】；change=True 时处理「换一套」（睡衣/白天/在外面换不了）。"""
        event = c["event"]
        if change:
            return self._ctx_outfit_change(event)
        parts = []
        try:
            wardrobe = getattr(self, "wardrobe", None)
            if wardrobe is not None:
                _outfit = wardrobe.current()
                _top = str(_outfit.get("top") or "")
                _bottom = str(_outfit.get("bottom") or "")
                _shoes = str(_outfit.get("shoes") or "")
                _wear = "、".join([part for part in (_top, _bottom) if part])
                if _wear:
                    try:
                        _at_hotel = self.wardrobe.at_hotel()
                    except Exception:
                        _at_hotel = False
                    try:
                        _is_home = self.wardrobe.at_home() is True and not _at_hotel
                    except Exception:
                        _is_home = False
                    if _outfit.get("sleep") and _is_home:
                        _line = ("【当前穿搭事实】你现在在家，穿着" + _wear
                                 + "（居家服/睡衣）。你今天没有出门，绝对没有穿外套、牛仔裤或外出的鞋，"
                                 "不要为了好看脑补光鲜的外出穿搭。")
                    elif _outfit.get("sleep"):
                        if _at_hotel:
                            _line = ("【穿搭】在酒店，换上"
                                     + ("酒店睡衣" if _outfit.get("hotel") else "睡衣")
                                     + "了：" + _wear)
                        else:
                            _line = "【穿搭】已经下班回家，换上睡衣了：" + _wear
                    else:
                        _line = "【穿搭】今天穿的是" + _wear
                    if _shoes:
                        _line += ("，脚上是" if _is_home else "，鞋子是") + _shoes
                    _line += "；被问起穿搭就照这个说。"
                    parts.append(_line)
                    try:
                        import json as _json
                        _act = str((self.state.current_activity() or {}).get("activity") or "")
                        _scene = ("takeout" if any(w in _act for w in ("拿外卖", "倒垃圾", "取快递"))
                                  else "work" if any(w in _act for w in ("上班", "开会", "工作", "公司"))
                                  else "date" if any(w in _act for w in ("约会", "逛街"))
                                  else "home" if _is_home else "outing")
                        _weather = getattr(self.state, "weather", {}) or {}
                        _facts = {
                            "scene": _scene,
                            "location": str((self.state.current_activity() or {}).get("place") or ""),
                            "outfit": [p for p in (_top, _bottom, _shoes) if p],
                            "is_homewear": bool(_outfit.get("homewear") or _outfit.get("sleep")),
                            "sleepwear": bool(_outfit.get("sleep")),
                            "outerwear_off": bool(_outfit.get("transition") or _outfit.get("outerwear_off")),
                            "weather": str(_weather.get("text") or ""),
                            "temperature": _weather.get("temp"),
                            "rain": any(w in str(_weather.get("text") or "") for w in ("雨", "雪")),
                            "is_makeup": _scene not in ("takeout", "home"),
                        }
                        parts.append("【穿搭事实】" + _json.dumps(_facts, ensure_ascii=False))
                        try:
                            try:
                                from .laundry_engine import LifeFacts
                            except ImportError:
                                from laundry_engine import LifeFacts
                            _life = LifeFacts(self)
                            _life.tick()
                            _life_facts = _life.snapshot()
                            parts.append("【生活事实】" + _json.dumps(
                                _life_facts, ensure_ascii=False))
                            if _life_facts.get("hand_wash"):
                                parts.append(
                                    "【洗衣】家里没有洗衣机：衣服都是手洗的（30 分钟洗完再晾 6–12 小时），"
                                    "袜子每天顺手洗；不要说用洗衣机洗。")
                            try:
                                _cur = self.state.current_activity() or {}
                                self.db.kv_set("last_activity", _cur)
                            except Exception as exc:
                                _note_swallowed("amo_runtime.py.567", exc)
                        except Exception as exc:
                            _note_swallowed("amo_runtime.py.573", exc)
                    except Exception as exc:
                        _note_swallowed("amo_runtime.py.575", exc)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
        return parts

    def _ctx_outfit_change(self: Any, event):
        # 「换一套」：在外面换不了 / 睡衣 / 白天在家；返回提示块列表
        _ctx_mod = _mod()
        parts: list[str] = []
        try:
            is_private = bool(event.is_private_chat())
        except Exception:
            is_private = False
        try:
            if is_private and getattr(self, "wardrobe", None) is not None:
                text_now = event.message_str or ""
                # 2026-11-23：先贬后夸不是嫌她/让她换；交给 _ctx_intimacy 接住夸。
                if _ctx_mod.is_contrast_praise(text_now) and not _ctx_mod.has_explicit_change_request(text_now):
                    return parts
                if _ctx_mod.is_outfit_change_request(text_now):
                    at_home = self.wardrobe.at_home()
                    at_hotel = self.wardrobe.at_hotel()
                    pajama = _ctx_mod.is_pajama_change_request(text_now)
                    try:
                        hour = int(_ctx_mod._now().hour)
                    except Exception:
                        hour = 20
                    night_time = hour >= 20 or hour < 8
                    place = ""
                    try:
                        place = self._short_place((self.state.current_activity() or {}).get("place") or "")
                    except Exception:
                        place = ""
                    if at_home is False and not at_hotel:
                        # 白天在外面：换不了，按人设解释，别假装换了
                        parts.append(
                            "【换装】他让你换一套，但你现在在外面"
                            + ("（" + place + "）" if place else "")
                            + "，换不了衣服。用你的人设语气（无奈/撒娇/讲道理都行）跟他说清楚："
                            "在外面不方便换，回家再换；不要答应，也不要假装已经换好。"
                        )
                    elif pajama:
                        changed = self.wardrobe.set_night(reroll=True)
                        self.wardrobe.current()
                        wear = "、".join([part for part in (changed.get("top"), changed.get("bottom")) if part])
                        if wear:
                            parts.append(
                                "【换装】他嫌睡衣不好看，你顺手换了一套睡衣：" + wear
                                + "（今晚穿）。用你的人设语气接一句，带点被他说动/配合的感觉，别报后台。"
                            )
                    elif night_time:
                        changed = self.wardrobe.set_night(reroll=True)
                        self.wardrobe.current()
                        wear = "、".join([part for part in (changed.get("top"), changed.get("bottom")) if part])
                        if wear:
                            parts.append(
                                "【换装】他让你换一套，你晚上在家，已经换上新的睡衣了：" + wear
                                + "。用你的人设语气接一句（配合/撒娇/嫌他事多都可以），别报后台。"
                            )
                    else:
                        # 白天在家：可以换白天这套
                        changed = self.wardrobe.reroll()
                        self.wardrobe.current()
                        wear = "、".join([part for part in (changed.get("top"), changed.get("bottom")) if part])
                        if wear:
                            parts.append(
                                "【换装】他让你换一套，你正好在家，已经换好新的白天穿搭：" + wear
                                + "。用你的人设语气接一句（被夸/配合/撒个娇都行），别报后台。"
                            )
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
        return parts


    def _ctx_tail_world(self: Any, c, parts):
        _ctx_mod = _mod()
        # 日期事实/约会状态/社交学习/住处/微型事件/归属纠正
        event = c["event"]
        umo = c["umo"]
        is_group = c["is_group"]
        try:
            _date_fact = self._date_fact_line()
            if _date_fact:
                parts.insert(0, "【最高事实】" + _date_fact)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context.date_fact", _amo_exc)
        try:
            try:
                from .date_state import prompt_line as _date_state_line
            except ImportError:
                from date_state import prompt_line as _date_state_line
            _date_line = _date_state_line(self)
            if _date_line:
                parts.append(_date_line)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context.date_state", _amo_exc)
        try:
            if getattr(self, "housing", None) is not None:
                parts.append(self.housing.prompt_line())
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context.housing", _amo_exc)
        try:
            _group = not bool(event.is_private_chat())
        except Exception:
            try:
                _group = bool(event.get_group_id())
            except Exception:
                _group = False
        if not _group:
            try:
                try:
                    from . import amo_household as _hh
                except ImportError:
                    import amo_household as _hh
                _presence = _hh.presence_line(self)
                if _presence:
                    parts.append(_presence)
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context.household", _amo_exc)
        try:
            if getattr(self, "micro_events", None) is not None:
                _micro = self.micro_events.injection()
                if _micro:
                    parts.append(_micro)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context.micro_events", _amo_exc)
        if (not is_group) and _ctx_mod.first_person_experience is not None:
            body_now = event.message_str or ""
            try:
                if _ctx_mod.first_person_experience(body_now):
                    parts.append(
                        "【归属】他刚说的「" + _ctx_mod.truncate(body_now, 36) + "」是他自己的经历，不是你做的；"
                        "不要接成“我也在/我也吃了/我也去了”。"
                    )
                elif _ctx_mod.now_ts() - _ctx_mod.to_float(getattr(self, "_attribution_alert", {}).get(umo, 0), 0) < 600:
                    parts.append("【归属】上一轮你把他的经历说成了自己的；这次分清：他说的是他，你说的是你。")
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)

