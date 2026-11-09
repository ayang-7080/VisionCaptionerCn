"""Online captions tested against a real, local HTTP server (no credentials)."""
import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
from PIL import Image

from backend import QwenEngine


@pytest.fixture
def server():
    state = {"requests": [], "status": 200,
             "response": {"choices": [{"message": {"content": " a red square "}}]}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, dict(self.headers), body))
            status, response = state["responses"].pop(0) if state.get("responses") else (state["status"], state["response"])
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if "location" in state:
                self.send_header("Location", state["location"])
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "sample.png"
    Image.new("RGBA", (400, 200), (255, 0, 0, 128)).save(path)
    return str(path)


def configured(server, **kwargs):
    engine = QwenEngine()
    assert hasattr(engine, "load_api"), "QwenEngine needs an online API mode"
    ok, message = engine.load_api(server["url"], "test-secret", "vision-test", **kwargs)
    assert ok, message
    return engine


def test_image_request_and_caption(server, picture):
    engine = configured(server, max_resolution=128)
    assert engine.generate_batch([picture], "Describe only colors", "token", max_tokens=99) == ["token, a red square"]
    path, headers, body = server["requests"][0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-secret"
    assert body["model"] == "vision-test"
    assert body["max_tokens"] == 99
    content = body["messages"][-1]["content"]
    assert content[0]["text"] == "Describe only colors"
    data = content[1]["image_url"]["url"]
    assert data.startswith("data:image/jpeg;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(data.split(",")[1])))
    assert image.size == (128, 64)


@pytest.mark.parametrize("suffix,expected", [
    ("/v1/", "/v1/chat/completions"),
    ("/custom/v1", "/custom/v1/chat/completions"),
    ("/custom/chat/completions/", "/custom/chat/completions"),
])
def test_custom_base_paths(server, picture, suffix, expected):
    engine = configured(server)
    assert engine.load_api(server["url"] + suffix, "", "vision-test")[0]
    engine.generate_batch([picture])
    assert server["requests"][0][0] == expected


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_http_failure_never_becomes_caption_or_leaks_key(server, picture, status):
    engine = configured(server)
    server["status"] = status
    server["response"] = {"error": {"message": "test-secret"}}
    result = engine.generate_batch([picture])[0]
    assert result.startswith("Error:")
    assert str(status) in result
    assert "test-secret" not in result


@pytest.mark.parametrize("response", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                                        {"choices": [{"message": {"content": " "}}]}])
def test_invalid_response_is_error(server, picture, response):
    engine = configured(server)
    server["response"] = response
    assert engine.generate_batch([picture])[0].startswith("Error:")


def test_video_frames_and_mask_use_existing_helpers(server, picture):
    engine = configured(server)
    engine.extract_video_frames = MagicMock(return_value=[Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8))])
    assert engine.generate_batch(["clip.mp4"], frame_count=2) == ["a red square"]
    assert len(server["requests"][0][2]["messages"][-1]["content"]) == 3
    engine.apply_mask = MagicMock(return_value=Image.new("RGB", (8, 8)))
    engine.generate_batch([picture], mask_paths=[picture])
    engine.apply_mask.assert_called_once_with(picture, picture)
    assert "foreground" in server["requests"][1][2]["messages"][-1]["content"][0]["text"]


def test_stop_prevents_requests(server, picture):
    engine = configured(server)
    assert engine.generate_batch([picture], stop_event=lambda: True) == []
    assert server["requests"] == []


def test_api_unload_and_local_state(server):
    engine = configured(server)
    assert engine.is_api and engine.model is None
    engine.unload_model()
    assert not engine.is_api
    assert engine.generate_batch(["missing.png"])[0] == "Error: Model not loaded"


def test_invalid_config_preserves_loaded_backend(server):
    engine = configured(server)
    for address, model in [("file:///tmp", "m"), ("https://", "m"), (server["url"], "")]:
        ok, _ = engine.load_api(address, "test-secret", model)
        assert not ok
        assert engine.is_api


