"""Real Qt UI checks; ML dependencies are mocked, no weights or API calls."""
import os
import sys
import tempfile
from contextlib import chdir
from pathlib import Path
from unittest.mock import MagicMock, patch
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6 import QtWidgets, QtCore, QtGui
sys.modules["huggingface_hub"] = MagicMock()
import tests.conftest  # Preserve already-imported Qt while stubbing ML dependencies.
from backend import QwenEngine
from tab_captions import CaptionsTab

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
if os.environ.get("API_GUI_LANGUAGE") == "zh":
    import launcher
    launcher.install_patches()
from main import apply_dark_theme
apply_dark_theme(app)
# Windows' offscreen Qt plugin may not enumerate system fonts automatically.
for filename in ("segoeui.ttf", "msyh.ttc"):
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
    if font_path.exists():
        QtGui.QFontDatabase.addApplicationFont(str(font_path))
with tempfile.TemporaryDirectory() as directory, chdir(directory):
    tab = CaptionsTab(QwenEngine())
    assert hasattr(tab, "combo_backend"), "Missing online/local mode selector"
    assert tab.combo_backend.currentData() == "local"
    tab.spin_batch.setValue(8)
    if os.environ.get("API_GUI_LANGUAGE") == "zh":
        assert tab.combo_backend.currentText() == "本地模型"
    assert not tab.api_group.isVisible()
    tab.combo_backend.setCurrentIndex(1)
    assert tab.spin_batch.value() == 8
    assert tab.spin_batch.isEnabled()
    tab.spin_batch.setValue(3)
    assert tab.get_settings()["api_concurrency"] == 3
    assert tab.get_settings()["batch_size"] == 8, "Keep the local model batch preference"
    tab.txt_api_url.setText("https://example.invalid/custom/v1")
    tab.txt_api_model.setText("vision-test")
    tab.txt_api_key.setText("dummy-secret")
    screenshot = os.environ.get("API_GUI_SCREENSHOT")
    if screenshot:
        tab.resize(1200, 850)
        tab.show()
        app.processEvents()
        assert tab.grab().save(screenshot)
    assert tab.txt_api_key.echoMode() == QtWidgets.QLineEdit.Password
    assert not tab.combo_quant.isEnabled()
    tab.load_model()
    assert tab.engine.is_api
    assert tab.btn_start.isEnabled()
    assert not tab.txt_api_url.isEnabled()
    assert not tab.btn_load.isEnabled()
    settings = tab.get_settings()
    assert settings["backend"] == "api"
    assert settings["api_key"] == ""
    tab.toggle_ui(False)
    assert not tab.combo_backend.isEnabled()
    assert not tab.spin_batch.isEnabled()
    tab.toggle_ui(True)
    tab.unload_model()
    assert tab.txt_api_url.isEnabled()
    tab.chk_remember_api_key.setChecked(True)
    settings = tab.get_settings()
    assert settings["api_key"] == "dummy-secret"
    restored = CaptionsTab(QwenEngine())
    restored.set_settings(settings)
    assert restored.combo_backend.currentData() == "api"
    assert restored.txt_api_url.text() == "https://example.invalid/custom/v1"
    assert restored.txt_api_model.text() == "vision-test"
    assert restored.txt_api_key.text() == "dummy-secret"
    assert restored.spin_batch.value() == 3
    restored.load_model()
    restored.combo_backend.setCurrentIndex(0)
    assert not restored.engine.is_api
    assert restored.btn_load.isEnabled()
    assert not restored.btn_start.isEnabled()
    assert restored.combo_quant.isEnabled()
    assert restored.spin_batch.value() == 8
    restored.combo_backend.setCurrentIndex(1)
    assert restored.spin_batch.value() == 3
    restored.combo_backend.setCurrentIndex(0)
    restored.set_settings({"batch_size": 3})
    assert restored.combo_backend.currentData() == "local"

    assert hasattr(tab, "btn_api_chat"), "Missing API conversation test button"
    assert tab.txt_api_test_prompt.text() == "你好，你是什么模型"
    assert tab.current_folder == ""
    assert not tab.engine.is_api
    from api_backend import ApiCaptionClient

    def wait_for_chat():
        deadline = time.monotonic() + 5
        while not tab.btn_api_chat.isEnabled() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert tab.btn_api_chat.isEnabled()
        assert tab.api_test_worker.wait(1000)

    gate = threading.Event()
    def reply(client, prompt, max_tokens=256):
        assert prompt == "你好，你是什么模型"
        assert client.model == "vision-test"
        gate.wait(3)
        return "<b>Hello from the API</b>"

    with patch.object(ApiCaptionClient, "test_chat", reply):
        tab.btn_api_chat.click()
        assert not tab.btn_api_chat.isEnabled()
        assert not tab.combo_backend.isEnabled()
        assert not tab.btn_load.isEnabled()
        QtCore.QTimer.singleShot(0, gate.set)
        wait_for_chat()
    assert tab.txt_preview.toPlainText() == "<b>Hello from the API</b>"
    assert not tab.engine.is_api
    assert not tab.btn_start.isEnabled()
    assert tab.btn_load.isEnabled()
    assert not list(Path(directory).glob("*.txt"))

    tab.load_model()
    client = tab.engine.api_client
    assert tab.btn_api_chat.isEnabled()
    with patch.object(ApiCaptionClient, "test_chat", side_effect=ValueError("HTTP 401 dummy-secret")):
        tab.btn_api_chat.click()
        wait_for_chat()
    assert "HTTP 401" in tab.txt_preview.toPlainText()
    assert "dummy-secret" not in tab.txt_preview.toPlainText()
    assert tab.engine.api_client is client
    assert tab.btn_start.isEnabled()
    assert not tab.txt_api_url.isEnabled()
    tab.txt_api_test_prompt.setText("Custom test")
    assert tab.get_settings()["api_test_prompt"] == "Custom test"

    from PIL import Image
    nested = Path(directory) / "nested"
    nested.mkdir()
    Image.new("RGB", (12, 12)).save(nested / "image.jpg")
    Image.new("RGB", (12, 12)).save(nested / "second.jpg")
    tab.update_folder(directory, recursive=False)

    def wait_for_media(worker):
        deadline = time.monotonic() + 5
        while not tab.btn_start.isEnabled() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert tab.btn_start.isEnabled()
        assert worker.wait(1000)

    tab.btn_test_rand.click()
    wait_for_media(tab.test_worker)
    assert "subfolder" in tab.txt_preview.toPlainText().lower()
    assert "Testing" not in tab.lbl_status.text()
    tab.set_recursive(True)
    with patch.object(ApiCaptionClient, "_request_caption", return_value='{"caption": "online"}') as request:
        for button in (tab.btn_test_first, tab.btn_test_rand):
            button.click()
            wait_for_media(tab.test_worker)
            assert tab.txt_preview.toPlainText() == '{"caption": "online"}'
        tab.btn_start.click()
        wait_for_media(tab.worker)
    assert request.call_count == 4
    assert len(list(nested.glob("*.txt"))) == 2
    assert tab.engine.api_client is client
    tab.on_batch_total(5271)
    tab.on_progress_update(1, 6.0)
    assert "0.02%" in tab.progress.format()
    assert "1 / 5271" in tab.progress.format()
    tab.on_progress_update(32, 6.0)
    assert "0.61%" in tab.progress.format()
    tab.on_progress_update(5271, 6.0)
    assert "100.00%" in tab.progress.format()
    tab.close()
    restored.close()
print("Real Qt mode switching, state locking, settings and key persistence passed.")
