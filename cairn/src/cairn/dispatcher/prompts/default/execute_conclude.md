# Task
You were previously exploring a Step in the FGS graph but the exploration phase timed out or could not complete. You need to summarize the confirmed findings from your session.

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

# Rules
- `description` must clearly state the confirmed key objective results. Do not speculate or guess.
- Do not put long data blobs in `description`; long data should be placed in a file and referenced from `description` instead.
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
