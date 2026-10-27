# -*- coding: utf-8 -*-
"""core 数值 A/B：给定源码根，跑固定输入，输出可逐项对比的 JSON 指纹。
用法：python tools/core_ab.py <源码根>
目的：修改 core 守卫后，证明"合法输入下数值结果逐项一致"（红线 9）。
"""
import hashlib
import json
import os
import sys
import tempfile

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402


def h(a) -> str:
    return hashlib.sha256(np.asarray(a, np.float64).tobytes()).hexdigest()[:16]


out = {"root": os.path.basename(ROOT)}

# 1) 校正场多项式拟合 + 残差（确定性输入）
n = 9 * 12
px = np.stack([np.linspace(100, 3000, n), np.linspace(80, 2400, n)], axis=1)
noise = (np.arange(n) % 7 - 3) * 1e-3
mm = px * 0.0211 + np.stack([noise, -noise], axis=1)
from core.calibrator import (  # noqa: E402
    compute_residuals, fit_correction_field, fit_residual_affine, _poly_design)
cx, cy = fit_correction_field(px, mm, order=2)
rms, mx, res = compute_residuals(px, mm, cx, cy, 2)
aff = fit_residual_affine(px, res)
out["poly"] = {"coeff_x": h(cx), "coeff_y": h(cy), "rms": round(float(rms), 12),
               "max": round(float(mx), 12), "res": h(res)}
out["affine"] = {k: (round(float(v), 12) if isinstance(v, (int, float)) else str(v))
                 for k, v in aff.items()}
out["design"] = h(_poly_design(px, 3))

# 2) 多摆放刚体配准
from core.multi_pose_fit import rigid_align  # noqa: E402
src = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [5, 5]], float)
dst = np.array([[1, 2], [10.9, 2.1], [10.8, 11.9], [0.1, 11.8], [5.4, 6.9]], float)
R, t, aligned = rigid_align(src, dst)
out["rigid"] = {"R": h(R), "t": h(t), "aligned": h(aligned)}

# 3) 图源 ROI / 灰度 / 预览比例
from core.image_loader import ImageSource  # noqa: E402
import cv2  # noqa: E402
tmp = os.path.join(tempfile.gettempdir(), "_core_ab_src.png")
img = np.zeros((300, 400), np.uint8)
img[60:150, 120:300] = 255
img[100:110, :] = 200
cv2.imwrite(tmp, img)
isz = ImageSource(tmp, use_full=True)
g = isz.to_gray()
roi = isz.roi(50, 60, 120, 90)
out["image"] = {"scale": round(float(isz.scale), 12), "gray": h(g), "roi": h(roi),
                "shape": list(roi.shape), "preview_scale": round(float(isz.preview_scale), 12)}

# 4) 刚体+剪切分解
from core.coordinate_frame import fit_rigid_shear  # noqa: E402
meas = np.stack([np.linspace(0, 100, 25), np.linspace(0, 80, 25)], axis=1)
nom = meas @ np.array([[1.0, 0.001], [-0.0005, 0.999]]) + np.array([0.3, -0.2])
rs = fit_rigid_shear(meas, nom)
out["shear"] = {k: (round(float(v), 12) if isinstance(v, (int, float)) else str(v))
                for k, v in vars(rs).items() if not k.startswith("_")}

try:
    os.remove(tmp)
except OSError:
    pass

out["ok"] = True
print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=1))
