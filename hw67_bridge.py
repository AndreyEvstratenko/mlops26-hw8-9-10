"""Offline provenance bridge. Recompute old scores and gate; never edit old reports."""
import argparse
import importlib
import os
from pathlib import Path
import shutil
import sys
from release import canonical, digest, read, write, verify_files, file_digest

PROJECT = Path(__file__).resolve().parent
SNAPSHOT = PROJECT / 'evidence' / 'hw67'


def old_modules():
    sys.path.insert(0, str(SNAPSHOT)) if str(SNAPSHOT) not in sys.path else None
    return importlib.import_module('evaluation.contract'), importlib.import_module('evaluation.solution')


def recompute():
    contract, solution = old_modules()
    config = read(SNAPSHOT / 'evaluation' / 'params.json')
    rows = contract.read_rows(SNAPSHOT / config['golden'])
    behavior = contract.read_rows(SNAPSHOT / config['behavioral'])
    training = contract.read_rows(SNAPSHOT / config['training_index'])
    proof = contract.validate_golden(rows, training, behavior)
    baseline_spec = read(SNAPSHOT / 'evaluation' / 'baseline.json')
    protocol = dict(config=config, golden=rows, behavioral=behavior, training_index=training,
                    evaluation_code={p.name: p.read_text(encoding='utf-8')
                                     for p in sorted((SNAPSHOT / 'evaluation').glob('*.py'))})
    ph = contract.fingerprint(protocol)
    reports = {}
    for role in ('baseline', 'candidate'):
        report = read(SNAPSHOT / 'metrics' / f'{role}-eval.json')
        outputs = read(SNAPSHOT / 'metrics' / f'{role}-outputs.json')
        if report['protocol_sha256'] != ph or report['golden_sha256'] != contract.fingerprint(rows):
            raise ValueError('Old protocol or golden set changed; reevaluation required')
        if report['outputs_sha256'] != contract.fingerprint(outputs):
            raise ValueError('Old raw outputs changed')
        if report['baseline_spec_sha256'] != contract.fingerprint(baseline_spec):
            raise ValueError('Baseline spec changed')
        by_id = {o['id']: o for o in outputs}
        if len(by_id) != len(outputs) or set(by_id) != {r['id'] for r in rows + behavior}:
            raise ValueError('Raw output IDs do not match evaluation inputs')
        measured = solution.score(rows, [by_id[r['id']] for r in rows])
        for key in ('overall', 'slices', 'per_item'):
            if measured[key] != report[key]:
                raise ValueError(f'Old {role} metrics changed: {key}')
        if report['behavioral'] != solution.score(behavior, [by_id[r['id']] for r in behavior]):
            raise ValueError('Old behavioral result changed')
        if report['leakage'] != proof or report['status'] != 'complete':
            raise ValueError('Incomplete or invalid leakage proof')
        reports[role] = report
    if reports['baseline']['model'] != baseline_spec:
        raise ValueError('Baseline model mismatch')
    if reports['candidate']['model'] != read(SNAPSHOT / 'metrics' / 'candidate-model.json'):
        raise ValueError('Candidate training run mismatch')
    try:
        decision = solution.quality_gate(reports['baseline'], reports['candidate'], config['critical_slices'])
    except ValueError as exc:
        decision = {'passed': False, 'reason': str(exc)}
    return config, rows, reports, decision


