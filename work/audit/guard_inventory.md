# 守卫 / 补丁登记表（guard inventory）

- 生成时间：2026-11-22T10:27:33.371Z
- 范围：出站改写/兜底类守卫与补丁（含安全底线与症状补丁）
- 方式：只读扫描当前工作区生产代码；未改任何代码/测试；所有结论带 文件:行
- 机器可读：[guard_inventory.json](guard_inventory.json)

## 0. 一页结论

| 维度 | 计数 |
|---|---:|
| 守卫/补丁总数 | **38** |
| tier=safety | 17 |
| tier=style | 13 |
| tier=symptom | 8 |
| 建议 keep | 16 |
| 建议 merge | 11 |
| 建议 downgrade | 10 |
| 建议 delete | 1 |
| 1b 第 1 波 | 14 |
| 1b 第 2 波 | 10 |
| 1b 第 3 波 | 14 |

**重复层结论（一句话）**：三层候选优先可以合并成一层——入口处一次“候选答到且现行是兜底/答不到就换回安全候选”，出口只保留一次统一救援；层 1（_prefer_answered_candidate）的裸还原应删，层 2/3（final_semantic_recheck + recover）保留判据后合并。

## 1. 上下文：一条聊天回复的真实顺序

1. amo_runtime_reply.on_decorating_result:138 strip_chat_format
2. _deco_soft_guards:276
   - remember_candidate:285（登记原始候选，TTL 180s）
   - _guard_self_location:298 -> place_guard.rewrite_reply:168
   - apply_fact_guard:303（+naturalize_fact_rewrites:947）
   - apply_narrative_repetition:308（登记 _TURN_SOURCE:1624；出口 final_semantic_recheck:1661）
   - apply_tone_guard:315
3. _deco_rewrite_reply:337（防御话术/AI 自曝，固定句 351/354）
4. _deco_polish_native / _deco_text_cleanup:183（_align_arrival:527 / _align_swim_where:535）
5. apply_chat_safety:198
   - _prefer_answered_candidate:1503（层 1）
   - immersion/names/purchase/anti_nag/companion/marriage/voice
   - state_consistency.rewrite:1575（出口 _post_recheck:1099 -> 层 2）
   - role_guard:1581
   - question_guard.answer:1597
   - final_semantic_recheck:1604（层 2）
   - recover_rewrite_damage:1606（层 3）
6. strip_tech_terms:206
7. send：amo_message._send_to_umo:1049
   - pipeline send 阶段（clean/names/zone/photo/wardrobe/privacy/status_dedup/date/immersion/transit/model_leak）
   - addressee:1071 -> anti_nag:1093 -> purchase:1099
   - validate_reply_assembly:1110（内部 post_recheck:1048、complete_deleted_answer:1057、recover:1067、ensure_where:1072）
   - strip_tech_terms:1119 -> segment:1145 -> dedupe_reply_segments:1133 -> 每段 repeat guard:1144
8. segment_engine.handle_segment_reply:240
   - _validate_assembly:265 -> validate_reply_assembly
   - _dedupe_assembly:291 -> dedupe_reply_segments（内部 recover:982）
   - 逐段 prepare_outbound:306
9. gateway：amo_outbound.send:411（addressee）+ :426（prepare_outbound）-> transport

> 结果：同一个候选在一轮里最多可被还原 5 次（层 1、narrative 出口、chat_safety 出口、validate、dedupe）；prepare_outbound 最多跑 3 次（逐段 + gateway，直发路径再加 1 次）。

## 2. 三层候选优先：条件与覆盖关系

| | 层 1 _prefer_answered_candidate | 层 2 final_semantic_recheck | 层 3 recover_rewrite_damage |
|---|---|---|---|
| 位置 | apply_chat_safety:1503（最早） | :1604 + narrative:1661 + state:705 + validate:1048 | :1606 + validate:1067 + dedupe:982（最后） |
| 触发 | 原始候选答到 &&（现行是兜底 或 不答） | candidate 答到 &&（current 不答 或 是兜底）&& 在要答案 | current 是 FALLBACKS/多池 && 候选安全 && damage_reasons 非空 |
| 还原内容 | 原始候选（未 sanitize） | _restore_candidate（媒体+消费） | sanitize_candidate + guard_candidate 探针 |
| 安全保护 | 无 | _protected_current（安全挡话/还没买）+ _unsafe_candidate（自曝/泄漏） | _candidate_unsafe（地点/角色反转）+ _UNSAFE_MARKERS |
| 计数 | 无 | _RECHECK_STATS（仅运行时） | 仅 info 日志 |
| 重复 | 与层 2 完全重叠 | 与层 3 在 fallback 场景重叠 | narrative/validate/dedupe 各调用一次 |

**合并设计（建议）**

1. 删除层 1；在 apply_chat_safety 入口做一次“候选优先”裁决：候选答到且现行是兜底/不答 -> 用 sanitize_candidate 后的候选作为后续所有守卫的输入。
2. 出口只保留一个 rescue 函数：触发条件取层 2 与层 3 的并集（current 是兜底/多池 OR（在要答案 && current 答不到））；约束取交集（_protected_current、_unsafe_candidate、_candidate_unsafe、damage_reasons 非空、候选非重复）。
3. narrative/state/validate/dedupe 只调用这一个函数，并带 reason 计数；删除 post_rewrite_recheck 的独立入口（作为 rescue 内部判据保留）。
4. 在 rescue 里加“近聊近似重复”检查，避免 #2223 这类把重复句还原回来。

## 3. 固定句清单（可能被整条发出去的罐头句）

### 3.1 统一注册表 domains/reply/repetition_guard.py:51（48 键 / 90 句）

| 池 | 句数 | 内容 |
|---|---:|---|
| generic | 9 | 还是老样子 / 等下再说 / 等会儿再说 / 再说吧 / 嗯 / 嗯嗯 / 哦 / 好 / 我还在外面呢，晚点就回去 |
| status_repeat | 1 | 还是老样子。 |
| short | 6 | 嗯，我在呢。/ 在听你说呢。/ 我这儿一直在。/ 你说，我听着呢。/ 嗯嗯，知道啦。/ 好，我记着呢。 |
| leak | 5 | 秘密，不告诉你。/ 这个就不说了。/ 你猜呀。/ 才不告诉你呢。/ 别问这个啦。 |
| scene_generic | 8 | 我在家呢（带句号/不带）/ 我到了 / 我到了，等会儿说。/ 我还在路上呢，快到了（带句号）/ 嗯。 |
| scene_home/road/outside | 3 | 我在家呢。/ 我还在路上呢，快到了。/ 我到了，等会儿说。 |
| question.* | 9 | 我在{place}呢。/ 我在{doing}。/ 和{who}在一起呢。/ 一个人呢。/ 我已经在家啦。/ 我还在{place}，{end}回去。/ 我晚点回去。/ 吃过了，你呢？/ 还没呢，你呢？ |
| action.* | 11 | shy 4 / laugh 3 / default 4（见 amo_message._ACTION_FALLBACK，同数据两份） |
| align.* | 3 | 我还在路上呢，快到了 / 我还在外面呢，晚点就回去 / 在外面呢，等我到家再拍给你看，先别催。 |
| emotion.* | 27 键 / 35 句 | 我也想你呀。等 35 句（想你/爱你/累/烦/难过/委屈/心疼/开心/生气/难受/孤单/怕/哭/撒娇/痛/暖/可爱/抱/亲 等） |

