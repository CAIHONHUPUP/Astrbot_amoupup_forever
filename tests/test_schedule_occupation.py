# -*- coding: utf-8 -*-
"""职业 → 日程活动库的单测：非技术职业不该出现写代码/改 Bug。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schedule_engine import (  # noqa: E402
    ADMIN_BANK,
    CREATIVE_BANK,
    MED_BANK,
    OFFICE_WORK_BANK,
    SALES_BANK,
    SERVICE_BANK,
    STUDENT_BANK,
    TEACHER_BANK,
    TECH_WORK_BANK,
    occupation_kind,
    travels_for_work,
    work_bank_for,
)


def world(occupation, workplace=""):
    return {"occupation": occupation, "workplace": workplace}


class WorkBankTests(unittest.TestCase):
    def test_tech(self):
        self.assertIs(work_bank_for(world("程序员")), TECH_WORK_BANK)
        self.assertIs(work_bank_for(world("后端开发")), TECH_WORK_BANK)

    def test_non_tech_never_gets_code_bank(self):
        for occupation, bank in (
            ("学生", STUDENT_BANK),
            ("大学老师", TEACHER_BANK),
            ("护士", MED_BANK),
            ("销售经理", SALES_BANK),
            ("平面设计", CREATIVE_BANK),
            ("厨师", SERVICE_BANK),
            ("会计", ADMIN_BANK),
            ("普通上班族", OFFICE_WORK_BANK),
            ("公务员", ADMIN_BANK),
        ):
            picked = work_bank_for(world(occupation))
            self.assertIs(picked, bank, occupation)
            self.assertIsNot(picked, TECH_WORK_BANK, occupation)
            self.assertNotIn("改 Bug", picked, occupation)

    def test_unknown_falls_back_to_office(self):
        self.assertIs(work_bank_for(world("自由职业")), OFFICE_WORK_BANK)
        self.assertIs(work_bank_for({}), OFFICE_WORK_BANK)

    def test_workplace_also_used(self):
        # occupation 没写，但公司名里带「科技」
        self.assertIs(work_bank_for(world("", "某某科技有限公司")), TECH_WORK_BANK)


class TravelTests(unittest.TestCase):
    def test_travel_jobs(self):
        self.assertTrue(travels_for_work(world("销售经理")))
        self.assertTrue(travels_for_work(world("商务")))
        self.assertTrue(travels_for_work(world("项目经理")))

    def test_non_travel_jobs(self):
        for occupation in ("程序员", "学生", "老师", "护士", "会计", "厨师"):
            self.assertFalse(travels_for_work(world(occupation)), occupation)


class KindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(occupation_kind(world("程序员")), "tech")
        self.assertEqual(occupation_kind(world("大学生")), "student")
        self.assertEqual(occupation_kind(world("高中老师")), "teacher")
        self.assertEqual(occupation_kind(world("医生")), "med")
        self.assertEqual(occupation_kind(world("销售")), "office")
        self.assertEqual(occupation_kind({}), "office")


if __name__ == "__main__":
    unittest.main()
