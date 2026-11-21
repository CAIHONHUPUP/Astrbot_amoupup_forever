# 人设包与人格档案

## 文件位置

- 默认包：`persona_packs/gentle-playful.json`（随插件发布，只读参考）
- 每个安装实例：`plugin_data/Astrbot_amoupup_forever/persona_profiles/`
  - `<id>/pack.json`：基础人设，切换不覆盖
  - `<id>/state.json`：进化语料、权重、候选、快照
  - `<id>/snapshots/`：每次进化前的备份
- 当前人格：kv `persona_active`

## 人设包结构

```json
{
  "schema_version": 1,
  "id": "gentle-playful",
  "name": "温柔娇泼",
  "card": {
    "core": "认定你、护短、要强、怕失去",
    "address": {"user": "老公", "self": "我"},
    "registers": {"partner_safe": "撒娇、使唤、泼辣"},
    "language": {"openers": ["老公，"], "rules": ["日常请求直接说"]},
    "money": "不拜金、不比较、不索取",
    "public": "可叫老公，不性暗示",
    "forbidden": ["窝囊废", "你很烦"]
  },
  "corpus": [
    {"scene": "sick", "mood": "示弱", "text": "能不能给我倒杯热水"}
  ],
  "rules": {"refusal": "按理由分档", "public_no_sex_hint": true, "repeat_guard": 5}
}
```

- `card` 不参与学习，只读；
- `corpus` 是 few-shot，只学语气，禁止逐字复用；
- 每场景最多 5 条、总 100 条。

## 人设来源（persona_source）

- `pack`（默认）：只用本插件人设包，**不读取 AstrBot 人格提示词**，避免「通用AI助手/小财迷」混入；
- `merge`：AstrBot 人格作为补充，冲突时以人设包为准；
- `astrbot`：沿用旧行为，AstrBot 人格优先。

AstrBot 的人格**名字**仍会同步用于称呼，不受此开关影响。

## 命令

- `/人设`：查看当前人格、可选人格、候选数
- `/人设 切换 <id>`：切换；旧人格状态保留
- `/人设 候选`：看待批准的新语料
- `/人设 通过 <序号>`：批准进当前人格

## 进化规则

- 只让当前人格进化，切走冻结、切回继续；
- 新语料先入候选，批准后才生效；
- 每次写状态前生成 snapshot，可回滚；
- 基础卡/禁语不由学习修改。

## 多安装者

每个人安装一份插件、一份 `plugin_data`，人设/语料/记忆互不影响；不按聊天用户切换人格。

## 记忆

- 当天缓冲：12 条 / 600 字 / 36 小时；
- 大日记按天折叠 60-80 字 digest；
- 聊天注入最近 3 天 digest；
- 关系记忆在数据插件统一库，切换人格不重置。
