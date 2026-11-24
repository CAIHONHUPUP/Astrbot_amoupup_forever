# -*- coding: utf-8 -*-
"""MediaGateway + MediaLedger（2026-11-22 task-23）：照片/媒体唯一事实源。

背景（work/audit/media_facts.md）：
- 照片是三条互不知情的链路：自动分享（photo_share）、send_photo 工具
  （draw_engine）、模型直接调外部插件 generate_selfie。后两条不记账、不占每日
  配额、不 claim 事件、不写 memory —— 记录覆盖 7/32=21.9%，11-19 单日 10 张
  是 cap=4 的 2.5 倍。
- [我发的照片] 占位符从非分段出口泄漏；失败话术把服务器/报错喂给模型。

本模块只做「媒体」这一件事：
- MediaLedger：kv 里的结构化账本（media_id/kind/source/scene_id/zone/place/
  pose_id/pose_name/pose_room/outfit/hash/event_id/umo/status/at/day）。
  photo_count、去重、审计全部从账本派生，不再各写各的 kv。
- MediaGateway.send_photo：三条照片链路的唯一发送入口。顺序固定：
  剥 caption 标记 → 内容签名去重（自动分享 6 小时） → 每日配额 → 事件占位
  （同一 event_id 最多一张）→ 统一出站出口（_send_media_to_umo，保留 task-16
  的 source=photo/kind 语义）→ 记一条账本 + 占 cap + 写 memory。
- record_external / reconcile_external：外部 generate_selfie 的回执收编
  （外部插件自己发图，只能按它写在 omni._selfie_turn_store 的证据补记）。
- failure_line：唯一失败话术出口，技术词一律不得出现在人话里。

不做的事：不 import 两个引擎（避免环形依赖）；调用方把插件实例传进来。
"""
from __future__ import annotations

import base64
import hashlib
import os
import time

try:  # Phase 11 统一时钟
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    import clock as _amo_clock

try:  # 统一吞异常留痕
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    from amo_observability import note as _note_swallowed

from astrbot.api import logger

#: 账本在 kv 里的键；按「最近 N 条」滚动保存，足够审计/去重/日计数。
LEDGER_KEY = "media_ledger"
LEDGER_LIMIT = 400
#: 自动分享的内容签名去重窗口（同签名 6 小时内不重发；用户明确要除外）。
DEDUPE_SECONDS = 6 * 3600
#: 同一 event_id 的账本兜底窗口（真正的占位在 amo_presence.claim_photo_event）。
EVENT_WINDOW_SECONDS = 12 * 3600
#: 外部 generate_selfie 证据（omni._selfie_turn_store 的 monotonic 时间）超过
#: 这么久就不再补记，避免插件启动时把历史上的旧自拍补记成刚发的。
EXTERNAL_RECENT_SECONDS = 600
#: 会计入每日照片配额的来源；test/chat/resend/manual 是用户明确要的独立通道。
COUNTED_SOURCES = ("auto", "tool", "external_selfie")
#: 会被每日配额拦截的来源（用户明确要图时不拦，只计数）。
CAP_ENFORCED_SOURCES = ("auto",)
#: 账本里合法的来源。
ORIGINS = ("auto", "tool", "external_selfie", "test", "chat", "resend", "manual")
#: 统一出站来源（task-16 OutboundGateway 的 source 词表）。
OUTBOUND_SOURCE = "photo"
#: 失败话术里的技术词：命中就不许说出口（替换成温柔兜底句）。
TECH_WORDS = ("服务器", "报错", "接口", "api", "API", "节点", "模型", "超时",
              "网络", "软件", "异常", "错误", "http", "HTTP", "error", "Error",
              "timeout", "traceback", "400", "401", "403", "500", "502", "503")
#: 唯一失败兜底句（温柔、不含技术词，也不承诺有图）。
FALLBACK_FAILURE_LINE = "这次没拍成，等会儿再给你拍；不是不理你。"

