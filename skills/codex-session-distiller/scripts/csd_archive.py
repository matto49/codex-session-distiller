"""Official JSON-RPC archive/restore with refreshed guards and durable readback."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from csd_core import digest, file_hash, inventory, load, manifest, protected_ids, source_matches, write
from csd_summary import verify_summary


class RPCError(RuntimeError):
    pass


class Client:
    def __init__(self, binary, home, run, transport='stdio', timeout=45):
        binary = Path(binary).expanduser().resolve()
        if not binary.is_file():
            raise ValueError('Provide the absolute path to the official Codex binary')
        self.timeout, self.seq = timeout, 0
        self.queue = queue.Queue(maxsize=512)
        self.stderr = os.fdopen(os.open(Path(run) / 'app-server.stderr.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'ab')
        args = [str(binary), 'app-server'] + (['proxy'] if transport == 'proxy' else ['--listen', 'stdio://'])
        env = {**os.environ, 'CODEX_HOME': str(home)}
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, env=env)
        def reader():
            try:
                for line in self.p.stdout:
                    self.queue.put(line)
            finally:
                self.queue.put(None)
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()
        try:
            result = self.call('initialize', {'clientInfo': {'name': 'codex_session_distiller', 'version': '0.1.0'}})
            if Path(result.get('codexHome', '')).resolve() != Path(home).resolve():
                raise ValueError('App-server connected to a different Codex home')
            self.send({'method': 'initialized'})
        except BaseException:
            self.close()
            raise

    def send(self, data):
        self.p.stdin.write((json.dumps(data) + '\n').encode())
        self.p.stdin.flush()

    def call(self, method, params):
        self.seq += 1
        rid = self.seq
        self.send({'id': rid, 'method': method, 'params': params})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                raw = self.queue.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as e:
                raise TimeoutError('App-server response timeout for ' + method) from e
            if raw is None:
                raise RPCError('App-server disconnected')
            message = json.loads(raw)
            if 'method' in message and 'id' in message:
                raise RPCError('Unexpected server request; interactive execution is not supported')
            if message.get('id') != rid:
                continue
            if 'error' in message:
                raise RPCError(json.dumps(message['error'], ensure_ascii=False))
            return message['result']

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait(timeout=5)
        self.reader.join(timeout=1)
        for stream in (self.p.stdin, self.p.stdout, self.stderr):
            stream.close()


def journal(run):
    p = Path(run) / 'archive-journal.jsonl'
    if not p.exists():
        return []
    # An interrupted/invalid last line must be investigated, never ignored.
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()]


def log(run, event):
    p = Path(run) / 'archive-journal.jsonl'
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(json.dumps({**event, 'at': time.time()}, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


class RunLock:
    def __init__(self, run):
        self.path = Path(run) / 'apply.lock'

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise ValueError('Run is locked. Inspect its journal and process before removing a stale apply.lock') from e
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        return self

    def __exit__(self, *_):
        self.path.unlink()


def proofs(run, m):
    good = {}
    for entry in m['sessions']:
        try:
            result = verify_summary(run, entry, require_review=True)
            good[entry['id']] = digest(result)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return good


def eligibility(run, m, records, verified, days, external=None):
    home = Path(m['codex_home'])
    protected = protected_ids(home, external)
    originals = {x['id']: x for x in m['sessions']}
    held_before = {x['session_id'] for x in journal(run) if x.get('action') == 'archive' and x.get('event') in ('held', 'error', 'intent')}
    reasons = {}
    for sid, row in records.items():
        original = originals.get(sid)
        if row['archived']:
            reasons[sid] = 'already_archived'
        elif original is None:
            reasons[sid] = 'not_in_snapshot'
        elif sid in protected or row.get('is_pinned'):
            reasons[sid] = 'protected'
        elif sid in held_before:
            reasons[sid] = 'held_in_prior_attempt'
        elif original['gaps']:
            reasons[sid] = 'incomplete_source'
        elif original.get('running_hint'):
            reasons[sid] = 'unfinished_turn_in_snapshot'
        elif sid not in verified:
            reasons[sid] = 'summary_not_reviewed'
        elif not source_matches(row, original):
            reasons[sid] = 'source_changed_or_missing'
        else:
            latest = max(float(row.get('updated_at') or 0), float(row.get('recency_at') or 0), Path(row['rollout_path']).stat().st_mtime)
            reasons[sid] = 'recent_activity' if time.time() - latest < days * 86400 else None
    # An archive may cascade through the whole live descendant tree, including new tasks.
    for sid, reason in list(reasons.items()):
        if reason is None or records[sid]['archived']:
            continue
        seen = set()
        parent = records[sid].get('parent_id')
        while parent and parent not in seen:
            seen.add(parent)
            if parent in reasons and reasons[parent] is None:
                reasons[parent] = 'protected_descendant'
            parent = records.get(parent, {}).get('parent_id')
    for sid in originals.keys() - records.keys():
        reasons[sid] = 'missing_thread'
    return reasons


def plan(run, days=30, external=None):
    if days < 1:
        raise ValueError('Inactive days must be at least one')
    run = Path(run).resolve()
    m = manifest(run, check_host=True)
    records = inventory(m['state_db'])
    verified = proofs(run, m)
    external = str(Path(external).resolve()) if external else None
    reasons = eligibility(run, m, records, verified, days, external)
    result = {'schema_version': 1, 'manifest_digest': m['manifest_digest'], 'created_at': time.time(), 'inactive_days': days, 'protected_ids_file': external, 'items': [{'session_id': x['id'], 'eligible': reasons[x['id']] is None, 'reason': reasons[x['id']]} for x in m['sessions']]}
    result['plan_digest'] = digest(result)
    write(run / 'archive-plan.json', result)
    return {'eligible': sum(x['eligible'] for x in result['items']), 'held': sum(not x['eligible'] for x in result['items'])}


def readback(row, original, archived):
    return row is not None and bool(row['archived']) == archived and source_matches(row, original, full_hash=True)


def descendants(sid, records):
    found = set()
    stack = [sid]
    children = {}
    for child, row in records.items():
        children.setdefault(row.get('parent_id'), []).append(child)
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in found and child != sid:
                found.add(child)
                stack.append(child)
    return found


def depth(sid, records):
    seen = set()
    while sid in records and records[sid].get('parent_id'):
        if sid in seen:
            raise ValueError('Cycle in task parent metadata')
        seen.add(sid)
        sid = records[sid]['parent_id']
    return len(seen)


def reconcile(run, m):
    entries = {x['id']: x for x in m['sessions']}
    events = journal(run)
    terminal = {x['operation'] for x in events if x.get('event') in ('verified', 'held', 'error')}
    records = inventory(m['state_db'])
    for intent in events:
        if intent.get('event') != 'intent' or intent['operation'] in terminal:
            continue
        sid = intent['session_id']
        wanted = intent['action'] == 'archive'
        if readback(records.get(sid), entries[sid], wanted):
            log(run, {**intent, 'event': 'verified', 'reconciled': True, 'rollout_path': records[sid]['rollout_path']})
        else:
            # A crash/timeout gives no permission to resend. Keep and investigate it.
            log(run, {**intent, 'event': 'held', 'reason': 'unconfirmed_prior_request'})


def apply(run, binary, transport='stdio', limit=None):
    run = Path(run).resolve()
    m = manifest(run, check_host=True)
    p = load(run / 'archive-plan.json')
    if p.get('manifest_digest') != m['manifest_digest'] or p.get('plan_digest') != digest({k: v for k, v in p.items() if k != 'plan_digest'}):
        raise ValueError('Plan does not match the current snapshot')
    with RunLock(run):
        reconcile(run, m)
        originals = {x['id']: x for x in m['sessions']}
        verified = proofs(run, m)
        records = inventory(m['state_db'])
        seen = {x['session_id'] for x in journal(run) if x.get('action') == 'archive' and x.get('event') in ('intent', 'held')}
        pending = [x['session_id'] for x in p['items'] if x['eligible'] and x['session_id'] not in seen]
        pending.sort(key=lambda sid: (-depth(sid, records), sid))
        remaining = max(0, len(pending) - limit) if limit is not None else 0
        if limit is not None:
            pending = pending[:limit]
        count = kept = 0
        client = None
        try:
            for sid in pending:
                if client is None:
                    client = Client(binary, m['codex_home'], run, transport)
                records = inventory(m['state_db'])
                reasons = eligibility(run, m, records, verified, p['inactive_days'], p['protected_ids_file'])
                reason = reasons.get(sid, 'missing_thread')
                # Check selected task and all live descendants again with full hashes.
                for child in {sid} | descendants(sid, records):
                    if child in records and not records[child]['archived']:
                        try:
                            if reasons.get(child) is not None:
                                raise ValueError('Protected descendant')
                            verify_summary(run, originals[child], require_review=True)
                            if not source_matches(records[child], originals[child], full_hash=True):
                                raise ValueError('Changed source')
                        except (OSError, ValueError, KeyError, TypeError):
                            reason = reason or 'source_or_review_changed'
                op = {'action': 'archive', 'session_id': sid, 'operation': str(uuid.uuid4())}
                if reason:
                    log(run, {**op, 'event': 'held', 'reason': reason})
                    kept += 1
                    continue
                before = {i for i, r in records.items() if r['archived']}
                log(run, {**op, 'event': 'intent', 'source_sha256': originals[sid]['source_sha256']})
                error = None
                try:
                    client.call('thread/archive', {'threadId': sid})
                except Exception as e:
                    error = str(e)
                    log(run, {**op, 'event': 'api_error', 'error': error})
                after = inventory(m['state_db'])
                unexpected = {i for i, r in after.items() if r['archived']} - before - {sid}
                if unexpected:
                    log(run, {**op, 'event': 'error', 'reason': 'unexpected_cascade_or_concurrent_change', 'ids': sorted(unexpected)})
                    raise ValueError('Archive scope changed unexpectedly; stopped for inspection')
                if readback(after.get(sid), originals[sid], True):
                    log(run, {**op, 'event': 'verified', 'rollout_path': after[sid]['rollout_path'], 'reconciled_api_error': bool(error)})
                    count += 1
                    if error:
                        client.close()
                        client = None
                elif error and 'already has an active writer' in error and not after[sid]['archived']:
                    log(run, {**op, 'event': 'held', 'reason': 'active_writer'})
                    kept += 1
                else:
                    log(run, {**op, 'event': 'error', 'reason': 'archive_not_verified'})
                    raise ValueError('Archive could not be verified; do not blindly retry')
                print(json.dumps({'event': 'progress', 'archived': count, 'held': kept}), flush=True)
        finally:
            if client:
                client.close()
        return {'archived': count, 'held': kept, 'remaining': remaining}


def restore(run, ids, binary, transport='stdio'):
    run = Path(run).resolve()
    m = manifest(run, check_host=True)
    originals = {x['id']: x for x in m['sessions']}
    selected = set(ids)
    known = {x['session_id'] for x in journal(run) if x.get('action') == 'archive' and x.get('event') == 'verified'}
    if not selected or not selected <= known:
        raise ValueError('Restore only IDs with verified archive receipts from this run')
    with RunLock(run):
        reconcile(run, m)
        client = Client(binary, m['codex_home'], run, transport)
        restored = 0
        try:
            records = inventory(m['state_db'])
            prior_restore = {x['session_id'] for x in journal(run) if x.get('action') == 'restore' and x.get('event') == 'intent'}
            if any(records[i]['archived'] for i in selected & prior_restore):
                raise ValueError('Prior restore attempt remains unresolved; inspect it before continuing')
            for sid in selected:
                extra = {i for i in descendants(sid, records) if records[i]['archived']} - selected
                if extra:
                    raise ValueError('Select archived descendants too before restoring their parent')
            for sid in sorted(selected, key=lambda x: depth(x, records)):
                records = inventory(m['state_db'])
                for target in {sid} | (descendants(sid, records) & selected):
                    if not source_matches(records[target], originals[target], full_hash=True):
                        raise ValueError('Restore source changed or missing')
                if not records[sid]['archived']:
                    continue
                op = {'action': 'restore', 'session_id': sid, 'operation': str(uuid.uuid4())}
                log(run, {**op, 'event': 'intent'})
                error = None
                try:
                    client.call('thread/unarchive', {'threadId': sid})
                except Exception as e:
                    error = str(e)
                    log(run, {**op, 'event': 'api_error', 'error': error})
                after = inventory(m['state_db'])
                changed = {i for i, r in records.items() if r['archived'] and i in after and not after[i]['archived']}
                if changed - selected:
                    log(run, {**op, 'event': 'error', 'reason': 'unexpected_restore_scope', 'ids': sorted(changed - selected)})
                    raise ValueError('Restore scope changed unexpectedly; stopped for inspection')
                if not readback(after.get(sid), originals[sid], False):
                    raise ValueError('Restore not verified; inspect journal before retrying')
                for target in changed:
                    if not readback(after.get(target), originals[target], False):
                        log(run, {**op, 'event': 'error', 'reason': 'restore_descendant_not_verified', 'target': target})
                        raise ValueError('Restored descendant bytes could not be verified')
                for target in sorted(changed):
                    log(run, {**op, 'session_id': target, 'event': 'verified', 'requested_session_id': sid, 'rollout_path': after[target]['rollout_path'], 'reconciled_api_error': bool(error)})
                restored += len(changed)
        finally:
            client.close()
        return {'restored': restored}
