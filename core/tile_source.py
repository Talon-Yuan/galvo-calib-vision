# -*- coding: utf-8 -*-
"""分块图像源与分块调度检测（阶段一：加载/检测分块调度）。

背景：A3@4800dpi 整图约 5.6 亿像素，虽在 MAX_FULL_PIXELS_FULL(6.4亿) 内可整图加载，
但内存占用高、加载慢，且更大尺寸将突破上限。分块扫描（多张中等尺寸图）是工程上
可行的大图全精度路线：每块独立全分辨率加载，检测按网格点归属分块并行，输出统一
到全局坐标系。

阶段一实现：
- TileSource.from_files : 多文件分块源（各块懒加载全分辨率灰度 + 1/8 预检小图，
  按行列 + 重叠组织成全局画布；适合真实分块扫描产物）
- TileSource.from_split  : 单文件逻辑分块（把已加载灰度按行列切片成内存视图，
  用于等价性/正确性验证——与整图检测结果逐点对比）
- detect_points_tiled   : 分块调度检测（预检+精测两阶段、坐标映射回全局、线程池并行、
  重叠区多块冗余容错）

注：本文件由 v1.9.17 反推源码修复重建（原反推残留 NODE/Decompyle incomplete 过多，
按 docstring 语义 + dev_v1.9.8 的 cross_detector.detect_points_fast 回退链重建）。
"""
import math
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from core.config import CrossDetectParams
from core.cross_detector import CrossResult, _detect_one_point, _detect_with_expand


def imread_unicode(path, flags):
    """支持中文/空格路径的图片读取（与 image_loader 保持一致）。"""
    if not os.path.isfile(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, flags)


