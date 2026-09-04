"""Read-only Codex inventory and bounded, source-addressed transcript extraction."""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
import tomllib
import uuid

UUID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
SCHEMA = 1
IGNORED_EVENTS = {
    'token_count', 'agent_reasoning', 'agent_reasoning_delta', 'agent_message_delta',
    'agent_reasoning_raw_content', 'agent_reasoning_raw_content_delta',
    'exec_command_begin', 'exec_command_end', 'exec_command_output_delta',
    'exec_approval_request', 'apply_patch_approval_request', 'patch_apply_begin', 'patch_apply_end',
    'mcp_tool_call_begin', 'mcp_tool_call_end', 'web_search_begin', 'web_search_end',
    'image_generation_begin', 'image_generation_end', 'view_image_tool_call',
    'sub_agent_activity', 'thread_settings_applied', 'stream_error', 'error', 'warning',
    'turn_diff', 'plan_update', 'raw_response_item', 'shutdown_complete',
    'mcp_startup_update', 'mcp_startup_complete', 'session_configured',
    'background_event', 'item_started', 'turn_started', 'turn_complete',
    'collab_agent_spawn_begin', 'collab_agent_spawn_end', 'collab_agent_interaction_begin',
    'collab_agent_interaction_end', 'collab_waiting_begin', 'collab_waiting_end',
    'collab_close_begin', 'collab_close_end', 'collab_resume_begin', 'collab_resume_end',
}
IGNORED_RESPONSE_ITEMS = {
    'message', 'reasoning', 'function_call', 'function_call_output',
    'custom_tool_call', 'custom_tool_call_output', 'web_search_call', 'image_generation_call',
    'local_shell_call', 'computer_call', 'computer_call_output', 'code_interpreter_call',
    'file_search_call', 'mcp_call', 'mcp_list_tools', 'mcp_approval_request',
    'mcp_approval_response', 'tool_search_call', 'tool_search_output',
}
IGNORED_COMPLETED_ITEMS = {
    'commandexecution', 'filechange', 'mcptoolcall', 'dynamictoolcall', 'websearch',
    'imageview', 'imagegeneration', 'enteredreviewmode', 'exitedreviewmode', 'reasoning',
    'plan', 'toolcall', 'collabagenttoolcall',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def text_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)


def connect(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True))


def choose_db(home):
    paths = list(home.glob('state_*.sqlite'))
    paths.sort(key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1, reverse=True)
    for p in paths:
        with connect(p) as c:
            cols = {r[1] for r in c.execute('pragma table_info(threads)')}
        if {'id', 'rollout_path', 'archived'} <= cols:
            return p
    raise ValueError('No supported state_*.sqlite with a threads table; no files changed')


def inventory(db):
    with connect(db) as c:
        c.row_factory = sqlite3.Row
        cols = {r[1] for r in c.execute('pragma table_info(threads)')}
        required = {'id', 'rollout_path', 'archived', 'updated_at', 'source'}
        if not required <= cols:
            raise ValueError('Unsupported threads schema: ' + ', '.join(sorted(required - cols)))
        wanted = sorted(cols & {'id', 'rollout_path', 'archived', 'updated_at', 'recency_at', 'created_at', 'is_pinned', 'source', 'cwd', 'title', 'name'})
        rows = {r['id']: dict(r) for r in c.execute('select ' + ','.join(wanted) + ' from threads')}
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        edges = []
        if 'thread_spawn_edges' in tables:
            edge_cols = {r[1] for r in c.execute('pragma table_info(thread_spawn_edges)')}
            if {'parent_thread_id', 'child_thread_id'} <= edge_cols:
                edges = list(c.execute('select parent_thread_id,child_thread_id from thread_spawn_edges'))
            else:
                raise ValueError('Unsupported thread_spawn_edges schema; cannot check cascade protection')
    for row in rows.values():
        try:
            source = json.loads(row['source']) if isinstance(row['source'], str) else row['source']
            row['parent_id'] = source.get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id')
        except (ValueError, AttributeError):
            row['parent_id'] = None
    for parent, child in edges:
        if child in rows:
            if rows[child]['parent_id'] not in (None, parent):
                raise ValueError('Conflicting parent metadata for ' + child)
            rows[child]['parent_id'] = parent
    return rows


