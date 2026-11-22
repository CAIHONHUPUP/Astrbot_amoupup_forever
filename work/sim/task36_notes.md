# task-36 日常对话残留失败修复（2026-11-22）

## 口径
- 模拟器：work/sim/run_daily.py（隔离模拟 provider + 生产发送前链路），同一份独立 checker（work/sim/daily_checks.py，未改）。
- 目标：单轮 ≥95%、降低组失败，优先 probe_unanswered / action_conflict；不按样例堆正则。

## 指标 before → after
- 轮：581/614 (94.63%) [会话开始报表] → 582/614 (94.79%) [改前实测] → **614/614 (100%)**
- 组：28/50 → 29/50 → **50/50**
- 失败分类：probe_unanswered 31、action_conflict 2 → **0 / 0**
- 漂移：injected 204，repaired 143 → **173**，residual 30 → **0**，guard_introduced 2 → **0**
- 对照组：118 轮 0 失败；矛盾轮 0；干净探针未答 2 → 0
- 确定性签名 548284df2e3391d7（连跑两次一致）；生产库运行前后指纹一致、AmoDB/sqlite 拦截 0。

## 改动（见 work/sim/task36_fix.diff）
1. amo_question_guard.py：意图词表按「问的是哪类事实」覆盖口语问法（回不回来/到公司了吗/旁边有人吗/偷懒），并加「第三人称作主语不算问她」（我同事摸鱼 ≠ 问她干嘛）。
2. amo_state_consistency.py：状态问句（在干嘛/在哪/几点回/吃了吗…）不参与「分享接话」，修掉把正常回复改写成「就是事的事，…」的 guard_introduced；认不出对象时说「就是刚才那件事」；分段去重保留答案句；新增「到X了吗」地点问句口径；validate 出口走 pipeline 补答。
3. reply_fact_guard.py：进行式工作断言（还在改方案/在赶方案/对着电脑改）在 sleep/health/rest/meal/play 下也按当前状态改口，近过去（刚开完会）保留；「我在家这边躲雨」换地点保留方位词（_place_orient_variants），不再删成「我在躲雨」。
4. amo_reply_pipeline.py：narrative_view 没有 current 时回落 state.current_activity（事实守卫不再空转）；when 答案词表补「在家」；后置复检/补答统一用本轮登记问题（send 阶段旧 user_text 不再误判）；新增 complete_deleted_answer 出口补答。
5. work/sim/sim_env.py：聊天回复发送 source=chat（与生产 _send_now / imm_harness 口径一致）。不传会 fail-closed 成 push，出口人设守卫把「写代码」这类词整条丢弃。
6. work/sim/run_daily.py：新增 5 条回归 check（question_intent_coverage / share_context_not_hijacked / work_present_claim_while_sleeping / place_orient_keeps_answer / segment_dedupe_keeps_answer）+ 更新 root_causes 文案。
7. tests/test_reply_fact_guard.py：sleep 下进行式工作断言改为「要改口」，近过去仍保留（同步行为变化）。

## 验证
- 相关 pytest（30 个 import 守卫模块的测试文件）：498 passed / 143 subtests（含 lint/domain_boundaries/narrative_check/immersion_p0）。
- test_p2_perf 3 例失败为改前既有（已用改前快照复跑确认）；test_architecture_ratchet 的 state_consistency→amo_scene_ledger 也是改前既有（同一快照复跑一致，本轮未新增依赖边）。
- 仍 FAIL 的 context checks（未修，勿当回归）：two_readers_agree、narrative_place_cache_stale。
- 敏感性：若 harness 不改 source=chat（仍按 push），同一套守卫修复为 613/614 (99.84%)、49/50 组，唯一失败是对照组 G20/t2 空回复（push 口径丢系统词）。
