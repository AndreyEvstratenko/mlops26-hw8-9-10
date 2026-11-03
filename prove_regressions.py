"""Run the SAME six integration tests against original and final code, preserve both logs."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from local_network import configure_loopback

ROOT = Path(__file__).resolve().parent


def main():
    configure_loopback()
    out = ROOT / 'evidence' / 'regressions'
    out.mkdir(parents=True, exist_ok=True)
    original = subprocess.check_output(['git', 'rev-list', '--max-parents=0', 'HEAD'], cwd=ROOT).decode().strip()
    archive = subprocess.check_output(['git', 'archive', '--format=zip', original], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='original-') as directory:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            z.extractall(directory)
        results = {}
        for label, target in [('original', directory), ('fixed', str(ROOT))]:
            child = subprocess.run([sys.executable, str(ROOT / 'tests' / 'test_integration.py')], cwd=ROOT,
                                   env={**os.environ, 'PYTHONUTF8': '1', 'LAB_TEST_TARGET': target},
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   encoding='utf-8', timeout=180)
            (out / f'{label}.log').write_text(child.stdout, encoding='utf-8')
            results[label] = {'exit_code': child.returncode, 'target': target}
            print(label, 'exit code:', child.returncode)
        # Require six assertion failures, no broken imports/CLI/route names.
        log = (out / 'original.log').read_text(encoding='utf-8')
        missing = [f'test_0{i}' for i in range(1, 7) if f'FAIL: test_0{i}' not in log]
        if missing or 'ERROR:' in log or results['original']['exit_code'] == 0 or results['fixed']['exit_code'] != 0:
            raise RuntimeError(f'Invalid regression proof: missing failures {missing}')
        results.update(original_commit=original, six_assertion_failures_verified=True)
        contracts = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_release.py', '-v'],
                                   cwd=directory, env={**os.environ, 'PYTHONUTF8': '1'},
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', timeout=60)
        (out / 'original-contracts.log').write_text(contracts.stdout, encoding='utf-8')
        assert contracts.returncode != 0 and 'FAIL: test_failed_atomic_replace_preserves_previous_bytes' in contracts.stdout
        results['original_contract_exit_code'] = contracts.returncode
        (out / 'summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('All six regressions fail meaningfully before and pass after.')


if __name__ == '__main__':
    main()
