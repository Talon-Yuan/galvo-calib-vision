# -*- coding: utf-8 -*-
"""棋盘格角点匹配回归：旋转摆放不应配错点（v4.3.4）。"""
import os
import sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.calibrator import _match_to_ideal, build_ideal_points
from core.config import ChessboardParams
FAILS = []
def chk(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))
    if not ok: FAILS.append(name)
p = ChessboardParams(rows=19, cols=14, square_mm=10.0)
ideal = build_ideal_points(p)
rng = np.random.default_rng(42)
for deg in (0.0, 2.5, 4.8, -4.7, 10.0, -12.0, 30.0):
    th = np.radians(deg)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    det = ideal @ R.T + np.array([8000.0, 6000.0]) + rng.normal(0, 0.02, ideal.shape)
    perm = rng.permutation(len(det))
    matched = _match_to_ideal(ideal, det[perm])
    err = np.linalg.norm(matched - det, axis=1)
    chk(err.max() < 0.1, "旋转 %.1f° 匹配无错点" % deg, "max=%.4fmm" % err.max())
print()
print("RESULT =", "PASS" if not FAILS else "FAIL: " + "; ".join(FAILS))
sys.exit(0 if not FAILS else 1)
