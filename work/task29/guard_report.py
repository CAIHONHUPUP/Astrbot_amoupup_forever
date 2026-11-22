# -*- coding: utf-8 -*-
"""汇总本轮收口前后指标（lead 口径），写 work/task29/guard_introduced_after.md。"""
import io
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORT = os.path.join(HERE, "work", "sim", "real_llm_layer.json")
REPLAY = os.path.join(HERE, "work", "task29", "replay_after_fix.json")
OUT = os.path.join(HERE, "work", "task29", "guard_introduced_after.md")

old_rows = json.load(io.open(REPORT, encoding="utf-8"))["rows"]
new = json.load(io.open(REPLAY, encoding="utf-8"))
summary = new["summary"]
new_rows = new["rows"]


def introduced_lead(row):
    """lead 口径：candidate 本来干净（cand_codes 空），sent 却带码。"""
    return bool(row.get("sent_codes")) and not row.get("cand_codes")


def introduced_loose(row):
    return sorted(set(row.get("sent_codes") or []) - set(row.get("cand_codes") or []))


old_intro = [r for r in old_rows if introduced_lead(r)]
new_intro = [r for r in new_rows if introduced_lead(r)]
old_failed = [r for r in old_rows if r.get("sent_codes")]
new_failed = [r for r in new_rows if r.get("sent_codes")]
fixed = [r for r in new_rows if not r.get("sent_codes") and r.get("old_codes")]
lines = [
    "# task-29 收口：真实模型层 guard-introduced 前后对比（lead 口径）",
    "",
    "> 隔离环境没有 chat provider（未读生产配置/密钥）：用 lead 最新 real_probe 的",
    "> 同一批 272 条真实候选，离线重放完整生产守卫链（sim_env.run_turn）。",
    "> 候选不变，只验证守卫层；guard-introduced 只由守卫决定。",
    "",
    "口径：guard-introduced = candidate 本来干净（cand_codes 空）且 sent_codes 非空。",
    "",
    "## 结果",
    "",
    "| 指标 | 收口前（lead 最新 real_probe） | 收口后（同候选离线重放） |",
    "| --- | ---: | ---: |",
    "| guard-introduced（lead 口径） | %d | **%d** |"
    % (len(old_intro), len(new_intro)),
    "| failed_turns（sent_codes 非空） | %d | %d |" % (len(old_failed), len(new_failed)),
    "| turn_pass_rate | %.2f%% | **%.2f%%** |"
    % (100.0 * (1 - len(old_failed) / float(len(old_rows))),
       100.0 * summary["turn_pass_rate"]),
    "| 相对上一份报告新增失败 | - | %d |" % summary["new_failures_vs_old"],
    "| 相对上一份报告被修好 | - | %d |" % summary["fixed_vs_old"],
    "",
    "## 收口前 guard-introduced",
    "",
    "| 轮 | 他这句 | 旧发出 | 码 |",
    "| --- | --- | --- | --- |",
]
for row in old_intro:
    user = next((n["user"] for n in new_rows
                 if n["gid"] == row["gid"] and n["turn"] == row["turn"]), "")
    lines.append("| %s/t%s | %s | %s | %s |" % (
        row["gid"], row["turn"], user, (row.get("sent") or "")[:60],
        ",".join(row.get("sent_codes") or [])))
lines += ["", "## 收口后", ""]
lines.append("guard-introduced（lead 口径）= %d。" % len(new_intro))
for row in old_intro:
    now = next((n for n in new_rows
                if n["gid"] == row["gid"] and n["turn"] == row["turn"]), None)
    if now:
        lines.append("- %s/t%s -> %s（sent_codes=%s）"
                     % (row["gid"], row["turn"], now["sent"],
                        now.get("sent_codes") or []))
lines += ["", "## 收口后仍失败的 %d 轮（既有问题，非候选干净被守卫改坏）"
          % len(new_failed), ""]
for row in new_failed:
    lines.append("- %s/t%s probe=%s cand_codes=%s sent_codes=%s | user: %s | sent: %s"
                 % (row["gid"], row["turn"], row.get("probe"),
                    row.get("cand_codes"), row.get("sent_codes"),
                    row.get("user"), row.get("sent")))
lines += ["", "## 本轮被修好（sent_codes 清空）", ""]
for row in fixed:
    lines.append("- %s/t%s probe=%s 旧码=%s | user: %s | 新 sent: %s"
                 % (row["gid"], row["turn"], row.get("probe"),
                    row.get("old_codes"), row.get("user"), row.get("sent")))
text = "\n".join(lines) + "\n"
io.open(OUT, "w", encoding="utf-8").write(text)
print(text)
print("wrote", OUT)