def evidence_hashes():
    return {str(p.relative_to(PROJECT)).replace('\\', '/'): digest(p.read_bytes())
            for p in sorted(SNAPSHOT.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def verify_evidence(manifest, report):
    hashes = evidence_hashes()
    if report.get('source_files') != hashes:
        raise ValueError('Source evidence changed')
    config, rows, reports, decision = recompute()
    if decision != report.get('candidate_gate'):
        raise ValueError('Gate decision does not match original reports')
    if report.get('decision_type') != 'retain_pinned_baseline_after_rejection' or decision['passed'] is not False:
        raise ValueError('This bridge authorizes baseline retention only after actual rejection')
    baseline = reports['baseline']
    if manifest.get('kind') != 'base' or manifest['source_model'] != baseline['model']:
        raise ValueError('Fallback must be the pinned original baseline')
    if (manifest['protocol_sha256'] != baseline['protocol_sha256'] or
            manifest['golden_sha256'] != baseline['golden_sha256'] or
            report['evaluation_run_id'] != baseline['evaluation_run_id'] or
            report['metrics'] != {k: baseline['overall'][k] for k in ('accuracy', 'format_rate')}):
        raise ValueError('Baseline evidence binding mismatch')
    runtime = manifest['runtime']
    expected_generation = {k: config[k] for k in ('system', 'seed', 'enable_thinking', 'max_new_tokens')}
    expected_generation['do_sample'] = False
    if runtime['generation'] != expected_generation or runtime['base_revision'] != baseline['model']['revision']:
        raise ValueError('Runtime differs from evaluated baseline')
    if runtime['dtype'] != 'float32' or runtime['device'] != 'cpu':
        raise ValueError('Saved evaluation used float32 on CPU; a change requires reevaluation')
    if manifest['runtime_sha256'] != report['runtime_sha256']:
        raise ValueError('Runtime identity mismatch')
    return decision


def snapshot(source):
    source = Path(source)
    if SNAPSHOT.exists():
        raise FileExistsError('Evidence snapshot already exists; use a new project for a different evaluation')
    SNAPSHOT.mkdir(parents=True)
    for folder in ('evaluation', 'metrics', 'tests', 'src', 'verification'):
        shutil.copytree(source / folder, SNAPSHOT / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(source / 'data' / 'eval', SNAPSHOT / 'data' / 'eval')
    for name in ('params.yaml', 'pyproject.toml', 'uv.lock'):
        shutil.copy2(source / name, SNAPSHOT / name)
    (SNAPSHOT / 'docs').mkdir()
    for name in ('conclusion.md', 'evaluation-protocol.md', 'eval-data-proof.json'):
        shutil.copy2(source / 'docs' / name, SNAPSHOT / 'docs' / name)


def bootstrap(source, base_dir):
    if not SNAPSHOT.exists():
        snapshot(source)
    config, rows, reports, decision = recompute()
    if decision['passed']:
        raise ValueError('Candidate passed; use an adapter manifest rather than baseline-retention policy')
    # Files are copied out of the read-only HF snapshot, never modified in place.
    artifact = PROJECT / 'artifacts' / 'baseline'
    artifact.mkdir(parents=True, exist_ok=True)
    for p in Path(base_dir).iterdir():
        if p.is_file():
            target = artifact / p.name
            if not target.exists():
                shutil.copy2(p, target)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(artifact, local_files_only=True)
    files = {p.name: file_digest(p) for p in sorted(artifact.iterdir()) if p.is_file()}
    tokenizer_files = {k: v for k, v in files.items() if k in
                       ('tokenizer.json', 'tokenizer_config.json', 'merges.txt', 'vocab.json', 'added_tokens.json', 'special_tokens_map.json')}
    generation = {k: config[k] for k in ('system', 'seed', 'enable_thinking', 'max_new_tokens')}
    generation['do_sample'] = False
    runtime = {'base_revision': reports['baseline']['model']['revision'],
               'tokenizer_sha256': digest(canonical(tokenizer_files)),
               'tokenizer_files': tokenizer_files,
               'template_sha256': digest(canonical(tokenizer.chat_template)),
               'generation': generation, 'dtype': 'float32', 'device': 'cpu',
               'max_input_tokens': 2048}
    manifest = {'schema_version': 1, 'kind': 'base',
                'model_id': 'baseline-Qwen3-0.6B-' + runtime['base_revision'],
                'artifact_uri': '../artifacts/baseline', 'file_hashes': files,
                'model_sha256': digest(canonical(files)), 'runtime': runtime,
                'runtime_sha256': digest(canonical(runtime)),
                'source_model': reports['baseline']['model'],
                'protocol_sha256': reports['baseline']['protocol_sha256'],
                'golden_sha256': reports['baseline']['golden_sha256']}
    report = {k: manifest[k] for k in ('model_id', 'model_sha256', 'runtime_sha256', 'protocol_sha256', 'golden_sha256')}
    report.update(status='complete', passed=decision['passed'] is False,
                  decision_type='retain_pinned_baseline_after_rejection', candidate_gate=decision,
                  evaluation_run_id=reports['baseline']['evaluation_run_id'],
                  training_run_id=reports['candidate']['model']['run_id'],
                  metrics={k: reports['baseline']['overall'][k] for k in ('accuracy', 'format_rate')},
                  source_files=evidence_hashes(),
                  note='passed authorizes retention of pinned baseline; candidate quality gate remains FAILED')
    verify_evidence(manifest, report)
    root = PROJECT / 'runtime'
    root.mkdir(exist_ok=True)
    write(root / 'manifest.json', manifest)
    write(root / 'release-report.json', report)
    write(root / 'rejected-candidate.json', {'model': reports['candidate']['model'], 'gate': decision,
                                          'evaluation_run_id': reports['candidate']['evaluation_run_id']})
    write(root / 'config.json', {'tokenizer_revision': runtime['base_revision'],
                               'tokenizer_sha256': runtime['tokenizer_sha256'],
                               'template_sha256': runtime['template_sha256'], 'max_length': 2048,
                               'code_revision': digest((PROJECT / 'pipeline.py').read_bytes())})
    (root / 'input.txt').write_text('\n'.join(r['input'] for r in rows) + '\n', encoding='utf-8')
    verify_files(manifest, root)
    print('Verified saved evaluation; candidate rejected:', decision['reason'])
    print('Baseline manifest:', root / 'manifest.json')
    print('No training or new full evaluation was performed.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--base-dir', type=Path, required=True)
    args = p.parse_args()
    bootstrap(args.source, args.base_dir)
