from gui.theme import c as C  # v3.7.2 按主题取色
# -*- coding: utf-8 -*-
"""棋盘格校准面板：参数输入 + 精度预估实时显示(F10) + 检测/残差校验 + 保存标定。
V2 新功能：棋盘格自动识别(行列/单格/推荐膨胀系数)、异步处理(不卡界面)、进度条与识别过程可视化、膨胀系数。"""
from typing import Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QMessageBox, QProgressBar, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from core.calibrator import (AutoChessDetectResult, CalibResult, ChessboardParams,
                             auto_detect_chessboard,
                             build_ideal_points, calibrate, detect_chessboard,
                             estimate_precision, save_calibration)
from core.config import CrossDetectParams
from core.image_loader import ImageSource, imread_unicode
from gui.i18n import tr

import cv2
import numpy as np




def _set_role(widget, role: str) -> None:
    """按"语义角色"给控件换样式（v3.7.2）。

    背景：原来这些地方用 setStyleSheet 直接写死颜色，切深色/浅色都不会变，
    导致深色下提示文字发暗、徽章发白。改为设置 objectName，由 theme.py 里
    的 QLabel#hintWarn / #okText / #badgeOk 等规则按主题上色。
    """
    try:
        if widget.objectName() != role:
            widget.setObjectName(role)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except Exception:
        pass




# ---- v3.8：文件对话框统一用 Qt 自带实现，才能跟随深色主题 ----
# 系统原生对话框不受 Qt 样式表控制，深色模式下会弹出一个刺眼的白色系统框。
def qfd_open(parent, caption, directory="", filt="", *a, **k):
    return QFileDialog.getOpenFileName(parent, caption, directory, filt, *a,
                                       **k)


def qfd_save(parent, caption, directory="", filt="", *a, **k):
    return QFileDialog.getSaveFileName(parent, caption, directory, filt, *a,
                                       **k)


def qfd_dir(parent, caption, directory="", *a, **k):
    return QFileDialog.getExistingDirectory(parent, caption, directory, *a,
                                            **k)


class CalibImageLoaderThread(QThread):
    """后台加载棋盘格图像（构造 ImageSource + 预热预览），避免超大图解码阻塞 UI。

    修复背景：旧实现 _on_load 在主线程同步执行 ImageSource()，4800dpi 大图的
    probe 与整图/降采样加载耗时可达数十秒，窗口无响应表现为“干死”。
    与主窗口 ImageLoaderThread 同一模式：后台线程加载完成后经信号回主线程更新界面。
    """
    done = pyqtSignal(object)  # ImageSource 或 Exception

    def __init__(self, path: str, use_full: bool, parent=None):
        super().__init__(parent)
        self.path = path
        self.use_full = use_full

    def run(self):
        try:
            src = ImageSource(self.path, use_full=self.use_full)
            src.preview()  # 预热预览缓存，主线程展示时零耗时
            self.done.emit(src)
        except Exception as e:
            self.done.emit(e)


class CalibThread(QThread):
    """异步校准线程：检测角点→拟合→残差校验，全程不阻塞主界面。"""
    progress = pyqtSignal(float)     # 0~1
    stage = pyqtSignal(str)          # 阶段文本
    cornersFound = pyqtSignal(object)  # 检测到的角点(加载分辨率坐标)，供叠加显示识别过程
    done = pyqtSignal(object)        # CalibResult

    def __init__(self, src, params, px_scale, parent=None):
        super().__init__(parent)
        self.src = src
        self.params = params
        self.px_scale = px_scale
        self.result = None

    def run(self):
        # v3.3.2：整段加异常兜底。此前若检测/拟合内部抛异常（例如超大图 SB 抛 cv2.error），
        # 本线程无 try/except → 既不 emit done 也不报错，界面会永久停在"处理中…"。
        try:
            self._run_impl()
        except Exception as e:  # noqa: BLE001
            try:
                msg = tr("calib.zh_de082aa7", e=str(e))
            except Exception:
                msg = "标定失败：" + str(e)
            self.done.emit(CalibResult(False, np.zeros((0, 2)), np.zeros((0, 2)),
                                       np.zeros((0, 2)), message=msg))

    def _run_impl(self):
        # to_gray() 对整图做 BGR→GRAY 转换，超大图可能耗时数秒，必须在子线程执行
        gray = self.src.to_gray()
        def cb(v):
            self.progress.emit(0.05 + 0.6 * v)
        self.stage.emit(tr("calib.detecting_corners"))
        ideal = build_ideal_points(self.params)
        det = detect_chessboard(gray, self.params, progress_cb=cb)
        if det is not None and len(det) == len(ideal):
            self.cornersFound.emit(det.astype(float).tolist())
        self.progress.emit(0.7)
        self.stage.emit(tr("calib.fitting_field"))
        # v3.3.2 性能修复：把上面已检测到的角点传进去，避免 calibrate 内部重复检测一遍。
        # 整图模式上一次检测耗时远超拟合，重复检测会使标定总耗时翻倍。
        res = calibrate(gray, self.params, px_scale=self.px_scale,
                        detected=(det if (det is not None and len(det) == len(ideal)) else None))
        self.result = res
        self.progress.emit(0.95)
        self.stage.emit(tr("calib.validating_residuals"))
        self.progress.emit(1.0)
        self.done.emit(res)


