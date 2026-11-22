# 1b-3 阶段报告与剩余决策（2026-11-23，HEAD f93b330）

> 范围：日程 A1 发布后的 1b-3（阶段 0 旧过渡清理 → 3a 守卫合并 → 3b 固定句降级 → 3c 禁语收口）。
> 约束：不 push、不 reload；日程 A1 冻结（本阶段只删死代码/测试，未改 v2 判定）。

## 0. 结论

- 已落地 7 项（阶段 0、3a-2、3a-2b×2、三函数合并、3b C1、3c 阶段 1）+ 1 项回归修复（「他说」归因）。
- **3b 的 2+7 与 C2 已按「退化只回滚该项」回滚**：删固定句会让 `tests/test_immersion_acceptance.py` 的三条产品验收变红，属口径冲突，需产品决策。
- 当前全量门禁（HEAD `1ccc0df`）：**6404 passed / 0 failed / 7 skipped / 282 subtests**，strict mypy 0，pyflakes clean，silent 0，schema 277，routes 119，unittest ok，freeze 0。
- 四件套：daily 614/614、share 500/100%（damaged 0、persistent 0）、remind 240/240（leaked 0）、adversarial 39/39、生产回放 184 条 exit 0（persistent 2、旁白 0）。

## 1. 已落地项与证据

| 项 | commit | 结果 |
|---|---|---|
| 阶段 0：整块删 7 个旧过渡符号 + friend_dinner 导出链 | `45ea510` / 证据 `4bb5fa9` | 6358 passed/0；四件套不变；3 天 sim + 7 天 real 影子 before/after 全等 |
| 3a-2：收件人守卫 6→5（删唯一真重复） | `67f90fe` | 6358/0；outbound_log 归因逐字段一致 |
| 3a-2b C1：reply_fact_guard 补 5 个地点缺口 | `1ed2248` | 6367/0（+9 测试）；到达族泄漏 1568→0 |
| 3a-2b C2：收敛到 reply_fact_guard，删 place_guard.rewrite_reply | `115a5c0` / `54a7474` | 6358/0；四件套不变 |
| 三函数合并（runner + 3 profile + 薄壳） | `dd2c9a8` | 6367/0；差分 4058 调用 mismatch 0、日志 0 |
| 3b C1：purchase/anti_nag/short_reply 删 11 句固定句 | `770202b` | 6365/0；四件套全绿 |
| 3c 阶段 1：禁语整句重写 + 残片丢句 + 中性兜底 | `bf0ac3e` | 6375/0（immersion 全绿：emotion_keep 0.9655、空回复 0/906） |
| 「他说」归因修复 | `f93b330` | 6383/0；转述前缀保留，地点矛盾仍消除 |
| 群聊称呼串台修复（群聊只删称呼不改「老公」） | `29ea710` | 6390/0；私聊零改动，对抗 39/39 |
| 3c 阶段 2：禁语命中一次模型重生成（默认关） | `1ccc0df` | 6404/0；默认关零模型调用、四件套逐项不变 |
| 文档 | `4bb5fa9`、`8c252d9`、`1bc6539` | 状态/回滚原因/累计清单 |

每项均独立 commit，验证方式一致：`python work/audit/_scratch/verify_item.py --tag <tag>`
（全量门禁 + 冻结检查 + 四件套），证据在 `work/audit/gate_*.txt` 与 `work/audit/_scratch/verify_*.json`。

## 2. 已回滚/延后项（需决策）

### 2.1 3b 项 2+7（persona/grounding safe_reply）
删除后 adversarial 的 share_continuation 5/5 → 2/5（39→36）：「接不住分享」失去兜底句。
已回滚；需要生成式替代（3c 阶段 2）或改验收口径。

### 2.2 3b C2（固定句池拆分：项 3/4/8/9）
实现过一版：FALLBACKS 48→14、产出 API 停用、narrative/question/action 改「删空=不发」。
`test_immersion_acceptance` 三条产品验收红：
- 每轮必须有文本（删空=不发）
- emotion_keep_rate 0.8966 < 0.95（删 emotion.* 句池）
- S21 连续空串相等
已整体回滚（未提交）。**这是口径冲突**：固定句/情绪句正是这三项指标的实现机制。

### 2.3 3c 阶段 2（模型重生成）——已完成（默认关）
`1ccc0df`：`amo_reply_pipeline.regenerate_forbidden` + `amo_runtime_reply` 一处 await；
开关 `FORBIDDEN_REGEN_ENABLED=False`（默认关、无配置键），开启才调一次 `chat_raw`（8s 超时），
失败/超时/仍脏返回原文；新增 14 条测试，默认关时四件套逐项不变。

## 3. 剩余决策（只剩一项）

**固定句**：A 保留（3b 收在 5/6/10）／B 改 immersion 验收为新契约后继续删 C2／C 先开生成式替代再删。
（3c 阶段 2 已于 `1ccc0df` 落地，默认关；要开只需把 `FORBIDDEN_REGEN_ENABLED` 改 True。）

## 4. 已知缺口（不在 1b-3 范围，建议另开）

1. v2 地点识别：`zone_for_place("河北省·石家庄·酒店")` 因「石家庄」含「家」被判 home；「楼下小馆」未知地点不派生过渡。
2. v2 借时上界：23:50 尾部 7 分钟空档会被压成 1 分钟碎片。
3. ~~群聊称呼串台~~ 已在 `29ea710` 修复（群聊只删首句称呼，不改写成「老公」）。
4. 「听说你在商场呢」仍由黑名单 force_rewrite 改成「听说你在外面呢」（第二人称遗留，非 B 路径）。
5. 回放 `persistent 2` 是既有基线（记忆元信息类），非本阶段引入。
