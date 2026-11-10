"""Check shell launch behavior without installing packages or opening a GUI."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell_tree(tmp_path):
    if os.name == "nt":
        git = shutil.which("git")
        bash = Path(git).parent.parent / "bin" / "bash.exe" if git else None
    else:
        bash = shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("Bash unavailable")
    for name in ("run.sh", "run_cn.sh"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    return str(bash), tmp_path


def script(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + contents, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def test_missing_venv_stops_english_launcher(shell_tree):
    bash, root = shell_tree
    result = subprocess.run([bash, "run.sh"], cwd=root, capture_output=True, timeout=15)
    assert result.returncode == 1
    assert b"Virtual environment not found" in result.stdout


def test_failed_install_stops_chinese_launcher(shell_tree):
    bash, root = shell_tree
    script(root / "bin" / "uv", 'echo "$*" >> calls.txt\nexit 23\n')
    result = subprocess.run(
        [bash, "-c", 'export PATH="$PWD/bin:$PATH"; bash run_cn.sh'],
        cwd=root, capture_output=True, timeout=15,
    )
    assert result.returncode == 23, result.stderr
    assert (root / "calls.txt").read_text().splitlines() == ["venv venv --seed --python 3.12"]


@pytest.mark.parametrize("name,entry", [("run.sh", "main.py"), ("run_cn.sh", "launcher.py")])
def test_launcher_uses_project_python_and_forwards_arguments(shell_tree, name, entry):
    bash, root = shell_tree
    script(root / "bin" / "uv", "exit 0\n")
    script(root / "venv" / "bin" / "python", 'printf "%s\\n" "$@" > arguments.txt\nexit 17\n')
    result = subprocess.run(
        [bash, "-c", 'export PATH="$PWD/bin:$PATH"; bash "$1" "two words"', "test", name],
        cwd=root, capture_output=True, timeout=15,
    )
    assert result.returncode == 17, result.stderr
    assert (root / "arguments.txt").read_text().splitlines() == [entry, "two words"]
