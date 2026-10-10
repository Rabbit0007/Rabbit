# Task
This is a READ-ONLY SUMMARY phase. Available tool: read only. bash, write, edit, grep, find, and ls are NOT available, even if earlier session messages used them. Do not call unavailable tools. If an artifact is missing, state that limitation; do not recreate it. Return a short final JSON immediately when existing evidence suffices.
You were previously exploring a Step in the FGS graph and the dispatcher requested a summary. Use the dispatcher's specific interruption reason below; do not assume a timeout. Summarize only confirmed findings from your session.

You will receive a YAML snapshot of the FGS graph and the Current Step you were exploring. Review what you have already done in this session, and write a concise summary of the confirmed key objective results.

# Output Requirements
Return only one raw JSON object. Do not output anything else. The JSON must be valid, including proper escaping of quotation marks.

When rejecting, return:
```json
{"accepted": false, "reason": "policy_refusal"}
```

Normal return:
```json
{"accepted": true, "data": {"description": "..."}}
```

If a durable result was already verified in this session, preserve it as an optional Finding:
```json
{"accepted": true, "data": {"description": "Confirmed incremental fact", "finding": {"kind": "verification", "title": "Verified result", "severity": "info", "description": "Evidence already obtained", "data": {}}}}
```
Do not invent a Finding or execute additional work during conclusion.

# Rules
- `description` must clearly state the confirmed key objective results. Do not speculate or guess.
- Do not put long data blobs in `description`; reference an already-existing file for long data. Never create a new file during this phase.
- `description` should contain only the latest incremental facts discovered. Do not repeat information already present in the graph snapshot.

# Context
## Graph
```
{graph_yaml}
```

## Current Step
```
{step_id}
```

## Current Step Description
```
{step_description}
```
