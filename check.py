"""One fast command: no torch, model weights, MLflow or Prefect server."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def main():
    evidence = ROOT / 'evidence' / 'checks'
    evidence.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                            cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', timeout=180)
    (evidence / 'green.log').write_text(result.stdout, encoding='utf-8')
    print(result.stdout)
    legacy = ROOT / 'evidence' / 'hw67'
    legacy_exit = None
    if legacy.exists():
        old = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                             cwd=legacy, env={**os.environ, 'PYTHONUTF8': '1'},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', timeout=180)
        (evidence / 'hw67-preserved.log').write_text(old.stdout, encoding='utf-8')
        print(old.stdout)
        legacy_exit = old.returncode
    code = result.returncode or (legacy_exit or 0)
    summary = {'exit_code': code, 'hw8910_exit_code': result.returncode, 'hw67_exit_code': legacy_exit,
               'seconds': time.perf_counter()-started,
               'python': sys.version, 'command': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']}
    (evidence / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
