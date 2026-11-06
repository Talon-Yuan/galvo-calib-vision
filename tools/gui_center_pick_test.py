# -*- coding: utf-8 -*-
"""v4.2 辅助建系（中心点选）合成图验收测试。

覆盖：
  T1 CenterProbeThread 单点中心：点击偏 30px → 精测中心误差 ≤0.5px
  T2 2° 旋转合成图 → 方向误差 ≤0.3°、四邻域成功 ≥3
  T3 MainWindow._make_grid_from_center 中心→角点换算误差 ≤0.5px（center/corner）
  T4 3×3 检测 9/9（走真实 GUI 回调 _on_center_probe_done）
  T5 4×4 偶数（中心无十字）：corner1 → 16/16（真实回调）＋ corner 语义直调对照
  T6 点击离阵列 >1.5 pitch → ok=False、不崩、不建网格（真实回调）
  T7 三点模式回归：原点=起始点、方向规范（X右/Y上）、9/9

运行（真实平台，勿设 QT_QPA_PLATFORM=offscreen）：
    python tools/gui_center_pick_test.py
退出码：0=全通过；1=有 FAIL；2=有 BLOCKED（接口未就绪）且无 FAIL。
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

from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox

RESULTS = []

PITCH_PX = 200.0
DPI = 2032.0
PITCH_MM = PITCH_PX * 25.4 / DPI      # = 2.5mm，使 pitch_mm*dpi/25.4 == 200px


def rec(name, status, detail=""):
    RESULTS.append((name, status, detail))
    print("[%-7s] %s%s" % (status, name, ("  -> " + detail) if detail else ""))


def check(name, ok, detail=""):
    rec(name, "PASS" if ok else "FAIL", detail)
    return ok


def angle_err_deg(u, v):
    au = math.atan2(u[1], u[0])
    av = math.atan2(v[1], v[0])
    d = math.degrees(av - au) % 360.0
    return min(d, 360.0 - d)


def stub_dialogs():
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")


# ---------------- 合成图 ----------------
def make_cross_image(rows, cols, pitch_px, center_xy=None, angle_deg=0.0, size=None):
    """白底黑细十字阵列（臂长 0.15*pitch，贴近真实扫描；高斯脊保证亚像素中心）。"""
    c0, r0 = (cols - 1) / 2.0, (rows - 1) / 2.0
    span_x, span_y = (cols - 1) * pitch_px, (rows - 1) * pitch_px
    if size is None:
        size = (int(math.ceil(span_x + 2.2 * pitch_px)), int(math.ceil(span_y + 2.2 * pitch_px)))
    w, h = int(size[0]), int(size[1])
    if center_xy is None:
        center_xy = (w / 2.0, h / 2.0)
    th = math.radians(angle_deg)
    ca, sa = math.cos(th), math.sin(th)
    canvas = np.full((h, w), 255.0, np.float64)
    arm = 0.15 * pitch_px
    sigma = 1.35
    positions = {}
    for r in range(rows):
        for c in range(cols):
            dx, dy = (c - c0) * pitch_px, (r - r0) * pitch_px
            x = center_xy[0] + ca * dx - sa * dy
            y = center_xy[1] + sa * dx + ca * dy
            positions[(r, c)] = (x, y)
            x0, x1 = max(0, int(x - arm - 4)), min(w, int(x + arm + 5))
            y0, y1 = max(0, int(y - arm - 4)), min(h, int(y + arm + 5))
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
            hz = np.exp(-((yy - y) ** 2) / (2 * sigma ** 2)) * (np.abs(xx - x) <= arm)
            vt = np.exp(-((xx - x) ** 2) / (2 * sigma ** 2)) * (np.abs(yy - y) <= arm)
            canvas[y0:y1, x0:x1] -= 210.0 * np.clip(hz + vt, 0.0, 1.0)
    import cv2
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, np.clip(canvas, 0, 255).astype(np.uint8))
    return path, (float(center_xy[0]), float(center_xy[1])), positions


def load_src(path):
    from core.image_loader import ImageSource
    return ImageSource(path)


def run_probe(src, hint, mode, rows, cols, pitch_mm, dpi, params, second=None,
              timeout_ms=60000):
    from gui.main_window import CenterProbeThread
    th = CenterProbeThread(src, hint, mode, rows, cols, pitch_mm, dpi, params,
                           second_px=second)
    box = {}
    loop = QEventLoop()
    th.done.connect(lambda p: (box.setdefault("p", p), loop.quit()))
    th.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    th.start()
    loop.exec_()
    th.wait(5000)
    return box.get("p")


def detect_all(gray, pts, pitch_px, params):
    from core.cross_detector import detect_points_fast
    res = detect_points_fast(gray, np.asarray(pts, np.float64), float(pitch_px), params)
    return sum(1 for r in res if r is not None and r.ok), res


def params_obj():
    from core.config import CrossDetectParams
    return CrossDetectParams(threshold_mode="otsu", polarity="auto",
                             search_window_scale=1.2, method="projection")


def expected_corner(center, xdir, ydir, rows, cols, pitch_px):
    c0, r0 = (cols - 1) / 2.0, (rows - 1) / 2.0
    x = np.asarray(xdir, np.float64); x /= (np.linalg.norm(x) or 1.0)
    y = np.asarray(ydir, np.float64); y /= (np.linalg.norm(y) or 1.0)
    return (center[0] - c0 * pitch_px * x[0] - r0 * pitch_px * y[0],
            center[1] - c0 * pitch_px * x[1] - r0 * pitch_px * y[1])


def make_win():
    from gui.main_window import MainWindow
    return MainWindow()


def set_params(win, rows, cols):
    win.sp_rows.setValue(int(rows))
    win.sp_cols.setValue(int(cols))
    win.sp_pitch.setValue(float(PITCH_MM))
    win.sp_dpi.setValue(float(DPI))
    win.calibrated = True
    try:
        win.chk_p1.setChecked(False)
    except Exception:  # noqa: BLE001
        pass


def set_pick_mode(win, mode):
    """切到指定点选模式（center1/corner1/center2/three）。"""
    from gui.main_window import PICK_MODES
    try:
        win.cb_pick_mode.setCurrentIndex(PICK_MODES.index(mode))
    except Exception:  # noqa: BLE001
        return False
    return win._pick_mode() == mode


def make_grid_direct(win, center, xdir, ydir, origin_kind):
    """直调 _make_grid_from_center，返回 (grid, used_api)。"""
    fn = getattr(win, "_make_grid_from_center", None)
    if fn is None:
        return None, False
    try:
        names = list(inspect.signature(fn).parameters)
        out = fn(center, xdir, ydir, origin_kind) if ("origin_kind" in names or len(names) >= 4) \
            else fn(center, xdir, ydir)
    except Exception:  # noqa: BLE001
        return None, False
    if out is not None and hasattr(out, "origin_px"):
        return out, True
    return getattr(win, "grid", None), True


# ---------------- 测试 ----------------
def t1_single_center(params):
    rows = cols = 5
    path, center, _ = make_cross_image(rows, cols, PITCH_PX, size=(1200, 1200))
    src = load_src(path)
    payload = run_probe(src, (center[0] + 30.0, center[1] - 12.0), "center1",
                        rows, cols, PITCH_MM, DPI, params)
    if not payload:
        return check("T1 单点精测(偏30px)", False, "无 done 载荷（线程超时）")
    if not payload.get("ok"):
        return check("T1 单点精测(偏30px)", False, "ok=False msg=%r" % (payload.get("message"),))
    cx, cy = payload["center_px"]
    err = math.hypot(cx - center[0], cy - center[1])
    return check("T1 单点精测(偏30px)", err <= 0.5,
                 "center=(%.3f,%.3f) err=%.3fpx(≤0.5)" % (cx, cy, err))


def t2_rotated(params):
    rows = cols = 5
    ang = 2.0
    path, center, _ = make_cross_image(rows, cols, PITCH_PX, angle_deg=ang, size=(1200, 1200))
    src = load_src(path)
    payload = run_probe(src, (center[0] + 25.0, center[1] + 18.0), "center1",
                        rows, cols, PITCH_MM, DPI, params)
    if not payload or not payload.get("ok"):
        return check("T2 2°旋转方向/邻域", False, "ok=%r" % (payload and payload.get("ok"),))
    th = math.radians(ang)
    e_x = angle_err_deg(payload["x_dir"], (math.cos(th), math.sin(th)))
    e_y = angle_err_deg(payload["y_dir"], (-math.sin(th), math.cos(th)))  # 行方向=图像向下
    n = int(payload.get("neighbors", 0))
    return check("T2 2°旋转方向/邻域", e_x <= 0.3 and e_y <= 0.3 and n >= 3,
                 "x_err=%.3f° y_err=%.3f° neighbors=%d(≤0.3°,≥3)" % (e_x, e_y, n))


def t3t4_center_path(win, params):
    """T3/T4：center1 探针 → 真实回调 → 网格 origin≈角点 → 3×3 检测 9/9。"""
    rows = cols = 3
    path, center, _ = make_cross_image(rows, cols, PITCH_PX, size=(1000, 1000))
    src = load_src(path)
    gray = src.original_gray()
    set_params(win, rows, cols)
    win.src = src
    win.grid = None
    if not set_pick_mode(win, "center1"):
        rec("T3 中心→角点换算", "BLOCKED", "缺少点选模式下拉 cb_pick_mode")
        rec("T4 3×3 检测 9/9", "BLOCKED", "同上")
        return
    payload = run_probe(src, (center[0] + 30.0, center[1] - 12.0), "center1",
                        rows, cols, PITCH_MM, DPI, params)
    if not payload or not payload.get("ok"):
        rec("T3 中心→角点换算", "FAIL", "探针失败: %r" % (payload and payload.get("message"),))
        rec("T4 3×3 检测 9/9", "BLOCKED", "探针失败")
        return
    win._on_center_probe_done(payload)          # 真实 GUI 回路径
    g = win.grid
    if g is None:
        return check("T3 中心→角点换算", False, "回调后 grid 仍为 None")
    exp = expected_corner(payload["center_px"], payload["x_dir"], payload["y_dir"],
                          rows, cols, PITCH_PX)
    err = math.hypot(g.origin_px[0] - exp[0], g.origin_px[1] - exp[1])
    check("T3 中心→角点换算", err <= 0.5 and getattr(g, "origin_kind", "") == "center",
          "origin=%.3f,%.3f 期望=%.3f,%.3f err=%.3f(≤0.5) kind=%r"
          % (g.origin_px[0], g.origin_px[1], exp[0], exp[1], err, getattr(g, "origin_kind", None)))
    n_ok, _ = detect_all(gray, g.points_px(), g.pitch_px, params)
    check("T4 3×3 检测 9/9", n_ok == 9, "detected %d/9" % n_ok)


def t5_even_4x4(win, params):
    rows = cols = 4
    path, center, pos = make_cross_image(rows, cols, PITCH_PX, size=(1000, 1000))
    src = load_src(path)
    gray = src.original_gray()
    set_params(win, rows, cols)
    win.src = src
    win.grid = None
    c0 = r0 = 1.5
    picked_corner = (center[0] - c0 * PITCH_PX, center[1] - r0 * PITCH_PX)  # row0/col0（图像上方）
    # (a) 直调 API：corner 语义 origin=角点 → 16/16
    set_pick_mode(win, "corner1")
    g, used = make_grid_direct(win, picked_corner, (1.0, 0.0), (0.0, 1.0), "corner")
    if not used:
        rec("T5 4×4 偶数 16/16", "BLOCKED", "_make_grid_from_center 不存在")
        return
    err = math.hypot(g.origin_px[0] - picked_corner[0], g.origin_px[1] - picked_corner[1])
    n_direct, _ = detect_all(gray, g.points_px(), g.pitch_px, params)
    check("T5a 4×4 corner 语义→16/16", err <= 0.5 and n_direct == 16,
          "origin_err=%.3f detected=%d/16" % (err, n_direct))
    # (b) 真实 GUI 回调路径：corner1 探针 → _on_center_probe_done → 16/16
    win.grid = None
    payload = run_probe(src, picked_corner, "corner1", rows, cols, PITCH_MM, DPI, params)
    if not payload or not payload.get("ok"):
        return check("T5b 4×4 corner1 真实回调→16/16", False,
                     "探针失败 ok=%r msg=%r" % (payload and payload.get("ok"),
                                                payload and payload.get("message")))
    win._on_center_probe_done(payload)
    g2 = win.grid
    if g2 is None:
        return check("T5b 4×4 corner1 真实回调→16/16", False, "回调后 grid 仍为 None")
    err2 = math.hypot(g2.origin_px[0] - picked_corner[0], g2.origin_px[1] - picked_corner[1])
    n_gui, _ = detect_all(gray, g2.points_px(), g2.pitch_px, params)
    check("T5b 4×4 corner1 真实回调→16/16", err2 <= 0.5 and n_gui == 16,
          "origin=%.1f,%.1f 期望(角点)=%.1f,%.1f err=%.1f detected=%d/16 kind=%r"
          % (g2.origin_px[0], g2.origin_px[1], picked_corner[0], picked_corner[1],
             err2, n_gui, getattr(g2, "origin_kind", None)))


def t6_far_click(win, params):
    rows = cols = 3
    path, center, _ = make_cross_image(rows, cols, PITCH_PX, size=(1400, 1000))
    src = load_src(path)
    set_params(win, rows, cols)
    win.src = src
    win.grid = None
    set_pick_mode(win, "center1")
    before = win.grid
    far = (center[0] + 3.0 * PITCH_PX, center[1])   # 距最近十字 2 pitch > 1.5 pitch
    payload = run_probe(src, far, "center1", rows, cols, PITCH_MM, DPI, params)
    if payload is None:
        return check("T6 远点点击失败不崩", False, "线程超时/无载荷")
    ok_thread = payload.get("ok") is False
    note = "probe.ok=%r msg=%r neighbors=%r" % (payload.get("ok"), payload.get("message"),
                                                payload.get("neighbors"))
    if ok_thread:
        try:
            win._on_center_probe_done(payload)
        except Exception as exc:  # noqa: BLE001
            return check("T6 远点点击失败不崩", False, note + " 回调异常=%r" % (exc,))
        ok = win.grid is before
        note += " 回调后未建网格=%r" % (ok,)
        return check("T6 远点点击失败不崩", ok, note)
    # 线程层已误判 ok=True：验证 GUI 是否会建出假网格（期望不建）
    try:
        win._on_center_probe_done(payload)
    except Exception as exc:  # noqa: BLE001
        return check("T6 远点点击失败不崩", False, note + " 回调异常=%r" % (exc,))
    built = win.grid is not None
    return check("T6 远点点击失败不崩", not built,
                 note + " → 前端是否建网格=%r（期望 ok=False 且不建）" % (built,))


def t7_three_point(win, params):
    rows = cols = 3
    path, center, pos = make_cross_image(rows, cols, PITCH_PX, size=(1000, 1000))
    src = load_src(path)
    gray = src.original_gray()
    set_params(win, rows, cols)
    win.src = src
    win.grid = None
    set_pick_mode(win, "three")
    win._reset_pick()
    p0 = pos[(0, 0)]          # 起始点=左上（row0/col0）
    p1 = pos[(0, 1)]          # X 向点（右侧）
    p2 = pos[(1, 0)]          # Y 向点（下方：行号向下增）
    win._picked = [p0, p1, p2]
    try:
        win._finish_pick()
    except Exception:  # noqa: BLE001
        return check("T7 三点模式回归", False,
                     "异常: " + traceback.format_exc().strip().splitlines()[-1])
    g = win.grid
    if g is None:
        return check("T7 三点模式回归", False, "_finish_pick 后 grid 仍为 None")
    o_err = math.hypot(g.origin_px[0] - p0[0], g.origin_px[1] - p0[1])
    x_err = angle_err_deg(g.x_dir_px, (p1[0] - p0[0], p1[1] - p0[1]))
    y_err = angle_err_deg(g.y_dir_px, (p2[0] - p0[0], p2[1] - p0[1]))
    n_ok, _ = detect_all(gray, g.points_px(), g.pitch_px, params)
    return check("T7 三点模式回归", o_err <= 0.5 and x_err <= 0.1 and y_err <= 0.1 and n_ok == 9,
                 "origin_err=%.2fpx x_err=%.3f° y_err=%.3f° detected=%d/9 kind=%r"
                 % (o_err, x_err, y_err, n_ok, getattr(g, "origin_kind", None)))


def main():
    print("ROOT =", ROOT, "PITCH_MM=%.4f DPI=%.0f PITCH_PX=%.1f" % (PITCH_MM, DPI, PITCH_PX))
    stub_dialogs()
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        import gui.main_window as _mw
        _mw._write_log_file = lambda *a, **k: None
    except Exception:  # noqa: BLE001
        pass

    from core.grid_definition import GridDefinition
    from gui.main_window import MainWindow, CenterProbeThread, PICK_MODES  # noqa: F401
    rec("预检 CenterProbeThread", "PASS", "存在")
    rec("预检 GridDefinition.origin_kind",
        "PASS" if "origin_kind" in getattr(GridDefinition, "__dataclass_fields__", {}) else "BLOCKED",
        repr(getattr(GridDefinition(1, 1, PITCH_MM, DPI), "origin_kind", "缺失")))
    rec("预检 _make_grid_from_center",
        "PASS" if hasattr(MainWindow, "_make_grid_from_center") else "BLOCKED",
        "存在" if hasattr(MainWindow, "_make_grid_from_center") else "缺失")

    p = params_obj()
    win = make_win()
    for fn, name in ((lambda: t1_single_center(p), "T1"), (lambda: t2_rotated(p), "T2"),
                     (lambda: t3t4_center_path(win, p), "T3/T4"),
                     (lambda: t5_even_4x4(win, p), "T5"),
                     (lambda: t6_far_click(win, p), "T6"),
                     (lambda: t7_three_point(win, p), "T7")):
        try:
            fn()
        except Exception:  # noqa: BLE001
            rec(name, "FAIL", traceback.format_exc().strip().splitlines()[-1])

    print("\n===== 汇总 =====")
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_block = sum(1 for _, s, _ in RESULTS if s == "BLOCKED")
    for name, s, d in RESULTS:
        print("%-7s %s%s" % (s, name, ("  -> " + d) if d else ""))
    print("PASS=%d FAIL=%d BLOCKED=%d（共 %d 项）" % (n_pass, n_fail, n_block, len(RESULTS)))
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