def _imread_gray_pil(path, scale_target=None):
    """PIL 灰度回退（OpenCV 有 CV_IO_MAX_IMAGE_PIXELS 上限，>10.7 亿像素
    整图解码被拒）。JPEG 优先 draft 1/scale_target 原生降采样解码，避免
    22 亿像素全分辨率 RGB 瞬态（实测 ~8.4GB 峰值）；draft 失败或非 JPEG
    回退全分辨率。失败返回 None。"""
    if not os.path.isfile(path):
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    import io
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        with open(path, 'rb') as f:
            buf = f.read()
        im = Image.open(io.BytesIO(buf))
        if scale_target and scale_target > 1 and im.format == 'JPEG':
            try:
                im.draft('L', (max(1, im.width // scale_target),
                                max(1, im.height // scale_target)))
            except Exception:
                pass
        gray = np.asarray(im.convert('L'), dtype=np.uint8)
        return gray
    except Exception:
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = old


def probe_size(path):
    """轻量探测图片原始尺寸 (w, h)，不加载全图。

    用 1/2 降采样探测：1/8 降采样在奇数尺寸下会引入 ±8px 取整误差，
    会导致 from_files 布局偏移；1/2 误差 ≤1px，足够布局使用。
    """
    probe = imread_unicode(path, cv2.IMREAD_REDUCED_COLOR_2)
    if probe is None:
        return None
    h, w = probe.shape[:2]
    return (w * 2, h * 2)


@dataclass
class Tile:
    """单块分块：全局画布偏移 (ox, oy)、块内尺寸 (w, h)。"""
    index: int
    path: str
    ox: int
    oy: int
    w: int
    h: int
    _gray: Optional[np.ndarray] = field(default=None, repr=False)
    _precheck: Optional[np.ndarray] = field(default=None, repr=False)


class TileSource:
    """分块图像源：管理多块分块图的布局与全局坐标系映射。"""

    def __init__(self, tiles: List[Tile], canvas_w: int, canvas_h: int,
                 overlap_px: int):
        self.tiles = tiles
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.overlap_px = overlap_px

    @classmethod
    def from_files(cls, paths: List[str], rows: int, cols: int,
                   overlap_px: int) -> 'TileSource':
        """多文件分块源：每块懒加载全分辨率灰度 + 1/8 预检小图。"""
        tiles: List[Tile] = []
        canvas_w = canvas_h = 0
        for r in range(rows):
            row_w = 0
            for c in range(cols):
                idx = r * cols + c
                p = paths[idx]
                size = probe_size(p) or (0, 0)
                ox = (canvas_w if c == 0 else
                      tiles[-1].ox + tiles[-1].w - overlap_px)
                oy = (sum(t.h for t in tiles[r * cols:r * cols + c]) if c == 0 else 0)
                # 更稳妥：按行累计
                if c == 0:
                    oy = sum(t.h for t in tiles[:r * cols + c])
                w, h = size
                t = Tile(index=idx, path=p, ox=ox, oy=oy, w=w, h=h)
                tiles.append(t)
                row_w += w - (overlap_px if c > 0 else 0)
            canvas_w = max(canvas_w, row_w)
        canvas_h = sum(t.h for t in tiles[:cols]) if rows else 0
        return cls(tiles, canvas_w, canvas_h, overlap_px)

    @classmethod
    def from_split(cls, gray: np.ndarray, rows: int, cols: int,
                   overlap_px: int) -> 'TileSource':
        """单文件逻辑分块：把已加载灰度按行列切片成内存视图（验证用）。"""
        H, W = gray.shape[:2]
        tiles: List[Tile] = []
        step_x = max(1, (W - overlap_px * (cols - 1)) // cols) if cols > 1 else W
        step_y = max(1, (H - overlap_px * (rows - 1)) // rows) if rows > 1 else H
        for r in range(rows):
            for c in range(cols):
                x0 = c * step_x
                y0 = r * step_y
                x1 = min(W, x0 + step_x + overlap_px) if c < cols - 1 else W
                y1 = min(H, y0 + step_y + overlap_px) if r < rows - 1 else H
                sub = gray[y0:y1, x0:x1]
                t = Tile(index=r * cols + c, path='', ox=x0, oy=y0,
                         w=sub.shape[1], h=sub.shape[0])
                t._gray = sub
                tiles.append(t)
        return cls(tiles, W, H, overlap_px)

    def tile_at(self, idx: int) -> Tile:
        return self.tiles[idx]

    def find_tiles_at(self, px: float, py: float) -> List[int]:
        """返回包含全局坐标点 (px, py) 的块索引列表（重叠区可能多个）。"""
        hits = []
        for t in self.tiles:
            if t.ox <= px < t.ox + t.w and t.oy <= py < t.oy + t.h:
                hits.append(t.index)
        return hits

    def _dist_to_tile_center(self, idx: int, px: float, py: float) -> float:
        t = self.tiles[idx]
        cx = t.ox + t.w / 2
        cy = t.oy + t.h / 2
        return (px - cx) ** 2 + (py - cy) ** 2

    def _detect_in_tile(self, t: Tile, px: float, py: float, pitch_px: float,
                        p: CrossDetectParams, refine_window_scale: float
                        ) -> Optional[CrossResult]:
        """在块 t 内检测全局点 (px,py)，成功返回全局坐标结果，失败返回 None。

        流程与 detect_points_fast 一致：块内预检(降采样) → 候选映射回块全分辨率
        小窗精测 → 失败扩窗 → 仍失败回退理论位置大窗。坐标统一加块偏移转全局。
        """
        ly = py - t.oy
        lx = px - t.ox
        if not (0 <= lx < t.w) or not (0 <= ly < t.h):
            return None
        if t._gray is None:
            t._gray = _imread_gray_pil(t.path) or imread_unicode(
                t.path, cv2.IMREAD_GRAYSCALE)
        if t._gray is None:
            return None
        base_win = int(pitch_px * p.search_window_scale / 2)
        res = _detect_one_point(t._gray, lx, ly, base_win, p, ref_angle_deg=None)
        if res is None and refine_window_scale:
            res = _detect_with_expand(t._gray, lx, ly, base_win, p)
        if res is None:
            return None
        return CrossResult(x=res.x + t.ox, y=res.y + t.oy,
                           score=res.score, ok=res.ok)

    def detect_points_tiled(self, points: np.ndarray, pitch_px: float,
                            p: CrossDetectParams, refine_window_scale: float = 0.5,
                            max_workers: int = 1,
                            progress_cb: Optional[Callable[[float], None]] = None
                            ) -> List[Optional[CrossResult]]:
        """分块调度检测：按全局网格点列表检测，返回与输入同序结果。

        每个点先定位所属块（重叠区取质心最近块优先，失败则尝试重叠区其他块，
        实现边界冗余容错）；块内走"预检→精测→扩窗"两阶段；坐标统一为全局。
        """
        results: List[Optional[CrossResult]] = []
        total = max(1, len(points))
        for i, (px_, py_) in enumerate(points):
            idxs = self.find_tiles_at(float(px_), float(py_))
            if not idxs:
                results.append(None)
                continue
            idxs_sorted = sorted(idxs, key=lambda k: self._dist_to_tile_center(
                k, float(px_), float(py_)))
            res = None
            for k in idxs_sorted:
                res = self._detect_in_tile(self.tiles[k], float(px_), float(py_),
                                           pitch_px, p, refine_window_scale)
                if res is not None:
                    break
            results.append(res)
            if progress_cb:
                progress_cb((i + 1) / total)
        return results

    def to_dict(self):
        return {'tiles': [{'index': t.index, 'ox': t.ox, 'oy': t.oy,
                           'w': t.w, 'h': t.h} for t in self.tiles],
                'canvas_w': self.canvas_w, 'canvas_h': self.canvas_h,
                'overlap_px': self.overlap_px}


@dataclass
class TileInfo:
    """分块管线单块信息（惰性视图版，无独立灰度数组直到物化）。"""
    index: int
    ox: int
    oy: int
    w: int
    h: int


class TilePipeline:
    """单文件大图分块管线：惰性载入 tile 灰度视图 + 预览降采样副本。

    - gray()      : 整图原始分辨率灰度（懒加载，一次解码；PNG 无法按块解码的工程折中）
    - load_tile() : 指定 tile 的灰度视图（零拷贝切片，惰性触发 gray()）
    - roi()       : 整图坐标取 ROI（自动裁剪边界；跨 tile 无缝，保证边界十字不截断）
    - build_preview(scale=0.25) : 预览降采样副本（BGR 或灰度，供 UI 显示）
    - release_gray(): 检测完成后释放整图灰度缓存（回收峰值内存）
    """
    DEFAULT_TILE_W = 2048
    DEFAULT_TILE_H = 2048
    PREVIEW_MAX_EDGE = 2400

    def __init__(self, path, tile_w=DEFAULT_TILE_W, tile_h=DEFAULT_TILE_H,
                 origin_w=None, origin_h=None, preview_max_edge=PREVIEW_MAX_EDGE):
        self.path = path
        self.tile_w = max(64, int(tile_w))
        self.tile_h = max(64, int(tile_h))
        self.preview_max_edge = int(preview_max_edge)
        self._base_gray = None          # 全分辨率灰度
        self._hi_gray = None            # 高分辨率底座（可能 1/2）
        self._hi_scale = 1.0
        self._materialized = False
        self._tile_arrays = None
        self._preview_cache = None
        if origin_w is not None and origin_h is not None:
            self.origin_w = int(origin_w)
            self.origin_h = int(origin_h)
        else:
            self._origin_w = self._origin_h = 0
        self.tiles: List[TileInfo] = []
        self._build_tiles()

    @property
    def origin_w(self):
        return self._origin_w

    @origin_w.setter
    def origin_w(self, v):
        self._origin_w = v

    @property
    def origin_h(self):
        return self._origin_h

    @origin_h.setter
    def origin_h(self, v):
        self._origin_h = v

    @property
    def tile_rows(self):
        return int(math.ceil(self.origin_h / self.tile_h)) if self.origin_h else 0

    @property
    def tile_cols(self):
        return int(math.ceil(self.origin_w / self.tile_w)) if self.origin_w else 0

    def _build_tiles(self):
        self.tiles = []
        idx = 0
        for ty in range(self.tile_rows):
            for tx in range(self.tile_cols):
                x0 = tx * self.tile_w
                y0 = ty * self.tile_h
                w = min(self.tile_w, self.origin_w - x0)
                h = min(self.tile_h, self.origin_h - y0)
                self.tiles.append(TileInfo(index=idx, ox=x0, oy=y0, w=w, h=h))
                idx += 1

    def gray(self):
        """整图原始分辨率灰度（懒加载缓存，锁内单例解码）。"""
        if self._materialized:
            return None
        if self._base_gray is None:
            with threading.Lock():
                if self._base_gray is None:
                    g = imread_unicode(self.path, cv2.IMREAD_GRAYSCALE)
                    if g is None:
                        g = _imread_gray_pil(self.path)
                    if g is not None:
                        self._base_gray = g
                        if self.origin_w == 0:
                            self.origin_w, self.origin_h = g.shape[1], g.shape[0]
                            self._build_tiles()
        return self._base_gray

    def gray_hi(self):
        """高分辨率灰度底座（检测/取窗统一入口）。

        降级链：全分辨率灰度 gray() → REDUCED_2（1/2 分辨率，OpenCV 解码器级
        降采样，不受像素上限约束）。返回底座数组并设置 self._hi_scale
        （底座相对整图分辨率比例，1.0 或 2.0）。
        """
        if self._hi_gray is None:
            g = self.gray()
            if g is not None:
                self._hi_gray = g
                self._hi_scale = 1.0
            else:
                g2 = imread_unicode(self.path, cv2.IMREAD_REDUCED_GRAYSCALE_2)
                if g2 is not None:
                    self._hi_gray = g2
                    self._hi_scale = 2.0
                    if self.origin_w == 0:
                        self.origin_w = g2.shape[1] * 2
                        self.origin_h = g2.shape[0] * 2
                        self._build_tiles()
        return self._hi_gray

    def hi_scale(self):
        """灰度底座相对整图分辨率的比例（>=1）。"""
        return self._hi_scale

    def materialize_tiles(self):
        """预热并物化（视图模式）：解码灰度底座并常驻，tile 访问走零拷贝视图。

        幂等：已物化直接返回 True。解码失败返回 False（保持原整图灰度路径）。
        """
        if self._materialized:
            return True
        if self._hi_gray is None:
            if self.gray_hi() is None:
                return False
        self._materialized = True
        return True

    def load_tile(self, ty, tx):
        """惰性载入 (ty, tx) tile 的灰度视图（底座坐标系，零拷贝切片）。

        未物化：整图灰度视图（零拷贝切片）；已物化：独立数组（常驻 ~4MB/块）。
        """
        if not (0 <= ty < self.tile_rows) or not (0 <= tx < self.tile_cols):
            return None
        t = self.tiles[ty * self.tile_cols + tx]
        if self.gray_hi() is None:
            return None
        view = self._hi_gray[t.oy:t.oy + t.h, t.ox:t.ox + t.w]
        if self._materialized:
            if self._tile_arrays is None:
                self._tile_arrays = [None] * len(self.tiles)
            arr = self._tile_arrays[t.index]
            if arr is None:
                arr = np.ascontiguousarray(view)
                self._tile_arrays[t.index] = arr
            return arr
        return view

    def tile_gray(self, idx):
        """按行优先索引取 tile 灰度。"""
        if not (0 <= idx < len(self.tiles)):
            return None
        return self.load_tile(idx // self.tile_cols, idx % self.tile_cols)

    def _roi_from_tiles(self, x, y, w, h):
        """已物化时的 ROI：按 tile 独立数组拼接目标窗口（跨 tile 无缝）。"""
        gx0 = max(0, int(x))
        gy0 = max(0, int(y))
        gx1 = min(self.origin_w, int(x) + max(0, int(w)))
        gy1 = min(self.origin_h, int(y) + max(0, int(h)))
        if gx1 <= gx0 or gy1 <= gy0:
            return None
        out = np.zeros((gy1 - gy0, gx1 - gx0), dtype=np.uint8)
        for t in self.tiles:
            if t.ox + t.w <= gx0 or t.oy + t.h <= gy0:
                continue
            if t.ox >= gx1 or t.oy >= gy1:
                continue
            part = self.tile_gray(t.index)
            if part is None:
                continue
            sx0 = max(0, gx0 - t.ox)
            sy0 = max(0, gy0 - t.oy)
            sx1 = min(t.w, gx1 - t.ox)
            sy1 = min(t.h, gy1 - t.oy)
            dx0 = max(0, t.ox - gx0)
            dy0 = max(0, t.oy - gy0)
            out[dy0:dy0 + (sy1 - sy0), dx0:dx0 + (sx1 - sx0)] = part[sy0:sy1, sx0:sx1]
        return out

    def roi(self, x, y, w, h):
        """按整图坐标取 ROI（自动裁剪边界，跨 tile 无缝，不会截断边界十字）。

        返回数组为灰度底座坐标系；调用方按 self.hi_scale() 映射回整图坐标。
        """
        gx0 = max(0, int(x))
        gy0 = max(0, int(y))
        gx1 = min(self.origin_w, int(x) + max(0, int(w)))
        gy1 = min(self.origin_h, int(y) + max(0, int(h)))
        if gx1 <= gx0 or gy1 <= gy0:
            return None
        if self._materialized and self._tile_arrays is not None:
            return self._roi_from_tiles(gx0, gy0, gx1 - gx0, gy1 - gy0)
        if self.gray_hi() is None:
            return None
        return self._hi_gray[gy0:gy1, gx0:gx1]

    def find_tiles_at(self, px, py):
        """返回包含全局坐标点 (px, py) 的 tile 索引列表（正常唯一；边界可能多个）。"""
        hits = []
        for t in self.tiles:
            if t.ox <= px < t.ox + t.w and t.oy <= py < t.oy + t.h:
                hits.append(t.index)
        return hits

    def build_preview(self, scale=1, bgr=True):
        """构建预览降采样副本（longest edge <= preview_max_edge），带缓存。

        scale: 目标降采样比例（0~1，相对原始分辨率）；取不到整图时用文件降采样档。
        bgr: True 返回 BGR 彩色（UI 显示），False 返回灰度。
        """
        if self._preview_cache is not None and bgr:
            return self._preview_cache
        g = self.gray_hi()
        if g is None:
            return None
        hi = g if self._hi_scale == 1 else cv2.resize(
            g, None, fx=1.0 / self._hi_scale, fy=1.0 / self._hi_scale,
            interpolation=cv2.INTER_AREA)
        h, w = hi.shape[:2]
        longest = max(h, w)
        if longest > self.preview_max_edge:
            s = self.preview_max_edge / float(longest)
            hi = cv2.resize(hi, (max(1, int(w * s)), max(1, int(h * s))),
                            interpolation=cv2.INTER_AREA)
        if bgr:
            out = cv2.cvtColor(hi, cv2.COLOR_GRAY2BGR) if hi.ndim == 2 else hi
            self._preview_cache = out
            return out
        return hi

    def preview(self):
        """默认预览（BGR，最长边 preview_max_edge）。"""
        return self.build_preview(scale=1, bgr=True)

    def release_gray(self):
        """释放灰度底座与物化 tile 数组（检测完成后调用，回收大块内存）。"""
        self._base_gray = None
        self._hi_gray = None
        self._hi_scale = 1.0
        self._tile_arrays = None
        self._materialized = False

    def to_dict(self):
        return {'path': self.path, 'origin_w': self.origin_w,
                'origin_h': self.origin_h, 'tile_w': self.tile_w,
                'tile_h': self.tile_h, 'tile_rows': self.tile_rows,
                'tile_cols': self.tile_cols,
                'tiles': [{'index': t.index, 'ox': t.ox, 'oy': t.oy,
                           'w': t.w, 'h': t.h} for t in self.tiles]}
