# -*- coding: utf-8 -*-
"""v4.2 NG/轴语义合成图验收测试（平移不被吸收 + Y 正方向=上 + origin_kind）。

覆盖：
  N1 整体 +0.03mm 平移 → 各行 dx≈+0.03 且 Pass
  N2 整体 +0.08mm 平移 → NG（0.05 公差，验证"不再吸收平移"）
  N3 轴语义：原点右/X 正、上/Y 正（Y 正方向=上），左/下为负
  N4 origin_kind="center" vs "corner" 的名义坐标区别
  N5 4×4 / 6×6 中心为连续值（±1.5 / ±2.5 格）
  N6 corner 语义一致性：角点原点的点实测≈(0,0)、Pass

运行（真实平台，勿设 QT_QPA_PLATFORM=offscreen）：
    python tools/ng_axis_test.py
退出码：0=全通过；1=有 FAIL；2=有 BLOCKED。
只读被测源码，不修改任何源文件。
"""
import inspect
import math
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np

from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

RESULTS = []

PITCH_PX = 200.0
DPI = 2032.0
PITCH_MM = PITCH_PX * 25.4 / DPI      # = 2.5mm，保证 pitch_mm*dpi/25.4 == 200px
COL_PX, COL_PY = 5, 6                 # 物理X(mm) / 物理Y(mm)
COL_DX, COL_DY, COL_DEV, COL_VERDICT = 9, 10, 11, 12


def rec(name, status, detail=""):
    RESULTS.append((name, status, detail))
    print("[%-7s] %s%s" % (status, name, ("  -> " + detail) if detail else ""))


def check(name, ok, detail=""):
    rec(name, "PASS" if ok else "FAIL", detail)
    return ok


def num(text):
    try:
        return float(str(text).strip())
    except Exception:  # noqa: BLE001
        return None


def stub_dialogs():
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")


def make_blank_image(size=(1200, 1200)):
    """仅用于提供 ImageSource（表格换算不用图像内容）。"""
    import cv2
    img = np.full((size[1], size[0]), 255, np.uint8)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, img)
    return path


def grid_via_api(win, center, xdir, ydir, origin_kind, rows, cols):
    """优先调用 MainWindow._make_grid_from_center；缺失时用等价 GridDefinition 直构。

    注意：_make_grid_from_center 的 rows/cols/pitch/dpi 读界面控件，因此先把参数写进控件。
    """
    try:
        win.sp_rows.setValue(int(rows))
        win.sp_cols.setValue(int(cols))
        win.sp_pitch.setValue(float(PITCH_MM))
        win.sp_dpi.setValue(float(DPI))
    except Exception:
        pass
    fn = getattr(win, "_make_grid_from_center", None)
    if fn is not None:
        try:
            names = list(inspect.signature(fn).parameters)
            g = fn(center, xdir, ydir, origin_kind) if ("origin_kind" in names or len(names) >= 4) \
                else fn(center, xdir, ydir)
            if g is None:
                g = getattr(win, "grid", None)
            if g is not None:
                return g, True
        except Exception as exc:  # noqa: BLE001
            rec("预检 _make_grid_from_center", "FAIL",
                "调用异常，改用直构: %r" % (exc,))
    from core.grid_definition import GridDefinition
    p_px = PITCH_MM * DPI / 25.4
    if origin_kind == "corner":
        origin = (float(center[0]), float(center[1]))
    else:
        c0, r0 = (cols - 1) / 2.0, (rows - 1) / 2.0
        x = np.asarray(xdir, np.float64); x /= (np.linalg.norm(x) or 1.0)
        y = np.asarray(ydir, np.float64); y /= (np.linalg.norm(y) or 1.0)
        origin = (center[0] - c0 * p_px * x[0] - r0 * p_px * y[0],
                  center[1] - c0 * p_px * x[1] - r0 * p_px * y[1])
    return GridDefinition(rows, cols, PITCH_MM, DPI, origin, xdir, ydir,
                          origin_kind=origin_kind), False


def set_win(win, src):
    win.src = src
    win.calibrated = True
    win.coeff_x = win.coeff_y = None
    win.piecewise = None
    win.reg_fit = None
    win.res_fb = None
    win._deskew_ang = None
    win.template_nominal_points = None
    win.tol_upper_mm = 0.05
    win.tol_lower_mm = -0.05
    try:
        win.chk_p1.setChecked(False)
    except Exception:  # noqa: BLE001
        pass
    win.sp_pitch.setValue(float(PITCH_MM))
    win.sp_dpi.setValue(float(DPI))


