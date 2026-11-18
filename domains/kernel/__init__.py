# -*- coding: utf-8 -*-
"""kernel 域（pilot）：跨域共享的纯函数/纯数据。

这里的模块只允许 import 标准库，外加极少数基础设施（clock / amo_observability）：
它们是时间与吞异常留痕的底座，不含业务逻辑。约束由 architecture ratchet 锁定。
"""