#: photo_share 的失败话术池（只读复用；photo_share 导入本模块，所以这里惰性取）。
_DEFAULT_FAILURE_LINES = (
    {"tone": "sweet", "text": "唔…这次没拍成，等我缓一下再给你拍，好不好？"},
    {"tone": "sweet", "text": "刚想弄给你看，结果没存上…等我一下下就好。"},
    {"tone": "playful", "text": "镜头今天跟我作对，刚想给你看就没了…别急嘛。"},
    {"tone": "playful", "text": "手滑了一下，什么都没留下来…再等我一会儿嘛。"},
    {"tone": "pout", "text": "没存下来，有点小郁闷。等我收拾一下再试一次。"},
    {"tone": "pout", "text": "刚才没拍好，不想给你看半成品，等我一下下。"},
    {"tone": "calm", "text": "今天状态不太对，拍出来不好看，晚点重新弄给你。"},
    {"tone": "calm", "text": "唔，没成功。等我换个角度再试一次，别催我啦。"},
)

_EXTERNAL_SEEN: dict = {}


# ---------------------------------------------------------------- 基础工具

def _now(now=None):
    if now is not None:
        try:
            return float(now)
        except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
            # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
            pass
    try:
        return float(_amo_clock.now_ts())
    except Exception:
        return time.time()


def _mono():
    return time.monotonic()


def _day_of(stamp):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(stamp)))
    except Exception:
        return ""


def _db(plugin):
    return getattr(plugin, "db", None)


def new_media_id():
    """一条账本记录的唯一 id（可读、稳定前缀）。"""
    return "m-%x-%s" % (int(_now() * 1000), os.urandom(3).hex())


def rows(plugin):
    """读账本（只读；坏数据按空列表）。"""
    db = _db(plugin)
    if db is None:
        return []
    try:
        data = db.kv_get(LEDGER_KEY, [])
    except Exception as exc:
        _note_swallowed("amo_media.rows", exc)
        return []
    return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _write_rows(plugin, data):
    db = _db(plugin)
    if db is None:
        return False
    try:
        db.kv_set(LEDGER_KEY, list(data[-LEDGER_LIMIT:]))
        return True
    except Exception as exc:
        _note_swallowed("amo_media.write_rows", exc)
        return False


def record(plugin, fields=None, **extra):
    """追加一条账本记录；返回落库后的 entry（失败也返回内存 entry）。"""
    data = dict(fields or {})
    data.update(extra)
    stamp = _now(data.get("at"))
    entry = {
        "media_id": str(data.get("media_id") or new_media_id()),
        "kind": str(data.get("kind") or "photo"),
        "label": str(data.get("label") or ""),
        "source": str(data.get("source") or "auto"),
        "status": str(data.get("status") or "sent"),
        "reason": str(data.get("reason") or "")[:80],
        "umo": str(data.get("umo") or ""),
        "scene_id": str(data.get("scene_id") or ""),
        "zone": str(data.get("zone") or ""),
        "place": str(data.get("place") or ""),
        "pose_id": str(data.get("pose_id") or ""),
        "pose_name": str(data.get("pose_name") or ""),
        "pose_room": str(data.get("pose_room") or ""),
        "outfit": str(data.get("outfit") or "")[:200],
        "hash": str(data.get("hash") or ""),
        "event_id": str(data.get("event_id") or ""),
        "caption": str(data.get("caption") or "")[:80],
        "test": bool(data.get("test")),
        "at": stamp,
        "day": str(data.get("day") or _day_of(stamp)),
    }
    history = rows(plugin)
    history.append(entry)
    _write_rows(plugin, history)
    return entry


# ---------------------------------------------------------------- 查询/派生

def _sent_rows(plugin, kind="photo"):
    out = []
    for entry in rows(plugin):
        if str(entry.get("kind") or "photo") != str(kind or "photo"):
            continue
        if str(entry.get("status") or "sent") != "sent":
            continue
        out.append(entry)
    return out


def photo_count(plugin, day=None):
    """某天真实发出的照片数（三条链路合计；测试/聊天重发不计）。"""
    day = str(day or _day_of(_now()))
    count = 0
    for entry in _sent_rows(plugin):
        if str(entry.get("source") or "") not in COUNTED_SOURCES:
            continue
        if str(entry.get("day") or _day_of(entry.get("at") or 0)) != day:
            continue
        count += 1
    return count


