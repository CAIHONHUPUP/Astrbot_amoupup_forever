from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_city_index import (  # noqa: E402
    city_location,
    is_coordinates,
    resolve_city,
    search_city,
)


class ResolveCityTests(unittest.TestCase):
    def test_dongguan_variants(self) -> None:
        for text in ("东莞", "东莞市", "广东东莞", "广东省东莞市", "东莞南城", " 东莞 "):
            hit = resolve_city(text)
            self.assertIsNotNone(hit, text)
            self.assertEqual(hit["id"], "101281601", text)
            self.assertEqual(hit["name"], "东莞", text)

    def test_municipality_and_district(self) -> None:
        self.assertEqual(resolve_city("北京")["id"], "101010100")
        # 写「北京朝阳」「朝阳区」都落到北京朝阳区
        self.assertEqual(resolve_city("北京朝阳")["id"], "101010300")
        self.assertEqual(resolve_city("朝阳区")["id"], "101010300")
        # 只写「朝阳」按地级市朝阳市算，不自动猜北京
        chao = resolve_city("朝阳")
        self.assertEqual(chao["city"], "朝阳市")

    def test_city_and_district(self) -> None:
        nanshan = resolve_city("深圳南山")
        self.assertEqual(nanshan["id"], "101280604")
        self.assertEqual(nanshan["city"], "深圳市")

    def test_ambiguous_short_name_is_flagged(self) -> None:
        hit = resolve_city("鼓楼区")  # 开封/南京/徐州/福州 四个
        self.assertEqual(hit["ambiguous"], 4)
        self.assertTrue(any("南京" in c for c in hit["candidates"]))
        self.assertIn("南京鼓楼", hit["note"])

    def test_ambiguous_district_has_candidates(self) -> None:
        hit = resolve_city("南山区")  # 鹤岗 / 深圳
        self.assertEqual(hit["ambiguous"], 2)
        self.assertIn("广东省·深圳市", hit["candidates"])

    def test_disambiguated_has_no_warning(self) -> None:
        self.assertFalse(resolve_city("深圳南山").get("ambiguous"))
        self.assertFalse(resolve_city("东莞").get("ambiguous"))

    def test_numeric_id_passthrough(self) -> None:
        hit = resolve_city("101281601")
        self.assertEqual(hit["id"], "101281601")
        self.assertTrue(hit["ok"])

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(resolve_city("不存在的城市xyz"))
        self.assertIsNone(resolve_city(""))
        self.assertIsNone(resolve_city(None))


class SearchCityTests(unittest.TestCase):
    def test_search_hit(self) -> None:
        rows = search_city("东莞", 5)
        self.assertTrue(rows)
        self.assertTrue(any(row["id"] == "101281601" for row in rows))

    def test_ambiguous_name_lists_city_level_first(self) -> None:
        rows = search_city("朝阳", 3)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["city"], "朝阳市")  # 地级市排在区县前面

    def test_limit(self) -> None:
        rows = search_city("朝阳", 2)
        self.assertLessEqual(len(rows), 2)

    def test_empty_query(self) -> None:
        self.assertEqual(search_city("", 5), [])


class WeatherMethodScopeTests(unittest.TestCase):
    """取天气的两个方法在 HumanState 上，不能调插件的私有方法。

    之前踩过：HumanState._fetch_qweather 里写了 self._city_lookup(...)，
    只有插件类有这个方法 → 每次刷新都 AttributeError，天气退回模拟。
    """

    def setUp(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "amo_human_state.py"), encoding="utf-8") as fh:
            self.state_source = fh.read()

    def test_state_does_not_call_plugin_only_resolvers(self) -> None:
        self.assertNotIn("self._city_lookup(", self.state_source)
        self.assertNotIn("self._weather_location(", self.state_source)
        self.assertNotIn("self._city_module(", self.state_source)

    def test_state_has_its_own_location_helper(self) -> None:
        self.assertIn("def _location_of(self, city):", self.state_source)
        self.assertEqual(self.state_source.count("self._location_of(city)"), 2)


class CityLocationTests(unittest.TestCase):
    """取天气前的入口：中文换 ID，数字原样，解析不了把原文交回去。"""

    def test_chinese_name_resolves(self) -> None:
        self.assertEqual(city_location("东莞"), "101281601")
        self.assertEqual(city_location(" 东莞市 "), "101281601")
        self.assertEqual(city_location("北京朝阳"), "101010300")
        self.assertEqual(city_location("深圳南山"), "101280604")

    def test_numeric_id_passthrough(self) -> None:
        self.assertEqual(city_location("101281601"), "101281601")
        self.assertEqual(city_location(101281601), "101281601")

    def test_empty_is_empty(self) -> None:
        self.assertEqual(city_location(""), "")
        self.assertEqual(city_location("   "), "")
        self.assertEqual(city_location(None), "")

    def test_unknown_keeps_original(self) -> None:
        # 交给上层走 GeoAPI 兜底或退回模拟天气，不在这里吞掉
        self.assertEqual(city_location("不存在的城市xyz"), "不存在的城市xyz")

    def test_coordinates_passthrough(self) -> None:
        # 和风 v7 直接支持「经度,纬度」，不要去查表也不要带空格
        self.assertTrue(is_coordinates("113.75,23.02"))
        self.assertTrue(is_coordinates("113.75, 23.02"))
        self.assertFalse(is_coordinates("东莞"))
        self.assertEqual(city_location("113.75,23.02"), "113.75,23.02")
        self.assertEqual(city_location(" 113.75 , 23.02 "), "113.75,23.02")


class CityPanelTests(unittest.TestCase):
    """面板那行要能显示中文城市 + 天气/温度/湿度。"""

    def setUp(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "views", "dashboard", "index.html")
        with open(path, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_row_calls_brief_api(self) -> None:
        self.assertIn('call("weather/brief")', self.html)

    def test_row_renders_temp_and_humidity(self) -> None:
        self.assertIn("function weatherBriefText(", self.html)
        self.assertIn("℃", self.html)
        self.assertIn("湿度 ", self.html)

    def test_simulate_is_labelled(self) -> None:
        self.assertIn('weather.source === "simulate"', self.html)

    def test_error_names_the_endpoint(self) -> None:
        # 面板 500 时要说清是哪个接口挂了，而不是只给一句 Internal server error
        self.assertIn('action + " 接口 " + response.status', self.html)
        self.assertIn("item.status !== 404", self.html)
        self.assertIn('action + " 接口返回错误："', self.html)




if __name__ == "__main__":
    unittest.main()
