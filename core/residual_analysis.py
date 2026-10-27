# -*- coding: utf-8 -*-
"""残差吸收闭环（B1）：把多摆放系统级残差的旋转/剪切分解结果转成
独立补偿层（npz 命名 res_rot_*.npz），叠加到 _to_mm 末尾。

红线保持：
- 不写校准 npz（吸收层永远独立成文件）；
- 补偿层与 res_fb 叠加顺序固定：base -> res_fb -> res_rot（res_rot 在末尾）；
- 只允许一个活动补偿层，切换前必须先回滚。

坐标系：补偿层以「软件输出系（mm）」为自变量，增量为
    d(x,y) = A @ [x, y] + t
吸收后软件输出 out = soft + d(soft)。模型即 order-1 ResidualCorrection，
与 multi_pose_fit.fit_system_residual 产出的 res_soft（软件输出系残差）配套。
"""
import glob
import os
from typing import Optional

import numpy as np

from core.residual_correction import (ResidualCorrection,
                                      load_residual_correction,
                                      save_residual_correction)


def absorb(xy_soft, res_soft, out_dir, source=''):
    """把软件输出系残差 res_soft 的仿射（旋转/剪切/缩放）分解转成独立补偿层。

    Args:
        xy_soft: Nx2 软件输出系坐标（mm，基础换算 + res_fb 之后）。
        res_soft: Nx2 同系残差（residual = real - soft，旋转回软件输出系）。
        out_dir: 补偿层 npz 输出目录（会创建）。
        source: 层来源描述（默认 "res_rot"）。
    Returns:
        (npz 绝对路径, 补偿层 ResidualCorrection, 分解报告 dict)。
    """
    xy = np.asarray(xy_soft, np.float64)
    res = np.asarray(res_soft, np.float64)
    if xy.shape != res.shape or len(xy) < 3:
        raise ValueError('xy_soft and res_soft must be Nx2 with same length (>=3)')
    Y = xy[:, 1]
    X = xy[:, 0]
    A = np.stack([np.ones_like(X), X, Y], axis=1)
    coef, *_ = np.linalg.lstsq(A, res, rcond=None)   # 3x2: [bias; a_x,a_y; c_x,c_y]
    bias = coef[0]
    lin = coef[1:].T                                # 2x2 仿射矩阵
    # 分解报告：旋转/剪切/缩放（角度制）
    a, c = lin[0, 0], lin[0, 1]
    b, d = lin[1, 0], lin[1, 1]
    theta = np.degrees(np.arctan2(c - b, a + d)) / 2.0
    scale_x = np.hypot(a, b)
    scale_y = np.hypot(c, d)
    shear = np.degrees(np.arctan2(b, a)) - theta
    report = {'theta_deg': float(theta), 'scale_x': float(scale_x),
              'scale_y': float(scale_y), 'shear_deg': float(shear),
              'bias': bias.tolist()}
    os.makedirs(out_dir, exist_ok=True)
    src = source or 'res_rot'
    path = os.path.join(out_dir, f'res_rot_{src}.npz')
    layer = ResidualCorrection(order=1)
    layer.coeff = np.concatenate([bias, lin.reshape(-1)])
    save_residual_correction(layer, path)
    return path, layer, report


def find_active_layer(out_dir):
    """返回 out_dir 下最近一个活动补偿层 npz；没有则 None。"""
    fs = sorted(glob.glob(os.path.join(out_dir, 'res_rot_*.npz')))
    return fs[-1] if fs else None


def rollback_layer(path):
    """删除指定补偿层文件（回滚吸收）。返回是否删除成功。"""
    if not path:
        return False
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def load_layer(path):
    """加载补偿层 npz（res_rot_*.npz 或 res_fb_*.npz 通用）。"""
    return load_residual_correction(path)
