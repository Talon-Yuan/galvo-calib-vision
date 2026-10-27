# -*- coding: utf-8 -*-
"""B9 配准模型层：仿射 → 投影变换 / 二阶多项式 / TPS（3.0 第一主攻）。

背景（项目书 3.0 §2 B9，已拍板）：
    原 `coordinate_frame.fit_affine_grid` 只有 6 参数仿射，对纸张变形与
    扫描进给这类**非线性**系统误差只能"平均好"，max 误差压不下去。
    本模块在 **mm 物理域**新增更高阶模型层，把「实测点 → 名义网格」的
    正向映射拟合得更准，承担 3.0 系统误差吸收。

红线遵守：
    1. 名义值严禁反推：模型只拟合「实测 → 名义」**正向**映射，名义坐标
       永远来自设计值（pitch × 索引），不由实测反算；
    5. 坐标铁律：本模块输入输出全部是 mm 物理量，不接触预览/灰度像素；
    9. 同幅标定：模型参数只对当前这幅扫描图有效。save/load 强制带
       `frame_id`（幅面指纹），跨幅加载默认拒绝，需显式 force=True。
       这是专利 CN1216342C 教训（平板扫描仪 Y 向进给误差逐次不同，
       一次性标定不可复用）的代码级落实。

选型规则（关键，来自 spatialworkflow 2026 与项目书）：
    **不用残差选模型**（残差必然偏向自由度大的模型，点数少时多项式
    条件数可达 1e8、边缘跑飞），改用 **leave-one-out（LOO）预测误差**：
    每个点用其余点拟合后预测该点，取 RMS / max 对比。
    按复杂度递增 affine → homography → poly2 → tps 依次尝试，
    首个 LOO-RMS ≤ target（默认 0.12mm）即停，不盲目上 TPS。

数值稳定性：
    affine / homography / poly2 全部做 Hartley 归一化（居中 + 缩放，
    使平均半径 = √2），把设计矩阵条件数从 1e8 量级压回 1e1 量级，
    这是"多项式边缘跑飞"的主要工程对策。归一化只影响求解过程，
    对外坐标语义不变。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:  # OpenCV 仅投影变换需要；缺失时降级（其余模型纯 numpy）
    import cv2
except Exception:  # pragma: no cover - 环境缺 cv2 时由调用方降级
    cv2 = None

# 模型标识（按复杂度递增，选型顺序即此顺序）
KIND_AFFINE = "affine"
KIND_HOMOGRAPHY = "homography"
KIND_POLY2 = "poly2"
KIND_TPS = "tps"
MODEL_ORDER: Tuple[str, ...] = (KIND_AFFINE, KIND_HOMOGRAPHY, KIND_POLY2, KIND_TPS)

MODEL_LABEL_ZH: Dict[str, str] = {
    KIND_AFFINE: "仿射 6 参数（P1 基线）",
    KIND_HOMOGRAPHY: "投影变换 8 参数（P2）",
    KIND_POLY2: "二阶多项式 12 参数（P2）",
    KIND_TPS: "薄板样条 TPS（P3）",
}


# ---------------------------------------------------------------- 数值工具
def _hartley(pts: np.ndarray) -> np.ndarray:
    """Hartley 归一化矩阵：使点集质心在原点、平均半径 = √2。

    返回 3x3 齐次矩阵 T，满足 p_norm = T @ p_homo。
    这是抑制高阶多项式条件数爆炸的标准预处理（Hartley & Zisserman）。
    """
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    c = p.mean(axis=0)
    d = float(np.mean(np.linalg.norm(p - c, axis=1)))
    s = np.sqrt(2.0) / d if d > 1e-12 else 1.0
    return np.array([[s, 0.0, -s * c[0]],
                     [0.0, s, -s * c[1]],
                     [0.0, 0.0, 1.0]], np.float64)


def _to_homo(pts: np.ndarray) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    return np.concatenate([p, np.ones((len(p), 1))], axis=1)


def _from_homo(h: np.ndarray) -> np.ndarray:
    """齐次 → 欧氏，**必须除以第三分量**（漏除是经典坑，坐标会整体放大）。
    第三分量接近 0 时（点在无穷远）标记为 nan，由上层判失败。
    """
    w = h[:, 2:3]
    safe = np.where(np.abs(w) < 1e-12, np.nan, w)
    return h[:, :2] / safe


def _apply(T: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """用 3x3 齐次矩阵变换 Nx2 点集，返回 Nx2（统一走这里，防转置错位）。

    注意：必须先 (T @ p_homo.T).T 转回 Nx3，再交给 _from_homo。
    若漏掉内层 .T，_from_homo 会把 3 行当成 3 个点做行切片（曾踩此坑）。
    """
    return _from_homo((T @ _to_homo(pts).T).T)


def _poly2_design(pts_n: np.ndarray) -> np.ndarray:
    """二阶多项式设计矩阵 [1, x, y, x², xy, y²]（归一化坐标下构造）。"""
    x = pts_n[:, 0]
    y = pts_n[:, 1]
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def _rms(v: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(v)))) if len(v) else float("nan")


def _fmt(v: float, nd: int = 4) -> str:
    return "nan" if v is None or not np.isfinite(v) else f"{v:.{nd}f}"


# ---------------------------------------------------------------- 拟合结果
@dataclass
class RegistrationFit:
    """单个模型的拟合结果（mm 域）。

    Attributes:
        kind: 模型标识，见 MODEL_ORDER。
        ok: 是否拟合成功。
        loo_rms_mm / loo_max_mm: leave-one-out 预测误差（选型唯一依据）。
        resid_rms_mm / resid_max_mm: 全点拟合残差，**仅作展示**，不用于选型。
        cond: 设计矩阵/求解矩阵条件数（数值稳定性体检；nan=不适用）。
        n_points: 参与拟合的控制点数。
        message: 人话说明（直接进 log 栏）。
    """
    kind: str
    ok: bool = False
    loo_rms_mm: float = float("inf")
    loo_max_mm: float = float("inf")
    resid_rms_mm: float = float("nan")
    resid_max_mm: float = float("nan")
    cond: float = float("nan")
    n_points: int = 0
    message: str = ""
    _model: Optional["_BaseModel"] = field(default=None, repr=False)

    # ---- 对外使用接口 ----
    def predict(self, pts_mm: np.ndarray) -> np.ndarray:
        """把实测 mm 坐标映射到名义 mm 坐标（正向，红线 1）。"""
        if not self.ok or self._model is None:
            raise ValueError(f"模型未就绪：{self.message}")
        return self._model.predict(pts_mm)

    def to_affine_2x3(self) -> Optional[np.ndarray]:
        """投影/仿射模型导出等价 2x3 矩阵（局部线性化用，poly2/tps 返回 None）。"""
        m = self._model
        if isinstance(m, (_AffineModel, _HomographyModel)):
            return m.matrix_2x3()
        return None

    def summary(self) -> str:
        if not self.ok:
            return f"{MODEL_LABEL_ZH.get(self.kind, self.kind)}：失败（{self.message}）"
        return (f"{MODEL_LABEL_ZH.get(self.kind, self.kind)}："
                f"LOO-RMS {_fmt(self.loo_rms_mm)}mm / LOO-max {_fmt(self.loo_max_mm)}mm "
                f"（拟合残差 {_fmt(self.resid_rms_mm)}mm，cond={_fmt(self.cond, 1)}，"
                f"{self.n_points} 点）")


class _BaseModel:
    """模型基类：子类实现 fit / predict / matrix_2x3。"""
    kind = "base"

    def fit(self, meas: np.ndarray, nom: np.ndarray) -> None:
        raise NotImplementedError

    def predict(self, pts_mm: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def matrix_2x3(self) -> Optional[np.ndarray]:
        return None

    def condition(self) -> float:
        return float("nan")


class _AffineModel(_BaseModel):
    kind = KIND_AFFINE

    def __init__(self) -> None:
        self.H = np.eye(3)
        self._T_src = np.eye(3)
        self._T_dst_inv = np.eye(3)
        self._cond = float("nan")

    def fit(self, meas: np.ndarray, nom: np.ndarray) -> None:
        self._T_src = _hartley(meas)
        self._T_dst = _hartley(nom)
        self._T_dst_inv = np.linalg.inv(self._T_dst)
        ms = _apply(self._T_src, meas)
        ns = _apply(self._T_dst, nom)
        A = np.stack([ms[:, 0], ms[:, 1], np.ones(len(ms))], axis=1)
        self._cond = float(np.linalg.cond(A))
        sol, *_ = np.linalg.lstsq(A, ns, rcond=None)      # 3x2
        M = np.zeros((3, 3))
        M[0, :] = sol[:, 0]
        M[1, :] = sol[:, 1]
        M[2, 2] = 1.0
        self._M = M
        self.H = self._T_dst_inv @ M @ self._T_src

    def predict(self, pts_mm: np.ndarray) -> np.ndarray:
        return _from_homo((self.H @ _to_homo(pts_mm).T).T)

    def matrix_2x3(self) -> Optional[np.ndarray]:
        return self.H[:2, :]

    def condition(self) -> float:
        return self._cond


class _HomographyModel(_BaseModel):
    """投影变换 8 参数，RANSAC 鲁棒（OpenCV findHomography）。

    注意单位：项目书里 4.0 的阈值是 **px 域**经验值；本模块坐标是 mm，
    4800dpi 下 1px ≈ 0.00529mm。默认阈值取 0.10mm（≈19px），
    足以吸收精测噪声、又能剔除误检点。不可直接沿用 4.0（=4mm 会全放行）。
    """
    kind = KIND_HOMOGRAPHY

    def __init__(self, ransac_thresh_mm: float = 0.10) -> None:
        self.H = np.eye(3)
        self.ransac_thresh_mm = float(ransac_thresh_mm)
        self.inliers = 0

    def fit(self, meas: np.ndarray, nom: np.ndarray) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV 不可用，投影变换模型跳过")
        H, mask = cv2.findHomography(np.asarray(meas, np.float32),
                                     np.asarray(nom, np.float32),
                                     cv2.RANSAC, self.ransac_thresh_mm)
        if H is None:
            raise RuntimeError("findHomography 求解失败（点共线或退化）")
        self.H = np.asarray(H, np.float64)
        self.inliers = int(mask.sum()) if mask is not None else len(meas)

    def predict(self, pts_mm: np.ndarray) -> np.ndarray:
        return _from_homo((self.H @ _to_homo(pts_mm).T).T)

    def matrix_2x3(self) -> Optional[np.ndarray]:
        return self.H[:2, :]

    def condition(self) -> float:
        return float(np.linalg.cond(self.H))


class _Poly2Model(_BaseModel):
    """二阶多项式 12 参数（x'、y' 各 6 系数），归一化坐标下 lstsq。"""
    kind = KIND_POLY2

    def __init__(self) -> None:
        self.coef = np.zeros((6, 2))
        self._T_src = np.eye(3)
        self._T_dst_inv = np.eye(3)
        self._cond = float("nan")

    def fit(self, meas: np.ndarray, nom: np.ndarray) -> None:
        self._T_src = _hartley(meas)
        self._T_dst = _hartley(nom)
        self._T_dst_inv = np.linalg.inv(self._T_dst)
        ms = _apply(self._T_src, meas)
        ns = _apply(self._T_dst, nom)
        A = _poly2_design(ms)
        self._cond = float(np.linalg.cond(A))
        self.coef, *_ = np.linalg.lstsq(A, ns, rcond=None)

    def predict(self, pts_mm: np.ndarray) -> np.ndarray:
        p = np.asarray(pts_mm, np.float64).reshape(-1, 2)
        ms = _apply(self._T_src, p)
        ns = _poly2_design(ms) @ self.coef
        return _apply(self._T_dst_inv, ns)

    def condition(self) -> float:
        return self._cond


class _TPSModel(_BaseModel):
    """薄板样条 TPS（U(r)=r²·log r），带 alpha 正则防边缘点被硬吃。

    求解 (n+3)x(n+3) 线性系统：
        [ K+αI  P ] [w]   [v]
        [ Pᵀ    0 ] [a] = [0]
    其中 K_ij = U(|c_i - c_j|)，P = [1, x, y]。
    自实现（numpy.linalg.solve），不引入第三方依赖；
    与 raphaelreme/tps 的广义 Polyharmonic 思路一致，alpha 语义对齐。
    """
    kind = KIND_TPS

    def __init__(self, alpha: float = 0.5) -> None:
        self.alpha = float(alpha)
        self.centers = None      # 归一化控制点 (n,2)
        self.weights = None      # (n,2)
        self.affine = None       # (3,2)
        self._T_src = np.eye(3)
        self._T_dst_inv = np.eye(3)
        self._cond = float("nan")

    @staticmethod
    def _u(r: np.ndarray) -> np.ndarray:
        # r²log r；r→0 时极限为 0，用 where 避免 log(0) 警告
        out = np.zeros_like(r)
        m = r > 1e-12
        out[m] = (r[m] ** 2) * np.log(r[m])
        return out

    def fit(self, meas: np.ndarray, nom: np.ndarray) -> None:
        self._T_src = _hartley(meas)
        self._T_dst = _hartley(nom)
        self._T_dst_inv = np.linalg.inv(self._T_dst)
        ms = _apply(self._T_src, meas)
        ns = _apply(self._T_dst, nom)
        n = len(ms)
        diff = ms[:, None, :] - ms[None, :, :]
        r = np.linalg.norm(diff, axis=2)
        K = self._u(r)
        # 正则只作用于非刚体部分（w），保证整体平移/旋转/缩放不被惩罚
        K = K + self.alpha * np.eye(n)
        P = np.concatenate([np.ones((n, 1)), ms], axis=1)
        L = np.zeros((n + 3, n + 3))
        L[:n, :n] = K
        L[:n, n:] = P
        L[n:, :n] = P.T
        rhs = np.zeros((n + 3, 2))
        rhs[:n, :] = ns
        self._cond = float(np.linalg.cond(L))
        sol = np.linalg.solve(L, rhs)
        self.centers = ms
        self.weights = sol[:n, :]
        self.affine = sol[n:, :]

    def predict(self, pts_mm: np.ndarray) -> np.ndarray:
        p = np.asarray(pts_mm, np.float64).reshape(-1, 2)
        ms = _apply(self._T_src, p)
        r = np.linalg.norm(ms[:, None, :] - self.centers[None, :, :], axis=2)
        out = self._u(r) @ self.weights
        out += np.concatenate([np.ones((len(ms), 1)), ms], axis=1) @ self.affine
        return _apply(self._T_dst_inv, out)

    def condition(self) -> float:
        return self._cond


_MODEL_FACTORY = {
    KIND_AFFINE: lambda **kw: _AffineModel(),
    KIND_HOMOGRAPHY: lambda **kw: _HomographyModel(kw.get("ransac_thresh_mm", 0.10)),
    KIND_POLY2: lambda **kw: _Poly2Model(),
    KIND_TPS: lambda **kw: _TPSModel(kw.get("tps_alpha", 0.5)),
}

# 各模型最少控制点数（低于此值直接判失败，不做硬拟合）
_MIN_POINTS = {
    KIND_AFFINE: 4,
    KIND_HOMOGRAPHY: 4,
    KIND_POLY2: 6,      # 6 系数/轴，至少 6 点；实际推荐 ≥10
    KIND_TPS: 6,
}

# 正式标定推荐控制点数（3x3=9 仅首测，不作精度判定 → 项目书验收 2）
RECOMMENDED_MIN_POINTS = 10


def _fit_once(kind: str, meas: np.ndarray, nom: np.ndarray, **kw) -> Tuple[Optional[_BaseModel], str]:
    m = np.asarray(meas, np.float64).reshape(-1, 2)
    n = np.asarray(nom, np.float64).reshape(-1, 2)
    if m.shape != n.shape:
        return None, f"实测/名义点形状不匹配 {m.shape} vs {n.shape}"
    if len(m) < _MIN_POINTS[kind]:
        return None, f"{MODEL_LABEL_ZH[kind]} 至少需 {_MIN_POINTS[kind]} 点（实际 {len(m)}）"
    if len(m) >= 3:
        # 退化检查：所有点近似共线时高阶模型无意义
        c = m - m.mean(axis=0)
        sv = np.linalg.svd(c, compute_uv=False)
        if sv[0] <= 1e-12 or sv[1] / sv[0] < 1e-4:
            return None, "控制点近似共线，模型退化"
    try:
        model = _MODEL_FACTORY[kind](**kw)
        model.fit(m, n)
    except Exception as exc:  # 单个模型失败不应中断选型
        return None, f"{type(exc).__name__}: {exc}"
    return model, ""


def fit_model(kind: str, meas_mm: np.ndarray, nom_mm: np.ndarray,
              compute_loo: bool = True, **kw) -> RegistrationFit:
    """拟合单个模型并（可选）计算 LOO 预测误差。

    Args:
        kind: MODEL_ORDER 之一。
        meas_mm: Nx2 实测点（mm，已完成归零换算）。
        nom_mm: Nx2 对应名义点（mm，设计值，红线 1 禁止由实测反算）。
        compute_loo: 是否算 LOO（对照实验需要；批量拟合内部会关掉加速）。
    """
    meas = np.asarray(meas_mm, np.float64).reshape(-1, 2)
    nom = np.asarray(nom_mm, np.float64).reshape(-1, 2)
    model, err = _fit_once(kind, meas, nom, **kw)
    if model is None:
        return RegistrationFit(kind=kind, ok=False, n_points=len(meas), message=err)

    pred = model.predict(meas)
    dist = np.linalg.norm(pred - nom, axis=1)
    fit = RegistrationFit(
        kind=kind, ok=True, n_points=len(meas),
        resid_rms_mm=_rms(dist), resid_max_mm=float(np.max(dist)),
        cond=model.condition(), _model=model,
    )
    if compute_loo:
        loo_rms, loo_max, loo_err = leave_one_out(kind, meas, nom, **kw)
        fit.loo_rms_mm = loo_rms
        fit.loo_max_mm = loo_max
        if loo_err:
            fit.message = loo_err
    msg = (f"{MODEL_LABEL_ZH[kind]} 拟合完成：LOO-RMS {_fmt(fit.loo_rms_mm)}mm，"
           f"拟合残差 {_fmt(fit.resid_rms_mm)}mm")
    if len(meas) < RECOMMENDED_MIN_POINTS:
        msg += f"；⚠ 控制点 {len(meas)} < {RECOMMENDED_MIN_POINTS}，仅可首测，不作精度判定"
    fit.message = (fit.message + "；" if fit.message else "") + msg
    return fit


def leave_one_out(kind: str, meas_mm: np.ndarray, nom_mm: np.ndarray,
                  **kw) -> Tuple[float, float, str]:
    """leave-one-out 预测误差：每次留出 1 点，用其余点拟合后预测该点。

    这是**唯一**选型依据（残差偏向自由度大的模型）。返回 (rms, max, err)。
    """
    meas = np.asarray(meas_mm, np.float64).reshape(-1, 2)
    nom = np.asarray(nom_mm, np.float64).reshape(-1, 2)
    n = len(meas)
    if n < _MIN_POINTS[kind] + 1:
        return float("inf"), float("inf"), f"点数 {n} 不足以做 LOO"
    errs: List[float] = []
    for i in range(n):
        idx = np.array([j for j in range(n) if j != i], dtype=int)
        model, msg = _fit_once(kind, meas[idx], nom[idx], **kw)
        if model is None:
            return float("inf"), float("inf"), f"LOO 第 {i + 1} 折拟合失败：{msg}"
        p = model.predict(meas[i:i + 1])[0]
        if not np.all(np.isfinite(p)):
            return float("inf"), float("inf"), "LOO 预测出现非有限值（齐次第三分量≈0）"
        errs.append(float(np.linalg.norm(p - nom[i])))
    arr = np.asarray(errs, np.float64)
    return _rms(arr), float(np.max(arr)), ""


def compare_models(meas_mm: np.ndarray, nom_mm: np.ndarray, **kw) -> List[RegistrationFit]:
    """跑全部模型（affine/homography/poly2/tps）出对照表，供验收实验用。"""
    out: List[RegistrationFit] = []
    for kind in MODEL_ORDER:
        out.append(fit_model(kind, meas_mm, nom_mm, compute_loo=True, **kw))
    return out


def select_model(meas_mm: np.ndarray, nom_mm: np.ndarray,
                 target_loo_rms_mm: float = 0.12,
                 kinds: Sequence[str] = MODEL_ORDER,
                 **kw) -> Tuple[Optional[RegistrationFit], List[RegistrationFit]]:
    """按复杂度递增选型：首个 LOO-RMS ≤ target 即停（项目书 B9 选型规则）。

    Returns:
        (选定模型, 实际试过的模型列表)。全部不达标时返回 LOO-RMS 最小者，
        并在 message 中明确标注"未达标"，绝不静默当过。
    """
    tried: List[RegistrationFit] = []
    for kind in kinds:
        fit = fit_model(kind, meas_mm, nom_mm, compute_loo=True, **kw)
        tried.append(fit)
        if fit.ok and np.isfinite(fit.loo_rms_mm) and fit.loo_rms_mm <= target_loo_rms_mm:
            fit.message = f"选型命中 {fit.kind}（LOO-RMS {_fmt(fit.loo_rms_mm)}mm ≤ {target_loo_rms_mm}mm）；" + fit.message
            return fit, tried
    ok_fits = [f for f in tried if f.ok and np.isfinite(f.loo_rms_mm)]
    if not ok_fits:
        return None, tried
    best = min(ok_fits, key=lambda f: f.loo_rms_mm)
    best.message = (f"⚠ 全部模型 LOO-RMS 均 > {target_loo_rms_mm}mm，"
                    f"取最小者 {best.kind}（{_fmt(best.loo_rms_mm)}mm）——精度未达标，按红线 4 不得宣告通过；"
                    + best.message)
    return best, tried


# ---------------------------------------------------------------- 名义网格
def nominal_grid_mm(rows: int, cols: int, pitch_mm: float,
                    origin_at_center: bool = True) -> np.ndarray:
    """生成名义点阵（mm，行主序，**设计值**，红线 1 禁反推）。

    origin_at_center=True 时把几何中心放在 (0,0)：奇数阵即中心十字为原点，
    与 B3「原点 = 阵列几何中心」一致。坐标轴：列方向 +X、行方向 +Y。
    """
    c = np.arange(cols, dtype=np.float64)
    r = np.arange(rows, dtype=np.float64)
    cc, rr = np.meshgrid(c, r)
    x = cc.ravel() * pitch_mm
    y = rr.ravel() * pitch_mm
    if origin_at_center:
        x -= (cols - 1) / 2.0 * pitch_mm
        y -= (rows - 1) / 2.0 * pitch_mm
    return np.stack([x, y], axis=1)


def compare_table(fits: Sequence[RegistrationFit], target_mm: float = 0.12) -> str:
    """对照表（markdown），验收 1 直接贴进报告。"""
    lines = ["| 模型 | 状态 | LOO-RMS(mm) | LOO-max(mm) | 拟合残差RMS(mm) | cond | 备注 |",
             "|---|---|---|---|---|---|---|"]
    for f in fits:
        if not f.ok:
            lines.append(f"| {MODEL_LABEL_ZH.get(f.kind, f.kind)} | 失败 | - | - | - | - | {f.message} |")
            continue
        flag = "✅ 达标" if f.loo_rms_mm <= target_mm else "❌ 未达标"
        cond = "-" if not np.isfinite(f.cond) else f"{f.cond:.3g}"
        lines.append(f"| {MODEL_LABEL_ZH.get(f.kind, f.kind)} | {flag} | {_fmt(f.loo_rms_mm)} | "
                     f"{_fmt(f.loo_max_mm)} | {_fmt(f.resid_rms_mm)} | {cond} | {f.n_points} 点 |")
    return "\n".join(lines)


# ---------------------------------------------------------------- 幅面绑定存取
def save_fit(path: str, fit: RegistrationFit, frame_id: str) -> str:
    """保存模型参数并绑定幅面指纹（红线 9：禁止跨幅复用）。

    frame_id 建议由调用方给「图像路径 + 文件大小/mtime + DPI」的哈希，
    保证同一幅图重跑可复用、换一幅图必然不匹配。
    """
    import json
    import os
    if not fit.ok or fit._model is None:
        raise ValueError("模型未就绪，拒绝保存")
    m = fit._model
    payload = {
        "kind": fit.kind, "frame_id": str(frame_id),
        "loo_rms_mm": fit.loo_rms_mm, "look_max_mm": fit.loo_max_mm,
        "resid_rms_mm": fit.resid_rms_mm, "n_points": fit.n_points,
        "msg": fit.message,
    }
    arrays = {}
    if isinstance(m, _AffineModel):
        arrays["H"] = m.H
    elif isinstance(m, _HomographyModel):
        arrays["H"] = m.H
    elif isinstance(m, _Poly2Model):
        arrays.update({"coef": m.coef, "T_src": m._T_src, "T_dst_inv": m._T_dst_inv})
    elif isinstance(m, _TPSModel):
        arrays.update({"centers": m.centers, "weights": m.weights, "affine": m.affine,
                       "T_src": m._T_src, "T_dst_inv": m._T_dst_inv})
        payload["alpha"] = m.alpha
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    np.savez_compressed(path, meta=json.dumps(payload, ensure_ascii=False), **arrays)
    return path


def load_fit(path: str, frame_id: str, force: bool = False) -> RegistrationFit:
    """加载模型；幅面指纹不匹配时**默认拒绝**（红线 9），需显式 force=True。"""
    import json
    data = np.load(path, allow_pickle=False)
    meta = json.loads(str(data["meta"]))
    if meta.get("frame_id") != str(frame_id) and not force:
        raise ValueError(f"幅面指纹不匹配（存档 {meta.get('frame_id')} ≠ 当前 {frame_id}）："
                         f"红线 9 禁止跨幅复用标定参数，请现场重标")
    kind = meta["kind"]
    if kind in (KIND_AFFINE, KIND_HOMOGRAPHY):
        m = _AffineModel() if kind == KIND_AFFINE else _HomographyModel()
        m.H = data["H"]
        if kind == KIND_HOMOGRAPHY:
            m._T_src = np.eye(3)
            m._T_dst_inv = np.eye(3)
    elif kind == KIND_POLY2:
        m = _Poly2Model()
        m.coef = data["coef"]
        m._T_src = data["T_src"]
        m._T_dst_inv = data["T_dst_inv"]
    elif kind == KIND_TPS:
        m = _TPSModel(alpha=float(meta.get("alpha", 0.5)))
        m.centers = data["centers"]
        m.weights = data["weights"]
        m.affine = data["affine"]
        m._T_src = data["T_src"]
        m._T_dst_inv = data["T_dst_inv"]
    else:
        raise ValueError(f"未知模型类型 {kind}")
    return RegistrationFit(kind=kind, ok=True,
                           loo_rms_mm=meta.get("loo_rms_mm", float("nan")),
                           loo_max_mm=meta.get("look_max_mm", float("nan")),
                           resid_rms_mm=meta.get("resid_rms_mm", float("nan")),
                           n_points=int(meta.get("n_points", 0)),
                           message=meta.get("msg", ""), _model=m)
