# P0-A 修复开发文档（GAP-01 ~ GAP-05）

> 依据：PRD v0.5（REQ-01~08）、《实现差距与修复验收清单 v0.5》。本阶段只修核心闭环 5 项，不做 P0-B/P1 项。

## 一、阶段目标

修通核心闭环，使：教练能做相关追问（GAP-01）、跟读结果影响教学（GAP-02）、完成判断有据且分两个子目标（GAP-03）、任何结束路径都统一收尾且回顾非空（GAP-04）、回顾真实且区分空/失败（GAP-05）。

## 二、数据模型变更

- `Session` 增加 `subgoals`（Text，JSON，默认 `{}`）：两个子目标状态与证据。
  ```json
  {"intro": {"status":"independent","evidence":["turn_id"]},
   "followup": {"status":"","evidence":[]}}
  ```
- `Evaluation` 增加 `subgoal`（String）、`input_type`（String）、`question`（Text）：记录每条评估对应哪个子目标、输入方式、被回答的题目。

开发库 schema 变更：删除重建 `data/ai_speaking.db`（仅含 mock 测试数据）。

## 三、各 GAP 修复方案

### GAP-01 教练上下文（REQ-01/03）

`coach.decide(context)` 的 context 从 `{eval_result, rechecked, practiced}` 扩展为：
- `goal`、`difficulty`、`level`；
- `current_question`（最近一次教练提问，取自最后一条 coach 回合）；
- `current_answer`、`input_type`、`hint_used`；
- `eval_result`、`subgoal`；
- `practice`（跟读结果，见 GAP-02）；
- `history`（最近 5 轮双方消息，含 who/text）；
- `rechecked`、`remaining_adjust`（剩余教学调整次数）。

Mock 教练：仍确定性，但追问时引用用户原话（如"你说你做过 X，那其中最难的是什么"）。DashScope 教练：prompt 明确"追问必须基于用户实际说的内容，不编造"。

### GAP-02 跟读结果影响教学（REQ-04）

`submit_practice` 后，把 `{score, valid, reference_sentence}` 传入 `coach.decide`（不再只传 `practice_done`）。决策与状态迁移一致：
- 有效低分 → 针对性帮助/可选重试；
- 有效正常 → 继续原目标；
- 无效/无分数 → 明确"不可评估"，不当低分也不当通过。

### GAP-03 完成判断与证据校验（REQ-02/06）

- 目标拆两个子目标：`intro`（介绍一项经历）、`followup`（回答相关追问）。
- 评估输入增加 `question`、`input_type`、`hint_used`、`answer`、`subgoal`。
- 评估器分别判断当前子目标；两个子目标均 `independent` 才达成目标。
- 证据校验：`evidence` 中的 turn id 必须存在且属于当前会话，否则不形成完成声明。
- Mock 评估器：基于内容判断（含"I have/experience"且非提示→independent 等），不只看长度。

### GAP-04 统一收尾（REQ-07）

抽出幂等的 `finalize_session(db, session)`：生成回顾 + 保存 + 按记忆开关生成候选记忆。
- 自动达标（sub_goal 均独立）→ 调用 finalize，落结束原因 `goal_reached`。
- `end` 接口：未结束时执行 finalize；已结束但 `review` 为空时补生成（幂等，不重复记忆）。

### GAP-05 回顾真实性（REQ-08）

`review.py`：
- 改进建议按原句去重，不同原句各自证据；
- 无可靠改进项时不生成模板建议，返回 `improvements: []` 且 `diagnosis: "无可靠改进项"`；
- 结束原因/目标状态/回顾状态分开；空结果与"完成得不错"区分。
前端：回顾区对"无改进项"显示"本次未生成改进建议"，不再显示"完成得不错"。

## 四、测试要求

1. 既有 39 个测试不删除，因需求变化需更新断言时记录理由。
2. 新增：
   - 教练 context 契约测试：捕获入参，断言含 current_answer/history/question。
   - 两个子目标：先 intro 后 followup，均独立才 ENDED；只答 intro 不结束。
   - 提示/复测：提示下完成不判独立；复测独立后才达成。
   - 自动 ENDED 后 end 返回非空回顾；重复 end 幂等。
   - 回顾：同原句去重；无改进项返回空 + diagnosis。

## 五、验收清单

1. 两个不同经历，教练追问各自相关。
2. 跟读低分/高分/无效分别得到不同教学动作，结果卡持续可见。
3. 提示复述/无关长回答不判独立完成；两个子目标独立才算达成。
4. 自动达标后回顾非空；end 幂等不重复生成。
5. 无改进项时前端不显示"完成得不错"。

## 六、风险与回退

- 子目标状态机改动较大：保留 mock 与 real 双路径，real 未验证部分标"待验"。
- schema 重建会清空 mock 测试历史，属可接受。
