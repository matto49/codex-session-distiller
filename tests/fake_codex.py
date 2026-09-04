#!/usr/bin/env python3
"""Synthetic app-server. It refuses any directory without a test-only marker."""
import json
import os
from pathlib import Path
import sqlite3
import sys

home = Path(os.environ['CODEX_HOME'])
if not (home / 'SYNTHETIC_TEST_HOME').is_file():
    raise SystemExit('Refusing to touch a non-synthetic Codex home')

def reply(rid, result=None, error=None):
    print(json.dumps({'id': rid, **({'error': {'code': -32600, 'message': error}} if error else {'result': result})}), flush=True)

for line in sys.stdin:
    req = json.loads(line)
    if req['method'] == 'initialized':
        continue
    rid = req['id']
    if req['method'] == 'initialize':
        print(json.dumps({'method': 'test/notification', 'params': {}}), flush=True)
        reply(rid, {'codexHome': str(home)})
        continue
    cfg_path = home / 'fake-behavior.json'
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    sid = req['params']['threadId']
    if sid in cfg.get('active_ids', []):
        reply(rid, error='thread ' + sid + ' already has an active writer')
        continue
    if sid in cfg.get('deny_ids', []):
        reply(rid, error='synthetic permission rejection')
        continue
    c = sqlite3.connect(home / 'state_5.sqlite')
    c.row_factory = sqlite3.Row
    rows = {x['id']: dict(x) for x in c.execute('select * from threads')}
    parents = {}
    for i, r in rows.items():
        try:
            parents[i] = json.loads(r['source'])['subagent']['thread_spawn']['parent_thread_id']
        except (ValueError, TypeError, KeyError):
            pass
    targets = {sid}
    while True:
        extra = {i for i, parent in parents.items() if parent in targets} - targets
        if not extra:
            break
        targets.update(extra)
    targets.update(cfg.get('outside_cascade', []))
    archived = req['method'] == 'thread/archive'
    for target in targets:
        row = rows[target]
        if bool(row['archived']) == archived:
            continue
        src = Path(row['rollout_path'])
        dst = home / ('archived_sessions' if archived else 'sessions/2000/01/01') / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        c.execute('update threads set archived=?,rollout_path=? where id=?', (int(archived), str(dst), target))
    c.commit()
    c.close()
    if cfg.get('disconnect_after_write'):
        raise SystemExit(0)
    reply(rid, {})