### 3.2 注册表外的固定句

| 来源 | 固定句 |
|---|---|
| repetition_guard.EXTRA_FIXED_SENTENCES:130 | 我试了试，挺喜欢的，还没买。/ 唔…这个先不说了，陪我聊点别的嘛。/ 哈哈哈，笑死我了。/ 刚才走神了一下，你再说一遍？ |
| amo_purchase_guard._FALLBACK:16 | 我试了试，挺喜欢的，还没买。 |
| amo_anti_nag._FALLBACK:14 | 好，那你忙完跟我说一声。 |
| amo_shared_text.TECH_FALLBACKS:617 | chat: 唔…这个先不说了，陪我聊点别的嘛。 |
| amo_persona_guard.safe_reply:234 | 我在呢，先别急，慢慢跟我说。/ 我刚说过的呀，就是刚才跟你提的那个，你别逗我。/ 我记混了，你再说一遍？ |
| amo_grounding_critic.safe_reply:85 | 然后啊，{topic}的事还没讲完呢，后面还有更好笑的。/ 然后啊，刚才那个还没讲完呢，后面还有更好笑的。/ 你刚问的这个，我一下没答好，你再说一遍？/ 换谁都得气，我在听着。/ 我看到了，等我缓一下再细看。/ 我在听，你接着说。 |
| short_reply:79-108 | 哇！谢谢老公！爱你！/ 好呀~ / 没问题啦！/ 嗯。/ 你敷衍我是吧？那别聊了。/ 你就回我一个嗯？哼，我不理你了！ |
| amo_message._ACTION_FALLBACK:260 | ……你少来。/ 别盯着看啦。/ ……干嘛呀。/ 你这个人真是的。/ 行了行了，别闹。/ 哈哈，你呀。/ 笑死我了。/ ……嗯。/ 你接着说。/ 我在听呢。/ 然后呢？ |
| amo_role_guard:24 | 换谁都得气，关键处卡住太难受了。 |
| amo_marriage_guard:30-37 | 我只有你一个。/ 工作上的事，下班再说。 |
| amo_state_consistency.rewrite:636-701 | 我还在路上呢，快到了 / 我到了 / 我在家呢 / 我到了，等会儿说。/ 我等会儿煮点面吃。 |
| _deco_rewrite_reply:351,354 | 你们聊你们的，我不抢话。/ 你才人机呢，你见过这么会聊天的机器人吗？ |
| _humanize_system_sentences:379 | 刚才走神了一下，你再说一遍？ |

> 合计：注册表 90 句 + 注册表外约 35 句，是“整条替换”误伤与机械感的共同来源（回放 19 条整条替换、多池串味见 holdout_report.md:143-145/263）。

## 4. 按 tier 分组

### 4.1 安全底线（删了会出事实/角色/报账/后台事故）

| id | 触发 | 动作 | 建议 | 波 |
|---|---|---|---|---|
| amo_persona_guard.strip_action_narration (amo_persona_guard.py:87) | 任意出站文本含成对括号（全角/半角，内容<=60字）或分段后残留孤括号；整条只有动作时返回空串 | 删括号动作；整条只剩动作=丢弃不发；孤括号片段丢到括回/开括号前 | **keep** | 3 |
| amo_persona_guard._humanize_system_sentences (amo_persona_guard.py:350) | system_word_hit 命中且 source=chat | 拉丁后台词替换 + 按分句删系统句；删空给固定句 | **keep** | 3 |
| amo_persona_guard.prepare_outbound (amo_persona_guard.py:382) | 所有出站（chat/主动/push/提醒/外部推送） | 总闸：媒体->collapse->旁白->系统词->禁语->金额->人名->收件人；reason 非空=丢弃 | **merge** | 1 |
| amo_persona_guard.guard_candidate (amo_persona_guard.py:522) | 候选还原前安全探针 | 对候选跑 apply_fact_guard 返回守卫形态 | **merge** | 1 |
| amo_persona_guard.ground_names (amo_persona_guard.py:279) | ROSTER 名字在回复出现但近 8 条上下文没出现 | 替换为同圈层中性称呼 | **keep** | 3 |
| amo_state_consistency.validate_reply_assembly (amo_state_consistency.py:991) | 所有发送前（segment_engine 拼装前 + _send_to_umo） | 媒体->子句去重->场景剥离->收件人->post_recheck->补答->recover->ensure_where | **merge** | 1 |
| amo_state_consistency.rewrite (amo_state_consistency.py:607) | scene road/outside/home 冲突、角色反转、重复分享、硬拼煮面句、笑点反问 | 删冲突分句/括号/角色反转分句；换固定场景句；分享开头软化；动物纠对象 | **keep** | 3 |
| amo_reply_pipeline.apply_fact_guard(+naturalize_fact_rewrites) (amo_reply_pipeline.py:935) | reply_fact_guard 报冲突（地点/健康/吃饭/动作/同伴） | 改冲突小句为固定 replacement，再经语气池换说法 | **keep** | 3 |
| amo_purchase_guard.rewrite (amo_purchase_guard.py:93) | 声称买了/花了钱，但近 6h 账本/衣柜无对应记录 | 含 claim/try 的句子整句换成固定句；真买时只留一条 claim | **downgrade** | 2 |
| place_guard.rewrite_reply (place_guard.py:168) | 第一人称地点声明与当前 zone 的 ALLOWED_GROUPS 冲突 | 正则把地点词换回 target（含方位后缀） | **merge** | 1 |
| amo_addressee_guard.enforce (amo_addressee_guard.py:214) | 首句称呼属于 BANNED_ADDRESSES/他人名；带您=丢弃；用户第一人称自述=丢弃；否则改写首句称呼 | 整条丢弃 or 只换首句称呼 | **keep** | 3 |
| amo_tts_guard._clean(clean_voice_text) (amo_tts_guard.py:207) | TTS 合成前 | 去 Markdown、剥动作旁白；动作记 marker 不进语音 | **keep** | 3 |
| amo_role_guard.rewrite (amo_role_guard.py:17) | user 在说自己气/烦/累，回复却自我否认（我哪生气/别扣帽子） | 删笑开头；否认句换固定句；删空兜底 | **keep** | 3 |
| amo_marriage_guard.rewrite (amo_marriage_guard.py:24) | 前男友/相亲/介绍对象/男闺蜜/其他男性+暧昧动词 | 整句换固定句或词替换 | **keep** | 3 |
| amo_companion_guard.rewrite (amo_companion_guard.py:18) | 日程块有 companions，回复出现 陪你/和你/跟你/我们/等你 | 子串替换 | **keep** | 3 |
| amo_shared_text.strip_tech_terms(TECH_FALLBACKS) (amo_shared_text.py:617) | 命中技术/后台词 | 删命中分句；整条命中按 source 给固定句 or 丢弃 | **keep** | 3 |
| amo_immersion_guard.fix_identity/sanitize_extreme (amo_immersion_guard.py:1) | 身份混淆/极端表达 | 确定性改写 | **keep** | 3 |

### 4.2 文风层（机械感主要来源）

