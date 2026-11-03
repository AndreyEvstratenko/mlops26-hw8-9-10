"""Compatible lightweight CLI; all fixture data is explicitly synthetic."""
import argparse
from pathlib import Path
from pipeline import prepare, run
from release import canonical, digest, read, write


def initialize(root):
    root = Path(root)
    if root.exists():
        raise FileExistsError('Workspace exists. Choose a new --workspace.')
    root.mkdir(parents=True)
    runtime = {'base_revision': 'fixture-only', 'tokenizer_sha256': 'a'*64,
               'template_sha256': 'b'*64, 'generation': {'max_new_tokens': 32, 'do_sample': False},
               'dtype': 'float32'}
    for version in ('A', 'B'):
        artifact = root / f'model-{version}.txt'
        artifact.write_text(f'STUB ONLY — version {version}\n', encoding='utf-8')
        files = {artifact.name: digest(artifact.read_bytes())}
        manifest = {'schema_version': 1, 'kind': 'fixture', 'model_id': f'fixture-{version}',
                    'artifact_uri': artifact.name, 'model_sha256': digest(canonical(files)),
                    'protocol_sha256': 'd'*64, 'golden_sha256': 'e'*64,
                    'runtime_sha256': digest(canonical(runtime)), 'runtime': runtime, 'file_hashes': files}
        report = {k: manifest[k] for k in ('model_id', 'model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256')}
        report.update(status='complete', passed=True, evaluation_run_id=f'fixture-eval-{version}',
                      metrics={'accuracy': .8, 'format_rate': 1.0}, synthetic=True)
        write(root / f'manifest-{version}.json', manifest)
        write(root / f'report-{version}.json', report)
    write(root / 'accepted.json', read(root / 'manifest-A.json'))
    report = read(root / 'report-B.json')
    report.update(status='running', passed='false')
    write(root / 'report-incomplete.json', report)
    report = read(root / 'report-B.json')
    report['passed'] = False
    write(root / 'report-reject.json', report)
    write(root / 'config.json', {'tokenizer_revision': 'r1', 'template_sha256': 'b'*64,
                                'max_length': 128, 'code_revision': 'prepare-v1'})
    (root / 'input.txt').write_text('Первый пример\nВторой пример\n', encoding='utf-8')
    print('Initialized STUB ONLY workspace:', root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path('.lab'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    execute = sub.add_parser('run')
    execute.add_argument('--report', default='report-B.json')
    execute.add_argument('--manifest', default='manifest-B.json')
    execute.add_argument('--fail-after-prepare', action='store_true')
    execute.add_argument('--fail-after-pointer', action='store_true')
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args.workspace)
    else:
        run(args.workspace, args.report, args.fail_after_prepare, args.manifest, args.fail_after_pointer)


if __name__ == '__main__':
    main()
