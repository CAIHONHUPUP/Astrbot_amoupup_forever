# -*- coding: utf-8 -*-
"""domains：按业务域拆分的骨架（pilot，2026-11-17）。

域边界规则（本文件由 tests/test_domain_boundaries.py 锁定）：
- domains/* 是**叶子**：只 import 标准库 / typing，不 import 插件内部模块（不做反向依赖）；
- 域之间只通过 Protocol（对外契约）交互，不直接 import 对方实现；
- 插件（main / api / 面板）可以用域里的契约，但域不知道插件存在。

现有成员：domains.memory（记忆终端契约）、domains.finance（钱包契约）。
下一步（未做）：把实现搬进 domains/*/impl.py，根目录模块只留薄薄的 re-export 门面。
"""