def results_for(grid, shift_mm=(0.0, 0.0)):
    """按 grid.points_px() 顺序造 CrossResult；shift_mm 为物理(+X右, +Y上)平移。"""
    from core.cross_detector import CrossResult
    ppm = DPI / 25.4                       # px per mm
    x = np.asarray(grid.x_dir_px, np.float64); x /= (np.linalg.norm(x) or 1.0)
    y = np.asarray(grid.y_dir_px, np.float64); y /= (np.linalg.norm(y) or 1.0)
    sx = (shift_mm[0] * x[0] + shift_mm[1] * y[0]) * ppm
    sy = (shift_mm[0] * x[1] + shift_mm[1] * y[1]) * ppm
    out = []
    for px, py in grid.points_px():
        out.append(CrossResult(float(px + sx), float(py + sy), True, "projection", 0.0, conf=1.0))
    return out


def fill_and_read(win, grid, results):
    from core.cross_detector import CrossResult  # noqa: F401
    win.grid = grid
    win.results = results
    win._fill_table()
    rows = []
    for i in range(win.table.rowCount()):
        item = win.table.item(i, COL_VERDICT)
        rows.append({
            "px": None,
            "x": num(win.table.item(i, COL_PX).text()) if win.table.item(i, COL_PX) else None,
            "y": num(win.table.item(i, COL_PY).text()) if win.table.item(i, COL_PY) else None,
            "dx": num(win.table.item(i, COL_DX).text()) if win.table.item(i, COL_DX) else None,
            "dy": num(win.table.item(i, COL_DY).text()) if win.table.item(i, COL_DY) else None,
            "dev": num(win.table.item(i, COL_DEV).text()) if win.table.item(i, COL_DEV) else None,
            "verdict": item.text() if item is not None else "",
        })
    return rows


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else float("nan")


def n1_translate_003(win, src):
    g, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "center", 5, 5)
    rows = fill_and_read(win, g, results_for(g, (0.03, 0.0)))
    dx = mean([r["dx"] for r in rows])
    ok_pass = all(r["verdict"] == "Pass" for r in rows)
    ok = (abs(dx - 0.03) <= 0.005) and ok_pass
    check("N1 +0.03mm 平移→dx≈+0.03 且 Pass", ok,
          "mean_dx=%.4f (期望 +0.03±0.005) pass=%d/%d" % (dx, sum(1 for r in rows if r['verdict'] == 'Pass'), len(rows)))


def n2_translate_008(win, src):
    g, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "center", 5, 5)
    rows = fill_and_read(win, g, results_for(g, (0.08, 0.0)))
    dx = mean([r["dx"] for r in rows])
    n_ng = sum(1 for r in rows if r["verdict"] == "NG")
    ok = (abs(dx - 0.08) <= 0.005) and n_ng == len(rows)
    check("N2 +0.08mm 平移→NG（不再吸收平移）", ok,
          "mean_dx=%.4f (期望 +0.08) NG=%d/%d" % (dx, n_ng, len(rows)))


def n3_axis_semantics(win, src):
    rows_n, cols_n = 5, 5
    g, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "center", rows_n, cols_n)
    rows = fill_and_read(win, g, results_for(g))
    # 行主序：i = r*cols + c；c=cols-1 最右，c=0 最左；r=0 最上（物理 Y 正），r=rows-1 最下
    # 契约：y_dir_px=行方向（图像向下）；物理 Y+ 向上由 _judge_ab 取反
    right = rows[cols_n - 1]
    left = rows[0]
    top = rows[0]
    bottom = rows[(rows_n - 1) * cols_n]
    ok_right = right["x"] is not None and right["x"] > 0
    ok_left = left["x"] is not None and left["x"] < 0
    ok_top = top["y"] is not None and top["y"] > 0
    ok_bottom = bottom["y"] is not None and bottom["y"] < 0
    ok_zero = all(abs(v) < 1e-6 for v in (right["dx"], left["dx"], top["dy"], bottom["dy"]))
    ok = ok_right and ok_left and ok_top and ok_bottom and ok_zero
    check("N3 轴语义(右+/上+/左-/下-)", ok,
          "右上点 X=%.3f Y=%.3f | 左下点 X=%.3f Y=%.3f | d≈0=%r"
          % (right["x"], top["y"], left["x"], bottom["y"], ok_zero))


def n4_origin_kind(win, src):
    from core.grid_definition import GridDefinition
    p = PITCH_MM
    gc, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "center", 3, 3)
    gk, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "corner", 3, 3)
    nm_c = gc.nominal_points_mm(include_p1=False)
    nm_k = gk.nominal_points_mm(include_p1=False)
    ok_c = (abs(nm_c[0][0] + p) < 1e-6 and abs(nm_c[0][1] - p) < 1e-6
            and abs(nm_c[4][0]) < 1e-6 and abs(nm_c[4][1]) < 1e-6)
    ok_k = (abs(nm_k[0][0]) < 1e-6 and abs(nm_k[0][1]) < 1e-6
            and abs(nm_k[8][0] - 2 * p) < 1e-6 and abs(nm_k[8][1] + 2 * p) < 1e-6)
    ok_kind = (getattr(gc, "origin_kind", None) == "center"
               and getattr(gk, "origin_kind", None) == "corner")
    ok = ok_c and ok_k and ok_kind
    check("N4 center vs corner 名义坐标", ok,
          "center首点=(%.3f,%.3f)中心=(%.3f,%.3f) | corner首点=(%.3f,%.3f)末点=(%.3f,%.3f) kind=%r/%r"
          % (nm_c[0][0], nm_c[0][1], nm_c[4][0], nm_c[4][1],
             nm_k[0][0], nm_k[0][1], nm_k[8][0], nm_k[8][1],
             getattr(gc, "origin_kind", None), getattr(gk, "origin_kind", None)))