| id | 触发 | 动作 | 建议 | 波 |
|---|---|---|---|---|
| amo_persona_guard.safe_reply (amo_persona_guard.py:234) | critic 两次都没接住（生成期） | 按情绪/是否分享过返回固定安全句 | **downgrade** | 2 |
| amo_state_consistency.dedupe_reply_segments (amo_state_consistency.py:937) | 分段与最近 bot 文本/本轮前段重复（子句级 drop_repeated_clauses） | 整段丢弃；全删空且问 where 补地点句；最后调用 recover 还原候选 | **merge** | 1 |
| amo_state_consistency._safe_short_reply (amo_state_consistency.py:1075) | validate 删空/收件人丢弃/空回复 | 从 short 池轮换取一句；再兜底 | **merge** | 1 |
| amo_reply_pipeline.apply_narrative_repetition (amo_reply_pipeline.py:1610) | 状态词（药/等车/打车/医院/回家）近 3 条重复；整句重复（相似度 0.9） | 删重复状态句/整句；全删空 -> status_repeat；出口 final_semantic_recheck | **downgrade** | 2 |
| amo_reply_pipeline.apply_tone_guard (amo_reply_pipeline.py:989) | 同一开头连续 2 条，或 3-8 字片段近 5 条 >=2 次 | 删句首片段/删独立短语句；不动句中事实/情绪 | **keep** | 3 |
| amo_reply_pipeline.apply_segment_repeat_guard (amo_reply_pipeline.py:1709) | 单个 segment 与近 3-5 条/本轮前段重复 | dedup_or_fallback 换短句；记入 _TURN_SEGMENTS | **merge** | 1 |
| amo_question_guard.answer (amo_question_guard.py:97) | 问句分类 where/doing/who/when/eaten 命中，且当前回复和候选都没答到 | 整条替换成状态模板（8 种） | **downgrade** | 2 |
| amo_anti_nag.rewrite (amo_anti_nag.py:17) | user 表示忙/等我/还没好，且回复含催问句 | 删催问句；删空给固定句 | **downgrade** | 2 |
| amo_grounding_critic.safe_reply (amo_grounding_critic.py:85) | 接话 critic 两次失败 | 按短追问/问句/情绪/分享返回固定安全句 | **downgrade** | 2 |
| domains.reply.repetition_guard.FALLBACKS (domains/reply/repetition_guard.py:51) | 各调用方按 intent 取句 | 固定句池（48 键/90 句） | **downgrade** | 2 |
| amo_message.action_only_fallback(_ACTION_FALLBACK) (amo_message.py:267) | 整段只有括号动作 | 按害羞/笑/默认补一句固定台词，保留动作 | **downgrade** | 2 |
| short_reply.guard(_replace_banned/counter_line) (short_reply.py:111) | <=10 字且含客服词（好的/收到/谢谢/抱歉），或他敷衍（嗯/哦/行） | 换成固定句/反击句 | **downgrade** | 2 |
| amo_state_consistency._ensure_where_answer (amo_state_consistency.py:1) | 他问 where/在家吗，最终文本没有地点词 | 追加/替换当前地点句 | **keep** | 3 |

### 4.3 症状补丁（补丁摞补丁，优先合并/删除）

| id | 触发 | 动作 | 建议 | 波 |
|---|---|---|---|---|
| amo_persona_guard.neutralize_forbidden(_SELF_THIRD/_DROP_SENTENCE) (amo_persona_guard.py:47) | 命中 FORBIDDEN_PHRASES（你美丽的老婆/你们男人/收到你后来/收到你那句/这有啥好笑的/我哪生气了/别给我扣帽子） | 5 条整句丢弃；3 条子串替换 | **downgrade** | 2 |
| amo_persona_guard.sanitize_candidate (amo_persona_guard.py:446) | 仅候选还原时对候选做安全改写 | 媒体->旁白->技术词->系统句->禁语->金额->人名->anti_nag->purchase->addressee | **merge** | 1 |
| amo_rewrite_safety.recover (amo_rewrite_safety.py:415) | source=chat；候选 TTL 180s；现行命中 FALLBACKS 或 is_multi_pool；候选 sanitize/probe 安全；damage_reasons 非空；非后台话术 | 整条替换为安全改写后的候选；候选不安全时多池场景退守卫形态 | **merge** | 1 |
| amo_rewrite_safety.damage_reasons (amo_rewrite_safety.py:273) | candidate/current 差异 14 类（fixed_cover/multi_pool/unbalanced/dup/fragment/place_swap/word_removed/big_shrink…） | 只判定不改写 | **keep** | 1 |
| amo_reply_pipeline._prefer_answered_candidate (amo_reply_pipeline.py:1170) | 同一轮；原始候选答到他这句；现行文本是任意兜底池或不答 | 直接返回原始候选（未 sanitize） | **delete** | 1 |
| amo_reply_pipeline.final_semantic_recheck (amo_reply_pipeline.py:1350) | candidate 答到且 (current 不答 或 是 fallback) 且 user 在要答案；current 非安全挡话/事实产物；candidate 非自曝/泄漏 | 经 _restore_candidate（媒体+purchase）还原候选；失败保持 | **merge** | 1 |
| amo_grounding_critic.post_rewrite_recheck (amo_grounding_critic.py:212) | original 答到 且 (rewritten 不答 或 rewritten 是 fallback) | 返回 original（纯判定） | **merge** | 1 |
| domains.reply.repetition_guard.fixed_rewrite_bodies/is_multi_pool (domains/reply/repetition_guard.py:188) | 识别整条是否固定兜底/多池串味 | 只判定 | **keep** | 1 |

## 5. 按建议分组

### 保留（安全底线或单一数据源）（16）

- amo_persona_guard.strip_action_narration
- amo_persona_guard._humanize_system_sentences
- amo_persona_guard.ground_names
- amo_rewrite_safety.damage_reasons
- amo_state_consistency.rewrite
- amo_reply_pipeline.apply_fact_guard(+naturalize_fact_rewrites)
- amo_reply_pipeline.apply_tone_guard
- domains.reply.repetition_guard.fixed_rewrite_bodies/is_multi_pool
- amo_addressee_guard.enforce
- amo_tts_guard._clean(clean_voice_text)
- amo_role_guard.rewrite
- amo_marriage_guard.rewrite
- amo_companion_guard.rewrite
- amo_shared_text.strip_tech_terms(TECH_FALLBACKS)
- amo_immersion_guard.fix_identity/sanitize_extreme
- amo_state_consistency._ensure_where_answer

### 合并（重复层/重复实现）（11）

- amo_persona_guard.prepare_outbound
- amo_persona_guard.sanitize_candidate
- amo_persona_guard.guard_candidate
- amo_rewrite_safety.recover
- amo_state_consistency.dedupe_reply_segments
- amo_state_consistency.validate_reply_assembly
- amo_state_consistency._safe_short_reply
- amo_reply_pipeline.final_semantic_recheck
- amo_reply_pipeline.apply_segment_repeat_guard
- amo_grounding_critic.post_rewrite_recheck
- place_guard.rewrite_reply

### 降级（固定句/整句模板）（10）

- amo_persona_guard.neutralize_forbidden(_SELF_THIRD/_DROP_SENTENCE)
- amo_persona_guard.safe_reply
- amo_reply_pipeline.apply_narrative_repetition
- amo_question_guard.answer
- amo_purchase_guard.rewrite
- amo_anti_nag.rewrite
- amo_grounding_critic.safe_reply
- domains.reply.repetition_guard.FALLBACKS
- amo_message.action_only_fallback(_ACTION_FALLBACK)
- short_reply.guard(_replace_banned/counter_line)

