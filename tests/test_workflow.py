from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/codex-session-distiller/scripts'))
from csd_core import digest, extract, file_hash, inventory, load, manifest, redact, snapshot, write
from csd_summary import distill, review, verify_summary
from csd_archive import apply, journal, plan, readback, restore
from session_distiller import main


@contextlib.contextmanager
def dbconnect(path):
    c = sqlite3.connect(path)
    try:
        with c:
            yield c
    finally:
        c.close()


def sid(n):
    return '00000000-0000-4000-8000-' + str(n).zfill(12)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'codex'
        self.run = self.root / 'run'
        self.home.mkdir()
        (self.home / 'SYNTHETIC_TEST_HOME').touch()
        self.db = self.home / 'state_5.sqlite'
        with dbconnect(self.db) as c:
            c.execute('create table threads (id text primary key, rollout_path text, archived integer, updated_at integer, recency_at integer, source text, title text, cwd text, is_pinned integer)')
        self.binary = ROOT / 'tests/fake_codex.py'
        self.command = [sys.executable, str(ROOT / 'tests/fake_summarizer.py')]

    def tearDown(self):
        self.temp.cleanup()

    def task(self, n, parent=None, pinned=0, age=45, body='Investigate the synthetic cache issue.', gap=None, running=False):
        ident = sid(n)
        src = {'subagent': {'thread_spawn': {'parent_thread_id': parent}}} if parent else 'cli'
        path = self.home / 'sessions' / ('rollout-' + ident + '.jsonl')
        path.parent.mkdir(exist_ok=True)
        messages = [
            {'type': 'session_meta', 'payload': {'id': ident}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'test-turn'}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': body}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'The cache key fix is implemented; deployment remains unverified.'}]}},
        ]
        if gap:
            messages.append({'type': 'compacted', 'payload': {}})
        if not running:
            messages.append({'type': 'event_msg', 'payload': {'type': 'task_complete'}})
        path.write_text(''.join(json.dumps(x) + '\n' for x in messages))
        stamp = int(time.time() - age * 86400)
        os.utime(path, (stamp, stamp))
        with dbconnect(self.db) as c:
            c.execute('insert into threads values(?,?,?,?,?,?,?,?,?)', (ident, str(path), 0, stamp, stamp, json.dumps(src), 'Synthetic task ' + str(n), '/synthetic/project', pinned))
        return path

    def summarize(self, max_chars=16000):
        snapshot(self.home, self.run)
        with contextlib.redirect_stdout(io.StringIO()):
            distill(self.run, self.command, max_chars=max_chars)
        ids = [x['id'] for x in manifest(self.run)['sessions']]
        review(self.run, ids, 'Synthetic source and final-state claims inspected by the fixture author.')

    def do_apply(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return apply(self.run, self.binary, **kwargs)

    def test_complete_flow_preserves_bytes_and_restores(self):
        src = self.task(1)
        original = file_hash(src)
        db_before = file_hash(self.db)
        self.summarize()
        self.assertEqual(file_hash(self.db), db_before)
        self.assertEqual(file_hash(src), original)
        self.assertEqual(plan(self.run)['eligible'], 1)
        self.assertEqual(self.do_apply()['archived'], 1)
        row = inventory(self.db)[sid(1)]
        self.assertEqual(row['archived'], 1)
        self.assertEqual(file_hash(row['rollout_path']), original)
        self.assertEqual(restore(self.run, [sid(1)], self.binary)['restored'], 1)
        row = inventory(self.db)[sid(1)]
        self.assertEqual(row['archived'], 0)
        self.assertEqual(file_hash(row['rollout_path']), original)

    def test_unreviewed_summary_is_not_eligible(self):
        self.task(1)
        snapshot(self.home, self.run)
        with contextlib.redirect_stdout(io.StringIO()):
            distill(self.run, self.command)
        self.assertEqual(plan(self.run)['eligible'], 0)

    def test_citations_from_wrong_input_rejected(self):
        self.task(1)
        self.summarize()
        path = self.run / 'summaries' / (sid(1) + '.json')
        result = load(path)
        result['chunks'][0]['sections']['goal'][0]['lines'] = [999999]
        write(path, result)
        with self.assertRaises(ValueError):
            verify_summary(self.run, manifest(self.run)['sessions'][0])
        self.assertEqual(plan(self.run)['eligible'], 0)

    def test_cross_session_output_identity_rejected(self):
        self.task(1)
        self.task(2)
        self.summarize()
        first = self.run / 'summaries' / (sid(1) + '.json')
        second = self.run / 'summaries' / (sid(2) + '.json')
        first.write_bytes(second.read_bytes())
        with self.assertRaises(ValueError):
            verify_summary(self.run, manifest(self.run)['sessions'][0])

    def test_changed_source_after_plan_is_held(self):
        src = self.task(1)
        self.summarize()
        plan(self.run)
        src.write_text(src.read_text() + '\n')
        self.assertEqual(self.do_apply()['archived'], 0)
        self.assertFalse(inventory(self.db)[sid(1)]['archived'])

    def test_same_size_same_mtime_content_change_is_held(self):
        src = self.task(1)
        self.summarize()
        plan(self.run)
        st = src.stat()
        src.write_bytes(src.read_bytes().replace(b'cache', b'CACHE'))
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.do_apply()['archived'], 0)

    def test_pins_goals_recent_and_gaps_are_held(self):
        self.task(1, pinned=1)
        self.task(2, age=1)
        self.task(3, gap=True)
        self.task(4)
        with dbconnect(self.home / 'goals_1.sqlite') as c:
            c.execute('create table thread_goals(thread_id text,status text)')
            c.execute('insert into thread_goals values(?,?)', (sid(4), 'active'))
        self.summarize()
        self.assertEqual(plan(self.run)['eligible'], 0)

    def test_running_turn_hint_is_held(self):
        self.task(1, running=True)
        self.summarize()
        self.assertEqual(plan(self.run)['eligible'], 0)

    def test_protected_child_protects_parent(self):
        self.task(1)
        self.task(2, parent=sid(1), pinned=1)
        self.summarize()
        self.assertEqual(plan(self.run)['eligible'], 0)
        p = load(self.run / 'archive-plan.json')
        self.assertEqual(p['items'][0]['reason'], 'protected_descendant')

    def test_new_child_after_plan_protects_parent(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        self.task(2, parent=sid(1))
        self.assertEqual(self.do_apply()['archived'], 0)

    def test_child_is_archived_before_parent(self):
        self.task(1)
        self.task(2, parent=sid(1))
        self.summarize()
        plan(self.run)
        self.assertEqual(self.do_apply()['archived'], 2)
        intents = [x['session_id'] for x in journal(self.run) if x['event'] == 'intent']
        self.assertEqual(intents, [sid(2), sid(1)])
        with self.assertRaises(ValueError):
            restore(self.run, [sid(1)], self.binary)
        self.assertEqual(restore(self.run, [sid(1), sid(2)], self.binary)['restored'], 2)
        restored_ids = {x['session_id'] for x in journal(self.run) if x['action'] == 'restore' and x['event'] == 'verified'}
        self.assertEqual(restored_ids, {sid(1), sid(2)})
        self.assertTrue(all(not x['archived'] for x in inventory(self.db).values()))

    def test_active_writer_is_held_without_retries(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        write(self.home / 'fake-behavior.json', {'active_ids': [sid(1)]})
        self.assertEqual(self.do_apply()['held'], 1)
        self.do_apply()
        self.assertEqual(len([x for x in journal(self.run) if x['event'] == 'intent']), 1)
        self.assertFalse(inventory(self.db)[sid(1)]['archived'])

    def test_error_after_mutation_is_reconciled(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        write(self.home / 'fake-behavior.json', {'disconnect_after_write': True})
        self.assertEqual(self.do_apply()['archived'], 1)
        self.assertTrue(journal(self.run)[-1]['reconciled_api_error'])

    def test_rejection_is_not_bypassed_or_retried(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        write(self.home / 'fake-behavior.json', {'deny_ids': [sid(1)]})
        with self.assertRaises(ValueError):
            self.do_apply()
        self.do_apply()
        self.assertEqual(len([x for x in journal(self.run) if x['event'] == 'intent']), 1)
        self.assertFalse(inventory(self.db)[sid(1)]['archived'])

    def test_crash_intent_is_reconciled_without_sending(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        from csd_archive import log
        log(self.run, {'event': 'intent', 'action': 'archive', 'session_id': sid(1), 'operation': 'test-interrupted-operation'})
        self.assertEqual(self.do_apply()['archived'], 0)
        self.assertEqual(journal(self.run)[-1]['reason'], 'unconfirmed_prior_request')

    def test_new_pin_after_plan_is_held(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        write(self.home / '.codex-global-state.json', {'pinned-thread-ids': [sid(1)]})
        self.assertEqual(self.do_apply()['archived'], 0)

    def test_cli_archive_without_apply_is_read_only(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        before = file_hash(self.db)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['archive', '--run', str(self.run)]), 0)
        self.assertEqual(file_hash(self.db), before)
        self.assertFalse((self.run / 'archive-journal.jsonl').exists())

    def test_large_line_is_bounded_and_unarchivable(self):
        self.task(1, body='x' * 20000)
        snapshot(self.home, self.run, max_line=1024)
        e = manifest(self.run)['sessions'][0]
        self.assertIn('oversized_record', [x['reason'] for x in e['gaps']])
        self.assertEqual(e['messages'] if 'messages' in e else e['coverage'], 'partial')

    def test_long_task_reduction_and_cache_resume(self):
        self.task(1, body='Long synthetic evidence. ' * 300)
        self.summarize(max_chars=1000)
        entry = manifest(self.run)['sessions'][0]
        result = verify_summary(self.run, entry, require_review=True)
        self.assertGreater(len(result['chunks']), 5)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(distill(self.run, ['/nonexistent-model'], max_chars=1000)['new_summaries'], 0)

    def test_rejected_child_is_not_archived_through_parent_on_resume(self):
        self.task(1)
        self.task(2, parent=sid(1))
        self.summarize()
        plan(self.run)
        write(self.home / 'fake-behavior.json', {'deny_ids': [sid(2)]})
        with self.assertRaises(ValueError):
            self.do_apply()
        self.assertEqual(self.do_apply()['archived'], 0)
        self.assertTrue(all(not x['archived'] for x in inventory(self.db).values()))

    def test_outside_home_rollout_is_not_exported(self):
        self.task(1)
        secret = self.root / 'outside.txt'
        secret.write_text('private outside content')
        with dbconnect(self.db) as c:
            c.execute('update threads set rollout_path=?', (str(secret),))
        with self.assertRaises(ValueError):
            snapshot(self.home, self.run)
        self.assertFalse((self.run / 'corpus' / (sid(1) + '.json')).exists())

    def test_rejected_restore_is_not_blindly_retried(self):
        self.task(1)
        self.summarize()
        plan(self.run)
        self.do_apply()
        write(self.home / 'fake-behavior.json', {'deny_ids': [sid(1)]})
        with self.assertRaises(ValueError):
            restore(self.run, [sid(1)], self.binary)
        with self.assertRaises(ValueError):
            restore(self.run, [sid(1)], self.binary)
        self.assertEqual(len([x for x in journal(self.run) if x['event'] == 'intent' and x['action'] == 'restore']), 1)

    def test_unknown_dialogue_schema_is_held(self):
        src = self.task(1)
        with src.open('a') as f:
            f.write(json.dumps({'type': 'response_item', 'payload': {'type': 'future_message_format', 'text': 'unparsed outcome'}}) + '\n')
        self.summarize()
        entry = manifest(self.run)['sessions'][0]
        self.assertIn('unsupported_response_item', [x['reason'] for x in entry['gaps']])
        self.assertEqual(plan(self.run)['eligible'], 0)

    def test_changed_descendant_blocks_parent_restore(self):
        self.task(1)
        self.task(2, parent=sid(1))
        self.summarize()
        plan(self.run)
        self.do_apply()
        child_path = Path(inventory(self.db)[sid(2)]['rollout_path'])
        child_path.write_text(child_path.read_text() + '\n')
        with self.assertRaises(ValueError):
            restore(self.run, [sid(1), sid(2)], self.binary)
        self.assertTrue(all(x['archived'] for x in inventory(self.db).values()))

    def test_verified_disconnect_reconnects_for_next_task(self):
        self.task(1)
        self.task(2)
        self.summarize()
        plan(self.run)
        write(self.home / 'fake-behavior.json', {'disconnect_after_write': True})
        self.assertEqual(self.do_apply()['archived'], 2)

    def test_snapshot_does_not_overwrite_existing_run(self):
        self.task(1)
        snapshot(self.home, self.run)
        before = file_hash(self.run / 'manifest.json')
        with self.assertRaises(ValueError):
            snapshot(self.home, self.run)
        self.assertEqual(file_hash(self.run / 'manifest.json'), before)

    def test_machine_binding_rejects_foreign_host(self):
        self.task(1)
        self.summarize()
        with patch('csd_core.machine_key', return_value='other-machine'):
            with self.assertRaises(ValueError):
                plan(self.run)

    def test_secrets_redacted_before_model_input(self):
        value = 'Cookie: a=private-cookie\nAuthorization: Bearer test-secret\n{"api_key":"some-private-key"}\nhttps://example.test/?token=private-token'
        result = redact(value)
        for secret in ('private-cookie', 'test-secret', 'some-private-key', 'private-token'):
            self.assertNotIn(secret, result)


if __name__ == '__main__':
    unittest.main()
