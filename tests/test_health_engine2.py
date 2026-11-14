# -*- coding: utf-8 -*-
import random
from services import health_engine as H

class State:
    def __init__(self, status, since=0, until=0):
        self.health = {"status": status, "since": since, "until": until}

class Plugin:
    def __init__(self, st): self.state = st

def test_status_interfaces():
    assert H.is_sick(Plugin(State("发烧"))) is True
    assert H.get_sick_status(Plugin(State("肠胃炎"))) == "肠胃炎"
    assert H.get_severity(Plugin(State("健康"))) == 0
    assert H.get_severity(Plugin(State("疲惫"))) == 1
    assert H.get_severity(Plugin(State("发烧"))) == 3
    assert H.is_sick(Plugin(State("疲惫"))) is False

def test_pure_functions_match_rules():
    rng = random.Random(7)
    assert H.resolve_hours(5, False, rng) == 5.0
    assert H.total_hours(5, True) == 5 and H.total_hours(5, False) == 10
    assert abs(H.sick_probability(0.02, "rain", 5, True, 70) - 0.11) < 1e-9
    assert H.pick_illness(rng) in ("轻微不适", "感冒", "低烧", "发烧", "肠胃炎", "疲惫")
    assert H.is_cured("感冒", 100, 200) is True and H.is_cured("健康", 0, 200) is False