def find_recent(plugin, *, signature="", event_id="", window=0.0, now=None,
                umo="", kind="photo"):
    """在账本里找最近一条已发送的匹配记录（签名/事件 + 可选时间窗）。"""
    stamp = _now(now)
    for entry in reversed(_sent_rows(plugin, kind)):
        if umo and str(entry.get("umo") or "") != str(umo):
            continue
        if signature and str(entry.get("hash") or "") != str(signature):
            continue
        if event_id and str(entry.get("event_id") or "") != str(event_id):
            continue
        if not signature and not event_id:
            continue
        if window:
            try:
                if stamp - float(entry.get("at") or 0) > float(window):
                    continue
            except (TypeError, ValueError):
                continue
        return entry
    return {}


def last_photo(plugin, umo=""):
    """最近一张已发照片（可按会话过滤）。"""
    for entry in reversed(_sent_rows(plugin)):
        if umo and str(entry.get("umo") or "") != str(umo):
            continue
        return entry
    return {}


def coverage(plugin, real_count=0):
    """记录覆盖率（账本照片数 / 真实发送数）；给审计/日报用。"""
    try:
        real = int(real_count or 0)
    except (TypeError, ValueError):
        real = 0
    recorded = len(_sent_rows(plugin))
    return {"recorded": recorded, "real": real,
            "ratio": (float(recorded) / real) if real else 0.0}


def stats(plugin, now=None):
    """媒体日报用的只读统计（按来源/状态/天）。"""
    stamp = _now(now)
    day = _day_of(stamp)
    by_source: dict[str, int] = {}
    sent_today = 0
    for entry in rows(plugin):
        if str(entry.get("status") or "") == "sent":
            key = str(entry.get("source") or "?")
            by_source[key] = by_source.get(key, 0) + 1
            if str(entry.get("day") or "") == day:
                sent_today += 1
    return {"total": len(rows(plugin)), "sent_today": sent_today,
            "photos_today": photo_count(plugin, day), "by_source": by_source}


# ---------------------------------------------------------------- 内容签名

def signature_for(item):
    """生图结果 → 内容签名（本地文件/bytes/data_url 用内容哈希；URL 退化用引用哈希）。"""
    item = item if isinstance(item, dict) else {}
    images = item.get("images")
    if isinstance(images, list) and images and isinstance(images[0], dict):
        item = images[0]
    data = item.get("bytes") or item.get("data")
    if isinstance(data, (bytes, bytearray)):
        return hashlib.sha1(bytes(data)).hexdigest()
    data_url = str(item.get("data_url") or "")
    if data_url:
        payload = data_url.split(",", 1)[1] if "," in data_url else data_url
        try:
            return hashlib.sha1(base64.b64decode(payload)).hexdigest()
        except Exception:
            return hashlib.sha1(data_url.encode("utf-8", "ignore")).hexdigest()
    path = str(item.get("file_path") or item.get("path") or "")
    if path:
        try:
            if os.path.isfile(path):
                size = os.path.getsize(path)
                if size and size <= 8 * 1024 * 1024:
                    digest = hashlib.sha1()
                    with open(path, "rb") as fh:
                        for chunk in iter(lambda: fh.read(256 * 1024), b""):
                            digest.update(chunk)
                    return digest.hexdigest()
                return hashlib.sha1(("file:%s:%d" % (os.path.basename(path), size))
                                    .encode("utf-8")).hexdigest()
        except Exception as exc:
            _note_swallowed("amo_media.signature_for.file", exc)
    url = str(item.get("url") or item.get("image_url") or path or "")
    if not url:
        return ""
    return hashlib.sha1(("ref:" + url).encode("utf-8", "ignore")).hexdigest()


# ---------------------------------------------------------------- 场景/配额/事件

