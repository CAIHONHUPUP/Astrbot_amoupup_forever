# -*- coding: utf-8 -*-
"""DB schema 版本（Phase 7）：PRAGMA user_version + 迁移注册表。

为什么要有这一层
- 以前只有 kv 里的 block_diary_migrate_version 一个版本号，其它"补列/建表"动作
  散在 AmoDB.init 的 try/except 里：谁跑过、跑到第几步，启动日志看不出来；
- 迁移失败也没记录，只能靠猜。

本模块做四件事
1. 用 SQLite 自带的 PRAGMA user_version 当 schema 版本号；
2. 迁移项登记成 (version, id, fn)：按序执行、幂等、只前进不回退；
3. 库版本比代码高（回滚过）只警告不降级；单项失败记 last_error 并停在原地，
   由调用方兜住异常，不阻断启动；
4. v3 起每次动手前先用 sqlite backup API 热备到外部目录（manifest + kv 记录），
   迁移本身在单个 BEGIN IMMEDIATE 事务里完成。

约定
- fn(db) 收 AmoDB 实例；建表仍由 amo_db.SCHEMA_SQL 负责，这里不写 CREATE TABLE；
- 旧库 kv 键 block_diary_migrate_version 照旧读（在 AmoDB.migrate_block_diary 里），
  这里只是把它登记成一项迁移，不删这个键。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

import hashlib
import json
import os
import re
import sqlite3
import threading
import time

try:  # AstrBot 运行环境和单测桩都提供 astrbot.api.logger
    from astrbot.api import logger
except ImportError:  # 直接按文件加载时的兜底
    import logging

    logger = logging.getLogger("amo_db_schema")

#: 代码期望的最新 schema 版本（只在加迁移项时 +1）
#: v4=block_diary.event_id（双列）；v5=旧账 ref 回填。
LATEST_VERSION = 5

#: 从哪个版本起迁移前必须先热备（v3 起会改写表/块日记；v1/v2 只建表/插行）。
BACKUP_MIN_VERSION = 3
#: 备份目录：kv 配置键 → 环境变量 → 按库路径推导。
BACKUP_DIR_KV_KEY = "db_backup_dir"
BACKUP_DIR_ENV = "AMO_DB_BACKUP_DIR"
LAST_BACKUP_KV_KEY = "db_backup_last"
BACKUP_SCHEMA = 1
_BACKUP_SUBDIR = ("backup", "migrations")

_LOCK = threading.RLock()
_LAST_ERROR = None


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def derive_backup_root(db_path):
    """按库路径推导备份根目录（可推导，不写死盘符）。

    真实环境：<astrbot>/data/plugin_data/<插件>/amo_forever.db
              → <astrbot 的父目录>/backup/migrations（本机实测落在备份盘同名目录）
    临时目录：.../plugin_data/<插件>/amo_forever.db
              → .../plugin_data/backup/migrations（跟临时目录一起清掉，不污染真实目录）
    """
    data_dir = os.path.dirname(os.path.abspath(db_path))
    current = data_dir
    while True:
        if os.path.basename(current).lower() == "astrbot":
            return os.path.join(os.path.dirname(current), *_BACKUP_SUBDIR)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.join(os.path.dirname(data_dir), *_BACKUP_SUBDIR)


def backup_root(db=None, db_path=None):
    """备份根：kv['db_backup_dir'] > 环境变量 AMO_DB_BACKUP_DIR > 按库路径推导。"""
    configured = ""
    if db is not None:
        try:
            configured = str(db.kv_get(BACKUP_DIR_KV_KEY, "") or "").strip()
        except Exception:
            configured = ""
    configured = configured or os.environ.get(BACKUP_DIR_ENV, "").strip()
    path = db_path or (getattr(db, "path", "") if db is not None else "")
    if not path:
        raise ValueError("无法确定备份目录：缺少 db.path")
    return os.path.abspath(configured or derive_backup_root(path))


def backup_database(db, from_version=0, to_version=LATEST_VERSION,
                    reason="schema_migration", target_dir=None):
    """sqlite backup API 热备当前库到外部目录，写 manifest + kv 记录，返回 manifest。

    热备走 SQLite backup API，和线上读写共用 WAL，不锁库、不手工复制 -wal/-shm；
    manifest 带 sha256，恢复工具（tools/restore_db_backup.py）据此校验后再恢复。
    备份失败直接抛出：宁可不迁移，也不在没有退路的情况下改数据。
    """
    conn = _conn(db)
    source = os.path.abspath(str(getattr(db, "path", "") or ""))
    if not source:
        raise ValueError("备份需要 db.path")
    root = os.path.abspath(target_dir or backup_root(db, source))
    os.makedirs(root, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = "amo_forever-v%d-to-v%d-%s" % (int(from_version), int(to_version), stamp)
    dest = os.path.join(root, stem + ".db")
    suffix = 1
    while os.path.exists(dest):
        suffix += 1
        dest = os.path.join(root, "%s-%d.db" % (stem, suffix))
    dest_conn = sqlite3.connect(dest)
    try:
        conn.backup(dest_conn)
        check = dest_conn.execute("PRAGMA quick_check").fetchone()
    finally:
        dest_conn.close()
    integrity = str((check[0] if check else "") or "")
    if integrity.lower() != "ok":
        try:
            os.remove(dest)
        except OSError:  # cleanup 半成品备份删不掉也必须继续抛错
            pass
        raise RuntimeError("热备文件完整性校验失败：%s" % (integrity or "unknown"))
    manifest = {
        "schema": BACKUP_SCHEMA,
        "tool": "amo_db_schema.backup_database",
        "source": source,
        "backup": dest,
        "name": os.path.basename(dest),
        "sha256": _sha256_file(dest),
        "bytes": os.path.getsize(dest),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "from_version": int(from_version),
        "to_version": int(to_version),
        "user_version": user_version(db),
        "reason": str(reason or ""),
        "integrity": integrity,
    }
    manifest_path = dest + ".manifest.json"
    with open(manifest_path, "w", encoding="utf-8", newline="") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    try:
        db.kv_set(LAST_BACKUP_KV_KEY, {
            "path": dest,
            "name": manifest["name"],
            "manifest": manifest_path,
            "sha256": manifest["sha256"],
            "bytes": manifest["bytes"],
            "created_at": manifest["created_at"],
            "from_version": manifest["from_version"],
            "to_version": manifest["to_version"],
            "reason": manifest["reason"],
        })
    except Exception as exc:  # 记录失败不影响备份本身（文件已落盘）
        logger.warning("[amo] 迁移前热备已生成，写 kv 记录失败：%s", exc)
    logger.info("[amo] 迁移前热备完成：%s（%d 字节，sha256=%s…）",
                dest, manifest["bytes"], manifest["sha256"][:12])
    return manifest


def has_data_to_protect(db):
    """库里有没有值得热备的数据：全新空库不制造空备份，也不写 kv 记录。

    只查 v3 会动的表；表名是常量，不存在注入面。
    """
    conn = _conn(db)
    for table in ("schedule", "schedule_diary", "block_diary",
                  "wallet_ledger", "kv"):
        try:
            if conn.execute("SELECT 1 FROM %s LIMIT 1" % table).fetchone():
                return True
        except sqlite3.Error:
            continue
    return False


def _now_ts():
    """统一时钟（源码护栏禁止裸 time.time()）。"""
    try:
        from .amo_shared import now_ts
    except ImportError:
        from amo_shared import now_ts
    return now_ts()


def _migration_schema_baseline(db):
    """v1 基线：建表 + 老表补列已在 AmoDB.init 里做完，这里只登记版本。"""
    return 0


def _migration_block_diary(db):
    """v2：旧 schedule_diary 整点行 → block_diary 块行。

    复用 AmoDB.migrate_block_diary（内部 INSERT OR IGNORE + kv 版本号判重），
    所以重复执行安全；kv 里的 block_diary_migrate_version 仍按原样读。

    旧表为空时直接跳过：没有可迁的行，也就不该为了“跑一遍”给库里白写 kv 版本号
    （空库/新装用户看到的 kv 键数不该因为这次重构多一个）。
    """
    fn = getattr(db, "migrate_block_diary", None)
    if not callable(fn):
        return 0
    try:
        row = db.conn.execute("SELECT COUNT(*) FROM schedule_diary").fetchone()
    except Exception as exc:
        logger.debug("[amo] 检查 schedule_diary 行数失败，跳过块日记迁移: %r", exc)
        return 0
    if not row or not int(row[0] or 0):
        return 0
    return int(fn() or 0)


def _timeline_funcs():
    """延迟导入时间线身份函数，避免模块加载期的循环依赖。"""
    try:
        from .amo_timeline import legacy_block_id, mint_event_id
    except ImportError:  # 直接按文件加载时
        from amo_timeline import legacy_block_id, mint_event_id
    return mint_event_id, legacy_block_id


def _load_schedule_days(conn):
    """读 schedule 表 → {day: [item...]}；坏 JSON 直接抛出，迁移中止（有备份兜底）。"""
    days: dict[str, Any] = {}
    try:
        rows = conn.execute("SELECT day, items FROM schedule").fetchall()
    except sqlite3.Error:
        return days
    for row in rows:
        day = str(row[0] or "")
        try:
            items = json.loads(row[1] or "[]")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("schedule 表 day=%s 的 items 不是合法 JSON：%s" % (day, exc))
        if not isinstance(items, list):
            raise RuntimeError("schedule 表 day=%s 的 items 不是列表" % day)
        days[day] = [item for item in items if isinstance(item, dict)]
    return days


def _mint_missing_event_ids(conn, day, items):
    """给一天里缺 event_id 的块补确定性 id；只有真补过才写回表。"""
    mint_event_id, _legacy = _timeline_funcs()
    minted = 0
    for item in items:
        if str(item.get("event_id") or "").strip():
            continue
        item["event_id"] = mint_event_id(day, item)
        minted += 1
    if minted:
        conn.execute("UPDATE schedule SET items=? WHERE day=?",
                     (json.dumps(items, ensure_ascii=False), day))
    return minted


def _rebuild_schedule_kv(conn, days):
    """kv['schedule'] 整体从 schedule 表重建（表是权威，不再单独补 id）。

    直接在本事务里 upsert，不调 AmoDB.kv_set：那个方法自己 commit，会把
    「表已迁移 / kv 还是旧的」暴露在两次提交之间。
    """
    snapshot = {day: items for day, items in days.items()}
    conn.execute(
        "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        ("schedule", json.dumps(snapshot, ensure_ascii=False), _now_ts()))
    return len(snapshot)


def _schedule_event_id_sets(conn, db):
    """返回 (schedule 表里的 (day, event_id) 集合, kv['schedule'] 里的集合)。

    kv 读走 AmoDB.kv_get（同一个连接能看到本事务未提交的重建结果），
    不在这里裸写 kv 查询 —— kv 读写入口仍归 amo_db / amo_state_store。
    """
    table = set()
    for day, items in _load_schedule_days(conn).items():
        for item in items:
            value = str(item.get("event_id") or "").strip()
            if value:
                table.add((day, value))
    kv_ids = set()
    snapshot = db.kv_get("schedule", {}) or {}
    for day, items in (snapshot if isinstance(snapshot, dict) else {}).items():
        for item in (items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            value = str(item.get("event_id") or "").strip()
            if value:
                kv_ids.add((str(day), value))
    return table, kv_ids


def _assert_schedule_kv_aligned(conn, db):
    """表与 kv 的 event_id 集合必须一致；不一致就让迁移失败、user_version 不前进。"""
    table, kv_ids = _schedule_event_id_sets(conn, db)
    if table == kv_ids:
        return
    only_table = sorted(table - kv_ids)[:5]
    only_kv = sorted(kv_ids - table)[:5]
    raise RuntimeError(
        "schedule 表与 kv['schedule'] 的 event_id 不一致："
        "表独有 %d 条 %s，kv 独有 %d 条 %s"
        % (len(table - kv_ids), only_table, len(kv_ids - table), only_kv))


def _ensure_ledger_event_id(conn):
    """wallet_ledger 补 event_id 列（只加列，不改金额、不动旧 ref）。"""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(wallet_ledger)")}
    except sqlite3.Error:
        return False
    if not cols or "event_id" in cols:
        return False
    conn.execute(
        "ALTER TABLE wallet_ledger ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
    return True


def _block_diary_has_event_id(conn):
    """block_diary 是否已有 event_id 列（v4 之前的老库返回 False）。"""
    try:
        return "event_id" in {row[1] for row in conn.execute(
            "PRAGMA table_info(block_diary)")}
    except sqlite3.Error:
        return False


def _ensure_block_diary_event_id(conn):
    """block_diary 补 event_id 列（只加列；block_id 仍是主键，旧代码照旧读）。"""
    if _block_diary_has_event_id(conn):
        return False
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(block_diary)")}
    except sqlite3.Error:
        return False
    if not cols:
        return False
    conn.execute(
        "ALTER TABLE block_diary ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
    return True


def _backfill_block_diary_event_ids(conn):
    """event_id 从 block_id 回填；两列不一致的旧行也按 block_id 对齐（幂等）。"""
    try:
        rows = conn.execute(
            "SELECT day, block_id FROM block_diary "
            "WHERE event_id IS NULL OR event_id='' OR event_id<>block_id").fetchall()
    except sqlite3.Error:
        return 0
    filled = 0
    for row in rows:
        key = str(row[1] or "")
        if not key:
            continue
        cur = conn.execute(
            "UPDATE block_diary SET event_id=? WHERE day=? AND block_id=? "
            "AND (event_id IS NULL OR event_id='' OR event_id<>?)",
            (key, str(row[0] or ""), key, key))
        filled += int(cur.rowcount or 0)
    return filled


#: 只认「block:ev_<16 位十六进制>」；食品 ref 允许带人类可读的时段后缀。
#: legacy 内容哈希 / sch_ / 自由文本 / 空 id 一律不猜（它们不是 event_id）。
_LEDGER_BLOCK_REF_RE = re.compile(
    r"^block:(ev_[0-9a-fA-F]{16})(?:\s+.*)?$")


def _parse_ledger_block_ref(ref):
    """从旧账 ref 解析事件 id：能唯一映射才返回，否则空串（保持空、不猜）。

    食品账的 ref 形如 block:ev_xxx 22:10-22:30（id + 时段后缀），id 仍明确；
    sch_...、旧内容哈希、自由文本都不唯一映射到「日程 event_id」，保持空。
    """
    match = _LEDGER_BLOCK_REF_RE.match(str(ref or "").strip())
    return match.group(1) if match else ""


def _backfill_ledger_event_ids(conn):
    """按可解析的 ref 回填 wallet_ledger.event_id；不改金额、不改旧 ref、幂等。"""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(wallet_ledger)")}
    except sqlite3.Error:
        return 0
    if "event_id" not in cols:
        return 0
    try:
        rows = conn.execute(
            "SELECT idem, ref FROM wallet_ledger "
            "WHERE (event_id IS NULL OR event_id='') AND ref LIKE 'block:%'").fetchall()
    except sqlite3.Error:
        return 0
    filled = 0
    for row in rows:
        ident = _parse_ledger_block_ref(row[1])
        if not ident:
            continue
        cur = conn.execute(
            "UPDATE wallet_ledger SET event_id=? "
            "WHERE idem=? AND (event_id IS NULL OR event_id='')",
            (ident, str(row[0])))
        filled += int(cur.rowcount or 0)
    return filled


def _rewrite_block_diary_keys(conn, days):
    """旧 block_diary 的内容哈希键 → 对应块的 event_id；目标键已存在时保留旧行。"""
    _mint, legacy_block_id = _timeline_funcs()
    try:
        diary_rows = conn.execute(
            "SELECT day, block_id, start FROM block_diary").fetchall()
    except sqlite3.Error:
        return 0
    rewritten = 0
    for row in diary_rows:
        day = str(row[0] or "")
        old_key = str(row[1] or "")
        if not old_key or old_key.startswith("legacy:"):
            continue
        start = str(row[2] or "")[:5]
        blocks = days.get(day) or []
        target = ""
        for item in blocks:
            if legacy_block_id(day, item) == old_key:
                target = str(item.get("event_id") or "")
                break
        if not target and start:
            for item in blocks:
                if str(item.get("start") or "")[:5] == start:
                    target = str(item.get("event_id") or "")
                    break
        if not target or target == old_key:
            continue
        exists = conn.execute(
            "SELECT 1 FROM block_diary WHERE day=? AND block_id=? LIMIT 1",
            (day, target)).fetchone()
        if exists:
            logger.debug("[amo] event_id 迁移：%s %s 目标键已存在，保留旧行不合并",
                         day, old_key)
            continue
        conn.execute("UPDATE block_diary SET block_id=? WHERE day=? AND block_id=?",
                     (target, day, old_key))
        rewritten += 1
    return rewritten


def _begin_immediate(conn):
    """开独占写事务；调用前先收掉隐式事务，避免 BEGIN 嵌套报错。"""
    if getattr(conn, "in_transaction", False):
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _migration_event_id(db):
    """v3：一个事件 id 贯穿日程 / 账本 / 块日记 / 记忆（单事务）。

    顺序固定：先补 schedule 表 → 同一事务里从表整体重建 kv['schedule'] →
    补 wallet_ledger.event_id 列 → 块日记改键 → 校验表与 kv 的 event_id 集合一致。
    任何一步失败都 rollback（表、kv、块日记一起回到迁移前），user_version 不前进；
    下次启动用同一个确定性 mint_event_id 重跑，不会写出第二套身份。
    """
    conn = _conn(db)
    _begin_immediate(conn)
    try:
        days = _load_schedule_days(conn)
        backfilled = 0
        for day, items in days.items():
            backfilled += _mint_missing_event_ids(conn, day, items)
        # 空表且本来没有 kv 缓存时不要凭空造一个 'schedule' 键（新装用户键数不变）。
        if days or db.kv_get("schedule", None) is not None:
            kv_days = _rebuild_schedule_kv(conn, days)
        else:
            kv_days = 0
        ledger_column = _ensure_ledger_event_id(conn)
        rewritten = _rewrite_block_diary_keys(conn, days)
        _assert_schedule_kv_aligned(conn, db)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # cleanup 回滚失败也不能盖住原始异常
            pass
        raise
    if backfilled or rewritten or ledger_column or kv_days:
        logger.info("[amo] event_id 迁移：日程补 id %d 条、kv 快照重建 %d 天，"
                    "块日记改键 %d 条，账本补列=%s",
                    backfilled, kv_days, rewritten, ledger_column)
    return rewritten


def _migration_block_diary_event_id(db):
    """v4：block_diary 显式 event_id 列（加列 + 从 block_id 回填，单事务）。

    旧代码仍按 block_id 读；两列写同一个身份。失败整体回滚，user_version 不前进；
    下次启动重跑，回填只碰空/不一致的行，幂等。
    """
    conn = _conn(db)
    _begin_immediate(conn)
    try:
        added = _ensure_block_diary_event_id(conn)
        filled = _backfill_block_diary_event_ids(conn)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # cleanup 回滚失败也不能盖住原始异常
            pass
        raise
    if added or filled:
        logger.info("[amo] block_diary event_id：补列=%s，回填 %d 行", added, filled)
    return filled


def _migration_ledger_event_ids(db):
    """v5：旧账 wallet_ledger.event_id 只从可解析 ref 回填（单事务、幂等）。

    只认 block:<id>；sch_... / 自由文本 / 空 id 不猜，保持空。不改金额、不改旧 ref。
    """
    conn = _conn(db)
    _begin_immediate(conn)
    try:
        filled = _backfill_ledger_event_ids(conn)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # cleanup 回滚失败也不能盖住原始异常
            pass
        raise
    if filled:
        logger.info("[amo] 旧账 event_id 回填：%d 条（只认 block:<id>）", filled)
    return filled


#: 迁移注册表：version 递增，(version, id, fn) 三元组
MIGRATIONS: tuple[dict[str, Any], ...] = (
    {
        "version": 1,
        "id": "schema_baseline",
        "fn": _migration_schema_baseline,
        "note": "建表与老表补列（SCHEMA_SQL + AmoDB._ensure_columns）已在 init 里完成",
    },
    {
        "version": 2,
        "id": "block_diary_hour_to_block",
        "fn": _migration_block_diary,
        "note": "旧整点日记 → 块日记；兼容旧 kv 版本号 block_diary_migrate_version",
    },
    {
        "version": 3,
        "id": "event_id_unify",
        "fn": _migration_event_id,
        "note": "日程补 event_id + 账本 event_id 列 + 块日记键统一（旧行按 day+start+end）",
    },
    {
        "version": 4,
        "id": "block_diary_event_id",
        "fn": _migration_block_diary_event_id,
        "note": "block_diary 补 event_id 列并从 block_id 回填（两列同一身份，旧代码仍读 block_id）",
    },
    {
        "version": 5,
        "id": "ledger_event_id_backfill",
        "fn": _migration_ledger_event_ids,
        "note": "旧账 wallet_ledger.event_id 只从 block:<id> ref 回填，sch_/自由文本不猜",
    },
)


def _conn(db):
    conn = getattr(db, "conn", None)
    if conn is None:
        raise TypeError("schema 迁移需要 AmoDB 连接（db.conn）")
    return conn


def user_version(db):
    """读 PRAGMA user_version；读不到按 0（等价于"从没迁移过"）。"""
    try:
        row = _conn(db).execute("PRAGMA user_version").fetchone()
    except Exception as exc:
        logger.debug("[amo] 读 user_version 失败: %r", exc)
        return 0
    if not row:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError):
        return 0


def set_user_version(db, version):
    """写 PRAGMA user_version（只前进；调用方负责别回退）。"""
    value = max(0, int(version))
    conn = _conn(db)
    # PRAGMA 不支持参数绑定；value 已经 int 化，不存在注入面。
    conn.execute("PRAGMA user_version=%d" % value)
    conn.commit()
    return value


def last_error():
    """最近一次迁移失败的原因（没有失败就是 None）。"""
    return _LAST_ERROR


def pending(db):
    """还没应用的迁移项（按 version 升序）。"""
    current = user_version(db)
    return [dict(item) for item in MIGRATIONS if int(item["version"]) > current]


def status(db):
    """schema 快照：{user_version, applied, pending, last_error}（只读，不迁移）。"""
    try:
        current = user_version(db)
    except Exception as exc:
        return {
            "user_version": 0,
            "latest_version": LATEST_VERSION,
            "applied": [],
            "pending": [],
            "last_error": "%s: %s" % (type(exc).__name__, exc),
            "ahead": False,
        }
    return {
        "user_version": current,
        "latest_version": LATEST_VERSION,
        "applied": [item["id"] for item in MIGRATIONS if int(item["version"]) <= current],
        "pending": [item["id"] for item in MIGRATIONS if int(item["version"]) > current],
        "last_error": _LAST_ERROR,
        "ahead": current > LATEST_VERSION,
    }


def run(db):
    """按序跑未应用的迁移，返回 status() + applied_now。

    - 只前进：某项成功后写 user_version=该项 version；
    - 幂等：user_version 已覆盖的项直接跳过；
    - 失败不抛：记 last_error、停在该项之前，交给调用方兜底（不阻断启动）。
    """
    global _LAST_ERROR
    with _LOCK:
        before = status(db)
        if before["ahead"]:
            logger.warning(
                "[amo] DB schema user_version=%d 高于代码期望的 %d，不降级，只警告",
                before["user_version"], LATEST_VERSION,
            )
            before["applied_now"] = []
            return before
        current = before["user_version"]
        applied_now = []
        for item in sorted(MIGRATIONS, key=lambda entry: int(entry["version"])):
            version = int(item["version"])
            if version <= current:
                continue
            # 已经迁过的库（current>0）升级到 v3/v4 一律先备份；只有从未迁过且全空的
            # 全新库才跳过，避免给新装用户制造空备份 / 多条 kv 键。
            if (version >= BACKUP_MIN_VERSION
                    and (before["user_version"] > 0 or has_data_to_protect(db))):
                try:
                    backup_database(db, from_version=current, to_version=version,
                                    reason="schema_migration:%s" % item["id"])
                except Exception as exc:
                    _LAST_ERROR = "%s: 迁移前热备失败: %s: %s" % (
                        item["id"], type(exc).__name__, exc)
                    logger.warning("[amo] 迁移 %s（v%d）前热备失败，暂不迁移：%s",
                                   item["id"], version, exc)
                    break
            fn = item.get("fn")
            if callable(fn):
                try:
                    fn(db)
                except Exception as exc:
                    _LAST_ERROR = "%s: %s: %s" % (item["id"], type(exc).__name__, exc)
                    logger.warning(
                        "[amo] DB 迁移 %s（v%d）失败，停在 v%d：%s",
                        item["id"], version, current, exc,
                    )
                    break
            try:
                set_user_version(db, version)
            except Exception as exc:
                _LAST_ERROR = "%s: 写 user_version 失败: %s" % (item["id"], exc)
                logger.warning("[amo] DB 迁移 %s 写版本号失败：%s", item["id"], exc)
                break
            current = version
            applied_now.append(item["id"])
        after = status(db)
        after["applied_now"] = applied_now
        return after