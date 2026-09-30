# -*- coding: utf-8 -*-
"""
VisionCaptioner 外挂中文汉化启动器（不修改项目任何源码）

原理:
  在导入 main.py 之前, 对 PySide6 的"文本设置入口"做 monkey patch。
  所有界面文本先查 i18n/zh_CN.json 词典, 命中则替换为中文, 未命中原样显示。

范围（仅界面）:
  - 标签/按钮/分组框/复选框/单选框 的文本与构造函数文本
  - 下拉框/列表项、表格表头、页签标题、窗口标题、占位符、工具提示(Tooltip)
  - QMessageBox 对话框的标题与正文；标准按钮(Yes/No/OK)由 Qt 自带 qt_zh_CN 翻译
不处理:
  - 日志输出(QTextEdit.append)、提示词等功能性文本(setPlainText)
  - 参与逻辑判断的功能性下拉项(见下方 EXCLUDE)

用法:
  python launcher.py            # 中文界面启动
  python launcher.py --en       # 临时回退英文(仍走本启动器)
  python launcher.py --selftest # 离屏自测钩子是否生效
  未翻译的字符串会在退出时收集到 i18n/pending.json, 人工翻译后并入 zh_CN.json 即可
"""
import os
import sys
import json
import atexit
import threading
import runpy

BASE = os.path.dirname(os.path.abspath(__file__))
I18N_DIR = os.path.join(BASE, "i18n")
DICT_FILE = os.path.join(I18N_DIR, "zh_CN.json")
PENDING_FILE = os.path.join(I18N_DIR, "pending.json")

TRANSLATE = "--en" not in sys.argv
SELFTEST = "--selftest" in sys.argv
for _f in ("--en", "--selftest", "--collect"):
    while _f in sys.argv:
        sys.argv.remove(_f)

# ---------------------------------------------------------------------------
# 词典与安全排除
# ---------------------------------------------------------------------------

# 功能性字符串: 显示文本参与逻辑判断或会被持久化, 必须保持英文原样。
# (量化选项被 currentText().split()[0] 用于引擎参数; 蒙版格式被
#  "Alpha Channel" in currentText() 判断; "person" 是 SAM3 检测默认提示词;
#  逗号开头的是会被追加进 caption 文件的质量标签预设)
EXCLUDE = {
    "None (BF16 - Default)", "FP16 (Half Precision)", "FP8 (8-bit, Ada+)",
    "Int8 (BitsAndBytes)", "NF4 (4-bit)",
    "Separate File (*-masklabel.png)", "Masks Subfolder (masks/*.png)",
    "Alpha Channel (PNG)",
    "person",
}
# 动态计数类前缀(f-string 会持续用英文覆盖), 翻译初始值反而造成中英跳变, 跳过
SKIP_PREFIXES = ("Masks: ", "Images: ", "Captions: ", "GPU Mem: ", "GPU Load: ",
                 "Frame: ", "Scan Count: ", "0 of ")

DICT = {}
if TRANSLATE and os.path.isfile(DICT_FILE):
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f:
            DICT = json.load(f)
    except Exception as e:
        print(f"[i18n] 词典加载失败, 界面将以英文显示: {e}")

_missing = set()
_lock = threading.Lock()


def _translatable(s):
    """是否是值得收集的英文 UI 文本"""
    if not isinstance(s, str) or not s:
        return False
    if len(s) > 200:  # 超长文本多为提示词内容等功能性文本, 不属于界面
        return False
    if s in EXCLUDE or s.startswith(", ") or s.startswith(SKIP_PREFIXES):
        return False
    import re
    if not re.search(r"[A-Za-z]{2,}", s):
        return False
    if re.search(r"(color\s*:|font-weight|background-color|border\s|px;)",
                 s):  # QSS 样式串
        return False
    return True


def tr(s):
    """精确匹配翻译; 未命中记录待翻译, 返回原文(永不抛错)"""
    try:
        if not TRANSLATE or not isinstance(s, str):
            return s
        if s in EXCLUDE:
            return s
        t = DICT.get(s)
        if t:
            return t
        if _translatable(s):
            with _lock:
                _missing.add(s)
        return s
    except Exception:
        return s


