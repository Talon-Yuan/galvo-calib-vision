# -*- coding: utf-8 -*-
"""多摆放系统级残差校正对话框（B 路线·无角度方案）。

用户导入 1~5 张不同摆放的玻璃板扫描图，程序自动检测棋盘格角点，
以「基础校准输出坐标（soft）」为自变量、固定名义坐标（nominal）为真值，
通过 SVD 刚体配准自动求取每次摆放的旋转/平移（无需用户提供角度），
联合拟合系统级残差层并做留一摆放验证；保存 res_fb_system_*.npz 后
自动加载到主窗口叠加生效。
"""
import os
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QApplication, QDialog, QFileDialog, QFrame, QGridLayout,
                             QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QVBoxLayout)

from core.calibrator import build_ideal_points, detect_chessboard
from core.image_loader import ImageSource
from core.multi_pose_fit import (PoseData, fit_system_residual,
                                 format_report, leave_one_out_validation)
from core.residual_correction import save_residual_correction
from gui.i18n import tr

# 摆放说明：第 1~5 张的摆放方式与命名（角度无需精确，SVD 自动求取）
POSE_GUIDE = [
    # (标签 key, 摆放说明 key, 示意图文件名)；消费处再 tr()，避免模块导入时固化语言
    ("第 1 张", "0° 正放基准位（板边与扫描仪长边大致平行）", "pose_01.png"),
    ("第 2 张", "顺时针旋转约 +5°", "pose_02.png"),
    ("第 3 张", "顺时针旋转约 +10°（A4 大板旋转勿超 10°）", "pose_03.png"),
    ("第 4 张", "逆时针旋转约 -5°", "pose_04.png"),
    ("第 5 张", "0° 正放 + 沿长边平移约 40mm（只平移不旋转）", "pose_05.png"),
]

MIN_POSES_FOR_FIT = 3  # 至少 3 张摆放才可拟合




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


class PoseImportThread(QThread):
    """后台导入单张摆放图：加载图片 → 灰度化 → 棋盘格角点检测 → 基础校准换算。

    超大图检测与数百点逐点 soft 换算均耗时，必须整体在子线程执行。
    """
    done = pyqtSignal(object)  # (path, detected Nx2 像素角点, soft Nx2 mm) 或 Exception

    def __init__(self, path: str, params, to_mm_base, parent=None):
        super().__init__(parent)
        self.path = path
        self.params = params
        self.to_mm_base = to_mm_base  # MainWindow._to_mm_base（像素→基础校准 mm）

    def run(self):
        try:
            src = ImageSource(self.path)
            gray = src.to_gray()
            det = detect_chessboard(gray, self.params)
            if det is None or len(det) == 0:
                self.done.emit(RuntimeError(tr("pose.no_chessboard_corners_detected_check_the_image_and_c")))
                return
            det = det.astype(float)
            if src.scale != 1.0:
                # 超大图按 scale 降采样加载，检测得到的角点在降采样坐标系；
                # 必须还原到原始分辨率坐标后再换算物理坐标（否则坐标域错位，拟合全废）
                det = det * src.scale
            # 软件输出坐标 = 基础校准换算（不含 res_fb，避免与待拟合层叠加）；
            # 逐点换算并入子线程，避免主线程数百毫秒卡顿
            soft = np.asarray([
                self.to_mm_base(float(px), float(py)) for px, py in det
            ], dtype=float)
            self.done.emit((self.path, det, soft))
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)


class PoseFitThread(QThread):
    """拟合线程：SVD 刚体配准 + 系统级残差拟合 + 留一验证，全部在子线程执行。"""
    done = pyqtSignal(object)

    def __init__(self, poses, parent=None):
        super().__init__(parent)
        self.poses = poses

    def run(self):
        try:
            res = fit_system_residual(self.poses)
            if res is None:
                self.done.emit(RuntimeError(tr("pose.fit_failed_insufficient_valid_placements")))
                return
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)


