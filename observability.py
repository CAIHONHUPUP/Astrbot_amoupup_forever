# -*- coding: utf-8 -*-
"""observability.py —— 结构化事件日志 / PII 脱敏 / 健康快照（Phase 17）。

三个插件（能陪 Astrbot_amoupup_forever、能记 Astrbot_amoupup_data、
守护 Astrbot_amoupup_watch）共用同一份实现、同一组函数名：

    mask_id / mask_open_id / mask_token / mask_secret / scrub   脱敏（单向）
    log_event(level, event, **fields)                           [event] k=v 结构化行
    health_snapshot(plugin, ...)                                只读健康快照，缺失降级

三条硬规矩：

1. 日志里不出现完整 uid / open_id / QQ 号 / 手机号 / token / api_key / Authorization：
   7 位以下整段打码，长值只留前后各 2-4 位，或改用 fingerprint() 的哈希前缀。
2. 只做单向脱敏：本模块不提供 unmask / 还原函数，掩码后无法反推原文。
3. health_snapshot 只读、不抛异常：取不到的字段降级成空 / None，并带 error_code。

这一层不写库、不读配置、不引第三方库，只依赖标准库 + 宿主 logger。
"""
from __future__ import annotations
from typing import Any

import hashlib
import logging
import re
import threading
import traceback

try:  # 线上走 AstrBot 日志
    from astrbot.api import logger
except Exception:  # pragma: no cover - 离线单测 / 直接按文件加载
    import logging

    logger = logging.getLogger("observability")

#: 掩码占位（长值保留前后若干位时用）
MASK = "****"
#: 短值整段打码（连长度都不暴露）
FULL_MASK = "******"
#: 单个字段渲染后的最大长度，防止 dict / json 把日志撑爆
MAX_FIELD_CHARS = 160

#: 键名（小写）命中即按 PII 处理
SENSITIVE_KEYWORDS = (
    "uid", "open_id", "openid", "qq", "phone", "mobile", "account",
    "token", "api_key", "apikey", "authorization", "secret", "password",
    "cookie", "session",
)
#: 连续 7 位以上数字当作 QQ 号 / 手机号 / 长数字 ID
_DIGITS_RE = re.compile(r"\d{7,}")
#: 连续 28 位以上的字母数字串当作 token / api_key
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=.]{28,}")
#: 合法日志级别
LOG_LEVELS = ("debug", "info", "warning", "error", "critical", "exception")


def _text(value):
    """任意值 -> 单行 str；None 给空串，坏对象也不抛。"""
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:  # 坏 __str__（历史上 note() 踩过）
        return "<unprintable>"
    return " ".join(text.split())


def _mask_middle(text, head=2, tail=2):
    """只留前后各 head/tail 位；太短就整段打码，保证掩码 != 原文。"""
    if not text:
        return ""
    if len(text) < head + tail + 3:
        return FULL_MASK
    return text[:head] + MASK + text[-tail:]


def mask_id(value):
    """uid / QQ 号 / 手机号：长值留前后各 2 位，短值整段打码。"""
    return _mask_middle(_text(value), 2, 2)


def mask_open_id(value):
    """平台 open_id（通常 28-32 位）：留前后各 4 位。"""
    return _mask_middle(_text(value), 4, 4)


def mask_token(value):
    """token / Authorization：只留末 4 位，其余全部打码。"""
    text = _text(value)
    if not text:
        return ""
    if len(text) <= 10:
        return FULL_MASK
    return MASK + text[-4:]


def mask_secret(value):
    """api_key / secret / password：整段打码，一位都不留。"""
    return FULL_MASK if _text(value) else ""


def fingerprint(value, size=8):
    """稳定的哈希前缀（同值同前缀，可用于对账，但不可反推）。"""
    text = _text(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:max(1, int(size))]


def scrub(value):
    """自由文本兜底：把 7 位以上数字、28 位以上长串就地打码。"""
    text = _text(value)
    if not text:
        return ""
    text = _DIGITS_RE.sub(lambda match: mask_id(match.group(0)), text)
    text = _LONG_TOKEN_RE.sub(lambda match: mask_token(match.group(0)), text)
    return text


def is_sensitive_key(key):
    """键名是否属于 PII / 凭据（大小写不敏感，包含即算）。"""
    name = _text(key).lower()
    return any(word in name for word in SENSITIVE_KEYWORDS)