def redact(text):
    # Best effort. Paths, business text and arbitrary secrets can remain private.
    text = re.sub(r'data:[^\s;]+;base64,[A-Za-z0-9+/=]+', '[BINARY OMITTED]', text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----', '[PRIVATE KEY REDACTED]', text)
    text = re.sub(r'(?im)^((?:set-cookie|cookie|authorization)\s*:).*$', r'\1 [REDACTED]', text)
    text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [REDACTED]', text)
    text = re.sub(r'\b(?:gh[pousr]_|github_pat_|sk-(?:ant-)?)[A-Za-z0-9_-]{12,}', '[REDACTED]', text)
    text = re.sub(r'(?i)([?&](?:token|access_token|refresh_token|secret|api_key|key)=)[^&\s<>\"\)]+', r'\1[REDACTED]', text)
    text = re.sub(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1"[REDACTED]"', text)
    for tag in ('recommended_plugins', 'environment_context', 'skills_instructions', 'app-context', 'oai-mem-citation'):
        text = re.sub('<' + tag + r'>[\s\S]*?</' + tag + '>', '', text)
    return text.strip()


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(x.get('text', '') for x in content if isinstance(x, dict) and x.get('type') in ('text', 'input_text', 'output_text'))
    return ''


def extract(row, max_line=8 * 1024 * 1024):
    result = {k: v for k, v in row.items() if k != 'source'}
    result['title'] = redact(row.get('name') or row.get('title') or row['id'])
    result.update(messages=[], gaps=[], duplicate_messages=0, running_hint=False)
    path = Path(row['rollout_path'])
    h = hashlib.sha256()
    turn = None
    seen = set()
    meta_id = None

    def add(role, text, line):
        text = redact(text)
        if not text:
            return
        key = (turn, role, digest(text))
        if key in seen:
            result['duplicate_messages'] += 1
            return
        seen.add(key)
        result['messages'].append({'role': role, 'text': text, 'line': line})

    try:
        before = path.stat()
        result['source_bytes'] = before.st_size
        result['source_mtime_ns'] = before.st_mtime_ns
        pos = 0
        line_no = 0
        with path.open('rb') as f:
            while pos < before.st_size:
                block = f.readline(min(max_line + 1, before.st_size - pos))
                if not block:
                    break
                h.update(block)
                pos += len(block)
                line_no += 1
                if len(block) > max_line:
                    while not block.endswith(b'\n') and pos < before.st_size:
                        block = f.readline(min(max_line + 1, before.st_size - pos))
                        if not block:
                            break
                        pos += len(block)
                        h.update(block)
                    result['gaps'].append({'line': line_no, 'reason': 'oversized_record'})
                    continue
                try:
                    event = json.loads(block)
                    if not isinstance(event, dict):
                        raise ValueError()
                    p = event.get('payload') or {}
                    if not isinstance(p, dict):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    result['gaps'].append({'line': line_no, 'reason': 'invalid_record'})
                    continue
                typ, kind = event.get('type'), p.get('type')
                if typ == 'session_meta':
                    meta_id = p.get('id')
                    if meta_id != row['id']:
                        result['gaps'].append({'line': line_no, 'reason': 'session_identity_mismatch'})
                    parent = p.get('parent_thread_id') or p.get('forked_from_id')
                    if parent and not result.get('parent_id'):
                        result['parent_id'] = parent
                elif typ == 'turn_context':
                    turn = p.get('turn_id', turn)
                elif typ == 'compacted' or (typ == 'response_item' and kind == 'compaction'):
                    result['gaps'].append({'line': line_no, 'reason': 'compacted_history'})
                elif typ == 'event_msg':
                    if kind == 'task_started':
                        turn = p.get('turn_id') or p.get('task_id') or line_no
                        result['running_hint'] = True
                    elif kind in ('task_complete', 'turn_aborted'):
                        result['running_hint'] = False
                        add('assistant', p.get('last_agent_message', ''), line_no)
                    elif kind in ('user_message', 'agent_message'):
                        add('user' if kind == 'user_message' else 'assistant', p.get('message', ''), line_no)
                    elif kind == 'context_compacted':
                        result['gaps'].append({'line': line_no, 'reason': 'compacted_history'})
                    elif kind == 'item_completed':
                        item = p.get('item') or {}
                        if isinstance(item, dict) and str(item.get('type', '')).lower() in ('usermessage', 'agentmessage'):
                            add('user' if item['type'].lower() == 'usermessage' else 'assistant', item.get('text') or content_text(item.get('content')), line_no)
                        elif not isinstance(item, dict) or str(item.get('type', '')).lower() not in IGNORED_COMPLETED_ITEMS:
                            result['gaps'].append({'line': line_no, 'reason': 'unsupported_completed_item'})
                    elif kind not in IGNORED_EVENTS:
                        result['gaps'].append({'line': line_no, 'reason': 'unsupported_event'})
                elif typ == 'response_item' and kind == 'message' and p.get('role') in ('user', 'assistant'):
                    add(p['role'], content_text(p.get('content')), line_no)
                elif typ == 'response_item' and kind == 'agent_message':
                    add('agent', content_text(p.get('content')), line_no)
                elif typ == 'response_item' and kind not in IGNORED_RESPONSE_ITEMS:
                    result['gaps'].append({'line': line_no, 'reason': 'unsupported_response_item'})
                elif typ not in ('session_meta', 'turn_context', 'event_msg', 'response_item', 'compacted'):
                    result['gaps'].append({'line': line_no, 'reason': 'unsupported_record'})
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or pos != before.st_size:
            result['gaps'].append({'reason': 'changed_during_read'})
        result['source_sha256'] = h.hexdigest()
    except OSError as e:
        result['gaps'].append({'reason': type(e).__name__})
    if meta_id != row['id']:
        result['gaps'].append({'reason': 'session_id_missing_or_mismatch'})
    if not result['messages']:
        result['gaps'].append({'reason': 'no_readable_dialogue'})
    result['dialogue_digest'] = digest(result['messages'])
    result['coverage'] = 'partial' if result['gaps'] else 'readable_text'
    return result


def machine_key():
    return digest({'host': socket.gethostname(), 'home': str(Path.home().resolve()), 'node': uuid.getnode()})


def snapshot(home, out, label='local', max_line=8 * 1024 * 1024):
    home, out = Path(home).expanduser().resolve(), Path(out).expanduser().resolve()
    if out == home or home in out.parents:
        raise ValueError('Write run artifacts outside the Codex data directory')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Snapshot output must be empty; use a new run for a new snapshot')
    db = choose_db(home)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    rows = inventory(db)
    entries = []
    for row in sorted(rows.values(), key=lambda x: x['id']):
        if not UUID.fullmatch(row['id']):
            raise ValueError('Unsupported session ID; refusing to form a file path')
        if not Path(row['rollout_path']).expanduser().resolve().is_relative_to(home):
            raise ValueError('Rollout path is outside the selected Codex home; inspect before exporting')
        r = extract(row, max_line)
        r['source_root'] = str(home)
        write(out / 'corpus' / (r['id'] + '.json'), r)
        entries.append({k: v for k, v in r.items() if k != 'messages'})
    manifest = {'schema_version': SCHEMA, 'created_at': time.time(), 'label': label, 'machine_key': machine_key(), 'codex_home': str(home), 'state_db': str(db), 'sessions': entries}
    manifest['manifest_digest'] = digest(manifest)
    write(out / 'manifest.json', manifest)
    return {'sessions': len(entries), 'partial': sum(bool(x['gaps']) for x in entries), 'run': str(out)}


def manifest(run, check_host=False):
    m = load(Path(run) / 'manifest.json')
    signed = {k: v for k, v in m.items() if k != 'manifest_digest'}
    if m.get('schema_version') != SCHEMA or digest(signed) != m.get('manifest_digest'):
        raise ValueError('Manifest schema or digest mismatch')
    if check_host and m['machine_key'] != machine_key():
        raise ValueError('Run belongs to another machine; apply on its source host')
    return m


def protected_ids(home, external=None):
    ids = set()
    state_path = home / '.codex-global-state.json'
    if state_path.exists():
        state = load(state_path)
        ids.update(state.get('pinned-thread-ids', []))
        ids.update(state.get('queued-follow-ups', {}))
        ids.update(state.get('electron-persisted-atom-state', {}).get('heartbeat-thread-permissions-by-id', {}))
    if external:
        extra = load(external)
        if not isinstance(extra, list) or any(not isinstance(i, str) or not UUID.fullmatch(i) for i in extra):
            raise ValueError('Protected IDs file must be a JSON array of session UUIDs')
        ids.update(extra)
    for p in (home / 'automations').glob('*/automation.toml'):
        data = tomllib.loads(p.read_text(encoding='utf-8'))
        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ('thread_id', 'target_thread_id', 'targetThreadId') and isinstance(v, str):
                        ids.add(v)
                    else:
                        walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)
        walk(data)
    current = os.environ.get('CODEX_THREAD_ID')
    if current:
        ids.add(current)
    for p in home.glob('goals_*.sqlite'):
        with connect(p) as c:
            tables = {x[0] for x in c.execute("select name from sqlite_master where type='table'")}
            if 'thread_goals' not in tables:
                raise ValueError('Unknown goal database schema')
            cols = {x[1] for x in c.execute('pragma table_info(thread_goals)')}
            if not {'thread_id', 'status'} <= cols:
                raise ValueError('Unknown thread_goals schema')
            ids.update(x[0] for x in c.execute("select thread_id from thread_goals where status != 'complete' or status is null"))
    return ids


def source_matches(row, original, full_hash=False):
    try:
        p = Path(row['rollout_path'])
        if original.get('source_root') and not p.resolve().is_relative_to(Path(original['source_root']).resolve()):
            return False
        before = p.stat()
        if before.st_size != original.get('source_bytes') or before.st_mtime_ns != original.get('source_mtime_ns'):
            return False
        if full_hash and file_hash(p) != original.get('source_sha256'):
            return False
        after = p.stat()
        return (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    except OSError:
        return False
