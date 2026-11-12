
from typing import TYPE_CHECKING
# -*- coding: utf-8 -*-
"""SQLite 存储层：连接、建表/迁移、kv、消息、记忆、日程落库。"""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

from datetime import timedelta
import json
import os
import sqlite3
import threading

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        PLUGIN_NAME,
        _now,
        logger,
        now_ts,
        today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            PLUGIN_NAME,
            _now,
            logger,
            now_ts,
            today_str,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            PLUGIN_NAME,
            _now,
            logger,
            now_ts,
            today_str,
        )


class AmoDB:
    def __init__(self, plugin_dir):
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            data_dir = os.path.join(get_astrbot_data_path(), "plugin_data", PLUGIN_NAME)
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.__init__", _amo_exc)
            data_dir = os.path.join(plugin_dir, "data")
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.__init__", _amo_exc)
            data_dir = plugin_dir
        self.path = os.path.join(data_dir, "amo_forever.db")
        # 4.0.0 起插件标识为 Astrbot_Amo_forever。
        # 4.0.6 保持新标识，并把 3.x 的 amo_forever 旧数据迁移到新目录。
        try:
            import shutil as _shutil
            data_root = get_astrbot_data_path()
            legacy_paths = [
                os.path.join(data_root, "plugin_data", "amo_forever", "amo_forever.db"),
                os.path.join(data_root, "plugin_data", "astrbot_plugin_amo_forever", "amo_forever.db"),
            ]
            current_size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
            if current_size < 4096:
                for legacy_path in legacy_paths:
                    if not os.path.exists(legacy_path) or os.path.getsize(legacy_path) < 4096:
                        continue
                    if os.path.exists(self.path):
                        _shutil.copy2(self.path, self.path + ".bak-4.0.6")
                    for suffix in ("-wal", "-shm", "-journal"):
                        stale = self.path + suffix
                        if os.path.exists(stale):
                            try:
                                os.remove(stale)
                            except Exception as _amo_exc:
                                _note_swallowed("amo_db.AmoDB.__init__", _amo_exc)
                    _shutil.copy2(legacy_path, self.path)
                    for suffix in ("-wal", "-shm"):
                        source = legacy_path + suffix
                        if os.path.exists(source):
                            _shutil.copy2(source, self.path + suffix)
                    logger.info("[amo] 已迁移旧版 amo_forever 数据: %s -> %s" % (legacy_path, self.path))
                    logger.info("[amo] 升级提示：插件标识保持 Astrbot_Amo_forever；旧 amo_forever 数据已迁移到新目录，配置文件也会自动合并。")
                    break
        except Exception as exc:
            logger.warning("[amo] 旧版 amo_forever 数据迁移失败: %s" % exc)
        self.lock = threading.RLock()
        self._conn = None
        self._open()

    def _open(self):
        with self.lock:
            self._open_unlocked()

    def _open_unlocked(self):
        """打开（或重新打开）连接。

        插件重载时 AstrBot 可能先调用旧实例的 terminate()，而旧实例注册的
        Web API 仍然存在；这里保证连接被关闭后下一次访问会自动重开，避免
        "Cannot operate on a closed database"。
        """
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB._open_unlocked", _amo_exc)

    @property
    def conn(self):
        # Web 协程 + 后台 _tick + _ensure_ready 可能同时进来；不加锁会各自 connect，
        # 后一个覆盖前一个，先前的连接泄漏，PRAGMA journal_mode=WAL 还可能出现 database is locked。
        if self._conn is None:
            with self.lock:
                if self._conn is None:
                    self._open()
        return self._conn

    def init(self):
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    uid TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    alias TEXT DEFAULT '',
                    birthday TEXT DEFAULT '',
                    relation TEXT DEFAULT '普通',
                    affection INTEGER DEFAULT 10,
                    notes TEXT DEFAULT '',
                    platform TEXT DEFAULT '',
                    private_umo TEXT DEFAULT '',
                    last_group TEXT DEFAULT '',
                    first_seen REAL DEFAULT 0,
                    last_seen REAL DEFAULT 0,
                    msg_count INTEGER DEFAULT 0,
                    last_proactive REAL DEFAULT 0,
                    proactive_day TEXT DEFAULT '',
                    proactive_count INTEGER DEFAULT 0,
                    relation_at REAL DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    profile_updated_at REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    umo TEXT DEFAULT '',
                    uid TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    role TEXT DEFAULT 'user',
                    content TEXT DEFAULT '',
                    created_at REAL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_msg_umo ON messages(umo);
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT DEFAULT '',
                    updated REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS schedule (
                    day TEXT PRIMARY KEY,
                    items TEXT DEFAULT '[]',
                    updated REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS schedule_diary (
                    day TEXT DEFAULT '',
                    hour TEXT DEFAULT '',
                    text TEXT DEFAULT '',
                    updated REAL DEFAULT 0,
                    PRIMARY KEY(day, hour)
                );
                CREATE TABLE IF NOT EXISTS proactive_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT DEFAULT '',
                    kind TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    created REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS dialects (
                    term TEXT PRIMARY KEY,
                    meaning TEXT DEFAULT '',
                    region TEXT DEFAULT '',
                    example TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS relation_network (
                    a_uid TEXT NOT NULL,
                    b_uid TEXT NOT NULL,
                    relation TEXT DEFAULT '朋友',
                    note TEXT DEFAULT '',
                    updated_at REAL DEFAULT 0,
                    PRIMARY KEY(a_uid, b_uid)
                );
                """
            )
            self.conn.commit()
            self._seed_dialects()
            self._migrate_legacy()
            try:
                cols = {row[1] for row in self.conn.execute("PRAGMA table_info(profiles)").fetchall()}
                if "relation_at" not in cols:
                    self.conn.execute("ALTER TABLE profiles ADD COLUMN relation_at REAL DEFAULT 0")
                    self.conn.execute("UPDATE profiles SET relation_at=last_seen WHERE relation_at=0")
                    self.conn.commit()
                if "profile_updated_at" not in cols:
                    # 画像最后一次「本地手动修改」的时间：面板/命令改画像才更新，
                    # 收消息刷新 last_seen 不算，否则面板改完聊一句就被本地旧值覆盖。
                    self.conn.execute("ALTER TABLE profiles ADD COLUMN profile_updated_at REAL DEFAULT 0")
                    self.conn.commit()
            except Exception as _amo_exc:
                _note_swallowed("amo_db.AmoDB.init", _amo_exc)

    def _migrate_legacy(self):
        try:
            has_old = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_status'"
            ).fetchone()
            if has_old:
                rows = self.conn.execute(
                    "SELECT uid, relation, mem_count FROM user_status"
                ).fetchall()
                for row in rows:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO profiles(uid, relation, msg_count, first_seen, last_seen) "
                        "VALUES(?,?,?,?,?)",
                        (row["uid"], row["relation"] or "普通", row["mem_count"] or 0, now_ts(), now_ts()),
                    )
            self.conn.commit()
        except Exception as exc:
            logger.debug("[amo] 旧数据迁移跳过: %s" % exc)

    def close(self):
        with self.lock:
            if self._conn is None:
                return
            try:
                self._conn.commit()
            except Exception as _amo_exc:
                _note_swallowed("amo_db.AmoDB.close", _amo_exc)
            try:
                self._conn.close()
            except Exception as _amo_exc:
                _note_swallowed("amo_db.AmoDB.close", _amo_exc)
            self._conn = None

    # ---------- KV ----------
    def kv_get(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.kv_get", _amo_exc)
            return default

    def kv_set(self, key, value):
        with self.lock:
            self.conn.execute(
                "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (key, json.dumps(value, ensure_ascii=False), now_ts()),
            )
            self.conn.commit()

    # ---------- 档案 ----------
    def get_profile(self, uid):
        with self.lock:
            row = self.conn.execute("SELECT * FROM profiles WHERE uid=?", (str(uid),)).fetchone()
        return dict(row) if row else {}

    def touch_profile(self, uid, name, platform, umo, group_id, private):
        uid = str(uid)
        name = name or ""
        now = now_ts()
        with self.lock:
            row = self.conn.execute("SELECT * FROM profiles WHERE uid=?", (uid,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO profiles(uid, name, platform, private_umo, last_group, "
                    "first_seen, last_seen, msg_count) VALUES(?,?,?,?,?,?,?,1)",
                    (uid, name, platform or "", umo if private else "",
                     umo if not private else "", now, now),
                )
            else:
                updates = ["last_seen=?", "msg_count=msg_count+1"]
                values = [now]
                if name and not row["name"]:
                    updates.append("name=?")
                    values.append(name)
                if private:
                    updates.append("private_umo=?")
                    values.append(umo)
                elif group_id:
                    updates.append("last_group=?")
                    values.append(umo)
                values.append(uid)
                self.conn.execute(
                    "UPDATE profiles SET " + ", ".join(updates) + " WHERE uid=?", values
                )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM profiles WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else {}

    def update_profile(self, uid, mark_local=True, **fields):
        allowed = ("name", "alias", "birthday", "relation", "notes",
                   "affection", "is_admin")
        updates = []
        values = []
        for key, value in fields.items():
            if key in allowed:
                updates.append(key + "=?")
                values.append(value)
        if not updates:
            return False
        if "relation" in fields:
            updates.append("relation_at=?")
            values.append(now_ts())
        if mark_local:
            updates.append("profile_updated_at=?")
            values.append(now_ts())
        values.append(str(uid))
        with self.lock:
            self.conn.execute(
                "UPDATE profiles SET " + ", ".join(updates) + " WHERE uid=?", values
            )
            self.conn.commit()
        return True

    def delete_profile(self, uid):
        with self.lock:
            self.conn.execute("DELETE FROM profiles WHERE uid=?", (str(uid),))
            self.conn.commit()

    def list_profiles(self, keyword="", limit=100):
        limit = max(1, min(int(limit), 500))
        with self.lock:
            if keyword:
                like = "%" + str(keyword) + "%"
                rows = self.conn.execute(
                    "SELECT * FROM profiles WHERE uid LIKE ? OR name LIKE ? OR alias LIKE ? "
                    "ORDER BY last_seen DESC LIMIT ?",
                    (like, like, like, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM profiles ORDER BY last_seen DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

    def _seed_dialects(self):
        rows = [
            ("么子", "什么", "贵州·土家族地区", "你在搞么子？"),
            ("哪门", "怎么", "贵州·土家族地区", "哪门搞的？"),
            ("要得", "好的", "贵州·土家族地区", "要得，没问题"),
            ("晓得", "知道", "贵州·土家族地区", "我晓得了"),
            ("恰饭", "吃饭", "贵州·土家族地区", "走，恰饭去"),
            ("莫", "不要", "贵州·土家族地区", "你莫急"),
            ("屋头", "家里", "贵州·土家族地区", "在屋头"),
            ("落雨", "下雨", "贵州·土家族地区", "外头落雨了"),
            ("安逸", "舒服", "贵州·土家族地区", "好安逸"),
            ("拐哒", "糟糕", "贵州·土家族地区", "拐哒，忘了"),
            ("摆龙门阵", "聊天", "贵州·土家族地区", "来摆龙门阵"),
            ("崽崽", "小孩", "贵州·土家族地区", "那个崽崽"),
            ("老汉", "父亲", "贵州·土家族地区", "我老汉"),
            ("堂客", "妻子", "贵州·土家族地区", "他堂客"),
            ("哈戳戳", "傻乎乎", "贵州·土家族地区", "你哈戳戳的"),
            ("扯皮", "吵架", "贵州·土家族地区", "莫扯皮"),
            ("逗是", "就是", "贵州·土家族地区", "逗是这么回事"),
            ("热和", "暖和", "贵州·土家族地区", "屋里热和"),
            ("搞么子", "干什么", "贵州·土家族地区", "你搞么子"),
            ("巴适", "舒服", "贵州·土家族地区", "巴适得很"),
            ("啷个", "怎么", "贵州·土家族地区", "啷个办嘛"),
            ("哪样", "什么", "贵州·土家族地区", "你要哪样"),
            ("为哪样", "为什么", "贵州·土家族地区", "你为哪样不吃饭"),
            ("恁个", "这么", "贵州·土家族地区", "恁个热"),
            ("恁么", "那么", "贵州·土家族地区", "恁么远"),
            ("等哈", "等一下", "贵州·土家族地区", "等哈我"),
            ("一哈", "一下", "贵州·土家族地区", "看一哈"),
            ("哈哈儿", "一会儿", "贵州·土家族地区", "哈哈儿就来"),
            ("将才", "刚才", "贵州·土家族地区", "将才说的"),
            ("这哈", "现在", "贵州·土家族地区", "这哈忙"),
            ("那哈", "那时候", "贵州·土家族地区", "那哈还小"),
            ("哪哈", "什么时候", "贵州·土家族地区", "你哪哈来"),
            ("好久", "多久", "贵州·土家族地区", "等了好久"),
            ("清早", "早晨", "贵州·土家族地区", "清早八晨"),
            ("晌午", "中午", "贵州·土家族地区", "晌午吃饭"),
            ("黑了", "晚上", "贵州·土家族地区", "黑了再说"),
            ("昨儿个", "昨天", "贵州·土家族地区", "昨儿个下雨"),
            ("今儿个", "今天", "贵州·土家族地区", "今儿个赶场"),
            ("明儿个", "明天", "贵州·土家族地区", "明儿个去"),
            ("赶场", "赶集", "贵州·土家族地区", "去镇上赶场"),
            ("吃酒", "参加酒席", "贵州·土家族地区", "明天去吃酒"),
            ("坐席", "入席", "贵州·土家族地区", "快坐席"),
            ("背篓", "竹背篓", "贵州·土家族地区", "背个背篓"),
            ("山卡卡", "山沟里", "贵州·土家族地区", "山卡卡里头"),
            ("坝子", "平地", "贵州·土家族地区", "坝子上晒谷"),
            ("坎坎", "台阶或坎", "贵州·土家族地区", "小心坎坎"),
            ("岩脚", "山岩下", "贵州·土家族地区", "在岩脚歇气"),
            ("河沟", "小河", "贵州·土家族地区", "河沟里摸鱼"),
            ("扯火闪", "闪电", "贵州·土家族地区", "天边扯火闪"),
            ("雪米子", "小冰粒", "贵州·土家族地区", "下雪米子了"),
            ("凌冰", "冰", "贵州·土家族地区", "路上结凌冰"),
            ("太阳坝", "向阳的地方", "贵州·土家族地区", "去太阳坝坐"),
            ("阴凉坝", "阴凉处", "贵州·土家族地区", "树脚阴凉坝"),
            ("热天", "夏天", "贵州·土家族地区", "热天莫出门"),
            ("冷天", "冬天", "贵州·土家族地区", "冷天烤火"),
            ("莫慌", "别急", "贵州·土家族地区", "莫慌，慢慢来"),
            ("慢点", "慢一点", "贵州·土家族地区", "你慢点走"),
            ("归一", "完成、结束", "贵州·土家族地区", "事情搞归一了"),
            ("刹角", "收尾、结束", "贵州·土家族地区", "今天刹角了"),
            ("杀割", "结束", "贵州·土家族地区", "饭吃杀割了"),
            ("架势", "开始、用力", "贵州·土家族地区", "架势干"),
            ("稀罕", "喜欢、少见", "贵州·土家族地区", "我不稀罕"),
            ("撇脱", "干脆、简单", "贵州·土家族地区", "撇脱点"),
            ("巴心巴肠", "一心一意", "贵州·土家族地区", "巴心巴肠对你"),
            ("焦人", "让人着急", "贵州·土家族地区", "这事焦人"),
            ("恼火", "难受、麻烦", "贵州·土家族地区", "心头恼火"),
            ("造孽", "可怜", "贵州·土家族地区", "这娃儿造孽"),
            ("舒气", "整洁、清爽", "贵州·土家族地区", "屋头收拾得舒气"),
            ("冰凊", "冰凉", "贵州·土家族地区", "水冰凊"),
            ("嘎公", "外公", "贵州·土家族地区", "我嘎公"),
            ("嘎婆", "外婆", "贵州·土家族地区", "我嘎婆"),
            ("舅公", "舅爷", "贵州·土家族地区", "舅公来了"),
            ("舅婆", "舅奶", "贵州·土家族地区", "舅婆做饭"),
            ("幺爸", "最小的叔叔", "贵州·土家族地区", "幺爸在家"),
            ("幺妈", "最小的婶婶", "贵州·土家族地区", "幺妈来了"),
            ("嬢嬢", "阿姨", "贵州·土家族地区", "嬢嬢好"),
            ("老表", "表亲或哥们", "贵州·土家族地区", "老表，走哪去"),
            ("家门", "同姓、本家", "贵州·土家族地区", "我们是家门"),
            ("老庚", "同岁好友", "贵州·土家族地区", "他是我老庚"),
            ("娃儿", "孩子", "贵州·土家族地区", "这个娃儿乖"),
            ("妹崽", "女孩", "贵州·土家族地区", "那个妹崽"),
            ("男娃儿", "男孩", "贵州·土家族地区", "男娃儿调皮"),
            ("吃莽莽", "吃饭，哄小孩说法", "贵州·土家族地区", "走，吃莽莽"),
            ("嘎嘎", "肉，儿语", "贵州·土家族地区", "要吃嘎嘎"),
            ("粑粑", "饼、糍粑", "贵州·土家族地区", "烤粑粑"),
            ("折耳根", "鱼腥草", "贵州·土家族地区", "凉拌折耳根"),
            ("酸汤", "酸汤", "贵州·土家族地区", "酸汤鱼"),
            ("糟辣椒", "糟辣椒", "贵州·土家族地区", "糟辣椒炒肉"),
            ("油茶", "油茶", "贵州·土家族地区", "喝碗油茶"),
            ("洋芋", "土豆", "贵州·土家族地区", "烤洋芋"),
            ("苞谷", "玉米", "贵州·土家族地区", "掰苞谷"),
            ("红苕", "红薯", "贵州·土家族地区", "烤红苕"),
            ("海椒", "辣椒", "贵州·土家族地区", "海椒面"),
            ("盐须", "香菜", "贵州·土家族地区", "放点盐须"),
            ("鬼火戳", "生气、窝火", "贵州·土家族地区", "鬼火戳得很"),
            ("毛焦火辣", "焦急", "贵州·土家族地区", "等得毛焦火辣"),
            ("扯把子", "开玩笑", "贵州·土家族地区", "你莫扯把子"),
            ("冲壳子", "吹牛", "贵州·土家族地区", "又冲壳子"),
            ("摆闲条", "闲聊", "贵州·土家族地区", "来摆闲条"),
            ("装莽", "装傻", "贵州·土家族地区", "你莫装莽"),
            ("千翻", "调皮", "贵州·土家族地区", "这娃儿千翻"),
            ("费头子", "调皮鬼", "贵州·土家族地区", "这个费头子"),
            ("猴跳跳", "顽皮好动", "贵州·土家族地区", "猴跳跳的"),
            ("落教", "懂事", "贵州·土家族地区", "这娃儿落教"),
            ("不落教", "不懂事", "贵州·土家族地区", "你莫不落教"),
            ("弯酸", "刁难、挖苦", "贵州·土家族地区", "莫弯酸人"),
            ("洗刷", "数落", "贵州·土家族地区", "又洗刷我"),
            ("日诀", "骂", "贵州·土家族地区", "莫日诀人"),
            ("割孽", "吵架、打架", "贵州·土家族地区", "两个人割孽"),
            ("扯筋", "扯皮", "贵州·土家族地区", "莫扯筋"),
            ("咋整", "怎么办", "东北", "咋整啊"),
            ("得瑟", "显摆", "东北", "别得瑟"),
            ("忽悠", "骗", "东北", "别忽悠我"),
            ("埋汰", "脏、讽刺", "东北", "别埋汰人"),
            ("唠嗑", "聊天", "东北", "来唠嗑"),
            ("嘎哈", "干什么", "东北", "你嘎哈呢"),
            ("老铁", "哥们", "东北", "老铁没毛病"),
            ("贼拉", "非常", "东北", "贼拉冷"),
            ("稀罕", "喜欢", "东北", "我稀罕你"),
            ("麻溜", "快点", "东北", "麻溜的"),
            ("嘚吧", "唠叨", "东北", "别嘚吧了"),
            ("秃噜", "滑脱、说漏", "东北", "别秃噜了"),
            ("稀碎", "碎得很", "东北", "摔稀碎"),
            ("吭哧", "磨蹭", "东北", "别吭哧了"),
            ("瓜娃子", "傻孩子", "四川", "你个瓜娃子"),
            ("雄起", "加油", "四川", "雄起"),
            ("莫得", "没有", "四川", "莫得问题"),
            ("撇脱", "干脆、利落", "四川", "撇脱点"),
            ("巴心不得", "求之不得", "四川", "我巴心不得"),
            ("耍朋友", "谈恋爱", "四川", "耍朋友没"),
            ("搞快些", "快点", "四川", "搞快些"),
            ("啷个办", "怎么办", "四川", "啷个办嘛"),
            ("唔该", "谢谢、劳驾", "广东", "唔该晒"),
            ("多谢", "谢谢", "广东", "多谢你"),
            ("食饭", "吃饭", "广东", "去食饭"),
            ("饮茶", "喝茶", "广东", "去饮茶"),
            ("靓仔", "帅哥", "广东", "靓仔"),
            ("靓女", "美女", "广东", "靓女"),
            ("点解", "为什么", "广东", "点解啊"),
            ("冇", "没有", "广东", "冇问题"),
            ("得闲", "有空", "广东", "得闲饮茶"),
            ("搞掂", "搞定", "广东", "搞掂啦"),
            ("早唞", "晚安", "广东", "早唞"),
            ("好正", "很棒", "广东", "好正"),
            ("唔使", "不用", "广东", "唔使客气"),
            ("酱紫", "这样", "台湾", "酱紫啊"),
            ("机车", "麻烦、烦人", "台湾", "很机车"),
            ("很瞎", "离谱", "台湾", "很瞎"),
            ("阿嬷", "奶奶", "台湾", "阿嬷"),
            ("蛮", "很", "台湾", "蛮好的"),
            ("超赞", "非常棒", "台湾", "超赞"),
            ("拍谢", "不好意思", "台湾", "拍谢啦"),
            ("古早", "传统、旧时", "台湾", "古早味"),
            ("龟毛", "挑剔", "台湾", "别龟毛"),
            ("嘛呢", "干什么", "天津", "嘛呢"),
            ("介", "这", "天津", "介是嘛"),
            ("倍儿", "非常", "天津", "倍儿好"),
            ("哏儿", "有趣", "天津", "真哏儿"),
            ("腻歪", "腻烦、黏人", "天津", "别腻歪"),
            ("得嘞", "好的", "天津", "得嘞"),
            ("幺蛾子", "麻烦", "天津", "出幺蛾子"),
            ("耐人", "招人喜欢", "天津", "真耐人"),
            ("走鸡", "事情办砸", "天津", "走鸡了"),
            ("各色", "特别、难相处", "天津", "真各色"),
            ("弄啥嘞", "干什么", "河南", "你弄啥嘞"),
            ("中", "好", "河南", "中，就这么办"),
            ("得劲", "舒服", "河南", "真得劲"),
            ("木有", "没有", "河南", "木有事"),
            ("恁", "你", "河南", "恁吃饭没"),
            ("俺", "我", "河南", "俺知道了"),
            ("嘟囔", "嘀咕", "河南", "别嘟囔"),
            ("夜儿个", "昨天", "河南", "夜儿个下雨"),
            ("咋着", "怎么了", "河南", "咋着了"),
            ("中不中", "行不行", "河南", "中不中"),
            ("喝汤", "吃晚饭", "河南", "喝汤没"),
            ("阿拉", "我、我们", "上海", "阿拉晓得"),
            ("侬", "你", "上海", "侬好"),
            ("哪能", "怎么", "上海", "哪能办"),
            ("结棍", "厉害", "上海", "侬结棍"),
            ("老灵", "很好", "上海", "老灵额"),
            ("白相", "玩", "上海", "出去白相"),
            ("适意", "舒服", "上海", "适意"),
            ("勿", "不", "上海", "勿晓得"),
            ("谢谢侬", "谢谢你", "上海", "谢谢侬"),
            ("辰光", "时候", "上海", "啥辰光"),
            ("困觉", "睡觉", "上海", "去困觉"),
            ("覅", "不要", "上海", "覅去"),
        ]
        with self.lock:
            for term, meaning, region, example in rows:
                self.conn.execute(
                    "INSERT OR IGNORE INTO dialects(term, meaning, region, example) VALUES(?,?,?,?)",
                    (term, meaning, region, example),
                )
            self.conn.commit()

    def list_dialects(self):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM dialects").fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _relation_pair(a_uid, b_uid):
        a, b = str(a_uid or "").strip(), str(b_uid or "").strip()
        return (a, b) if a <= b else (b, a)

    def list_relations(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM relation_network ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def relations_for(self, uid):
        uid = str(uid or "")
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM relation_network WHERE a_uid=? OR b_uid=? ORDER BY updated_at DESC",
                (uid, uid),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_relation(self, a_uid, b_uid, relation, note=""):
        a, b = self._relation_pair(a_uid, b_uid)
        if not a or not b or a == b:
            return False
        # 机器人自己的账号不是关系网里的「人」，别把它写成节点
        try:
            self_uid = str(self.kv_get("self_uid", "") or "")
        except Exception:
            self_uid = ""
        if self_uid and self_uid in (str(a_uid), str(b_uid)):
            return False
        with self.lock:
            self.conn.execute(
                "INSERT INTO relation_network(a_uid, b_uid, relation, note, updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(a_uid, b_uid) DO UPDATE SET "
                "relation=excluded.relation, note=excluded.note, updated_at=excluded.updated_at",
                (a, b, str(relation or "朋友"), str(note or "")[:200], now_ts()),
            )
            self.conn.commit()
        return True

    def delete_relation(self, a_uid, b_uid):
        a, b = self._relation_pair(a_uid, b_uid)
        with self.lock:
            cursor = self.conn.execute(
                "DELETE FROM relation_network WHERE a_uid=? AND b_uid=?", (a, b)
            )
            self.conn.commit()
            return cursor.rowcount > 0

    def proactive_profiles(self, limit=30):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM profiles WHERE private_umo != '' AND relation NOT IN ('仇恨','抵触') "
                "ORDER BY last_proactive ASC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def bump_proactive(self, uid):
        today = today_str()
        with self.lock:
            row = self.conn.execute(
                "SELECT proactive_day, proactive_count FROM profiles WHERE uid=?", (str(uid),)
            ).fetchone()
            if not row:
                return
            count = int(row["proactive_count"] or 0) + 1
            if row["proactive_day"] != today:
                count = 1
            self.conn.execute(
                "UPDATE profiles SET last_proactive=?, proactive_day=?, proactive_count=? WHERE uid=?",
                (now_ts(), today, count, str(uid)),
            )
            self.conn.commit()

    def log_proactive(self, uid, kind, content):
        with self.lock:
            self.conn.execute(
                "INSERT INTO proactive_log(uid, kind, content, created) VALUES(?,?,?,?)",
                (str(uid), kind, content, now_ts()),
            )
            self.conn.commit()

    # ---------- 消息 ----------
    def add_message(self, umo, uid, name, role, content):
        content = str(content or "").strip()
        if not content:
            return
        with self.lock:
            self.conn.execute(
                "INSERT INTO messages(umo, uid, name, role, content, created_at) VALUES(?,?,?,?,?,?)",
                (str(umo), str(uid), str(name or ""), role, content, now_ts()),
            )
            self.conn.commit()

    def recent_messages(self, umo, limit=10):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE umo=? ORDER BY id DESC LIMIT ?",
                (str(umo), int(limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def messages_by_uid(self, uid, limit=20):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE uid=? ORDER BY id DESC LIMIT ?",
                (str(uid), int(limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def chat_history(self, uid, limit=200):
        """按用户参与过的 umo 取原始聊天记录（本人 + 机器人），不是摘要记忆。"""
        uid = str(uid)
        limit = max(1, min(2000, int(limit or 200)))
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT umo FROM messages WHERE uid=? AND umo!=''",
                (uid,),
            ).fetchall()
            umos = [str(row["umo"]) for row in rows if str(row["umo"] or "")]
            if not umos:
                rows = self.conn.execute(
                    "SELECT * FROM messages WHERE uid=? ORDER BY id DESC LIMIT ?",
                    (uid, limit),
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in umos)
                params = umos + [uid, limit]
                rows = self.conn.execute(
                    "SELECT * FROM messages WHERE umo IN (" + placeholders + ") "
                    "AND (uid=? OR role='bot') ORDER BY id DESC LIMIT ?",
                    params,
                ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def interaction_stats(self, days=30):
        """按 uid 统计真实互动量（只算用户自己说的话），给关系沉淀用。

        ``days <= 0`` 表示统计全部历史，这样很早以前聊过的人也会被记进关系网。
        """
        days = int(days or 0)
        cutoff = 0 if days <= 0 else now_ts() - days * 86400
        with self.lock:
            rows = self.conn.execute(
                "SELECT uid, MAX(name) AS name, COUNT(*) AS c, MIN(created_at) AS f, MAX(created_at) AS l "
                "FROM messages WHERE role='user' AND uid!='' AND uid!='bot' AND created_at>=? "
                "GROUP BY uid ORDER BY c DESC LIMIT 1000",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_users(self, since, limit=2):
        with self.lock:
            rows = self.conn.execute(
                "SELECT uid, name, COUNT(*) AS c FROM messages WHERE role='user' AND created_at>? "
                "GROUP BY uid ORDER BY c DESC LIMIT ?", (float(since), int(limit))
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_messages(self, days=14):
        with self.lock:
            self.conn.execute("DELETE FROM messages WHERE created_at<?",
                              (now_ts() - max(1, int(days)) * 86400,))
            self.conn.commit()

    # ---------- 日程 ----------
    def save_schedule(self, day, items):
        with self.lock:
            self.conn.execute(
                "INSERT INTO schedule(day, items, updated) VALUES(?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET items=excluded.items, updated=excluded.updated",
                (str(day), json.dumps(items, ensure_ascii=False), now_ts()),
            )
            self.conn.commit()

    def fade_schedules(self, days=7):
        """7~14 天前的日程去掉细节，只保留每小时骨架。"""
        cutoff = (_now().date() - timedelta(days=max(1, int(days)))).isoformat()
        with self.lock:
            rows = self.conn.execute(
                "SELECT day, items FROM schedule WHERE day < ?", (cutoff,)
            ).fetchall()
            for row in rows:
                try:
                    items = json.loads(row["items"])
                except Exception as _amo_exc:
                    _note_swallowed("amo_db.AmoDB.fade_schedules", _amo_exc)
                    continue
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        item.pop("detail", None)
                self.conn.execute(
                    "UPDATE schedule SET items=? WHERE day=?",
                    (json.dumps(items, ensure_ascii=False), row["day"]),
                )
            self.conn.commit()

    def purge_schedules(self, days=14):
        cutoff = (_now().date() - timedelta(days=max(1, int(days)))).isoformat()
        with self.lock:
            self.conn.execute("DELETE FROM schedule WHERE day < ?", (cutoff,))
            self.conn.commit()

    def get_schedule(self, day):
        with self.lock:
            row = self.conn.execute("SELECT items FROM schedule WHERE day=?", (str(day),)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["items"])
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.get_schedule", _amo_exc)
            return None

    def get_diary(self, day, hour):
        with self.lock:
            row = self.conn.execute(
                "SELECT text FROM schedule_diary WHERE day=? AND hour=?",
                (str(day), str(hour)),
            ).fetchone()
        return str(row["text"] or "") if row else ""

    def save_diary(self, day, hour, text):
        with self.lock:
            self.conn.execute(
                "INSERT INTO schedule_diary(day, hour, text, updated) VALUES(?,?,?,?) "
                "ON CONFLICT(day, hour) DO UPDATE SET text=excluded.text, updated=excluded.updated",
                (str(day), str(hour), str(text or ""), now_ts()),
            )
            self.conn.commit()

    def clear_diaries(self, day):
        with self.lock:
            self.conn.execute("DELETE FROM schedule_diary WHERE day=?", (str(day),))
            self.conn.commit()

    def clear_diaries_from(self, day, hour):
        """只清某个小时及以后的小日记（换城覆盖日程时用，过去的留着）。"""
        with self.lock:
            # hour 是 TEXT 列：直接比字符串会认为 "9" >= "10"（字典序），必须转成整数比
            self.conn.execute(
                "DELETE FROM schedule_diary WHERE day=? AND CAST(hour AS INTEGER)>=CAST(? AS INTEGER)",
                (str(day), str(hour)),
            )
            self.conn.commit()

    def purge_diaries(self, days=30):
        cutoff = (_now().date() - timedelta(days=max(1, int(days)))).isoformat()
        with self.lock:
            self.conn.execute("DELETE FROM schedule_diary WHERE day < ?", (cutoff,))
            self.conn.commit()

    # ---------- 统计 ----------
    def counts(self):
        with self.lock:
            profiles = self.conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()["c"]
            memories = 0
            messages = self.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {"profiles": profiles, "memories": memories, "messages": messages}