def test_worker_does_not_overwrite_caption_on_api_failure(server, picture, tmp_path):
    from gui_workers import CaptionWorker
    engine = configured(server)
    server["status"] = 401
    caption = tmp_path / "sample.txt"
    caption.write_text("existing caption")
    worker = CaptionWorker(engine, str(tmp_path), {
        "batch_size": 1, "skip_existing": False, "prompt": "describe", "trigger": "",
        "frame_count": 2, "max_tokens": 99, "use_masks": False})
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert len(server["requests"]) == 1
    assert caption.read_text() == "existing caption"


def test_cli_api_without_local_model(server, picture, tmp_path, monkeypatch):
    import cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setattr("sys.argv", ["cli.py", "--folder", str(tmp_path), "--backend", "api",
                                   "--api-base-url", server["url"], "--api-model", "vision-test"])
    cli.main()
    assert (tmp_path / "sample.txt").read_text() == "a red square"
    assert server["requests"][0][1]["Authorization"] == "Bearer test-secret"


def test_cli_loads_gui_api_settings(tmp_path, monkeypatch):
    import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"generate_tab": {
        "backend": "api", "api_base_url": "https://example.invalid/v1", "api_model": "vision",
        "api_key": "saved-key", "remember_api_key": True, "api_timeout": 180}}))
    settings = cli.load_defaults_from_settings()
    assert settings["backend"] == "api"
    assert settings["api_model"] == "vision"
    assert settings["api_key"] == "saved-key"
    assert settings["api_timeout"] == 180


def test_timeout_is_reported_without_saving(server, picture, monkeypatch):
    import api_backend
    engine = configured(server, timeout=1)
    opener = MagicMock()
    opener.open.side_effect = TimeoutError()
    monkeypatch.setattr(api_backend, "build_opener", lambda *args: opener)
    assert "timed out" in engine.generate_batch([picture])[0]
    assert opener.open.call_args.kwargs["timeout"] == 1


def test_redirect_does_not_forward_credentials(server, picture):
    engine = configured(server)
    server["status"] = 302
    server["location"] = server["url"] + "/redirected"
    assert "HTTP 302" in engine.generate_batch([picture])[0]
    assert len(server["requests"]) == 1


def test_missing_file_does_not_block_next_image(server, picture):
    engine = configured(server)
    results = engine.generate_batch(["missing.png", picture])
    assert results[0].startswith("Error:")
    assert results[1] == "a red square"
    assert len(server["requests"]) == 1


def test_stop_after_response_prevents_next_request(server, picture):
    engine = configured(server)
    assert engine.generate_batch([picture, picture], stop_event=lambda: bool(server["requests"])) == []
    assert len(server["requests"]) == 1


def test_text_chat_default_prompt_without_images(server):
    client = configured(server).api_client
    assert hasattr(client, "test_chat"), "Missing text-only API test"
    assert client.test_chat() == "a red square"
    body = server["requests"][0][2]
    assert body["messages"] == [{"role": "user", "content": "你好，你是什么模型"}]
    assert body["model"] == "vision-test"


def test_text_chat_custom_prompt_and_empty_validation(server):
    client = configured(server).api_client
    assert hasattr(client, "test_chat")
    client.test_chat("Say hello", max_tokens=64)
    assert server["requests"][0][2]["messages"][0]["content"] == "Say hello"
    assert server["requests"][0][2]["max_tokens"] == 64
    with pytest.raises(ValueError):
        client.test_chat("  ")
    assert len(server["requests"]) == 1


def test_text_chat_auth_error(server):
    client = configured(server).api_client
    assert hasattr(client, "test_chat")
    server["status"] = 401
    with pytest.raises(ValueError, match="HTTP 401"):
        client.test_chat()


def test_official_endpoint_uses_completion_token_limit(monkeypatch):
    import api_backend
    client = api_backend.ApiCaptionClient("https://api.openai.com/v1", "test-secret", "gpt-6.1-sol")
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = io.BytesIO(
        json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode())
    monkeypatch.setattr(api_backend, "build_opener", lambda *args: opener)
    assert client.test_chat(max_tokens=384) == "hello"
    body = json.loads(opener.open.call_args.args[0].data)
    assert body.get("max_completion_tokens") == 384
    assert "max_tokens" not in body


