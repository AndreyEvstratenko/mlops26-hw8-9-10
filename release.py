"""Release contracts. Standard library only; no model is imported here."""
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_bytes(target, data):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def write(path, value):
    atomic_bytes(path, canonical(value) + b'\n')


def cache_key(data_path: Path, config: dict) -> str:
    return digest(canonical({'data_sha256': digest(Path(data_path).read_bytes()), 'config': config}))


def validate_release(manifest: dict, report: dict) -> None:
    try:
        if not isinstance(manifest, dict) or not isinstance(report, dict):
            raise ValueError('Manifest and report must be objects')
        if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
            raise ValueError('Unsupported schema_version')
        for key in ('model_id', 'artifact_uri'):
            if not isinstance(manifest[key], str) or not manifest[key].strip():
                raise ValueError(f'Invalid {key}')
        for key in ('model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256'):
            if not isinstance(manifest[key], str) or not re.fullmatch('[0-9a-f]{64}', manifest[key]):
                raise ValueError(f'Invalid {key}')
            if report[key] != manifest[key]:
                raise ValueError(f'Report mismatch: {key}')
        runtime = manifest['runtime']
        if not isinstance(runtime, dict) or not runtime or digest(canonical(runtime)) != manifest['runtime_sha256']:
            raise ValueError('Runtime hash mismatch')
        if report['model_id'] != manifest['model_id']:
            raise ValueError('Report model mismatch')
        if report['status'] != 'complete' or report['passed'] is not True:
            raise ValueError('Incomplete evaluation or quality rejection')
        if not isinstance(report['evaluation_run_id'], str) or not report['evaluation_run_id'].strip():
            raise ValueError('Missing evaluation run')
        for key in ('accuracy', 'format_rate'):
            value = report['metrics'][key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'Invalid metric: {key}')
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError(f'Malformed release: {exc}') from exc


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    atomic_bytes(target, canonical(manifest) + b'\n')


def verify_files(manifest, root):
    """Verify pinned local files without downloads or executing remote code."""
    files = manifest.get('file_hashes')
    if not isinstance(files, dict) or not files:
        raise ValueError('Missing pinned file_hashes')
    artifact = Path(manifest['artifact_uri'])
    if not artifact.is_absolute():
        artifact = Path(root) / artifact
    base = artifact.parent if artifact.is_file() else artifact
    actual = {}
    for name, expected in files.items():
        path = (base / name).resolve()
        if not path.is_relative_to(base.resolve()):
            raise ValueError('Artifact path escapes its root')
        actual[name] = file_digest(path)
        if actual[name] != expected:
            raise ValueError(f'Artifact changed: {name}')
    if digest(canonical(actual)) != manifest['model_sha256']:
        raise ValueError('Artifact aggregate hash mismatch')
    runtime = manifest['runtime']
    if manifest.get('kind') in ('base', 'adapter'):
        tokenizer_files = runtime.get('tokenizer_files')
        if (not isinstance(tokenizer_files, dict) or not tokenizer_files or
                any(actual.get(k) != v for k, v in tokenizer_files.items()) or
                digest(canonical(tokenizer_files)) != runtime['tokenizer_sha256']):
            raise ValueError('Tokenizer files or aggregate hash mismatch')
    return artifact