def _scene_meta(plugin, scene=None):
    """调用方给 scene 就用；没给就只读当前场景（不写、不降级）。"""
    data = dict(scene) if isinstance(scene, dict) else {}
    if not data:
        try:
            try:
                from .amo_scene_ledger import scene_at
            except ImportError:
                from amo_scene_ledger import scene_at
            data = dict(scene_at(plugin) or {})
        except Exception as exc:
            _note_swallowed("amo_media.scene_meta", exc)
            data = {}
    return {
        "scene_id": str(data.get("event_id") or data.get("scene_id") or ""),
        "zone": str(data.get("zone") or ""),
        "place": str(data.get("place") or ""),
    }


def _cap(plugin):
    try:
        try:
            from .amo_image_common import cfg
        except ImportError:
            from amo_image_common import cfg
        return max(0, int(cfg(plugin, "photo_daily_cap", 4, int, "amo_media.cap")))
    except Exception:
        return 4


def _claim_event(plugin, event_id, stamp):
    """事件占位（唯一实现仍是 amo_presence.claim_photo_event）。"""
    key = str(event_id or "").strip()
    if not key:
        return True
    try:
        try:
            from .amo_presence import claim_photo_event
        except ImportError:
            from amo_presence import claim_photo_event
        return bool(claim_photo_event(plugin, key, now=stamp))
    except Exception as exc:
        _note_swallowed("amo_media.claim_event", exc)
        return True


def _bump_count(plugin):
    """占每日配额：复用照片引擎的 kv 计数（photo_engine.today_count 也读账本）。"""
    photo = getattr(plugin, "photo", None)
    fn = getattr(photo, "_bump_count", None)
    if not callable(fn):
        return False
    try:
        fn()
        return True
    except Exception as exc:
        _note_swallowed("amo_media.bump_count", exc)
        return False


def _memory_marker(entry):
    """写进 messages/长期记忆的媒体记录（读侧整行剔除，只是内部审计标记）。"""
    entry = entry if isinstance(entry, dict) else {}
    label = "自拍" if entry.get("pose_name") else "照片"
    return "[我发的照片] " + label


async def _remember_async(plugin, umo, entry):
    remember = getattr(plugin, "_remember_bot_media", None)
    if not callable(remember):
        return False
    try:
        await remember(str(umo or ""), _memory_marker(entry))
        return True
    except Exception as exc:
        logger.debug("[amo] 媒体记忆写入失败: %s", exc)
        return False


def _remember(plugin, umo, entry):
    """同步上下文里补写 memory（有 _spawn 用 _spawn，否则尝试当前 loop）。"""
    remember = getattr(plugin, "_remember_bot_media", None)
    if not callable(remember):
        return False
    try:
        coro = _remember_async(plugin, umo, entry)
    except Exception:
        return False
    spawn = getattr(plugin, "_spawn", None)
    try:
        if callable(spawn):
            spawn(coro)
            return True
    except Exception as exc:  # cleanup: 兜底/降级，保持原行为
        _note_swallowed("amo_media._remember", exc)
    try:
        import asyncio

        asyncio.get_running_loop().create_task(coro)
        return True
    except Exception:
        try:
            coro.close()
        except Exception as exc:  # cleanup: 兜底/降级，保持原行为
            _note_swallowed("amo_media._remember", exc)
        return False


# ---------------------------------------------------------------- 媒体出口

def _plain(text):
    try:
        from astrbot.api.message_components import Plain
    except Exception:
        try:
            from astrbot.api.event.components import Plain
        except Exception:
            Plain = None
    if Plain is None:
        return None
    try:
        return Plain(text=str(text))
    except Exception:
        return None


def _components(plugin, item, caption="", components=None):
    """生图结果 + caption → 消息组件（caption 里的媒体标记先剥掉）。

    调用方可以自己给 components（宿主有自己的图片组件构造器时）；否则用
    amo_image_common.image_component(item)。
    """
    try:
        try:
            from .amo_message import strip_media_markers
        except ImportError:
            from amo_message import strip_media_markers
        caption = strip_media_markers(caption)
    except Exception:
        caption = str(caption or "")
    parts = list(components or [])
    if not parts:
        try:
            try:
                from .amo_image_common import image_component
            except ImportError:
                from amo_image_common import image_component
            component = image_component(item)
        except Exception as exc:
            _note_swallowed("amo_media.components", exc)
            component = None
        if component is None:
            return []
        parts.append(component)
    has_text = any(isinstance(getattr(comp, "text", None), str) for comp in parts)
    if caption and not has_text:
        plain = _plain(caption)
        if plain is not None:
            parts.insert(0, plain)
    return parts