class AutoChessThread(QThread):
    """异步标定板自动识别线程（棋盘格角点法 / 十字中心点法）。"""
    progress = pyqtSignal(float)
    stage = pyqtSignal(str)
    cornersFound = pyqtSignal(object)
    extentFound = pyqtSignal(object)   # 识别范围 (x0,y0,x1,y1) 加载分辨率坐标，供主窗口画红框
    done = pyqtSignal(object)        # AutoChessDetectResult

    def __init__(self, src, square_mm, dpi, pattern="auto", direction="auto", parent=None):
        super().__init__(parent)
        self.src = src
        self.square_mm = square_mm
        self.dpi = dpi
        self.pattern = pattern
        self.direction = direction
        self.result: Optional[AutoChessDetectResult] = None

    def run(self):
        try:
            # to_gray() 对整图做 BGR→GRAY 转换，超大图可能耗时数秒，必须在子线程执行
            gray = self.src.to_gray()
            self.stage.emit(tr("calib.auto_detecting_target"))
            self.result = auto_detect_chessboard(
                gray, self.square_mm, self.dpi,
                progress_cb=lambda v: self.progress.emit(v),
                direction=self.direction, pattern=self.pattern,
                pixel_scale=getattr(self.src, "scale", 1.0) or 1.0)
            if self.result.ok and self.result.corners is not None:
                self.cornersFound.emit(self.result.corners.astype(float).tolist())
                if self.result.extent_px is not None:
                    self.extentFound.emit(tuple(self.result.extent_px))
            self.done.emit(self.result)
        except Exception as exc:   # G11: 自动识别线程兜底，避免界面停在"识别中"
            try:
                self.done.emit(exc)
            except Exception:
                pass


def _uniform_form(form, label_w=112, field_w=148):
    """把 QFormLayout 排成统一节奏：标签右对齐、字段定宽（避免溢出被裁）。"""
    try:
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout,
                                     QLineEdit, QSizePolicy, QSpinBox)
    except Exception:
        return
    try:
        for r in range(form.rowCount()):
            li = form.itemAt(r, QFormLayout.LabelRole)
            fi = form.itemAt(r, QFormLayout.FieldRole)
            lw = li.widget() if li is not None else None
            if lw is not None and hasattr(lw, "text"):
                lw.setToolTip(str(lw.text() or ""))
            fw = fi.widget() if fi is not None else None
            if isinstance(fw, (QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit)):
                fw.setFixedWidth(148)
                if isinstance(fw, (QSpinBox, QDoubleSpinBox)):
                    fw.setAlignment(_Qt.AlignRight | _Qt.AlignVCenter)
    except Exception:
        pass