def n5_even_center_continuous(win, src):
    p = PITCH_MM
    g4, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "center", 4, 4)
    g6, _ = grid_via_api(win, (600.0, 600.0), (1.0, 0.0), (0.0, 1.0), "center", 6, 6)
    tx4 = sorted({round(v[0], 6) for v in g4.nominal_points_mm(include_p1=False)})
    tx6 = sorted({round(v[0], 6) for v in g6.nominal_points_mm(include_p1=False)})
    ok4 = (abs(min(tx4) + 1.5 * p) < 1e-6 and abs(max(tx4) - 1.5 * p) < 1e-6)
    ok6 = (abs(min(tx6) + 2.5 * p) < 1e-6 and abs(max(tx6) - 2.5 * p) < 1e-6)
    ok = ok4 and ok6
    check("N5 4×4/6×6 理论中心连续值(±1.5/±2.5格)", ok,
          "4×4 tx=%s | 6×6 tx=%s" % (tx4, tx6))


def n6_corner_consistency(win, src):
    corner = (400.0, 800.0)
    g, _ = grid_via_api(win, corner, (1.0, 0.0), (0.0, 1.0), "corner", 3, 3)
    rows = fill_and_read(win, g, results_for(g))
    r0 = rows[0]
    ok = (r0["x"] is not None and abs(r0["x"]) < 1e-6 and abs(r0["y"]) < 1e-6
          and abs(r0["dx"]) < 1e-6 and abs(r0["dy"]) < 1e-6
          and all(r["verdict"] == "Pass" for r in rows))
    check("N6 corner 语义一致性", ok,
          "首点实测=(%r,%r) Δ=(%r,%r) pass=%d/%d"
          % (r0["x"], r0["y"], r0["dx"], r0["dy"],
             sum(1 for r in rows if r["verdict"] == "Pass"), len(rows)))


def main():
    print("ROOT =", ROOT, " PITCH_MM=%.4f DPI=%.0f PITCH_PX=%.1f" % (PITCH_MM, DPI, PITCH_PX))
    stub_dialogs()
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        import gui.main_window as _mw
        _mw._write_log_file = lambda *a, **k: None
    except Exception:  # noqa: BLE001
        pass

    from core.grid_definition import GridDefinition
    has_kind = "origin_kind" in getattr(GridDefinition, "__dataclass_fields__", {})
    rec("预检 GridDefinition.origin_kind", "PASS" if has_kind else "BLOCKED",
        repr(getattr(GridDefinition(3, 3, PITCH_MM, DPI), "origin_kind", "缺失")))
    from gui.main_window import MainWindow
    rec("预检 MainWindow._make_grid_from_center",
        "PASS" if hasattr(MainWindow, "_make_grid_from_center") else "BLOCKED",
        "存在" if hasattr(MainWindow, "_make_grid_from_center") else "缺失（N1/N2/N3 将用等价 GridDefinition 直构）")

    src = None
    try:
        from core.image_loader import ImageSource
        src = ImageSource(make_blank_image())
    except Exception:  # noqa: BLE001
        rec("预检 ImageSource", "BLOCKED", traceback.format_exc().strip().splitlines()[-1])

    win = MainWindow()
    set_win(win, src)

    for fn, name in ((n1_translate_003, "N1"), (n2_translate_008, "N2"),
                     (n3_axis_semantics, "N3"), (n4_origin_kind, "N4"),
                     (n5_even_center_continuous, "N5"), (n6_corner_consistency, "N6")):
        if src is None:
            rec(name, "BLOCKED", "无 ImageSource")
            continue
        try:
            fn(win, src)
        except Exception:  # noqa: BLE001
            rec(name, "FAIL", traceback.format_exc().strip().splitlines()[-1])

    print("\n===== 汇总 =====")
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_block = sum(1 for _, s, _ in RESULTS if s == "BLOCKED")
    for name, s, d in RESULTS:
        print("%-7s %s%s" % (s, name, ("  -> " + d) if d else ""))
    print("PASS=%d FAIL=%d BLOCKED=%d" % (n_pass, n_fail, n_block))
    try:
        win.close()
    except Exception:  # noqa: BLE001
        pass
    if n_fail:
        return 1
    if n_block:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