def mask_value(key, value):
    """按键名的语义选脱敏方式；拿不准的一律走 scrub。"""
    text = _text(value)
    if not text:
        return ""
    name = _text(key).lower()
    if "open_id" in name or "openid" in name:
        return mask_open_id(text)
    if any(word in name for word in ("token", "authorization", "cookie", "session")):
        return mask_token(text)
    if any(word in name for word in ("secret", "password", "api_key", "apikey")):
        return mask_secret(text)
    if any(word in name for word in ("uid", "qq", "phone", "mobile", "account")):
        return mask_id(text)
    return scrub(text)


def _render_value(key, value):
    """一个字段的最终文本：先脱敏，再限长；空值给 ""。"""
    text = mask_value(key, value) if is_sensitive_key(key) else scrub(value)
    if not text:
        return '""'
    if len(text) > MAX_FIELD_CHARS:
        text = text[:MAX_FIELD_CHARS] + "..."
    if " " in text or '"' in text or "=" in text:
        return '"' + text.replace('"', "'") + '"'
    return text


def format_event(event, fields):
    """把 event + fields 拼成一行：[event] key=value key2=value2。"""
    head = "[" + (_text(event) or "event") + "]"
    parts = ["%s=%s" % (_text(key), _render_value(key, value)) for key, value in (fields or {}).items()]
    return head + ((" " + " ".join(parts)) if parts else "")


def log_event(level, event, **fields):
    """结构化事件日志：自动脱敏，返回写出去的那一行（方便测试断言）。"""
    line = format_event(event, fields)
    name = _text(level).lower() or "info"
    if name == "warn":
        name = "warning"
    if name not in LOG_LEVELS:
        name = "info"
    try:
        emit = getattr(logger, name, None)
        if callable(emit):
            emit(line)
        else:
            logger.info(line)
    except Exception as exc:  # pragma: no cover - 日志后端异常不能影响业务
        logger.debug("[obs] event emit failed: %r", exc)
    return line


def _as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def uptime_seconds(started_at=None, now=None):
    """运行秒数；started_at / now 缺一个就给 None（不瞎编 0）。

    这里刻意不读系统时钟：能陪的源码守护禁止生产模块裸调 time.time()，
    由调用方把「插件启动时刻 + 当前时刻」传进来（能陪用 clock.now_ts）。
    """
    start = _as_float(started_at)
    if start is None or start <= 0:
        return None
    current = _as_float(now)
    if current is None:
        return None
    return max(0, int(current - start))


def _plugin_name(plugin):
    """插件名：字符串直接用，对象找 PLUGIN_NAME / plugin_name / name。"""
    if isinstance(plugin, str):
        return plugin
    for attr in ("PLUGIN_NAME", "plugin_name", "name"):
        value = getattr(plugin, attr, "")
        if value:
            return _text(value)
    return ""


def _plugin_attr(plugin, attr):
    if isinstance(plugin, str):
        return ""
    return _text(getattr(plugin, attr, ""))


def _db_part(db):
    """{schema_version, ok}：db 可以是 dict、带 schema_status()/stats() 的对象或 None。"""
    version = None
    ok = False
    if isinstance(db, dict):
        version = _as_int(db.get("schema_version", db.get("user_version", db.get("schema"))))
        if db.get("ok") is not None:
            ok = bool(db.get("ok"))
        else:
            ok = version is not None and not db.get("last_error")
        return {"schema_version": version, "ok": bool(ok)}
    if db is None:
        return {"schema_version": None, "ok": False}
    for attr in ("schema_status", "schema_report", "stats"):
        getter = getattr(db, attr, None)
        if not callable(getter):
            continue
        try:
            snap = getter()
        except Exception as exc:
            logger.debug("[obs] db probe %s failed: %r", attr, exc)
            continue
        if isinstance(snap, dict):
            version = _as_int(snap.get("schema_version", snap.get("user_version", snap.get("schema"))))
            ok = version is not None and not snap.get("last_error")
            break
    if version is None:
        version = _as_int(getattr(db, "schema_version", None))
        ok = version is not None
    return {"schema_version": version, "ok": bool(ok)}


