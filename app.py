# -*- coding: utf-8 -*-
"""振镜校准视觉定位软件入口。"""
import os
import sys


# ---- 崩溃落盘（v3.5）：窗口化程序崩溃时没有控制台、不弹窗，必须主动留堆栈 ----
# faulthandler 能捕获 Python 层之外的硬崩溃（段错误/栈溢出/Qt 原生崩溃）；
# 已有的 sys.excepthook 只覆盖 Python 异常，且在 GUI 初始化前就崩溃时无效。
import faulthandler as _fh
import time as _time
try:
    _logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(_logdir, exist_ok=True)
    _crash_fp = open(os.path.join(_logdir, "crash_%s.txt" % _time.strftime("%Y%m%d_%H%M%S")), "w")
    _fh.enable(_crash_fp, all_threads=True)
except Exception:
    pass

# PyInstaller onedir 运行时兜底：显式指定 Qt 平台插件目录，避免个别机器
# 因插件路径解析失败而报 "could not find or load the Qt platform plugin"。
if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
    _cand = [
        os.path.join(_base, "_internal", "PyQt5", "Qt", "plugins"),
        os.path.join(_base, "PyQt5", "Qt", "plugins"),
    ]
    for _p in _cand:
        if os.path.isdir(_p):
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", _p)
            break

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

# 高分屏适配：必须在 QApplication 创建前设置，否则 125%/150% 缩放下控件显示不全
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)


def main():
    from gui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("GalvoCalibVision")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
