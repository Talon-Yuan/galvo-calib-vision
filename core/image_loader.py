# -*- coding: utf-8 -*-
"""图像加载与大图处理：支持中文路径、整图/降采样加载、ROI 读取。

v4.3：新增"检测解码策略"——
  * 原图处理（use_full=True）：按原始分辨率解码灰度（2.23Gpx 也可，需内存足够）；
  * 后台 1/2（use_full=False）：按 1/2 分辨率解码后放大到原尺寸，坐标口径不变。
两种模式的 original_gray() 返回数组尺寸始终等于原图尺寸，下游检测代码无需改动。
"""
import ctypes
import os
import threading
from typing import Optional, Tuple

# 必须在 import cv2 之前设置：OpenCV 默认限制约 2^30 像素，2.23Gpx 扫描图会直接解码失败
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", "4000000000")

import cv2
import numpy as np

# 整图加载上限（像素数）。超过则降采样加载并提示精度受限。
MAX_FULL_PIXELS = 200_000_000  # 预览/交互图目标像素（约 2 亿；与检测解码策略无关）
MAX_FULL_PIXELS_FULL = 640_000_000  # 兼容旧语义（小图整图加载）
# 真原图灰度解码硬上限：2.23Gpx A4@4800dpi 可解；再大按 1/2 后台处理
MAX_FULL_PIXELS_HARD = 2_600_000_000
PREVIEW_MAX_EDGE = 2400  # 预览图最长边


def available_ram_gb() -> float:
    """可用物理内存（GB）；失败返回 0（调用方按"未知"保守处理）。"""
    try:
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return float(st.ullAvailPhys) / (1024.0 ** 3)
    except Exception:
        pass
    return 0.0


