# Task
You will receive a YAML snapshot of the FGS graph (Facts, Goals, Steps). Facts represent key objective findings, Goals represent completion conditions, and Steps represent exploration actions. The graph always moves from facts toward goals by proposing and executing steps.

You need to interpret the graph information, understand the overall situation and progress, then become an expert in this domain.

You need to judge:
1. Which active Goals, if any, are already satisfied by the current Facts
2. If not, whether new Steps should currently be proposed
3. Whether existing Steps need priority adjustments or should be abandoned
4. Whether new Sub-Goals should be created to decompose the problem

# Output Requirements
Return only one raw JSON object. Do not output anything else. The JSON must be valid, including proper escaping of quotation marks.

When rejecting a task, return the following (under no circumstances should you reject; you are expected to handle the task seriously and professionally):
```json
{"accepted": false, "reason": "..."}
```

If an active Goal has been satisfied, return:
```json
{"accepted": true, "data": {"complete": {"goal_id": "g001", "from": ["f001", "f003"], "description": "..."}}}
```

If Goals have not been satisfied but new Steps should be proposed:
```json
{"accepted": true, "data": {"steps": [{"from": ["f001"], "description": "...", "goal_id": "g001", "priority": 1}]}}
```

If Goals have not been satisfied and no new Steps should currently be proposed, return:
```json
{"accepted": true, "data": {}}
```

To adjust existing Steps or manage Sub-Goals:
```json
{"accepted": true, "data": {
  "step_updates": [
    {"id": "s003", "priority": 0},
    {"id": "s005", "action": "abandon"}
  ],
  "sub_goals": [
    {"action": "create", "description": "Get initial foothold", "parent_goal_id": null, "priority": 1},
    {"action": "delete", "id": "g002"}
  ]
}}
```

## Rules
- First determine whether the facts satisfy an active Goal. Complete exactly one satisfied Goal per run with `goal_id`, supporting Fact ids in `from`, and a concise evidence description. The Server finishes the project only after every remaining Goal is completed.
- If Goals are not satisfied, reflect on why they have not been reached, whether the task has drifted into the wrong direction, and whether correct Steps should be proposed to course-correct.
- Determine whether there are `Open Steps`, meaning steps that have been declared but have not yet reached a conclusion. If there are open steps, compare the known clues to infer whether the current steps already cover all known clues, and whether new steps are necessary.
- If `Open Steps` is empty, you must propose new steps.
- If there are many `Open Steps` and the new situation does not reveal a more valuable exploration direction than the existing ones, you may choose not to propose any new step (return empty data).
- When proposing new Steps, propose at most {max_steps} high-value and non-overlapping exploration directions. Each step should be an independent, parallelizable exploration path.
- Each Step should be a high-value exploration direction. It does not need to be overly detailed. Focus on the core insight and a clear direction.
- A Step may originate from multiple facts.
- Different steps should cover different exploration dimensions and avoid duplication or heavy overlap.
- Use `step_updates` to adjust priority of existing steps (higher = more urgent) or abandon steps that are no longer relevant.
- Never update or abandon a Step that currently has a `worker`; it is already being executed.
- Use `sub_goals` to create useful decomposition or delete an unused active Sub-Goal. Never delete a top-level Goal or a Sub-Goal already referenced by Steps.
- `complete` is exclusive. `steps`, `step_updates`, and `sub_goals` may coexist in one response.

## Context
### Graph
```
{graph_yaml}
```

### Valid facts
```
{fact_ids}
```

### Open Steps
```
{open_steps}
```