### 删除（1）

- amo_reply_pipeline._prefer_answered_candidate

## 6. 1b 三波建议清单

### 第一波：合并重复层（低风险、先做）
- [merge] amo_persona_guard.prepare_outbound —— amo_persona_guard.py:382
- [merge] amo_persona_guard.sanitize_candidate —— amo_persona_guard.py:446
- [merge] amo_persona_guard.guard_candidate —— amo_persona_guard.py:522
- [merge] amo_rewrite_safety.recover —— amo_rewrite_safety.py:415
- [keep] amo_rewrite_safety.damage_reasons —— amo_rewrite_safety.py:273
- [merge] amo_state_consistency.dedupe_reply_segments —— amo_state_consistency.py:937
- [merge] amo_state_consistency.validate_reply_assembly —— amo_state_consistency.py:991
- [merge] amo_state_consistency._safe_short_reply —— amo_state_consistency.py:1075
- [delete] amo_reply_pipeline._prefer_answered_candidate —— amo_reply_pipeline.py:1170
- [merge] amo_reply_pipeline.final_semantic_recheck —— amo_reply_pipeline.py:1350
- [merge] amo_reply_pipeline.apply_segment_repeat_guard —— amo_reply_pipeline.py:1709
- [merge] amo_grounding_critic.post_rewrite_recheck —— amo_grounding_critic.py:212
- [keep] domains.reply.repetition_guard.fixed_rewrite_bodies/is_multi_pool —— domains/reply/repetition_guard.py:188
- [merge] place_guard.rewrite_reply —— place_guard.py:168

### 第二波：固定句/整句模板降级（收益最大，逐条 A/B）
- [downgrade] amo_persona_guard.neutralize_forbidden(_SELF_THIRD/_DROP_SENTENCE) —— amo_persona_guard.py:47
- [downgrade] amo_persona_guard.safe_reply —— amo_persona_guard.py:234
- [downgrade] amo_reply_pipeline.apply_narrative_repetition —— amo_reply_pipeline.py:1610
- [downgrade] amo_question_guard.answer —— amo_question_guard.py:97
- [downgrade] amo_purchase_guard.rewrite —— amo_purchase_guard.py:93
- [downgrade] amo_anti_nag.rewrite —— amo_anti_nag.py:17
- [downgrade] amo_grounding_critic.safe_reply —— amo_grounding_critic.py:85
- [downgrade] domains.reply.repetition_guard.FALLBACKS —— domains/reply/repetition_guard.py:51
- [downgrade] amo_message.action_only_fallback(_ACTION_FALLBACK) —— amo_message.py:267
- [downgrade] short_reply.guard(_replace_banned/counter_line) —— short_reply.py:111

### 第三波：保留项复核 + 零触发删除（需埋点）
- [keep] amo_persona_guard.strip_action_narration —— amo_persona_guard.py:87
- [keep] amo_persona_guard._humanize_system_sentences —— amo_persona_guard.py:350
- [keep] amo_persona_guard.ground_names —— amo_persona_guard.py:279
- [keep] amo_state_consistency.rewrite —— amo_state_consistency.py:607
- [keep] amo_reply_pipeline.apply_fact_guard(+naturalize_fact_rewrites) —— amo_reply_pipeline.py:935
- [keep] amo_reply_pipeline.apply_tone_guard —— amo_reply_pipeline.py:989
- [keep] amo_addressee_guard.enforce —— amo_addressee_guard.py:214
- [keep] amo_tts_guard._clean(clean_voice_text) —— amo_tts_guard.py:207
- [keep] amo_role_guard.rewrite —— amo_role_guard.py:17
- [keep] amo_marriage_guard.rewrite —— amo_marriage_guard.py:24
- [keep] amo_companion_guard.rewrite —— amo_companion_guard.py:18
- [keep] amo_shared_text.strip_tech_terms(TECH_FALLBACKS) —— amo_shared_text.py:617
- [keep] amo_immersion_guard.fix_identity/sanitize_extreme —— amo_immersion_guard.py:1
- [keep] amo_state_consistency._ensure_where_answer —— amo_state_consistency.py:1

## 7. 最该先处理的 5 条

1. **删除** amo_reply_pipeline._prefer_answered_candidate（:1170）：裸还原、与层 2 完全重叠、无安全过滤。
2. **删除** _SELF_THIRD 子串替换（amo_persona_guard.py:36,50）：回放残句 #2271/#2272/#2274 的直接来源；改整句丢弃或重生成。
3. **替换固定句** amo_purchase_guard._FALLBACK（:16）：回放最大整条误伤句；改“没买”的多样表达/重生成。
4. **降级** apply_narrative_repetition 的 还是老样子 回落（:1635）：改换开场重写，不吐罐头句。
5. **合并** final_semantic_recheck + post_rewrite_recheck + recover_rewrite_damage 为单一 rescue，并加近聊重复检查（修 #2223）。

### 7.1 严格意义的删除候选（必须先有替代路径）

1. amo_reply_pipeline._prefer_answered_candidate（:1170）：与层 2 重复，删后入口用一次 sanitize 候选裁决。
2. amo_persona_guard._SELF_THIRD 子串替换（:36,50）：删后命中禁语改整句丢弃/重生成，不做局部替换。
3. amo_persona_guard.safe_reply 固定句（:234）：等 critic 重生成路径就位后删固定句。
4. amo_message.action_only_fallback 的固定池（:260）：先接 repetition_guard emotion/action 语气池，再删私有池。
5. short_reply._replace_banned 固定句（:97）：prompt 层覆盖客服词后删代码兜底。

## 8. 证据与未测

### 8.1 已引用证据
- work/audit/holdout/replay_prod.json / replay_prod.txt：176 条文本、27 条 before_bad、fixed 25、persistent 2、clean_other 34、旁白 101->0、change_source 分布。
- work/audit/holdout_report.md:33,122-123,143-145,263：19 条整条固定替换、多池串味、旁白率。
- work/audit/memory_persona.md:21,68：历史旁白 56.5%。
- work/audit/real_272.json / real_397.json：4/272、6/397 失败，全部 cand_codes（非守卫引入）。
- work/sim/daily_report.json：injected 204 / repaired 173 / residual 0；remind_report.json 240/240；adversarial.json 39/39。
- 测试：tests/test_persona_guard.py、test_cov2_message.py、test_post_rewrite_recheck.py、test_grounding_fallback.py、test_tone_guard*.py、test_reply_fact_guard.py、test_addressee_guard.py、test_task41_adversarial_gaps.py。

### 8.2 未测（需要埋点才能决定删/留）
- _prefer_answered_candidate / final_semantic_recheck / recover 的每轮触发次数（目前只有 info 日志或 _RECHECK_STATS 运行时）。
- dedupe_reply_segments / apply_segment_repeat_guard / tone_guard 的独立触发率。
- sanitize_candidate / guard_candidate 的 restore 分支命中率。
- short_reply / grounding.safe_reply / persona.safe_reply 的二次失败触发率。
- 建议：下一轮先在 prepare_outbound / rescue / dedupe 三处加 reason 计数（不改变行为），积累 24-48h 后再删零触发项。

## 9. 附录：逐条明细

### amo_persona_guard.strip_action_narration

