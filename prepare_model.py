"""Restore excluded model files on a different machine, pinned revision + SHA-256."""
import argparse
from pathlib import Path
import shutil
from release import digest, file_digest, read, write, verify_files

ROOT = Path(__file__).resolve().parent


def main(base_dir=None):
    saved = ROOT / 'evidence' / 'release'
    manifest = read(saved / 'manifest.json')
    if base_dir is None:
        from huggingface_hub import snapshot_download
        base_dir = Path(snapshot_download(manifest['source_model']['name'],
                                          revision=manifest['runtime']['base_revision'],
                                          allow_patterns=list(manifest['file_hashes'])))
    artifact = ROOT / 'artifacts' / 'baseline'
    artifact.mkdir(parents=True, exist_ok=True)
    for name, expected in manifest['file_hashes'].items():
        source = Path(base_dir) / name
        if file_digest(source) != expected:
            raise ValueError(f'Pinned model file mismatch: {name}')
        target = artifact / name
        if not target.exists() or file_digest(target) != expected:
            shutil.copy2(source, target)
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    write(runtime / 'manifest.json', manifest)
    write(runtime / 'release-report.json', read(saved / 'release-report.json'))
    write(runtime / 'rejected-candidate.json', read(saved / 'rejected-candidate.json'))
    cfg = manifest['runtime']
    write(runtime / 'config.json', {'tokenizer_revision': cfg['base_revision'],
                                   'tokenizer_sha256': cfg['tokenizer_sha256'],
                                   'template_sha256': cfg['template_sha256'],
                                   'max_length': cfg['max_input_tokens'],
                                   'code_revision': digest((ROOT / 'pipeline.py').read_bytes())})
    import json
    rows = [json.loads(s) for s in (ROOT / 'evidence/hw67/data/eval/golden.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    (runtime / 'input.txt').write_text('\n'.join(r['input'] for r in rows) + '\n', encoding='utf-8')
    verify_files(manifest, runtime)
    print('Model files restored and verified. Run publish_real.py with the fast environment.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--base-dir', type=Path)
    args = p.parse_args()
    main(args.base_dir)
