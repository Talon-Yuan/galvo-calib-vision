# -*- coding: utf-8 -*-
"""自审截图工具：用真实平台（不 offscreen）渲染窗口并 grab() 成 PNG。

用法：python tools/shot.py [输出目录]
说明：offscreen 平台没有字体，截出来是空白；这里必须用默认平台插件，
      才能拿到真实字体与布局，用于和渲染图逐像素比对。
"""
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shots")
os.makedirs(OUT, exist_ok=True)
os.environ.pop("QT_QPA_PLATFORM", None)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

from PyQt5.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
from gui.main_window import MainWindow

w = MainWindow()
w.resize(1440, 900)
w.show()
app.processEvents()
app.processEvents()


def shot(name, widget=None):
    widget = widget or w
    app.processEvents()
    p = os.path.join(OUT, name + ".png")
    widget.grab().save(p)
    print("shot:", p)


# 主窗口：浅色三页
for idx, tag in ((0, "tab1_calib"), (1, "tab2_array"), (2, "tab3_detect")):
    w.tabs.setCurrentIndex(idx)
    app.processEvents()
    shot("main_light_" + tag)

# 深色三页
w.act_dark.setChecked(True)
app.processEvents()
for idx, tag in ((0, "tab1_calib"), (1, "tab2_array"), (2, "tab3_detect")):
    w.tabs.setCurrentIndex(idx)
    app.processEvents()
    shot("main_dark_" + tag)
w.act_dark.setChecked(False)
app.processEvents()

# 对话框
os.environ["GCV_NOEXEC"] = "1"
try:
    from gui.multi_pose_dialog import MultiPoseDialog
    mp = MultiPoseDialog(w)
    mp.resize(980, 720)
    mp.show()
    app.processEvents()
    shot("dlg_multi_pose_light", mp)
    w.act_dark.setChecked(True)
    app.processEvents()
    shot("dlg_multi_pose_dark", mp)
    w.act_dark.setChecked(False)
    mp.close()
except Exception as e:
    print("multi pose shot failed:", e)

for fn, name in (("_on_column_settings", "dlg_columns"), ("_on_about", "dlg_about")):
    try:
        getattr(w, fn)()
        app.processEvents()
        d = getattr(w, "_last_dialog", None)
        if d is not None:
            d.show()
            app.processEvents()
            shot(name, d)
            d.close()
    except Exception as e:
        print(fn, "failed:", e)

print("done ->", OUT)
