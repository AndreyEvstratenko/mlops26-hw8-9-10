"""Publish verified baseline via Prefect; export reviewable release metadata."""
import os
from pathlib import Path
import subprocess
import sys
from release import read, write

ROOT = Path(__file__).resolve().parent


def main():
    out = ROOT / 'evidence' / 'release'
    out.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / 'flow.py'), '--workspace', str(ROOT / 'runtime'),
               '--manifest', 'manifest.json', '--report', 'release-report.json']
    result = subprocess.run(command, cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', timeout=180)
    (out / 'publish.log').write_text(result.stdout, encoding='utf-8')
    write(out / 'command.json', {'command': command, 'exit_code': result.returncode})
    if result.returncode:
        print(result.stdout)
        return result.returncode
    for name in ('manifest.json', 'release-report.json', 'accepted.json', 'rejected-candidate.json'):
        write(out / name, read(ROOT / 'runtime' / name))
    print('Published verified pinned baseline; rejected candidate remains rejected.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