def _tr_pos(args):
    """翻译位置参数中的词典命中串(数字/对象/枚举永远不会命中词典, 天然安全)"""
    out = []
    for a in args:
        if isinstance(a, str):
            out.append(tr(a))
        elif isinstance(a, (list, tuple)):
            out.append(type(a)(tr(x) if isinstance(x, str) else x for x in a))
        else:
            out.append(a)
    return tuple(out)


# ---------------------------------------------------------------------------
# 钩子安装
# ---------------------------------------------------------------------------

def install_patches():
    from PySide6.QtWidgets import (
        QApplication, QWidget, QLabel, QPushButton, QToolButton, QCheckBox,
        QRadioButton, QGroupBox, QTabWidget, QComboBox, QListWidget,
        QListWidgetItem, QLineEdit, QTextEdit, QPlainTextEdit,
        QTableWidgetItem, QTableWidget, QTreeWidget, QProgressDialog,
        QMessageBox, QSplashScreen, QFormLayout,
    )

    # ---- 通用包装器 -------------------------------------------------------
    def patch_method(cls, name):
        orig = getattr(cls, name)

        def wrapper(*args, **kwargs):
            try:
                args = _tr_pos(args)
            except Exception:
                pass
            return orig(*args, **kwargs)

        try:
            setattr(cls, name, wrapper)
        except Exception:
            pass

    def patch_ctor(cls):
        orig_init = cls.__init__

        def wrapper(self, *args, **kwargs):
            try:
                args = _tr_pos(args)
            except Exception:
                pass
            orig_init(self, *args, **kwargs)

        try:
            cls.__init__ = wrapper
        except Exception:
            pass

    # ---- 构造函数直接传入的文本 -------------------------------------------
    for c in (QLabel, QPushButton, QToolButton, QCheckBox, QRadioButton,
              QGroupBox, QTableWidgetItem, QListWidgetItem, QProgressDialog):
        patch_ctor(c)

    # ---- 各类 setter / addItem -------------------------------------------
    for c in (QLabel, QPushButton, QToolButton, QCheckBox, QRadioButton):
        patch_method(c, "setText")
    patch_method(QGroupBox, "setTitle")
    patch_method(QWidget, "setToolTip")       # 基类补丁覆盖全部子类
    patch_method(QWidget, "setWindowTitle")
    patch_method(QTabWidget, "addTab")
    patch_method(QTabWidget, "setTabText")
    for c in (QComboBox, QListWidget):
        patch_method(c, "addItem")
        patch_method(c, "addItems")
        patch_method(c, "insertItem")
    patch_method(QComboBox, "setItemText")
    for c in (QLineEdit, QTextEdit, QPlainTextEdit):
        patch_method(c, "setPlaceholderText")
    patch_method(QProgressDialog, "setLabelText")
    patch_method(QTableWidget, "setHorizontalHeaderLabels")
    patch_method(QTreeWidget, "setHeaderLabels")
    patch_method(QSplashScreen, "showMessage")
    # QFormLayout.addRow("Label:", widget) 的标签在 C++ 内部创建, 必须单独拦截
    patch_method(QFormLayout, "addRow")

    # ---- QMessageBox 静态对话框: 翻译 标题+正文, 标准按钮由 QTranslator 处理 --
    for name in ("question", "information", "warning", "critical", "about"):
        orig = getattr(QMessageBox, name)

        def make_wrapper(_orig):
            def wrapper(*args, **kwargs):
                try:
                    args = list(args)
                    start = 1 if (args and isinstance(args[0], QWidget)) else 0
                    for i in range(start, min(start + 2, len(args))):
                        if isinstance(args[i], str):
                            args[i] = tr(args[i])
                except Exception:
                    pass
                return _orig(*args, **kwargs)
            return wrapper

        try:
            setattr(QMessageBox, name, make_wrapper(orig))
        except Exception:
            pass

    # ---- QApplication 建立后加载 Qt 官方中文翻译(Yes/No/OK 等标准按钮) -------
    orig_app_init = QApplication.__init__
    _translator_holder = []

    def app_init(self, *args, **kwargs):
        orig_app_init(self, *args, **kwargs)
        if _translator_holder:
            return
        try:
            from PySide6.QtCore import QTranslator, QLibraryInfo, QLocale
            t = QTranslator()
            paths = [
                QLibraryInfo.path(QLibraryInfo.TranslationsPath),
                os.path.join(os.path.dirname(__import__("PySide6").__file__),
                             "translations"),
            ]
            for p in paths:
                if t.load(QLocale("zh_CN"), "qt", "_", p):
                    self.installTranslator(t)
                    _translator_holder.append(t)
                    break
        except Exception:
            pass

    QApplication.__init__ = app_init


