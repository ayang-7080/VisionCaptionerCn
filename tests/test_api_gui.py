"""Exercise real Qt widgets in a subprocess, isolated from suite-wide Qt mocks."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize("language", ["en", "zh"])
def test_online_mode_gui(language):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", API_GUI_LANGUAGE=language)
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("gui_api_smoke.py"))],
                            capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_main_window_layout():
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("gui_layout_smoke.py"))],
                            capture_output=True, text=True,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
