# -*- coding: utf-8 -*-
"""审查一天（只读）：日程时间轴 + 块日记分级审查。
用法：python work/preview_day.py [--date 2026-11-16] [--html out.html] [--now 19:30]

HARD（退出码 1）：bad_start/bad_end/overlap/fragment/midnight/跨地点无路上/非 sleep 已结束块缺日记/
      约会结束后的块又写「他就在身边/一起回家/同居/他抱着我」（约会时段内允许面对面）。
WARN（不 FAIL）：家→外面缺出发段、stale 日记、sleep 块缺日记、地点词预演、相邻日记重复。
"""
from __future__ import annotations
import argparse, datetime, html, json, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tests'))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import amo_location_rules as loc  # noqa: E402
import amo_state_common as common  # noqa: E402
# 只读审查：库路径从插件目录推导（不写死个人路径），可用 AMO_DB 覆盖。
DB = os.environ.get('AMO_DB') or os.path.join(
    os.path.dirname(os.path.dirname(ROOT)), 'plugin_data', 'Astrbot_amoupup_forever', 'amo_forever.db')
HOME_WORDS = ('家', '卧室', '客厅', '出租屋')
ROAD_WORDS = ('路上', '地铁', '公交', '交通', '车站')
PREVIEW_WORDS = ('想着', '下午去', '打算', '准备去')
#: 约会结束后（B 方案：各回各家）日记里绝不能再出现的「他还在身边」写法
DATE_TOGETHER_WORDS = ('一起回家', '同居', '住在一起', '他就在身边', '他抱着我', '他还在我身边')


def m(t):
    try:
        h, mi = str(t)[:5].split(':'); return int(h) * 60 + int(mi)
    except Exception:
        return None


def _items_of(con, day):
    """日程 canonical 存储：schedule 表优先（面板/详情读的就是它），kv 只给老库兜底。"""
    try:
        row = con.execute("SELECT items FROM schedule WHERE day=?", (day,)).fetchone()
    except sqlite3.Error:
        row = None
    if row and row[0]:
        try:
            table_items = json.loads(row[0])
        except ValueError:
            table_items = None
        if isinstance(table_items, list) and table_items:
            return [dict(x) for x in table_items if isinstance(x, dict)]
    row = con.execute("SELECT value FROM kv WHERE key='schedule'").fetchone()
    data = json.loads(row[0]) if row else {}
    return [dict(x) for x in (data.get(day) or []) if isinstance(x, dict)]


def load(day):
    con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
    items = _items_of(con, day)
    rows = []
    for r in items:
        try:
            bid = common.block_id(day, r)
            rec = con.execute('SELECT text,status FROM block_diary WHERE day=? AND block_id=?', (day, bid)).fetchone()
        except Exception:
            bid, rec = '', None
        r['_bid'] = bid; r['_diary'] = rec[0] if rec else ''; r['_status'] = rec[1] if rec else ''
        rows.append(r)
    con.close()
    rows.sort(key=lambda r: m(r.get('start')) if m(r.get('start')) is not None else 9999)
    return rows


def _is_home(place):
    return any(w in place for w in HOME_WORDS)


def _is_out(place):
    return bool(place) and not _is_home(place) and not any(w in place for w in ROAD_WORDS)


def _ended(row, day, today, now_min):
    if day and today and day < today:
        return True
    if day and today and day > today:
        return False
    end = m(row.get('end'))
    return end is not None and end <= now_min


def _is_date_block(row):
    """约会时段内的块（允许面对面）：category=date，或 social 且活动写了老公/约会。"""
    category = str(row.get('category') or '')
    activity = str(row.get('activity') or '')
    if category == 'date':
        return True
    return category == 'social' and ('老公' in activity or '约会' in activity)


def _derived_transit_spans(rows, day=None):
    """A1：过渡只在读侧派生。返回派生视图里过渡块的 (start, end) 分钟区间。

    只用它判定「这对相邻外出块之间是不是已经有过渡」，不参与任何元数据/文本审查。
    """
    try:
        import schedule_engine_v2 as engine
        view, _defects = engine.safe_view(rows, day=str(day or ''))
    except Exception:
        return []
    spans = []
    for row in view or []:
        if not (row.get('derived') and row.get('transit_kind')):
            continue
        span = engine.span_of(row)
        if span:
            spans.append(span)
    return spans


def _has_derived_transit(spans, prev_end, next_start):
    """这对相邻块之间是否已有派生过渡（贴上一块结尾 / 对齐下一块起点 / 跨住空档）。"""
    if prev_end is None:
        return False
    for begin, finish in spans:
        if finish == prev_end or begin == prev_end:
            return True
        if next_start is not None and (begin == next_start or finish == next_start):
            return True
        if next_start is not None and begin <= prev_end and finish >= next_start:
            return True
    return False


def _date_end_min(rows):
    """当天约会结束的那一分钟（最后一个约会块的结束时间）。"""
    ends = [m(row.get('end')) for row in rows if _is_date_block(row)]
    ends = [value for value in ends if value is not None]
    return max(ends) if ends else None


