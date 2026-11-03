"""Six regressions use real subprocesses, disk and HTTP on BOTH revisions."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

PROJECT = Path(os.environ.get('LAB_TEST_TARGET', Path(__file__).resolve().parents[1])).resolve()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hw8910-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        self.command('init')

    def command(self, *args, good=True):
        result = subprocess.run([sys.executable, str(PROJECT / 'lab.py'), '--workspace', str(self.root), *args],
                                cwd=PROJECT, env={**os.environ, 'PYTHONUTF8': '1'},
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        if good:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def json(self, name):
        return json.loads((self.root / name).read_text(encoding='utf-8'))

    def start_server(self, warmup=0):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        log = (self.base / f'server-{port}.log').open('w', encoding='utf-8')
        process = subprocess.Popen([sys.executable, str(PROJECT / 'service.py'), '--workspace', str(self.root),
                                    '--port', str(port), '--warmup-seconds', str(warmup)],
                                   cwd=PROJECT, env={**os.environ, 'PYTHONUTF8': '1'},
                                   stdout=log, stderr=subprocess.STDOUT)
        def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.close()
        self.addCleanup(stop)
        self.url = f'http://127.0.0.1:{port}'
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try:
                if self.http('/health')[0] == 200:
                    return stop
            except OSError:
                pass
            if process.poll() is not None:
                log.flush()
                self.fail((self.base / f'server-{port}.log').read_text(encoding='utf-8'))
            time.sleep(.05)
        self.fail('HTTP server did not start')

    def http(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.url + path, data=data,
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def wait_ready(self):
        for _ in range(200):
            if self.http('/ready')[0] == 200:
                return
            time.sleep(.05)
        self.fail('Model did not become ready')

    def test_01_crash_cache_and_changed_input(self):
        failed = self.command('run', '--fail-after-prepare', good=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('PREPARED', failed.stdout)
        self.assertIn('CACHE_HIT', self.command('run').stdout)
        with (self.root / 'input.txt').open('ab') as stream:
            stream.write(b'\nnew input at the same path\n')
        self.assertIn('PREPARED', self.command('run').stdout)

    def test_02_reports_rejected_without_pointer_change(self):
        before = (self.root / 'accepted.json').read_bytes()
        for report in ('report-incomplete.json', 'report-reject.json', 'report-A.json'):
            with self.subTest(report=report):
                (self.root / 'accepted.json').write_bytes(before)
                result = self.command('run', '--report', report, good=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual((self.root / 'accepted.json').read_bytes(), before)

    def test_03_repeat_has_one_publication(self):
        self.command('run')
        before = (self.root / 'accepted.json').read_bytes()
        self.command('run')
        rows = (self.root / 'publications.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((self.root / 'accepted.json').read_bytes(), before)

    def test_04_http_not_ready_during_warmup(self):
        self.start_server(warmup=2)
        self.assertEqual(self.http('/health')[0], 200)
        self.assertEqual(self.http('/ready')[0], 503)
        self.assertEqual(self.http('/generate', {'prompt': 'hello'})[0], 503)
        self.wait_ready()
        self.assertEqual(self.http('/generate', {'prompt': 'hello'})[0], 200)

    def test_05_http_loaded_version_before_and_after_restart(self):
        stop = self.start_server()
        self.wait_ready()
        self.command('run')
        status, answer = self.http('/generate', {'prompt': 'hello'})
        self.assertEqual(status, 200)
        self.assertIn('fixture-A', answer['answer'])
        self.assertEqual(answer['model_id'], 'fixture-A')
        stop()
        stop = self.start_server()
        self.wait_ready()
        self.assertEqual(self.http('/generate', {'prompt': 'hello'})[1]['model_id'], 'fixture-B')

    def test_06_http_invalid_requests_never_call_backend(self):
        self.start_server()
        self.wait_ready()
        before = self.http('/debug')[1]['calls']
        invalid = [{'prompt': ''}, {'prompt': 'x', 'max_new_tokens': 0},
                   {'prompt': 'x', 'max_new_tokens': 100000}, {'prompt': 'x', 'max_new_tokens': '2'},
                   {'prompt': 'x', 'max_new_tokens': True}, {'prompt': 'x' * 8001}]
        for request in invalid:
            with self.subTest(request=str(request)[:80]):
                status, _ = self.http('/generate', request)
                self.assertTrue(400 <= status < 500, status)
                self.assertEqual(self.http('/debug')[1]['calls'], before)
        self.assertEqual(self.http('/generate', {'prompt': 'hello', 'max_new_tokens': 16})[0], 200)
        self.assertEqual(self.http('/debug')[1]['calls'], before + 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
