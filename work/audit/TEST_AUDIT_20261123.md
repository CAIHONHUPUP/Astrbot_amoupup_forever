# 测试自查报告（2026-11-23）

口径：tests/ 下约 494 个 test_*.py、6443 个用例。三个角度：逐字节重复的用例体、源码形状断言、目标已删的断言。

## 结论

**没有 150–400 个可删的空转用例**——6000+ 是产品面宽度（222 个模块、日程/日记/记忆/钱包/衣橱/照片/社交/LLM/API），不是守卫数量堆出来的。
AST 逐函数比对：**只有 8 对（16 个）用例体完全相同**；其余相似断言测不同输入/不同层，不能互相替代。

## 已删（8 个，每对保留一份）

| 删除 | 等价副本 |
|---|---|
| test_timeline.py::test_block_id_changes_with_content_or_time | test_block_diary.py::test_content_or_time_change_changes_id |
| test_shared_core.py::test_lark_without_ids_falls_back | test_lark_quote.py::test_nothing_available |
| test_reply_pipeline.py::test_ab_photo_promise | test_reply_pipeline.py::test_ab_generate_photo_promise |
| test_reply_pipeline.py::test_ab_plain | test_reply_pipeline.py::test_ab_generate_plain |
| test_shared_split.py::test_new_modules_have_no_silent_except | test_runtime_split.py::test_new_modules_have_no_silent_except |
| test_shared_split.py::test_each_new_module_under_1500_lines | test_runtime_split.py::test_each_new_module_under_1500_lines |
| test_schedule_block_restore_b3d.py::test_restore_rejects_overlap_even_with_full_fields | test_schedule_block_restore_b3c.py::test_restore_rejects_overlap |
| test_wardrobe_split.py::test_no_bare_silent_except | test_schedule_engine_split.py::test_no_bare_silent_except |

验证：受影响 9 文件 173 passed / 9 subtests；用例数 6451 → 6443；diff 仅 -46 行（未改行尾）。

## 保留（观察名单）

- 源码形状断言（inspect/co_code/md5/golden 顺序）29 个文件：架构棘轮与契约，删了会失去防回流。
- 38 处「符号不存在」断言：这次新增/保留的反向守卫，防模板回流，必须留。

## 建议

1. 新测试优先行为断言，少写源码形状断言；
2. 每季度用 work/audit/_scratch/find_dup_tests.py 复跑，只删逐字节相同的；
3. 提速不靠删用例：按里程碑跑全量 + xdist 试点（只读子集验证后启用）。
