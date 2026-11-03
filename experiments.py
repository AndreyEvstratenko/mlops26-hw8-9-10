"""Reproducible Prefect failure/cache/publication and HTTP A -> B -> A evidence."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from benchmark import request
from release import digest, read, write
from local_network import configure_loopback

ROOT = Path(__file__).resolve().parent


def run_experiments():
    configure_loopback()
    out = ROOT / 'evidence' / 'experiments'
    out.mkdir(parents=True, exist_ok=True)
    workspace = ROOT / '.runs' / ('experiments-' + uuid.uuid4().hex[:8])
    env = {**os.environ, 'PYTHONUTF8': '1'}
    recorded = []
    def call(label, script, args, success=True):
        command = [sys.executable, str(ROOT / script), *[str(a) for a in args]]
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding='utf-8', timeout=150)
        (out / f'{label}.log').write_text(result.stdout, encoding='utf-8')
        recorded.append({'label': label, 'command': command, 'exit_code': result.returncode})
        write(out / 'commands.json', recorded)
        print(label, 'exit:', result.returncode, flush=True)
        if (result.returncode == 0) != success:
            raise RuntimeError(f'Unexpected result: {label}; see evidence/experiments/{label}.log')
        return result.stdout
    call('00-init', 'lab.py', ['--workspace', workspace, 'init'])
    common = ['--workspace', workspace]
    log = call('01-crash-after-prepare', 'flow.py', common + ['--fail-after-prepare'], False)
    if 'Injected failure after preparation' not in log or 'PREPARED' not in log:
        tail = '\n'.join(log.splitlines()[-18:])
        raise RuntimeError(
            'Flow failed before the planned preparation failure. '
            'See evidence/experiments/01-crash-after-prepare.log. '
            'If SQLite reports unable to open database file, check access to the project .prefect folder.\n' + tail)
    log = call('02-resume', 'flow.py', common)
    assert 'Preparation state=Cached' in log
    with (workspace / 'input.txt').open('ab') as stream:
        stream.write(b'\nChanged content at same path\n')
    log = call('03-changed-input', 'flow.py', common)
    assert 'PREPARED' in log and 'Preparation state=Completed' in log
    call('04-repeat', 'flow.py', common)
    assert len((workspace / 'publications.jsonl').read_text(encoding='utf-8').splitlines()) == 1
    # Same model, different bound evaluation = a distinct release identity.
    report = read(workspace / 'report-B.json')
    report['evaluation_run_id'] = 'fixture-evaluation-v2'
    write(workspace / 'report-B.json', report)
    log = call('05-crash-after-pointer', 'flow.py', common + ['--fail-after-pointer'], False)
    assert 'Injected failure after pointer' in log
    log = call('06-reconcile', 'flow.py', common)
    assert 'RECOVERED_PUBLICATION' in log
    assert len((workspace / 'publications.jsonl').read_text(encoding='utf-8').splitlines()) == 2
    before = digest((workspace / 'accepted.json').read_bytes())
    call('07-rejection', 'flow.py', common + ['--report', 'report-reject.json'], False)
    after = digest((workspace / 'accepted.json').read_bytes())
    assert before == after
    write(out / 'pointer-rejection.json', {'before_sha256': before, 'after_sha256': after, 'unchanged': before == after})
    for name in ('publications.jsonl', 'attempts.jsonl'):
        (out / name).write_bytes((workspace / name).read_bytes())
    write(out / 'commands.json', recorded)
    http_cycle(out, env)
    print('All Prefect and HTTP experiments verified.')


def http_cycle(out, env):
    workspace = ROOT / '.runs' / ('http-cycle-' + uuid.uuid4().hex[:8])
    subprocess.run([sys.executable, str(ROOT / 'lab.py'), '--workspace', str(workspace), 'init'],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    snapshots = []
    def start(label):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        log = (out / f'http-{label}.log').open('w', encoding='utf-8')
        process = subprocess.Popen([sys.executable, str(ROOT / 'service.py'), '--workspace', str(workspace),
                                    '--port', str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        url = f'http://127.0.0.1:{port}'
        try:
            for _ in range(200):
                try:
                    if request(url + '/ready', timeout=1)[0] == 200:
                        return process, log, url
                except OSError:
                    pass
                time.sleep(.05)
            raise TimeoutError('HTTP cycle startup failed')
        except Exception:
            stop(process, log)
            raise
    def stop(process, log):
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
    def response(url, phase, expected):
        status, body = request(url + '/generate', {'prompt': 'Version check', 'max_new_tokens': 16})
        assert status == 200 and body['model_id'] == expected and expected in body['answer']
        snapshots.append({'phase': phase, 'http_status': status, 'response': body,
                          'pointer_model_id': read(workspace / 'accepted.json')['model_id']})
    process, log, url = start('A')
    try:
        response(url, 'initial-A', 'fixture-A')
        subprocess.run([sys.executable, str(ROOT / 'lab.py'), '--workspace', str(workspace), 'run'],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        response(url, 'B-published-A-still-loaded', 'fixture-A')
    finally:
        stop(process, log)
    process, log, url = start('B')
    try:
        response(url, 'B-after-restart', 'fixture-B')
        subprocess.run([sys.executable, str(ROOT / 'lab.py'), '--workspace', str(workspace), 'run',
                        '--manifest', 'manifest-A.json', '--report', 'report-A.json'],
                       cwd=ROOT, env=env, check=True, capture_output=True)
        response(url, 'A-restored-B-still-loaded', 'fixture-B')
    finally:
        stop(process, log)
    process, log, url = start('A-restored')
    try:
        response(url, 'A-after-rollback-restart', 'fixture-A')
    finally:
        stop(process, log)
    write(out / 'http-A-B-A.json', snapshots)


if __name__ == '__main__':
    run_experiments()
