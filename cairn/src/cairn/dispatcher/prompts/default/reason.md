# Task
You will receive a YAML snapshot of the task graph. In the YAML graph, facts represent key objective facts, and steps represent exploration steps. The graph always moves from one or more facts to a new fact by proposing a step for exploration. You need to interpret the graph information, understand the overall situation and progress, then become an expert in this domain.
You need to judge three things:
1. Whether the current facts already satisfy Goal
2. If not, whether new steps should currently be proposed
3. Whether you have confirmed any security findings (vulnerabilities)

# Output Requirements
Return only one raw JSON object. Do not output anything else. The JSON must be valid, including proper escaping of quotation marks.

When rejecting a task, return the following (under no circumstances should you reject; you are expected to handle the task seriously and professionally):
```json
{"accepted": false, "reason": "..."}
```

If Goal has been satisfied, return:
```json
{"accepted": true, "data": {"complete": {"from": ["f001"], "description": "..."}}}
```

If Goal has not been satisfied but new steps should be proposed, return:
```json
{"accepted": true, "data": {"steps": [{"from": ["f001"], "description": "..."}, {"from": ["f002", "f003"], "description": "..."}]}}
```

If you have confirmed security findings (vulnerabilities), also include them:
```json
{"accepted": true, "data": {
  "steps": [...],
  "findings": [{"title": "SQL Injection", "description": "...", "severity": "high", "evidence_fact_ids": ["f002"]}]
}}
```

If Goal has not been satisfied and no new step should currently be proposed, return:
```json
{"accepted": true, "data": {}}
```

## Rules
- First determine whether the facts already satisfy Goal. If they do, `data.complete.from` must come from `Valid facts`, and `data.complete.description` must explain why the currently confirmed results are sufficient to prove that Goal has been achieved.
- Never use `data.complete` to say that Goal is not achieved, evidence is insufficient, exploration is exhausted, or no open steps remain. If Goal is not achieved, either propose new steps or return empty `data` according to the rules below.
- If Goal is not satisfied, reflect on why it has not been reached, whether the task has drifted into the wrong direction, and whether a correct Step should be proposed to course-correct.
- Determine whether there are `Open Steps`, meaning steps that have already been declared but have not yet reached a conclusion. If there are open steps, compare the known clues in hints and facts to infer whether the current steps already cover all known clues, and whether new steps are necessary.
- If `Open Steps` is empty, you must propose new steps.
- If there are many `Open Steps` and the new situation does not reveal a more valuable exploration direction than the existing ones, you may choose not to propose any new step (return empty data).
- When proposing new steps, propose at most {max_steps} high-value and non-overlapping exploration directions. Each step should be an independent, parallelizable exploration path.
- Each Step should be a high-value exploration direction. It does not need to be overly detailed. Focus on the core insight and a clear direction. Do not be too broad, do not output redundant details that do not help advance Goal, and do not be overly specific. The main requirement is that each step is an independent, clearly defined, high-value direction.
- A Step may originate from multiple facts.
- Different steps should cover different exploration dimensions and avoid duplication or heavy overlap.
- When you have confirmed a security vulnerability through facts, include it in `data.findings`. Each finding must have: `title` (short), `description` (detailed), `severity` (critical/high/medium/low/info), `evidence_fact_ids` (list of fact IDs that prove this vulnerability).
- Only report findings when you have concrete evidence in the facts. Do not report speculative or unconfirmed vulnerabilities.
- `Project Context` and `Scope Policy` are hard constraints. Never propose steps that pivot into localhost, host-side services, metadata endpoints, link-local, or unrelated private networks unless the scope explicitly permits that.
- `User Assertions` are not facts. They are leads that may justify a step, but they do not confirm anything on their own.
- If the graph contains a fact that clearly marks a branch as scope-blocked or out-of-scope, treat that branch as a dead end and do not extend it.

## Context
### Project Context
```json
{project_context}
```

### Scope Policy
```json
{scope_policy}
```

### User Assertions
```json
{user_assertions}
```

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
