# -*- coding: utf-8 -*-
"""回复流水线的数据结构内核：StopPipeline / ReplyContext / Stage。

域内只依赖标准库（dataclasses/typing）；根目录 amo_reply_pipeline.py 以 re-export
适配器身份保留旧名字，identity 不变。搬自 amo_reply_pipeline.py（2026-11-18，逐字未改）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

PHASE_SEND = "send"
CHANNEL_PRIVATE = "private"

class StopPipeline(Exception):
    """阶段要求立刻停下（后面的阶段都不跑）；run() 会把 stopped 记下来。"""

@dataclass
class ReplyContext:
    """一条回复的上下文：谁、在哪、哪种会话、他的原话、上一句 bot 话。"""

    plugin: Any = None
    umo: str = ""
    phase: str = PHASE_SEND
    channel: str = CHANNEL_PRIVATE
    group: bool = False
    event: Any = None
    result: Any = None
    profile: Dict[str, Any] = field(default_factory=dict)
    relation: str = ""
    activity: Dict[str, Any] = field(default_factory=dict)
    place: str = ""
    scene: str = ""
    owner_name: str = "他"
    snap: Dict[str, Any] = field(default_factory=dict)
    dating: bool = False
    his_words: str = ""
    last_bot_line: str = ""
    system: str = ""
    prompt: str = ""
    tone: str = ""
    state_text: str = ""
    name: str = ""
    persona: str = ""
    style: str = ""
    consult: bool = False
    cash_cents: int = 0
    hour: Optional[int] = None
    silence_days: int = 0
    stage_set: Set[str] = field(default_factory=set)
    target_set: Set[str] = field(default_factory=set)
    trace: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def mark(self, stage_id: str, target: str = "") -> None:
        self.stage_set.add(str(stage_id))
        if target:
            self.target_set.add(str(target))

    def marked(self, stage_id: str = "", target: str = "") -> bool:
        if stage_id and str(stage_id) in self.stage_set:
            return True
        return bool(target) and str(target) in self.target_set

    def note(self, message: str) -> None:
        self.notes.append(str(message))

@dataclass
class Stage:
    """一道守卫。

    - run(text, ctx)：返回新文本；返回 None 表示「不动这段文本」。
      可以是同步函数，也可以是 async 函数（run() 会 await）。
    - when(ctx)：这道守卫在当前场景是否适用；None 表示永远适用。
    - replace：False 表示这个阶段只做副作用，返回值不参与文本。
    - side_effect：会改状态/发消息/落库的显式标注。
    - wired：False 表示只登记顺序，生产调用点还没接管它。
    - target：底层守卫函数名，用于跨阶段去重与 audit 的「重复/覆盖」。
    - ctx_keys：这道守卫读了 ctx 的哪些字段（空 = 纯文本阶段）。
    - run=None：纯登记（描述阶段），复合块暂时不动。
    """

    id: str
    phase: str
    priority: int
    run: Optional[Callable[[str, ReplyContext], Any]] = None
    when: Optional[Callable[[ReplyContext], bool]] = None
    replace: bool = True
    side_effect: bool = False
    wired: bool = True
    target: str = ""
    ctx_keys: Sequence[str] = ()
    note: str = ""
    swallow: str = ""          # "" 抛出 | "debug" | "note"
    log_msg: str = ""
    note_scope: str = ""