def test_explicit_unsupported_token_parameter_retries_once_and_remembers(server):
    client = configured(server).api_client
    server["responses"] = [(400, {"error": {
        "message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.",
        "type": "invalid_request_error", "param": "max_tokens", "code": "unsupported_parameter"}})]
    assert client.test_chat(max_tokens=512) == "a red square"
    assert server["requests"][0][2]["max_tokens"] == 512
    assert server["requests"][1][2]["max_completion_tokens"] == 512
    assert "max_tokens" not in server["requests"][1][2]
    client.test_chat(max_tokens=128)
    assert server["requests"][2][2]["max_completion_tokens"] == 128


def test_error_details_are_visible_but_keys_are_redacted(server):
    client = configured(server).api_client
    server["status"] = 400
    server["response"] = {"error": {"message": "Model unavailable for test-secret; Bearer another-secret",
                                    "code": "model_not_found", "param": "model"}}
    with pytest.raises(ValueError) as caught:
        client.test_chat()
    error = str(caught.value)
    assert "Model unavailable" in error
    assert "model_not_found" in error
    assert "test-secret" not in error and "another-secret" not in error
    assert len(server["requests"]) == 1


def test_token_parameter_retries_are_bounded(server):
    client = configured(server).api_client
    server["responses"] = [
        (400, {"error": {"message": "Unsupported parameter: 'max_tokens'", "param": "max_tokens", "code": "unsupported_parameter"}}),
        (400, {"error": {"message": "Unsupported parameter: 'max_completion_tokens'", "param": "max_completion_tokens", "code": "unsupported_parameter"}}),
    ]
    with pytest.raises(ValueError, match="HTTP 400"):
        client.test_chat()
    assert len(server["requests"]) == 2


def test_empty_reply_due_to_reasoning_limit_explains_budget(server):
    client = configured(server).api_client
    server["response"] = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    with pytest.raises(ValueError, match="[Tt]oken.*reasoning"):
        client.test_chat()
    assert len(server["requests"]) == 1


@pytest.mark.parametrize("error", [
    {"param": "max_tokens", "code": "invalid_value", "message": "max_tokens must be positive"},
    {"param": "model", "code": "unsupported_parameter", "message": "Unsupported parameter: model"},
    {"message": "Unable to process the request"},
])
def test_unrelated_400_errors_never_replay_requests(server, error):
    client = configured(server).api_client
    server["status"] = 400
    server["response"] = {"error": error}
    with pytest.raises(ValueError, match="HTTP 400"):
        client.test_chat()
    assert len(server["requests"]) == 1


def test_legacy_fallback_after_modern_parameter_is_rejected(server):
    client = configured(server).api_client
    server["responses"] = [(400, {"error": {"code": "unsupported_parameter", "param": "max_tokens"}})]
    client.test_chat()
    server["responses"] = [(400, {"error": {"code": "unsupported_parameter", "param": "max_completion_tokens"}})]
    client.test_chat(max_tokens=99)
    assert server["requests"][2][2]["max_completion_tokens"] == 99
    assert server["requests"][3][2]["max_tokens"] == 99
    assert "max_completion_tokens" not in server["requests"][3][2]


def test_error_redaction_precedes_truncation(server):
    from api_backend import ApiCaptionClient
    key = "sk-" + "x" * 1000
    client = ApiCaptionClient(server["url"], key, "vision-test")
    server["status"] = 401
    server["response"] = {"error": {"message": "Invalid credential " + key}}
    with pytest.raises(ValueError) as caught:
        client.test_chat()
    assert "xxxx" not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_image_test_reports_subfolder_hint(server, tmp_path):
    from gui_workers import TestWorker
    engine = configured(server)
    nested = tmp_path / "1 lane"
    nested.mkdir()
    Image.new("RGB", (12, 12)).save(nested / "image.jpg")
    worker = TestWorker(engine, str(tmp_path), {"recursive": False})
    worker.error = MagicMock()
    worker.run()
    assert "subfolder" in worker.error.emit.call_args.args[0].lower()
    assert server["requests"] == []


def test_empty_batch_reports_error(server, tmp_path):
    from gui_workers import CaptionWorker
    worker = CaptionWorker(configured(server), str(tmp_path), {"recursive": False})
    worker.signals = MagicMock()
    worker.run()
    worker.signals.error.emit.assert_called_once()
    worker.signals.finished.emit.assert_called_once()


