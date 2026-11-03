"""Small nonstreaming measurement; includes every failure and timeout."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.request
import uuid
from release import read, write
from local_network import configure_loopback

configure_loopback()


def request(url, payload=None, timeout=120):
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def measure(url, prompts, limit, destination, timeout=120):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if request(url + '/ready')[0] != 200:
        raise RuntimeError('Model is not ready')
    rows, summaries = [], []
    def one(item, concurrency):
        index, prompt = item
        started = time.perf_counter()
        row = {'attempt_id': str(uuid.uuid4()), 'input_id': index, 'concurrency': concurrency,
               'max_new_tokens': limit, 'prompt': prompt, 'status': None, 'error': None,
               'model_id': None, 'request_id': None, 'generation_seconds': None,
               'queue_seconds': None, 'output_tokens': None}
        try:
            status, response = request(url + '/generate', {'prompt': prompt, 'max_new_tokens': limit}, timeout)
            row['status'] = status
            if status == 200:
                for key in ('model_id', 'runtime_sha256', 'request_id', 'generation_seconds', 'queue_seconds', 'output_tokens', 'answer'):
                    row[key] = response.get(key)
            else:
                row['error'] = json.dumps(response, ensure_ascii=False)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        row['client_seconds'] = time.perf_counter()-started
        return row
    for concurrency in (1, 2):
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            group = list(executor.map(lambda item: one(item, concurrency), enumerate(prompts)))
        seconds = time.perf_counter()-started
        rows.extend(group)
        success = [r for r in group if r['status'] == 200 and not r['error']]
        failed = [r for r in group if r not in success]
        summaries.append({'concurrency': concurrency, 'n': len(group), 'success': len(success), 'errors': len(failed),
                          'batch_seconds': seconds, 'successful_requests_per_second': len(success)/seconds,
                          'mean_success_seconds': statistics.mean(r['client_seconds'] for r in success) if success else None,
                          'max_success_seconds': max((r['client_seconds'] for r in success), default=None),
                          'mean_error_seconds': statistics.mean(r['client_seconds'] for r in failed) if failed else None,
                          'max_error_seconds': max((r['client_seconds'] for r in failed), default=None)})
        # Save after each group so a later group failure does not discard completed attempts.
        (destination / 'requests.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        write(destination / 'summary.json', summaries)
    return summaries


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://127.0.0.1:8091')
    p.add_argument('--prompts', type=Path, default=Path('evidence/hw67/data/eval/golden.jsonl'))
    p.add_argument('--count', type=int, default=8)
    p.add_argument('--limit', type=int, default=16)
    p.add_argument('--output', type=Path, default=Path('evidence/benchmark'))
    args = p.parse_args()
    prompts = [json.loads(s)['input'] for s in args.prompts.read_text(encoding='utf-8').splitlines() if s.strip()][:args.count]
    print(json.dumps(measure(args.url, prompts, args.limit, args.output), indent=2))
