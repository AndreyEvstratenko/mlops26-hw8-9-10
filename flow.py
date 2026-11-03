"""Local Prefect flow. Only prepare is cached; publication always checks current files."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
(ROOT / '.prefect').mkdir(exist_ok=True)
(ROOT / '.runs' / 'tmp').mkdir(parents=True, exist_ok=True)
os.environ.setdefault('PREFECT_HOME', str(ROOT / '.prefect'))
os.environ.setdefault('PREFECT_SERVER_DATABASE_CONNECTION_URL',
                      'sqlite+aiosqlite:///' + (ROOT / '.prefect' / 'orchestration.db').as_posix())
os.environ.setdefault('SQLITE_TMPDIR', str(ROOT / '.runs' / 'tmp'))
os.environ['TEMP'] = str(ROOT / '.runs' / 'tmp')
os.environ['TMP'] = str(ROOT / '.runs' / 'tmp')
os.environ.setdefault('PREFECT_LOCAL_STORAGE_PATH', str(ROOT / '.prefect' / 'storage'))
os.environ.setdefault('PREFECT_SERVER_ANALYTICS_ENABLED', 'false')
os.environ.setdefault('PREFECT_CLIENT_ANALYTICS_ENABLED', 'false')

from prefect import flow, task, get_run_logger
from prefect.cache_policies import NO_CACHE
from pipeline import prepare, commit_release, attempt, preparation_config
from release import cache_key, digest, canonical, read, validate_release, verify_files


def preparation_key(context, parameters):
    root = Path(parameters['root'])
    content = cache_key(root / 'input.txt', preparation_config(root))
    # Local URI in task result is workspace-specific; input identity itself is path independent.
    return digest(canonical({'content': content, 'storage_root': str(root.resolve())}))


@task(cache_key_fn=preparation_key, persist_result=True,
      result_storage=ROOT / '.prefect' / 'storage')
def prepare_task(root):
    get_run_logger().info('Preparing and validating input; no training')
    return prepare(root)


@task(cache_policy=NO_CACHE)
def artifact_task(root, manifest_name):
    manifest = read(Path(root) / manifest_name)
    verify_files(manifest, root)
    return {'manifest_uri': str(Path(root) / manifest_name), 'model_id': manifest['model_id']}


@task(cache_policy=NO_CACHE)
def gate_task(root, artifact, report_name):
    manifest = read(artifact['manifest_uri'])
    report = read(Path(root) / report_name)
    validate_release(manifest, report)
    if manifest.get('kind') in ('base', 'adapter'):
        from hw67_bridge import verify_evidence
        verify_evidence(manifest, report)
    elif report.get('synthetic') is not True:
        raise ValueError('Fixture reports must be explicitly synthetic')
    return {'manifest_uri': artifact['manifest_uri'], 'report_uri': str(Path(root) / report_name)}


@task(cache_policy=NO_CACHE)
def publish_task(root, decision, prepared, fail_after_pointer):
    return commit_release(root, read(decision['manifest_uri']), read(decision['report_uri']),
                          prepared, fail_after_pointer)


@flow(name='model-release', persist_result=False)
def release_flow(root, manifest_name='manifest-B.json', report_name='report-B.json',
                 fail_after_prepare=False, fail_after_pointer=False):
    root = str(Path(root).resolve())
    try:
        state = prepare_task(root, return_state=True)
        prepared = state.result()
        get_run_logger().info('Preparation state=%s key=%s', state.name, prepared['cache_key'])
        if fail_after_prepare:
            raise RuntimeError('Injected failure after preparation')
        artifact = artifact_task(root, manifest_name)
        decision = gate_task(root, artifact, report_name)
        event = publish_task(root, decision, prepared, fail_after_pointer)
        attempt(root, 'complete', release_id=event['release_id'])
        return event
    except Exception as exc:
        attempt(root, 'failed', error=f'{type(exc).__name__}: {exc}')
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, default=Path('.lab'))
    p.add_argument('--manifest', default='manifest-B.json')
    p.add_argument('--report', default='report-B.json')
    p.add_argument('--fail-after-prepare', action='store_true')
    p.add_argument('--fail-after-pointer', action='store_true')
    args = p.parse_args()
    release_flow(args.workspace, args.manifest, args.report, args.fail_after_prepare, args.fail_after_pointer)
