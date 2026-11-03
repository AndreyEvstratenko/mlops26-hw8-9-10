"""Run real smoke + benchmark in a child server, then close only that child."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from benchmark import request, measure
from release import read, write

ROOT = Path(__file__).resolve().parent


def main(python, count):
    out = ROOT / 'evidence' / 'real-api'
    out.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (out / 'server.log').open('w', encoding='utf-8')
    command = [str(python), '-u', str(ROOT / 'service.py'), '--workspace', str(ROOT / 'runtime'),
               '--backend', 'transformers', '--port', str(port), '--debug']
    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, 'PYTHONUTF8': '1', 'HF_HUB_OFFLINE': '1',
                                    'OPENBLAS_NUM_THREADS': '1'})
    url = f'http://127.0.0.1:{port}'
    startup = []
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Real server exited; see evidence/real-api/server.log')
            try:
                status, body = request(url + '/ready', timeout=2)
                startup.append({'status': status, **body})
                debug = request(url + '/debug', timeout=2)[1]
                if debug.get('error'):
                    raise RuntimeError(debug['error'])
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(.5)
        else:
            raise TimeoutError('Real model startup exceeded 240 seconds')
        write(out / 'startup.json', startup)
        rows = [json.loads(s) for s in (ROOT / 'evidence/hw67/data/eval/golden.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
        limit = read(ROOT / 'runtime/manifest.json')['runtime']['generation']['max_new_tokens']
        prompt = rows[0]['input']
        before = request(url + '/debug')[1]['calls']
        invalid = request(url + '/generate', {'prompt': prompt, 'max_new_tokens': 100000})
        after = request(url + '/debug')[1]['calls']
        if not 400 <= invalid[0] < 500 or before != after:
            raise AssertionError('Invalid request called real backend')
        status, answer = request(url + '/generate', {'prompt': prompt, 'max_new_tokens': limit})
        manifest = read(ROOT / 'runtime/accepted.json')
        assert status == 200 and answer['model_id'] == manifest['model_id']
        assert answer['runtime_sha256'] == manifest['runtime_sha256'] and 'STUB ONLY' not in answer['answer']
        write(out / 'smoke.json', {'http_status': status, 'response': answer, 'prompt': prompt,
                                  'invalid_status': invalid[0], 'calls_before_invalid': before, 'calls_after_invalid': after,
                                  'health': request(url + '/health')[1], 'ready': request(url + '/ready')[1]})
        summary = measure(url, [r['input'] for r in rows[:count]], limit, ROOT / 'evidence' / 'benchmark')
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--python', type=Path, default=Path(sys.executable))
    p.add_argument('--count', type=int, choices=[4, 8], default=8)
    args = p.parse_args()
    main(args.python, args.count)