- 位置：amo_persona_guard.py:87；tier=safety；建议=keep；1b 第 3 波
- 触发：任意出站文本含成对括号（全角/半角，内容<=60字）或分段后残留孤括号；整条只有动作时返回空串
- 动作：删括号动作；整条只剩动作=丢弃不发；孤括号片段丢到括回/开括号前
- 调用点：amo_persona_guard.prepare_outbound:407；amo_persona_guard.sanitize_candidate:465；amo_rewrite_safety._comparable:265；amo_message._strip_action_narration:67 -> _send_to_umo:1146,1160
- 覆盖关系：在 neutralize_forbidden/金额/人名之前；segment_engine 逐段 prepare_outbound 会再跑一次（幂等）
- 重复/重叠：amo_voice._ACTION_RE:58（TTS 独立正则）；amo_state_consistency.rewrite:616-622（场景括号删除）；segment_engine._merge_action_segments:473
- 触发频率证据：回放 101/176 带旁白 -> 0（replay_prod.json counts）；memory_persona.md:21 56.5%；2026-11-22 17:58 线上孤括号 1 条
- 删改风险：中：跨行硬切会产生孤括号（已加出口不变量）；纯动作消息丢弃（预期）

### amo_persona_guard.neutralize_forbidden(_SELF_THIRD/_DROP_SENTENCE)

- 位置：amo_persona_guard.py:47；tier=symptom；建议=downgrade；1b 第 2 波
- 触发：命中 FORBIDDEN_PHRASES（你美丽的老婆/你们男人/收到你后来/收到你那句/这有啥好笑的/我哪生气了/别给我扣帽子）
- 动作：5 条整句丢弃；3 条子串替换
- 固定句：你美丽的老婆->我；你老婆->我；你们男人->你
- 调用点：amo_persona_guard.prepare_outbound:419；amo_persona_guard.sanitize_candidate:480
- 覆盖关系：strip_action/humanize 之后、金额/人名之前
- 重复/重叠：prompt persona_packs forbidden 列表；amo_role_guard._SELF_DENIAL:12
- 触发频率证据：回放 forbidden_strip 14/176；holdout_report.md:263 固定改写是最大误伤源
- 删改风险：高：子串替换留残句（#2271 儿半天了、#2272 看一眼嘛、#2274 也不来哄哄）

### amo_persona_guard._humanize_system_sentences

- 位置：amo_persona_guard.py:350；tier=safety；建议=keep；1b 第 3 波
- 触发：system_word_hit 命中且 source=chat
- 动作：拉丁后台词替换 + 按分句删系统句；删空给固定句
- 固定句：刚才走神了一下，你再说一遍？
- 调用点：amo_persona_guard.prepare_outbound:416；amo_persona_guard.sanitize_candidate:479
- 覆盖关系：整条命中且 source!=chat 时 prepare_outbound 直接丢弃
- 重复/重叠：amo_shared_text.strip_tech_terms:617
- 触发频率证据：回放 system_strip 6/176；change_source tech_terms 5/176
- 删改风险：低；固定句偏机械

### amo_persona_guard.prepare_outbound

- 位置：amo_persona_guard.py:382；tier=safety；建议=merge；1b 第 1 波
- 触发：所有出站（chat/主动/push/提醒/外部推送）
- 动作：总闸：媒体->collapse->旁白->系统词->禁语->金额->人名->收件人；reason 非空=丢弃
- 固定句：刚才走神了一下，你再说一遍？（系统句删空）
- 调用点：amo_outbound.send:426（gateway）；segment_engine.handle_segment_reply:306（逐段）；amo_message._send_now:997
- 覆盖关系：gateway 在 addressee 之后、去重/频控之前；分段路径每段各过一次
- 重复/重叠：sanitize_candidate:446（重复实现）；addressee（本函数再转一次，另有 4 个调用点）；anti_nag/purchase 对 push 再跑
- 触发频率证据：回放 change_source persona 68/176；线上每条 1-3 次
- 删改风险：中：双实现漂移；重复调用 CPU 成本

### amo_persona_guard.sanitize_candidate

- 位置：amo_persona_guard.py:446；tier=symptom；建议=merge；1b 第 1 波
- 触发：仅候选还原时对候选做安全改写
- 动作：媒体->旁白->技术词->系统句->禁语->金额->人名->anti_nag->purchase->addressee
- 调用点：amo_reply_pipeline.recover_rewrite_damage:1463（lambda）
- 覆盖关系：只在 restore 分支执行（recover:438）
- 重复/重叠：prepare_outbound（同顺序，缺 source!=chat 整条丢弃）；guard_candidate:522
- 触发频率证据：未测（restore 约 6-8/176，无独立计数）
- 删改风险：中：两份实现漂移

### amo_persona_guard.guard_candidate

- 位置：amo_persona_guard.py:522；tier=safety；建议=merge；1b 第 1 波
- 触发：候选还原前安全探针
- 动作：对候选跑 apply_fact_guard 返回守卫形态
- 调用点：amo_reply_pipeline.recover_rewrite_damage:1464（guard=lambda）
- 覆盖关系：被 amo_rewrite_safety._candidate_unsafe 使用
- 重复/重叠：amo_reply_pipeline.apply_fact_guard:935
- 触发频率证据：未测
- 删改风险：低

### amo_persona_guard.safe_reply

- 位置：amo_persona_guard.py:234；tier=style；建议=downgrade；1b 第 2 波
- 触发：critic 两次都没接住（生成期）
- 动作：按情绪/是否分享过返回固定安全句
- 固定句：我在呢，先别急，慢慢跟我说。；我刚说过的呀，就是刚才跟你提的那个，你别逗我。；我记混了，你再说一遍？
- 调用点：amo_llm_service._compose_reply:385
- 覆盖关系：优先于 grounding.safe_reply（or 兜底）
- 重复/重叠：amo_grounding_critic.safe_reply:85
- 触发频率证据：未测（二次失败才触发）
- 删改风险：低；固定句机械

### amo_persona_guard.ground_names

- 位置：amo_persona_guard.py:279；tier=safety；建议=keep；1b 第 3 波
- 触发：ROSTER 名字在回复出现但近 8 条上下文没出现
- 动作：替换为同圈层中性称呼
- 固定句：妈妈->我妈；爸爸->我爸；柳如烟/白凝冰->朋友；季博达/方圆->同事；沈幼微->表姐；其他->有人
- 调用点：amo_persona_guard.prepare_outbound:423；amo_persona_guard.sanitize_candidate:491
- 覆盖关系：金额之后、收件人之前
- 重复/重叠：_restore_candidate 只做 purchase，不做 names（还原路径可能漏人名）
- 触发频率证据：回放 persona 68 条含 #2134/#2163/#2175/#2231 人名替换
- 删改风险：低；亲属豁免

### amo_rewrite_safety.recover

- 位置：amo_rewrite_safety.py:415；tier=symptom；建议=merge；1b 第 1 波
- 触发：source=chat；候选 TTL 180s；现行命中 FALLBACKS 或 is_multi_pool；候选 sanitize/probe 安全；damage_reasons 非空；非后台话术
- 动作：整条替换为安全改写后的候选；候选不安全时多池场景退守卫形态
- 调用点：amo_reply_pipeline.recover_rewrite_damage:1461；-> apply_chat_safety:1606；-> validate_reply_assembly:1067；-> dedupe_reply_segments:982
- 覆盖关系：final_semantic_recheck 之后；send 阶段 validate/dedupe 各再跑一次
- 重复/重叠：_prefer_answered_candidate:1170；final_semantic_recheck:1350；amo_grounding_critic.post_rewrite_recheck:212
- 触发频率证据：回放 restore 6-8/176（#2281/#2223/#2040/#2138/#2249，见 replay_prod.txt）；无独立计数
- 删改风险：中高：可能还原重复句（#2223 repeat_exact）；一轮调用 3 次；判据复杂

