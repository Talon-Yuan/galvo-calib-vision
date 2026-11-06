# -*- coding: utf-8 -*-
"""v4.2.1 回归：审计 P0/P1 的断言（合成，不依赖真实图）。

覆盖：
P0-1 看门狗：解码心跳(≤0.005)不得续期/压短冷启预算；真实进度(>0.02)才续期 20s
P0-2 超时路径：不得 terminate；应走"放弃等待 + finished 恢复"
P1-1 工程加载：origin_kind 往返保留；超护栏正交化被拒
P1-2 模板：D 列（中心归零/Y 下）→ 判定系（绝对/Y 上）换算
P1-4 名义值：固定在纸面轴（右/上），不随精修轴旋转
"""
import os
import sys
import math

os.environ.pop("QT_QPA_PLATFORM", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv[:1])

FAILS = []


def chk(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def main():
    from core.grid_definition import GridDefinition

    # ---- P1-1：origin_kind 往返 + 正交化加载护栏 ----
    g = GridDefinition(rows=3, cols=3, pitch_mm=5.0, dpi=4800,
                       origin_px=(100.0, 200.0), x_dir_px=(1.0, 0.0),
                       y_dir_px=(0.0, 1.0), origin_kind="corner")
    d = g.to_dict()
    g2 = GridDefinition.from_dict(d)
    chk(g2.origin_kind == "corner", "P1-1 to_dict/from_dict 保留 origin_kind", repr(g2.origin_kind))
    chk(g2.nominal_points_mm(False) == g.nominal_points_mm(False),
        "P1-1 往返后名义值一致")

    bad = dict(d)
    bad["orthogonalize"] = True
    bad["y_dir_px"] = (0.5, 0.8660)   # 夹角≈60° → 超 1° 护栏
    g3 = GridDefinition.from_dict(bad)
    chk(g3.orthogonalize is False and getattr(g3, "_ortho_rejected_on_load", False),
        "P1-1 超护栏正交化在加载时被拒绝")

    # ---- P1-4：名义值与轴旋转无关 ----
    base = GridDefinition(rows=3, cols=3, pitch_mm=5.0, dpi=4800,
                          origin_px=(0.0, 0.0), x_dir_px=(1.0, 0.0),
                          y_dir_px=(0.0, 1.0))
    rot = GridDefinition(rows=3, cols=3, pitch_mm=5.0, dpi=4800,
                         origin_px=(0.0, 0.0), x_dir_px=(0.8660, 0.5),
                         y_dir_px=(-0.5, 0.8660))
    same = base.nominal_points_mm(False) == rot.nominal_points_mm(False)
    chk(same, "P1-4 名义值不随精修轴旋转（旋转误差会暴露）")

    # ---- P1-2：模板换算（中心归零/Y 下 → 原点绝对/Y 上） ----
    import gui.main_window as MW
    win = MW.MainWindow()
    win.grid = GridDefinition(rows=3, cols=3, pitch_mm=5.0, dpi=4800,
                              origin_px=(0.0, 0.0), x_dir_px=(1.0, 0.0),
                              y_dir_px=(0.0, 1.0), origin_kind="center")
    win.results = [None] * 9
    tmpl = [(0.0, 0.0)] + [((c - 1) * 5.0, (r - 1) * 5.0) for r in range(3) for c in range(3)]
    win.template_nominal_points = tmpl
    nm = win._nominal_mm_list()
    chk(abs(nm[0][0] + 5.0) < 1e-9 and abs(nm[0][1] - 5.0) < 1e-9,
        "P1-2 模板左上点 → (-5, +5)（Y 已翻转）", str(nm[0]))
    win.template_nominal_points = None

    # ---- P0-1：心跳不续期，真实进度才续期 ----
    calls = []

    class Guard:
        def start(self, ms):
            calls.append(ms)

    win._detect_guard = Guard()
    win._on_detect_progress(0.005)
    chk(calls == [], "P0-1 解码心跳(0.005)不续期", str(calls))
    win._on_detect_progress(0.5)
    chk(calls == [20000], "P0-1 真实进度续期 20s", str(calls))

    # ---- P0-2：超时路径不得 terminate ----
    src = open(os.path.join(ROOT, "gui", "main_window.py"), encoding="utf-8").read()
    seg = src[src.find("def _on_detect_timeout("): src.find("def _on_detect_done(")]
    chk(".terminate(" not in seg, "P0-2 超时路径不再 terminate（避免持锁死锁）")
    chk("_on_detect_timeout_ui" in seg, "P0-2 超时走放弃等待 UI")

    print()
    print("RESULT =", "PASS" if not FAILS else "FAIL: " + "; ".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
