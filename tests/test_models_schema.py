# -*- coding: utf-8 -*-
from models.database import Database
from models.schema import get_hp, update_hp, get_sick, set_sick, sick_log

class FakeDB:
    def __init__(self): self.kv = {}
    def kv_get(self, k, d=None): return self.kv.get(k, d)
    def kv_set(self, k, v): self.kv[k] = v

def test_schema_roundtrip():
    db = FakeDB()
    assert get_hp(db) == 100
    assert update_hp(db, 55) == 55 and get_hp(db) == 55
    assert update_hp(db, 999) == 100
    assert get_sick(db) is False
    set_sick(db, True, hours=3, reason="感冒")
    assert get_sick(db) is True and sick_log(db)[-1]["reason"] == "感冒"
    assert Database(db).get("health:hp") == 100
