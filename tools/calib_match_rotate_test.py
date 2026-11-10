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


# ---- v4.3.5：质量门回归（失败必须返回 None，不允许"唯一但错"静默通过）----
def _rot(deg, pts, noise=0.02, seed=7):
    rng = np.random.default_rng(seed)
    th = np.radians(deg)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    return pts @ R.T + np.array([8000.0, 6000.0]) + rng.normal(0, noise, pts.shape)

base_det = _rot(0.0, ideal)
for deg in (40.0, 44.0, 45.0, 90.0):
    det_r = _rot(deg, ideal)
    m = _match_to_ideal(ideal, det_r)
    if deg <= 44.0:
        ok = m is not None and np.linalg.norm(m - det_r, axis=1).max() < 0.1
        chk(ok, "质量门：%.1f° 正确匹配" % deg, "None" if m is None else "max=%.4f" % np.linalg.norm(m - det_r, axis=1).max())
    else:
        chk(m is None, "质量门：%.1f° 拒绝(返回None)" % deg, "not None" if m is not None else "")
# 单角离群 2.5x 间距
bad = base_det.copy(); bad[17] += np.array([2.5 * 100.0, 0.0])
chk(_match_to_ideal(ideal, bad) is None, "质量门：单角离群 2.5×间距 拒绝")
# 点数不符
chk(_match_to_ideal(ideal, base_det[:-1]) is None, "质量门：点数不符 拒绝")
# 重复点
dup = base_det.copy(); dup[1] = dup[0]
chk(_match_to_ideal(ideal, dup) is None, "质量门：重复点 拒绝")

print()
print("RESULT =", "PASS" if not FAILS else "FAIL: " + "; ".join(FAILS))
sys.exit(0 if not FAILS else 1)
