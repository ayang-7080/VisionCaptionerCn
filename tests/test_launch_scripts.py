"""Exercise Windows launchers with real, isolated Python environments."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launchers")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launch_tree(tmp_path):
    root = tmp_path / "中文 project & launcher"
    root.mkdir()
    for name in ("run.bat", "run_cn.bat", "run_portable.bat", "launch_windows.bat"):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, root / name)
    # A small real venv stands in for the portable distribution, without ML packages.
    runtime = root / "runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    for name in ("python.exe", "pythonw.exe"):
        shutil.copyfile(runtime / "Scripts" / name, runtime / name)
    packages = root / "venv" / "Lib" / "site-packages"
    (packages / "PySide6").mkdir(parents=True)
    (packages / "PySide6" / "__init__.py").write_text("")
    (packages / "PySide6" / "QtWidgets.py").write_text("")
    for module in ("main", "launcher", "backend", "tab_captions", "tab_review", "tab_mask", "tab_video", "tab_qa"):
        (root / f"{module}.py").write_text("")
    return root


def run_check(root, script, option="--check"):
    env = os.environ.copy()
    # These must not override the environment selected by the launcher.
    env["PYTHONHOME"] = str(root / "nonexistent-python")
    env["PYTHONPATH"] = str(root / "unrelated-packages")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        ["cmd.exe", "/d", "/c", script] + ([option] if option else []), cwd=root, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
    )


@pytest.mark.parametrize("script", ["run.bat", "run_cn.bat", "run_portable.bat"])
def test_missing_venv_uses_portable_runtime(launch_tree, script):
    result = run_check(launch_tree, script)
    assert result.returncode == 0, result.stdout
    assert b"Environment check passed." in result.stdout
    assert b"runtime" in result.stdout


def test_broken_venv_falls_back_to_runtime(launch_tree):
    venv.EnvBuilder(with_pip=False).create(launch_tree / "venv")
    (launch_tree / "venv" / "pyvenv.cfg").write_text("home = C:\\missing-captioner-python\n")
    result = run_check(launch_tree, "run_cn.bat")
    assert result.returncode == 0, result.stdout
    assert b"Environment check passed." in result.stdout
    assert b"runtime" in result.stdout


def test_valid_venv_is_preferred(launch_tree):
    venv.EnvBuilder(with_pip=False).create(launch_tree / "venv")
    result = run_check(launch_tree, "run_cn.bat")
    assert result.returncode == 0, result.stdout
    assert b"venv\\Scripts\\python.exe" in result.stdout
    assert b"Environment check passed." in result.stdout


def test_missing_runtime_fails_without_using_system_python(launch_tree):
    (launch_tree / "runtime" / "python.exe").unlink()
    result = run_check(launch_tree, "run_portable.bat")
    assert result.returncode != 0
    assert b"Environment check passed." not in result.stdout


def test_import_failure_is_reported(launch_tree):
    (launch_tree / "backend.py").write_text("raise ImportError('missing-test-dependency')\n")
    result = run_check(launch_tree, "run_portable.bat")
    assert result.returncode != 0
    assert b"missing-test-dependency" in result.stdout


def test_selftest_runs_in_console_and_returns_exit_code(launch_tree):
    (launch_tree / "launcher.py").write_text(
        "import sys\nassert sys.argv[1:] == ['--selftest']\nprint('SELFTEST_RAN')\nsys.exit(9)\n"
    )
    result = run_check(launch_tree, "run_cn.bat", "--selftest")
    assert result.returncode == 9, result.stdout
    assert b"SELFTEST_RAN" in result.stdout


@pytest.mark.parametrize("script,entry", [("run.bat", "main.py"), ("run_cn.bat", "launcher.py"),
                                         ("run_portable.bat", "launcher.py")])
def test_default_launch_starts_correct_entry_with_pythonw(launch_tree, script, entry):
    (launch_tree / entry).write_text(
        "from pathlib import Path\nimport sys\n"
        "Path('started.txt').write_text(Path(sys.executable).name)\n"
    )
    result = run_check(launch_tree, script, "")
    assert result.returncode == 0, result.stdout
    marker = launch_tree / "started.txt"
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.read_text() == "pythonw.exe"
