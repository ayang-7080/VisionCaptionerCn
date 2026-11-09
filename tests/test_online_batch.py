"""Bounded online scheduling, completion mapping, retries and cancellation."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def scheduler():
    import importlib.util
    assert importlib.util.find_spec("online_batch"), "Missing concurrent online scheduler"
    from online_batch import iter_online_results
    return iter_online_results


def test_concurrency_bound_and_out_of_order_mapping():
    run = scheduler()
    lock = threading.Lock()
    active = 0
    peak = 0
    def generate(paths, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.12 if paths[0] == "slow" else 0.02)
        with lock:
            active -= 1
        return ["caption:" + paths[0]]
    engine = SimpleNamespace(generate_batch=generate)
    pairs = [(name, None) for name in ("slow", "fast", "third", "fourth")]
    results = list(run(engine, pairs, concurrency=2))
    assert peak == 2
    assert results[0][0] == "fast"
    assert dict(results) == {p: "caption:" + p for p, _ in pairs}


def test_stop_drains_active_results_without_starting_more():
    run = scheduler()
    stop = threading.Event()
    both_started = threading.Barrier(2)
    calls = []
    def generate(paths, **kwargs):
        calls.append(paths[0])
        both_started.wait(timeout=2)
        stop.set()
        return ["finished:" + paths[0]]
    results = list(run(SimpleNamespace(generate_batch=generate),
                       [(str(i), None) for i in range(10)], concurrency=2, stop_event=stop.is_set))
    assert len(calls) == 2
    assert len(results) == 2


def test_stop_before_start_sends_no_requests():
    run = scheduler()
    engine = SimpleNamespace(generate_batch=MagicMock())
    assert list(run(engine, [("one", None)], concurrency=4, stop_event=lambda: True)) == []
    engine.generate_batch.assert_not_called()


def test_rate_limit_retries_and_auth_does_not(monkeypatch):
    run = scheduler()
    from api_backend import ApiRequestError
    import online_batch
    monkeypatch.setattr(online_batch, "wait_before_retry", lambda seconds, stop: True)
    rate = ApiRequestError("rate limited", status=429, code="rate_limit_exceeded", retry_after=0)
    engine = SimpleNamespace(generate_batch=MagicMock(side_effect=[rate, ["ok"]]))
    assert list(run(engine, [("one", None)], concurrency=1)) == [("one", "ok")]
    assert engine.generate_batch.call_count == 2
    for status, code in [(401, "invalid_api_key"), (429, "insufficient_quota")]:
        engine.generate_batch = MagicMock(side_effect=ApiRequestError("denied", status=status, code=code))
        result = list(run(engine, [("one", None)], concurrency=1))
        assert result[0][1].startswith("Error:")
        assert engine.generate_batch.call_count == 1


def test_retry_limit_and_stop_during_backoff(monkeypatch):
    run = scheduler()
    from api_backend import ApiRequestError
    import online_batch
    engine = SimpleNamespace(generate_batch=MagicMock(side_effect=ApiRequestError("busy", status=429)))
    monkeypatch.setattr(online_batch, "wait_before_retry", lambda seconds, stop: True)
    assert list(run(engine, [("one", None)], concurrency=1))[0][1].startswith("Error:")
    assert engine.generate_batch.call_count == 3
    engine.generate_batch.reset_mock()
    monkeypatch.setattr(online_batch, "wait_before_retry", lambda seconds, stop: False)
    assert list(run(engine, [("one", None)], concurrency=1))[0][1].startswith("Error:")
    assert engine.generate_batch.call_count == 1


@pytest.mark.parametrize("value", [0, 33])
def test_invalid_concurrency(value):
    run = scheduler()
    with pytest.raises(ValueError):
        list(run(SimpleNamespace(), [], concurrency=value))


def test_eight_real_http_requests_overlap_and_keep_file_mapping(tmp_path):
    import base64
    import io
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from PIL import Image
    from backend import QwenEngine
    from gui_workers import CaptionWorker
    lock = threading.Lock()
    ready = threading.Event()
    stats = {"active": 0, "peak": 0}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            data_url = payload['messages'][0]['content'][1]['image_url']['url']
            width = Image.open(io.BytesIO(base64.b64decode(data_url.split(',')[1]))).width
            with lock:
                stats['active'] += 1
                stats['peak'] = max(stats['peak'], stats['active'])
                if stats['active'] == 8:
                    ready.set()
            ready.wait(3)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'choices': [{'message': {'content': f'width:{width}'}}]}).encode())
            with lock:
                stats['active'] -= 1
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.request_queue_size = 16
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for i in range(8):
            Image.new('RGB', (20 + i, 20)).save(tmp_path / f'{i}.png')
        engine = QwenEngine()
        assert engine.load_api(f'http://127.0.0.1:{server.server_port}', '', 'vision-test')[0]
        worker = CaptionWorker(engine, str(tmp_path), {
            'api_concurrency': 8, 'skip_existing': False, 'prompt': 'describe',
            'trigger': '', 'frame_count': 1, 'max_tokens': 50, 'use_masks': False})
        worker.isInterruptionRequested = lambda: False
        worker.run()
        assert stats['peak'] == 8
        assert worker.saved_count == 8 and worker.failed_count == 0
        for i in range(8):
            assert (tmp_path / f'{i}.txt').read_text() == f'width:{20 + i}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_atomic_save_failure_keeps_old_caption(tmp_path, monkeypatch):
    from online_batch import save_online_caption
    import online_batch
    path = tmp_path / 'image.jpg'
    path.with_suffix('.txt').write_text('original')
    monkeypatch.setattr(online_batch.os, 'replace', MagicMock(side_effect=PermissionError('locked')))
    with pytest.raises(PermissionError):
        save_online_caption(str(path), 'new')
    assert path.with_suffix('.txt').read_text() == 'original'
    assert not list(tmp_path.glob('*.tmp'))


def test_skip_existing_reserves_duplicate_output_paths(tmp_path):
    from gui_workers import CaptionWorker
    first = tmp_path / 'same.jpg'
    second = tmp_path / 'same.png'
    engine = SimpleNamespace(is_api=True, api_client=SimpleNamespace(model='test'),
                             find_files=MagicMock(return_value=[(str(first), None), (str(second), None)]),
                             generate_batch=MagicMock(return_value=['caption']))
    worker = CaptionWorker(engine, str(tmp_path), {
        'api_concurrency': 8, 'skip_existing': True, 'prompt': 'describe',
        'trigger': '', 'frame_count': 1, 'max_tokens': 50})
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert worker.saved_count == 1 and worker.skipped_count == 1
    assert engine.generate_batch.call_count == 1


def test_skip_existing_keeps_caption_created_while_request_is_running(tmp_path):
    from gui_workers import CaptionWorker
    image = tmp_path / 'same.jpg'
    def generate(*args, **kwargs):
        image.with_suffix('.txt').write_text('created during request')
        return ['remote caption']
    engine = SimpleNamespace(is_api=True, api_client=SimpleNamespace(model='test'),
                             find_files=MagicMock(return_value=[(str(image), None)]), generate_batch=generate)
    worker = CaptionWorker(engine, str(tmp_path), {
        'api_concurrency': 2, 'skip_existing': True, 'prompt': 'describe',
        'trigger': '', 'frame_count': 1, 'max_tokens': 50})
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert image.with_suffix('.txt').read_text() == 'created during request'
    assert worker.skipped_count == 1 and worker.saved_count == 0
