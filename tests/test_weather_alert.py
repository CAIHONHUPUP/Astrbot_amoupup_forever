from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weather_alert import evaluate, parse_daily  # noqa: E402


TODAY = {"fxDate": "2026-11-08", "textDay": "晴", "textNight": "晴", "tempMax": "30", "tempMin": "22"}


class ParseDailyTests(unittest.TestCase):
    def test_parses_and_sorts(self) -> None:
        payload = {
            "code": "200",
            "daily": [
                {"fxDate": "2026-11-10", "textDay": "小雨", "textNight": "阴", "tempMax": "21", "tempMin": "16"},
                {"fxDate": "2026-11-09", "textDay": "多云", "textNight": "晴", "tempMax": "26", "tempMin": "20"},
            ],
        }
        items = parse_daily(payload)
        self.assertEqual([x["fxDate"] for x in items], ["2026-11-09", "2026-11-10"])
        self.assertEqual(items[0]["textDay"], "多云")

    def test_bad_payload_returns_empty(self) -> None:
        self.assertEqual(parse_daily(None), [])
        self.assertEqual(parse_daily({"code": "401"}), [])
        self.assertEqual(parse_daily({"daily": "oops"}), [])

    def test_skips_rows_without_date(self) -> None:
        items = parse_daily({"daily": [{"textDay": "雨"}, {"fxDate": "2026-11-09", "textDay": "晴"}]})
        self.assertEqual(len(items), 1)


class EvaluateTests(unittest.TestCase):
    def _tomorrow(self, day="晴", night="晴", max_temp="29"):
        return {"fxDate": "2026-11-09", "textDay": day, "textNight": night, "tempMax": max_temp}

    def test_rain_reminds_umbrella(self) -> None:
        self.assertEqual(evaluate(TODAY, self._tomorrow(day="小雨")), "明天有雨，伞别忘了。")

    def test_rain_at_night_also_counts(self) -> None:
        self.assertIn("伞", evaluate(TODAY, self._tomorrow(night="阵雨")))

    def test_thunder_wins_over_rain(self) -> None:
        text = evaluate(TODAY, self._tomorrow(day="雷阵雨"))
        self.assertIn("雷雨", text)
        self.assertNotIn("伞", text)

    def test_snow(self) -> None:
        self.assertIn("有雪", evaluate(TODAY, self._tomorrow(day="小雪")))

    def test_temp_drop(self) -> None:
        self.assertEqual(evaluate(TODAY, self._tomorrow(max_temp="20"), 8), "明天降温 10 度，多穿件衣服。")

    def test_temp_drop_below_threshold(self) -> None:
        self.assertEqual(evaluate(TODAY, self._tomorrow(max_temp="26"), 8), "")

    def test_rain_plus_drop(self) -> None:
        text = evaluate(TODAY, self._tomorrow(day="中雨", max_temp="20"), 8)
        self.assertIn("伞", text)
        self.assertIn("降温", text)

    def test_good_weather_no_alert(self) -> None:
        self.assertEqual(evaluate(TODAY, self._tomorrow()), "")

    def test_missing_data_is_safe(self) -> None:
        self.assertEqual(evaluate(None, None), "")
        self.assertEqual(evaluate(TODAY, {"textDay": "晴", "tempMax": ""}), "")

    def test_custom_threshold(self) -> None:
        self.assertEqual(evaluate(TODAY, self._tomorrow(max_temp="24"), 5), "明天降温 6 度，多穿件衣服。")


if __name__ == "__main__":
    unittest.main()