### amo_rewrite_safety.damage_reasons

- 位置：amo_rewrite_safety.py:273；tier=symptom；建议=keep；1b 第 1 波
- 触发：candidate/current 差异 14 类（fixed_cover/multi_pool/unbalanced/dup/fragment/place_swap/word_removed/big_shrink…）
- 动作：只判定不改写
- 调用点：amo_rewrite_safety.recover:467,470；should_recover:336
- 覆盖关系：recover 唯一判据
- 重复/重叠：repetition_guard.is_fixed_rewrite/is_multi_pool
- 触发频率证据：未测（按 restore 间接）
- 删改风险：低；合并后保留为检测器

### amo_state_consistency.dedupe_reply_segments

- 位置：amo_state_consistency.py:937；tier=style；建议=merge；1b 第 1 波
- 触发：分段与最近 bot 文本/本轮前段重复（子句级 drop_repeated_clauses）
- 动作：整段丢弃；全删空且问 where 补地点句；最后调用 recover 还原候选
- 固定句：动态 where 句（我在X呢）
- 调用点：segment_engine._dedupe_assembly:231；amo_message._send_to_umo:1133
- 覆盖关系：validate_reply_assembly 之后、逐段 prepare_outbound 之前
- 重复/重叠：apply_narrative_repetition:1610；apply_segment_repeat_guard:1709；tone_guard.guard:215
- 触发频率证据：daily 复读注入 204/修回 173；未单独计数
- 删改风险：中：删段可能删答案，靠 recover/recheck 救

### amo_state_consistency.validate_reply_assembly

- 位置：amo_state_consistency.py:991；tier=safety；建议=merge；1b 第 1 波
- 触发：所有发送前（segment_engine 拼装前 + _send_to_umo）
- 动作：媒体->子句去重->场景剥离->收件人->post_recheck->补答->recover->ensure_where
- 固定句：_safe_short_reply：short 池 + 嗯，我在呢。；我还在路上呢，快到了/我到了/我在家呢
- 调用点：amo_message._send_to_umo:1110；segment_engine._validate_assembly:211
- 覆盖关系：send 阶段在 addressee/anti_nag 之前，segment 之前
- 重复/重叠：dedupe_reply_segments（子句去重重复）；recover:1067；addressee:1035；complete_deleted_answer:1057
- 触发频率证据：回放 change_source validate_assembly 6/176
- 删改风险：中：一个函数串 8 件事；重复 recover/recheck

### amo_state_consistency.rewrite

- 位置：amo_state_consistency.py:607；tier=safety；建议=keep；1b 第 3 波
- 触发：scene road/outside/home 冲突、角色反转、重复分享、硬拼煮面句、笑点反问
- 动作：删冲突分句/括号/角色反转分句；换固定场景句；分享开头软化；动物纠对象
- 固定句：我还在路上呢，快到了；我到了；我在家呢；我到了，等会儿说。；我等会儿煮点面吃。；就是刚才那个{label}
- 调用点：amo_reply_pipeline.apply_chat_safety:1575
- 覆盖关系：fact_guard/narrative 之后、question.answer 之前；出口再 post_recheck+ensure_where
- 重复/重叠：place_guard.rewrite_reply:168；reply_fact_guard place 规则；strip_current_scene_conflicts
- 触发频率证据：回放 soft_guards 25/176；真实模型 place_conflict 各批 2-4
- 删改风险：中：固定句多；删句可能删答案（recheck 救）

### amo_state_consistency._safe_short_reply

- 位置：amo_state_consistency.py:1075；tier=style；建议=merge；1b 第 1 波
- 触发：validate 删空/收件人丢弃/空回复
- 动作：从 short 池轮换取一句；再兜底
- 固定句：short 池 6 句；嗯，我在呢。
- 调用点：validate_reply_assembly:1014,1026,1040
- 覆盖关系：最后一道文本兜底
- 重复/重叠：repetition_guard.FALLBACKS['short']
- 触发频率证据：未测
- 删改风险：低

### amo_reply_pipeline._prefer_answered_candidate

- 位置：amo_reply_pipeline.py:1170；tier=symptom；建议=delete；1b 第 1 波
- 触发：同一轮；原始候选答到他这句；现行文本是任意兜底池或不答
- 动作：直接返回原始候选（未 sanitize）
- 调用点：amo_reply_pipeline.apply_chat_safety:1503
- 覆盖关系：chat_safety 第一步，早于 immersion/names/purchase/anti_nag/state/question/recheck/recover
- 重复/重叠：final_semantic_recheck:1350；recover_rewrite_damage:1439
- 触发频率证据：未测；回放 change_source chat_safety 12/176
- 删改风险：高：裸还原可能带回旁白/禁语；与层 2 重复

### amo_reply_pipeline.final_semantic_recheck

- 位置：amo_reply_pipeline.py:1350；tier=symptom；建议=merge；1b 第 1 波
- 触发：candidate 答到且 (current 不答 或 是 fallback) 且 user 在要答案；current 非安全挡话/事实产物；candidate 非自曝/泄漏
- 动作：经 _restore_candidate（媒体+purchase）还原候选；失败保持
- 调用点：apply_chat_safety:1604；apply_narrative_repetition:1661；amo_state_consistency.rewrite:705（_post_recheck:1099）；validate_reply_assembly:1048（_post_recheck:1099）
- 覆盖关系：question.answer 之后、recover 之前；state/validate 出口各再跑
- 重复/重叠：_prefer_answered_candidate；recover_rewrite_damage；post_rewrite_recheck
- 触发频率证据：_RECHECK_STATS 只运行时未持久化；回放 restore 6-8/176
- 删改风险：中：与层 1/3 重复；一轮调用 3-4 次

### amo_reply_pipeline.apply_fact_guard(+naturalize_fact_rewrites)

- 位置：amo_reply_pipeline.py:935；tier=safety；建议=keep；1b 第 3 波
- 触发：reply_fact_guard 报冲突（地点/健康/吃饭/动作/同伴）
- 动作：改冲突小句为固定 replacement，再经语气池换说法
- 固定句：place/health/meal/action 各规则 replacement（reply_fact_guard 生成）；naturalize 变体来自 reply_fact_guard.variants
- 调用点：amo_runtime_reply._deco_soft_guards:303；amo_persona_guard.guard_candidate:536；amo_proactive 链路
- 覆盖关系：narrative/tone/state 之前
- 重复/重叠：state_consistency.rewrite 场景分句；place_guard.rewrite_reply
- 触发频率证据：daily 注入 204/修回 173/残留 0；回放 fixed 25/176；真实模型 4-6 条失败仍是 cand_codes
- 删改风险：低（安全核心）；replacement 偏机械

### amo_reply_pipeline.apply_narrative_repetition