class MultiPoseDialog(QDialog):
    """多摆放系统级残差校正对话框。"""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window  # MainWindow，用于 _to_mm_base 与 res_fb 注入
        self.poses: List[Optional[PoseData]] = [None] * 5
        self.import_paths: List[Optional[str]] = [None] * 5
        self.threads: List[PoseImportThread] = []
        self._fit_thread = None  # PoseFitThread 实例（拟合中非 None）
        self._busy = [False] * 5
        self._rc = None
        self._report = ""
        self.setWindowTitle(tr("pose.multi_pose_system_residual_correction"))
        self.resize(820, 660)
        self.setMinimumSize(680, 520)
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        """按渲染图 01 重构：左侧 5 个摆放卡片（序号/姿态/状态徽章/导入按钮），
        右侧"跳过检测"与"拟合参数"两张卡，底部拟合报告 + 操作按钮。
        注意：控件属性名（btn_import/lbl_state/btn_import_data/btn_fit/btn_save/
        btn_close/report）与原来保持一致，业务逻辑无需改动。"""
        from gui.ui_kit import add_row, btn, card, chip

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- 顶部：说明 + 进度 ----
        top = QHBoxLayout()
        top.setSpacing(10)
        hint = QLabel(tr("pose.scan_the_same_glass_reference_plate_in_the_5_poses_b"))
        hint.setWordWrap(True)
        hint.setObjectName("rowLabel")
        top.addWidget(hint, 1)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("rowValue")
        top.addWidget(self.lbl_progress)
        root.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 5)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        body = QHBoxLayout()
        body.setSpacing(12)

        # ---- 左：摆放卡片 ----
        left = QVBoxLayout()
        left.setSpacing(8)
        self.btn_import: List[QPushButton] = []
        self.lbl_state: List[QLabel] = []
        self._chips: List[QLabel] = []
        for i, (label, desc, _fname) in enumerate(POSE_GUIDE, start=1):
            box = card("")
            cl = box.layout()
            head = QHBoxLayout()
            head.setSpacing(10)
            num = QLabel(str(i))
            num.setObjectName("cardHeader")
            num.setFixedWidth(18)
            head.addWidget(num)
            tcol = QVBoxLayout()
            tcol.setSpacing(2)
            tt = QLabel(tr(label))
            tt.setObjectName("cardHeader")
            dd = QLabel(tr(desc))
            dd.setObjectName("rowLabel")
            tcol.addWidget(tt)
            tcol.addWidget(dd)
            head.addLayout(tcol, 1)
            ch = chip(tr("pose.not_imported"), "muted")
            self._chips.append(ch)
            head.addWidget(ch)
            cl.addLayout(head)

            b = btn(tr("pose.import_pose_n", n=i), "secondary")
            b.clicked.connect(lambda _=False, idx=i - 1: self._on_import(idx))
            self.btn_import.append(b)
            st = QLabel("")
            st.setObjectName("rowLabel")
            st.setWordWrap(True)
            self.lbl_state.append(st)
            cl.addWidget(b)
            cl.addWidget(st)
            left.addWidget(box)
        left.addStretch(1)
        left_host = QWidget()
        left_host.setLayout(left)
        self.scroll_poses = QScrollArea()
        self.scroll_poses.setWidgetResizable(True)
        self.scroll_poses.setFrameShape(QFrame.NoFrame)
        self.scroll_poses.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_poses.setWidget(left_host)
        body.addWidget(self.scroll_poses, 3)

        # ---- 右：跳过检测 + 拟合参数 ----
        right = QVBoxLayout()
        right.setSpacing(10)

        c_skip = card(tr("pose.skip_image_detection"))
        sl = c_skip.layout()
        tip = QLabel(tr("pose.directly_import_chessboard_corner_data_each_row_has_"))
        tip.setObjectName("rowLabel")
        tip.setWordWrap(True)
        sl.addWidget(tip)
        self.btn_import_data = btn(tr("pose.import_chessboard_data_file_skip_image_detection"), "secondary")
        self.btn_import_data.clicked.connect(self._on_import_data)
        sl.addWidget(self.btn_import_data)
        right.addWidget(c_skip)

        c_param = card(tr("pose.fit_params"))
        pl = c_param.layout()
        self.lbl_param_min = add_row(pl, tr("pose.min_control_points"), str(MIN_POSES_FOR_FIT))
        self.lbl_param_order = add_row(pl, tr("pose.residual_order"), tr("pose.residual_order_default"))
        self.lbl_param_loo = add_row(pl, tr("pose.output_loo_rms"), "—")
        right.addWidget(c_param)

        c_state = card(tr("pose.current_state"))
        stl = c_state.layout()
        self.lbl_state_chip = chip(tr("pose.none_ready"), "warn")
        _chiprow = QHBoxLayout()
        _chiprow.addWidget(self.lbl_state_chip)
        _chiprow.addStretch(1)
        stl.addLayout(_chiprow)
        self.lbl_state_hint = QLabel(tr("pose.suggest_at_least_3"))
        self.lbl_state_hint.setObjectName("rowLabel")
        self.lbl_state_hint.setWordWrap(True)
        stl.addWidget(self.lbl_state_hint)
        right.addWidget(c_state)
        right.addStretch(1)
        body.addLayout(right, 2)

        root.addLayout(body, 1)

        # ---- 拟合报告 ----
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setObjectName("fitReport")
        self.report.setPlaceholderText(tr("pose.fit_report_will_appear_here"))
        self.report.setMinimumHeight(80)
        root.addWidget(self.report, 1)

        # ---- 底部按钮 ----
        self.btn_fit = btn(tr("pose.fit_validate"), "primary")
        self.btn_fit.setEnabled(False)
        self.btn_fit.clicked.connect(self._on_fit)
        self.btn_save = btn(tr("pose.save_residual_layer_apply"), "secondary")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_close = btn(tr("pose.close"), "secondary")
        self.btn_close.clicked.connect(self.reject)
        op = QHBoxLayout()
        op.setSpacing(10)
        op.addStretch(1)
        op.addWidget(self.btn_fit)
        op.addWidget(self.btn_save)
        op.addWidget(self.btn_close)
        root.addLayout(op)

        self._sync_progress()

    def _sync_progress(self):
        """同步进度条 / 徽章 / 状态提示（导入与拟合状态变化时调用）。"""
        try:
            n_ok = sum(1 for p in self.poses if p is not None)
            self.progress.setValue(n_ok)
            self.lbl_progress.setText(tr("pose.imported_progress", n=n_ok, total=5))
            for i, ch in enumerate(getattr(self, "_chips", [])):
                ok = self.poses[i] is not None
                self._set_chip(ch, tr("pose.imported") if ok else tr("pose.not_imported"),
                               "ok" if ok else "muted")
            if hasattr(self, "lbl_state_chip"):
                if n_ok >= MIN_POSES_FOR_FIT:
                    self._set_chip(self.lbl_state_chip, tr("pose.n_ready", n=n_ok), "ok")
                elif n_ok > 0:
                    self._set_chip(self.lbl_state_chip, tr("pose.n_ready_need_more", n=n_ok), "warn")
                else:
                    self._set_chip(self.lbl_state_chip, tr("pose.none_ready"), "warn")
            self.btn_fit.setEnabled(n_ok >= MIN_POSES_FOR_FIT)
            self.btn_fit.setText(tr("pose.fit_validate_n_5_imported", n=n_ok)
                                 if n_ok >= MIN_POSES_FOR_FIT else tr("pose.fit_validate"))
            self.btn_save.setEnabled(bool(getattr(self, "_rc", None)))
        except Exception:
            pass

    @staticmethod
    def _set_chip(lab, text: str, kind: str):
        try:
            from gui.ui_kit import set_chip
            set_chip(lab, text, kind)
        except Exception:
            lab.setText(text)

    # ---------------- 导入 ----------------
    def _on_import(self, idx: int):
        if self._busy[idx]:
            return
        path, _ = qfd_open(
            self, tr("pose.import_pose_image_n", n=idx + 1), "",
            tr("pose.image_files_png_jpg_jpeg_bmp_tif_tiff"))
        if not path:
            return
        params = self.window.calib_widget.params()
        self._busy[idx] = True
        self.btn_import[idx].setEnabled(False)
        self.lbl_state[idx].setText(tr("pose.detecting_corners"))
        self._sync_progress()
        th = PoseImportThread(path, params, self.window._to_mm_base, self)
        th.done.connect(lambda res, i=idx: self._on_import_done(i, res))
        self.threads.append(th)
        th.start()

    def _on_import_done(self, idx: int, res):
        self._busy[idx] = False
        self.btn_import[idx].setEnabled(True)
        if isinstance(res, Exception):
            self.lbl_state[idx].setText(tr("pose.import_failed_msg").format(msg=res))
            self._sync_progress()
            return
        path, det, soft_arr = res
        params = self.window.calib_widget.params()
        nominal = build_ideal_points(params)
        if len(det) != len(nominal):
            self.lbl_state[idx].setText(tr(
                "pose.corner_count_mismatch_detected_d_nominal_n_check_row"
            ).format(d=len(det), n=len(nominal)))
            return
        # soft 换算已在子线程完成（基础校准换算，不含 res_fb，避免与待拟合层叠加）
        name = f"pose{idx + 1}_{os.path.basename(path)}"
        self.poses[idx] = PoseData(name=name, soft_mm=soft_arr, nominal_mm=nominal.astype(float))
        self.import_paths[idx] = path
        self.lbl_state[idx].setText(tr("pose.imported_name_n_pts").format(name=os.path.basename(path), n=len(det)))
        self._refresh_fit_state()

    # ---------------- 棋盘格数据直导 ----------------
    def _on_import_data(self):
        """跳过图片检测，直接导入已检测好的棋盘格角点数据文件。

        支持格式（每行对应一个角点，点序与名义点逐行对应）：
          - 2 列：x, y（原始分辨率像素坐标）→ 自动走基础校准换算 soft_mm；
          - 4 列：x, y, soft_x, soft_y（已换算物理坐标 mm，直接使用）。
        文件：.csv / .txt（空白或逗号分隔）/ .npy / .npz。
        """
        path, _ = qfd_open(
            self, tr("pose.import_chessboard_data_file"), "",
            tr("pose.data_files_csv_txt_npy_npz"))
        if not path:
            return
        try:
            pts = self._load_points_file(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("pose.failed_to_read_data_file"), str(e))
            return
        params = self.window.calib_widget.params()
        nominal = build_ideal_points(params)
        if pts.shape[0] != len(nominal):
            QMessageBox.warning(self, tr("pose.corner_count_mismatch"), tr(
                "pose.data_has_d_pts_nominal_n_pts_check_rows_cols_point_o"
            ).format(d=pts.shape[0], n=len(nominal)))
            return
        if pts.shape[1] >= 4:
            # 4 列：直接使用已换算 soft 坐标（第 3、4 列）
            soft = pts[:, 2:4].astype(float)
        else:
            # 2 列：像素坐标 → 基础校准换算
            soft = np.asarray([
                self.window._to_mm_base(float(x), float(y))
                for x, y in pts[:, :2]
            ], dtype=float)
        idx = self._first_free_slot()
        if idx is None:
            QMessageBox.warning(self, tr("pose.full"), tr("pose.all_5_pose_slots_are_already_filled_close_the_dialog"))
            return
        name = f"pose{idx + 1}_data_{os.path.basename(path)}"
        self.poses[idx] = PoseData(name=name, soft_mm=soft, nominal_mm=nominal.astype(float))
        self.import_paths[idx] = path
        self.lbl_state[idx].setText(tr("pose.data_imported_name_n_pts").format(
            name=os.path.basename(path), n=pts.shape[0]))
        self._refresh_fit_state()

    @staticmethod
    def _load_points_file(path: str) -> np.ndarray:
        """读取角点数据文件，返回 Nx2 或 Nx4 数值数组。"""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".npy":
            arr = np.load(path)
        elif ext == ".npz":
            with np.load(path) as z:
                key = next(iter(z.files))
                arr = z[key]
        else:
            arr = np.loadtxt(path, ndmin=2)
        arr = np.asarray(arr, dtype=float)
        if arr.ndim != 2 or arr.shape[1] not in (2, 4):
            raise ValueError(tr("pose.data_must_be_a_2d_array_of_nx2_pixel_x_y_or_nx4_x_y_"))
        return arr

    def _first_free_slot(self) -> Optional[int]:
        for i, p in enumerate(self.poses):
            if p is None:
                return i
        return None

    def _refresh_fit_state(self):
        self._sync_progress()

    # ---------------- 拟合 ----------------
    def _on_fit(self):
        poses = [p for p in self.poses if p is not None]
        if len(poses) < MIN_POSES_FOR_FIT:
            return
        if self._fit_thread is not None and self._fit_thread.isRunning():
            return
        self.btn_fit.setEnabled(False)
        self.btn_fit.setText(tr("pose.fitting"))
        self._fit_thread = PoseFitThread(poses, parent=self)
        self._fit_thread.done.connect(self._on_fit_done)
        self._fit_thread.start()

    def _on_fit_done(self, res):
        # G19: 拒绝旧拟合线程的结果
        if self.sender() is not None and self.sender() is not getattr(self, "_fit_thread", None):
            if getattr(self, "_fit_thread", None) is not None:
                return
        self._fit_thread = None
        self.btn_fit.setText(tr("pose.re_fit_validate"))
        self.btn_fit.setEnabled(True)
        if isinstance(res, Exception):
            QMessageBox.warning(self, tr("pose.fit_failed"), str(res))
            return
        rc, report = res
        self._rc = rc
        self._report = format_report(report)
        self.report.setPlainText(self._report)
        self.btn_save.setEnabled(True)

    # ---------------- 保存 ----------------
    def _on_save(self):
        if self._rc is None:
            return
        default_dir = os.path.dirname(self.import_paths[0]) if self.import_paths[0] else ""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = os.path.join(default_dir, f"res_fb_system_{stamp}.npz")
        path, _ = qfd_save(
            self, tr("pose.save_residual_layer"), default_name, tr("common.residual_correction_files_npz"))
        if not path:
            return
        try:
            save_residual_correction(path, self._rc)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("common.save_failed"), str(e))
            return
        # 自动加载到主窗口叠加生效
        self.window.res_fb = self._rc
        stats = self._rc.stats or {}
        rms_a = stats.get("rms_after_mm")
        extra = tr("pose.compensated_rms_a_4f_mm", a=rms_a) if rms_a is not None else ""
        self.window.statusBar().showMessage(
            tr("pose.multi_pose_system_residual_layer_loaded_path_order_o",
               path=os.path.basename(path), o=self._rc.order, extra=extra))
        self.window.lbl_hint.setText(tr(
            "pose.multi_pose_system_residual_correction_enabled_order_", o=self._rc.order))
        QMessageBox.information(
            self, tr("pose.save_successful"),
            tr("pose.residual_layer_saved_and_applied_path_use_it_directl", path=path))
