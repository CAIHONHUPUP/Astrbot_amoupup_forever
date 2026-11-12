from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weather_alert import forecast_brief, is_weather_query  # noqa: E402


TODAY = {"fxDate": "2026-11-08", "textDay": "晴", "textNight": "多云", "tempMax": "30", "tempMin": "22"}
TOMORROW_RAIN = {"fxDate": "2026-11-09", "textDay": "小雨转中雨", "textNight": "小雨", "tempMax": "18", "tempMin": "12"}
TOMORROW_SNOW = {"fxDate": "2026-11-09", "textDay": "小雪", "textNight": "阴", "tempMax": "2", "tempMin": "-4"}
TOMORROW_THUNDER = {"fxDate": "2026-11-09", "textDay": "雷阵雨", "textNight": "阵雨", "tempMax": "24", "tempMin": "20"}
TOMORROW_WARMER = {"fxDate": "2026-11-09", "textDay": "晴", "textNight": "晴", "tempMax": "36", "tempMin": "26"}
NOW = {"city": "东莞", "text": "多云", "temp": 27, "humidity": 62, "wind": "东风2级"}


class IsWeatherQueryTests(unittest.TestCase):
    def test_real_questions(self) -> None:
        for text in (
            "明天天气怎么样",
            "现在几度",
            "现在外面热不热",
            "我要不要带伞",
            "明天会下雨吗",
            "会下雪吗",
            "明天降温没",
            "今天穿什么",
            "外面风大不大",
        ):
            self.assertTrue(is_weather_query(text), text)

    def test_not_weather(self) -> None:
        for text in ("", None, "我饿了", "晚安", "帮我看看这个代码", "今天吃什么好呢"):
            self.assertFalse(is_weather_query(text), text)

    def test_too_long_is_not_query(self) -> None:
        self.assertFalse(is_weather_query("天气" + "哈" * 200))


class ForecastBriefTests(unittest.TestCase):
    def test_full_brief_has_now_today_tomorrow(self) -> None:
        text = forecast_brief(NOW, TODAY, TOMORROW_RAIN)
        self.assertIn("现在 东莞 多云 27℃", text)
        self.assertIn("今天 22~30℃", text)
        self.assertIn("明天 小雨转中雨 小雨 12~18℃", text)
        self.assertIn("明天有雨，记得带伞", text)
        self.assertIn("明天比今天最高温低 12℃", text)

    def test_snow_and_thunder(self) -> None:
        snow = forecast_brief(NOW, TODAY, TOMORROW_SNOW)
        self.assertIn("明天有雪，路滑", snow)
        thunder = forecast_brief(NOW, TODAY, TOMORROW_THUNDER)
        self.assertIn("明天有雷雨", thunder)

    def test_warmer_reads_as_higher(self) -> None:
        text = forecast_brief(NOW, TODAY, TOMORROW_WARMER)
        self.assertIn("明天比今天最高温高 6℃", text)

    def test_missing_pieces(self) -> None:
        only_now = forecast_brief(NOW, {}, {})
        self.assertIn("现在", only_now)
        self.assertNotIn("明天", only_now)
        only_tomorrow = forecast_brief({}, {}, TOMORROW_RAIN)
        self.assertIn("明天", only_tomorrow)
        self.assertNotIn("现在", only_tomorrow)
        self.assertEqual(forecast_brief({}, {}, {}), "")
        self.assertEqual(forecast_brief(None, None, None), "")

    def test_same_day_and_night_wording_is_not_repeated(self) -> None:
        same = {"textDay": "多云", "textNight": "多云", "tempMax": "24", "tempMin": "14"}
        text = forecast_brief(NOW, TODAY, same)
        self.assertIn("明天 多云 14~24℃", text)
        self.assertNotIn("多云 多云", text)

    def test_small_drop_is_silent(self) -> None:
        near = dict(TOMORROW_RAIN, tempMax="29")
        text = forecast_brief(NOW, TODAY, near)
        self.assertNotIn("比今天最高温", text)


if __name__ == "__main__":
    unittest.main()