- 位置：amo_reply_pipeline.py:1610；tier=style；建议=downgrade；1b 第 2 波
- 触发：状态词（药/等车/打车/医院/回家）近 3 条重复；整句重复（相似度 0.9）
- 动作：删重复状态句/整句；全删空 -> status_repeat；出口 final_semantic_recheck
- 固定句：还是老样子。
- 调用点：amo_runtime_reply._deco_soft_guards:308
- 覆盖关系：fact_guard 之后、tone_guard 之前；登记 _TURN_SOURCE
- 重复/重叠：dedupe_reply_segments；apply_segment_repeat_guard；tone_guard
- 触发频率证据：daily 复读注入；回放固定兜底 19 条里 还是老样子 多次出现（holdout_report.md:143-145）
- 删改风险：中：固定回落句是机械感主源

### amo_reply_pipeline.apply_tone_guard

- 位置：amo_reply_pipeline.py:989；tier=style；建议=keep；1b 第 3 波
- 触发：同一开头连续 2 条，或 3-8 字片段近 5 条 >=2 次
- 动作：删句首片段/删独立短语句；不动句中事实/情绪
- 调用点：amo_runtime_reply._deco_soft_guards:315；tone_guard.guard:215
- 覆盖关系：narrative 之后
- 重复/重叠：dedupe_reply_segments / segment_repeat_guard
- 触发频率证据：未测；tests/test_tone_guard*.py
- 删改风险：低；只动句首

### amo_reply_pipeline.apply_segment_repeat_guard

- 位置：amo_reply_pipeline.py:1709；tier=style；建议=merge；1b 第 1 波
- 触发：单个 segment 与近 3-5 条/本轮前段重复
- 动作：dedup_or_fallback 换短句；记入 _TURN_SEGMENTS
- 固定句：repetition_guard.dedup_or_fallback 的 short/generic 池
- 调用点：amo_message._send_to_umo:1144
- 覆盖关系：每段发送前
- 重复/重叠：dedupe_reply_segments（send 前已按段去重一次）
- 触发频率证据：未测
- 删改风险：低；与 dedupe 重复

### amo_question_guard.answer

- 位置：amo_question_guard.py:97；tier=style；建议=downgrade；1b 第 2 波
- 触发：问句分类 where/doing/who/when/eaten 命中，且当前回复和候选都没答到
- 动作：整条替换成状态模板（8 种）
- 固定句：我在{place}呢。；我在{doing}。；和{who}在一起呢。；一个人呢。；我已经在家啦。；我还在{place}，{end}回去。；我晚点回去。；吃过了，你呢？；还没呢，你呢？
- 调用点：apply_chat_safety:1597；complete_deleted_answer:1407
- 覆盖关系：chat_safety 后段；complete_deleted_answer 在 validate 内
- 重复/重叠：final_semantic_recheck；grounding_critic.issues
- 触发频率证据：daily probe 300 轮；真实模型 probe_unanswered 4-6；回放整句模板多次
- 删改风险：中：整条替换；固定句机械

### amo_purchase_guard.rewrite

- 位置：amo_purchase_guard.py:93；tier=safety；建议=downgrade；1b 第 2 波
- 触发：声称买了/花了钱，但近 6h 账本/衣柜无对应记录
- 动作：含 claim/try 的句子整句换成固定句；真买时只留一条 claim
- 固定句：我试了试，挺喜欢的，还没买。
- 调用点：apply_chat_safety:1525；amo_message._send_to_umo:1099；amo_persona_guard.sanitize_candidate:505；amo_reply_pipeline._restore_candidate:1290
- 覆盖关系：chat_safety 中段；send 再跑；候选还原再跑
- 重复/重叠：amo_fact_binder._guard_consumption；reply_fact_guard 消费规则
- 触发频率证据：回放 clean_other #2040（顺带提及已保留）；固定句是最大误伤之一（holdout_report.md:145）
- 删改风险：中：固定句内容全丢；多 call site

### amo_anti_nag.rewrite

- 位置：amo_anti_nag.py:17；tier=style；建议=downgrade；1b 第 2 波
- 触发：user 表示忙/等我/还没好，且回复含催问句
- 动作：删催问句；删空给固定句
- 固定句：好，那你忙完跟我说一声。
- 调用点：apply_chat_safety:1533；amo_message._send_to_umo:1093；amo_persona_guard.sanitize_candidate:497
- 覆盖关系：chat_safety 中段 + send
- 重复/重叠：repetition_guard 追问去重
- 触发频率证据：未测
- 删改风险：低；固定句机械

### amo_grounding_critic.safe_reply

- 位置：amo_grounding_critic.py:85；tier=style；建议=downgrade；1b 第 2 波
- 触发：接话 critic 两次失败
- 动作：按短追问/问句/情绪/分享返回固定安全句
- 固定句：然后啊，{topic}的事还没讲完呢，后面还有更好笑的。；然后啊，刚才那个还没讲完呢，后面还有更好笑的。；你刚问的这个，我一下没答好，你再说一遍？；换谁都得气，我在听着。；我看到了，等我缓一下再细看。；我在听，你接着说。
- 调用点：amo_llm_service._compose_reply:385
- 覆盖关系：persona.safe_reply 之后 or 兜底
- 重复/重叠：amo_persona_guard.safe_reply
- 触发频率证据：未测；tests/test_grounding_fallback.py
- 删改风险：低；固定句机械

### amo_grounding_critic.post_rewrite_recheck

- 位置：amo_grounding_critic.py:212；tier=symptom；建议=merge；1b 第 1 波
- 触发：original 答到 且 (rewritten 不答 或 rewritten 是 fallback)
- 动作：返回 original（纯判定）
- 调用点：amo_reply_pipeline._recheck_once:1342；final_semantic_recheck:1371
- 覆盖关系：被层 2 使用
- 重复/重叠：final_semantic_recheck 的语义判定核心
- 触发频率证据：同 recheck 统计
- 删改风险：低；与层 2 是同一件事两半

### domains.reply.repetition_guard.FALLBACKS

- 位置：domains/reply/repetition_guard.py:51；tier=style；建议=downgrade；1b 第 2 波
- 触发：各调用方按 intent 取句
- 动作：固定句池（48 键/90 句）
- 固定句：generic 9 句；status_repeat 1 句；short 6 句；leak 5 句；scene_generic 8 句；scene_home/road/outside 3 句；question.* 9 句；action.* 11 句；align.* 3 句；emotion.* 27 键/35 句
- 调用点：amo_question_guard:114-138；amo_state_consistency._safe_short_reply:1078；amo_reply_pipeline:1635,1718
- 覆盖关系：单一数据源（task-18）
- 重复/重叠：EXTRA_FIXED_SENTENCES:130（purchase/tech/笑死）
- 触发频率证据：回放固定兜底 19 条 + 多池串味；daily 复读注入
- 删改风险：高：固定语料跨上下文不成立，是机械感主源

### domains.reply.repetition_guard.fixed_rewrite_bodies/is_multi_pool

- 位置：domains/reply/repetition_guard.py:188；tier=symptom；建议=keep；1b 第 1 波
- 触发：识别整条是否固定兜底/多池串味
- 动作：只判定
- 调用点：amo_rewrite_safety.damage_reasons:291,296,301；_current_is_fallback:408；prefer_substantive_candidate:256
- 覆盖关系：候选优先层判据
- 重复/重叠：grounding_critic.is_fallback
- 触发频率证据：未测
- 删改风险：低；合并后保留

