# task-32 收件人/角色反转：11:31 飞书真实 P0 复盘

日期：2026-11-22 11:31:57（Asia/Shanghai）
收件人：老公（lark:FriendMessage:ou_cec66ea605ad17f62632fb044bc5f6ac）
DB：`amo_data.db messages #2288`（role=bot, uid=bot, name=大肥鱼, created_at=1795318317）
原文：`妈，我地铁上呢，中午回家吃，随便做点就行，我马上到。`

## 1. 来源结论（已核实）

**不是**陪伴自己的 proactive 轮（不在 proactive_log），**不是**延迟回复，**不是**旧发送函数。
链路是：

1. 11:31:47~50 watch 插件 social tick（`Astrbot_amoupup_watch/core/jobs.py:137`）
   调 `social.scheduled()`；妈妈发来邀约，watch 的 `services/social_ai.py` `respond()`
   让模型生成 `now`（「她当下回对方的一句话」），模型对「妈妈」写了
   `妈，我地铁上呢，中午回家吃，随便做点就行，我马上到。`。
2. 11:31:50.319 watch `social_engine._accept_invite_locked()` 写记忆
   （`_memory(... idem + ":mem")`，见 `services/social_engine.py:1561-1562`）：
   `amo_data.memories #1403` = `答应了妈妈，明天 12:00 去中午回家吃饭。`
   idem=`social_event:妈妈:2026-11-23:12:00:mem`；forever 日志
   `amo_memory_data:713 [IDENTITY_DEBUG] action=memory_write subject=bot kind=finance_permanent`；
   kv `mood_applied:social_event:妈妈:2026-11-23:12:00:mood:mood=true`。
3. `social_engine._apply()` 把 `result["line"]`（= `said` = `now_line`，对妈妈说的一句话）
   放进 `out["push"]`（`services/social_engine.py:1722-1723`）。
4. watch `core/jobs.py:139-150` 对 push 文本调 `amo.request_push(text, "social")`。
5. forever `amo_feature_api.request_push()` → `push_to_owner()` →
   `amo_message._send_to_umo()`（source=push）→ 分段延迟 4.14s → 11:31:57.787 发出。
   日志：`amo_message:726 [amo] 分段延迟...`、`amo_message:986 [amo] 主动 -> ...`。
6. kv 证据：`external_push_ts:social=1795318312.505433`（11:31:52.505，11:31:57.787 落库），
   证明这一条走的是 `request_push` 的外部 push 通道；该通道**不写 proactive_log**
   （proactive_log 只在 `amo_proactive.py` 的主动轮里写，如 `:2208`）。

排除项：
- 延迟队列：`amo_schedule._schedule_delayed_reply` 延迟 4~18 分钟，最后一条用户消息是
  10:32:28，最晚 10:50:28 就该发，对不上 11:31:57。
- 旧发送函数：日志行号来自 09:57:27 启动时加载的旧版本代码（当时代码行号 726/986，
  当前工作树已改到 815/1159），但 726 是 `_segment_wait`、986 是 `_send_to_umo` 里的
  `[amo] 主动 ->`（对照 `work/audit/_scratch/repo_pin/amo_message.py:727/996` 与
  `work/audit/_scratch/mem_persona/pin_20261122-104956/amo_message.py:800/1087`），
  仍是统一 outbound，不是旁路直发。
- 生成层根因在 **watch 插件**：`services/social_ai.py` 的 `PROMPT` 把 `now` 定义为
  「她当下回对方的一句话」，`social_engine._apply` 又把这句对 NPC 说的话当「给老公的 push」
  原样发出，中间没有换收件人。

## 2. 修复

### 2.1 唯一出站守卫（新增）
`amo_addressee_guard.py`：
- 首句称呼白名单：老公 / 你 / profile 名字/昵称 / 亲爱的等；
- 禁称呼：妈、爸、婆婆、阿姨、朋友、同事、领导、老师、亲戚…以及名单 NPC 名字；
- 命中禁称呼且是用户第一人称自述（`我马上到/我地铁上/我回家吃/随便做点`…）
  → `addressee_role_reversal:<称呼>`，整条丢弃；
- 只错称呼 → 改回老公/名字，`addressee_rewrite:<旧>-><新>`；
- `enforce(plugin, umo, text, source=...)` 统一打 `[ADDRESSEE]` 日志（含 source）。

