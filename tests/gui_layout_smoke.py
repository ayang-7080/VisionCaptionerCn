"""Real complete UI layout; no network, model loading or user settings writes."""
import json
import os
import sys
import tempfile
from contextlib import chdir
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6 import QtWidgets, QtCore, QtGui
sys.modules["huggingface_hub"] = MagicMock()
import tests.conftest
sys.modules["torch"].__version__ = "test"
import main
from backend import QwenEngine
from gui_widgets import DropLineEdit
from tab_captions import CaptionsTab
from tab_mask import MaskTab
from tab_video import VideoTab
from tab_review import ReviewTab
from tab_qa import QATab

main.DropLineEdit = DropLineEdit
main.CaptionsTab = CaptionsTab
main.MaskTab = MaskTab
main.VideoTab = VideoTab
main.ReviewTab = ReviewTab
main.QATab = QATab

class LayoutWindow(main.MainWindow):
    def __init__(self):
        QtWidgets.QMainWindow.__init__(self)
        self.engine = QwenEngine()
        self.sam_engine = MagicMock(model=None)
        self.setup_ui()

    def showEvent(self, event):
        QtWidgets.QMainWindow.showEvent(self, event)

    def closeEvent(self, event):
        event.accept()

app = QtWidgets.QApplication([])
main.apply_dark_theme(app)
for name in ("segoeui.ttf", "msyh.ttc"):
    path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
    if path.exists():
        QtGui.QFontDatabase.addApplicationFont(str(path))

with tempfile.TemporaryDirectory() as directory, chdir(directory):
    window = LayoutWindow()
    window.resize(1100, 720)
    window.show()
    app.processEvents()
    print("Actual window size:", window.width(), window.height(), "Minimum:", window.minimumSizeHint().width(), window.minimumSizeHint().height())
    assert window.main_splitter.orientation() == QtCore.Qt.Horizontal
    assert window.width() == 1100 and window.height() == 720
    assert window.txt_log.height() > 450
    assert window.txt_log.mapTo(window, QtCore.QPoint(0, 0)).x() > window.tabs.mapTo(window, QtCore.QPoint(0, 0)).x() + window.tabs.width()
    window.main_splitter.setSizes([750, 300])
    app.processEvents()
    assert window.txt_log.width() >= 230
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        assert window.size() == QtCore.QSize(1100, 720)
    window.tabs.setCurrentWidget(window.tab_gen)
    window.tab_gen.combo_backend.setCurrentIndex(1)
    window.tab_gen.on_batch_total(5271)
    window.tab_gen.on_progress_update(32, 6.31)
    window.log("Online API model: gpt-6.1-sol\nSaved 32 / 5271 images\nProcessing image 33...")
    window.save_settings()
    saved = json.loads(Path("settings.json").read_text())
    assert saved["main_layout_version"] == 2
    window.main_splitter.setSizes([600, 450])
    window.load_settings()
    app.processEvents()
    assert window.main_splitter.orientation() == QtCore.Qt.Horizontal
    # Old vertical splitter state must not restore a bottom log panel.
    legacy = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    legacy.addWidget(QtWidgets.QWidget())
    legacy.addWidget(QtWidgets.QWidget())
    saved.pop("main_layout_version")
    saved["main_splitter_state"] = legacy.saveState().toHex().data().decode()
    Path("settings.json").write_text(json.dumps(saved))
    window.load_settings()
    assert window.main_splitter.orientation() == QtCore.Qt.Horizontal
    screenshot = os.environ.get("LAYOUT_SCREENSHOT")
    if screenshot:
        window.resize(1400, 850)
        app.processEvents()
        window.grab().save(screenshot)
    window.close()
print("Right log panel, resizing, all tabs and layout migration passed.")
