# -*- coding: utf-8 -*-
"""B1 摆正模块（deskew）。

从原始灰度图估计整幅十字阵列的旋转角，并把摆正角度应用到坐标变换，
不物理旋转图像。红线 5：检测链仍在原始灰度系跑，摆正角度只进换算层
（gui/main_window.py 的 `_to_mm_base` 入口），避免破坏灰度系坐标铁律。

已拍板砍掉路线 A（cv2.calibrateCamera 全图畸变校正）：平板扫描仪是
线扫成像，无桶形/枕形畸变，一次性相机标定不可复用（专利 CN1216342C
教训），非线性残差统一交给 B9 同幅标定吸收。
"""
from typing import Optional, Tuple

import numpy as np

from .calibrator import _estimate_rotation_deg


def estimate_skew_angle(gray: np.ndarray) -> Optional[float]:
    """估计整幅图像的摆正角（度），已归一到 [-45°, 45°)。

    角度来源复用 calibrator._estimate_rotation_deg（Hough 直线方向投票，
    十字阵列专用估角，贴合黑纸样本）。返回 None 表示无法可靠估角。
    """
    ang = _estimate_rotation_deg(gray)
    if ang is None:
        return None
    return float(ang)


def _rotation_matrix(ang_deg: float) -> np.ndarray:
    """绕原点逆时针旋转 ang_deg 的 2×2 旋转矩阵。"""
    th = np.deg2rad(ang_deg)
    c, s = np.cos(th), np.sin(th)
    return np.array([[c, -s], [s, c]], np.float64)


def _center(shape: Tuple[int, int]) -> np.ndarray:
    h, w = shape[:2]
    return np.array([w / 2.0, h / 2.0], np.float64)


def deskew_point(px_x: float, px_y: float, ang_deg: float,
                 shape: Tuple[int, int]) -> Tuple[float, float]:
    """原始灰度系一点 → 回正系（绕图中心旋转 -ang_deg 摆正）。

    shape = (h, w)。与 calibrator._rotate_back 同向（原图 → 回正）。
    """
    R = _rotation_matrix(-ang_deg)
    c = _center(shape)
    p = np.array([px_x, px_y], np.float64)
    q = (p - c) @ R.T + c
    return float(q[0]), float(q[1])


def deskew_points(pts: np.ndarray, ang_deg: float,
                  shape: Tuple[int, int]) -> np.ndarray:
    """批量摆正：pts 为 N×2 像素坐标数组，返回回正系 N×2 数组。"""
    R = _rotation_matrix(-ang_deg)
    c = _center(shape)
    return (pts - c) @ R.T + c


def restore_point(px_x: float, px_y: float, ang_deg: float,
                  shape: Tuple[int, int]) -> Tuple[float, float]:
    """回正系一点 → 原始灰度系（绕图中心旋转 +ang_deg 反向）。"""
    R = _rotation_matrix(ang_deg)
    c = _center(shape)
    p = np.array([px_x, px_y], np.float64)
    q = (p - c) @ R.T + c
    return float(q[0]), float(q[1])


def restore_points(pts: np.ndarray, ang_deg: float,
                   shape: Tuple[int, int]) -> np.ndarray:
    """批量反向：pts 为 N×2 回正系坐标数组，返回原图系 N×2 数组。"""
    R = _rotation_matrix(ang_deg)
    c = _center(shape)
    return (pts - c) @ R.T + c
