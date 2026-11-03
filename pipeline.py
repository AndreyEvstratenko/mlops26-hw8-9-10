"""Durable preparation and publication, one writer, write-ahead intent."""
import json
import os
import uuid
from pathlib import Path
from release import atomic_bytes, cache_key, canonical, digest, file_digest, publish, read, validate_release, verify_files, write


def preparation_config(root):
    return {**read(Path(root) / 'config.json'), 'implementation_sha256': file_digest(Path(__file__))}


def prepare(root):
    root = Path(root)
    config = preparation_config(root)
    key = cache_key(root / 'input.txt', config)
    entry = root / 'cache' / (key + '.json')
    if entry.exists():
        result = read(entry)
        if result.get('cache_key') != key:
            raise ValueError('Damaged preparation cache')
        print('CACHE_HIT', key, flush=True)
    else:
        payload = (root / 'input.txt').read_bytes()
        rows = [' '.join(row.split()) for row in payload.decode('utf-8').splitlines() if row.strip()]
        if not rows:
            raise ValueError('Empty preparation input')
        normalized = root / 'cache' / (key + '.txt')
        atomic_bytes(normalized, ('\n'.join(rows) + '\n').encode('utf-8'))
        result = {'cache_key': key, 'input_sha256': digest(payload), 'rows': len(rows),
                  'config': config, 'prepared_uri': str(normalized.resolve()),
                  'prepared_sha256': digest(normalized.read_bytes())}
        write(entry, result)
        print('PREPARED', key, flush=True)
    if digest(Path(result['prepared_uri']).read_bytes()) != result['prepared_sha256']:
        raise ValueError('Prepared file changed')
    return result


def events(root):
    path = Path(root) / 'publications.jsonl'
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def save_event(root, event):
    current = events(root)
    if not any(row['release_id'] == event['release_id'] for row in current):
        current.append(event)
        atomic_bytes(Path(root) / 'publications.jsonl', b''.join(canonical(row) + b'\n' for row in current))


def recover(root):
    root = Path(root)
    pending = root / 'pending-publication.json'
    if pending.exists():
        item = read(pending)
        target = root / 'accepted.json'
        if target.exists() and read(target) == item['manifest']:
            save_event(root, item['event'])
            print('RECOVERED_PUBLICATION', item['event']['release_id'], flush=True)
        pending.unlink()


def commit_release(root, manifest, report, prepared, fail_after_pointer=False):
    root = Path(root)
    validate_release(manifest, report)
    verify_files(manifest, root)
    if manifest.get('kind') in ('base', 'adapter'):
        from hw67_bridge import verify_evidence
        verify_evidence(manifest, report)
    elif report.get('synthetic') is not True:
        raise ValueError('Fixture reports must be explicitly synthetic')
    recover(root)
    identity = digest(canonical({'manifest': manifest, 'report': report}))
    event = {'release_id': identity, 'model_id': manifest['model_id'],
             'runtime_sha256': manifest['runtime_sha256'],
             'evaluation_run_id': report['evaluation_run_id'],
             'prepared': prepared['input_sha256']}
    write(root / 'history' / (identity + '.json'), {'manifest': manifest, 'report': report})
    write(root / 'pending-publication.json', {'manifest': manifest, 'event': event})
    publish(manifest, report, root / 'accepted.json')
    if fail_after_pointer:
        raise RuntimeError('Injected failure after pointer, before publication journal')
    existed = any(row['release_id'] == identity for row in events(root))
    save_event(root, event)
    (root / 'pending-publication.json').unlink()
    print('ALREADY_PUBLISHED' if existed else 'PUBLISHED', manifest['model_id'], identity, flush=True)
    return event


def attempt(root, status, **fields):
    path = Path(root) / 'attempts.jsonl'
    with path.open('ab') as stream:
        stream.write(canonical({'attempt_id': str(uuid.uuid4()), 'status': status, **fields}) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def run(root, report_name='report-B.json', fail_after_prepare=False,
        manifest_name='manifest-B.json', fail_after_pointer=False):
    root = Path(root)
    try:
        prepared = prepare(root)
        if fail_after_prepare:
            raise RuntimeError('Injected failure after preparation')
        result = commit_release(root, read(root / manifest_name), read(root / report_name),
                                prepared, fail_after_pointer)
        attempt(root, 'complete', release_id=result['release_id'])
        return result
    except Exception as exc:
        attempt(root, 'failed', error=f'{type(exc).__name__}: {exc}')
        raise
