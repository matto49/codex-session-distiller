"""Offline model adapter for synthetic tests. Never intended for real summaries."""
import json
import sys
p = json.loads(sys.stdin.read().split('\nINPUT\n', 1)[1])
refs = set()
for item in p['data']:
    if 'line' in item:
        refs.add(item['line'])
    else:
        refs.update(n for claims in item['sections'].values() for c in claims for n in c['lines'])
sections = {x: [] for x in ('goal', 'outcome', 'decisions', 'evidence', 'unresolved', 'next_steps')}
sections['goal'] = [{'text': 'Synthetic task evidence, preserved for offline protocol testing.', 'lines': sorted(refs)}]
print(json.dumps({k: p[k] for k in ('session_id', 'input_digest', 'unit')} | {'sections': sections}))
