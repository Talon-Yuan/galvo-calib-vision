# -*- coding: utf-8 -*-
"""图像预览控件：QGraphicsView 大图预览，支持缩放/平移/叠加绘制/点选/拖拽微调。"""
from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem,
                             QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                             QGraphicsTextItem, QGraphicsView)

MODE_NONE = 0
MODE_PICK = 1      # 点选（起始点/方向点）
MODE_DRAG = 2      # 拖拽微调结果点
MODE_MANUAL = 3    # 手动识别（点击+吸附精化+方向键微调）


class ImageView(QGraphicsView):
    """预览图控件。scene 坐标 == 预览像素坐标（以图左上角为原点）。"""
    pointPicked = pyqtSignal(float, float)   # 预览坐标
    pointDragged = pyqtSignal(int, float, float)  # 索引, 预览坐标
    dragFinished = pyqtSignal(int)           # 拖拽结束（鼠标松开），供低频收尾
    manualPicked = pyqtSignal(float, float)  # 手动单击（预览坐标）
    manualNudge = pyqtSignal(int, float, float)  # 索引, 方向步长（原图像素：±1）
    undoRequested = pyqtSignal()             # Ctrl+Z 撤销手动操作

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self._pix_item: Optional[QGraphicsPixmapItem] = None
        self._overlay_items: List[QGraphicsItem] = []
        self._mode = MODE_NONE
        self._drag_index = -1
        self._img_size = (1, 1)  # 预览图宽高
        self._orig_size = (1, 1)  # 原图宽高
        self._to_orig = 1.0
        self._pick_points: List[Tuple[float, float]] = []
        # 手动识别区域灰色圆圈（虚线）及半径；_manual_roi 惰性创建
        self._manual_roi: Optional[QGraphicsEllipseItem] = None
        self._manual_roi_radius: float = 40.0
        # 选中点白圈：{idx: QGraphicsEllipseItem}，防御性初始化
        self._sel_rings: dict = {}
        # 与 _pick_points 对齐：每个可拖拽点对应一组 (graphics_item, 相对中心偏移[(dx,dy),...])
        self._pick_graphics: List[List[Tuple[QGraphicsItem, List[Tuple[float, float]]]]] = []
        self._auto_fit = True  # 自动适配模式：窗口resize后重新fitInView；用户滚轮缩放后关闭
        # 手动识别模式状态
        self._manual_hover_lines: List[QGraphicsLineItem] = []  # 悬停十字准线
        self._manual_loupe: Optional[QGraphicsPixmapItem] = None  # 局部放大镜
        self._manual_loupe_border: Optional[QGraphicsRectItem] = None
        self._manual_sel_idx = -1    # 手动模式当前选中点索引（方向键微调对象）
        self._preview_bgr = None     # 预览图 BGR（放大镜数据源）
        self._last_hover: Optional[Tuple[float, float]] = None
        # 大阵列缩放性能：SmartViewportUpdate 只重绘脏区；NoIndex 避免 BSP 空间
        # 索引在大量 item 移动/增删时反复重建（数万叠加标记卡死主因之一）。
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self._scene.setItemIndexMethod(QGraphicsScene.NoIndex)
        self._label_items: List[QGraphicsTextItem] = []  # 序号文本（LOD 控制显隐）
        self._lod_threshold = 2.0  # 视图缩放达到该倍数后显示序号文本
        self._label_force = False  # 小阵列时置 True：序号始终显示（由 main 设置）
        # B5 核对锚点（原点 O / 起始点角标）：常数个、永不参与 LOD 隐藏、
        # 不注册 pick_idx（不参与拖拽/命中测试），故单独登记一套清场列表。
        self._anchor_items: List = []

    # ---------- 显示 ----------
    def set_dark(self, dark: bool):
        """v3.5：预览画布背景跟随主题。
        QGraphicsView/QGraphicsScene 的背景不受 QSS 控制，深色模式下会一直是浅灰，
        必须显式设置背景刷，否则"深色主题 + 白色画布"非常刺眼。"""
        try:
            from PyQt5.QtGui import QBrush
            self._dark = bool(dark)
            col = QColor("#0f1319" if self._dark else "#eef1f6")
            self.setBackgroundBrush(QBrush(col))
            self._scene.setBackgroundBrush(QBrush(col))
            try:
                self.viewport().update()
            except Exception:
                pass
        except Exception:
            pass

    def show_image(self, preview_bgr, orig_w: int, orig_h: int):
        """preview_bgr: BGR numpy 数组（预览分辨率）。"""
        from core.image_loader import PREVIEW_MAX_EDGE
        h, w = preview_bgr.shape[:2]
        self._preview_bgr = preview_bgr
        self._scene.clear()
        self._pix_item = None
        # G01 修复：scene.clear() 会删除所有图元，手动模式的引用必须同步置空，
        # 否则后续 _hide_manual_hover()/悬停会 removeItem 已析构对象 -> RuntimeError -> qFatal。
        self._manual_roi = None
        self._manual_hover_lines = []
        self._manual_loupe = None
        self._manual_loupe_border = None
        self._manual_sel_idx = -1
        self._last_hover = None
        self._overlay_items = []
        self._pick_graphics = []
        self._pick_points = []
        self._sel_rings = {}
        self._label_items = []
        self._anchor_items = []
        from PyQt5.QtGui import QImage
        if preview_bgr.ndim == 3 and preview_bgr.shape[2] == 3:
            rgb = preview_bgr[:, :, ::-1].copy()
            qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
        else:
            qimg = QImage(preview_bgr.data, w, h, preview_bgr.strides[0], QImage.Format_Grayscale8).copy()
        self._pix_item = self._scene.addPixmap(QPixmap.fromImage(qimg))
        self._pix_item.setFlag(QGraphicsItem.ItemIsMovable, False)
        self._img_size = (w, h)
        self._orig_size = (orig_w, orig_h)
        self._to_orig = orig_w / w if w > 0 else 1.0
        self._scene.setSceneRect(0, 0, w, h)
        self._fit()

    def _fit(self):
        """自动适配视口（窗口resize后重新调用以保持图像完整可见）。"""
        if self._pix_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
            self._auto_fit = True

    def resizeEvent(self, event):
        """窗口尺寸变化后若处于自动适配模式则重新适配，避免图像超出视口显示异常。"""
        super().resizeEvent(event)
        if self._auto_fit and self._pix_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def clear_overlay(self):
        for it in self._overlay_items:
            try:
                self._scene.removeItem(it)
            except Exception:
                pass
        for ln in getattr(self, "_manual_hover_lines", []):
            try:
                self._scene.removeItem(ln)
            except Exception:
                pass
        self._manual_hover_lines = []
        self._manual_roi = None
        self._manual_loupe = None
        self._manual_loupe_border = None
        self._overlay_items = []
        self._pick_graphics = []
        self._pick_points = []
        self._sel_rings = {}
        self._label_items = []
        self._anchor_items = []

    def to_orig(self, px: float, py: float) -> Tuple[float, float]:
        """预览坐标 → 原图像素坐标。"""
        return px * self._to_orig, py * self._to_orig

    def add_marker(self, px: float, py: float, color: str = "#ff3b30", radius: float = 8.0,
                   label: str = "", pen_w: float = 2.0, pick_idx: int = -1):
        """叠加一个点标记（scene/预览坐标）。pick_idx>=0 时登记为可拖拽点图形。"""
        pen = QPen(QColor(color), pen_w)
        el = QGraphicsEllipseItem(px - radius, py - radius, radius * 2, radius * 2)
        el.setPen(pen)
        self._scene.addItem(el)
        self._overlay_items.append(el)
        if pick_idx >= 0:
            self._ensure_pick_graphics(pick_idx)
            self._pick_graphics[pick_idx].append((el, [(-radius, -radius)]))
        if label:
            t = QGraphicsTextItem(label)
            t.setDefaultTextColor(QColor(color))
            t.setPos(px + radius, py - radius)
            f = t.font()
            f.setPointSize(10)
            t.setFont(f)
            self._scene.addItem(t)
            self._overlay_items.append(t)
            if pick_idx >= 0:
                self._pick_graphics[pick_idx].append((t, [(radius, -radius)]))
        return el

    def add_cross_mark(self, px: float, py: float, size: float = 10.0, color: str = "#34c759",
                       pen_w: float = 2.0, pick_idx: int = -1, label: str = ""):
        """叠加十字标记。label 非空时在右上角显示序号文本（随拖拽联动，受 LOD 控制）。
        pick_idx>=0 时登记为可拖拽点图形。"""
        pen = QPen(QColor(color), pen_w)
        l1 = self._scene.addLine(px - size, py, px + size, py, pen)
        l2 = self._scene.addLine(px, py - size, px, py + size, pen)
        self._overlay_items.extend([l1, l2])
        if pick_idx >= 0:
            self._ensure_pick_graphics(pick_idx)
            self._pick_graphics[pick_idx].extend([
                (l1, [(-size, 0.0), (size, 0.0)]),
                (l2, [(0.0, -size), (0.0, size)]),
            ])
        if label:
            t = QGraphicsTextItem(label)
            t.setDefaultTextColor(QColor(color))
            f = t.font()
            f.setPointSize(9)
            t.setFont(f)
            t.setPos(px + size + 2, py - size - 2)
            self._scene.addItem(t)
            self._overlay_items.append(t)
            self._label_items.append(t)
            if pick_idx >= 0:
                self._pick_graphics[pick_idx].append((t, [(size + 2, -size - 2)]))
        return [l1, l2]

    def _ensure_pick_graphics(self, idx: int):
        while len(self._pick_graphics) <= idx:
            self._pick_graphics.append([])

    def set_pick_point(self, idx: int, px: float, py: float):
        """拖拽时同步更新第 idx 个可拖拽点的位置并移动其叠加图形，实现视觉跟随。"""
        if 0 <= idx < len(self._pick_points):
            self._pick_points[idx] = (px, py)
            if idx < len(self._pick_graphics):
                for item, offs in self._pick_graphics[idx]:
                    if isinstance(item, QGraphicsLineItem):
                        (ox1, oy1), (ox2, oy2) = offs[0], offs[1]
                        item.setLine(px + ox1, py + oy1, px + ox2, py + oy2)
                    elif isinstance(item, QGraphicsEllipseItem):
                        r = item.rect()
                        ox, oy = offs[0]
                        item.setRect(px + ox, py + oy, r.width(), r.height())
                    elif isinstance(item, QGraphicsTextItem):
                        ox, oy = offs[0]
                        item.setPos(px + ox, py + oy)
            # 不显式全场景 update：setLine/setRect/setPos 会自动标记脏区重绘，
            # 大阵列下逐帧全场景刷新会放大拖拽/微调卡顿。

    def set_pick_mark(self, idx: int, px: float, py: float, kind: str = "cross",
                      color: str = "#34c759", size: float = 12.0, label: str = "",
                      selected: bool = False):
        """重设第 idx 个可拖拽点的标记：删除旧图形就地重建，避免全量 _redraw_marks
        重建数万 item（大阵列手动点选/微调卡死根因之一）。kind: cross=十字 / dot=圆点。"""
        if idx < 0:
            return
        self._ensure_pick_graphics(idx)
        # 移除该点旧图形（含选中圈）
        for item, offs in self._pick_graphics[idx]:
            self._scene.removeItem(item)
            if item in self._overlay_items:
                self._overlay_items.remove(item)
        self._pick_graphics[idx] = []
        self._sel_rings.pop(idx, None)
        while len(self._pick_points) <= idx:
            self._pick_points.append((0.0, 0.0))
        self._pick_points[idx] = (px, py)
        if kind == "cross":
            self.add_cross_mark(px, py, size=size, color=color, pick_idx=idx, label=label)
        else:
            self.add_marker(px, py, color=color, radius=size, label=label, pick_idx=idx)
        if selected:
            ring = self.add_marker(px, py, "#ffffff", radius=16.0, pen_w=1.5, pick_idx=idx)
            self._sel_rings[idx] = ring
        self.scene().update()

    def update_pick_style(self, idx: int, color: str):
        """仅更新第 idx 个点标记颜色（不重建图形，选中圈保持不变）。"""
        if not (0 <= idx < len(self._pick_graphics)):
            return
        sel_item = self._sel_rings.get(idx)
        pen = QPen(QColor(color), 2.0)
        for item, offs in self._pick_graphics[idx]:
            if item is sel_item:
                continue
            if isinstance(item, (QGraphicsLineItem, QGraphicsEllipseItem)):
                item.setPen(pen)

    def set_manual_roi_radius(self, radius: float):
        """设置识别区域圆圈半径（预览坐标）。由 main 按精化窗口半径换算。"""
        self._manual_roi_radius = max(2.0, radius)

    def set_manual_roi(self, px: float, py: float):
        """在指定预览坐标绘制/移动识别区域灰色圆圈（虚线，不遮挡图像）。"""
        r = self._manual_roi_radius
        if self._manual_roi is None:
            pen = QPen(QColor("#8e8e93"), 1.2)
            pen.setStyle(Qt.DashLine)
            self._manual_roi = QGraphicsEllipseItem(px - r, py - r, r * 2, r * 2)
            self._manual_roi.setPen(pen)
            self._manual_roi.setZValue(85)
            self._scene.addItem(self._manual_roi)
        else:
            self._manual_roi.setRect(px - r, py - r, r * 2, r * 2)
            self._manual_roi.show()

    def hide_manual_roi(self):
        if self._manual_roi is not None:
            self._manual_roi.hide()

    def add_rect(self, x0: float, y0: float, x1: float, y1: float,
                 color: str = "#ff3b30", pen_w: float = 2.5):
        """叠加识别范围红框（scene/预览坐标），供自动识别结果核对。"""
        pen = QPen(QColor(color), pen_w)
        pen.setStyle(Qt.DashLine)
        r = QGraphicsRectItem(x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))
        r.setPen(pen)
        self._scene.addItem(r)
        self._overlay_items.append(r)
        return r

    # ---------- B5 核对锚点（原点 O / 起始点角标） ----------
    def add_origin_mark(self, px: float, py: float, x_dir=(1.0, 0.0), y_dir=(0.0, 1.0),
                        label: str = "O", color: str = "#0a84ff",
                        ax_x_color: str = "#ff3b30", ax_y_color: str = "#34c759",
                        size: float = 18.0, pen_w: float = 2.5, label_size: int = 11):
        """叠加阵列原点 O（= 阵列几何中心）核对锚点，scene/预览坐标。

        样式对齐 OpenCV drawFrameAxes 惯例：原点空心圆 + 红 X 短轴 + 绿 Y 短轴；
        轴方向取 grid.x_dir_px / y_dir_px 实测方向，阵列斜置时亦能读出朝向。
        锚点不注册 pick_idx（O 不是结果点，注册会被拖拽链路误当作第 N 点移动），
        也不进 _label_items（不参与 LOD 隐藏：缩远时锚点最需要可见）。
        """
        pen = QPen(QColor(color), pen_w)
        r = max(3.0, size * 0.35)
        circ = QGraphicsEllipseItem(px - r, py - r, r * 2.0, r * 2.0)
        circ.setPen(pen)
        self._scene.addItem(circ)
        self._overlay_items.append(circ)
        self._anchor_items.append(circ)
        for (dx, dy), c in (((x_dir[0], x_dir[1]), ax_x_color),
                            ((y_dir[0], y_dir[1]), ax_y_color)):
            n = (dx * dx + dy * dy) ** 0.5 or 1.0
            ex, ey = px + dx / n * size, py + dy / n * size
            ln = self._scene.addLine(px, py, ex, ey, QPen(QColor(c), pen_w))
            self._overlay_items.append(ln)
            self._anchor_items.append(ln)
        if label:
            t = QGraphicsTextItem(label)
            t.setDefaultTextColor(QColor(color))
            f = t.font()
            f.setPointSize(label_size)
            t.setFont(f)
            # 文本置于左下角：避开点序号（右上角 9pt）与红标，防止叠字
            t.setPos(px - size - 8.0, py + size - 2.0)
            self._scene.addItem(t)
            self._overlay_items.append(t)
            self._anchor_items.append(t)
        return circ

    def add_start_badge(self, px: float, py: float, label: str = "1",
                        color: str = "#ff2d55", arm: float = 16.0, pen_w: float = 2.5,
                        label_size: int = 11, inward=(1.0, 1.0)):
        """叠加「起始点」角标（当前编号模式下 1 号点所在角），scene/预览坐标。

        用 L 形直角括号而非圆圈：圆圈会与「选中点白圈」（radius=16）混淆。
        inward 为指向阵列内侧的方向，决定两条边长朝哪一侧伸出；文本放在角标
        外侧（-inward 侧），不压住该点自身十字与序号。
        """
        sx = 1.0 if inward[0] >= 0 else -1.0
        sy = 1.0 if inward[1] >= 0 else -1.0
        pen = QPen(QColor(color), pen_w)
        l1 = self._scene.addLine(px, py, px + sx * arm, py, pen)
        l2 = self._scene.addLine(px, py, px, py + sy * arm, pen)
        for it in (l1, l2):
            self._overlay_items.append(it)
            self._anchor_items.append(it)
        if label:
            t = QGraphicsTextItem(label)
            t.setDefaultTextColor(QColor(color))
            f = t.font()
            f.setPointSize(label_size)
            t.setFont(f)
            tx = px - sx * (arm + 4.0)
            ty = py - sy * (arm + 4.0) - (0.0 if sy > 0 else 14.0)
            t.setPos(tx, ty)
            self._scene.addItem(t)
            self._overlay_items.append(t)
            self._anchor_items.append(t)
        return l1, l2

    # ---------- 交互 ----------
    def set_mode(self, mode: int):
        self._mode = mode
        self._drag_index = -1
        if mode == MODE_DRAG or mode == MODE_MANUAL:
            self.setDragMode(QGraphicsView.NoDrag)
        else:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        if mode != MODE_MANUAL:
            self._hide_manual_hover()

    ZOOM_MIN = 0.02
    ZOOM_MAX = 400.0

    def wheelEvent(self, event):
        self._auto_fit = False  # 用户手动缩放后保持缩放比例，不再随窗口resize重排
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        z = max(float(self.transform().m11()), 0.01)
        new_z = z * factor
        if not (self.ZOOM_MIN <= new_z <= self.ZOOM_MAX):
            return  # 钳制缩放范围，避免极端倍数下浮点溢出/场景重绘异常
        self.scale(factor, factor)
        self._on_zoom_changed()
        event.accept()

    def _on_zoom_changed(self):
        """缩放后 LOD 控制序号文本显隐，并重置手动悬停状态。

        修复背景：手动识别模式下滚轮缩放时，旧缩放比例绘制的放大镜/十字准线
        会与场景错位且随场景重绘放大开销，表现为卡顿/崩溃；重置后按新场景重建。
        """
        self._apply_lod(float(self.transform().m11()))
        if self._mode == MODE_MANUAL:
            self._hide_manual_hover()
            self._last_hover = None

    def _apply_lod(self, zoom: float):
        """大阵列缩放保护：缩放级别低于阈值时隐藏全部序号文本（文本是场景重绘的
        最大开销），达到阈值或小阵列强制显示时恢复。"""
        show = zoom >= self._lod_threshold or self._label_force
        for t in self._label_items:
            if t.isVisible() != show:
                t.setVisible(show)

    def mousePressEvent(self, event):
        if self._mode == MODE_PICK and event.button() == Qt.LeftButton:
            sp = self.mapToScene(event.pos())
            if 0 <= sp.x() <= self._img_size[0] and 0 <= sp.y() <= self._img_size[1]:
                self.pointPicked.emit(sp.x(), sp.y())
            event.accept()
            return
        if self._mode == MODE_MANUAL and event.button() == Qt.LeftButton:
            sp = self.mapToScene(event.pos())
            if 0 <= sp.x() <= self._img_size[0] and 0 <= sp.y() <= self._img_size[1]:
                self.manualPicked.emit(sp.x(), sp.y())
            event.accept()
            return
        if self._mode == MODE_DRAG and event.button() == Qt.LeftButton:
            sp = self.mapToScene(event.pos())
            idx = self._hit_test(sp.x(), sp.y())
            if idx >= 0:
                self._drag_index = idx
                # 按下即让标记跳到鼠标位置，提供即时视觉反馈
                self.set_pick_point(idx, sp.x(), sp.y())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_index >= 0:
            sp = self.mapToScene(event.pos())
            self.pointDragged.emit(self._drag_index, sp.x(), sp.y())
            event.accept()
            return
        if self._mode == MODE_MANUAL:
            sp = self.mapToScene(event.pos())
            if 0 <= sp.x() <= self._img_size[0] and 0 <= sp.y() <= self._img_size[1]:
                self._update_manual_hover(sp)
            else:
                self._hide_manual_hover()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hide_manual_hover()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if self._mode == MODE_MANUAL and self._manual_sel_idx >= 0:
            k = event.key()
            dx = dy = 0.0
            if k == Qt.Key_Left:
                dx = -1.0
            elif k == Qt.Key_Right:
                dx = 1.0
            elif k == Qt.Key_Up:
                dy = -1.0
            elif k == Qt.Key_Down:
                dy = 1.0
            if dx != 0.0 or dy != 0.0:
                self.manualNudge.emit(self._manual_sel_idx, dx, dy)
                event.accept()
                return
        if event.matches(QKeySequence.Undo):
            self.undoRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_index >= 0:
            idx = self._drag_index
            self._drag_index = -1
            self.dragFinished.emit(idx)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _hit_radius(self) -> float:
        """命中半径（场景坐标）：随视图缩放自适应。屏幕上保持约 20px，
        且不小于标记视觉半长的 1.3 倍，保证放大后点标记边缘也能命中。"""
        zoom = max(float(self.transform().m11()), 0.01)
        size = 12.0  # 标记半长基准（绿十字 size=12 / 红标 radius=6）
        return max(20.0 / zoom, size * 1.3)

    def _hit_test(self, px: float, py: float) -> int:
        """命中检测：返回叠加点中距离最近的索引（半径自适应缩放）。"""
        if not self._pick_points:
            return -1
        radius = self._hit_radius()
        best, bd = -1, radius
        for i, (x, y) in enumerate(self._pick_points):
            d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
            if d < bd:
                bd, best = d, i
        return best

    # ---------- 手动识别模式 ----------
    def set_manual_selection(self, idx: int):
        """记录手动模式当前选中点索引（方向键微调对象）。"""
        self._manual_sel_idx = idx

    def clear_manual_selection(self):
        self._manual_sel_idx = -1

    def _update_manual_hover(self, sp):
        """手动模式悬停：绘制十字准线，并按需重建局部放大镜（5x）。"""
        if self._pix_item is None:
            return
        # 十字准线（虚线，贯穿整图）
        if not self._manual_hover_lines:
            pen = QPen(QColor("#ff9500"), 1.0)
            pen.setStyle(Qt.DashLine)
            l1 = self._scene.addLine(0, sp.y(), self._img_size[0], sp.y(), pen)
            l2 = self._scene.addLine(sp.x(), 0, sp.x(), self._img_size[1], pen)
            l1.setZValue(90)
            l2.setZValue(90)
            self._manual_hover_lines = [l1, l2]
        else:
            self._manual_hover_lines[0].setLine(0, sp.y(), self._img_size[0], sp.y())
            self._manual_hover_lines[1].setLine(sp.x(), 0, sp.x(), self._img_size[1])
        # 放大镜：位置变化超过阈值才重建像素内容，避免悬停时高频 resize
        if (self._last_hover is None
                or abs(sp.x() - self._last_hover[0]) > 1.5
                or abs(sp.y() - self._last_hover[1]) > 1.5):
            self._last_hover = (sp.x(), sp.y())
            self._rebuild_manual_loupe(sp)

    def _rebuild_manual_loupe(self, sp):
        """局部放大镜：取鼠标邻域 ROI 放大 5 倍显示（最近邻保持像素清晰）。"""
        if self._preview_bgr is None:
            return
        hw = 12  # 放大镜半宽（预览像素）
        cx, cy = int(round(sp.x())), int(round(sp.y()))
        h, w = self._preview_bgr.shape[:2]
        xa, xb = max(0, cx - hw), min(w, cx + hw + 1)
        ya, yb = max(0, cy - hw), min(h, cy + hw + 1)
        if xb - xa < 3 or yb - ya < 3:
            return
        roi = self._preview_bgr[ya:yb, xa:xb]
        import cv2
        zoom = 5
        big = cv2.resize(roi, ((xb - xa) * zoom, (yb - ya) * zoom), interpolation=cv2.INTER_NEAREST)
        if big.ndim == 3:
            rgb = big[:, :, ::-1].copy()
            qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
        else:
            qimg = QImage(big.data, big.shape[1], big.shape[0], big.strides[0], QImage.Format_Grayscale8).copy()
        pm = QPixmap.fromImage(qimg)
        if self._manual_loupe is None:
            self._manual_loupe = self._scene.addPixmap(pm)
            self._manual_loupe.setZValue(100)
            pen = QPen(QColor("#ff9500"), 1.5)
            self._manual_loupe_border = self._scene.addRect(0, 0, pm.width(), pm.height(), pen)
            self._manual_loupe_border.setZValue(101)
            self._manual_loupe_border.setParentItem(self._manual_loupe)
        else:
            self._manual_loupe.setPixmap(pm)
        # 放置：优先光标右下方，越界则移到左上方
        lw, lh = pm.width(), pm.height()
        ox = sp.x() + 16
        if ox + lw > self._img_size[0]:
            ox = max(0.0, sp.x() - 16 - lw)
        oy = sp.y() + 16
        if oy + lh > self._img_size[1]:
            oy = max(0.0, sp.y() - 16 - lh)
        self._manual_loupe.setPos(ox, oy)
        self._manual_loupe.show()

    def _hide_manual_hover(self):
        for it in self._manual_hover_lines:
            self._scene.removeItem(it)
        self._manual_hover_lines = []
        if self._manual_loupe is not None:
            # 先移除子项 border 再移除父项 loupe（border 是 loupe 的 child，
            # 先删父项会连带移除子项，随后对已不在 scene 的子项再 removeItem
            # 会触发 Qt 运行时警告）
            if self._manual_loupe_border is not None:
                self._scene.removeItem(self._manual_loupe_border)
                self._manual_loupe_border = None
            self._scene.removeItem(self._manual_loupe)
            self._manual_loupe = None
        self._last_hover = None

    # ---------- 整图痕迹导出 ----------
    def grab_scene_image(self, scale: float = 2.0, force_labels: bool = False) -> QPixmap:
        """将整个 scene（原图 + 全部叠加痕迹：十字/序号/红橙标）渲染为图像。

        scale 为超采样倍数（2.0 即导出 2 倍像素，供整板汇报留档）。
        force_labels=True 时临时显示 LOD 隐藏的序号文本（导出后恢复原显隐状态），
        保证导出图序号完整可见。
        注意：导出分辨率基于预览图（scene 坐标），与屏幕上所见一致。"""
        from PyQt5.QtCore import QRectF
        hidden = []
        if force_labels:
            for entry in getattr(self, "_label_items", []):
                it = entry[0] if isinstance(entry, (tuple, list)) else entry
                if not it.isVisible():
                    it.setVisible(True)
                    hidden.append(it)
        try:
            rect = self._scene.sceneRect()
            w = max(1, int(rect.width() * scale))
            h = max(1, int(rect.height() * scale))
            img = QImage(w, h, QImage.Format_RGB32)
            img.fill(Qt.white)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.scale(scale, scale)
            self._scene.render(p, QRectF(0.0, 0.0, rect.width(), rect.height()), rect)
            p.end()
            return QPixmap.fromImage(img)
        finally:
            for it in hidden:
                it.setVisible(False)