def _jobs_part(jobs):
    """job 快照行列表：report()/summary() 都给容错，拿不到就是 []。"""
    rows: Any = []
    if isinstance(jobs, dict):
        rows = jobs.get("jobs") if isinstance(jobs.get("jobs"), list) else []
    elif isinstance(jobs, (list, tuple)):
        rows = list(jobs)
    elif jobs is not None:
        for attr in ("report", "summary"):
            getter = getattr(jobs, attr, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception as exc:
                logger.debug("[obs] jobs probe %s failed: %r", attr, exc)
                continue
            if isinstance(value, dict):
                rows = value.get("jobs") or []
            elif isinstance(value, (list, tuple)):
                rows = list(value)
            break
    out = []
    for row in list(rows)[:50]:
        out.append(dict(row) if isinstance(row, dict) else {"value": _text(row)})
    return out


def _counts_part(counts):
    """counts 只收 dict；数字保留，其它值转字符串，绝不抛。"""
    if not isinstance(counts, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(counts.items())[:40]:
        name = _text(key)
        if isinstance(value, bool):
            out[name] = value
        elif isinstance(value, int):
            out[name] = value
        elif isinstance(value, float):
            out[name] = round(value, 3)
        else:
            out[name] = _text(value)[:60]
    return out


def health_snapshot(plugin="", version="", db=None, jobs=None, counts=None,
                    started_at=None, uptime_s=None, now=None, error_code=""):
    """只读健康快照：{plugin, version, uptime_s, db, jobs, counts, error_code}。

    db 取不到时是 {"schema_version": None, "ok": False}，并自动带上
    error_code=health.degraded —— 调用方不需要 try/except，也不会被这里炸掉。

    uptime_s 需要调用方给 started_at 与 now；给不出就是 None（本模块不读系统时钟）。
    """
    name = _plugin_name(plugin)
    ver = _text(version) or _plugin_attr(plugin, "PLUGIN_VERSION")
    db_part = _db_part(db)
    if uptime_s is None:
        uptime_s = uptime_seconds(started_at, now)
    snap = {
        "plugin": name,
        "version": ver,
        "uptime_s": _as_int(uptime_s),
        "db": db_part,
        "jobs": _jobs_part(jobs),
        "counts": _counts_part(counts),
    }
    code = _text(error_code)
    if not code and not db_part["ok"]:
        code = "health.degraded"
    snap["error_code"] = code
    return snap


def plugin_health(plugin, name="Astrbot_amoupup_forever", version="",
                  started_at=None, now=None):
    """按插件对象的 db/jobs 读一份健康快照（/status 的 obs_health 字段）。

    只读、字段缺失降级、绝不抛：面板每 60 秒轮询一次，不能因为它挂掉。
    这里不读系统时钟：started_at / now 由调用方传进来（能陪用 clock.now_ts）。
    """
    db = getattr(plugin, "db", None)
    schema = {}
    getter = getattr(db, "schema_status", None)
    if callable(getter):
        try:
            schema = getter()
        except Exception as exc:
            logger.debug("[amo] 读 schema 快照失败: %r", exc)
            schema = {}
    counts = {}
    counter = getattr(db, "counts", None)
    if callable(counter):
        try:
            counts = counter()
        except Exception as exc:
            logger.debug("[amo] 读 counts 失败: %r", exc)
            counts = {}
    return health_snapshot(name, version=version, db=schema,
                           jobs=getattr(plugin, "jobs", None), counts=counts,
                           started_at=started_at, now=now)

# ---------------------------------------------------------------------------
# 日志脱敏（2026-11-22 task-26）
#
# 现场（work/audit/cross_plugin.md §5.3）：生产 astrbot.log 有 23 行明文
# Bearer sk-…，1,061 行 open_id，117 行完整生图 prompt。来源是 provider 异常
# traceback 把 headers（Authorization）当局部变量打了出来。这里补一层**进程级**
# logging.Filter：
# - 覆盖 forever / watch / data / photo 的 logger：不只装在某一个 logger 上，
#   而是装到「当前所有 logger 的 handler + 之后新加的 handler」，同一个 AstrBot
#   进程里哪个插件打日志都会被脱敏；
# - 覆盖 record.msg / record.args / 已格式化的 exc_text；异常 traceback 里带密钥
#   时折成脱敏后的文本（不丢栈，只丢明文）；
# - install_log_redaction() 幂等，可重复调用；RedactionFilter 可单独用于测试。
# ---------------------------------------------------------------------------

#: 脱敏占位符（单向：不含原文，也无法还原）
REDACTED = "<redacted>"

#: 凭据类规则：(名字, 正则, 替换)。顺序有意义：先按 header 整段吃掉，
#: 再兜底 Bearer / sk- 裸串，最后按 key=value 形式吃。
_SECRET_PATTERNS = (
    ("authorization", re.compile(
        r"(?i)\b((?:authorization|x-api-key)\b[\"']?\s*[:=]\s*[\"']?)"
        r"(?:(?:Bearer|Basic|Token)\s+)?[A-Za-z0-9._\-+/=]{4,}"),
     r"\1" + REDACTED),
    ("bearer", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+/=]{6,}"), r"\1" + REDACTED),
    ("sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"), "sk-" + REDACTED),
    ("api_key", re.compile(
        r"(?i)\b(api[_-]?key\b[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;}\]]+"),
     r"\1" + REDACTED),
    ("token", re.compile(
        r"(?i)\b((?:access_|refresh_)?token\b[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;}\]]+"),
     r"\1" + REDACTED),
    ("secret", re.compile(
        r"(?i)\b((?:client_)?secret\b[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;}\]]+"),
     r"\1" + REDACTED),
    ("password", re.compile(
        r"(?i)\b(?:password|passwd)\b[\"']?\s*[:=]\s*[\"']?[^\"'\s,;}\]]+"),
     "password=" + REDACTED),
)

#: 日志字段名（dict key）命中即整值脱敏：headers 以 dict 形式进日志时没有 "=" 可匹配。
_SENSITIVE_LOG_KEYS = (
    "authorization", "api_key", "apikey", "api-key", "token", "secret",
    "password", "passwd", "open_id", "openid", "cookie", "session",
)

#: open_id 规则：默认脱敏，可用 open_id=False 关闭（任务里写作“可选脱敏”）。
_OPEN_ID_PATTERNS = (
    ("open_id", re.compile(r"\bou_[A-Za-z0-9]{6,}"), "ou_" + REDACTED),
    ("open_id", re.compile(
        r"(?i)\b(open_?id\b[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9_\-]{6,}"),
     r"\1" + REDACTED),
)


def redact_text(value, open_id=True, stats=None):
    """把一段文本里的凭据 / open_id 就地脱敏；幂等，绝不抛。

    stats 可选：dict，命中时按规则名累加次数（脚本统计用，只记次数不记值）。
    """
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if not text:
        return text
    for name, pattern, repl in _SECRET_PATTERNS:
        try:
            text, count = pattern.subn(repl, text)
        except Exception:  # pragma: no cover - 正则本身不会失败
            count = 0
        if count and stats is not None:
            stats[name] = int(stats.get(name, 0)) + count
    if open_id:
        for name, pattern, repl in _OPEN_ID_PATTERNS:
            try:
                text, count = pattern.subn(repl, text)
            except Exception:  # pragma: no cover
                count = 0
            if count and stats is not None:
                stats[name] = int(stats.get(name, 0)) + count
    return text


def _redact_value(value, open_id=True):
    """递归脱敏 logging record 的 msg / args（保留容器类型，%d 等格式不受影响）。"""
    if isinstance(value, str):
        return redact_text(value, open_id=open_id)
    if isinstance(value, tuple):
        return tuple(_redact_value(item, open_id) for item in value)
    if isinstance(value, list):
        return [_redact_value(item, open_id) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            safe_key = redact_text(key, open_id=open_id) if isinstance(key, str) else key
            key_text = str(key or "").lower()
            if any(word in key_text for word in _SENSITIVE_LOG_KEYS):
                out[safe_key] = REDACTED
            else:
                out[safe_key] = _redact_value(item, open_id)
        return out
    return value


class RedactionFilter(logging.Filter):
    """进程级日志脱敏过滤器：凭据 / Authorization / token / open_id 不出日志。"""

    def __init__(self, redact_open_id=True):
        super().__init__("amo-log-redaction")
        self.redact_open_id = bool(redact_open_id)

    def filter(self, record):
        try:
            record.msg = _redact_value(record.msg, self.redact_open_id)
            if record.args:
                record.args = _redact_value(record.args, self.redact_open_id)
            if getattr(record, "exc_text", None):
                record.exc_text = redact_text(
                    record.exc_text, open_id=self.redact_open_id)
            self._fold_redacted_exception(record)
        except Exception:  # pragma: no cover - 日志脱敏不能反过来弄挂业务  # cleanup: 兜底/降级，保持原行为
            pass
        return True

    def _fold_redacted_exception(self, record):
        """异常栈里带密钥时，折成一条脱敏后的文本；没命中就保持原样。"""
        exc_info = getattr(record, "exc_info", None)
        if not exc_info:
            return
        try:
            raw = "".join(traceback.format_exception(*exc_info))
        except Exception:  # pragma: no cover
            return
        fixed = redact_text(raw, open_id=self.redact_open_id)
        if fixed == raw:
            return
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover
            message = str(record.msg)
        # AstrBot 的 logger 把 record.exc_info 直接交给 loguru（不会读 exc_text），
        # 所以把脱敏后的栈并进 msg，再清掉 exc_info：信息不丢，明文不落盘。
        record.msg = (message + "\n" + fixed).rstrip()
        record.args = ()
        record.exc_info = None
        record.exc_text = None


_REDACTION_LOCK = threading.Lock()
_REDACTION_FILTER = None
_REDACTION_INSTALLED = False
_REDACTION_PATCHED = False
_ORIGINAL_ADD_HANDLER = None


def _ensure_redaction_filter(redact_open_id=True):
    global _REDACTION_FILTER
    if _REDACTION_FILTER is None:
        _REDACTION_FILTER = RedactionFilter(redact_open_id=redact_open_id)
    else:
        _REDACTION_FILTER.redact_open_id = bool(redact_open_id)
    return _REDACTION_FILTER


def _attach_redaction(target, filter_obj):
    """把一个 logger 或 handler 接上过滤器；已接过的跳过（幂等）。"""
    try:
        if any(isinstance(item, RedactionFilter) for item in target.filters):
            return
        target.addFilter(filter_obj)
    except Exception:  # pragma: no cover - logging 对象异常不影响启动  # cleanup: 兜底/降级，保持原行为
        pass


def _patch_logger_add_handler(filter_obj):
    """之后新加的 handler（别的插件/核心建的 logger）也自动接上过滤器。"""
    global _REDACTION_PATCHED, _ORIGINAL_ADD_HANDLER
    if _REDACTION_PATCHED:
        return
    _ORIGINAL_ADD_HANDLER = logging.Logger.addHandler

    def _add_handler(self, hdlr):
        try:
            _attach_redaction(hdlr, filter_obj)
        except Exception:  # pragma: no cover  # cleanup: 兜底/降级，保持原行为
            pass
        return _ORIGINAL_ADD_HANDLER(self, hdlr)

    setattr(logging.Logger, "addHandler", _add_handler)
    _REDACTION_PATCHED = True


def install_log_redaction(redact_open_id=True, force=False):
    """把脱敏过滤器装到当前进程的**所有** logger / handler 上（幂等）。

    覆盖范围是进程级的：forever / watch / data / photo 四个插件共用同一个
    AstrBot 进程，谁之后新建 logger，都会经 addHandler 钩子接上过滤器。
    返回 True 表示本次真的装了一遍，False 表示之前已经装过。
    """
    global _REDACTION_INSTALLED
    with _REDACTION_LOCK:
        if _REDACTION_INSTALLED and not force:
            _ensure_redaction_filter(redact_open_id)
            return False
        filter_obj = _ensure_redaction_filter(redact_open_id)
        _patch_logger_add_handler(filter_obj)
        targets = [logging.getLogger()]
        try:
            for _name, obj in list(logging.Logger.manager.loggerDict.items()):
                if isinstance(obj, logging.Logger):
                    targets.append(obj)
        except Exception:  # pragma: no cover  # cleanup: 兜底/降级，保持原行为
            pass
        # 显式点名四个插件 + AstrBot 核心 logger：即使还没建也会建出来接上，
        # 之后 LogManager.GetLogger 复用的就是同一个对象。
        for name in ("astrbot", "astrbot.api", "astrbot.plugin",
                     "Astrbot_amoupup_forever", "Astrbot_amoupup_watch",
                     "Astrbot_amoupup_data", "Astrbot_amoupup_photo"):
            try:
                targets.append(logging.getLogger(name))
            except Exception:  # pragma: no cover  # cleanup: 兜底/降级，保持原行为
                pass
        for logger_obj in targets:
            _attach_redaction(logger_obj, filter_obj)
            for hdlr in list(getattr(logger_obj, "handlers", ()) or ()):
                _attach_redaction(hdlr, filter_obj)
        _REDACTION_INSTALLED = True
        return True


def redaction_installed():
    """当前进程是否已装脱敏过滤器（/status 与测试用）。"""
    return bool(_REDACTION_INSTALLED)
