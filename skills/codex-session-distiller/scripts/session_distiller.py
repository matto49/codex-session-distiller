#!/usr/bin/env python3
"""Portable, standard-library CLI for the Codex Session Distiller skill."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

from csd_core import inventory, load, manifest, snapshot
from csd_summary import distill, index, review, verify_summary
from csd_archive import apply, journal, plan, readback, restore


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('snapshot', help='Read Codex state/transcripts and create a private immutable run')
    s.add_argument('--codex-home', default=os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    s.add_argument('--out', required=True)
    s.add_argument('--label', default='local')
    s.add_argument('--max-line-bytes', type=int, default=8 * 1024 * 1024)
    for name, help_text in [('distill', 'Create resumable source-cited summaries'), ('verify', 'Validate identities, chunks and citations'), ('review', 'Record an actual content review'), ('index', 'Rebuild the Markdown index'), ('search', 'Search generated summaries'), ('source', 'Read an extracted source line'), ('plan', 'Prepare a read-only archive plan'), ('archive', 'Preview or apply the prepared archive plan'), ('restore', 'Preview or restore archived tasks'), ('status', 'Inspect receipts and optionally rehash archived/restored sources')]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument('--run', required=True)
        if name == 'distill':
            cmd.add_argument('--command-json', required=True, help='JSON argv array for a tool-disabled model CLI; prompt on stdin, JSON on stdout')
            cmd.add_argument('--max-chars', type=int, default=16000)
            cmd.add_argument('--timeout', type=int, default=180)
            cmd.add_argument('--limit', type=int)
        elif name == 'review':
            cmd.add_argument('--session', action='append', required=True)
            cmd.add_argument('--note', required=True)
        elif name == 'search':
            cmd.add_argument('query')
            cmd.add_argument('--limit', type=int, default=10)
        elif name == 'source':
            cmd.add_argument('--session', required=True)
            cmd.add_argument('--line', type=int, required=True)
        elif name == 'plan':
            cmd.add_argument('--inactive-days', type=int, default=30)
            cmd.add_argument('--protected-ids', help='JSON array of current/pinned/running/automation task UUIDs from the coordinating app')
        elif name in ('archive', 'restore'):
            cmd.add_argument('--apply', action='store_true', help='Mutate through the official Codex interface; requires task authorization')
            cmd.add_argument('--codex-bin', help='Absolute path to the official Codex binary (not an adapter)')
            cmd.add_argument('--transport', choices=['stdio', 'proxy'], default='stdio')
            if name == 'archive':
                cmd.add_argument('--limit', type=int)
            else:
                cmd.add_argument('--session', action='append', required=True)
        elif name == 'status':
            cmd.add_argument('--verify-sources', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        name = args.command
        if name == 'snapshot':
            if args.max_line_bytes < 1024:
                raise ValueError('max-line-bytes must be at least 1024')
            result = snapshot(args.codex_home, args.out, args.label, args.max_line_bytes)
        else:
            run = Path(args.run).expanduser().resolve()
            m = manifest(run)
            if name == 'distill':
                result = distill(run, json.loads(args.command_json), args.max_chars, args.timeout, args.limit)
            elif name == 'review':
                result = review(run, args.session, args.note)
            elif name == 'index':
                result = index(run)
            elif name == 'verify':
                errors = []
                for entry in m['sessions']:
                    try:
                        verify_summary(run, entry)
                    except (OSError, ValueError, KeyError, TypeError) as e:
                        errors.append({'session_id': entry['id'], 'error': str(e)})
                result = {'total': len(m['sessions']), 'verified': len(m['sessions']) - len(errors), 'errors': errors}
                print(json.dumps(result, ensure_ascii=False))
                return 2 if errors else 0
            elif name == 'search':
                result = []
                for entry in m['sessions']:
                    p = run / 'summaries' / (entry['id'] + '.md')
                    if p.exists() and args.query.casefold() in p.read_text(encoding='utf-8').casefold():
                        result.append({'session_id': entry['id'], 'title': entry['title'], 'summary': str(p)})
                    if len(result) >= args.limit:
                        break
            elif name == 'source':
                entries = {e['id']: e for e in m['sessions']}
                entry = entries[args.session]
                from csd_summary import corpus
                result = [x for x in corpus(run, entry) if x['line'] == args.line]
            elif name == 'plan':
                result = plan(run, args.inactive_days, args.protected_ids)
            elif name in ('archive', 'restore'):
                if not args.apply:
                    result = load(run / 'archive-plan.json') if name == 'archive' else {'action': 'restore', 'session_ids': args.session, 'applied': False}
                else:
                    if not args.codex_bin:
                        raise ValueError('--apply requires --codex-bin pointing to the official binary')
                    if getattr(args, 'limit', None) is not None and args.limit < 1:
                        raise ValueError('--limit must be positive')
                    result = apply(run, args.codex_bin, args.transport, args.limit) if name == 'archive' else restore(run, args.session, args.codex_bin, args.transport)
            elif name == 'status':
                events = journal(run)
                terminal = {e['operation'] for e in events if e.get('event') in ('verified', 'held', 'error')}
                unresolved = [e for e in events if e.get('event') == 'intent' and e['operation'] not in terminal]
                latest = {}
                for e in events:
                    if e.get('event') in ('verified', 'held', 'error'):
                        latest[e['session_id']] = e
                result = {'unresolved_requests': len(unresolved), 'snapshot_sessions': len(m['sessions']), 'archived_receipts': sum(x.get('event') == 'verified' and x['action'] == 'archive' for x in latest.values()), 'held_or_errors': sum(x.get('event') != 'verified' for x in latest.values())}
                if args.verify_sources:
                    manifest(run, check_host=True)
                    records = inventory(m['state_db'])
                    originals = {x['id']: x for x in m['sessions']}
                    checks = {sid: readback(records.get(sid), originals[sid], x['action'] == 'archive') for sid, x in latest.items() if x['event'] == 'verified'}
                    result['source_readback'] = checks
                    if unresolved or not all(checks.values()):
                        print(json.dumps(result))
                        return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
        print(json.dumps({'error': str(e), 'changed_by_this_command': 'See durable archive-journal.jsonl if --apply was used'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
