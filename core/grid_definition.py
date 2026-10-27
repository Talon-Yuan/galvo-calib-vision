# -*- coding: utf-8 -*-
"""阵列网格定义：依据起始点、方向、行列数、间距生成搜索网格。
方向用预览图上的两个参考点定义：X 方向点与 Y 方向点（可为 None 时自动取垂直方向）。"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import math

import numpy as np


@dataclass
class GridDefinition:
    rows: int = 5
    cols: int = 5
    pitch_mm: float = 20.0
    dpi: float = 4800.0
    origin_px: Tuple[float, float] = (0.0, 0.0)
    x_dir_px: Tuple[float, float] = (1.0, 0.0)
    y_dir_px: Tuple[float, float] = (0.0, 1.0)
    # 实测单格间距(mm)：建网格/理想位置用（自动建系回填）；None 时回退 pitch_mm。
    # 红线：名义表 nominal_points_mm() 恒用 pitch_mm（设计标称），禁止用实测反推名义。
    measured_pitch_mm: Optional[float] = None
    # 建系正交化(B1)：把用户点选得到的两个方向轴对称收拢为正交基（角平分线不变）。
    # 背景与依据：
    #   * _finish_pick 里 x=点1-点0、y=点2-点0，无任何正交约束，两轴夹角 gamma
    #     完全由点击误差决定；
    #   * 精化只做"两轴同角度旋转"，gamma 是旋转不变量 => 全程被冻结、无法纠正；
    #   * 两份真机报表实测 gamma = 89.7061° / 89.6563°（差 0.0498°，仅换点选）；
    #   * 离线归因实验：自由夹角的最优值恰为 90.00°，即该非正交量属手点噪声，
    #     而非设备/样件的方形度误差。
    # 该开关只把"点选歪基"收成"规范正交基"，不引入任何拟合自由度，也不改动
    # 名义值 / pitch_mm / 网格原点 / 校准 npz。
    orthogonalize: bool = False

    def __post_init__(self):
        self._normalize_dirs()

    def _normalize_dirs(self):
        x = np.asarray(self.x_dir_px, np.float64)
        nx = np.linalg.norm(x)
        if nx < 1e-9:
            x = np.array([1.0, 0.0])
        else:
            x = x / nx
        y = np.asarray(self.y_dir_px, np.float64)
        ny = np.linalg.norm(y)
        if ny < 1e-9:
            y = np.array([-x[1], x[0]])
        else:
            y = y / ny
        self._ortho_correction_deg = 0.0   # 本次正交化收拢角(°)；未启用/本就正交时为 0
        if self.orthogonalize:
            x, y, delta = self.orthonormalize(x, y)
            self._ortho_correction_deg = delta
        self.x_dir_px = tuple(x)
        self.y_dir_px = tuple(y)

    @staticmethod
    def orthonormalize(x, y):
        """把两方向轴对称收拢为正交，返回 (x', y', 收拢角°)。

        对称(sym)做法：保持两轴的角平分线（平均朝向）不变，各向 90° 收拢一半，
        因此不翻转、不交换两轴，是改动最小的正交化。
        收拢角 = |gamma| - 90（负值表示原夹角小于 90°）。
        """
        x = np.asarray(x, np.float64)
        y = np.asarray(y, np.float64)
        nx = float(np.linalg.norm(x))
        ny = float(np.linalg.norm(y))
        # 零向量保护（监督 #3 提出）：当前调用方已保证非零，但作为 public static 需自守
        if nx < 1e-9 or ny < 1e-9:
            return x, y, 0.0
        x = x / nx
        y = y / ny
        ax = math.degrees(math.atan2(x[1], x[0]))
        ay = math.degrees(math.atan2(y[1], y[0]))
        d = ay - ax
        while d > 180.0:
            d -= 360.0
        while d <= -180.0:
            d += 360.0
        s = 1.0 if d >= 0 else -1.0
        delta = abs(d) - 90.0
        if abs(delta) < 1e-12:
            return x, y, 0.0
        half = delta / 2.0 * s
        ax2 = ax + half
        ay2 = ay - half
        x2 = np.array([math.cos(math.radians(ax2)), math.sin(math.radians(ax2))])
        y2 = np.array([math.cos(math.radians(ay2)), math.sin(math.radians(ay2))])
        return x2, y2, float(delta)

    @property
    def axis_angles_deg(self) -> Tuple[float, float]:
        """当前 X/Y 方向轴的角度(°)，用于诊断与报表留痕。"""
        x = np.asarray(self.x_dir_px, np.float64)
        y = np.asarray(self.y_dir_px, np.float64)
        return (math.degrees(math.atan2(x[1], x[0])),
                math.degrees(math.atan2(y[1], y[0])))

    @property
    def axis_gamma_deg(self) -> float:
        """两轴夹角 gamma(°)，恒为正（0~180）。"""
        ax, ay = self.axis_angles_deg
        d = (ay - ax) % 360.0
        if d > 180.0:
            d = 360.0 - d
        return d

    @property
    def ortho_correction_deg(self) -> float:
        """本对象正交化时收拢的角度(°)，0 表示未启用或本就正交。"""
        return float(getattr(self, "_ortho_correction_deg", 0.0))

    @property
    def nominal_pitch_mm(self) -> float:
        """设计标称间距(mm)：名义值表专用，禁止反推。"""
        return self.pitch_mm

    @property
    def grid_pitch_mm(self) -> float:
        """建网格用间距(mm)：实测间距优先，未回填时回退设计标称。"""
        return self.measured_pitch_mm if self.measured_pitch_mm and self.measured_pitch_mm > 0 else self.pitch_mm

    @property
    def pitch_px(self) -> float:
        """网格理想位置间距(px)：基于建网格间距（实测优先）。"""
        return self.grid_pitch_mm * self.dpi / 25.4

    def points_px(self) -> np.ndarray:
        """按行优先生成 (rows*cols, 2) 像素坐标（间距=实测优先，见 grid_pitch_mm）。"""
        ox, oy = self.origin_px
        xv = np.asarray(self.x_dir_px, np.float64) * self.pitch_px
        yv = np.asarray(self.y_dir_px, np.float64) * self.pitch_px
        pts = []
        for r in range(self.rows):
            for c in range(self.cols):
                pts.append([ox + c * xv[0] + r * yv[0], oy + c * xv[1] + r * yv[1]])
        return np.array(pts, np.float64)

    def nominal_points_mm(self, include_p1: bool = True) -> List[Tuple[float, float]]:
        """以阵列几何中心为原点的固定名义设计坐标（mm），行主序。

        公式：名义坐标 = (距中心列差 × pitch_mm, 距中心行差 × pitch_mm)。
        名义值只与行列空间位置有关（列序号增 → X 增、行序号增 → Y 增），
        是固定的设计值，符号与点选方向、图像轴方向均无关：
        例如 5×5、间距 25mm 幅面 → 行主序首点（row0,col0）= (-50,-50)，
        前两行（点1~点10）Y = -50 / -25 均为负，与用户设计认知一致。
        实测坐标必须先中心归零、再按点选方向投影（见 to_phys），
        才能与名义值在同一坐标系比较，避免反向点选时出现两倍间距级假 NG。
        与用户点选起始点及检测结果无关，生成后即为固定值：
        - include_p1=True（默认）：首项为中心基准点 P1=(0,0)（P2 缺失），
          其余项 P3..P(3+rows*cols-1) 按行主序对应网格点；
        - include_p1=False：仅返回网格点行主序名义值（与 points_px 顺序一致），
          例如 5×5 幅面 120mm + 30mm 间隔 → 首点 P3=(-60,-60)、中心点=(0,0)。
        """
        c0 = (self.cols - 1) / 2.0
        r0 = (self.rows - 1) / 2.0
        pts: List[Tuple[float, float]] = []
        if include_p1:
            pts.append((0.0, 0.0))
        for r in range(self.rows):
            for c in range(self.cols):
                # 名义值恒用设计标称 pitch_mm（与建网格实测间距 measured_pitch_mm 无关，禁反推）
                pts.append(((c - c0) * self.pitch_mm,
                            (r - r0) * self.pitch_mm))
        return pts

    def rotate_dirs(self, deg_ccw: float) -> None:
        """绕图像系原点把 X/Y 方向轴整体逆时针旋转 deg_ccw 度（检测后刚体建系精化用）。

        只旋转方向（长度归一保持单位向量），不动 origin_px / pitch_mm / 名义值。
        方案 A：用成功检测点 SVD 刚体拟合出阵列真实方向角 θ 后调用，精化
        to_phys 投影轴，消除点选方向轴与阵列真实方向的小角度偏差（~0.1° 级
        旋转残差），不触碰名义值模板与校准 npz。
        """
        a = np.radians(deg_ccw)
        c, s = np.cos(a), np.sin(a)
        R = np.array([[c, -s], [s, c]], np.float64)
        x = np.asarray(self.x_dir_px, np.float64)
        y = np.asarray(self.y_dir_px, np.float64)
        nx = np.linalg.norm(x)
        ny = np.linalg.norm(y)
        xr = x @ R.T
        yr = y @ R.T
        self.x_dir_px = tuple(xr / (nx if nx > 1e-9 else 1.0))
        self.y_dir_px = tuple(yr / (ny if ny > 1e-9 else 1.0))

    def to_phys(self, vx: float, vy: float) -> Tuple[float, float]:
        """把图像轴上的 mm 向量分解到用户点选的 X/Y 物理方向轴，返回 (a, b)。

        名义值为固定行列设计坐标（见 nominal_points_mm），实测坐标必须先
        中心归零、再投影到点选方向，二者才处于同一坐标系：
            Δmm ≈ a·xu + b·yu   （xu/yu 为点选 X/Y 方向的单位向量）
        采用双线性分解，即使方向点未精确垂直也能正确反解行列分量。
        默认方向 x=(1,0), y=(0,1) 时投影恒等，行为与旧版完全一致；
        反向点选（如 Y 向点从下往上点，yu=(0,-1)）时行0 实测 Y=+50
        投影后 b=-50，与名义 -50 一致，消除假 NG。
        """
        xv = np.asarray(self.x_dir_px, np.float64)
        yv = np.asarray(self.y_dir_px, np.float64)
        nx = np.linalg.norm(xv)
        ny = np.linalg.norm(yv)
        if nx < 1e-9 or ny < 1e-9:
            return float(vx), float(vy)
        xu = xv / nx
        yu = yv / ny
        v = np.array([vx, vy], np.float64)
        cos = float(xu @ yu)
        det = 1.0 - cos * cos
        if abs(det) < 1e-6:  # 两方向平行/共线：无法构成平面基，退回原值
            return float(vx), float(vy)
        a = ((v @ xu) - cos * (v @ yu)) / det
        b = ((v @ yu) - cos * (v @ xu)) / det
        return float(a), float(b)

    def to_dict(self) -> dict:
        return {
            "rows": self.rows, "cols": self.cols, "pitch_mm": self.pitch_mm,
            "dpi": self.dpi, "origin_px": list(self.origin_px),
            "x_dir_px": list(self.x_dir_px), "y_dir_px": list(self.y_dir_px),
            "measured_pitch_mm": self.measured_pitch_mm,
            "orthogonalize": bool(self.orthogonalize),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GridDefinition":
        return cls(rows=d.get("rows", 5), cols=d.get("cols", 5),
                   pitch_mm=d.get("pitch_mm", 20.0), dpi=d.get("dpi", 4800.0),
                   origin_px=tuple(d.get("origin_px", (0.0, 0.0))),
                   x_dir_px=tuple(d.get("x_dir_px", (1.0, 0.0))),
                   y_dir_px=tuple(d.get("y_dir_px", (0.0, 1.0))),
                   measured_pitch_mm=d.get("measured_pitch_mm"),
                   orthogonalize=bool(d.get("orthogonalize", False)))


def estimate_pitch_px(pts: np.ndarray) -> float:
    """从候选点集合估算平均间距（用于自动间距）。"""
    if len(pts) < 2:
        return 0.0
    p = np.asarray(pts, np.float64)
    d = np.linalg.norm(p[1:] - p[:1], axis=1)
    return float(np.median(d[d > 0])) if len(d) > 0 else 0.0