async def _call_media_sender(sender, umo, parts, *, event_id="", source="photo", kind="photo"):
    """调用宿主的图片出口；旧宿主没有 kind/event_id 参数时逐级回退（不吞真异常）。"""
    try:
        return await sender(umo, parts, event_id=event_id, source=source, kind=kind)
    except TypeError as exc:
        if "kind" not in str(exc):
            raise
    try:
        return await sender(umo, parts, event_id=event_id, source=source)
    except TypeError as exc:
        if "event_id" not in str(exc) and "source" not in str(exc):
            raise
    try:
        return await sender(umo, parts, source=source)
    except TypeError as exc:
        if "source" not in str(exc):
            raise
    return await sender(umo, parts)


def _result(ok, reason="", entry=None):
    entry = entry if isinstance(entry, dict) else {}
    return {"ok": bool(ok), "reason": str(reason or ""),
            "media_id": str(entry.get("media_id") or ""), "entry": entry}


async def send_photo(plugin, umo, item, *, components=None, origin="auto",
                     outbound_source=OUTBOUND_SOURCE, kind="photo", event_id="",
                     caption="", scene=None, pose=None, outfit="", test=False,
                     claim_event=True, dedupe=None, enforce_cap=None, count=True,
                     remember=True, transport=None, sender=None, bump=None, now=None):
    """三条照片链路唯一发送入口；返回 {ok, reason, media_id, entry}。

    origin：auto=自动分享 / tool=send_photo 工具 / external_selfie=外部回执 /
    test=测试图 / chat=用户明确要（含重发）/ resend / manual。
    """
    umo = str(umo or "").strip()
    if not umo:
        return _result(False, "no_umo")
    caption = str(caption or "")
    parts = _components(plugin, item, caption, components)
    if not parts:
        return _result(False, "no_component")
    origin = str(origin or "auto").strip().lower()
    if origin not in ORIGINS:
        origin = "manual"
    stamp = _now(now)
    pose = pose if isinstance(pose, dict) else {}
    scene_meta = _scene_meta(plugin, scene)
    signature = signature_for(item)
    base = {
        "kind": "photo",
        "label": str(kind or "photo"),
        "source": origin,
        "umo": umo,
        "outfit": str(outfit or ""),
        "hash": signature,
        "event_id": str(event_id or ""),
        "caption": caption,
        "test": bool(test),
        "at": stamp,
        "scene_id": scene_meta["scene_id"],
        "zone": scene_meta["zone"],
        "place": scene_meta["place"],
        "pose_id": str(pose.get("id") or ""),
        "pose_name": str(pose.get("name") or ""),
        "pose_room": str(pose.get("room") or ""),
    }
    auto = origin == "auto" and not test
    if dedupe is None:
        dedupe = auto
    if dedupe and signature:
        hit = find_recent(plugin, signature=signature, window=DEDUPE_SECONDS,
                          now=stamp, umo=umo)
        if hit:
            entry = record(plugin, dict(base, status="dup", reason="dup_signature"))
            logger.info("[MEDIA] 同一张照片 6 小时内已发过，本次不重发（media=%s）",
                        str(hit.get("media_id") or ""))
            return _result(False, "dup_signature", entry)
    if enforce_cap is None:
        enforce_cap = origin in CAP_ENFORCED_SOURCES and not test
    if enforce_cap:
        cap = _cap(plugin)
        if photo_count(plugin, day=_day_of(stamp)) >= cap:
            entry = record(plugin, dict(base, status="cap", reason="daily_cap"))
            logger.info("[MEDIA] 今日照片已达上限 %d 张，本次不发（source=%s）", cap, origin)
            return _result(False, "daily_cap", entry)
    if claim_event and not test:
        reused = find_recent(plugin, event_id=str(event_id or ""),
                             window=EVENT_WINDOW_SECONDS, now=stamp)
        if reused:
            entry = record(plugin, dict(base, status="dup", reason="dup_event"))
            logger.info("[MEDIA] 同一事件已有照片，本次不发（event=%s）",
                        str(event_id or "-")[:48])
            return _result(False, "dup_event", entry)
        if not _claim_event(plugin, event_id, stamp):
            entry = record(plugin, dict(base, status="dup", reason="dup_event"))
            return _result(False, "dup_event", entry)
    if components is not None:
        parts = list(components)
    sender_fn = sender or getattr(plugin, "_send_media_to_umo", None)
    ok = False
    try:
        if callable(sender_fn):
            ok = bool(await _call_media_sender(
                sender_fn, umo, parts, event_id=str(event_id or ""),
                source=str(outbound_source or OUTBOUND_SOURCE),
                kind=str(kind or "photo")))
        elif callable(transport):
            await transport()
            ok = True
        else:
            context = getattr(plugin, "context", None)
            if context is None:
                raise RuntimeError("没有 context，无法发送媒体")
            await context.send_message(umo, _message_chain(parts))
            ok = True
    except Exception as exc:
        logger.warning("[MEDIA] 发送失败（%s/%s）: %s", origin, kind, exc)
        entry = record(plugin, dict(base, status="failed", reason="send_error"))
        return _result(False, "send_error", entry)
    if not ok:
        entry = record(plugin, dict(base, status="failed", reason="send_blocked"))
        return _result(False, "send_blocked", entry)
    # 先占配额再记帐：photo_engine.today_count 取 kv/账本最大值，顺序反过来会多算 1。
    # 真实宿主用 plugin.photo._bump_count；单测/旧宿主没有 photo 引擎时用调用方给的 bump。
    if count and origin in COUNTED_SOURCES:
        if not _bump_count(plugin) and callable(bump):
            try:
                bump()
            except Exception as exc:
                _note_swallowed("amo_media.send_photo.bump", exc)
    entry = record(plugin, dict(base, status="sent"))
    if remember and not test:
        await _remember_async(plugin, umo, entry)
    logger.info("[MEDIA] 图片已发（source=%s kind=%s scene=%s pose=%s media=%s）",
                origin, kind, scene_meta["scene_id"] or "-",
                str(entry.get("pose_name") or "-"), entry["media_id"])
    return _result(True, "", entry)


