#!/usr/bin/env python3
"""Build a deterministic Skill ZIP from an explicit public-file allowlist."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/codex-session-distiller'
FILES = (
    'SKILL.md', 'agents/openai.yaml',
    'scripts/session_distiller.py', 'scripts/csd_core.py',
    'scripts/csd_summary.py', 'scripts/csd_archive.py',
    'references/workflow.md', 'references/summarizer.md', 'references/compatibility.md',
)

def build(out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in (*FILES, 'LICENSE'):
            p = ROOT / 'LICENSE' if name == 'LICENSE' else SKILL / name
            if not p.is_file() or p.is_symlink():
                raise ValueError('Missing or symlinked public file: ' + name)
            info = zipfile.ZipInfo('codex-session-distiller/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix + '.sha256').write_text(sha + '  ' + out.name + '\n')
    return sha

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default=str(ROOT / 'dist/codex-session-distiller-v0.1.0.zip'))
    args = p.parse_args()
    print(build(args.out))
