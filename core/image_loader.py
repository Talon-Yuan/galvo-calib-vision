# -*- coding: utf-8 -*-
"""图像加载与大图处理：支持中文路径、整图/降采样加载、ROI 读取。"""
import os
import threading
from typing import Optional, Tuple

import cv2
import numpy as np

# 整图加载上限（像素数）。超过则降采样加载并提示精度受限。
MAX_FULL_PIXELS = 200_000_000  # 约 2 亿像素（A4@4800dpi 的 1.4 亿可整图）
MAX_FULL_PIXELS_FULL = 640_000_000  # "整图处理(不降采样)"模式上限（约 6.4 亿像素，A3@4800dpi 约 5.6 亿可整图）
PREVIEW_MAX_EDGE = 2400  # 预览图最长边


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
        self._orig_gray: Optional[np.ndarray] = None
        self._orig_gray_lock = threading.Lock()
        self._load(use_full)

    def _load(self, use_full: bool = False):
        probe = imread_unicode(self.path, cv2.IMREAD_REDUCED_COLOR_8)
        if probe is None:
            probe = imread_unicode(self.path, cv2.IMREAD_REDUCED_GRAYSCALE_8)
        if probe is None:
            raise ValueError("无法读取图片，请确认文件格式与路径")
        self.origin_h, self.origin_w = probe.shape[:2]
        self.origin_w *= 8
        self.origin_h *= 8
        n_pix = self.origin_w * self.origin_h
        limit = MAX_FULL_PIXELS_FULL if use_full else MAX_FULL_PIXELS
        # 尝试整图
        if n_pix <= limit:
            img = imread_unicode(self.path, cv2.IMREAD_UNCHANGED)
            if img is None:
                img = imread_unicode(self.path, cv2.IMREAD_COLOR)
            if img is not None:
                self.full = img
                self.width, self.height = self.origin_w, self.origin_h
                self.scale = 1.0
                return
        # 降采样加载
        target = limit
        scale = np.sqrt(target / n_pix)
        factor = max(2, int(1.0 / scale))
        img = imread_unicode(self.path, cv2.IMREAD_REDUCED_COLOR_2 if factor <= 2 else
                             cv2.IMREAD_REDUCED_COLOR_4 if factor <= 4 else
                             cv2.IMREAD_REDUCED_COLOR_8)
        if img is None:
            img = imread_unicode(self.path, cv2.IMREAD_REDUCED_GRAYSCALE_2 if factor <= 2 else
                                 cv2.IMREAD_REDUCED_GRAYSCALE_4 if factor <= 4 else
                                 cv2.IMREAD_REDUCED_GRAYSCALE_8)
        if img is None:
            raise ValueError("无法读取图片")
        self.full = img
        self.scale = self.origin_w / img.shape[1]
        self.width, self.height = img.shape[1], img.shape[0]
        if use_full:
            self.warning = (f"图像极大（{self.origin_w}×{self.origin_h}），超过整图处理上限，"
                            f"已按 1/{round(self.scale)} 降采样加载用于预览与检测。如需全精度请分块扫描。")
        else:
            self.warning = (f"图像过大（{self.origin_w}×{self.origin_h}），已按 1/{round(self.scale)} "
                            f"降采样加载用于预览与检测，输出坐标按比例还原。如需全精度请勾选“整图处理”或分块扫描。")

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
        """原始分辨率灰度图（uint8，懒加载缓存）。
        检测线程必须使用本图：无论预览/整图是否降采样，十字中心精测都在
        原始分辨率上进行，避免坐标按 scale 还原带来的精度损失（“整图处理”）。"""
        g = getattr(self, "_orig_gray", None)
        if g is not None:
            return g
        # 互斥锁：防止自动检测（DetectorThread）与手动识别灰度加载（GrayLoaderThread）
        # 并发对同一张超大图重复 IMREAD_GRAYSCALE 全分辨率解码——双份 2GB+ 灰度数组 +
        # 双倍 CPU 满载会造成内存/CPU 叠加，表现为程序“干死”。锁内解码、后到者复用缓存。
        with self._orig_gray_lock:
            g = getattr(self, "_orig_gray", None)
            if g is not None:
                return g
            g = imread_unicode(self.path, cv2.IMREAD_GRAYSCALE)
            if g is None:
                # 回退：降采样图放大回原始尺寸（精度受限，仅兜底）
                g = cv2.cvtColor(self.full, cv2.COLOR_BGR2GRAY) if self.full.ndim == 3 else self.full
                if self.scale != 1.0:
                    g = cv2.resize(g, (self.origin_w, self.origin_h), interpolation=cv2.INTER_CUBIC)
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
