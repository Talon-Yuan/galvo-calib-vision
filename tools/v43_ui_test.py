# -*- coding: utf-8 -*-
"""v4.3 UI/解码策略回归（合成数据，不依赖 2.23Gpx 大图）。

断言：
V1 默认检测方法 = 寻边法（cb_method.index == 1）
V2 棋盘格面板"原图处理"默认勾选（chk_full.isChecked()）
V3 绿色十字标记按阵列角度旋转（add_cross_mark angle）
V4 原点锚点绿色 Y 箭头指向物理 +Y（= -y_dir，图像上方）
V5 ImageSource 解码策略：小图原图模式 level=1；模拟 use_full=False → level=2
V6 检测日志/状态含解码策略提示（代码路径存在）
"""
import math
import os
import sys

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import cv2
from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv[:1])

FAILS = []


def chk(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def main():
    import gui.main_window as MW

    win = MW.MainWindow()
    chk(win.cb_method.currentIndex() == 1, "V1 默认检测方法=寻边法",
        "index=%d, text=%s" % (win.cb_method.currentIndex(), win.cb_method.currentText()))
    chk(win.calib_widget.chk_full.isChecked(), "V2 棋盘格面板原图处理默认勾选")

    # V3：绿色十字按角度旋转（30°）
    view = win.view
    view.clear_overlay()
    ang = math.radians(30.0)
    before = len(view._overlay_items)
    view.add_cross_mark(100.0, 100.0, size=10.0, angle=ang)
    lines = view._overlay_items[before:]
    chk(len(lines) == 2, "V3 绿色十字生成 2 条臂", str(len(lines)))
    if len(lines) == 2:
        l1 = lines[0]
        a = l1.line()
        dx, dy = a.x2() - a.x1(), a.y2() - a.y1()
        got = math.degrees(math.atan2(dy, dx)) % 180.0
        chk(abs(got - 30.0) < 0.5, "V3 横臂角度=30°", "%.2f" % got)

    # V4：原点锚点绿色 Y 箭头朝上（图像 y 减小）
    view.clear_overlay()
    view.add_origin_mark(200.0, 200.0, x_dir=(1.0, 0.0), y_dir=(0.0, 1.0), label="O")
    y_lines = [it for it in view._overlay_items
               if hasattr(it, "line") and abs(it.line().x2() - it.line().x1()) < 1e-6
               and abs(it.line().y2() - it.line().y1()) > 1e-6]
    ok_up = bool(y_lines) and (y_lines[-1].line().y2() < y_lines[-1].line().y1())
    chk(ok_up, "V4 绿色Y箭头朝上（物理+Y）",
        "" if not y_lines else "y1=%.1f y2=%.1f" % (y_lines[-1].line().y1(), y_lines[-1].line().y2()))

    # V5：小图解码策略（临时合成 1200x900 图）
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "_v43_tmp.png")
    canvas = np.full((900, 1200), 240, np.uint8)
    cv2.line(canvas, (500, 400), (700, 400), 30, 5)
    cv2.line(canvas, (600, 300), (600, 500), 30, 5)
    ok, buf = cv2.imencode(".png", canvas)
    with open(tmp, "wb") as fp:
        fp.write(buf.tobytes())
    from core.image_loader import ImageSource
    s_full = ImageSource(tmp, use_full=True)
    g1 = s_full.original_gray()
    chk(g1.shape == (900, 1200) and s_full.decode_mode == "full", "V5 原图模式：level=1",
        "%s %s" % (g1.shape, s_full.decode_mode))
    s_half = ImageSource(tmp, use_full=False)
    g2 = s_half.original_gray()
    chk(g2.shape == (900, 1200) and s_half.decode_mode == "half", "V5 后台1/2：尺寸还原、mode=half",
        "%s %s" % (g2.shape, s_half.decode_mode))
    try:
        os.remove(tmp)
    except OSError:
        pass

    # V6：检测路径含解码策略日志
    src = open(os.path.join(ROOT, "gui", "main_window.py"), encoding="utf-8").read()
    chk("main.decode_mode_full_original" in src and "main.decode_mode_half_downsample" in src,
        "V6 检测日志含解码策略提示")

    print()
    print("RESULT =", "PASS" if not FAILS else "FAIL: " + "; ".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

