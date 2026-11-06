# -*- coding: utf-8 -*-
"""真实图多轮测试：检测结果/导出的"起始点(编号1)"是否在图像左下角。

用法：
  python tools/real_rounds_start_test.py --out <分析目录>
说明：
  * 用桌面 2026-11-06-0004..0009.jpg（4800dpi 真实扫描）+ 棋盘格标定；
  * 每张图按"单点中心建系"流程建 3x3 网格（pitch 用该轮真实实测值，避免场漂移导致漏检）；
  * 每张图跑两遍：A=软件当前默认编号模式；B=强制"左下起·行主序"；
  * 校验：编号1 的点是否为图像最下+最左；导出的 Sheet2 第一行是否 序号=1、行=底、列=1；
    Sheet1 第一点(序号3/P3)名义值是否为左下角 (-p,-p)。
"""
import argparse
import json
import math
import os
import sys
import tempfile
import time

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from PyQt5.QtWidgets import QApplication

DESK = r"C:\Users\17523\OneDrive\桌面"
CHESS = r"F:\工作文件夹\校正软件开发测试结果\实际测试\扫描图像\2026-10-14-0001.jpg"
CASES = [
    ("0004", "50.04"), ("0005", "55.98"), ("0006", "70.73"),
    ("0007", "50.41"), ("0008", "50.03"), ("0009", "50.01"),
]


