# -*- coding: utf-8 -*-
"""识别过程可视化渲染（v4.3.7）

把每个检测点的"寻边点 / 四臂中线 / 交点"用不同颜色画到 ROI 放大图上：
  - 红色：参与拟合的臂像素（寻边点）
  - 绿色：四臂中线（拟合直线）
  - 蓝色：交点（最终中心）
  - 黄色小点：像素质心（用于对比"交点 vs 质心"是否偏移）

同时输出 CSV/日志所需的逐点记录。用于验证"寻边中线交点法"是否真的在运行、
以及中心点是否被质心法带偏。
"""
from __future__ import annotations

import os

import cv2
import numpy as np


def _bgr(gray: np.ndarray) -> np.ndarray:
    if gray.ndim == 2:
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return gray.copy()


def _pt(m, x, y):
    return (int(round(x - m[0])), int(round(y - m[1])))


def render_result_debug(gray: np.ndarray, r, index: int, out_path: str,
                        label: str = "", note: str = "") -> dict:
    """渲染单点识别过程图，返回该点记录 dict（供 CSV/日志）。失败时仍写图并标注原因。"""
    h, w = gray.shape[:2]
    dbg = getattr(r, "debug", None) if r is not None else None
    rec = {
        "index": int(index), "label": label,
        "ok": bool(r is not None and getattr(r, "ok", False)),
        "method": (getattr(r, "method", "") if r is not None else ""),
        "conf": (float(getattr(r, "conf", 0.0)) if r is not None else 0.0),
        "x": (float(r.x) if r is not None else float("nan")),
        "y": (float(r.y) if r is not None else float("nan")),
        "fallback_mean": (bool(dbg.get("fallback_mean")) if isinstance(dbg, dict) else False)
                          or ("centroid_fallback" in str(getattr(r, "method", ""))),
        "fit_used": bool(dbg.get("fit_used")) if isinstance(dbg, dict) else False,
        "centroid_dx": float("nan"), "centroid_dy": float("nan"),
        "fit_vs_mean_px": float("nan"), "n_arm": 0, "note": note,
    }
    cx = rec["x"] if np.isfinite(rec["x"]) else (w / 2.0)
    cy = rec["y"] if np.isfinite(rec["y"]) else (h / 2.0)
    roi = None
    if isinstance(dbg, dict):
        r_ = dbg.get("roi")
        if r_ is not None:
            roi = (float(r_[0]), float(r_[1]), float(r_[2]), float(r_[3]))
    if roi is None:
        half = 260
        roi = (max(0.0, cx - half), max(0.0, cy - half),
               min(float(w - 1), cx + half), min(float(h - 1), cy + half))
    pad = 24
    x0 = max(0, int(roi[0]) - pad); y0 = max(0, int(roi[1]) - pad)
    x1 = min(w, int(roi[2]) + pad); y1 = min(h, int(roi[3]) + pad)
    if x1 - x0 < 20 or y1 - y0 < 20:
        cv2.imwrite(out_path, np.zeros((32, 32, 3), np.uint8)) if False else None
        return rec
    crop = _bgr(gray[y0:y1, x0:x1])
    scale = min(4.0, 720.0 / max(1, max(crop.shape[:2])))
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    m = (x0, y0)  # 全局 -> 图内 偏移

    def to_img(x, y):
        return (int(round((x - m[0]) * scale)), int(round((y - m[1]) * scale)))

    if isinstance(dbg, dict):
        _fallback = bool(dbg.get("fallback_center")) or ("centroid_fallback" in str(rec.get("method", "")))
        ap = {} if _fallback else (dbg.get("edge_points") or dbg.get("arm_pixels") or {})
        total = 0
        for k, arr in ap.items():
            if arr is None or len(arr) == 0:
                continue
            total += len(arr)
            for px_, py_ in np.asarray(arr, float):
                cv2.circle(crop, to_img(px_, py_), 1, (0, 0, 255), -1)  # 红：寻边点
        rec["n_edge"] = total
        # 四臂中线（拟合在 ROI 局部坐标，全局 = ROI 起点 + 局部）
        fits = dbg.get("fits") or {}
        r_roi = dbg.get("roi")
        if isinstance(fits, dict) and r_roi is not None:
            gx0, gy0 = float(r_roi[0]), float(r_roi[1])
            ww = float(r_roi[2]) - gx0; hh = float(r_roi[3]) - gy0
            def line_h(a, b):
                pts = []
                for u in (0.0, ww):
                    pts.append(to_img(gx0 + u, gy0 + a * u + b))
                cv2.line(crop, pts[0], pts[1], (0, 200, 0), 1, cv2.LINE_AA)  # 绿：中线
            def line_v(a, b):
                pts = []
                for v in (0.0, hh):
                    pts.append(to_img(gx0 + a * v + b, gy0 + v))
                cv2.line(crop, pts[0], pts[1], (0, 200, 0), 1, cv2.LINE_AA)
            try:
                if _fallback:
                    raise StopIteration
                def _dash(p1, p2, color, dash=18, gap=12):
                    x1, y1 = p1; x2, y2 = p2
                    L = float(np.hypot(x2 - x1, y2 - y1))
                    if L < 1: return
                    n = int(L // (dash + gap)) + 1
                    for k in range(n):
                        t0 = (k * (dash + gap)) / L; t1 = min(1.0, (k * (dash + gap) + dash) / L)
                        cv2.line(crop, (int(x1 + (x2 - x1) * t0), int(y1 + (y2 - y1) * t0)),
                                 (int(x1 + (x2 - x1) * t1), int(y1 + (y2 - y1) * t1)), color, 1, cv2.LINE_AA)
                # 绿实线 = 四条边缘线（贴着十字边缘）；绿虚线 = 两条中线（边缘成对平均）
                if fits.get("edge_top") is not None:
                    a, b = fits["edge_top"]
                    cv2.line(crop, to_img(gx0, gy0 + a * 0 + b), to_img(gx0 + ww, gy0 + a * ww + b), (0, 220, 0), 1, cv2.LINE_AA)
                if fits.get("edge_bottom") is not None:
                    a, b = fits["edge_bottom"]
                    cv2.line(crop, to_img(gx0, gy0 + a * 0 + b), to_img(gx0 + ww, gy0 + a * ww + b), (0, 220, 0), 1, cv2.LINE_AA)
                if fits.get("edge_left") is not None:
                    a, b = fits["edge_left"]
                    cv2.line(crop, to_img(gx0 + a * 0 + b, gy0), to_img(gx0 + a * hh + b, gy0 + hh), (0, 220, 0), 1, cv2.LINE_AA)
                if fits.get("edge_right") is not None:
                    a, b = fits["edge_right"]
                    cv2.line(crop, to_img(gx0 + a * 0 + b, gy0), to_img(gx0 + a * hh + b, gy0 + hh), (0, 220, 0), 1, cv2.LINE_AA)
                if fits.get("h") is not None:
                    a, b = fits["h"]
                    _dash(to_img(gx0, gy0 + b), to_img(gx0 + ww, gy0 + a * ww + b), (0, 255, 0), dash=14, gap=10)
                if fits.get("v") is not None:
                    a, b = fits["v"]
                    _dash(to_img(gx0 + b, gy0), to_img(gx0 + a * hh + b, gy0 + hh), (0, 255, 0), dash=14, gap=10)
                if fits.get("h") is None and fits.get("v") is None:
                    if fits.get("l") is not None: line_h(*fits["l"])
                    if fits.get("r") is not None: line_h(*fits["r"])
                    if fits.get("t") is not None: line_v(*fits["t"])
                    if fits.get("b") is not None: line_v(*fits["b"])
            except StopIteration:
                pass
            except Exception:
                pass
        # 质心（黄）与交点（蓝）
        if dbg.get("centroid") is not None:
            ccx, ccy = dbg["centroid"]
            cv2.circle(crop, to_img(ccx, ccy), 3, (0, 255, 255), -1)
            if np.isfinite(rec["x"]) and np.isfinite(rec["y"]):
                rec["centroid_dx"] = float(rec["x"] - ccx)
                rec["centroid_dy"] = float(rec["y"] - ccy)
        if dbg.get("mid") is not None and dbg.get("mean_center") is not None:
            mx, my = dbg["mid"]; nx, ny = dbg["mean_center"]
            rec["fit_vs_mean_px"] = float(np.hypot(mx - nx, my - ny))
    if np.isfinite(rec["x"]) and np.isfinite(rec["y"]):
        p = to_img(rec["x"], rec["y"])
        cv2.drawMarker(crop, p, (255, 0, 0), cv2.MARKER_CROSS, 22, 2)  # 蓝：交点
        cv2.circle(crop, p, 8, (255, 0, 0), 1)
    txt = "%s #%d %s conf=%.2f" % (rec["label"] or "P", index, rec["method"], rec["conf"])
    if rec["fallback_mean"]:
        txt += " [mean-fallback]"
    cv2.putText(crop, txt, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 3, cv2.LINE_AA)
    cv2.putText(crop, txt, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    try:
        ok, buf = cv2.imencode(".png", crop)
        if ok:
            with open(out_path, "wb") as f:
                f.write(buf.tobytes())
            rec["image"] = os.path.basename(out_path)
    except Exception as e:  # noqa: BLE001
        rec["note"] = (rec["note"] + " | render_fail:%r" % e)[:200]
    return rec


def export_debug_process(gray: np.ndarray, results: list, out_dir: str,
                         labels: list | None = None, prefix: str = "p") -> dict:
    """导出全部点的识别过程图 + CSV。

    返回 {"dir":..., "n":..., "csv": path, "rows":[rec...]}
    """
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    labels = labels or []
    n = 0
    for i, r in enumerate(results):
        lab = labels[i] if i < len(labels) else ""
        p = os.path.join(out_dir, "%s%d.png" % (prefix, i + 1))
        rec = render_result_debug(gray, r, i + 1, p, label=lab)
        rows.append(rec)
        n += 1
    csv_path = os.path.join(out_dir, "识别过程记录.csv")
    cols = ["index", "label", "ok", "method", "conf", "x", "y", "fallback_mean",
            "fit_used", "centroid_dx", "centroid_dy", "fit_vs_mean_px", "n_edge", "note"]
    try:
        with open(csv_path, "w", encoding="utf-8-sig") as f:
            f.write(",".join(cols) + "\n")
            for rec in rows:
                vals = []
                for c in cols:
                    v = rec.get(c, "")
                    if isinstance(v, float):
                        vals.append("%.4f" % v if np.isfinite(v) else "")
                    else:
                        vals.append(str(v))
                f.write(",".join(vals) + "\n")
    except Exception:
        csv_path = ""
    return {"dir": out_dir, "n": n, "csv": csv_path, "rows": rows}