def _message_chain(parts):
    try:
        try:
            from .amo_image_common import MessageChain
        except ImportError:
            from amo_image_common import MessageChain
    except Exception:
        MessageChain = None
    if MessageChain is None:
        return parts
    return MessageChain(parts)


# ---------------------------------------------------------------- 外部 generate_selfie 回执

def record_external(plugin, umo, *, scene=None, pose=None, outfit="", seed="",
                    count=True, remember=True, now=None):
    """外部插件 generate_selfie 已经自己发图：按它的回执补一条账本 + 占 cap + 写 memory。"""
    umo = str(umo or "").strip()
    if not umo:
        return {}
    stamp = _now(now)
    scene_meta = _scene_meta(plugin, scene)
    pose = pose if isinstance(pose, dict) else {}
    signature = "external:%s:%s" % (umo, str(seed or scene_meta["scene_id"] or int(stamp)))
    if count:
        _bump_count(plugin)
    entry = record(plugin, {
        "kind": "photo", "source": "external_selfie", "status": "sent",
        "umo": umo, "scene_id": scene_meta["scene_id"], "zone": scene_meta["zone"],
        "place": scene_meta["place"], "pose_id": str(pose.get("id") or ""),
        "pose_name": str(pose.get("name") or ""), "pose_room": str(pose.get("room") or ""),
        "outfit": str(outfit or ""), "hash": signature, "at": stamp,
        "event_id": "", "caption": "",
    })
    if remember:
        _remember(plugin, umo, entry)
    logger.info("[MEDIA] 外部 generate_selfie 回执已补记（umo=%s media=%s）",
                umo[:48], entry["media_id"])
    return entry