class CalibrationWidget(QWidget):
    passed = pyqtSignal(bool)          # 校验通过/失败状态变化
    message = pyqtSignal(str)          # 状态栏消息
    previewRequest = pyqtSignal(object)  # 请求主窗口显示棋盘格预览图(ImageSource)
    progress = pyqtSignal(float)       # 校准/自动识别进度 0~1
    stage = pyqtSignal(str)            # 处理阶段文本
    cornersFound = pyqtSignal(object)  # 识别到的角点(加载分辨率坐标列表)
    extentFound = pyqtSignal(object)   # 自动识别范围 (x0,y0,x1,y1)，供主窗口画红框确认

    def __init__(self, parent=None):
        super().__init__(parent)
        self.src: Optional[ImageSource] = None
        self.result = None
        self.calib_params: Optional[ChessboardParams] = None
        self._build_ui()
        try:
            for _f in self.findChildren(QFormLayout):
                _uniform_form(_f)
        except Exception:
            pass
        self._update_precision()

    @property
    def mm_per_px(self) -> float:
        """当前扫描 DPI 对应的像素物理尺度(mm/px)。"""
        return 25.4 / self.sp_dpi.value()

    def _build_ui(self):
        root = QVBoxLayout(self)
        # ---- 自动识别区 ----
        self._grp_auto = QGroupBox(tr("calib.auto_chessboard_detect_no_manual_counting")); g0 = self._grp_auto
        v0 = QVBoxLayout(g0)
        self.btn_auto = QPushButton(tr("calib.auto_detect_rows_cols_params"))
        self.btn_auto.setToolTip(tr("calib.auto_infer_inner_corner_rows_cols_square_size_and_ga"))
        self.lbl_auto = QLabel(tr("calib.not_detected_import_a_chessboard_image_and_click_the"))
        self.lbl_auto.setTextFormat(Qt.RichText)
        self.lbl_auto.setWordWrap(True)
        self.lbl_auto.setWordWrap(True)
        _set_role(self.lbl_auto, "dimText")
        # 识别模式：自动 / 棋盘格角点 / 十字中心点
        h_mode = QHBoxLayout()
        self.lbl_pattern = QLabel(tr("calib.pattern")); h_mode.addWidget(self.lbl_pattern)
        self.cb_pattern = QComboBox()
        self.cb_pattern.addItem(tr("calib.auto_chessboard_then_cross"), "auto")
        self.cb_pattern.addItem(tr("calib.chessboard_corners"), "chessboard")
        self.cb_pattern.addItem(tr("calib.cross_centers"), "cross")
        self.cb_pattern.setToolTip(tr("calib.auto_try_chessboard_inner_corner_method_first_fall_b"))
        h_mode.addWidget(self.cb_pattern, 1)
        self.cb_pattern.setMinimumWidth(176)
        # 棋盘格走向：自动 / 保持 / 行列交换
        self.lbl_direction = QLabel(tr("calib.orientation")); h_mode.addWidget(self.lbl_direction)
        self.cb_direction = QComboBox(); self.cb_direction.setMaximumWidth(126)
        self.cb_direction.addItem(tr("common.auto"), "auto")
        self.cb_direction.addItem(tr("calib.keep_detected_orientation"), "normal")
        self.cb_direction.addItem(tr("calib.swap_rows_cols"), "transpose")
        self.cb_direction.setToolTip(tr("calib.if_auto_detection_fails_or_rows_cols_are_swapped_con"))
        h_mode.addWidget(self.cb_direction)
        v0.addWidget(self.btn_auto)
        v0.addLayout(h_mode)
        v0.addWidget(self.lbl_auto)
        root.addWidget(g0)

        g1 = QGroupBox(tr("calib.chessboard_params")); self._grp_params = g1
        f = QFormLayout(g1)
        # v3.6.1：标签右对齐 + 紧凑间距，避免输入框横跨整栏、值左对齐显散
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        f.setHorizontalSpacing(10)
        f.setVerticalSpacing(6)
        self.sp_rows = QSpinBox(); self.sp_rows.setRange(2, 60); self.sp_rows.setValue(9)
        self.sp_cols = QSpinBox(); self.sp_cols.setRange(2, 80); self.sp_cols.setValue(12)
        self.sp_square = QDoubleSpinBox(); self.sp_square.setRange(0.5, 100.0)
        self.sp_square.setValue(10.0); self.sp_square.setDecimals(2); self.sp_square.setSuffix(" mm")
        self.sp_gap = QDoubleSpinBox(); self.sp_gap.setRange(0.0, 50.0)
        self.sp_gap.setValue(0.0); self.sp_gap.setDecimals(2); self.sp_gap.setSuffix(" mm")
        self.sp_meas_sq = QDoubleSpinBox(); self.sp_meas_sq.setRange(0.0, 100.0)
        self.sp_meas_sq.setValue(0.0); self.sp_meas_sq.setDecimals(3); self.sp_meas_sq.setSuffix(" mm")
        self.sp_meas_w = QDoubleSpinBox(); self.sp_meas_w.setRange(0.0, 2000.0)
        self.sp_meas_w.setValue(0.0); self.sp_meas_w.setDecimals(3); self.sp_meas_w.setSuffix(" mm")
        self.sp_meas_h = QDoubleSpinBox(); self.sp_meas_h.setRange(0.0, 2000.0)
        self.sp_meas_h.setValue(0.0); self.sp_meas_h.setDecimals(3); self.sp_meas_h.setSuffix(" mm")
        self.sp_dpi = QDoubleSpinBox(); self.sp_dpi.setRange(72, 9600)
        self.sp_dpi.setValue(4800.0); self.sp_dpi.setDecimals(0); self.sp_dpi.setSuffix(" dpi")
        # 膨胀系数：棋盘格打标/打印实际尺寸与标称的比值（>1=膨胀，<1=收缩）
        self.sp_dilation = QDoubleSpinBox(); self.sp_dilation.setRange(0.900, 1.100)
        self.sp_dilation.setValue(1.000); self.sp_dilation.setDecimals(3)
        self.sp_dilation.setSingleStep(0.001); self.sp_dilation.setSuffix(" ×")
        self.sp_dilation.setToolTip(tr("calib.for_emergency_calibration_of_damaged_chessboards_ide"))
        self.lbl_dilation_rec = QLabel(tr("calib.rec_1_000_normal_no_dilation"))
        _set_role(self.lbl_dilation_rec, "okText")
        h_dil = QHBoxLayout()
        h_dil.addWidget(self.sp_dilation)
        h_dil.addWidget(self.lbl_dilation_rec)
        self.chk_full = QCheckBox(tr("common.full_image_processing_no_downscale"))
        self.chk_full.setToolTip(tr("calib.when_checked_oversized_images_load_at_full_resolutio"))
        self.cb_order = QComboBox(); self.cb_order.addItems([tr("calib.2nd_order"), tr("calib.3rd_order")])
        self.cb_method = QComboBox(); self.cb_method.addItems([tr("calib.sb_robust"), tr("calib.classic")])
        self.chk_subpixel = QCheckBox(tr("calib.subpixel_refinement")); self.chk_subpixel.setChecked(True)
        f.addRow(tr("calib.inner_corner_rows"), self.sp_rows)
        f.addRow(tr("calib.inner_corner_cols"), self.sp_cols)
        f.addRow(tr("calib.square_size"), self.sp_square)
        f.addRow(tr("calib.gap"), self.sp_gap)
        f.addRow(tr("calib.scan_dpi"), self.sp_dpi)
        f.addRow("", self.chk_full)
        self.lbl_dilation_lab = QLabel(tr("calib.dilation")); f.addRow(self.lbl_dilation_lab, h_dil)
        f.addRow(tr("calib.measured_square_mm_0_off"), self.sp_meas_sq)
        f.addRow(tr("calib.measured_width_mm_0_off"), self.sp_meas_w)
        f.addRow(tr("calib.measured_height_mm_0_off"), self.sp_meas_h)
        # ---- 分区校正场（阶段三）：默认启用，推荐 3x3 一阶仿射 ----
        self.chk_piecewise = QCheckBox(tr("calib.enable_piecewise_correction_field_recommended"))
        self.chk_piecewise.setChecked(True)
        self.chk_piecewise.setToolTip(tr("calib.fits_local_correction_field_per_pixel_plane_partitio"))
        self.sp_pw_nx = QSpinBox(); self.sp_pw_nx.setRange(1, 8); self.sp_pw_nx.setValue(3)
        self.sp_pw_ny = QSpinBox(); self.sp_pw_ny.setRange(1, 8); self.sp_pw_ny.setValue(3)
        self.sp_pw_nx.setToolTip(tr("calib.pixel_plane_partition_grid_cols_default_3"))
        self.sp_pw_ny.setToolTip(tr("calib.pixel_plane_partition_grid_rows_default_3"))
        self.cb_pw_order = QComboBox(); self.cb_pw_order.addItems([tr("calib.1st_order_affine_recommended"), tr("calib.2nd_order_curvature")])
        self.cb_pw_order.setToolTip(tr("calib.local_polynomial_order_1st_order_affine_absorbs_loca"))
        h_pw = QHBoxLayout()
        self.lbl_partition = QLabel(tr("calib.partition")); h_pw.addWidget(self.lbl_partition)
        h_pw.addWidget(self.sp_pw_nx)
        self.lbl_pw_x = QLabel("×"); h_pw.addWidget(self.lbl_pw_x)
        h_pw.addWidget(self.sp_pw_ny)
        self.lbl_pw_order = QLabel(tr("calib.order")); h_pw.addWidget(self.lbl_pw_order)
        h_pw.addWidget(self.cb_pw_order, 1)
        f.addRow("", self.chk_piecewise)
        self.lbl_piecewise_lab = QLabel(tr("calib.piecewise_correction_field"))
        # v3.6.1：原来标签和 分区/阶数 控件挤在同一行 -> 文字被裁；改为两行
        f.addRow(self.lbl_piecewise_lab)
        f.addRow(h_pw)
        f.addRow(tr("calib.field_order"), self.cb_order)
        f.addRow(tr("calib.corner_method"), self.cb_method)
        f.addRow("", self.chk_subpixel)
        # ---- v3.9.8 方案A：把"高级参数"整行搬到独立卡片，再整体折叠 ----
        self.btn_adv = QPushButton(tr("calib.advanced_params_click_to_expand"))
        self.btn_adv.setObjectName("collapseRow")
        self.btn_adv.setCheckable(True)
        self.btn_adv.setChecked(True)
        self.btn_adv.toggled.connect(self._on_toggle_advanced)

        _adv_ids = {id(w) for w in (
            self.sp_dilation, self.sp_meas_sq, self.sp_meas_w, self.sp_meas_h,
            self.chk_piecewise, self.sp_pw_nx, self.sp_pw_ny, self.cb_pw_order,
            self.lbl_partition, self.lbl_pw_order, self.lbl_piecewise_lab,
            getattr(self, "lbl_pw_x", None), self.chk_subpixel,
            self.cb_order, self.cb_method,
            getattr(self, "lbl_dilation_lab", None),
        ) if w is not None}

        from PyQt5.QtWidgets import QFormLayout as _QFL
        self.grp_adv = QGroupBox(tr("calib.advanced_params"))
        _fa = _QFL(self.grp_adv)
        _fa.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        _fa.setHorizontalSpacing(10)
        _fa.setVerticalSpacing(6)
        for _r in range(f.rowCount() - 1, -1, -1):
            _fi = f.itemAt(_r, _QFL.FieldRole)
            _li = f.itemAt(_r, _QFL.LabelRole)
            _fw = _fi.widget() if _fi is not None else None
            _lw = _li.widget() if _li is not None else None
            _is_adv = False
            if _fw is not None and id(_fw) in _adv_ids:
                _is_adv = True
            elif _fw is None and _fi is not None and _fi.layout() is not None:
                _is_adv = True
            elif _lw is not None and id(_lw) in _adv_ids:
                _is_adv = True
            if not _is_adv:
                continue
            _row = f.takeRow(_r)
            _li2 = getattr(_row, "labelItem", None)
            _fi2 = getattr(_row, "fieldItem", None)
            _a = _li2.widget() if _li2 is not None else None
            _b = _fi2
            if _b is not None and _b.widget() is not None:
                _fa.addRow(_a, _b.widget())
            elif _b is not None and _b.layout() is not None:
                if _a is not None:
                    _fa.addRow(_a)
                _fa.addRow(_b.layout())
            elif _a is not None:
                _fa.addRow(_a)
        self.grp_adv.setVisible(False)
        root.addWidget(self.grp_adv)

        root.addWidget(g1)

        g2 = QGroupBox(tr("calib.precision_estimate_live")); self._grp_precision = g2
        self.lbl_precision = QLabel("")
        self.lbl_precision.setTextFormat(Qt.RichText)
        self.lbl_precision.setWordWrap(True)
        self.lbl_precision.setWordWrap(True)
        g2.setLayout(QVBoxLayout()); g2.layout().addWidget(self.lbl_precision)
        root.addWidget(g2)
        root.addWidget(self.btn_adv)

        g3 = QGroupBox(tr("calib.actions")); self._grp_ops = g3
        v = QVBoxLayout(g3)
        self.btn_load = QPushButton(tr("calib.import_chessboard_image"))
        self.btn_calib = QPushButton(tr("calib.detect_corners_validate"))
        self.btn_save = QPushButton(tr("calib.save_calibration"))
        self.btn_save.setEnabled(False)
        self.lbl_res = QLabel(tr("common.not_calibrated"))
        self.lbl_res.setTextFormat(Qt.RichText)
        self.lbl_res.setWordWrap(True)
        self.lbl_res.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.lbl_stage = QLabel("")
        self.lbl_stage.setWordWrap(True)
        _set_role(self.lbl_stage, "dimText")
        v.addWidget(self.btn_load); v.addWidget(self.btn_calib)
        v.addWidget(self.btn_save); v.addWidget(self.lbl_res)
        v.addWidget(self.progress_bar); v.addWidget(self.lbl_stage)
        root.addWidget(g3)
        root.addStretch(1)

        for w in [self.sp_rows, self.sp_cols, self.sp_square, self.sp_gap,
                  self.sp_meas_sq, self.sp_meas_w, self.sp_meas_h, self.sp_dpi,
                  self.sp_dilation, self.cb_order, self.sp_pw_nx, self.sp_pw_ny,
                  self.cb_pw_order, self.chk_piecewise]:
            if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.valueChanged.connect(self._update_precision)
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._update_precision)
            else:
                w.toggled.connect(self._update_precision)
        self.btn_load.clicked.connect(self._on_load)
        self.btn_calib.clicked.connect(self._on_calibrate)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_auto.clicked.connect(self._on_auto)
        self._form = f
        # v3.6.1：数值框限宽，形成统一的对齐节奏
        for _w in (self.sp_rows, self.sp_cols, self.sp_square, self.sp_gap, self.sp_dpi,
                   getattr(self, "sp_dilation", None), getattr(self, "sp_meas_sq", None),
                   getattr(self, "sp_meas_w", None), getattr(self, "sp_meas_h", None)):
            try:
                if _w is not None:
                    _w.setMaximumWidth(132)
            except Exception:
                pass
        for _w in (getattr(self, "sp_pw_nx", None), getattr(self, "sp_pw_ny", None)):
            try:
                if _w is not None:
                    _w.setMaximumWidth(56)
            except Exception:
                pass
        try:
            self.cb_pw_order.setMinimumWidth(110)
        except Exception:
            pass

    def retranslate(self):
        """切换语言后刷新本面板静态文本。"""
        from gui.i18n import tr as _tr
        self.btn_auto.setText(_tr("calib.auto_detect_rows_cols_params"))
        self.lbl_auto.setText(_tr("calib.not_detected_import_a_chessboard_image_and_click_the"))
        self.lbl_dilation_rec.setText(_tr("calib.rec_1_000_normal_no_dilation"))
        self.chk_subpixel.setText(_tr("calib.subpixel_refinement"))
        self.chk_full.setText(_tr("common.full_image_processing_no_downscale"))
        self.btn_load.setText(_tr("calib.import_chessboard_image"))
        self.btn_calib.setText(_tr("calib.detect_corners_validate"))
        self.btn_save.setText(_tr("calib.save_calibration"))
        if not self.result:
            self.lbl_res.setText(_tr("common.not_calibrated"))
        for i, key in enumerate([_tr("calib.2nd_order"), _tr("calib.3rd_order")]):
            self.cb_order.setItemText(i, key)
        for i, key in enumerate([_tr("calib.sb_robust"), _tr("calib.classic")]):
            self.cb_method.setItemText(i, key)
        for i, key in enumerate([_tr("calib.1st_order_affine_recommended"), _tr("calib.2nd_order_curvature")]):
            self.cb_pw_order.setItemText(i, key)
        for i, key in enumerate([_tr("calib.auto_chessboard_then_cross"), _tr("calib.chessboard_corners"), _tr("calib.cross_centers")]):
            self.cb_pattern.setItemText(i, key)
        for i, key in enumerate([_tr("common.auto"), _tr("calib.keep_detected_orientation"), _tr("calib.swap_rows_cols")]):
            self.cb_direction.setItemText(i, key)
        self.chk_piecewise.setText(_tr("calib.enable_piecewise_correction_field_recommended"))
        # 表单标签
        mapping = [
            (self.sp_rows, "calib.inner_corner_rows"), (self.sp_cols, "calib.inner_corner_cols"),
            (self.sp_square, "calib.square_size"), (self.sp_gap, "calib.gap"),
            (self.sp_dpi, "calib.scan_dpi"), (self.sp_dilation, "calib.dilation"),
            (self.sp_meas_sq, "calib.measured_square_mm_0_off"), (self.sp_meas_w, "calib.measured_width_mm_0_off"),
            (self.sp_meas_h, "calib.measured_height_mm_0_off"), (self.cb_order, "calib.field_order"),
            (self.cb_method, "calib.corner_method"),
        ]
        for w, key in mapping:
            lb = self._form.labelForField(w)
            if lb is not None:
                lb.setText(_tr(key))
        # 分区校正场标签（显式保存的属性）
        if getattr(self, "lbl_piecewise_lab", None) is not None:
            self.lbl_piecewise_lab.setText(_tr("calib.piecewise_correction_field"))

        # 下拉框同理：英文选项更长时会被挤压，按 sizeHint 给最小宽度
        for _cmb in (getattr(self, "cb_pattern", None), getattr(self, "cb_direction", None),
                     getattr(self, "cb_order", None), getattr(self, "cb_pw_order", None),
                     getattr(self, "cb_method", None)):
            try:
                if _cmb is not None:
                    _cmb.setMinimumWidth(min(360, max(_cmb.width(), _cmb.sizeHint().width())))
            except Exception:
                pass
        for _cb in (getattr(self, "chk_full", None), getattr(self, "chk_subpixel", None),
                    getattr(self, "chk_piecewise", None)):
            try:
                if _cb is not None:
                    _cb.setMinimumWidth(_cb.sizeHint().width())
            except Exception:
                pass
        # GroupBox 标题
        for g, key in [(self._grp_auto, "calib.auto_chessboard_detect_no_manual_counting"), (self._grp_params, "calib.chessboard_params"),
                       (self._grp_precision, "calib.precision_estimate_live"), (self._grp_ops, "calib.actions")]:
            g.setTitle(_tr(key))
        # 行内标签
        self.lbl_pattern.setText(_tr("calib.pattern"))
        self.lbl_direction.setText(_tr("calib.orientation"))
        self.lbl_partition.setText(_tr("calib.partition"))
        self.lbl_pw_order.setText(_tr("calib.order"))
        self.lbl_dilation_lab.setText(_tr("calib.dilation"))
        # toolTip 刷新
        self.btn_auto.setToolTip(_tr("calib.auto_infer_inner_corner_rows_cols_square_size_and_ga"))
        self.cb_pattern.setToolTip(_tr("calib.auto_try_chessboard_inner_corner_method_first_fall_b"))
        self.cb_direction.setToolTip(_tr("calib.if_auto_detection_fails_or_rows_cols_are_swapped_con"))
        self.sp_pw_nx.setToolTip(_tr("calib.pixel_plane_partition_grid_cols_default_3"))
        self.sp_pw_ny.setToolTip(_tr("calib.pixel_plane_partition_grid_rows_default_3"))
        self.cb_pw_order.setToolTip(_tr("calib.local_polynomial_order_1st_order_affine_absorbs_loca"))
        self.chk_full.setToolTip(_tr("calib.when_checked_oversized_images_load_at_full_resolutio"))
        self.chk_piecewise.setToolTip(_tr("calib.fits_local_correction_field_per_pixel_plane_partitio"))
        self.sp_dilation.setToolTip(_tr("calib.for_emergency_calibration_of_damaged_chessboards_ide"))
        self._update_precision()

    # ---------- 参数 ----------
    def shutdown(self):
        for attr in ("_worker", "_calib_loader", "_auto_worker", "_loader"):
            th = getattr(self, attr, None)
            if th is not None and hasattr(th, "isRunning") and th.isRunning():
                try:
                    th.requestInterruption()
                except Exception:
                    pass
                try:
                    th.wait(1500)
                except Exception:
                    pass

    def _on_toggle_advanced(self, collapsed: bool):
        """收起/展开"高级参数"整张卡片。"""
        try:
            self.grp_adv.setVisible(not collapsed)
            self.btn_adv.setText(tr("calib.advanced_params_click_to_collapse") if not collapsed
                                 else tr("calib.advanced_params_click_to_expand"))
        except Exception:
            pass

    def params(self) -> ChessboardParams:
        return ChessboardParams(
            rows=self.sp_rows.value(), cols=self.sp_cols.value(),
            square_mm=self.sp_square.value(), gap_mm=self.sp_gap.value(),
            measured_square_mm=self.sp_meas_sq.value() or None,
            measured_w_mm=self.sp_meas_w.value() or None,
            measured_h_mm=self.sp_meas_h.value() or None,
            corner_method="sb" if self.cb_method.currentIndex() == 0 else "classic",
            subpixel=self.chk_subpixel.isChecked(),
            order=2 if self.cb_order.currentIndex() == 0 else 3,
            dilation=self.sp_dilation.value(),
            use_piecewise=self.chk_piecewise.isChecked(),
            piecewise_nx=self.sp_pw_nx.value(), piecewise_ny=self.sp_pw_ny.value(),
            piecewise_order=1 if self.cb_pw_order.currentIndex() == 0 else 2,
        )

    # ---------- 精度预估 ----------
    def _update_precision(self):
        p = self.params()
        est = estimate_precision(p.rows, p.cols, p.square_mm, p.gap_mm, p.order)
        w = est.get("span_w_mm", 0)
        h = est.get("span_h_mm", 0)
        n = est.get("points", 0)
        step = est.get("step_mm", 0)
        dens = n / (w * h) if w > 0 and h > 0 else 0
        rms = est.get("est_residual_mm", 0)
        ok = est.get("level", "红")
        color = {"绿": C("ok"), "黄": C("warn"), "红": C("err")}.get(ok, C("text"))
        dil = p.dilation
        step_eff = step * dil
        self.lbl_precision.setText((
            tr("calib.span_w_0f_h_0f_mm", w=w, h=h) + "\n"
            + tr("calib.corners_n_step_step_1f_mm", n=n, step=step)
            + (tr("calib.after_dilation_step_1f", step=step_eff) if abs(dil - 1.0) > 1e-4 else "") + "\n"
            + tr("calib.corner_density_dens_5f_pts_mm", dens=dens) + "\n"
            + tr("calib.est_residual_rms_3f_mm", rms=rms) + "\n"
            + tr("calib.grade") + f": <span style='color:{color};font-weight:bold;'>{tr(ok)}</span>"
            + tr("calib.green_0_03_yellow_0_05_red_0_05_mm") + "\n"
            + (tr("calib.correction_piecewise_field_nx_ny_order_ord_recommend", nx=p.piecewise_nx, ny=p.piecewise_ny, ord=p.piecewise_order)
               if p.use_piecewise else tr("calib.correction_global_polynomial_order_ord", ord=p.order)) + "\n"
            + tr("calib.pixel_scale_1px_um_3f_m_limit_limit_2f_mm_px_1f_px", um=self.mm_per_px * 1000, limit=p.residual_limit_mm, px=p.residual_limit_mm / self.mm_per_px)).replace("\n", "<br>"))

    # ---------- 操作 ----------
    def _on_load(self):
        d = getattr(self, "_last_calib_dir", "") or ""
        path, _ = qfd_open(self, tr("calib.import_chessboard_image_2"), d,
                           tr("common.image_files_png_tif_tiff_jpg_jpeg_bmp"))
        if not path:
            return
        try:
            self._last_calib_dir = os.path.dirname(path)
        except Exception:
            pass
        # 异步加载：ImageSource 构造（含大图解码）放后台线程，主界面保持响应
        self._set_busy(True)
        self.lbl_res.setText(tr("calib.loading_large_image_in_background_please_wait"))
        self._calib_loader = CalibImageLoaderThread(path, self.chk_full.isChecked(), parent=self)
        self._calib_loader.done.connect(self._on_calib_loaded)
        self._calib_loader.start()

    def _on_calib_loaded(self, obj):
        self._set_busy(False)
        if isinstance(obj, Exception):
            QMessageBox.warning(self, tr("common.import_failed"), str(obj))
            self.lbl_res.setText(tr("common.import_failed"))
            return
        self.src = obj
        self.result = None
        self.lbl_res.setText(tr("calib.loaded_path", path=self.src.path.split("/")[-1].split("\\")[-1]))
        if self.src.warning:
            self.message.emit(self.src.warning)
        self.previewRequest.emit(self.src)

    def _busy(self):
        return getattr(self, "_worker", None) is not None and self._worker.isRunning()

    def _set_busy(self, busy: bool):
        for b in (self.btn_auto, self.btn_calib, self.btn_load):
            b.setEnabled(not busy)

    def _on_calibrate(self):
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("calib.please_import_a_chessboard_image_first"))
            return
        if self._busy():
            return
        self._set_busy(True)
        self.result = None
        self.btn_save.setEnabled(False)
        self.lbl_res.setText(tr("calib.processing"))
        p = self.params()
        self.calib_params = p
        self.progress_bar.setValue(0)
        worker = CalibThread(self.src, p, px_scale=self.src.scale)
        self._worker = worker
        worker.progress.connect(lambda v: self.progress_bar.setValue(int(v * 100)))
        worker.stage.connect(self.lbl_stage.setText)
        worker.cornersFound.connect(self._on_corners_found)
        worker.done.connect(self._on_calib_done)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def _on_worker_finished(self):
        # 防御性收尾：仅当结束的线程仍是当前 worker 才清 busy。
        # 常规收尾已在 _on_calib_done 完成；此处兜底线程异常提前退出等场景，
        # 且避免旧线程 finished 晚到误清新一轮 worker。
        if self.sender() is self._worker:
            self._set_busy(False)
            self._worker = None

    def _on_corners_found(self, pts):
        self.cornersFound.emit(pts)

    def _on_calib_done(self, res):
        # 仅接受当前 worker 的结果，杜绝旧线程 done 晚到覆盖新结果
        if self.sender() is not self._worker:
            return
        self.result = res
        self.progress_bar.setValue(100)
        p = self.calib_params
        # done 即结果主收尾点：同步清 busy/worker，消除与 finished 之间的竞态窗口
        self._set_busy(False)
        self._worker = None
        if res.ok:
            self.btn_save.setEnabled(True)
            self.passed.emit(True)
            mode_txt = ""
            if getattr(res, "piecewise", None) is not None:
                pw = res.piecewise
                mode_txt = (tr("calib.piecewise_nx_ny_order_ord_n_valid_partitions",
                               nx=pw.grid_nx, ny=pw.grid_ny, ord=pw.order, n=len(pw.cells)))
            self.lbl_res.setText(
                f"<span style='color:{C('ok')};font-weight:bold;'>{tr("calib.validation_passed")}</span>"
                + tr("calib.n_corners", n=len(res.detected))
                + tr("calib.rms_rms_4f_mm_max_max_4f_mm_limit_2f_mm",
                     rms=res.rms_mm, max=res.max_mm, limit=p.residual_limit_mm)
                + mode_txt)
        else:
            self.btn_save.setEnabled(False)
            self.passed.emit(False)
            self.lbl_res.setText(f"<span style='color:{C('err')};font-weight:bold;'>{tr("calib.validation_failed")}</span>：{res.message}")

    # ---------- 自动识别 ----------
    def _on_auto(self):
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("calib.please_import_a_chessboard_image_first"))
            return
        if self._busy():
            return
        self._set_busy(True)
        self.progress_bar.setValue(0)
        self.lbl_auto.setText(tr("calib.detecting"))
        pattern = str(self.cb_pattern.currentData() or "auto")
        direction = str(self.cb_direction.currentData() or "auto")
        worker = AutoChessThread(self.src, self.sp_square.value(), self.sp_dpi.value(),
                                 pattern=pattern, direction=direction)
        self._worker = worker
        worker.progress.connect(lambda v: self.progress_bar.setValue(int(v * 100)))
        worker.stage.connect(self.lbl_stage.setText)
        worker.cornersFound.connect(self._on_corners_found)
        worker.extentFound.connect(self.extentFound.emit)
        worker.done.connect(self._on_auto_done)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def _on_auto_done(self, res: AutoChessDetectResult):
        # G19: 只接受当前线程的结果，避免上一轮识别覆盖新一轮
        if self.sender() is not None and self.sender() is not getattr(self, "_auto_worker", None):
            if getattr(self, "_auto_worker", None) is not None:
                return
        self.progress_bar.setValue(100)
        if not res.ok:
            self.lbl_auto.setText(f"<span style='color:{C('err')};'>{tr("calib.detection_failed")}</span>：{res.message}<br>"
                                  f"<span style='color:{C('err')};'>{tr("calib.try_switching_pattern_orientation_above_and_retry")}</span>")
            return
        # 回填行列参数
        self.sp_rows.blockSignals(True); self.sp_rows.setValue(res.rows); self.sp_rows.blockSignals(False)
        self.sp_cols.blockSignals(True); self.sp_cols.setValue(res.cols); self.sp_cols.blockSignals(False)
        self._update_precision()
        # 推荐膨胀系数（不自动覆盖用户设定，仅提示并支持一键应用）
        rec = res.dilation_rec
        if abs(rec - 1.0) < 1e-4:
            rec_txt = tr("calib.rec_1_000_normal_no_dilation")
        else:
            rec_txt = (f"{tr("calib.recommended")}：{rec:.4f}（{tr("calib.dilated") if rec > 1 else tr("calib.shrunk")}）→ "
                       f"<a href='apply' style='color:{C('info')};'>{tr("calib.click_to_apply")}</a>")
        self._dilation_rec = rec
        self.lbl_dilation_rec.setText(rec_txt)
        self.lbl_dilation_rec.setOpenExternalLinks(False)
        # 防重复连接：多次自动识别成功会累积 linkActivated 回调，
        # 一次点击将触发多次 setValue（无功能副作用但冗余）
        try:
            self.lbl_dilation_rec.linkActivated.disconnect()
        except TypeError:
            pass
        self.lbl_dilation_rec.linkActivated.connect(self._apply_dilation_rec)
        tag = tr("calib.chessboard") if res.pattern_detected == "chessboard" else tr("calib.cross_array")
        ext = ""
        if res.extent_px is not None:
            x0, y0, x1, y1 = res.extent_px
            ext = tr("calib.extent_w_0f_h_0f_px", w=x1 - x0, h=y1 - y0)
        self.lbl_auto.setText(f"<span style='color:{C('ok')};'>✓ {tag}：{res.message}{ext}</span>")

    def _apply_dilation_rec(self, _=None):
        rec = getattr(self, "_dilation_rec", 1.0)
        self.sp_dilation.setValue(rec)
        applied = self.sp_dilation.value()
        if abs(applied - rec) > 1e-6:
            self.lbl_dilation_rec.setText(
                tr("calib.recommended_rec_4f_exceeds_adjustable_range_clamped_", rec=rec, applied=applied))
            self.message.emit(tr("calib.recommended_expansion_factor_rec_4f_out_of_range_cla", rec=rec, applied=applied))
        else:
            self.lbl_dilation_rec.setText(tr("calib.applied_rec_4f", rec=rec))
            self.message.emit(tr("calib.updated_with_recommended_dilation_rec_4f", rec=rec))

    def _on_save(self):
        if self.result is None:
            return
        path, _ = qfd_save(self, tr("calib.save_calibration_2"), "calibration.npz",
                                              tr("calib.calibration_files_npz"))
        if not path:
            return
        try:
            save_calibration(path, self.result, self.calib_params, self.mm_per_px)
            self.message.emit(tr("calib.calibration_saved_path", path=path))
        except Exception as e:
            QMessageBox.warning(self, tr("common.save_failed"), str(e))
