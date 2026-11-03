"""Choose local model environment without changing the previous homework."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from release import read

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stub', action='store_true')
    p.add_argument('--port', type=int, default=8091)
    args = p.parse_args()
    python = sys.executable
    if args.stub:
        from lab import initialize
        workspace = ROOT / '.lab-demo'
        if not workspace.exists():
            initialize(workspace)
        backend = 'stub'
    else:
        config = ROOT / 'local-config.json'
        if config.exists():
            python = read(config).get('model_python', python)
        workspace = ROOT / 'runtime'
        backend = 'transformers'
        if not (workspace / 'accepted.json').exists():
            raise SystemExit('First restore model files with prepare_model.py, then run publish_real.py. See README.md.')
    command = [str(python), '-u', str(ROOT / 'service.py'), '--workspace', str(workspace),
               '--backend', backend, '--port', str(args.port)]
    raise SystemExit(subprocess.call(command, cwd=ROOT,
                                    env={**os.environ, 'PYTHONUTF8': '1', 'HF_HUB_OFFLINE': '1', 'OPENBLAS_NUM_THREADS': '1'}))


if __name__ == '__main__':
    main()
