# -*- coding: utf-8 -*-
"""Phase 11：时间/数字/金额/地点/幂等构造器的等价性测试。

每个 A/B 用例左边是新 owner（amo_numbers / amo_place / amo_idem），右边是**逐字
拷贝自重构前源码**的冻结实现；输入矩阵覆盖空值、坏值、边界（24:00 / 1440 /
None / 负数 / 一位小时 / 带秒）。要求逐字节一致，不允许"差不多"。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_idem  # noqa: E402
import amo_numbers as N  # noqa: E402
import amo_place  # noqa: E402
import amo_shared  # noqa: E402


# ---------------- 冻结的旧实现（重构前源码逐字拷贝） ----------------
def old_clamp(value, low=0.0, high=100.0):
    try:
        value = float(value)
    except Exception:
        return float(low)
    return max(float(low), min(float(high), value))


def old_to_int(value, default=0):
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def old_to_float(value, default=0.0):
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def old_parse_hhmm(value, default=0):
    try:
        parts = str(value).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return default


_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})")


def old_minutes_of(value):
    matched = _CLOCK_RE.match(str(value or "").strip())
    if not matched:
        return None
    hour = int(matched.group(1))
    minute = int(matched.group(2))
    if hour < 0 or hour > 24 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def old_clock_of(total):
    value = max(0, min(1440, int(total)))
    if value >= 1440:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def old_minutes_fact(value):
    text = str(value or "").strip()[:5]
    try:
        hh, mm = [int(x) for x in text.split(":")]
    except (TypeError, ValueError):
        return None
    if not (0 <= hh <= 24 and 0 <= mm < 60):
        return None
    return hh * 60 + mm


def old_clock_fact(total):
    if total is None:
        return ""
    value = max(0, min(1440, int(total)))
    if value >= 1440:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def old_hhmm(value):
    text = str(value or "")[:5]
    try:
        hh, mm = text.split(":")
        return "%02d:%02d" % (int(hh), int(mm))
    except Exception:
        return ""


def old_minutes_plain(value):
    text = old_hhmm(value)
    if not text:
        return None
    hh, mm = text.split(":")
    return int(hh) * 60 + int(mm)


def old_minutes_loose(text):
    try:
        hour, minute = str(text).split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def old_clock_ts(ts):
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except Exception:
        return ""


def old_day_text(day):
    if isinstance(day, _dt.datetime):
        return day.date().isoformat()
    if isinstance(day, _dt.date):
        return day.isoformat()
    return str(day or "")[:10]


def old_clock_after(now, minutes=0.0):
    return (now + _dt.timedelta(minutes=float(minutes))).strftime("%H:%M")


def old_yuan(cents):
    try:
        value = int(cents or 0) / 100.0
    except (TypeError, ValueError):
        value = 0.0
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    return text or "0"


def old_normalize_place(value, zone=""):
    text = str(value or "").strip()
    if text in ("家", "卧室", "客厅", "公司", "楼下小馆", "地铁", "商场"):
        return text
    for alias, canonical in (("他桌面上", "家"), ("桌面", "家"), ("电脑里", "家"), ("家里", "家"),
                             ("家中", "家"), ("出租屋", "家"), ("小窝", "家"), ("房间", "家")):
        if alias in text:
            return canonical
    if text in ("路上", "医院", "酒店房间", "景区", "餐厅", "车站", "学校"):
        return text
    zone = str(zone or "")
    if zone == "office":
        return "公司"
    if zone == "mall":
        return "商场"
    if zone == "restaurant":
        return "楼下小馆"
    if zone == "road":
        if "地铁" in text or "公交" in text:
            return "地铁"
        return text or "路上"
    if zone in ("health", "hospital", "hotel", "scenic", "station", "school"):
        return text or "家"
    return "家"


def old_clean_place(place, limit=16):
    text = str(place or "").strip()
    if not text:
        return ""
    if "桌" in text or text.startswith("他") or text.startswith("她"):
        return "家里"
    return text[:limit]


CLOCKS: list[object] = [None, "", "0", "00:00", "9:00", "09:00", "9:5", "09:05", "13:41", "13:41:00",
          "24:00", "23:59", "25:00", "12:60", " 13:41 ", "abc", "13", "13:4:5", 0, 540,
          -1, 1440, 1500, 1.9, "1.9", [], {}, True, False]
NUMS: list[object] = [None, "", 0, "0", "1", 1, 1.5, -3, "abc", "1e3", [], {}, True, False, "12.7"]
MOMS = [None, "", "abc", 0, 1763300000.0, -1, "1763300000"]
YUANS = [None, "", 0, 1, 99, 100, 2580, 258000, -100, "abc", 1.005, 1000000, 250005]
PLACES = [None, "", "家", "卧室", "他桌面上", "电脑里", "家里", "出租屋", "房间",
          "路上", "医院", "酒店房间", "景区", "餐厅", "车站", "学校", "地铁",
          "在公交上", "东莞的写字楼", "客厅的沙发"]
ZONES = ["", "office", "mall", "restaurant", "road", "health", "hotel", "scenic",
         "station", "school", "unknown"]


def _pairs(new_fn, old_fn, inputs):
    for value in inputs:
        try:
            expected = ("ok", old_fn(value))
        except Exception as exc:
            expected = ("exc", type(exc).__name__)
        try:
            actual = ("ok", new_fn(value))
        except Exception as exc:
            actual = ("exc", type(exc).__name__)
        if expected != actual:
            raise AssertionError("%r: old=%r new=%r" % (value, expected, actual))


class NumbersABTests(unittest.TestCase):
    def test_clamp_matches_legacy(self):
        _pairs(N.clamp, old_clamp, NUMS)

    def test_to_int_matches_legacy(self):
        _pairs(N.to_int, old_to_int, NUMS)

    def test_to_float_matches_legacy(self):
        _pairs(N.to_float, old_to_float, NUMS)

    def test_parse_hhmm_matches_legacy(self):
        _pairs(N.parse_hhmm, old_parse_hhmm, CLOCKS)

    def test_minutes_of_matches_legacy(self):
        _pairs(N.minutes_of, old_minutes_of, CLOCKS)

    def test_clock_of_matches_legacy(self):
        _pairs(N.clock_of, old_clock_of, CLOCKS)

    def test_fact_variants_match_legacy(self):
        _pairs(N.minutes_of_fact, old_minutes_fact, CLOCKS)
        _pairs(N.clock_of_fact, old_clock_fact, CLOCKS)

    def test_loose_variants_match_legacy(self):
        _pairs(N.hhmm_text, old_hhmm, CLOCKS)
        _pairs(N.minutes_of_plain, old_minutes_plain, CLOCKS)
        _pairs(N.minutes_of_loose, old_minutes_loose, CLOCKS)

    def test_timestamp_variants_match_legacy(self):
        _pairs(N.clock_of_ts, old_clock_ts, MOMS)
        _pairs(N.clock_of_ts_guarded, old_clock_ts, MOMS)

    def test_day_text_matches_legacy(self):
        _pairs(N.day_text, old_day_text, [None, "", "2026-11-16",
                                          _dt.date(2026, 11, 16),
                                          _dt.datetime(2026, 11, 16, 8, 2)])

    def test_clock_after_matches_legacy(self):
        for now in (_dt.datetime(2026, 11, 16, 8, 2), _dt.datetime(2026, 11, 16, 23, 59)):
            for minutes in (0, 15, -30, 1.5, 60):
                self.assertEqual(N.clock_after(now, minutes), old_clock_after(now, minutes))

    def test_md5_int_stable(self):
        self.assertEqual(N.md5_int("chatclaim:1:2"), amo_shared.md5_int("chatclaim:1:2"))
        self.assertEqual(N.md5_int(""), 0xD41D8CD9)

    def test_legacy_note_scope_preserved(self):
        import amo_observability
        amo_observability.reset()
        self.assertEqual(N.to_int("abc", 9), 9)
        self.assertIn("amo_shared.to_int", amo_observability.snapshot())


class MoneyABTests(unittest.TestCase):
    def test_yuan_matches_legacy_bytes(self):
        _pairs(N.yuan, old_yuan, YUANS)
        self.assertEqual(len(YUANS) >= 10, True)

    def test_yuan_display_examples(self):
        self.assertEqual(N.yuan(258000), "2580")
        self.assertEqual(N.yuan(2580), "25.8")
        self.assertEqual(N.yuan(0), "0")


class PlaceABTests(unittest.TestCase):
    def test_normalize_place_matches_legacy(self):
        for place in PLACES:
            for zone in ZONES:
                self.assertEqual(amo_place.normalize_place(place, zone),
                                 old_normalize_place(place, zone),
                                 "place=%r zone=%r" % (place, zone))

    def test_clean_place_matches_legacy(self):
        _pairs(amo_place.clean_place, old_clean_place, PLACES)

    def test_zone_for_place_matches_old_photo_rules(self):
        import photo_rules
        for place in PLACES:
            self.assertEqual(amo_place.zone_for_place(place), photo_rules.zone_for_place(place))

    def test_place_text_is_unchanged_for_user_visible_samples(self):
        samples = [("他桌面上", ""), ("电脑里", ""), ("在公交上", "road"), ("东莞的写字楼", "") ]
        for place, zone in samples:
            self.assertEqual(amo_place.normalize_place(place, zone),
                             old_normalize_place(place, zone))


class IdemTests(unittest.TestCase):
    def test_key_join(self):
        self.assertEqual(amo_idem.key("jit", "2026-11-16", "08"), "jit:2026-11-16:08")
        self.assertEqual(amo_idem.key("chatclaim", 1, 2), "chatclaim:1:2")

    def test_historical_keys_are_recognised(self):
        history = [
            "diary_audit_done:2026-11-16", "diary_audit:2026-11-16",
            "chatclaim:10001:1763300000", "activity:2026-11-16:13:41",
            "jit:2026-11-16:08", "trip:abc:meal", "trip:rollback:trip:abc:meal",
            "mood_applied:chatclaim:1:2", "online:g-1", "gift:evt-1",
            "micro:2026-11-16:coffee", "photo_roll:2026-11-16:kind:-",
            "proactive_slot:10001:2026-11-16:15", "todo:2026-11-16:09:00",
            "diary_task_failed:2026-11-16:09:00", "group_tease:10001:work_group",
            "home:t1:b1:s1",
        ]
        for item in history:
            self.assertTrue(amo_idem.is_valid(item), item)
            self.assertEqual(amo_idem.domain_of(item), item.split(":")[0])

    def test_rejects_malformed_keys(self):
        for bad in [None, 123, "", "no-separator", "has space:1", "nul\x00:1", "x" * 300 + ":1"]:
            self.assertFalse(amo_idem.is_valid(bad), repr(bad))

    def test_is_known(self):
        self.assertTrue(amo_idem.is_known("jit:2026-11-16:08"))
        self.assertFalse(amo_idem.is_known("random:1"))

    def test_key_matches_legacy_formatting(self):
        cases = [("jit", ("2026-11-16", "%02d" % 8)), ("activity", ("2026-11-16", "13:41")),
                 ("trip", ("abc", "meal")), ("micro", ("2026-11-16", "coffee"))]
        for domain, parts in cases:
            legacy = (domain + ":" + ":".join(str(x) for x in parts))
            self.assertEqual(amo_idem.key(domain, *parts), legacy)


class ReexportTests(unittest.TestCase):
    def test_shared_reexports_new_owners(self):
        self.assertIs(amo_shared.clamp, N.clamp)
        self.assertIs(amo_shared.to_int, N.to_int)
        self.assertIs(amo_shared.to_float, N.to_float)
        self.assertIs(amo_shared.parse_hhmm, N.parse_hhmm)
        self.assertIs(amo_shared.md5_int, N.md5_int)

    def test_original_modules_reexport(self):
        import amo_fact_timeline
        import amo_state_common
        import amo_timeline
        import photo_rules
        self.assertIs(amo_timeline.minutes_of, N.minutes_of)
        self.assertIs(amo_timeline.clock_of, N.clock_of)
        self.assertIs(amo_fact_timeline.minutes_of, N.minutes_of_fact)
        self.assertIs(amo_state_common.normalize_place, amo_place.normalize_place)
        self.assertIs(photo_rules.clean_place, amo_place.clean_place)


if __name__ == "__main__":
    unittest.main()