class MB:
    Ok = 1
    Yes = 2
    No = 3
    Warning = 0
    Critical = 2
    Question = 4

    def __init__(self, *a, **k):
        pass

    def setWindowTitle(self, *a):
        pass

    def setText(self, *a):
        pass

    def addButton(self, *a, **k):
        return object()

    def clickedButton(self):
        return None

    def exec_(self):
        return 0

    @staticmethod
    def information(*a, **k):
        return 1

    warning = information
    critical = information
    question = information


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tags", default="")
    args = ap.parse_args()
    out = args.out
    os.makedirs(out, exist_ok=True)

    app = QApplication(sys.argv[:1])
    import gui.main_window as MW
    import gui.calibration_widget as CW
    MW.QMessageBox = MB
    CW.QMessageBox = MB
    CW.qfd_open = lambda *a, **k: (CHESS, "")
    s_xlsx = [os.path.join(tempfile.gettempdir(), "_round_export.xlsx")]
    MW.qfd_save = lambda *a, **k: (s_xlsx[0], "")

    # 真值中心（px）
    truth_path = os.path.join(out, "_evidence", "truth_points_full.json")
    truth = json.load(open(truth_path, encoding="utf-8"))
    centers = {}
    rots = {}
    for tag, rec in truth.items():
        pts = sorted(rec["points"], key=lambda q: (q["row"], q["col"]))
        cx = sum(p["px_x"] for p in pts) / len(pts)
        cy = sum(p["px_y"] for p in pts) / len(pts)
        centers[tag] = (cx, cy)
        dxs, dys = [], []
        for i in range(0, len(pts) - 1, 3):
            for j in range(3 - 1):
                dxs.append(pts[i + j + 1]["px_x"] - pts[i + j]["px_x"])
                dys.append(pts[i + j + 1]["px_y"] - pts[i + j]["px_y"])
        rots[tag] = math.atan2(sum(dys) / len(dys), sum(dxs) / len(dxs)) if dxs else 0.0

    win = MW.MainWindow()
    win.resize(1400, 900)
    win.show()
    app.processEvents()

    # ---- 棋盘格标定 ----
    cw = win.calib_widget
    cw.btn_load.click()
    t = time.time()
    while time.time() - t < 180 and cw.src is None:
        app.processEvents(); time.sleep(0.1)
    cw.btn_auto.click()
    t = time.time()
    while time.time() - t < 300:
        app.processEvents(); time.sleep(0.2)
        if cw.btn_auto.isEnabled() and time.time() - t > 3:
            break
    cw.btn_calib.click()
    t = time.time()
    while time.time() - t < 300:
        app.processEvents(); time.sleep(0.2)
        if cw.result is not None:
            break
    print("calib ok:", bool(getattr(cw.result, "ok", False)),
          "rms=%.4f" % (getattr(cw.result, "rms_mm", 0) or 0), flush=True)

    import openpyxl
    rows_report = []
    for mode_label, mode in (("default(当前默认)", None), ("bottom_left_row_major", "bottom_left_row_major")):
        for tag, pitch in CASES:
            if args.tags and tag not in args.tags.split(","):
                continue
            path = os.path.join(DESK, "2026-11-06-%s.jpg" % tag)
            if not os.path.isfile(path):
                continue
            # 复位
            win._reset_pick()
            win.results = []
            win.sp_rows.setValue(3); win.sp_cols.setValue(3)
            win.sp_pitch.setValue(float(pitch)); win.sp_dpi.setValue(4800.0)
            if mode is None:
                win.order_mode = MW.DEFAULT_MODE
            else:
                win.order_mode = mode
            # 打开图
            MW.qfd_open = lambda *a, **k: (path, "")
            win._on_open_image()
            t = time.time()
            while time.time() - t < 300:
                app.processEvents(); time.sleep(0.1)
                s = getattr(win, "src", None)
                if s is not None and os.path.normcase(os.path.abspath(s.path)) == os.path.normcase(os.path.abspath(path)):
                    break
            if win.src is None:
                rows_report.append((mode_label, tag, "LOAD_FAIL", "", "", ""))
                continue
            # 单点中心建系（用真值中心做点击点）
            cx, cy = centers[tag]
            win.origin_xy = (cx, cy)
            th = rots.get(tag, 0.0)
            xdir = (math.cos(th), math.sin(th))
            ydir = (-math.sin(th), math.cos(th))   # 行方向（图像向下）
            g = win._make_grid_from_center((cx, cy), xdir, ydir, "center")
            if g is None:
                rows_report.append((mode_label, tag, "GRID_FAIL", "", "", ""))
                continue
            # 检测
            win._on_detect()
            t = time.time()
            while time.time() - t < 300:
                app.processEvents(); time.sleep(0.2)
                if win.results and any(r is not None for r in win.results):
                    break
            t = time.time()
            while time.time() - t < 180:
                app.processEvents(); time.sleep(0.2)
                th = getattr(win, "_detect_thread", None)
                if th is None or not th.isRunning():
                    break
            ok = [r for r in win.results if r is not None and r.ok]
            if len(ok) < 9:
                rows_report.append((mode_label, tag, "N=%d" % len(ok), "", "", ""))
                continue
            # 编号1 对应的点
            idx1 = next((i for i in range(len(win.results)) if win._order_display_no(i) == 1), None)
            r1 = win.results[idx1] if idx1 is not None else None
            if r1 is None or not r1.ok:
                rows_report.append((mode_label, tag, "NO_P1", "", "", ""))
                continue
            # 判据用"网格行列"而不是像素极值：阵列有旋转时最下/最左像素点会偏移。
            # 3x3 行主序、左上为 row1：左下角 = 第 3 行第 1 列 = 索引 6。
            is_bottom = (idx1 == (3 - 1) * 3 + 0)
            is_left = (idx1 % 3 == 0)
            # 导出并检查 Sheet2 第一行 / Sheet1 第一点
            win._on_export()
            app.processEvents()
            seq1 = rowc = colc = None
            sheet1 = None
            if os.path.isfile(s_xlsx[0]):
                wb = openpyxl.load_workbook(s_xlsx[0], data_only=True)
                ws2 = wb.worksheets[1]
                # 找到序号=1 的数据行
                for rr in range(2, ws2.max_row + 1):
                    if ws2.cell(rr, 1).value == 1:
                        seq1 = 1
                        rowc = ws2.cell(rr, 2).value
                        colc = ws2.cell(rr, 3).value
                        break
                ws1 = wb.worksheets[0]
                sheet1 = (ws1.cell(10, 1).value, ws1.cell(10, 2).value,
                          ws1.cell(10, 4).value, ws1.cell(10, 5).value,
                          ws1.cell(11, 4).value)
                os.remove(s_xlsx[0])
            exp_ok = (rowc == 3 and colc == 1)
            s1_ok = (sheet1 is not None and abs(float(sheet1[2]) + float(pitch)) < 1e-6
                     and abs(float(sheet1[4]) + float(pitch)) < 1e-6)
            verdict = "PASS" if (is_bottom and is_left and exp_ok and s1_ok) else "FAIL"
            rows_report.append((mode_label, tag, verdict,
                                "P1=%.0f,%.0f" % (r1.x, r1.y),
                                "Sheet2 序号1 row=%s col=%s" % (rowc, colc),
                                "Sheet1 P3 nom=%s,%s | bottom=%s left=%s exp=%s s1=%s" % (
                                    sheet1[2], sheet1[4], is_bottom, is_left, exp_ok, s1_ok) if sheet1 else ""))

    print("\n%-22s %-6s %-8s %-18s %-22s %s" % ("模式", "图", "结果", "P1 px", "Sheet2", "Sheet1 P3 名义"))
    for r in rows_report:
        print("%-22s %-6s %-8s %-18s %-22s %s" % r)
    # 保存 csv
    import csv
    with open(os.path.join(out, "真实图起始点测试.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["编号模式", "图像", "结果", "P1像素", "Sheet2序号1行/列", "Sheet1首点名义"])
        w.writerows(rows_report)
    fails = [r for r in rows_report if r[2] != "PASS"]
    print("\nRESULT =", "PASS" if not fails else "FAIL: %d 项" % len(fails))


if __name__ == "__main__":
    main()