def image_size_exact(path: str) -> Optional[Tuple[int, int]]:
    """读图片真实像素尺寸（只读文件头，不做整图解码）。

    v4.3：原来用 IMREAD_REDUCED_8 探针 *8 估算原尺寸，对尺寸不是 8 的倍数的图会差几像素
    （如 39685→39688），导致坐标映射轻微伸缩。这里优先用 PIL 读头拿到精确尺寸。
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:
        return None


def imread_unicode(path: str, flags: int = cv2.IMREAD_UNCHANGED) -> Optional[np.ndarray]:
    """支持中文/空格路径的图片读取。"""
    if not os.path.isfile(path):
        return None
    try:
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, flags)
    except Exception:
        return None


class ImageSource:
    """大图源：记录路径、原始尺寸、整图数组（若可整图加载）、加载降采样比例。"""

    def __init__(self, path: str, use_full: bool = False):
        """use_full=True 时采用"整图处理"模式：整图加载上限放宽到 MAX_FULL_PIXELS_FULL，
        避免 2400/4800dpi 大图被降采样导致精度损失（内存占用相应升高）。"""
        self.path = path
        self.use_full = use_full
        self.full: Optional[np.ndarray] = None
        self.scale = 1.0
        self.preview_scale = 1.0
        self.width = 0
        self.height = 0
        self.origin_w = 0
        self.origin_h = 0
        self.warning: str = ""
        self.decode_mode: str = "auto"   # full=真原图 / half=后台1/2
        self.orig_gray_scale: float = 1.0
        self._orig_gray: Optional[np.ndarray] = None
        self._orig_gray_lock = threading.Lock()
        self._orig_gray_level: int = 0   # 实际解码级别 1/2/4
        self._load(use_full)

    def _load(self, use_full: bool = False):
        probe = imread_unicode(self.path, cv2.IMREAD_REDUCED_COLOR_8)
        if probe is None:
            probe = imread_unicode(self.path, cv2.IMREAD_REDUCED_GRAYSCALE_8)
        if probe is None:
            raise ValueError("无法读取图片，请确认文件格式与路径")
        _exact = image_size_exact(self.path)
        if _exact is not None:
            self.origin_w, self.origin_h = int(_exact[0]), int(_exact[1])
        else:
            self.origin_h, self.origin_w = probe.shape[:2]
            self.origin_w *= 8
            self.origin_h *= 8
        n_pix = self.origin_w * self.origin_h
        # v4.3：交互/预览图固定按 ~MAX_FULL_PIXELS 像素加载（省内存，长边再缩到 2400）；
        # 检测用的"原图/1/2"解码在 original_gray() 里按需进行，二者解耦。
        if n_pix <= MAX_FULL_PIXELS:
            img = imread_unicode(self.path, cv2.IMREAD_UNCHANGED)
            if img is None:
                img = imread_unicode(self.path, cv2.IMREAD_COLOR)
            if img is not None:
                self.full = img
                self.width, self.height = self.origin_w, self.origin_h
                self.scale = 1.0
                return
        target = MAX_FULL_PIXELS
        scale = np.sqrt(target / float(n_pix))
        factor = max(2, int(round(1.0 / scale)))
        factor = min(8, max(2, 2 ** int(round(np.log2(factor)))))
        flags = {2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4,
                 8: cv2.IMREAD_REDUCED_COLOR_8}.get(factor, cv2.IMREAD_REDUCED_COLOR_8)
        img = imread_unicode(self.path, flags)
        if img is None:
            gflags = {2: cv2.IMREAD_REDUCED_GRAYSCALE_2, 4: cv2.IMREAD_REDUCED_GRAYSCALE_4,
                      8: cv2.IMREAD_REDUCED_GRAYSCALE_8}.get(factor, cv2.IMREAD_REDUCED_GRAYSCALE_8)
            img = imread_unicode(self.path, gflags)
        if img is None:
            raise ValueError("无法读取图片")
        self.full = img
        self.scale = self.origin_w / float(img.shape[1])
        self.width, self.height = img.shape[1], img.shape[0]
        # 提示只描述"检测解码策略"，避免把预览降采样误当成检测精度损失
        if use_full:
            self.warning = (f"图像极大（{self.origin_w}×{self.origin_h}）：已启用原图处理，"
                            f"检测按原始分辨率解码；预览仍用 1/{round(self.scale)} 降采样显示。")
        else:
            self.warning = (f"图像极大（{self.origin_w}×{self.origin_h}）：检测按后台 1/2 降采样处理"
                            f"（坐标已还原到原尺寸）；如需全精度请勾选“原图处理”。")

    def _ensure_precheck(self):
        """懒加载预检小图（约 1/8 降采样灰度，用于两阶段检测的候选预检）。"""
        if getattr(self, "_precheck_gray", None) is None:
            small = imread_unicode(self.path, cv2.IMREAD_REDUCED_GRAYSCALE_8)
            if small is None:
                small = imread_unicode(self.path, cv2.IMREAD_REDUCED_COLOR_8)
                if small is not None:
                    small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if small is None:
                raise ValueError("无法读取预检小图")
            self._precheck_gray = small

    def precheck_gray(self) -> np.ndarray:
        """预检小图（灰度，约原图 1/8，懒加载缓存）。"""
        self._ensure_precheck()
        return self._precheck_gray

    def precheck_scale(self) -> float:
        """预检小图相对原始图像的缩放比（origin / small，>=1）。"""
        self._ensure_precheck()
        return self.origin_w / self._precheck_gray.shape[1]

    def preview(self, max_edge: int = PREVIEW_MAX_EDGE) -> np.ndarray:
        """返回用于显示的降采样图（BGR，长边不超过 max_edge），并记录预览缩放比。

        结果带缓存：超大图首次 resize 可能耗时，在后台线程预热后，主线程
        再次调用直接命中缓存，避免 GUI 主线程重复执行大图缩放（卡死隐患）。
        """
        cache = getattr(self, "_preview_cache", None)
        if cache is not None:
            self.preview_scale = 1.0 / self._preview_cache_scale
            return cache
        img = self.full
        h, w = img.shape[:2]
        if max(h, w) <= max_edge:
            self.preview_scale = 1.0
            self._preview_cache = img
            self._preview_cache_scale = 1.0
            return img
        k = max_edge / max(h, w)
        self.preview_scale = 1.0 / k
        out = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        self._preview_cache = out
        self._preview_cache_scale = k
        return out

    def to_gray(self) -> np.ndarray:
        """整图灰度（uint8）。若原图为灰度则直接返回。"""
        if self.full.ndim == 2:
            return self.full
        return cv2.cvtColor(self.full, cv2.COLOR_BGR2GRAY)

    def original_gray(self) -> np.ndarray:
        """检测用灰度图（尺寸恒等于原图，uint8，懒加载缓存）。

        v4.3 解码策略：
          * use_full=True 且内存足够 & 像素数<=MAX_FULL_PIXELS_HARD → 原始分辨率解码；
          * 否则 → 1/2 分辨率解码后放大到原尺寸（坐标口径不变，精度约 ±1 原像素）。
        返回数组尺寸恒为 (origin_h, origin_w)，下游检测代码无需感知解码级别。
        """
        g = getattr(self, "_orig_gray", None)
        if g is not None:
            return g
        with self._orig_gray_lock:
            g = getattr(self, "_orig_gray", None)
            if g is not None:
                return g
            n_pix = int(self.origin_w) * int(self.origin_h)
            avail = available_ram_gb()
            want_full = bool(self.use_full) and n_pix > 0 and n_pix <= MAX_FULL_PIXELS_HARD
            # 真原图灰度约 n_pix 字节；要求可用内存 >= 需求*1.2 + 1GB，避免 OOM
            if want_full and avail > 0 and avail < n_pix / (1024.0 ** 3) * 1.2 + 1.0:
                want_full = False
            levels = [1, 2, 4] if want_full else [2, 4]
            g = None
            used = 0
            for level in levels:
                flags = {1: cv2.IMREAD_GRAYSCALE, 2: cv2.IMREAD_REDUCED_GRAYSCALE_2,
                         4: cv2.IMREAD_REDUCED_GRAYSCALE_4}.get(level, cv2.IMREAD_GRAYSCALE)
                try:
                    cand = imread_unicode(self.path, flags)
                except MemoryError:
                    cand = None
                if cand is not None:
                    g = cand
                    used = level
                    break
            if g is None:
                # 最后兜底：用预览图灰度放大（精度受限，明确告警）
                g = cv2.cvtColor(self.full, cv2.COLOR_BGR2GRAY) if self.full.ndim == 3 else self.full
                used = max(2, int(round(self.scale)))
            # 统一放大到原图尺寸（1/2 或 1/4 → 原尺寸），下游坐标口径恒定
            if g.shape[0] != self.origin_h or g.shape[1] != self.origin_w:
                g = cv2.resize(g, (self.origin_w, self.origin_h), interpolation=cv2.INTER_CUBIC)
            self.orig_gray_scale = 1.0
            self._orig_gray_level = used
            self.decode_mode = "full" if used == 1 else "half"
            if used >= 4:
                self.warning = (f"原图解码失败（{self.origin_w}×{self.origin_h}），已用 1/{used} 预览图放大，"
                                f"精度受限；建议增加内存或分块扫描。")
            self._orig_gray = g
            return g

    def roi(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """按原始像素坐标取 ROI（自动裁剪边界，按加载比例映射）。"""
        # C03 守卫：scale 异常（0/None/负数）时按 1.0 处理，避免 ZeroDivisionError；
        # 正常路径（1.0 或多倍降采样）数值行为与旧实现完全一致。
        sc = float(getattr(self, "scale", 1.0) or 1.0)
        if sc <= 0:
            sc = 1.0
        if sc == 1.0:
            img = self.full
        else:
            img = self.full
            x = int(x / sc)
            y = int(y / sc)
            w = int(w / sc)
            h = int(h / sc)
        x0 = max(0, min(x, img.shape[1] - 1))
        y0 = max(0, min(y, img.shape[0] - 1))
        x1 = max(x0 + 1, min(x + w, img.shape[1]))
        y1 = max(y0 + 1, min(y + h, img.shape[0]))
        return img[y0:y1, x0:x1]

    def to_dict(self) -> dict:
        return {"path": self.path, "origin_w": self.origin_w, "origin_h": self.origin_h,
                "scale": self.scale, "warning": self.warning}