@pytest.mark.parametrize("mode", ["first", "random"])
def test_recursive_image_test_uses_online_model(server, tmp_path, mode):
    from gui_workers import TestWorker
    nested = tmp_path / "1 lane"
    nested.mkdir()
    path = nested / "image.jpg"
    Image.new("RGB", (12, 12)).save(path)
    worker = TestWorker(configured(server), str(tmp_path), {
        "recursive": True, "prompt": "describe", "trigger": "",
        "frame_count": 2, "max_tokens": 99, "use_masks": False}, mode=mode)
    worker.isInterruptionRequested = lambda: False
    worker.result_ready = MagicMock()
    worker.run()
    assert worker.result_ready.emit.call_args.args[0] == str(path)
    assert worker.result_ready.emit.call_args.args[1] == "a red square"
    assert server["requests"][0][2]["model"] == "vision-test"
    assert server["requests"][0][2]["messages"][0]["content"][1]["type"] == "image_url"
    assert not path.with_suffix(".txt").exists()


def test_recursive_online_batch_writes_each_caption(server, tmp_path):
    from gui_workers import CaptionWorker
    for name in ("1 lane", "2 lanes"):
        folder = tmp_path / name
        folder.mkdir()
        Image.new("RGB", (12, 12)).save(folder / "image.jpg")
    worker = CaptionWorker(configured(server), str(tmp_path), {
        "recursive": True, "batch_size": 1, "skip_existing": False,
        "prompt": "describe", "trigger": "", "frame_count": 2,
        "max_tokens": 99, "use_masks": False})
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert len(server["requests"]) == 2
    assert all(request[2]["model"] == "vision-test" for request in server["requests"])
    captions = list(tmp_path.rglob("*.txt"))
    assert len(captions) == 2
    assert all(path.read_text() == "a red square" for path in captions)


def test_partial_caption_at_token_limit_is_not_saved(server, picture, tmp_path):
    from gui_workers import CaptionWorker
    server["response"] = {"choices": [{"finish_reason": "length", "message": {"content": '{"incomplete":'}}]}
    worker = CaptionWorker(configured(server), str(tmp_path), {
        "batch_size": 1, "skip_existing": False, "prompt": "json", "trigger": "",
        "frame_count": 2, "max_tokens": 99, "use_masks": False})
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert not (tmp_path / "sample.txt").exists()


def test_generation_exception_is_counted_as_batch_failure(server, picture, tmp_path):
    from gui_workers import CaptionWorker
    engine = configured(server)
    engine.generate_batch = MagicMock(side_effect=RuntimeError("generation failed"))
    worker = CaptionWorker(engine, str(tmp_path), {
        "batch_size": 1, "skip_existing": False, "prompt": "json", "trigger": "",
        "frame_count": 2, "max_tokens": 99, "use_masks": False})
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert worker.failed_count == 1
    assert worker.saved_count == 0


@pytest.mark.parametrize("kind,expected", [
    ("dns", "DNS lookup failed"),
    ("tls_timeout", "TLS handshake timed out"),
    ("certificate", "TLS certificate verification failed"),
    ("refused", "connection refused"),
])
def test_transport_errors_expose_sanitized_root_cause(server, monkeypatch, kind, expected):
    import socket
    import ssl
    from urllib.error import URLError
    import api_backend
    reasons = {
        "dns": socket.gaierror(11001, "getaddrinfo failed"),
        "tls_timeout": TimeoutError("The handshake operation timed out"),
        "certificate": ssl.SSLCertVerificationError(1, "certificate verify failed"),
        "refused": ConnectionRefusedError(10061, "connection refused via http://user:proxy-pass@127.0.0.1:7897; test-secret"),
    }
    client = configured(server).api_client
    opener = MagicMock()
    opener.open.side_effect = URLError(reasons[kind])
    monkeypatch.setattr(api_backend, "build_opener", lambda *args: opener)
    with pytest.raises(ValueError) as caught:
        client.test_chat()
    assert expected in str(caught.value)
    assert "test-secret" not in str(caught.value)
    assert "proxy-pass" not in str(caught.value)
    assert type(reasons[kind]).__name__ in str(caught.value)
    assert opener.open.call_count == 1
