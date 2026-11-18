# -*- coding: utf-8 -*-
"""运行时 mixin 的共同类型底座：跨 mixin 的方法/属性统一从这里取。

运行期是 object（和拆分前 _MixinBase = object 的语义一致，不参与 MRO 决议）；
类型检查时继承 PluginLike，并用 __getattr__ 兜住另一个 mixin 里的成员。
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _ProtocolBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _ProtocolBase = object


class RuntimeMixinBase(_ProtocolBase):
    """三个 mixin 的公共底座；运行期等价于 object。"""

    if TYPE_CHECKING:  # 跨 mixin 的方法/属性由这里兜底
        def __getattr__(self, name: str) -> Any: ...
