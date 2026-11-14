# -*- coding: utf-8 -*-
"""数据层入口：只做连接/表的适配，不写业务判断。

当前阶段是“薄封装”，真实库仍是 amo 原有的 self.db；
后续把 amo_db 的建表/迁移收进来时，只改这个文件，不动业务。
"""
from __future__ import annotations


class Database:
    """对 amo 现有 db 的只读/读写适配（kv_get/kv_set 已存在）。"""

    def __init__(self, db):
        self.db = db

    # ---------- 通用 kv ----------
    def get(self, key, default=None):
        try:
            return self.db.kv_get(key, default)
        except Exception:
            return default

    def set(self, key, value):
        try:
            self.db.kv_set(key, value)
            return True
        except Exception:
            return False

    # ---------- 表访问（后续迁移建表用） ----------
    def execute(self, sql, params=()):
        return self.db.conn.execute(sql, params)

    def commit(self):
        try:
            self.db.conn.commit()
        except Exception:
            pass