def reconcile_external(plugin, umo, omni=None, now=None, remember=True):
    """把外部插件 generate_selfie 的发送证据补进账本。

    外部插件（Astrbot_amoupup_photo）自己发图并写 omni._selfie_turn_store[umo]
    = monotonic 时间；这里每轮回复前调用一次，新时间戳就补一条 external_selfie
    账本（占 cap + 写 memory），保证「发过的图在聊天/记忆里查得到」。
    """
    umo = str(umo or "").strip()
    if not umo:
        return False
    if omni is None:
        photo = getattr(plugin, "photo", None)
        finder = getattr(photo, "_find_omni", None)
        try:
            omni = finder() if callable(finder) else None
        except Exception:
            omni = None
    getter = getattr(omni, "_selfie_turn_store", None)
    if not callable(getter):
        return False
    try:
        store = getter() or {}
    except Exception:
        return False
    if not isinstance(store, dict) or umo not in store:
        return False
    try:
        _raw_stamp = store.get(umo)
        if _raw_stamp is None:
            return False
        stamp_mono = float(_raw_stamp)
    except (TypeError, ValueError):
        return False
    last = _EXTERNAL_SEEN.get(umo)
    if last is not None and stamp_mono <= float(last):
        return False
    _EXTERNAL_SEEN[umo] = stamp_mono
    try:
        age = _mono() - stamp_mono
    except Exception:
        return False
    if age < 0 or age > EXTERNAL_RECENT_SECONDS:
        return False
    photo = getattr(plugin, "photo", None)
    outfit = ""
    getter_outfit = getattr(photo, "outfit_today", None)
    if callable(getter_outfit):
        try:
            outfit = str(getter_outfit() or "")
        except Exception:
            outfit = ""
    record_external(plugin, umo, outfit=outfit, seed="%x" % int(stamp_mono),
                    remember=remember, now=now)
    return True


# ---------------------------------------------------------------- 统一失败话术

def _failure_lines():
    try:
        try:
            from .photo_share import PHOTO_FAILURE_LINES
        except ImportError:
            from photo_share import PHOTO_FAILURE_LINES
        if PHOTO_FAILURE_LINES:
            return list(PHOTO_FAILURE_LINES)
    except Exception as exc:  # cleanup: 兜底/降级，保持原行为
        logger.debug("[amo_media._failure_lines] optional step failed: %s", exc)
    return list(_DEFAULT_FAILURE_LINES)


def sanitize_failure_text(text):
    """失败话术过统一闸：出现技术词就不许说出口，换成温柔兜底句。"""
    body = str(text or "").strip()
    if not body:
        return FALLBACK_FAILURE_LINE
    lowered = body.lower()
    if any(str(word).lower() in lowered for word in TECH_WORDS):
        logger.warning("[MEDIA] 失败话术含技术词，已替换: %s", body[:60])
        return FALLBACK_FAILURE_LINE
    return body


def failure_line(plugin=None, relation="", last="", mood=None, fixed=False):
    """唯一失败话术出口（photo_share / draw_engine 共用）。"""
    if fixed:
        return sanitize_failure_text(FALLBACK_FAILURE_LINE)
    if mood is None and plugin is not None:
        try:
            mood = float(plugin.state.mood_score())
        except Exception:
            mood = None
    try:
        try:
            from .domains.photo import failure_text as _ft
        except ImportError:
            from domains.photo import failure_text as _ft
        return sanitize_failure_text(
            _ft.pick_failure_line(_failure_lines(), mood, relation, last))
    except Exception:
        return sanitize_failure_text(FALLBACK_FAILURE_LINE)


__all__ = [
    "LEDGER_KEY", "LEDGER_LIMIT", "DEDUPE_SECONDS", "COUNTED_SOURCES",
    "TECH_WORDS", "FALLBACK_FAILURE_LINE", "OUTBOUND_SOURCE",
    "rows", "record", "new_media_id", "photo_count", "find_recent", "last_photo",
    "coverage", "stats", "signature_for", "send_photo", "record_external",
    "reconcile_external", "failure_line", "sanitize_failure_text",
]