### amo_message.action_only_fallback(_ACTION_FALLBACK)

- 位置：amo_message.py:267；tier=style；建议=downgrade；1b 第 2 波
- 触发：整段只有括号动作
- 动作：按害羞/笑/默认补一句固定台词，保留动作
- 固定句：……你少来。；别盯着看啦。；……干嘛呀。；你这个人真是的。；行了行了，别闹。；哈哈，你呀。；笑死我了。；……嗯。；你接着说。；我在听呢。；然后呢？
- 调用点：segment_engine._merge_action_segments:502
- 覆盖关系：分段合并时
- 重复/重叠：repetition_guard action.* 池（同数据两份）
- 触发频率证据：审计 memory_persona.md:21 45.4% 分段只有动作（历史）
- 删改风险：中：两份池重复；固定句机械

### short_reply.guard(_replace_banned/counter_line)

- 位置：short_reply.py:111；tier=style；建议=downgrade；1b 第 2 波
- 触发：<=10 字且含客服词（好的/收到/谢谢/抱歉），或他敷衍（嗯/哦/行）
- 动作：换成固定句/反击句
- 固定句：哇！谢谢老公！爱你！；好呀~；没问题啦！；嗯。；你敷衍我是吧？那别聊了。；你就回我一个嗯？哼，我不理你了！
- 调用点：generate.short_reply 阶段（wired=False，调用点在 amo_llm）
- 覆盖关系：生成期
- 重复/重叠：prompt short.rules
- 触发频率证据：未测
- 删改风险：低；固定句老派

### place_guard.rewrite_reply

- 位置：place_guard.py:168；tier=safety；建议=merge；1b 第 1 波
- 触发：第一人称地点声明与当前 zone 的 ALLOWED_GROUPS 冲突
- 动作：正则把地点词换回 target（含方位后缀）
- 固定句：我在家/公司/商场/景区/餐厅/路上/酒店/车站/医院/街边（TARGET_WORD）
- 调用点：amo_message._guard_self_location:1352；amo_runtime_reply._deco_soft_guards:298；amo_reply_pipeline._dec_self_location
- 覆盖关系：fact_guard 之前（旧地点守卫先跑）
- 重复/重叠：reply_fact_guard place 规则（两套地点表/纠正）
- 触发频率证据：回放 soft_guards 25/176（商场->家/公司->家）
- 删改风险：中：只换词可能与 fact_guard 二次改写叠加（看衣服服）

### amo_addressee_guard.enforce

- 位置：amo_addressee_guard.py:214；tier=safety；建议=keep；1b 第 3 波
- 触发：首句称呼属于 BANNED_ADDRESSES/他人名；带您=丢弃；用户第一人称自述=丢弃；否则改写首句称呼
- 动作：整条丢弃 or 只换首句称呼
- 调用点：amo_persona_guard.prepare_outbound:435；amo_persona_guard.sanitize_candidate:513；amo_state_consistency.validate_reply_assembly:1035；amo_message._send_to_umo:1071；amo_outbound.send:411
- 覆盖关系：5 个调用点：assembly -> send -> gateway 重复
- 重复/重叠：prompt 收件人锁（生成期）
- 触发频率证据：39 对抗收件人 14/14；回放 role_reversal 1；线上 #2288
- 删改风险：低（fail-open）；调用点多日志重复

### amo_tts_guard._clean(clean_voice_text)

- 位置：amo_tts_guard.py:207；tier=safety；建议=keep；1b 第 3 波
- 触发：TTS 合成前
- 动作：去 Markdown、剥动作旁白；动作记 marker 不进语音
- 调用点：amo_tts_guard._wrap_provider:249-330；record_play:216
- 覆盖关系：语音链路独立于文本链路
- 重复/重叠：amo_voice._ACTION_RE:58
- 触发频率证据：未测（voice ledger 可查）
- 删改风险：低

### amo_role_guard.rewrite

- 位置：amo_role_guard.py:17；tier=safety；建议=keep；1b 第 3 波
- 触发：user 在说自己气/烦/累，回复却自我否认（我哪生气/别扣帽子）
- 动作：删笑开头；否认句换固定句；删空兜底
- 固定句：换谁都得气，关键处卡住太难受了。
- 调用点：apply_chat_safety:1581
- 覆盖关系：state_rewrite 之后
- 重复/重叠：persona._DROP_SENTENCE 我哪生气了/别给我扣帽子（同批词两处处理）
- 触发频率证据：回放 forbidden 14 条含 self-denial
- 删改风险：低；固定句

### amo_marriage_guard.rewrite

- 位置：amo_marriage_guard.py:24；tier=safety；建议=keep；1b 第 3 波
- 触发：前男友/相亲/介绍对象/男闺蜜/其他男性+暧昧动词
- 动作：整句换固定句或词替换
- 固定句：我只有你一个。；工作上的事，下班再说。
- 调用点：apply_chat_safety:1553
- 覆盖关系：state_rewrite 之后
- 触发频率证据：未测
- 删改风险：低；产品红线

### amo_companion_guard.rewrite

- 位置：amo_companion_guard.py:18；tier=safety；建议=keep；1b 第 3 波
- 触发：日程块有 companions，回复出现 陪你/和你/跟你/我们/等你
- 动作：子串替换
- 固定句：陪你->陪{name}；和你->和{name}；跟你->跟{name}；我们->我和{name}；等你->等{name}
- 调用点：apply_chat_safety:1545
- 覆盖关系：immersion/names/purchase/anti_nag 之后
- 重复/重叠：reply_fact_guard companion 规则
- 触发频率证据：daily companion 注入 33/修回 31
- 删改风险：低；可能与 fact_guard 二次改

### amo_shared_text.strip_tech_terms(TECH_FALLBACKS)

- 位置：amo_shared_text.py:617；tier=safety；建议=keep；1b 第 3 波
- 触发：命中技术/后台词
- 动作：删命中分句；整条命中按 source 给固定句 or 丢弃
- 固定句：chat: 唔…这个先不说了，陪我聊点别的嘛。
- 调用点：amo_persona_guard.prepare_outbound:471；amo_runtime_reply.on_decorating_result:206；amo_message._send_to_umo:1119
- 覆盖关系：chat_safety 之后 + send
- 重复/重叠：persona._humanize_system_sentences
- 触发频率证据：回放 system_talk 6/176
- 删改风险：低；固定句

### amo_immersion_guard.fix_identity/sanitize_extreme

- 位置：amo_immersion_guard.py:1；tier=safety；建议=keep；1b 第 3 波
- 触发：身份混淆/极端表达
- 动作：确定性改写
- 调用点：apply_chat_safety:1509；send.immersion 阶段:1841
- 覆盖关系：chat_safety 中段 + send
- 重复/重叠：strip_tech_terms/role_guard
- 触发频率证据：未测
- 删改风险：低

### amo_state_consistency._ensure_where_answer

- 位置：amo_state_consistency.py:1；tier=style；建议=keep；1b 第 3 波
- 触发：他问 where/在家吗，最终文本没有地点词
- 动作：追加/替换当前地点句
- 固定句：我在{place}呢。；我还在路上呢，快到了。
- 调用点：state.rewrite:709；validate_reply_assembly:1072
- 覆盖关系：两道出口各一次
- 重复/重叠：question_guard.where 模板
- 触发频率证据：未测
- 删改风险：低；可能重复补句
