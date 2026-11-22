# -*- coding: utf-8 -*-
"""A1 社交政策单测：自动社交块不进日程、旧块迁移、关系数据保留、备份 7 天。"""
from __future__ import annotations

import unittest

import schedule_social_a1 as a1


def _row(start, end, activity, category="social", **extra):
    row = {"start": start, "end": end, "activity": activity,
           "place": "饭店", "zone": "restaurant", "category": category}
    row.update(extra)
    return row


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_delete(self, key):
        return self.kv.pop(key, None) is not None

    def kv_keys(self, prefix="", limit=200, offset=0):
        return [key for key in self.kv if key.startswith(prefix)][:limit]


class StripAutoSocialTests(unittest.TestCase):
    def test_auto_source_strips_social(self):
        rows = [_row("19:00", "21:00", "和朋友吃饭"), _row("21:00", "22:00", "睡觉", "sleep")]
        kept, removed = a1.strip_auto_social(rows, "jit")
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(removed), 1)
        self.assertEqual(kept[0]["activity"], "睡觉")

    def test_manual_source_keeps_social(self):
        rows = [_row("19:00", "21:00", "和老公吃饭")]
        kept, removed = a1.strip_auto_social(rows, "chat")
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])

    def test_date_block_is_social(self):
        rows = [_row("19:00", "21:00", "约会", "date")]
        kept, removed = a1.strip_auto_social(rows, "ensure")
        self.assertEqual((len(kept), len(removed)), (0, 1))


class MigrationTests(unittest.TestCase):
    def test_solo_replacement_keeps_span_and_drops_people(self):
        row = _row("19:00", "21:00", "和柳如烟吃饭、聊天",
                   people=["柳如烟"], companions=["柳如烟"], detail="和柳如烟约好")
        new = a1.solo_replacement(row)
        self.assertEqual(new["start"], "19:00")
        self.assertEqual(new["end"], "21:00")
        self.assertEqual(new["place"], "饭店")
        self.assertNotIn("柳如烟", new["activity"])
        self.assertNotIn("people", new)
        self.assertNotIn("companions", new)
        self.assertNotIn("柳如烟", new.get("detail") or "")
        self.assertEqual(new["category"], "meal")
        self.assertTrue(new["migrated_from_social"])

    def test_plan_day_idempotent(self):
        rows = [_row("19:00", "21:00", "和朋友吃饭"), _row("21:00", "22:00", "睡觉", "sleep")]
        once, changes = a1.plan_day(rows)
        self.assertEqual(len(changes), 1)
        twice, changes2 = a1.plan_day(once)
        self.assertEqual(changes2, [])
        self.assertEqual([row["activity"] for row in twice],
                         [row["activity"] for row in once])

    def test_migrate_day_backs_up_and_commits(self):
        db = FakeDB()
        rows = [_row("19:00", "21:00", "和朋友吃饭", people=["柳如烟"])]
        calls = []

        def commit(day, new_rows, **kwargs):
            calls.append((day, new_rows, kwargs))
            return {"changed": True, "version": 3}

        report = a1.migrate_day(db, "2026-11-21", rows, commit, now_ts=1000)
        self.assertTrue(report["changed"])
        self.assertTrue(report["backup"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2]["source"], "social_migration")
        backup = db.kv_get(a1.BACKUP_PREFIX + "2026-11-21")
        self.assertEqual(backup["rows"][0]["activity"], "和朋友吃饭")
        a1.archive_changes(db, report, 1000)
        archive = db.kv_get(a1.ARCHIVE_KEY)
        self.assertEqual(archive[0]["changes"][0]["people"], ["柳如烟"])

    def test_migrate_day_noop_does_not_write(self):
        db = FakeDB()
        rows = [{"start": "09:00", "end": "10:00", "activity": "上班",
                 "place": "公司", "zone": "office", "category": "work"}]
        report = a1.migrate_day(db, "2026-11-21", rows, lambda *a, **k: {}, now_ts=1000)
        self.assertFalse(report["changed"])
        self.assertEqual(db.kv, {})

    def test_purge_keeps_seven_days(self):
        db = FakeDB()
        now = 10 * 86400
        db.kv_set(a1.BACKUP_PREFIX + "old", {"at": now - 8 * 86400})
        db.kv_set(a1.BACKUP_PREFIX + "new", {"at": now - 6 * 86400})
        removed = a1.purge_backups(db, now, keep_days=7)
        self.assertEqual(removed, 1)
        self.assertIn(a1.BACKUP_PREFIX + "new", db.kv)
        self.assertNotIn(a1.BACKUP_PREFIX + "old", db.kv)


if __name__ == "__main__":
    unittest.main()
