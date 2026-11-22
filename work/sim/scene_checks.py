# -*- coding: utf-8 -*-
"""scene_checks：用生产 DB/日志离线复算“她说的话 vs 当时日程/照片场景”。

只读：两个 DB 都以 sqlite mode=ro 打开，不写任何库；默认只打印摘要，
--json 输出机器可读结果，--out 才落盘（落盘路径由调用者指定）。

用法（在仓库根目录）：
  python work/sim/scene_checks.py \
      --forever-db D:/.../amo_forever.db \
      --messages-db D:/.../amo_data.db \
      --log D:/.../astrbot.log \
      --days 2026-11-16:2026-11-22
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_state_snapshot as Snap  # noqa: E402
import amo_scene_ledger as Ledger  # noqa: E402

TZ = datetime.timezone(datetime.timedelta(hours=8))

#: 词 → zone（照片单 zone / 说话地点归类共用；最长词优先）
_WORD_ZONE = {}
for _zone, _words in Ledger.ZONE_WORDS.items():
    for _word in _words:
        _WORD_ZONE.setdefault(_word, _zone)
for _word in Snap.EXTRA_PLACE_WORDS:
    if _word in _WORD_ZONE:
        continue
    for _zone, _words in Ledger.ZONE_WORDS.items():
        if _word in _words:
            _WORD_ZONE[_word] = _zone
            break
ALL_WORDS = sorted(_WORD_ZONE, key=len, reverse=True)


def _conn_ro(path):
    return sqlite3.connect("file:" + str(path).replace(os.sep, "/") + "?mode=ro", uri=True)


def _minutes(hhmm):
    try:
        hour, minute = str(hhmm or "")[:5].split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (ValueError, TypeError):
        return None


def _parse_ts(value):
    try:
        return datetime.datetime.fromtimestamp(float(value), TZ)
    except (TypeError, ValueError, OSError):
        return None


def _covering(items, minute):
    for item in items or ():
        if not isinstance(item, dict):
            continue
        start = _minutes(item.get("start"))
        end = _minutes(item.get("end"))
        if start is None:
            continue
        if end is None or end <= start:
            end = start + 60
        if start <= minute < end:
            return item
    return None


def _latest_before(items, minute):
    best = None
    best_start = -1
    for item in items or ():
        if not isinstance(item, dict):
            continue
        start = _minutes(item.get("start"))
        if start is not None and start <= minute and start > best_start:
            best, best_start = item, start
    return best


def _block_zone(item):
    return Ledger.normalize_zone((item or {}).get("zone"), (item or {}).get("place"))


def _claims(text):
    """文本里出现的地点词 → zone 集合（否定用法不算）。"""
    body = Snap.strip_negated(str(text or ""), ALL_WORDS)
    found = {}
    for word in ALL_WORDS:
        if word and word in body:
            found.setdefault(_WORD_ZONE.get(word, ""), []).append(word)
    return found


class Checker:
    def __init__(self, forever_db, messages_db=None, log_path=None):
        self.forever_db = forever_db
        self.messages_db = messages_db
        self.log_path = log_path
        self.schedule = {}
        self.issues = []
        self.counts = {"utterances": 0, "claims": 0, "conflicts": 0,
                       "unknown_claims": 0, "photos": 0, "photo_conflicts": 0}

    # ---------- 载入 ----------
    def load_schedule(self, days=None):
        con = _conn_ro(self.forever_db)
        try:
            for day, items in con.execute("SELECT day, items FROM schedule"):
                if days and day not in days:
                    continue
                try:
                    self.schedule[day] = json.loads(items or "[]")
                except ValueError:
                    self.schedule[day] = []
        finally:
            con.close()

    def load_utterances(self, days=None):
        rows = []
        if self.messages_db:
            con = _conn_ro(self.messages_db)
            try:
                try:
                    cur = con.execute("SELECT created_at, role, content FROM messages")
                except sqlite3.Error:
                    cur = []
                for created, role, content in cur:
                    moment = _parse_ts(created)
                    if moment is None or str(role) != "bot":
                        continue
                    day = moment.strftime("%Y-%m-%d")
                    if days and day not in days:
                        continue
                    rows.append((moment, str(content or ""), "messages"))
            finally:
                con.close()
        try:
            con = _conn_ro(self.forever_db)
            try:
                for created, content in con.execute(
                        "SELECT created, content FROM proactive_log"):
                    moment = _parse_ts(created)
                    if moment is None:
                        continue
                    day = moment.strftime("%Y-%m-%d")
                    if days and day not in days:
                        continue
                    rows.append((moment, str(content or ""), "proactive_log"))
            finally:
                con.close()
        except sqlite3.Error:  # cleanup: 兜底/降级，保持原行为
            pass
        rows.sort(key=lambda row: row[0])
        return rows

    # ---------- 检查 ----------
    def check_utterance(self, moment, text, source):
        day = moment.strftime("%Y-%m-%d")
        minute = moment.hour * 60 + moment.minute
        items = self.schedule.get(day) or []
        self.counts["utterances"] += 1
        cover = _covering(items, minute)
        if cover is None:
            latest = _latest_before(items, minute)
            if latest is None:
                return
            latest_end = _minutes(latest.get("end")) or (_minutes(latest.get("start")) + 60)
            if minute - latest_end > 30:
                claims = _claims(text)
                if claims:
                    self.counts["claims"] += len(claims)
                    self.counts["unknown_claims"] += 1
                    self.issues.append({
                        "kind": "unknown_place_claim", "day": day,
                        "time": moment.strftime("%H:%M:%S"), "source": source,
                        "text": text[:160],
                        "claimed_zones": sorted(zone for zone in claims if zone),
                        "reason": "距上一块结束 %d 分钟，地点未知" % (minute - latest_end),
                    })
                return
            cover = latest
        zone = _block_zone(cover)
        block = "%s-%s %s@%s" % (cover.get("start"), cover.get("end"),
                                 cover.get("activity"), cover.get("place"))
        claims = _claims(text)
        if not claims:
            return
        self.counts["claims"] += len(claims)
        banned = Snap.zone_blacklist(zone)
        hit_words = []
        for claim_zone, words in claims.items():
            for word in words:
                if word in banned:
                    hit_words.append(word)
        if hit_words:
            self.counts["conflicts"] += 1
            self.issues.append({
                "kind": "zone_conflict", "day": day,
                "time": moment.strftime("%H:%M:%S"), "source": source,
                "text": text[:160], "schedule": block, "schedule_zone": zone,
                "hit_words": hit_words,
            })

    def check_photos(self, days=None):
        if not self.log_path or not os.path.isfile(self.log_path):
            return
        pattern = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\].*照片已分享（[^）]*）：(.*)$")
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = pattern.match(line)
                if not match:
                    continue
                try:
                    moment = datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                moment = moment.replace(tzinfo=TZ)
                day = moment.strftime("%Y-%m-%d")
                if days and day not in days:
                    continue
                prompt = match.group(2)
                minute = moment.hour * 60 + moment.minute
                self.counts["photos"] += 1
                cover = _covering(self.schedule.get(day) or [], minute)
                if cover is None:
                    continue
                zone = _block_zone(cover)
                check = Ledger.verify_photo_prompt(prompt, {"zone": zone})
                if not check.get("ok"):
                    self.counts["photo_conflicts"] += 1
                    self.issues.append({
                        "kind": "photo_multi_zone", "day": day,
                        "time": moment.strftime("%H:%M:%S"),
                        "schedule": "%s-%s %s@%s" % (cover.get("start"), cover.get("end"),
                                                     cover.get("activity"), cover.get("place")),
                        "schedule_zone": zone, "prompt_zones": check.get("zones"),
                        "extra_zones": check.get("extra_zones"), "prompt": prompt[:200],
                    })

    # ---------- 汇总 ----------
    def run(self, days=None, kinds=None):
        self.load_schedule(days)
        for moment, text, source in self.load_utterances(days):
            self.check_utterance(moment, text, source)
        self.check_photos(days)
        if kinds:
            self.issues = [row for row in self.issues if row.get("kind") in kinds]
        return self

    def summary(self):
        return dict(self.counts, issues=len(self.issues),
                    by_kind=self._by_kind())

    def _by_kind(self):
        counts = {}
        for row in self.issues:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        return counts

    def report(self, limit=40):
        lines = ["# scene_checks 复算结果", ""]
        summary = self.summary()
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        for key, value in summary.items():
            if key == "by_kind":
                continue
            lines.append("| %s | %s |" % (key, value))
        lines.append("")
        if summary["by_kind"]:
            lines.append("按类型：" + "、".join("%s=%s" % item for item in summary["by_kind"].items()))
            lines.append("")
        lines.append("| day | time | kind | 她说的话/照片 | 当时日程 | 证据 |")
        lines.append("|---|---|---|---|---|---|")
        for row in self.issues[:limit]:
            detail = row.get("hit_words") or row.get("extra_zones") or row.get("claimed_zones")
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                row.get("day"), row.get("time"), row.get("kind"),
                str(row.get("text") or row.get("prompt") or "")[:80].replace("|", "/"),
                str(row.get("schedule") or "")[:60],
                detail,
            ))
        return "\n".join(lines)


def _parse_days(text):
    text = str(text or "").strip()
    if not text:
        return None
    if ":" in text:
        start, end = text.split(":", 1)
        try:
            first = datetime.date.fromisoformat(start)
            last = datetime.date.fromisoformat(end)
        except ValueError:
            return set()
        days = set()
        cursor = first
        while cursor <= last:
            days.add(cursor.isoformat())
            cursor += datetime.timedelta(days=1)
        return days
    return {part.strip() for part in text.split(",") if part.strip()}


def main(argv=None):
    parser = argparse.ArgumentParser(description="她说的话 vs 当时日程/照片场景（只读复算）")
    parser.add_argument("--forever-db", required=True, help="amo_forever.db（mode=ro 打开）")
    parser.add_argument("--messages-db", default="", help="amo_data.db（mode=ro 打开）")
    parser.add_argument("--log", default="", help="astrbot.log（照片 prompt）")
    parser.add_argument("--days", default="", help="2026-11-16:2026-11-22 或逗号分隔")
    parser.add_argument("--kind", default="", help="只看某类：zone_conflict,photo_multi_zone,unknown_place_claim")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--out", default="", help="写文件（默认只打印）")
    parser.add_argument("--fail-on-conflict", action="store_true", help="有冲突时退出码 1")
    args = parser.parse_args(argv)

    kinds = {part.strip() for part in args.kind.split(",") if part.strip()} or None
    checker = Checker(args.forever_db, args.messages_db, args.log)
    checker.run(_parse_days(args.days), kinds)
    payload = {"summary": checker.summary(), "issues": checker.issues}
    if args.json:
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    else:
        text = checker.report()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    if args.fail_on_conflict and (payload["summary"]["conflicts"] or payload["summary"]["photo_conflicts"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