# ---------------------------------------------------------------------------
# 待翻译字符串收集
# ---------------------------------------------------------------------------

def dump_pending():
    try:
        if not _missing:
            return
        old = set()
        if os.path.isfile(PENDING_FILE):
            with open(PENDING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                old = set(data.get("untranslated", []))
        merged = sorted(old | _missing, key=lambda s: (len(s), s))
        os.makedirs(I18N_DIR, exist_ok=True)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump({"untranslated": merged}, f, ensure_ascii=False, indent=1)
        print(f"[i18n] {len(merged)} 条未翻译字符串已记录到 i18n/pending.json")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 自测模式 (不启动主程序, 不加载 torch)
# ---------------------------------------------------------------------------

def selftest():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                                   QGroupBox, QComboBox, QTabWidget,
                                   QLineEdit, QCheckBox)
    install_patches()
    app = QApplication([])

    checks = []

    def chk(name, got, want):
        checks.append((name, got == want, got))

    lbl = QLabel("Load Model")
    chk("QLabel 构造器", lbl.text(), DICT.get("Load Model", "Load Model"))
    btn = QPushButton("START PROCESSING")
    btn.setText("Unload")
    chk("QPushButton setText", btn.text(), DICT.get("Unload", "Unload"))
    grp = QGroupBox("Generation Settings")
    chk("QGroupBox 构造器", grp.title(), DICT.get("Generation Settings", "Generation Settings"))
    combo = QComboBox()
    combo.addItem("None (BF16 - Default)")     # 必须保持英文(功能性)
    combo.addItem("Custom (Type below...)")    # 应被翻译
    chk("功能项排除", combo.itemText(0), "None (BF16 - Default)")
    chk("普通项翻译", combo.itemText(1), DICT.get("Custom (Type below...)", "Custom (Type below...)"))
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Captions")
    chk("页签翻译", tabs.tabText(0), DICT.get("Captions", "Captions"))
    edit = QLineEdit()
    edit.setPlaceholderText("Select an image to edit caption...")
    chk("占位符翻译", edit.placeholderText(),
        DICT.get("Select an image to edit caption...", "Select an image to edit caption..."))
    box = QCheckBox("Include subfolders")
    chk("复选框翻译", box.text(), DICT.get("Include subfolders", "Include subfolders"))
    lbl2 = QLabel()
    lbl2.setToolTip("Free up VRAM by removing the model from memory.")
    chk("Tooltip 翻译", lbl2.toolTip(),
        DICT.get("Free up VRAM by removing the model from memory.",
                 "Free up VRAM by removing the model from memory."))
    # 未命中词典的字符串必须原样显示且不崩溃
    lbl3 = QLabel("Something Not In Dict 12345")
    chk("未命中回退英文", lbl3.text(), "Something Not In Dict 12345")
    chk("动态计数跳过", tr("Images: 42"), "Images: 42")

    ok = all(c[1] for c in checks)
    for name, passed, got in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {got!r}")
    print(f"自测结果: {'全部通过' if ok else '存在失败项'}; 词典条目数: {len(DICT)}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    if SELFTEST:
        sys.exit(selftest())

    install_patches()
    atexit.register(dump_pending)

    sys.path.insert(0, BASE)
    os.chdir(BASE)  # main.py 依赖相对路径(settings.json/prompts/logo.png)

    # 以 __main__ 身份执行原程序, 效果等同 python main.py
    runpy.run_path(os.path.join(BASE, "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
