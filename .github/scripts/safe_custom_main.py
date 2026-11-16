#!/usr/bin/env python3
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

CUSTOM = Path('.github/workflows/main-6.1.x-kernelsupin-nods.yml')
BACKUP = Path(str(CUSTOM) + '.bak')
STATE = Path('.github/.custom-main-sync.sha')
SOURCES = [Path('.github/workflows/main.yml'), Path(__file__), Path('.github/scripts/apply_custom_main.py')]

def run(args):
    subprocess.run(args, check=True)

def main():
    h = hashlib.sha256()
    for p in SOURCES:
        h.update(str(p).encode() + b'\0' + p.read_bytes() + b'\0')
    fp = h.hexdigest()
    if CUSTOM.exists() and STATE.exists() and STATE.read_text().strip() == fp:
        print('Unchanged source and scripts; output exists; skipping')
        return
    fd, name = tempfile.mkstemp(prefix='custom-main-candidate-', suffix='.yml', dir=CUSTOM.parent)
    os.close(fd)
    candidate = Path(name)
    try:
        run(['python3', '.github/scripts/apply_custom_main.py', str(candidate)])
        run(['actionlint', str(candidate)])
        changed = not CUSTOM.exists() or CUSTOM.read_bytes() != candidate.read_bytes()
        if changed:
            if CUSTOM.exists():
                old = CUSTOM.read_bytes()
                BACKUP.unlink(missing_ok=True)
                BACKUP.write_bytes(old)
            os.replace(candidate, CUSTOM)
        STATE.write_text(fp + '\n')
        for p in [CUSTOM, BACKUP, STATE]:
            if p.exists():
                run(['git', 'add', str(p)])
    finally:
        candidate.unlink(missing_ok=True)

if __name__ == '__main__':
    main()
