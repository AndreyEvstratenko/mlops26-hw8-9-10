"""FastAPI service with asynchronous startup, strict requests and a pinned loaded manifest."""
import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import threading
import time
import uuid
from pathlib import Path

from backends import StubBackend, TransformersBackend
from release import canonical, digest, read, verify_files


class NotReady(Exception):
    pass


class ServiceState:
    def __init__(self, root, warmup_seconds=0, backend='stub'):
        self.root = Path(root)
        self.ready = False
        self.error = None
        self.backend = None
        self.manifest = None
        self.backend_name = backend
        self.warmup_seconds = warmup_seconds
        self.warmup_finished = threading.Event()
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    def load(self):
        try:
            manifest = read(self.root / 'accepted.json')
            if digest(canonical(manifest['runtime'])) != manifest['runtime_sha256']:
                raise ValueError('Runtime changed')
            artifact = verify_files(manifest, self.root)
            if self.backend_name == 'stub' and manifest.get('kind', 'fixture') != 'fixture':
                raise ValueError('Real artifacts cannot be served as STUB')
            if self.backend_name == 'transformers' and manifest.get('kind') not in ('base', 'adapter'):
                raise ValueError('STUB cannot be served as a real model')
            cls = StubBackend if self.backend_name == 'stub' else TransformersBackend
            self.backend = cls(manifest, artifact)
            self.manifest = manifest
            # Warmup is an actual backend call; request validation tests compare against this baseline.
            self.backend.generate('Выбери вариант: A. Да; B. Нет.', manifest['runtime']['generation']['max_new_tokens'])
            if self.stopping.wait(self.warmup_seconds):
                return
            self.warmup_finished.set()
            self.ready = True
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
            import logging
            logging.getLogger('uvicorn.error').exception('Model startup failed; /ready remains 503')

    def validate_request(self, request):
        if not isinstance(request, dict) or set(request) - {'prompt', 'max_new_tokens'}:
            raise ValueError('Expected object with prompt and optional max_new_tokens')
        prompt = request.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
            raise ValueError('prompt must contain 1..8000 characters')
        limit = request.get('max_new_tokens', self.manifest['runtime']['generation']['max_new_tokens'])
        maximum = self.manifest['runtime']['generation']['max_new_tokens']
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise ValueError(f'max_new_tokens must be an integer in 1..{maximum}')
        # A real eval certifies its fixed decoding budget; fixture supports 1..32 for tests.
        if self.backend_name == 'transformers' and limit != maximum:
            raise ValueError(f'This evaluated runtime requires max_new_tokens={maximum}')
        if hasattr(self.backend, 'validate_prompt'):
            self.backend.validate_prompt(prompt)
        return prompt, limit

    def generate(self, request):
        if not self.ready:
            raise NotReady('Model is loading or warmup failed')
        prompt, limit = self.validate_request(request)
        started = time.perf_counter()
        with self.lock:
            compute_started = time.perf_counter()
            result = self.backend.generate(prompt, limit)
            compute = time.perf_counter() - compute_started
        result.update(model_id=self.backend.loaded_id, runtime_sha256=self.backend.runtime_sha256,
                      request_id=str(uuid.uuid4()), generation_seconds=compute,
                      queue_seconds=compute_started-started, total_seconds=time.perf_counter()-started)
        return result


def create_app(root, warmup_seconds=0, backend='stub', debug=False):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    state = ServiceState(root, warmup_seconds, backend)

    @asynccontextmanager
    async def lifespan(app):
        loader = asyncio.create_task(asyncio.to_thread(state.load))
        yield
        state.stopping.set()
        with suppress(asyncio.CancelledError):
            await loader

    app = FastAPI(title='MLOps 8–9–10', lifespan=lifespan)
    app.state.model = state

    @app.get('/health')
    def health():
        return {'alive': True, 'backend': 'STUB ONLY' if backend == 'stub' else 'transformers'}

    @app.get('/ready')
    def ready():
        return JSONResponse(status_code=200 if state.ready else 503,
                            content={'ready': state.ready,
                                     'model_id': state.backend.loaded_id if state.ready else None})

    if debug:
        @app.get('/debug')
        def diagnostic():
            return {'loaded_id': state.backend.loaded_id if state.backend else None,
                    'calls': state.backend.calls if state.backend else 0,
                    'warmup_finished': state.warmup_finished.is_set(), 'error': state.error}

    @app.post('/generate')
    async def generate(request: Request):
        try:
            # Bound the actual stream as well as Content-Length before JSON parsing.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    raise HTTPException(status_code=413, detail='Request body too large')
            import json
            payload = json.loads(body)
            return await asyncio.to_thread(state.generate, payload)
        except NotReady as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, TypeError, UnicodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path('.lab'))
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--warmup-seconds', type=float, default=0)
    parser.add_argument('--backend', choices=['stub', 'transformers'], default='stub')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.workspace, args.warmup_seconds, args.backend, args.debug or args.backend == 'stub'),
                host='127.0.0.1', port=args.port, log_level='info')


if __name__ == '__main__':
    main()
