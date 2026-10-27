# -*- coding: utf-8 -*-
"""全局配置：视觉检测参数与棋盘格校准参数集中管理。"""
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class CrossDetectParams:
    """十字检测参数（满足"可定义视觉数值多"）"""
    threshold_mode: str = "otsu"          # otsu / adaptive / fixed
    polarity: str = "auto"                # auto / dark_on_light(白底黑线) / light_on_dark(黑底白线)
    fixed_threshold: int = 128
    threshold_blocksize: int = 51
    threshold_c: float = 10.0
    morph_kernel: int = 3                 # 形态学闭运算核尺寸(px)
    min_area: int = 0                     # 0=自动
    min_area_ratio: float = 0.001         # 自动最小面积：ROI 像素数 * 该比例（真实扫描图细十字约0.3%）
    fill_range: Tuple[float, float] = (0.01, 0.5)  # 十字填充率（前景面积/外接矩形）范围，滤除实心块/长条符号
    max_area: int = 0                     # 0=自动
    aspect_range: Tuple[float, float] = (0.75, 1.35)  # 2026-10-14 真实测试图：部分十字两臂略不对称，bbox 比例 0.799/1.210 被误拒，放宽并靠 fill/正交/方向捕获兜底
    search_window_scale: float = 1.2      # 网格ROI相对间距的放大比例
    subpixel_refine: bool = True
    refine_win: int = 11
    refine_max_delta: float = 1.5   # cornerSubPix 相对投影质心的最大允许偏移(px)，超限回退投影
    method: str = "edge_center"           # projection / edge_center(寻边中线交点) / template；4800dpi真实图实测edge_center精度最优
    # ---- 投影质心改进：对称窗口质心 + 迭代收敛 + 可选高斯拟合 ----
    sym_win_scale: float = 2.5            # 对称窗口半宽 = 线宽 × 该系数（窗口对称⇒长臂不再拉偏质心）
    sym_iterations: int = 2               # 对称窗口迭代次数（直到中心位移<0.1px 提前收敛）
    use_gaussian_fit: bool = False        # 是否启用可选高斯拟合增强（对亮度不均/线宽突变更稳）
    gauss_scan_gap: float = 2.0           # 高斯拟合扫描线间隔系数（间隔 = 线宽 × 系数）
    # ---- 寻边中线交点改进：十字形校验 + 四臂直线拟合 + 方向约束 ----
    cross_check: bool = True              # 十字形校验开关（臂数=4、正交、臂长比、方向约束）
    arm_ortho_tol_deg: float = 10.0       # 两两正交容差（90°±tol）
    arm_ratio_max: float = 3.0            # 最长臂/最短臂比上限
    arm_angle_tol_deg: float = 15.0       # 臂方向约束容差（上/下≈90°±tol，左/右≈0°±tol）
    dir_capture_min: float = 0.4          # 方向捕获率下限：旋转系锥形分类保留像素/总像素 ≥ 该值；偏斜目标臂像素被裁剪只剩交叉区，捕获率低
    fit_vs_mean_max_delta_px: float = 3.0  # 直线拟合与像素均值中心差阈值(px)：超限说明拟合被污染，回退均值法
    # ---- 方案D：检测置信度评分（2026-10-14，低置信显式标 DETECT_FAIL）----
    conf_low: float = 0.50        # 置信度阈值：低于该值标 DETECT_FAIL（LOW_CONF），不再静默放行 OK
    conf_w_sym: float = 0.30      # 臂对称性权重
    conf_w_ortho: float = 0.25    # 正交度权重
    conf_w_contrast: float = 0.30 # 对比度权重（弱十字/残影降分）
    conf_w_center: float = 0.15   # 中心一致性权重


@dataclass
class ChessboardParams:
    """棋盘格参数（全部可定制）"""
    rows: int = 9                         # 内角点行数
    cols: int = 12                        # 内角点列数
    square_mm: float = 10.0               # 单格标称边长(mm)
    gap_mm: float = 0.0                   # 格间间隔(mm)
    measured_square_mm: Optional[float] = None   # 实测单格边长(mm)
    measured_w_mm: Optional[float] = None         # 实测整板宽(mm)
    measured_h_mm: Optional[float] = None         # 实测整板高(mm)
    corner_method: str = "sb"             # sb / classic
    subpixel: bool = True
    order: int = 2                        # 校正场多项式阶数(2/3)
    residual_limit_mm: float = 0.15       # 残差硬性门槛
    dilation: float = 1.0                 # 膨胀系数(0.9~1.1)：打标/打印棋盘格实际尺寸与标称的比值，>1=图形膨胀，<1=收缩；棋盘格损坏紧急标定时用于整体修正理想点步长
    # ---- 分区校正场（阶段三，默认启用：真实 4800dpi 棋盘 266 点 CV-RMS 0.0667→0.0447mm）----
    use_piecewise: bool = True            # 启用分区校正场（3x3 一阶仿射，边界 IDW 融合）
    piecewise_nx: int = 3                 # 分区网格列数
    piecewise_ny: int = 3                 # 分区网格行数
    piecewise_order: int = 1              # 局部多项式阶数(1/2)


@dataclass
class AppConfig:
    dpi: int = 4800
    cross: CrossDetectParams = field(default_factory=CrossDetectParams)
    chess: ChessboardParams = field(default_factory=ChessboardParams)

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        cfg = cls()
        cfg.dpi = d.get("dpi", cfg.dpi)
        if "cross" in d:
            cfg.cross = CrossDetectParams(**{k: v for k, v in d["cross"].items()
                                             if k in CrossDetectParams.__dataclass_fields__})
        if "chess" in d:
            cfg.chess = ChessboardParams(**{k: v for k, v in d["chess"].items()
                                            if k in ChessboardParams.__dataclass_fields__})
        return cfg
