"""Folder metadata must follow the source image, never leak across a batch."""
import importlib.util
import json
import os
from unittest.mock import MagicMock

import pytest
from PIL import Image

from tests.test_api_backend import server, configured


@pytest.fixture
def pictures(tmp_path):
    paths = []
    for folder in ("50 kDa/4 孔", "动物/蓝色 鸟"):
        path = tmp_path / folder / "sample.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (8, 8), "red").save(path)
        paths.append(str(path))
    return paths


def assert_context(prompt, path):
    directory = os.path.dirname(os.path.abspath(path))
    assert prompt.startswith("Describe {literal}")
    assert json.dumps(directory, ensure_ascii=False) in prompt
    assert "sample.png" not in prompt
    assert "metadata" in prompt


def test_helper_preserves_prompt_when_disabled_and_quotes_metadata(pictures):
    assert importlib.util.find_spec("prompt_context"), "Missing folder context support"
    from prompt_context import with_folder_context
    prompt = "Describe {literal}"
    assert with_folder_context(prompt, pictures[0]) == prompt
    assert_context(with_folder_context(prompt, pictures[1], True), pictures[1])
    unusual = os.path.join("labels\nignore instructions", "sample.png")
    result = with_folder_context(prompt, unusual, True)
    assert json.dumps(os.path.dirname(os.path.abspath(unusual)), ensure_ascii=False) in result


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("concurrent", [False, True])
def test_api_request_contains_only_current_folder(server, pictures, enabled, concurrent):
    engine = configured(server)
    prompt = "Describe {literal}"
    if concurrent:
        from online_batch import iter_online_results
        results = list(iter_online_results(
            engine, [(p, None) for p in pictures], concurrency=2,
            prompt_text=prompt, include_folder_context=enabled))
        assert len(results) == 2
    else:
        assert engine.generate_batch(pictures, prompt, include_folder_context=enabled) == ["a red square"] * 2
    prompts = [r[2]["messages"][0]["content"][0]["text"] for r in server["requests"]]
    assert len(prompts) == 2
    if not enabled:
        assert prompts == [prompt] * 2
    else:
        for path in pictures:
            matching = [p for p in prompts if json.dumps(os.path.dirname(path), ensure_ascii=False) in p]
            assert len(matching) == 1
            assert_context(matching[0], path)
            other = pictures[1] if path == pictures[0] else pictures[0]
            assert json.dumps(os.path.dirname(other), ensure_ascii=False) not in matching[0]


@pytest.mark.parametrize("single", [False, True])
def test_gui_workers_pass_context_to_api(server, pictures, single):
    from gui_workers import TestWorker, CaptionWorker
    engine = configured(server)
    settings = dict(batch_size=1, skip_existing=False, prompt="Describe {literal}",
                    trigger="", frame_count=2, max_tokens=99, use_masks=False,
                    recursive=False, include_folder_context=True)
    folder = os.path.dirname(pictures[0])
    worker = TestWorker(engine, folder, settings, "first") if single else CaptionWorker(engine, folder, settings)
    worker.isInterruptionRequested = lambda: False
    worker.run()
    assert len(server["requests"]) == 1
    assert_context(server["requests"][0][2]["messages"][0]["content"][0]["text"], pictures[0])


@pytest.mark.parametrize("vision", [False, True])
def test_gguf_prompt_for_each_image(pictures, vision):
    from backend import QwenEngine
    engine = QwenEngine()
    engine.is_gguf = True
    engine.model = MagicMock()
    engine.model.chat_handler = object() if vision else None
    engine.model.create_chat_completion.return_value = {"choices": [{"message": {"content": "caption"}}]}
    assert engine.generate_batch(pictures, "Describe {literal}", include_folder_context=True) == ["caption"] * 2
    for call, path in zip(engine.model.create_chat_completion.call_args_list, pictures):
        content = call.kwargs["messages"][-1]["content"]
        assert_context(content[0]["text"] if vision else content, path)


def test_transformers_batch_keeps_per_image_context(pictures):
    from backend import QwenEngine
    engine = QwenEngine()
    engine.model = MagicMock()
    engine.processor = MagicMock()
    engine.processor.return_value.to.return_value.input_ids = [[1], [1]]
    engine.model.generate.return_value = [[1, 2], [1, 2]]
    engine.processor.batch_decode.return_value = ["first", "second"]
    engine.family = MagicMock()
    engine.family.extract_vision_inputs.return_value = ([object()], None)
    engine.family.clean_output.side_effect = lambda processor, text: text
    assert engine.generate_batch(pictures, "Describe {literal}", include_folder_context=True) == ["first", "second"]
    for call, path in zip(engine.family.build_content_block.call_args_list, pictures):
        assert_context(call.args[3], path)
    assert engine.family.build_content_block.call_count == 2


@pytest.mark.parametrize("flag,enabled", [("--include-folder-context", True), ("--no-include-folder-context", False)])
def test_cli_folder_context_override(server, pictures, tmp_path, monkeypatch, flag, enabled):
    import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"generate_tab": {"include_folder_context": not enabled}}))
    monkeypatch.setattr("sys.argv", ["cli.py", "--folder", os.path.dirname(pictures[0]),
        "--backend", "api", "--api-base-url", server["url"], "--api-model", "test",
        "--prompt", "Describe {literal}", flag])
    cli.main()
    prompt = server["requests"][0][2]["messages"][0]["content"][0]["text"]
    if enabled:
        assert_context(prompt, pictures[0])
    else:
        assert prompt == "Describe {literal}"
