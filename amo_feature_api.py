# -*- coding: utf-8 -*-
"""陪伴插件对外状态/事件 API（守护插件读取陪伴状态的唯一入口）。

对外签名固化：get_hunger / add_hunger / hunger_state / ... / on_forced_overtime。
内部实现可以变，方法名、参数、返回值不要改。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
logger = logging.getLogger("amo")

try:
    from .amo_shared import now_ts
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import now_ts

try:  # 约会会话单一源（Phase 2）
    from . import amo_date_session as _date_session
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_date_session as _date_session


def _contract_module(name):
    """读 services/<name>.py（包模式与顶层导入都兼容）；失败抛 ImportError。

    task-27：跨插件只读契约实现放在 services/ 下，FeatureApiMixin 只做透传；
    用模块级函数而不是 self._xxx 方法，避免 main.py 挂载公开方法时漏掉私有辅助。
    """
    import importlib as _importlib
    name = str(name or "")
    package = str(__package__ or "")
    if package:
        try:
            return _importlib.import_module("." + name, package + ".services")
        except ImportError:  # cleanup: 兜底/降级，保持原行为
            pass
    return _importlib.import_module("services." + name)


#: 宵夜决定审计键（amo_food_event 写入：late_snack_decision:<day>:<hh>）
LATE_SNACK_DECISION_PREFIX = "late_snack_decision:"
#: 同一小时只决定一次的防重键（amo_food_event 写入：late_snack:<day>:<hh>）
LATE_SNACK_SLOT_PREFIX = "late_snack:"
#: 决定记录只保留今天 + 昨天；更早的在当天首次 tick 时清掉
LATE_SNACK_KEEP_DAYS = 2


def _late_snack_day(text):
    """把键里的日期段解析成 date；不是 YYYY-MM-DD 就返回 None。"""
    import datetime as _date

    try:
        return _date.datetime.strptime(str(text or ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _late_snack_key_parts(key):
    """late_snack_decision:2026-11-17:22 -> ("2026-11-17", 22)；坏键返回 ("", 0)。"""
    parts = str(key or "").split(":")
    if len(parts) < 3:
        return "", 0
    try:
        hour = int(parts[-1])
    except (TypeError, ValueError):
        hour = 0
    return str(parts[-2]), hour


def _late_snack_rows(plugin, prefix, limit=1000):
    """一次读一批 kv 行（key/value）；优先 kv_rows，退 kv_keys + kv_get，失败返回 []。

    P2：决定页只扫一次 kv，不逐键 kv_get 反复打库。
    """
    db = getattr(plugin, "db", None)
    if db is None:
        return []
    reader = getattr(db, "kv_rows", None)
    if callable(reader):
        try:
            try:
                rows = reader(prefix, limit)
            except TypeError:      # 老签名只收前缀
                rows = reader(prefix)
        except Exception as exc:
            logger.debug("[amo] 扫 kv 失败 %s: %r", prefix, exc)
            rows = None
        if isinstance(rows, list):
            return [row for row in rows
                    if isinstance(row, dict)
                    and str(row.get("key") or "").startswith(prefix)]
    keys = getattr(db, "kv_keys", None)
    if not callable(keys):
        return []
    try:
        names = keys(prefix, limit)
    except TypeError:
        names = keys(prefix)
    except Exception as exc:
        logger.debug("[amo] 列 kv 键失败 %s: %r", prefix, exc)
        return []
    out = []
    for name in names or []:
        try:
            value = db.kv_get(name, None)
        except Exception:
            value = None
        out.append({"key": str(name), "value": value})
    return out


def _late_snack_decision_row(day, hour, payload):
    """kv 行 -> 面板行；缺失字段留空，不崩。"""
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        decision = {}
    factors = decision.get("factors")
    if not isinstance(factors, dict):
        factors = {}
    eat = payload.get("eat")
    if eat is None:
        eat = decision.get("eat")
    amount = payload.get("charged")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        amount = 0
    return {
        "day": str(day or ""),
        "hour": int(hour or 0),
        "eat": bool(eat),
        "score": decision.get("score", ""),
        "roll": decision.get("roll", ""),
        "factors": dict(factors),
        "food": str(payload.get("food") or ""),
        "amount_cents": int(amount or 0),
        "block_id": str(payload.get("block_id") or ""),
    }


def _late_snack_delete(db, keys):
    """批量删 kv 键（优先一次 kv_delete_many）；返回实际删除数。"""
    rows = sorted({str(key) for key in keys or [] if str(key)})
    if not rows:
        return 0
    many = getattr(db, "kv_delete_many", None)
    if callable(many):
        try:
            return int(many(rows) or 0)
        except Exception as exc:
            logger.debug("[amo] 批量删 kv 失败: %r", exc)
    deleted = 0
    for method in ("kv_delete", "kv_del"):
        one = getattr(db, method, None)
        if not callable(one):
            continue
        for key in rows:
            try:
                deleted += 1 if one(key) else 0
            except Exception:
                continue
        break
    return deleted


def _current_scene_meta(plugin, now=None):
    """当前场景版本（task-30：约会事实也带 scene_id，消费方可校验是否同一场景）。"""
    try:
        try:
            from .amo_scene_ledger import scene_at as _scene_at
        except ImportError:
            from amo_scene_ledger import scene_at as _scene_at
        scene = _scene_at(plugin, now) or {}
    except Exception:
        return {}
    if not isinstance(scene, dict) or not scene:
        return {}
    return {"scene_id": str(scene.get("event_id") or ""),
            "scene_zone": str(scene.get("zone") or ""),
            "scene_place": str(scene.get("place") or "")}

try:
    from .amo_api_persona import PersonaApiMixin
except ImportError:
    if not TYPE_CHECKING:
        from amo_api_persona import PersonaApiMixin


class FeatureApiMixin(PersonaApiMixin):
        if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
            _send_to_umo: Any
            cfg_str: Any
            db: Any
            hunger: Any
            state: Any
            wardrobe: Any
        def _relation_overrides(self):
            try:
                data = self.db.kv_get("relation_overrides", {}) or {}
            except Exception:
                data = {}
            return dict(data) if isinstance(data, dict) else {}

        def relation_of(self, name=""):
            """名单人物的关系标签：用户覆盖 > amo_names.RELATION_LABEL。"""
            try:
                from . import amo_names
            except ImportError:
                import amo_names
            key = str(name or "").strip()
            override = self._relation_overrides().get(key) or {}
            if isinstance(override, dict) and override.get("relation"):
                return str(override.get("relation"))
            return amo_names.relation_of(key)

        def circle_of(self, name=""):
            """名单人物的圈层：用户覆盖 > amo_names.RELATION_CIRCLE。"""
            try:
                from . import amo_names
            except ImportError:
                import amo_names
            key = str(name or "").strip()
            override = self._relation_overrides().get(key) or {}
            if isinstance(override, dict) and override.get("circle"):
                return str(override.get("circle"))
            return amo_names.circle_of(key)

        def set_relation(self, name, relation="", circle=""):
            """用户改关系/圈层：写覆盖层（守护迁移/面板编辑共用）。"""
            key = str(name or "").strip()
            if not key:
                return False
            data = self._relation_overrides()
            current = dict(data.get(key) or {})
            if str(relation or "").strip():
                current["relation"] = str(relation).strip()
            if str(circle or "").strip():
                current["circle"] = str(circle).strip()
            if not current:
                return False
            data[key] = current
            try:
                self.db.kv_set("relation_overrides", data)
            except Exception:
                return False
            return True

        def note_expense_summary(self, line):
            """外部（守护/账本流）写「今日支出」缓存：以钱包实时口径为准，只对当天有效。

            2026-11-22 P0：旧实现 48h TTL，扣完款仍显示「今日总支出 0.00」；
            而且外部传进来的数字可能把存款/投资算成支出。这里一律请钱包按当天账本
            重算（排除存款/投资/转账/退款），TTL 只到当天结束。
            """
            import datetime as _dt
            text = str(line or "")
            wallet = getattr(self, "wallet", None)
            maker = getattr(wallet, "expense_summary_line", None)
            if callable(maker):
                try:
                    text = str(maker() or text)
                except Exception as exc:
                    logger.debug("[amo] 重算今日支出失败，沿用外部文本: %r", exc)
            moment = _amo_clock.now()
            end = _dt.datetime.combine(moment.date(), _dt.time.min).timestamp() + 86400.0
            self.db.kv_set("expense_summary", {"line": text, "until": end,
                                               "day": moment.strftime("%Y-%m-%d"),
                                               "source": "wallet"})

        def get_hunger(self, target="amo"):

            """给守护/面板读：当前饥饿值 0-100。"""

            return self.hunger.get()



        def add_hunger(self, target="amo", amount=0):

            """吃东西后加饱腹度；target 目前只有她本人（amo）。"""

            return self.hunger.add(amount)



        def hunger_state(self):

            return self.hunger.state()



        def mark_meal(self, food, cooldown_hours=4.0, mood="满足"):

            self.hunger.mark_meal(food, cooldown_hours, mood)



        def request_hunger_help(self):

            self.hunger.request_help()



        def note_transfer_event(self, amount_cents):

            """记录一笔他转来的钱，供“转账推拉”Prompt 用。"""

            import time as _t

            now = _t.time()

            self.db.kv_set("transfer:last", {"amount": int(amount_cents or 0), "ts": now})

            self.db.kv_set("positive:last", now)

        def note_purchase(self, name, price_cents):

            """记录她刚买的贵东西，供“花钱心虚”Prompt 用。"""

            import time as _t

            self.db.kv_set("last_purchase", {"name": str(name or "东西"),

                                             "price": int(price_cents or 0), "ts": _t.time()})

        def _owner_umos(self):

            """她能主动找到他的会话：qq / 飞书私聊优先。"""

            umos = []

            try:

                rows = self.db.proactive_profiles(limit=10) or []

            except Exception:

                rows = []

            for row in rows:

                for key in ("private_umo", "last_group"):

                    umo = str((row or {}).get(key) or "").strip()

                    if umo and umo not in umos:

                        umos.append(umo)

                try:

                    data = self.db.kv_get("channels:%s" % (row or {}).get("uid"), {}) or {}

                    for value in data.values():

                        umo = str((value or {}).get("umo") or "").strip()

                        if umo and umo not in umos:

                            umos.append(umo)

                except Exception as exc:

                    logger.debug("[amo] optional step failed: %r", exc)

                if umos:

                    break

            return umos



        async def request_push(self, text, reason=""):
            """外部模块（守护）申请一条主动消息：静默时段和最小间隔由陪伴统一把关。

            2026-11-20：_owner_umos 修好后，守护原来哑掉的推送会突然变多；
            这里给它一个统一闸口，避免夜间轰炸和连续刷屏。
            """
            text = str(text or "").strip()
            if not text:
                return False
            try:
                try:
                    from .amo_shared import quiet_now
                except ImportError:
                    from amo_shared import quiet_now
                quiet = bool(quiet_now(self.cfg_str("quiet_hours", "23:30-07:30")))
            except Exception:
                quiet = False
            tag = str(reason or "default")[:24]
            # 宵夜（22-24 允许她说一句「我饿了」）与真正紧急的事故不受静默时段限制
            if quiet and tag not in ("urgent", "late_snack"):
                logger.info("[amo] 静默时段，拦下外部主动消息（%s）", tag)
                return False
            key = "external_push_ts:" + tag
            try:
                last = float(self.db.kv_get(key, 0) or 0)
            except Exception:
                last = 0.0
            now = now_ts()
            if last and now - last < 600:
                logger.info("[amo] 距同类上一条主动消息不足 10 分钟，跳过（%s）", tag)
                return False
            sent = await self.push_to_owner(text)
            if sent:
                try:
                    self.db.kv_set(key, now)
                except Exception as exc:
                    logger.debug("[amo] 记录 %s 失败: %r", key, exc)
            return bool(sent)

        def remove_schedule_block(self, day, start="", activity=""):
            """删除某天某时段的一个块（约会取消/替换用）；走 commit 唯一入口。"""
            day = str(day or "")
            if not day:
                return {"ok": False, "reason": "no_day"}
            try:
                items = list(self.state.schedule.get(day) or [])
            except Exception:
                items = []
            start = str(start or "")[:5]
            activity = str(activity or "")
            if not start and not activity:
                return {"ok": False, "reason": "bad_args"}

            def _hit(item):
                if start and str(item.get("start") or "")[:5] != start:
                    return False
                if activity and activity not in str(item.get("activity") or ""):
                    return False
                return True

            kept = [item for item in items if not _hit(item)]
            if len(kept) == len(items):
                # 未来日期的约会只在 todo 表：按 start/activity 关掉对应待办
                closed = 0
                try:
                    for row in list(self.state.todos(day)):
                        if start and str(row.get("start") or "")[:5] != start:
                            continue
                        if activity and activity not in str(row.get("activity") or ""):
                            continue
                        if self.db.close_todo(int(row.get("id") or 0)):
                            closed += 1
                except Exception as exc:
                    logger.debug("[amo] 删除未来待办失败: %r", exc)
                return {"ok": True, "changed": bool(closed), "todo_removed": closed}
            try:
                result = self.state.commit_schedule_change(
                    day, kept, source="date_remove", reason="取消/替换约会")
            except Exception as exc:
                return {"ok": False, "reason": "commit_failed", "error": str(exc)[:80]}
            return {"ok": True, "changed": bool(result.get("saved", True)), "result": result}

        async def push_to_owner(self, text, kind=""):

            """主动消息：给管理员/老公发一条（逛街试穿、回家炫耀、守护事件等）。"""

            text = str(text or "").strip()

            if not text:

                return False

            # 唯一在场策略：用户最后一条消息超过 6 小时，禁止催问/逛街/自拍/追问，
            # 只留早晚问候和明确时间点提醒（2026-11-22 真实飞书记录）。
            try:
                try:
                    from . import amo_presence as _presence
                except ImportError:
                    import amo_presence as _presence
                _profile = {}
                try:
                    _rows = list(self.db.proactive_profiles(limit=1) or [])
                    if _rows and isinstance(_rows[0], dict):
                        _profile = _rows[0]
                except Exception as exc:
                    logger.debug("[amo] push_to_owner 画像查询失败: %r", exc)
                if not _presence.can_push(self, _profile, kind, now=now_ts()):
                    logger.info("[amo] 推送被沉默 6 小时规则拦下（%s）", str(kind or "-"))
                    return False
            except Exception as exc:
                logger.debug("[amo] push_to_owner 在场策略检查失败: %r", exc)

            # 2026-11-21 协调层：守护/网购/试穿的推送也要遵守主动免打扰和 5 分钟节流，
            # 不能绕过陪伴自己的概率/晚安/夜间规则。
            try:
                from .amo_shared_time import quiet_now
                _spec = self.cfg_str("proactive_quiet_hours", "23:00-07:30")
                if quiet_now(_spec):
                    logger.info("[amo] 推送被主动免打扰拦下（%s）", _spec)
                    return False
            except Exception as exc:
                logger.debug("[amo] push_to_owner 免打扰检查失败: %r", exc)
            for umo in self._owner_umos():

                try:

                    # 频控/5 分钟节流/原子占位都在 amo_outbound 统一出口里做，
                    # 这里不再维护第二份 _proactive_burst 字典（读-判-写非原子）。
                    _old_source = getattr(self, "_outbound_source", "")
                    _old_kind = getattr(self, "_outbound_kind", "")
                    self._outbound_source = "push"
                    self._outbound_kind = str(kind or "")
                    try:
                        sent = await self._send_to_umo(umo, text)
                    finally:
                        self._outbound_source = _old_source
                        self._outbound_kind = _old_kind
                    if sent:
                        return True

                except Exception:

                    continue

            return False

        def can_shop(self):
            """watch 逛街入口：唯一策略在 amo_presence.can_shop（人不在商场/街上/景区就不逛）。"""
            try:
                from . import amo_presence as _presence
            except ImportError:
                import amo_presence as _presence
            return bool(_presence.can_shop(self))

        def can_engage(self, kind="", profile=None):
            """watch 推送入口：统一走 amo_presence.can_proactive（沉默/地点/类型一起判）。"""
            try:
                from . import amo_presence as _presence
            except ImportError:
                import amo_presence as _presence
            return bool(_presence.can_proactive(self, profile, kind, now=now_ts()))

        async def hunger_help(self, message):

            """饿到没钱：写进状态，并真的给他发一条求助。"""

            self.hunger.request_help(message)

            for umo in self._owner_umos():

                try:

                    await self._send_to_umo(umo, message, source="push", kind="hunger")

                    return True

                except Exception:

                    continue

            return False



        def set_date_mood(self, line, until_ts=0):

            """守护插件写进来的约会情绪提示（期待/失落/冷淡），到点自动失效。"""

            self.db.kv_set("date_mood_line", {"line": str(line or ""), "until": float(until_ts or 0)})

        def set_date_separation(self, title, ended_ts=0):
            import time as _time
            base = float(ended_ts or _amo_clock.now_ts())
            self.db.kv_set("date_separation", {"title": str(title or "约会"),
                                               "ended_ts": base,
                                               "until": base + 6 * 3600})

        def set_date_home(self, title, at=0):
            """约会收尾：约会结束，他不在她身边；她一个人回到家（同城不同住、各回各家），只能手机/视频联系。"""
            _date_session.finish(self, at or None, reason="home", title=title)

        def set_date_now(self, line, until_ts=0, since=None):
            """约会进行中：最高事实 + 锁定当前行为（守护插件 /赴约 调用）。"""
            text = str(line or "").strip()
            if not text:
                _date_session.clear_now(self)
                return
            _date_session.begin(self, text, until_ts, since or 0, source="feature")

        def clear_date_now(self):
            """只清「进行中」；已经回家/结束的会话保持原样，别把回家事实抹掉。"""
            _date_session.clear_now(self)

        def set_date_meet(self, title, place="", ttl=900):
            """碰面：只记事实（事件/地点），开场白由大模型按事实自己生成。"""
            _date_session.meet(self, title, place, ttl)

        def set_date_place(self, place):
            """约会中途换地点（换店/转场）：地点也归会话单一源。"""
            _date_session.set_place(self, place)

        def set_date_pause(self, minutes=15):
            """约会中途他走开（洗手间/接电话）：窗口不结束，只记暂停。"""
            _date_session.pause(self, minutes)

        def clear_date_pause(self):
            _date_session.resume(self)

        def set_date_ending(self, fact, reason="", ttl=600):
            """约会收尾：只写客观事实，道别话由大模型现场生成（禁止模板）。"""
            _date_session.request_end(self, fact, reason, ttl, sent=False)

        def clear_date_ending(self):
            _date_session.clear_ending(self)

        def finish_date_session(self, title="", reason="", now=None):
            """守护插件收尾：一次关窗 + together=False + 回家投影，不再各写各的。"""
            return _date_session.finish(self, now, reason=reason, title=title)

        def cancel_date_session(self, reason="", now=None):
            """约会中断：关窗但不写「一个人回到家」。"""
            return _date_session.cancel(self, reason, now)

        def date_session(self, now=None):
            """对外只读：当前约会会话（窗口/地点/together/收尾）。"""
            return _date_session.digest(self, now)

        def is_dating(self, now=None):
            return _date_session.is_dating(self, now)

        def date_together(self, now=None):
            return _date_session.together(self, now)

        def date_ending(self, now=None):
            return _date_session.ending(self, now)

        def set_date_fact(self, line, until_ts=0):
            import time as _t
            stamp = float(until_ts or 0) or _t.time()
            day = _t.strftime("%Y-%m-%d", _t.localtime(stamp))

            """约会已发生：当天内最高事实，防“一个人吃饭”幻觉。"""

            try:
                from .amo_state_store import for_plugin as _state_store_for
            except ImportError:
                from amo_state_store import for_plugin as _state_store_for
            _state_store_for(self).set_ttl(
                "date_fact", {"line": str(line or ""), "day": day}, 0,
                until=float(until_ts or 0))

        def set_last_date_event(self, activity, ended_ts=0, hours=6):

            """约会结束锁：6 小时内她必须记得“刚和老公共度”，防失忆。"""

            import time as _t

            end = float(ended_ts or _t.time())

            self.db.kv_set("last_date_event", {

                "activity": str(activity or "约会"), "ended_ts": end,

                "until_ts": end + float(hours or 6) * 3600,

                "day": _t.strftime("%Y-%m-%d", _t.localtime(end)),

                **_current_scene_meta(self),

            })

        def set_date_prep(self, activity, start_ts=0):

            """约会前准备：化妆/挑衣服，别让她秒回“我到了”。"""

            self.db.kv_set("date_prep", {"activity": str(activity or "约会"),

                                          "start_ts": float(start_ts or 0),

                                          **_current_scene_meta(self)})

        def set_date_incident(self, text, until_ts=0):

            """约会中的随机小插曲（下雨/排队/试衣服），让聊天有话题。"""

            self.db.kv_set("date_incident", {"text": str(text or ""),

                                             "until_ts": float(until_ts or 0),

                                             **_current_scene_meta(self)})

        def set_current_action(self, action, until_ts=0, place="外面", zone="restaurant", category="social"):

            """守护插件写当前行为：压过 JIT 到点决策，到点自动失效。

            2026-11-15：写入口收敛到 amo_current_action.set_action（全局状态锁），
            日程 Tick / 微型事件 / 守护插件共用同一条写路径。
            """

            try:

                try:
                    from .amo_current_action import set_action as _set_action
                except ImportError:
                    from amo_current_action import set_action as _set_action

                if _set_action(self, action, until_ts, place=place, zone=zone,
                               category=category, source="writer"):
                    return
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)

            # task-30：兜底分支也不绕过场景源——先过 scene_ledger.write，被硬约束拒绝就不写。
            try:

                try:
                    from .amo_scene_ledger import write as _scene_write
                except ImportError:
                    from amo_scene_ledger import write as _scene_write

                _written = _scene_write(

                    self, {"activity": str(action or ""), "place": str(place or ""),

                           "zone": str(zone or ""), "category": str(category or "")},

                    source="writer")

                _record = _written.get("record") if isinstance(_written, dict) else {}

                if not _written.get("ok") or not isinstance(_record, dict) or not _record:

                    return

            except Exception as exc:

                logger.debug("[amo] optional step failed: %r", exc)

                return

            self.db.kv_set("current_action", {

                "action": str(_record.get("activity") or action or ""),

                "place": str(_record.get("place") or place or ""),

                "zone": str(_record.get("zone") or zone or ""),

                "category": str(_record.get("category") or category or ""),

                "scene_id": str(_record.get("event_id") or ""),

                "at": now_ts(), "until": float(until_ts or 0),

            })



        def mark_hunger_cooldown(self, minutes):

            self.hunger.mark_cooldown(minutes)



        def late_night_state(self):

            if getattr(self.state, "late", None) is None:

                return {}

            return self.state.late.state()



        def note_late_snack(self, amount_cents, food=""):

            if getattr(self.state, "late", None) is not None:

                self.state.late.note_snack(amount_cents, food)



        def note_late_fine(self, amount_cents):

            if getattr(self.state, "late", None) is not None:

                self.state.late.note_fine(amount_cents)



        def soothe_late_night(self, reason=""):

            if getattr(self.state, "late", None) is not None:

                return self.state.late.soothe(reason)

            return None



        def late_snack_decisions(self, days=1, limit=50):
            """守护面板只读：最近的宵夜自主决定，按 day/hour 倒序。

            days=1（默认）只看今天；缺失/旧格式的行留空字段，不抛。
            """
            import datetime as _date

            try:
                days = max(1, int(days or 1))
            except (TypeError, ValueError):
                days = 1
            try:
                limit = max(1, int(limit or 50))
            except (TypeError, ValueError):
                limit = 50
            today = _amo_clock.now().date()
            first_day = today - _date.timedelta(days=days - 1)
            rows = []
            for row in _late_snack_rows(self, LATE_SNACK_DECISION_PREFIX, 2000):
                day, hour = _late_snack_key_parts(row.get("key"))
                when = _late_snack_day(day)
                if when is None or when < first_day:
                    continue
                payload = row.get("value")
                if not isinstance(payload, dict):
                    payload = {}
                rows.append(_late_snack_decision_row(day, hour, payload))
            rows.sort(key=lambda item: (item["day"], item["hour"]), reverse=True)
            return rows[:limit]

        def prune_decisions(self, days=LATE_SNACK_KEEP_DAYS):
            """只保留最近 days 个自然日的宵夜决定/小时锁键；重复调用幂等。"""
            import datetime as _date

            try:
                days = max(1, int(days or LATE_SNACK_KEEP_DAYS))
            except (TypeError, ValueError):
                days = LATE_SNACK_KEEP_DAYS
            today = _amo_clock.now().date()
            first_day = today - _date.timedelta(days=days - 1)
            db = getattr(self, "db", None)
            if db is None:
                return {"ok": False, "deleted": 0, "kept_days": days}
            doomed = []
            for prefix in (LATE_SNACK_DECISION_PREFIX, LATE_SNACK_SLOT_PREFIX):
                for row in _late_snack_rows(self, prefix, 5000):
                    day, _hour = _late_snack_key_parts(row.get("key"))
                    when = _late_snack_day(day)
                    if when is not None and when < first_day:
                        doomed.append(str(row.get("key")))
            deleted = _late_snack_delete(db, doomed)
            return {"ok": True, "deleted": deleted, "kept_days": days}

        async def late_snack_tick(self, now=None, roll=None, say=None):

            """宵夜领域入口：决定 → 日程块 → 扣款 → 日记/主动；守护插件只调它。"""

            # 每天第一次 tick 清掉超过 2 天的决定/小时锁键。main.py 只把公开方法搬到
            # 插件类上，所以这里直接调 public 的 prune_decisions，不依赖下划线辅助方法。
            try:
                today = _amo_clock.now().date().isoformat()
                if getattr(self, "_late_snack_pruned_day", "") != today:
                    self.prune_decisions(LATE_SNACK_KEEP_DAYS)
                    self._late_snack_pruned_day = today
            except Exception as exc:
                logger.debug("[amo] 宵夜决定清理失败: %r", exc)

            try:

                from .amo_food_event import late_snack_tick as _entry

            except ImportError:

                from amo_food_event import late_snack_tick as _entry

            return await _entry(self, now=now, roll=roll, say=say)

        def current_commute_mode(self, now=None):

            """今天的通勤方式（守护买交通/陪伴拍照共用）：地铁/公交/共享单车/打车/开车/走路。"""

            now = now or _amo_clock.now()
            day = now.strftime("%Y-%m-%d")
            state = getattr(self, "state", None)
            try:
                rows = list((getattr(state, "schedule", {}) or {}).get(day) or [])
            except Exception:
                rows = []
            low, high = 6 * 60, 10 * 60 + 30
            mapping = {"subway": "地铁", "bus": "公交", "taxi": "打车", "drive": "开车",
                       "bicycle": "共享单车", "bike": "共享单车", "walk": "走路"}
            words = (("共享单车", "共享单车"), ("单车", "共享单车"), ("骑车", "共享单车"),
                     ("自行车", "共享单车"), ("公交", "公交"), ("巴士", "公交"),
                     ("地铁", "地铁"), ("打车", "打车"), ("出租", "打车"), ("网约", "打车"),
                     ("开车", "开车"), ("自驾", "开车"))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    sh, sm = str(row.get("start") or "")[:5].split(":")
                    eh, em = str(row.get("end") or "")[:5].split(":")
                    start, end = int(sh) * 60 + int(sm), int(eh) * 60 + int(em)
                except (TypeError, ValueError):
                    continue
                if not (start < high and low < end):
                    continue
                code = str(row.get("transit") or "").strip().lower()
                if code in mapping:
                    return mapping[code]
                text = "%s %s %s" % (row.get("activity") or "", row.get("vehicle") or "",
                                     row.get("detail") or "")
                for word, mode in words:
                    if word in text:
                        return mode
            return ""

        def shopping_trigger(self, now=None):

            """周末 70%、发工资、情绪低落时想逛街；返回原因或空串。"""

            import datetime as _dt

            import hashlib

            now = now or _amo_clock.now()

            # 唯一在场策略：只有人真的在商场/街上/景区（或活动明确逛街/试衣）才触发；
            # 在家休息不能凭空说在试衣服（2026-11-22 真实飞书记录 09:00）。
            try:
                from . import amo_presence as _presence
            except ImportError:
                import amo_presence as _presence
            if not _presence.can_shop(self):
                return ""

            day = now.date().isoformat()

            if now.weekday() >= 5:

                roll = int(hashlib.md5(day.encode()).hexdigest()[:8], 16) % 100

                if roll < 70:

                    return "周末逛街"

            if now.day == 15:

                return "发工资犒劳自己"

            try:

                mood = self.state.mood

                if float(mood.get("郁闷", 0)) + float(mood.get("焦虑", 0)) >= 130:

                    return "心情不好花钱发泄"

            except Exception as exc:

                logger.debug("[amo] optional step failed: %r", exc)

            return ""



        def add_closet_item(self, item_data):

            """购物同步衣橱：name/color/category/time_slot/price。"""

            if getattr(self, "wardrobe", None) is None:

                return {"ok": False, "msg": "衣橱未就绪"}

            data = dict(item_data or {})

            name = str(data.get("name") or "").strip()

            if not name:

                return {"ok": False, "msg": "衣服名字为空"}

            cat = str(data.get("category") or "top")

            cat = {"上衣": "top", "裤子": "bottom", "裙子": "skirt", "外套": "coat",

                   "鞋子": "shoes", "睡衣": "top"}.get(cat, cat)

            slot = str(data.get("time_slot") or "day")

            sleep = slot == "night"

            res = self.wardrobe.add_item(cat, name, source="shopping", sleep=sleep)
            try:
                from .outfit_refs import spawn_ensure_ref
            except ImportError:
                from outfit_refs import spawn_ensure_ref
            if res.get("ok"):
                spawn_ensure_ref(self, res.get("item") or {"name": name, "category": cat})

            if res.get("ok") and res.get("item"):

                try:

                    db = self.wardrobe._load()

                    for row in db.get("items") or []:

                        if row.get("id") == res["item"].get("id"):

                            row["color"] = str(data.get("color") or "")

                            row["time_slot"] = slot

                            row["price"] = int(data.get("price") or 0)

                            break

                    self.wardrobe._save(db)

                except Exception as exc:

                    logger.debug("[amo] optional step failed: %r", exc)

                try:

                    import asyncio

                    from .amo_closet_image import ensure_after_add

                    asyncio.get_running_loop().create_task(ensure_after_add(self, res["item"]))

                except Exception as exc:

                    logger.debug("[amo] optional step failed: %r", exc)

            return res



        def persona_prompt_block(self, scene="", mood="", register="partner_safe"):
            try:
                from .amo_persona_store import active_pack
                from .amo_persona_pack import prompt_block
            except ImportError:
                from amo_persona_store import active_pack
                from amo_persona_pack import prompt_block
            try:
                return prompt_block(active_pack(self), scene=str(scene or ""),
                                    mood=str(mood or ""), register=str(register or "partner_safe"))
            except Exception:
                return ""

        def note_recent_topic(self, text, until_ts=0, key="", ttl_hours=36, day=""):
            """当天记忆缓冲：只留短事实，按 key 合并、限量、过期清理，不写记忆库。

            传 day 表示「把这条事实记在那一天」：第二天早上补写昨天的日记时，
            之前写的小日记事实必须仍然折进昨天，否则 day_digest:<day> 永远是空的
            （P0 死链路的写入侧，2026-11-22）。
            """
            try:
                from .amo_episodic import merge as _merge
            except ImportError:
                from amo_episodic import merge as _merge
            now = _amo_clock.now_ts()
            body = str(text or "").strip()[:80]
            if not body:
                return
            until = max(float(until_ts or 0), now + max(1, int(ttl_hours)) * 3600)
            try:
                raw = self.db.kv_get("episodic_memory", []) or []
            except Exception:
                raw = []
            try:
                from .amo_shared import today_str as _today
            except ImportError:
                from amo_shared import today_str as _today
            target_day = str(day or "").strip() or _today()
            items = _merge(raw, body, until, key=key, now=now, day=target_day)
            self.db.kv_set("episodic_memory", items)
            # 兼容旧读取方
            self.db.kv_set("recent_topic", {"text": body, "until": until})

        def note_shopping_pending(self, line, until_ts=0, scene_id="", name="", color="", price_cents=0):

            # 2026-11-22 task-25：逛街事实带 scene_id（场景账本 event_id），照片/约会/日记
            # 之后再引用这条事实时能对上是哪一段 schedule，不再靠地点词猜。
            # 2026-11-22 task-27：文案里写死的“卡里余额 4072 元”会过期（真实 3086.54）；
            # 这里同时存结构化字段（商品/价格/写入时的余额），快照据此判陈旧并给实时余额。
            record = {"line": str(line or ""), "until": float(until_ts or 0),
                      "name": str(name or ""), "color": str(color or "")}
            try:
                record["price_cents"] = int(price_cents or 0)
            except (TypeError, ValueError):
                record["price_cents"] = 0
            try:
                record["wallet_balance_cents_at_write"] = int(self.wallet.balance("cash") or 0)
            except Exception:
                record["wallet_balance_cents_at_write"] = 0
            record["written_at"] = _amo_clock.now_ts()
            sid = str(scene_id or "").strip()
            if not sid:
                try:
                    try:
                        from .amo_scene_ledger import scene_at as _scene_at
                    except ImportError:
                        from amo_scene_ledger import scene_at as _scene_at
                    _scene = _scene_at(self) or {}
                    sid = str(_scene.get("event_id") or "")
                    if _scene.get("zone"):
                        record["scene_zone"] = str(_scene.get("zone"))
                except Exception as exc:
                    logger.debug("[amo] 逛街事实 scene_id 读取失败: %r", exc)
            if sid:
                record["scene_id"] = sid
            self.db.kv_set("shopping_prompt", record)



        def note_gift_event(self, line, until_ts=0):

            self.db.kv_set("gift_line", {"line": str(line or ""), "until": float(until_ts or 0)})



        def on_big_stock_loss(self):

            if getattr(self.state, "late", None) is not None:

                self.state.late.on_big_stock_loss()



        def on_forced_overtime(self):

            if getattr(self.state, "late", None) is not None:

                self.state.late.on_forced_overtime()

        # ---- 跨插件只读契约（task-27）：LifeStateSnapshot / MetricsAPI ----
        # 守护/能记/定格只读这两个方法，禁止各自维护“此刻/余额/本月支出”副本。
        # 返回 dict 契约见 services/life_state.py、services/metrics.py。

        def life_state_contract(self):
            """LifeStateSnapshot 契约自述（版本/字段/单位）；纯常量，不读库。"""
            try:
                return dict(_contract_module("life_state").contract_descriptor())
            except Exception as exc:
                logger.debug("[amo] life_state_contract 不可用: %r", exc)
                return {}

        def life_state_snapshot(self, now=None):
            """只读状态快照：location/current_action/schedule/date_session/
            shopping/wallet/outfit/mood/media + consistency 自检。"""
            try:
                snapshot = _contract_module("life_state").build_snapshot(self, now)
                return dict(snapshot) if isinstance(snapshot, dict) else {}
            except Exception as exc:
                logger.warning("[amo] life_state_snapshot 构建失败: %r", exc)
                return {}

        def metrics_contract(self):
            """MetricsAPI 契约自述（指标定义/单位）；纯常量，不读库。"""
            try:
                return dict(_contract_module("metrics").contract_descriptor())
            except Exception as exc:
                logger.debug("[amo] metrics_contract 不可用: %r", exc)
                return {}

        def metrics_snapshot(self, now=None):
            """统一指标快照：钱包余额/本月消费/本月资产变动/今日生图（读失败只降级）。"""
            try:
                return dict(_contract_module("metrics").build_metrics(self, now) or {})
            except Exception as exc:
                logger.warning("[amo] metrics_snapshot 构建失败: %r", exc)
                return {}

        def month_consumption_cents(self, month=""):
            """本月消费（整数分）：面板/守护唯一口径，含退款/资产/账单排除。"""
            try:
                metrics = _contract_module("metrics")
                wallet = getattr(self, "wallet", None)
                return int(metrics.wallet_month_consumption_cents(wallet, str(month or "")))
            except Exception as exc:
                logger.debug("[amo] month_consumption_cents 失败: %r", exc)
                return 0