### 2.2 接线（全部只调上面这一处）
| 文件 | 位置 | 行为 |
| --- | --- | --- |
| `amo_persona_guard.py` | `prepare_outbound` 末尾 | 所有走语义卫生的出口（含 `_send_now`、gateway）兜底 |
| `amo_outbound.py` | `send()` 频控前 | 丢弃/改写并写 `outbound_log(source=..., decision=drop, reason=addressee_*)` |
| `amo_outbound.py` | `send_media()` | 图片/语音 caption 同样过守卫（只改/去文字，不丢媒体） |
| `amo_message.py` | `_send_to_umo` 开头 | 主动/push/延迟补话/外部推送统一入口；丢弃时日志带 source |
| `amo_state_consistency.py` | `validate_reply_assembly` | 聊天拼装阶段先拦；丢弃给安全短句，绝不把原文交回核心 |

### 2.3 生成层 prompt（收件人锁）
- `amo_prompt_registry.py`：新增 `llm.rule.addressee`（【收件人锁】），内容明确：
  收件人只有老公；首句称呼只能老公/你/名字；妈/爸/朋友/同事只作话题，不能当收件人；
  禁止用户第一人称视角（我马上到/我回家吃/我下班了…）；别人的消息只能转述。
- `amo_llm.py` `_reply_system`：私聊 system 渲染该块（聊天主链）。
- `amo_llm_service.py` `gen_proactive`：主动消息 system 渲染该块。
- `amo_llm_service.py` `polish`：重写要求加第 8 条收件人锁。
- `tests/golden/prompts_golden.json`：仅更新 `private_system` / `private_system_note`
  两个 key（有意变更，golden 逐字节测试需要）。

### 2.4 跨插件后续（不在本仓库）
watch 的生成根因需要 watch 侧修，建议二选一：
- `social_engine._apply` 不再把 `result["line"]`（对 NPC 说的那句）放进 `out["push"]`，
  改成单独生成一句「对老公说」的分享句，或对家人/NPC 邀约直接不 push；
- 或在 `social_ai.PROMPT` 明确：`now` 只回给 NPC，不会发给老公；给老公的话要另写第三人称转述。
本次已由 forever 出站守卫兜住（真实样例会被 `addressee_role_reversal:妈` 丢弃并记 outbound_log）。

## 3. 测试

新增 `tests/test_addressee_guard.py`（21 cases）：
- 真实 11:31 样例（`妈，我地铁上呢…`）→ 纯函数/ persona_guard / outbound / message / state 五层都拦住；
- 合法句 `我妈今天给我打电话了，说周末让我回家吃饭。` → 五层都不误杀；
- `老公，我在地铁上呢…`、`你回来啦…`、`亲爱的，我到楼下了。` → 放行；
- `妈，今天天气不错。` → 改写成 `老公，今天天气不错。`；
- 名单 NPC 称呼（柳如烟）→ 不进收件人；
- prompt 锁块存在，chat/proactive/polish 都渲染。

媒体/出站子套件：
```
pytest tests/test_addressee_guard.py tests/test_media_gateway.py tests/test_outbound_gateway.py
  tests/test_outbound_guard.py tests/test_photo_event_binding.py -q
78 passed in 6.50s
```

相关套件（persona/outbound/message/state 及 prompt golden）：
```
pytest tests/test_addressee_guard.py tests/test_prompt_registry.py tests/test_persona_guard.py
  tests/test_persona_tone.py tests/test_outbound_gateway.py tests/test_outbound_guard.py
  tests/test_cov2_message.py tests/test_task15_reply_assembly.py tests/test_state_consistency.py
  tests/test_reply_pipeline.py tests/test_post_rewrite_recheck.py tests/test_memory_recall_ledger.py
  tests/test_media_gateway.py tests/test_backend_talk.py tests/test_immersion_guard.py
  tests/test_intent_wiring.py tests/test_transit.py tests/test_date_context.py
  tests/test_message_helpers.py tests/test_reply_guard.py tests/test_proactive_guard.py -q
```
结果见 `work/audit/task32_addressee_pytest.log`。

## 4. diff
`work/audit/task32_addressee.diff`（只含 task-32 改动；golden 仅两个 key，未贴全文）。
