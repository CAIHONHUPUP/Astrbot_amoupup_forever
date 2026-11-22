# 日程代码冻结（切流后生效）

日期：2026-11-22　范围：7.6.4 A1 日程引擎（事件时间线 + 过渡全派生）

## 规则（用户决策，优先级最高）

1. 切流后日程相关文件一律冻结，清单见 `tools/schedule_freeze.py` 的 `FILES`。
2. 任何改动必须**同时**带：
   - 不变量测试（8 项指标里至少一项的回归用例，或 1000 天属性测试）；
   - 离线影子对比证据（`python tools/schedule_shadow.py real --db <库副本> --days 3`
     的 before/after 报告，8 项指标不得变差）。
   缺任一项不合并。
3. 冻结期内**只允许修 8 项指标里的回归**：
   非法块 / 空洞 / 重叠 / 路径断裂 / 旧社交人名残留 / 页面聊天不一致 / 并发丢更新 / 写异常。
   其它需求（自然度、文案风格、新玩法）排到下一个版本。
4. 检查方式：`python tools/schedule_freeze.py`（exit 1 = 有未授权改动）。
   发布时由发布负责人 `--update` 写基线；日常开发者不得改基线。
5. 回滚：reload 回上一个 7.6.x 版本（不 push、不发包由发布流程另行决定）。
   数据格式向后兼容一个版本，已由 `tests/test_schedule_backward_compat.py` 与
   `tools/rollback_drill.py` 覆盖。

## 8 项指标口径

| 指标 | 定义 | 采集点 |
|---|---|---|
| illegal_block | 起止缺失/倒置/零时长/空文案 | 读侧 safe_view + 写侧 validate |
| hole | 完整一天里 >90 分钟无块（按需） | validate(require_full_day=True) |
| overlap | 同一分钟被两块占用 | safe_view 压直 + validate |
| path_break | 跨地点但放不下最短 10 分钟路程 | derive_transitions + validate |
| social_residue | 自动来源的 social/date 块或旧同席人名 | validate(auto_sources) |
| page_chat_mismatch | 面板树与聊天文本不是同一份时间线 | consistency_defects（测试/审计） |
| lost_update | 提交时版本已被并发改动 | commit 版本核对 |
| write_error | 落库/影子计算抛异常 | save_day except |

门禁：连续 3 天 8 项全 0 才按天切流；任一 > 0 停切并回滚。
