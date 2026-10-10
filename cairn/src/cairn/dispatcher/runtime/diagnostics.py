"""Payload-free model/CLI diagnostics. Counts are events, not independent failures."""
from __future__ import annotations
import json
from collections import Counter


def response_failure_reason(stdout: str) -> str:
    """Classify the final Pi response without persisting model text or secrets."""
    last = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        messages = event.get('messages', []) if event.get('type') == 'agent_end' else [event.get('message')]
        if not isinstance(messages, list):
            continue
        for message in messages:
            if isinstance(message, dict) and message.get('role') == 'assistant':
                last = message
    if last.get('errorMessage'):
        return 'provider_error'
    return {
        'length': 'output_limit', 'error': 'provider_error',
        'aborted': 'response_aborted', 'toolUse': 'incomplete_response',
    }.get(last.get('stopReason'), 'invalid_response')


def pi_run_metrics(stdout: str) -> dict:
    events = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except (ValueError, TypeError):
            pass
    messages = [e.get('message', {}) for e in events if e.get('type') == 'message_end']
    if not messages:
        messages = [e.get('message', {}) for e in events if e.get('type') == 'message']
    tools, errors, stops = Counter(), Counter(), Counter()
    turns = 0
    usage = Counter()
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get('role') == 'assistant':
            turns += 1
            reason = m.get('stopReason')
            stops[reason if reason in {'stop', 'toolUse', 'error', 'aborted', 'length'} else 'other'] += 1
            err = str(m.get('errorMessage') or '').lower()
            if err:
                category = next((label for match, label in [
                    ('timed out', 'provider_timeout'), ('connection', 'provider_connection'),
                    ('terminated', 'provider_terminated')] if match in err), 'provider_other')
                errors[category] += 1
            usage_record = m.get('usage')
            usage_record = usage_record if isinstance(usage_record, dict) else {}
            for key in ('input', 'output', 'cacheRead', 'cacheWrite'):
                val = usage_record.get(key)
                if isinstance(val, (int, float)):
                    usage[key] += val
            content = m.get('content')
            content = content if isinstance(content, list) else []
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'toolCall':
                    name = item.get('name')
                    tools[name if name in {'read', 'write', 'edit', 'bash', 'grep', 'find', 'ls'} else 'other'] += 1
        elif m.get('role') == 'toolResult' and m.get('isError'):
            errors['tool_error'] += 1
    return {'assistant_turns': turns, 'tools': dict(tools), 'errors': dict(errors),
            'stop_reasons': dict(stops), 'token_usage': dict(usage)}
