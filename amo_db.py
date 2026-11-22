"""SQLite 存储层：连接、建表/迁移、kv、消息、记忆、日程落库。"""
from typing import Any, TYPE_CHECKING

try:  # 兼容层：旧插件名 / 旧数据目录 / 旧配置名的唯一来源
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat
# -*- coding: utf-8 -*-

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
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
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                PLUGIN_NAME,
                _now,
                logger,
                now_ts,
                today_str,
            )



try:  # DB schema 版本注册表（Phase 7）：PRAGMA user_version + 迁移项
    from . import amo_db_schema as _db_schema
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_db_schema as _db_schema  # type: ignore[no-redef]


DIALECT_SEEDS = (
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
)

#: 块日记迁移规则版本。1=首版（整点旧行直接当块日记）；2=只有块窗口完全落在旧整点
#: 小时内才可信，跨多小时/起点偏移的旧行一律 stale（详情按块窗口重新生成）。
#: 升版会用 block_diary_migrate_version 触发一次性重判，只动迁移副本，不动新生成的行。
BLOCK_DIARY_MIGRATE_VERSION = 2


SCHEMA_SQL = """
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
CREATE TABLE IF NOT EXISTS todo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT DEFAULT '',
    start TEXT DEFAULT '',
    end_time TEXT DEFAULT '',
    activity TEXT DEFAULT '',
    place TEXT DEFAULT '',
    category TEXT DEFAULT '',
    source TEXT DEFAULT '',
    cost_cents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    created REAL DEFAULT 0,
    done REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_todo_day ON todo(day, status);
CREATE TABLE IF NOT EXISTS schedule_diary (
    day TEXT DEFAULT '',
    hour TEXT DEFAULT '',
    text TEXT DEFAULT '',
    updated REAL DEFAULT 0,
    version INTEGER DEFAULT 0,
    status TEXT DEFAULT 'valid',
    invalidated_at REAL DEFAULT 0,
    PRIMARY KEY(day, hour)
);
CREATE TABLE IF NOT EXISTS block_diary (
    day TEXT DEFAULT '',
    block_id TEXT DEFAULT '',
    start TEXT DEFAULT '',
    end TEXT DEFAULT '',
    place TEXT DEFAULT '',
    text TEXT DEFAULT '',
    updated REAL DEFAULT 0,
    version INTEGER DEFAULT 0,
    status TEXT DEFAULT 'valid',
    invalidated_at REAL DEFAULT 0,
    source TEXT DEFAULT '',
    PRIMARY KEY(day, block_id)
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


def _kv_prefix_upper(prefix):
    """前缀区间的右边界（用范围查询代替 LIKE，避免 '_' 被当成通配符）。"""
    prefix = str(prefix or "")
    if not prefix:
        return ""
    try:
        return prefix[:-1] + chr(ord(prefix[-1]) + 1)
    except (TypeError, ValueError):
        return prefix + "\uffff"


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
        # 4.0.0 起插件标识为 Astrbot_Amo_forever（2026-11-12 起改为 Astrbot_amoupup_forever）。
        # 4.0.6 保持新标识，并把 3.x 的 amo_forever 旧数据迁移到新目录。
        try:
            import shutil as _shutil
            data_root = get_astrbot_data_path()
            legacy_paths = _compat.legacy_db_paths(data_root)
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
                    logger.info("[amo] 升级提示：插件标识现为 Astrbot_amoupup_forever；旧 amo_forever 数据已迁移到新目录，配置文件也会自动合并。")
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
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=20)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=20000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
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
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()
            self._seed_dialects()
            self._migrate_legacy()
            self._ensure_columns()
            self._run_schema_migrations()

    def _run_schema_migrations(self):
        """跑 schema 迁移注册表（Phase 7）：幂等、只前进；失败只记日志，不阻断启动。"""
        try:
            report = _db_schema.run(self)
        except Exception as exc:
            logger.warning("[amo] DB schema 迁移失败（已忽略）: %s" % exc)
            return None
        extra = ""
        if report.get("applied_now"):
            extra = "，本次应用 " + ",".join(report["applied_now"])
        logger.info("[amo] DB schema user_version=%d（applied=%s pending=%s%s）",
                    report["user_version"], ",".join(report["applied"]) or "-",
                    ",".join(report["pending"]) or "-", extra)
        return report

    def schema_status(self):
        """DB schema 版本快照 {user_version, applied, pending, last_error}（只读）。"""
        try:
            return _db_schema.status(self)
        except Exception as exc:
            return {"user_version": 0, "latest_version": _db_schema.LATEST_VERSION,
                    "applied": [], "pending": [],
                    "last_error": "%s: %s" % (type(exc).__name__, exc), "ahead": False}

    def _ensure_columns(self):
        """老库补列：profiles 的 relation_at/profile_updated_at，schedule_diary 的版本/状态字段。"""
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
        try:
            diary_cols = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(schedule_diary)").fetchall()
            }
            if "version" not in diary_cols:
                self.conn.execute(
                    "ALTER TABLE schedule_diary ADD COLUMN version INTEGER DEFAULT 0"
                )
            if "status" not in diary_cols:
                self.conn.execute(
                    "ALTER TABLE schedule_diary ADD COLUMN status TEXT DEFAULT 'valid'"
                )
            if "invalidated_at" not in diary_cols:
                self.conn.execute(
                    "ALTER TABLE schedule_diary ADD COLUMN invalidated_at REAL DEFAULT 0"
                )
            # 2026-11-20：区分谁写的这一小时。守护的财务/社交日记不再覆盖块日记。
            if "source" not in diary_cols:
                self.conn.execute(
                    "ALTER TABLE schedule_diary ADD COLUMN source TEXT DEFAULT ''"
                )
            self.conn.commit()
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.init.diary_columns", _amo_exc)
        try:
            # block_diary 的 source：区分“迁移副本”和之后真正生成/重写过的正文，
            # 规则升级时只重判迁移副本，绝不误杀新生成的 valid 行。
            block_cols = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(block_diary)").fetchall()
            }
            if block_cols and "source" not in block_cols:
                self.conn.execute(
                    "ALTER TABLE block_diary ADD COLUMN source TEXT DEFAULT ''"
                )
            self.conn.commit()
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.init.block_diary_columns", _amo_exc)

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

    def kv_delete(self, key):
        """删一个 kv 键，返回是否真的删掉（kv 删除统一入口，禁止外部裸 SQL）。"""
        with self.lock:
            cur = self.conn.execute("DELETE FROM kv WHERE key=?", (str(key),))
            self.conn.commit()
            return bool(cur.rowcount)

    def kv_delete_many(self, keys):
        """批量删 kv 键，返回实际删除条数（一次 commit）。"""
        rows = [str(key) for key in (keys or []) if str(key)]
        if not rows:
            return 0
        with self.lock:
            cur = self.conn.execute("DELETE FROM kv WHERE key IN (%s)"
                                    % ",".join("?" * len(rows)), tuple(rows))
            self.conn.commit()
            return int(cur.rowcount or 0)

    def kv_keys(self, prefix="", limit=200, offset=0):
        """按前缀分页取键（有序；prefix='' 表示全部）。"""
        prefix = str(prefix or "")
        try:
            limit = max(1, int(limit))
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            limit, offset = 200, 0
        with self.lock:
            if prefix:
                rows = self.conn.execute(
                    "SELECT key FROM kv WHERE key >= ? AND key < ? "
                    "ORDER BY key LIMIT ? OFFSET ?",
                    (prefix, _kv_prefix_upper(prefix), limit, offset),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT key FROM kv ORDER BY key LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
        return [str(row["key"]) for row in rows]

    def kv_rows(self, prefix="", limit=10000):
        """按前缀读 key/value/updated（只读；健康报告/巡检用）。"""
        prefix = str(prefix or "")
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 10000
        with self.lock:
            if prefix:
                rows = self.conn.execute(
                    "SELECT key, value, updated FROM kv WHERE key >= ? AND key < ? "
                    "ORDER BY key LIMIT ?",
                    (prefix, _kv_prefix_upper(prefix), limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT key, value, updated FROM kv ORDER BY key LIMIT ?", (limit,)
                ).fetchall()
        out = []
        for row in rows:
            raw = row["value"]
            try:
                value = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                value = None
            out.append({"key": str(row["key"]), "value": value,
                        "updated": float(row["updated"] or 0)})
        return out

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
        """把内置方言词表灌进 dialects 表（INSERT OR IGNORE，重复启动不会翻倍）。"""
        with self.lock:
            for term, meaning, region, example in DIALECT_SEEDS:
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

    def recent_proactive_texts(self, uid, hours=2, limit=3):
        """最近几小时主动说过的话（防同一件事反复说）。"""
        cutoff = now_ts() - max(1, int(hours)) * 3600
        with self.lock:
            rows = self.conn.execute(
                "SELECT content FROM proactive_log WHERE uid=? AND created>=? ORDER BY id DESC LIMIT ?",
                (str(uid), cutoff, max(1, int(limit))),
            ).fetchall()
        return [str(row["content"] or "") for row in rows if str(row["content"] or "").strip()]

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

    def purge_wallet_ledger(self, days=90):
        """只保留最近 90 天流水，避免账本把 SQLite 撑爆。"""
        cutoff = now_ts() - max(1, int(days)) * 86400
        try:
            with self.lock:
                self.conn.execute("DELETE FROM wallet_ledger WHERE ts<?", (cutoff,))
                self.conn.commit()
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.purge_wallet_ledger", _amo_exc)

    def purge_messages(self, days=14):
        with self.lock:
            self.conn.execute("DELETE FROM messages WHERE created_at<?",
                              (now_ts() - max(1, int(days)) * 86400,))
            self.conn.commit()

    # ---------- 日程 ----------
    # ---------- 明日待办（未来事件只存这里，不提前铺日程） ----------
    def add_todo(self, day, item, source=""):
        """把一条未来事件写进待办表；同一天同时间同活动视为同一条（更新不重复）。"""
        if not isinstance(item, dict):
            return 0
        day = str(day or "")
        start = str(item.get("start") or "")
        activity = str(item.get("activity") or "")
        if not day or not activity:
            return 0
        payload = (str(item.get("end") or ""), str(item.get("place") or ""),
                   str(item.get("category") or ""), str(item.get("source") or source or ""),
                   int(item.get("cost_cents") or 0))
        with self.lock:
            row = self.conn.execute(
                "SELECT id FROM todo WHERE day=? AND start=? AND activity=? AND status='open'",
                (day, start, activity),
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE todo SET end_time=?, place=?, category=?, source=?, cost_cents=?, created=? "
                    "WHERE id=?",
                    payload + (now_ts(), int(row["id"])),
                )
                self.conn.commit()
                return int(row["id"])
            cur = self.conn.execute(
                "INSERT INTO todo(day, start, end_time, activity, place, category, source, "
                "cost_cents, status, created) VALUES(?,?,?,?,?,?,?,?,'open',?)",
                (day, start, str(item.get("end") or ""), activity,
                 str(item.get("place") or ""), str(item.get("category") or ""),
                 str(item.get("source") or source or ""),
                 int(item.get("cost_cents") or 0), now_ts()),
            )
            self.conn.commit()
            return int(cur.lastrowid or 0)

    def todos(self, day, include_done=False):
        day = str(day or "")
        if not day:
            return []
        sql = ("SELECT id, day, start, end_time, activity, place, category, source, cost_cents, status "
               "FROM todo WHERE day=?")
        if not include_done:
            sql += " AND status='open'"
        sql += " ORDER BY start ASC, id ASC"
        try:
            with self.lock:
                rows = self.conn.execute(sql, (day,)).fetchall()
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.todos", _amo_exc)
            return []
        out = []
        for row in rows:
            out.append({"id": int(row["id"]), "day": str(row["day"] or ""),
                        "start": str(row["start"] or ""), "end": str(row["end_time"] or ""),
                        "activity": str(row["activity"] or ""), "place": str(row["place"] or ""),
                        "category": str(row["category"] or ""), "source": str(row["source"] or ""),
                        "cost_cents": int(row["cost_cents"] or 0),
                        "status": str(row["status"] or "open")})
        return out

    def close_todo(self, todo_id):
        try:
            with self.lock:
                self.conn.execute("UPDATE todo SET status='done', done=? WHERE id=?",
                                  (now_ts(), int(todo_id)))
                self.conn.commit()
            return True
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.close_todo", _amo_exc)
            return False

    def clear_todos(self, day):
        try:
            with self.lock:
                self.conn.execute("DELETE FROM todo WHERE day=?", (str(day),))
                self.conn.commit()
            return True
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.clear_todos", _amo_exc)
            return False

    def purge_todos(self, before_day):
        try:
            with self.lock:
                self.conn.execute("DELETE FROM todo WHERE day<?", (str(before_day),))
                self.conn.commit()
            return True
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.purge_todos", _amo_exc)
            return False

    def delete_schedule(self, day):
        try:
            with self.lock:
                self.conn.execute("DELETE FROM schedule WHERE day=?", (str(day),))
                self.conn.commit()
            return True
        except Exception as _amo_exc:
            _note_swallowed("amo_db.AmoDB.delete_schedule", _amo_exc)
            return False

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

    def schedule_version(self, day):
        """某天日程的变更版本号；每次真正改日程 +1。"""
        try:
            return int(self.kv_get("schedule_ver:" + str(day), 0) or 0)
        except Exception:
            return 0

    def bump_schedule_version(self, day):
        """把某天日程版本 +1，并返回新版本。"""
        with self.lock:
            version = self.schedule_version(day) + 1
            self.kv_set("schedule_ver:" + str(day), version)
            return version

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

    def all_schedules(self):
        """一次读出 schedule 表所有天：kv 镜像重建 / HumanState 读侧单一来源。"""
        with self.lock:
            rows = self.conn.execute("SELECT day, items FROM schedule").fetchall()
        output = {}
        for row in rows:
            try:
                items = json.loads(row["items"])
            except Exception as _amo_exc:
                _note_swallowed("amo_db.AmoDB.all_schedules", _amo_exc)
                continue
            if isinstance(items, list):
                output[str(row["day"])] = items
        return output

    def get_diary_record(self, day, hour):
        """返回小日记的完整记录（不存在返回 None）。"""
        with self.lock:
            row = self.conn.execute(
                "SELECT day, hour, text, updated, version, status, invalidated_at, source "
                "FROM schedule_diary WHERE day=? AND hour=?",
                (str(day), str(hour)),
            ).fetchone()
        if not row:
            return None
        return {
            "day": str(row["day"] or ""),
            "hour": str(row["hour"] or ""),
            "text": str(row["text"] or ""),
            "updated": float(row["updated"] or 0),
            "version": int(row["version"] or 0),
            "status": str(row["status"] or "valid"),
            "invalidated_at": float(row["invalidated_at"] or 0),
            "source": str(row["source"] or ""),
        }

    def get_diary(self, day, hour):
        with self.lock:
            row = self.conn.execute(
                "SELECT text FROM schedule_diary WHERE day=? AND hour=?",
                (str(day), str(hour)),
            ).fetchone()
        return str(row["text"] or "") if row else ""

    def save_diary(self, day, hour, text, version=None, status="valid", source="block"):
        """写入小日记；version 默认取当天当前日程版本。

        旧调用 save_diary(day, hour, text) 仍然兼容。
        source：block=陪伴的块日记（默认）；finance=守护的流水/社交日记。
        注意：**外部插件写这张表必须显式传 source**，否则会被当成块日记，
        从而挡住真正的块日记（2026-11-20）。
        """
        try:
            from .amo_diary_guard import sanitize_diary
        except ImportError:
            from amo_diary_guard import sanitize_diary
        text = sanitize_diary(text)
        if version is None:
            version = self.schedule_version(day)
        with self.lock:
            self.conn.execute(
                "INSERT INTO schedule_diary(day, hour, text, updated, version, status, invalidated_at, source) "
                "VALUES(?,?,?,?,?,?,0,?) "
                "ON CONFLICT(day, hour) DO UPDATE SET "
                "text=excluded.text, updated=excluded.updated, version=excluded.version, "
                "status=excluded.status, invalidated_at=0, source=excluded.source",
                (str(day), str(hour), str(text or ""), now_ts(), int(version),
                 str(status or "valid"), str(source or "block")),
            )
            self.conn.commit()

    def diary_is_valid(self, day, hour, version=None):
        """这条日记是否仍然是有效缓存。

        有效性以 status 为准：日程变更只把目标小时及以后标 stale，之前的日记
        即使 day 版本号变新也仍然有效，不能因为全局版本变化全部重算。
        """
        record = self.get_diary_record(day, hour)
        if not record:
            return False
        return record["status"] == "valid" and bool(str(record.get("text") or "").strip())

    def mark_diaries_stale(self, day, from_hour, version=None):
        """把某天 from_hour 及以后的旧整点表小日记标成 stale，不物理删除。

        旧文本保留，供生成失败时兜底显示；下一次读取/调度会按当前版本重算。
        返回被标记的行数。块级小日记不在这里按小时粗扫：身份是 event_id，
        由 sync_block_diaries 在日程落库点按 diff 精确失效 + 清孤儿。
        """
        with self.lock:
            params = [now_ts(), str(day), str(from_hour)]
            sql = (
                "UPDATE schedule_diary SET status='stale', invalidated_at=? "
                "WHERE day=? AND CAST(hour AS INTEGER)>=CAST(? AS INTEGER) "
                "AND status NOT IN ('stale', 'frozen', 'archived')"
            )
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            marked = int(cur.rowcount or 0)
        # 块级小日记不在这一步按小时粗扫（2026-11-22 P0 修）：改一块会把这一小时
        # 之后所有块日记一起打 stale（11-21 快照 1 分钟 14 次 bump → stale 风暴）。
        # 块日记身份是 event_id，由 sync_block_diaries 在日程落库点按 diff 精确失效。
        return marked

    def clear_diaries(self, day):
        with self.lock:
            self.conn.execute("DELETE FROM schedule_diary WHERE day=?", (str(day),))
            self.conn.commit()

    def clear_diaries_from(self, day, hour):
        """只清某个小时及以后的小日记（保留给旧调用/迁移用）。"""
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
            self.conn.execute("DELETE FROM block_diary WHERE day < ?", (cutoff,))
            self.conn.commit()

    # ---------- 块级小日记（block_diary） ----------
    _BLOCK_DIARY_FIELDS = ("day, block_id, start, end, place, text, updated, version, "
                           "status, invalidated_at, source")

    def _block_diary_record_row(self, day, block_id):
        """按 (day, block_id) 取一行；老库还没有 event_id 列时退回旧列集。"""
        legacy_sql = ("SELECT %s FROM block_diary WHERE day=? AND block_id=?"
                      % self._BLOCK_DIARY_FIELDS)
        try:
            row = self.conn.execute(
                "SELECT %s, event_id FROM block_diary WHERE day=? AND block_id=?"
                % self._BLOCK_DIARY_FIELDS, (str(day), str(block_id))).fetchone()
        except sqlite3.OperationalError:
            row = self.conn.execute(legacy_sql, (str(day), str(block_id))).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["event_id"] = data.get("block_id") or ""
            return data
        return dict(row) if row is not None else None

    def get_block_diary_record(self, day, block_id):
        """按块身份取完整记录（不存在返回 None）。"""
        with self.lock:
            row = self._block_diary_record_row(day, block_id)
        if not row:
            return None
        return {
            "day": str(row["day"] or ""),
            "block_id": str(row["block_id"] or ""),
            "event_id": str(row.get("event_id") or row["block_id"] or ""),
            "start": str(row["start"] or ""),
            "end": str(row["end"] or ""),
            "place": str(row["place"] or ""),
            "text": str(row["text"] or ""),
            "updated": float(row["updated"] or 0),
            "version": int(row["version"] or 0),
            "status": str(row["status"] or "valid"),
            "invalidated_at": float(row["invalidated_at"] or 0),
            "source": str(row["source"] or ""),
        }

    def get_block_diary(self, day, block_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT text FROM block_diary WHERE day=? AND block_id=?",
                (str(day), str(block_id)),
            ).fetchone()
        return str(row["text"] or "") if row else ""

    def save_block_diary(self, day, block_id="", text="", start="", end="", place="",
                         version=None, status="valid", source="", event_id=""):
        """按块身份写入小日记；block_id / event_id 两列写同一个身份。

        block_id 仍是主键（旧代码照旧读），event_id 是显式列。两个都传时以 event_id
        为准并把 block_id 对齐成同一个值——两列永远相等；v4 之前的老库退回旧列。
        """
        try:
            from .amo_diary_guard import sanitize_diary
        except ImportError:
            from amo_diary_guard import sanitize_diary
        text = sanitize_diary(text)
        ident = str(event_id or block_id or "").strip()
        if version is None:
            version = self.schedule_version(day)
        common = (str(start or ""), str(end or ""), str(place or ""), str(text or ""),
                  now_ts(), int(version), str(status or "valid"), str(source or ""))
        legacy_params = (str(day), ident) + common
        with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO block_diary(day, block_id, event_id, start, end, place, "
                    "text, updated, version, status, invalidated_at, source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,0,?) "
                    "ON CONFLICT(day, block_id) DO UPDATE SET "
                    "event_id=excluded.event_id, start=excluded.start, "
                    "end=excluded.end, place=excluded.place, text=excluded.text, "
                    "updated=excluded.updated, version=excluded.version, "
                    "status=excluded.status, invalidated_at=0, source=excluded.source",
                    (str(day), ident, ident) + common,
                )
            except sqlite3.OperationalError:
                # v4 之前的老库没有 event_id 列：先写旧列，迁移后由 v4 回填。
                self.conn.execute(
                    "INSERT INTO block_diary(day, block_id, start, end, place, text, "
                    "updated, version, status, invalidated_at, source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,?) "
                    "ON CONFLICT(day, block_id) DO UPDATE SET "
                    "start=excluded.start, end=excluded.end, place=excluded.place, "
                    "text=excluded.text, updated=excluded.updated, "
                    "version=excluded.version, status=excluded.status, "
                    "invalidated_at=0, source=excluded.source",
                    legacy_params,
                )
            self.conn.commit()

    def _current_block_ids(self, day, items):
        """当前日程里的块身份集合（event_id 优先，旧数据退回内容哈希）。"""
        try:
            from .amo_state_common import block_id as _bid
        except ImportError:  # 直接按文件加载时
            from amo_state_common import block_id as _bid
        ids = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                ids.add(str(_bid(day, item)))
            except Exception:  # cleanup: 单条坏块不影响整体核对
                continue
        return ids

    def block_diary_is_valid(self, day, block_id, version=None):
        """这一块是否已经有一条有效缓存：status + 正文 + 块还在 + 身份/窗口对得上。

        2026-11-22 P0：以前只看 status+正文。日程删块/改块后旧 id 的行永远是
        valid，补写调度以为「这块写过了」，于是 11-18 攒下 25 条孤儿日记。
        现在依次校验：
        - 记录存在、status=valid、正文非空；
        - version 给了时，日记不能写于更晚的日程版本（回滚/时间倒流不算缓存）；
        - 当天日程表读得到时，块身份必须还在，且记录的 start/end 与当前块一致；
          读不到日程（老库/纯规则假件）就退回旧口径，不误杀。
        """
        record = self.get_block_diary_record(day, block_id)
        if not record:
            return False
        if record["status"] != "valid" or not str(record.get("text") or "").strip():
            return False
        if version is not None:
            try:
                if int(record.get("version") or 0) > int(version or 0):
                    return False
            except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
                pass
        items = self.get_schedule(day)
        if items is None:  # 没有 schedule 表/行：无法核对块存在性，保持旧行为
            return True
        try:
            from .amo_state_common import block_id as _bid
        except ImportError:  # 直接按文件加载时
            from amo_state_common import block_id as _bid
        wanted = str(block_id or "")
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                same = str(_bid(day, item)) == wanted
            except Exception:  # cleanup: 单条坏块不影响核对
                continue
            if not same:
                continue
            if str(record.get("start") or "") and str(item.get("start") or "") \
                    and str(record["start"]) != str(item["start"]):
                return False
            if str(record.get("end") or "") and str(item.get("end") or "") \
                    and str(record["end"]) != str(item["end"]):
                return False
            return True
        return False

    def sync_block_diaries(self, day, current_items, stale_ids=(), version=None):
        """按当前日程精确失效块日记：指定 event_id 失效 + 孤儿行清成 stale。

        日程落库点唯一调用（commit_schedule_change）。「孤儿」= 块日记的
        block_id 已经不在当前日程里（块被删/被换身份/旧内容哈希）。只标 stale、
        不删正文（生成失败还能兜底，人工也能审计），frozen/archived 不动。
        返回 {"stale": 标了几条, "orphans": [...], "invalidated": [...]}。
        """
        day = str(day or "")
        ids = self._current_block_ids(day, current_items)
        exact = {str(x) for x in (stale_ids or ()) if str(x or "").strip()}
        if not ids and not exact:
            return {"stale": 0, "orphans": [], "invalidated": []}
        orphans = []
        with self.lock:
            try:
                rows = self.conn.execute(
                    "SELECT block_id, status FROM block_diary WHERE day=?",
                    (day,),
                ).fetchall()
            except sqlite3.Error:
                return {"stale": 0, "orphans": [], "invalidated": []}
            for row in rows:
                bid = str(row["block_id"] or "")
                if not bid or bid in ids:
                    continue
                if str(row["status"] or "valid") in ("stale", "frozen", "archived"):
                    continue
                orphans.append(bid)
            targets = set(orphans) | exact
            marked = 0
            for bid in sorted(targets):
                cur = self.conn.execute(
                    "UPDATE block_diary SET status='stale', invalidated_at=? "
                    "WHERE day=? AND block_id=? "
                    "AND status NOT IN ('stale', 'frozen', 'archived')",
                    (now_ts(), day, bid),
                )
                marked += int(cur.rowcount or 0)
            self.conn.commit()
        return {"stale": marked, "orphans": sorted(orphans),
                "invalidated": sorted(bid for bid in exact if bid in ids)}

    def mark_block_diaries_stale(self, day, from_start, version=None):
        """把某天 from_start 及以后开始的块日记标 stale（不删正文，供失败兜底）。"""
        try:
            start_hour = int(str(from_start)[:2])
        except (TypeError, ValueError):
            start_hour = 0
        with self.lock:
            cur = self.conn.execute(
                "UPDATE block_diary SET status='stale', invalidated_at=? "
                "WHERE day=? AND CAST(substr(start, 1, 2) AS INTEGER)>=? "
                "AND status NOT IN ('stale', 'frozen', 'archived')",
                (now_ts(), str(day), start_hour),
            )
            self.conn.commit()
            return int(cur.rowcount or 0)

    def mark_block_diary_stale(self, day, block_id):
        """把某一块的小日记标 stale（日记质检用，不删正文）。

        和 mark_block_diaries_stale 的区别：这里按块身份精确标记，不会把同一小时
        之后的块一起打掉；frozen / archived 是人工冻结的内容，不动。
        """
        with self.lock:
            cur = self.conn.execute(
                "UPDATE block_diary SET status='stale', invalidated_at=? "
                "WHERE day=? AND block_id=? AND status NOT IN ('stale', 'frozen', 'archived')",
                (now_ts(), str(day), str(block_id)),
            )
            self.conn.commit()
            return int(cur.rowcount or 0)

    def list_block_diaries(self, day):
        """某天全部块日记（按开始时间排序），迁移/排查用。"""
        fields = "day, block_id, start, end, place, text, version, status, source"
        with self.lock:
            try:
                rows = self.conn.execute(
                    "SELECT %s, event_id FROM block_diary WHERE day=? "
                    "ORDER BY start ASC" % fields, (str(day),)).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.OperationalError:
                rows = self.conn.execute(
                    "SELECT %s FROM block_diary WHERE day=? ORDER BY start ASC"
                    % fields, (str(day),)).fetchall()
                out = []
                for row in rows:
                    data = dict(row)
                    data["event_id"] = data.get("block_id") or ""
                    out.append(data)
                return out

    def block_diary_identity_mismatches(self, day=""):
        """两列不相等的行（健康检查；迁移/双写正常时永远为空）。"""
        sql = ("SELECT day, block_id, event_id FROM block_diary "
               "WHERE event_id IS NULL OR event_id<>block_id")
        params: tuple[str, ...] = ()
        if day:
            sql += " AND day=?"
            params = (str(day),)
        with self.lock:
            try:
                rows = self.conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def migrate_block_diary(self):
        """启动时幂等迁移：旧 schedule_diary 行 → 当天日程块。

        旧表按整点落库，新表按块身份落库。旧行的正文只有在**块窗口完全落在旧行
        覆盖的那一小时里**才可信（block.start == HH:00 且 block.end <= HH+1:00）：
        - 跨多小时块（16:56-18:26 对应旧 17:00 行）只覆盖 17:00-18:00，18:10 的
          「25 块外卖」不在旧文里，直接当缓存就会「聊天 25 块、日记 15 块」；
        - 起点偏移块（16:07-16:56 对应旧 16:00 行）窗口也不相等。
        这两种都写 stale（或 legacy stale），详情命中按块窗口重新生成。
        映射规则：
        - 一个 hour 只对应一个块 → 写该块的 block_diary 行；
        - 多个候选 / 非整点旧行 → 写 `legacy:<hour>` 只读行；
        - 没有任何原始块 → 跳过（整点行）/ 留 legacy（非整点行）。
        INSERT OR IGNORE 保证不覆盖新生成的正文；规则升版时用 block_diary_migrate_version
        触发一次性重判，只改迁移副本（source='migration' 或正文与旧行逐字相同）。
        """
        try:
            from .amo_state_common import block_id as _block_id
        except ImportError:
            from amo_state_common import block_id as _block_id
        try:
            stamp = int(self.kv_get("block_diary_migrate_version", 0) or 0)
        except (TypeError, ValueError):
            stamp = 0
        with self.lock:
            try:
                rows = self.conn.execute(
                    "SELECT day, hour, text, version, status FROM schedule_diary"
                ).fetchall()
            except sqlite3.Error:
                return 0
            plan = self._block_diary_migration_plan(rows, _block_id)
            migrated = self._insert_block_diary_plan(plan)
            rejudged = 0
            if stamp < BLOCK_DIARY_MIGRATE_VERSION:
                rejudged = self._rejudge_migrated_block_diaries(plan)
                self.kv_set("block_diary_migrate_version", BLOCK_DIARY_MIGRATE_VERSION)
            self.conn.commit()
            if migrated or rejudged:
                logger.info("[amo] 小日记迁移：旧整点行 → 块日记 %d 条，重判 stale %d 条",
                            migrated, rejudged)
            return migrated

    def _block_diary_migration_plan(self, rows, block_id_fn):
        """旧 schedule_diary 行 → 待写入的 block_diary 计划（纯计算，不写库）。"""
        plan = []
        schedule_cache: dict[str, Any] = {}
        for row in rows:
            entry = self._block_diary_migration_entry(row, schedule_cache, block_id_fn)
            if entry is not None:
                plan.append(entry)
        return plan

    def _block_diary_migration_entry(self, row, schedule_cache, block_id_fn):
        """单条旧行 → 一条迁移计划；整点行认不到块时返回 None（不落库）。"""
        day = str(row["day"] or "")
        hour = str(row["hour"] or "")
        text = str(row["text"] or "").strip()
        if not day or not text:
            return None
        hour_key = hour[:5]
        point = self._clock_minutes(hour_key)
        if point is None:
            return None
        on_hour = (point % 60 == 0)
        if day not in schedule_cache:
            schedule_cache[day] = self.get_schedule(day) or []
        candidates = self._block_diary_candidates(schedule_cache[day] or [], point, on_hour)
        trusted = self._block_diary_trusted(candidates, point, on_hour)
        row_status = (str(row["status"] or "valid") if trusted else "stale")
        if len(candidates) == 1:
            item = candidates[0]
            block_key = block_id_fn(day, item)
            start_text = str(item.get("start") or "")[:5]
            end_text = str(item.get("end") or "")[:5]
            place_text = str(item.get("place") or "")
        elif len(candidates) > 1:
            block_key = "legacy:" + hour_key
            start_text = hour_key
            end_text = ""
            place_text = ""
        elif on_hour:
            return None
        else:
            # 认不到块也要留档：只读 legacy 行，不覆盖任何新数据。
            block_key = "legacy:" + hour_key
            start_text = hour_key
            end_text = ""
            place_text = ""
        return {"day": day, "key": block_key, "start": start_text,
                "end": end_text, "place": place_text, "text": text,
                "version": int(row["version"] or 0), "status": row_status}

    def _block_diary_candidates(self, items, point, on_hour):
        """和这一小时/这一刻重叠的日程块。"""
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_begin = self._clock_minutes(item.get("start"))
            if item_begin is None:
                continue
            item_finish = self._clock_minutes(item.get("end"))
            if item_finish is None or item_finish <= item_begin:
                item_finish = item_begin + 60
            if on_hour:
                # 整点旧行 = 那一小时，和它重叠的块都算候选。
                overlap = item_begin < point + 60 and item_finish > point
            else:
                # 非整点旧行（如 16:56）= 那一刻所在的块，按包含关系认。
                overlap = item_begin <= point < item_finish
            if overlap:
                candidates.append(item)
        return candidates

    def _block_diary_trusted(self, candidates, point, on_hour):
        """只有「唯一整点块且整块都在这一小时内」旧文才可信。"""
        if not (len(candidates) == 1 and on_hour):
            return False
        only = candidates[0]
        only_begin = self._clock_minutes(only.get("start"))
        only_finish = self._clock_minutes(only.get("end"))
        if only_finish is None or only_finish <= (only_begin or 0):
            only_finish = (only_begin or 0) + 60
        # 必须「块起点正好是那一小时、且整块不超出这一小时」：
        # 16:07-16:56 这种起点偏移块虽然被 16 点窗口包含，但旧 16:00
        # 行描述的是别的东西（整点/另一段），不能当缓存复用。
        return (only_begin is not None and only_begin == point
                and only_finish <= point + 60)

    def _block_diary_has_event_id(self):
        """block_diary 是否有 event_id 列（v4 迁移前为 False）。"""
        try:
            cols = {row[1] for row in self.conn.execute(
                "PRAGMA table_info(block_diary)")}
        except sqlite3.Error:
            return False
        return "event_id" in cols

    def _insert_block_diary_plan(self, plan):
        """把迁移计划写进 block_diary；INSERT OR IGNORE 不覆盖新生成的正文。"""
        migrated = 0
        has_event_id = self._block_diary_has_event_id()
        for entry in plan:
            if has_event_id:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO block_diary(day, block_id, event_id, start, "
                    "end, place, text, updated, version, status, invalidated_at, source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,0,'migration')",
                    (entry["day"], entry["key"], entry["key"], entry["start"],
                     entry["end"], entry["place"], entry["text"], now_ts(),
                     entry["version"], entry["status"]),
                )
            else:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO block_diary(day, block_id, start, end, place, "
                    "text, updated, version, status, invalidated_at, source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,'migration')",
                    (entry["day"], entry["key"], entry["start"], entry["end"],
                     entry["place"], entry["text"], now_ts(), entry["version"],
                     entry["status"]),
                )
            migrated += int(cur.rowcount or 0)
        return migrated

    def _rejudge_migrated_block_diaries(self, plan):
        """把“迁移副本”里时间口径对不上的行一次性标 stale。

        只认迁移来源：source='migration'，或旧库还没有 source 列时正文与旧行逐字相同。
        之后真正生成/重写过的 valid 行（source 为空且正文不同）绝不动。
        """
        changed = 0
        for entry in plan:
            if entry["status"] != "stale":
                continue
            row = self.conn.execute(
                "SELECT status, text, source FROM block_diary WHERE day=? AND block_id=?",
                (entry["day"], entry["key"]),
            ).fetchone()
            if not row:
                continue
            if str(row["status"] or "valid") in ("stale", "frozen", "archived"):
                continue
            source = str(row["source"] or "")
            same_text = str(row["text"] or "").strip() == entry["text"]
            if source != "migration" and not (not source and same_text):
                continue
            self.conn.execute(
                "UPDATE block_diary SET status='stale', invalidated_at=? "
                "WHERE day=? AND block_id=?",
                (now_ts(), entry["day"], entry["key"]),
            )
            changed += 1
        return changed

    @staticmethod
    def _clock_minutes(value):
        """HH:MM → 分钟；解析不了返回 None（迁移/窗口判断用）。"""
        text = str(value or "").strip()[:5]
        try:
            hour_text, minute_text = text.split(":")[:2]
            return int(hour_text) * 60 + int(minute_text)
        except (TypeError, ValueError):
            return None

    # ---------- 统计 ----------
    def counts(self):
        with self.lock:
            profiles = self.conn.execute("SELECT COUNT(*) AS c FROM profiles").fetchone()["c"]
            memories = 0
            messages = self.conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {"profiles": profiles, "memories": memories, "messages": messages}
