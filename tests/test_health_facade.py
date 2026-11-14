# -*- coding: utf-8 -*-
from services.health_engine import is_sick, get_sick_status, get_severity, get_sick_duration

class State:
    health = {"status": "发烧", "since": __import__("time").time() - 7200, "until": 0}

class Plugin:
    state = State()

def test_facade_reads_state():
    assert get_sick_status(Plugin()) == "发烧"
    assert is_sick(Plugin()) is True
    assert get_severity(Plugin()) == 3
    assert get_sick_duration(Plugin()) == 2

def test_facade_defaults():
    assert get_sick_status(None) == "健康" and is_sick(None) is False