def review(rows, day=None, now=None):
    hard, warn = [], []
    today = now.strftime('%Y-%m-%d') if now else ''
    now_min = now.hour * 60 + now.minute if now else 24 * 60
    for i, r in enumerate(rows):
        s, e = m(r.get('start')), m(r.get('end'))
        if s is None: hard.append(('bad_start', r))
        if e is None: hard.append(('bad_end', r))
        if s is not None and e is not None:
            if e <= s: hard.append(('bad_span', r))
            if 0 < e - s < 5: hard.append(('fragment', r))
            if e > 1440: hard.append(('midnight', r))
            if i + 1 < len(rows):
                ns = m(rows[i + 1].get('start'))
                if ns is not None and ns < e: hard.append(('overlap', r))
    derived = _derived_transit_spans(rows, day)
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        pa, pb = str(a.get('place') or ''), str(b.get('place') or '')
        if not pa or not pb: continue
        if any(w in pa for w in ROAD_WORDS) or any(w in pb for w in ROAD_WORDS): continue
        if _is_home(pa) and _is_out(pb):
            warn.append(('missing_outbound', a))
        elif _is_out(pa) and _is_out(pb) and pa != pb:
            # A1：库里不再存路上块，过渡由读侧派生——该空档已有派生过渡就不算瞬移。
            if _has_derived_transit(derived, m(a.get('end')), m(b.get('start'))):
                continue
            hard.append(('no_transit', a))
    for r in rows:
        text = str(r.get('_diary') or '')
        status = str(r.get('_status') or '')
        if not text:
            if not _ended(r, day, today, now_min):
                continue  # 未来块本来就没日记
            if str(r.get('category') or '') == 'sleep':
                warn.append(('sleep_no_diary', r))
            else:
                hard.append(('no_diary', r))
            continue
        if status == 'stale':
            warn.append(('stale_diary', r))  # 等调度按块重写，不算坏
        place = str(r.get('place') or '')
        try:
            bad = loc.check(text, place, str(r.get('zone') or ''))
        except Exception:
            bad = []
        if bad:
            if any(w in text for w in PREVIEW_WORDS):
                warn.append(('place_preview', r, bad))
            else:
                hard.append(('place_word', r, bad))
    # 约会结束之后：日记不许把老公写回她身边（B 方案：各回各家，只能手机）
    if any(_is_date_block(row) for row in rows):
        date_end = _date_end_min(rows)
        for r in rows:
            text = str(r.get('_diary') or '')
            if not text or _is_date_block(r):
                continue  # 约会时段内允许面对面
            begin = m(r.get('start'))
            if date_end is not None and begin is not None and begin < date_end:
                continue
            hits = [word for word in DATE_TOGETHER_WORDS if word in text]
            if hits:
                hard.append(('date_presence_after_end', r, hits))
    for i in range(len(rows) - 1):
        a = str(rows[i].get('_diary') or '')[:30]; b = str(rows[i + 1].get('_diary') or '')[:30]
        if a and a == b: warn.append(('dup_diary', rows[i]))
    return hard, warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.date.today().isoformat())
    ap.add_argument('--html', default='')
    ap.add_argument('--now', default='')
    args = ap.parse_args()
    if args.now:
        now = datetime.datetime.combine(datetime.date.fromisoformat(args.date),
                                        datetime.time.fromisoformat(args.now))
    else:
        now = datetime.datetime.now()
    rows = load(args.date); hard, warn = review(rows, args.date, now)
    print('DAY', args.date, 'blocks', len(rows))
    for r in rows:
        print('  %s-%s  %-18s %-6s %-10s %-6s diary=%s' % (
            r.get('start'), r.get('end'), str(r.get('activity'))[:18], r.get('place') or '-',
            r.get('source') or '-', r.get('_status') or '-',
            (str(r.get('_diary') or '')[:24] + ('…' if r.get('_diary') else '（缺）'))))
    for label, items in (('HARD', hard), ('WARN', warn)):
        for it in items:
            print('  [%s]%s' % (label, it[0]), it[1].get('start'), it[1].get('end'),
                  it[2] if len(it) > 2 else '')
    print('HARD %d / WARN %d' % (len(hard), len(warn)))
    print('RESULT', 'PASS' if not hard else 'FAIL %d' % len(hard))
    if args.html:
        lines = ['<meta charset="utf-8"><h2>%s 日程审查</h2><table border=1 cellpadding=4>' % args.date,
                 '<tr><th>时间</th><th>活动</th><th>地点</th><th>来源</th><th>日记</th></tr>']
        for r in rows:
            lines.append('<tr><td>%s-%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                r.get('start'), r.get('end'), html.escape(str(r.get('activity'))),
                html.escape(str(r.get('place') or '')), html.escape(str(r.get('source') or '')),
                html.escape(str(r.get('_diary') or '（缺）')[:120])))
        lines.append('</table>')
        open(args.html, 'w', encoding='utf-8').write('\n'.join(lines))
        print('HTML', args.html)
    return 0 if not hard else 1


if __name__ == '__main__':
    sys.exit(main())
