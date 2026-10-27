from gui.theme import c as C  # v3.7.2 按主题取色
# -*- coding: utf-8 -*-
"""主窗口：三步走（导入图片 → 定义阵列 → 一键检测导出），棋盘格校准为必需前置。"""
import json
import math
import os
import sys
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import (QDateTime, QSettings, Qt, QThread, QTimer,
                          pyqtSignal)
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (QAction, QApplication, QCheckBox, QComboBox,
                             QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                             QScrollArea, QSpinBox, QSplitter, QTabWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.auto_deskew import deskew_point, estimate_skew_angle
from core.calibrator import apply_correction, auto_detect_chessboard
from core.coordinate_frame import (build_grid_from_frame, build_grid_manual_first,
                                   compare_manual_vs_auto, fit_affine_grid,
                                   validate_frame_vs_pick)
from core.piecewise_correction import apply_piecewise
from core.residual_correction import load_residual_correction
from core.registration import (compare_models, compare_table, load_fit,
                               save_fit, select_model)
from core.config import CrossDetectParams
from core.cross_detector import CrossResult, detect_points, detect_points_fast, refine_manual_point, rematch_results_to_grid
from core.measurement_saver import _refine_win_for
from core.excel_exporter import export_excel
from core.template_parser import parse_template
from core.grid_definition import GridDefinition
from core.image_loader import ImageSource
from core.point_order import (DEFAULT_MODE, POINT_ORDER_MODES, display_no,
                              display_order, mode_label)
from gui.calibration_widget import CalibrationWidget
from gui.i18n import get_lang, set_lang, tr
from gui.multi_pose_dialog import MultiPoseDialog
from gui.image_view import MODE_DRAG, MODE_MANUAL, MODE_NONE, MODE_PICK, ImageView
from gui.theme import QSS, qss_for
# 叠加标记数量上限：超过后抽样绘制。QGraphicsScene 中数万个 item 的创建与重绘
# 会长时间阻塞主线程，表现为“能多动两下又卡死”（大阵列卡死主因之一）。
MAX_OVERLAY_ITEMS = 1200
# 建系正交化(B1)安全上限：点选夹角偏离 90° 超过该值时拒绝正交化并告警。
# 理由：偏离过大说明点选本身有误，或可能隐含真实方形度误差，静默收拢会掩盖问题
# （监督 #3 复核要求：必须有此护栏，防止"真不方度被吃掉"）。
ORTHO_MAX_DEV_DEG = 1.0

# B4：阵列规格下拉档位（奇数 N×N），补齐 7×7/13×13/15×15 等奇数档。
# 条目为纯数字 + 乘号，无需翻译；"自定义" 项在填充时用 tr() 拼接，
# 其索引以 len(ARRAY_SPECS) 对齐，避免硬编码错位。
ARRAY_SPECS = ["3×3", "5×5", "7×7", "9×9", "11×11", "13×13", "15×15", "17×17"]

# P0-3：日志落盘。原实现 _log 只 appendPlainText 到界面控件，进程退出即丢失，
# 故障无法事后取证（三测「自动精化未生效」只能靠反解数据推断，就是这个原因）。
_LOG_FILE_PATH = None


def _log_file_path() -> str:
    """日志文件路径：exe 同目录 logs/app_YYYYMMDD.log；不可写时回退用户临时目录。"""
    global _LOG_FILE_PATH
    if _LOG_FILE_PATH:
        return _LOG_FILE_PATH
    import datetime as _dt
    import tempfile
    name = "app_{}.log".format(_dt.datetime.now().strftime("%Y%m%d"))
    cands = []
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cands.append(os.path.join(base, "logs"))
    except Exception:
        pass
    cands.append(os.path.join(tempfile.gettempdir(), "GalvoCalibVision_logs"))
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, name)
            with open(p, "a", encoding="utf-8"):
                pass
            _LOG_FILE_PATH = p
            return p
        except Exception:
            continue
    return ""


def _write_log_file(line: str) -> None:
    """把一行日志追加进日志文件；任何失败静默（落盘不得影响主流程）。"""
    try:
        p = _log_file_path()
        if not p:
            return
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass



def _uniform_form(form, label_w=112, field_w=148):
    """把 QFormLayout 排成统一节奏：标签定宽右对齐、字段定宽。
    效果图就是这样——行高一致、右边缘齐平，而不是每个控件自适应宽度。"""
    from PyQt5.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout,
                                 QLineEdit, QSpinBox)
    try:
        from PyQt5.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout,
                                     QLineEdit, QSizePolicy, QSpinBox)
        from PyQt5.QtCore import Qt as _Qt
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
                # 定宽 + 数值右对齐：既能贴右边又不被裁（Expanding 会溢出卡片被切）
                fw.setFixedWidth(148)
                if isinstance(fw, (QSpinBox, QDoubleSpinBox)):
                    fw.setAlignment(_Qt.AlignRight | _Qt.AlignVCenter)
    except Exception:
        pass


def _system_ram_gb() -> float:
    """读取系统物理内存（GB），用于“默认整图处理”的内存保护；失败返回 0。"""
    try:
        import ctypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = _MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullTotalPhys / (1024.0 ** 3)
    except Exception:
        pass
    return 0.0




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


class DetectorThread(QThread):
    progress = pyqtSignal(float)
    done = pyqtSignal(object)
    error = pyqtSignal(str)  # 异常兜底信号：检测线程任何异常都经此上报，严禁静默失败

    def __init__(self, src: ImageSource, points, pitch_px, params: CrossDetectParams,
                 max_workers: int = 1, parent=None, ref_angle_deg: Optional[float] = None):
        super().__init__(parent)
        self.src = src
        self.points = points          # 原始图像坐标（像素）
        self.pitch_px = pitch_px      # 原始图像间距（像素）
        self.params = params
        self.max_workers = max_workers
        self.ref_angle_deg = ref_angle_deg  # 方向约束：以实测网格方向为基准（None=不约束）
        self.results: List[Optional[CrossResult]] = []

    def run(self):
        # 检测始终在【原始分辨率】灰度图上进行：
        # 即使预览/整图因超大而降采样，也通过 original_gray() 加载原始分辨率灰度，
        # 配合降采样小图预检 + 原图小窗精测（detect_points_fast 两阶段），
        # 保证每个十字中心都来自原始扫描图像的真实识别，不因 scale 损失精度。
        try:
            gray = self.src.original_gray()
            self.results = detect_points_fast(
                gray, self.points, self.pitch_px, self.params,
                precheck_gray=self.src.precheck_gray(),
                precheck_scale=self.src.precheck_scale(),
                max_workers=self.max_workers, progress_cb=self.progress.emit,
                ref_angle_deg=self.ref_angle_deg)
            self.done.emit(self.results)
        except Exception:
            # 无异常兜底时 done 永不发出，界面永久卡在"重新检测中…"。
            # 捕获后经 error 信号交主线程恢复 UI，并落盘 traceback 供排查。
            import traceback
            import time
            tb = traceback.format_exc()
            try:
                tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp")
                os.makedirs(tmp_dir, exist_ok=True)
                log_path = os.path.join(tmp_dir, "detect_error_traceback.log")
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write("\n===== %s =====\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), tb))
            except Exception:
                pass
            self.error.emit(tb)


class GrayLoaderThread(QThread):
    """后台加载原始分辨率灰度图（手动识别吸附精化用），避免大图解码阻塞 UI。"""
    done = pyqtSignal(object)

    def __init__(self, src: ImageSource, parent=None):
        super().__init__(parent)
        self.src = src

    def run(self):
        try:
            self.done.emit(self.src.original_gray())
        except Exception as e:
            self.done.emit(e)


class DeskewAngleThread(QThread):
    """B1 后台估摆正角：用预检小图（约 1/8 降采样灰度）估整幅十字阵列方向角。

    估角只进换算层旋转矩阵、不物理旋转图像（红线 5），故用轻量预检图即可，
    无需与检测线程争抢全分辨率 original_gray 解码锁。
    """
    done = pyqtSignal(object)  # float 角度（度）或 None（未检出）或 Exception

    def __init__(self, src: ImageSource, parent=None):
        super().__init__(parent)
        self.src = src

    def run(self):
        try:
            ang = estimate_skew_angle(self.src.precheck_gray())
            self.done.emit(ang)
        except Exception as e:
            self.done.emit(e)


class ImageLoaderThread(QThread):
    """后台加载图片（构造 ImageSource + 生成预览），避免超大图解码阻塞 UI。

    修复背景：旧实现 _load_image 在主线程同步执行 ImageSource()，超大 PNG（如
    4800dpi 全幅约 22 亿像素）的 probe 与降采样加载需反复全量解压文件，耗时可达
    数十秒，期间窗口无响应、按钮点不动，表现为“程序干死”。改后台线程后 UI 始终
    可交互，状态栏给出加载提示。
    """
    done = pyqtSignal(object)  # (ImageSource, preview) 或 Exception

    def __init__(self, path: str, use_full: bool, parent=None):
        super().__init__(parent)
        self.path = path
        self.use_full = use_full

    def run(self):
        try:
            src = ImageSource(self.path, use_full=self.use_full)
            self.done.emit((src, src.preview()))
        except Exception as e:
            self.done.emit(e)


class AutoFrameThread(QThread):
    """主窗口「① 自动建系(角点)」的后台检测线程。

    修复背景：旧实现 _on_auto_frame 在 GUI 主线程同步执行
    auto_detect_chessboard + fit_affine_grid，4800dpi 大图上角点法/旋转回正
    兜底链耗时数十秒，期间窗口无响应，表现为“卡在棋盘格找角点”。搬进
    QThread 后 UI 始终可交互，完成经 done 信号回主线程写网格。
    """
    done = pyqtSignal(object)  # (AutoChessDetectResult, AffineFitResult, origin_xy) 或 AutoChessDetectResult(失败) 或 Exception

    def __init__(self, src: ImageSource, square_mm: float, dpi: float,
                 params: CrossDetectParams, parent=None):
        super().__init__(parent)
        self.src = src
        self.square_mm = square_mm
        self.dpi = dpi
        self.params = params

    def run(self):
        try:
            gray = self.src.original_gray()
            res = auto_detect_chessboard(gray, square_mm=self.square_mm,
                                         dpi=self.dpi, pattern="auto")
            if not res.ok or res.corners is None or len(res.corners) < 4:
                self.done.emit(res)
                return
            fit = fit_affine_grid(res.corners, res.rows, res.cols)
            origin_xy = self._refine_center_origin(gray, fit)
            self.done.emit((res, fit, origin_xy))
        except Exception as e:  # noqa: BLE001
            self.done.emit(e)

    def _refine_center_origin(self, gray, fit):
        """B3：对阵列几何中心十字做亚像素精测，返回精测像素坐标 (x, y)；失败返回 None。

        以仿射拟合几何中心为搜索中心，开 pitch_px × search_window_scale 局部窗口，
        复用 detect_points 单点精测（投影质心/边缘中心 + 整图坐标叠加），
        避免直接用粗搜像素级坐标当原点。奇数阵中心十字为物理原点锚点。
        """
        if not getattr(fit, "ok", False):
            return None
        cc = fit.cols // 2
        rc = fit.rows // 2
        cx = float(fit.origin[0] + fit.a[0] * cc + fit.b[0] * rc)
        cy = float(fit.origin[1] + fit.a[1] * cc + fit.b[1] * rc)
        pitch_px = float((fit.pitch_x_px + fit.pitch_y_px) / 2.0)
        if pitch_px <= 1e-6:
            return None
        ref = float(np.degrees(np.arctan2(fit.a[1], fit.a[0])))
        try:
            res = detect_points(gray, np.array([[cx, cy]], np.float64),
                                pitch_px, self.params, ref_angle_deg=ref)
        except Exception:  # noqa: BLE001
            return None
        r0 = res[0] if res else None
        if r0 is not None and r0.ok:
            return (float(r0.x), float(r0.y))
        return None


class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str)  # A1：跨线程 log 回主线程追加（QThread 内禁止直接写 UI）

    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("main.galvo_calibration_vision_software"))
        self.resize(1400, 880)
        self.setMinimumSize(1120, 700)
        self.src: Optional[ImageSource] = None
        self.grid: Optional[GridDefinition] = None
        self.results: List[Optional[CrossResult]] = []
        # P0-3：本次检测的诊断记录（网格校正/精化决策），导出报表“参数记录”一并落盘，
        # 便于现场故障取证（失败时不复现问题也能看到链路走到哪一步）。
        self._diag: dict = {}
        self.calibrated = False
        self.coeff_x = None
        self.coeff_y = None
        self.res_fb = None  # 外部基准残差校正层（二次元实测拟合，叠加在 _to_mm 输出上）
        self.reg_fit = None  # B9 配准模型（实测点→名义网格，叠加在 _to_mm_base 末尾）
        self.reg_frame_id = None  # 幅面指纹（红线 9 禁止跨幅复用）
        self.reg_kind = None  # 当前配准模型类型（affine/homography/poly2/tps）
        self._deskew_ang = None      # B1 摆正角（度，[-45,45)），None=未检出/未启用
        self._deskew_shape = None    # B1 旋转中心参考尺寸 (h, w)，原始灰度系
        self._deskew_loader = None   # B1 估角后台线程引用（结果作废判定用）
        self.origin_xy = None        # B3 会话级原点：阵列几何中心十字亚像素精测坐标(px, 原始灰度系)；建系后禁止覆盖
        self.order = 2
        self.tol_upper_mm = 0.05   # 分量公差：上公差（mm），可从模板读取
        self.tol_lower_mm = -0.05  # 分量公差：下公差（mm），可从模板读取
        self.template_meta = None  # 已加载模板的表头元数据（dict）
        self.template_path = None  # 已加载模板路径
        # 模板 D 列固定名义值 26 点（P1 + P3..P27）；未加载模板时为 None
        self.template_nominal_points = None
        self._pick_state = -1  # 0=起始点 1=X方向 2=Y方向
        # 定长 3：None 表示该点未点选；独立清除按钮只清对应项
        self._picked: List[Optional[Tuple[float, float]]] = [None, None, None]
        # 手动识别模式状态
        self._manual_active = False
        self._manual_history: List[Tuple[int, Optional[CrossResult]]] = []  # (idx, 修改前值)
        self._auto_snapshot: Optional[List[Optional[CrossResult]]] = None  # 进入手动模式时的自动结果快照
        self._manual_sel_idx = -1
        self._manual_gray = None
        self._gray_loader: Optional[GrayLoaderThread] = None
        # 编号顺序模式（4 种，见 core/point_order.py）；默认左上起行主序（与旧版一致）
        self.order_mode = DEFAULT_MODE
        # A1：跨线程 log 信号 → _log；全局异常兜底进 log（保留原 excepthook 兜底）
        self.log_signal.connect(self._log)
        self._prev_excepthook = sys.excepthook
        sys.excepthook = self._excepthook
        # v3.5：主题（浅色/深色）在构建 UI 前应用，并持久化到用户配置
        self._theme_mode = self._load_theme_mode()
        self._apply_theme_global()
        self._build_ui()
        self._refresh_flow()
        self._set_calibrated(False)
        # P0-3：启动即写首条日志。日志文件为懒加载，若不主动写一条，
        # 现场在未点检测前根本看不到 logs 目录，无法确认落盘通道是否可用。
# v3.6.1: 启动后再校准一次状态卡（此时所有控件已就绪）
        self._log(tr("main.app_startup_version"))
        self._update_status_card()
        self._sync_collapse_label()

    def _log(self, msg: str):
        """A1 基础设施：log 栏追加（时间戳 + 消息，自动滚动到底）+ 日志文件落盘。

        主线程回调内直接调用安全；QThread 内经 log_signal.emit 回主线程。
        """
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        line = "[{}] {}".format(ts, msg)
        view = getattr(self, "log_view", None)
        if view is not None:
            view.appendPlainText(line)
            sb = view.verticalScrollBar()
            sb.setValue(sb.maximum())
        _write_log_file(line)

    def _excepthook(self, exc_type, exc_value, exc_tb):
        """全局未处理异常兜底：写 log 栏并交原 excepthook（不静默吞异常）。"""
        import traceback
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        self._log("未处理异常：\n" + tb)
        self._prev_excepthook(exc_type, exc_value, exc_tb)

    DEFAULT_HIDDEN_COLS = (3, 4, 7, 8, 9, 10, 13)

    def _apply_default_columns(self):
        try:
            for c in self.DEFAULT_HIDDEN_COLS:
                self.table.setColumnHidden(c, True)
        except Exception:
            pass

    def _wire_menu_corner(self):
        """把语言下拉与"关于"放到菜单栏右上角（方案A 的头部布局）。"""
        try:
            corner = QWidget()
            chb = QHBoxLayout(corner)
            chb.setContentsMargins(0, 0, 10, 0)
            chb.setSpacing(8)
            chb.addWidget(self.lbl_lang)
            chb.addWidget(self.cb_lang)
            if getattr(self, "btn_about_link", None) is None:
                self.btn_about_link = QPushButton(tr("main.about"))
                self.btn_about_link.setObjectName("btnGhost")
                self.btn_about_link.setCursor(Qt.PointingHandCursor)
                self.btn_about_link.clicked.connect(self._on_about)
            chb.addWidget(self.btn_about_link)
            self.lbl_lang.show()
            self.cb_lang.show()
            self.menuBar().setCornerWidget(corner)
        except Exception:
            pass

    def _sync_collapse_label(self):
        """折叠行统一显示 "> 标题" / "v 标题"（不靠 checked 上色）。"""
        for btn, base in (
            (getattr(self, "btn_collapse_detect", None), tr("main.detection_settings")),
            (getattr(self, "btn_collapse_ref", None), tr("main.pick_reference_points")),
        ):
            if btn is None:
                continue
            try:
                btn.setText((">  " if btn.isChecked() else "v  ") + base)
            except Exception:
                pass

    def _on_log_toggle(self, checked=False):
        """日志抽屉展开/收起（只改可见性，不动日志内容）。"""
        try:
            self.log_view.setVisible(bool(checked))
            self.btn_log_toggle.setText(tr("main.log_collapse") if checked else tr("main.log_expand"))
        except Exception:
            pass

    def _on_column_settings(self):
        """列设置对话框（v3.8 重构）：预设 + 全选/全不选 + 带状态徽章的列清单。"""
        try:
            from gui import ui_kit
            from PyQt5.QtWidgets import QCheckBox, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget
            dlg = ui_kit.dialog(self, tr("main.column_settings"), 520, 560)
            lay = dlg.layout()
            hh = self.table.horizontalHeader()
            n = self.table.columnCount()
            hidden_default = getattr(self, "_default_hidden_cols", None) or (3, 4, 7, 8, 9, 10, 13)

            # 预设行
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(QLabel(tr("kit.column_preset_default")))
            row.addStretch(1)
            lay.addLayout(row)

            inner = QWidget()
            il = QVBoxLayout(inner)
            il.setContentsMargins(4, 4, 4, 4)
            il.setSpacing(6)
            boxes = []
            chips = []
            for c in range(n):
                line = QHBoxLayout()
                line.setSpacing(10)
                cb = QCheckBox(str(hh.model().headerData(c, 1)))
                cb.setChecked(not self.table.isColumnHidden(c))
                ch = ui_kit.chip(tr("kit.show") if cb.isChecked() else tr("kit.hide"),
                                 "ok" if cb.isChecked() else "muted")
                cb.toggled.connect(lambda on, _ch=ch: ui_kit.set_chip(
                    _ch, tr("kit.show") if on else tr("kit.hide"), "ok" if on else "muted"))
                boxes.append((c, cb))
                chips.append(ch)
                line.addWidget(cb, 1)
                line.addWidget(ch)
                il.addLayout(line)
            il.addStretch(1)
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(inner)
            lay.addWidget(sa, 1)

            # 预设 / 全选 / 全不选
            pre = QHBoxLayout()
            pre.setSpacing(6)
            b_def = ui_kit.btn(tr("kit.column_preset_default"), "secondary")
            b_all = ui_kit.btn(tr("kit.column_all"), "secondary")
            b_min = ui_kit.btn(tr("kit.column_min"), "secondary")
            b_sel = ui_kit.btn(tr("kit.select_all"), "ghost")
            b_non = ui_kit.btn(tr("kit.select_none"), "ghost")
            b_def.clicked.connect(lambda: [cb.setChecked(c not in hidden_default) for c, cb in boxes])
            b_all.clicked.connect(lambda: [cb.setChecked(True) for _c, cb in boxes])
            b_min.clicked.connect(lambda: [cb.setChecked(c < 4) for c, cb in boxes])
            b_sel.clicked.connect(lambda: [cb.setChecked(True) for _c, cb in boxes])
            b_non.clicked.connect(lambda: [cb.setChecked(False) for _c, cb in boxes])
            for b in (b_def, b_all, b_min):
                pre.addWidget(b)
            pre.addStretch(1)
            pre.addWidget(b_sel)
            pre.addWidget(b_non)
            lay.addLayout(pre)

            note = QLabel(tr("kit.column_hidden_note"))
            note.setObjectName("rowLabel")
            lay.addWidget(note)
            lay.addWidget(ui_kit.button_box(dlg, tr("kit.ok"), tr("kit.cancel")))
            if os.environ.get("GCV_NOEXEC"):
                self._last_dialog = dlg
                return
            if dlg.exec_() == dlg.Accepted:
                for c, cb in boxes:
                    self.table.setColumnHidden(c, not cb.isChecked())
        except Exception:
            pass

    # ---------------- 主题（v3.5：白天/黑夜） ----------------
    def _load_theme_mode(self):
        try:
            v = QSettings("GalvoCalibVision", "UI").value("theme", "light")
            return "dark" if str(v).lower() == "dark" else "light"
        except Exception:
            return "light"

    def _save_theme_mode(self, mode):
        try:
            QSettings("GalvoCalibVision", "UI").setValue("theme", mode)
        except Exception:
            pass

    def _apply_theme_global(self):
        try:
            from PyQt5.QtWidgets import QApplication
            import gui.theme as _theme
            # 让代码内取色（C("ok") 等）与样式表主题保持一致
            _theme.set_mode(self._theme_mode)
            app = QApplication.instance()
            sheet = qss_for(self._theme_mode)
            if app is not None:
                app.setStyleSheet(sheet)
                # v3.7.4：样式表只覆盖"被 QSS 显式画背景"的控件；QTabWidget 页、
                # 滚动区内容等通用容器用的是调色板，调色板不改就一直是浅色。
                from PyQt5.QtGui import QColor, QPalette
                if self._theme_mode == "dark":
                    pal = QPalette()
                    pal.setColor(QPalette.Window, QColor("#0f1319"))
                    pal.setColor(QPalette.WindowText, QColor("#e6ebf2"))
                    pal.setColor(QPalette.Base, QColor("#1b2129"))
                    pal.setColor(QPalette.AlternateBase, QColor("#20262f"))
                    pal.setColor(QPalette.Text, QColor("#e6ebf2"))
                    pal.setColor(QPalette.Button, QColor("#1b2129"))
                    pal.setColor(QPalette.ButtonText, QColor("#e6ebf2"))
                    pal.setColor(QPalette.ToolTipBase, QColor("#1b2129"))
                    pal.setColor(QPalette.ToolTipText, QColor("#e6ebf2"))
                    pal.setColor(QPalette.Highlight, QColor("#2563eb"))
                    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
                    pal.setColor(QPalette.BrightText, QColor("#ffffff"))
                    pal.setColor(QPalette.PlaceholderText, QColor("#7c8899"))
                    for _role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
                        pal.setColor(QPalette.Disabled, _role, QColor("#8b95a5"))
                else:
                    # 不能在设过样式表之后用 app.style().standardPalette()：
                    # 实测它返回 Window=#000000 的异常调色板，切回浅色会把页面刷黑。
                    pal = QPalette()
                    pal.setColor(QPalette.Window, QColor("#eef1f6"))
                    pal.setColor(QPalette.WindowText, QColor("#1f2d3d"))
                    pal.setColor(QPalette.Base, QColor("#ffffff"))
                    pal.setColor(QPalette.AlternateBase, QColor("#f8fafc"))
                    pal.setColor(QPalette.Text, QColor("#1f2d3d"))
                    pal.setColor(QPalette.Button, QColor("#ffffff"))
                    pal.setColor(QPalette.ButtonText, QColor("#1f2d3d"))
                    pal.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
                    pal.setColor(QPalette.ToolTipText, QColor("#1f2d3d"))
                    pal.setColor(QPalette.Highlight, QColor("#3b82f6"))
                    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
                    pal.setColor(QPalette.BrightText, QColor("#1f2d3d"))
                    pal.setColor(QPalette.PlaceholderText, QColor("#9aa8ba"))
                    for _role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
                        pal.setColor(QPalette.Disabled, _role, QColor("#9aa8ba"))
                app.setPalette(pal)
                # QTabWidget / QScrollArea 等容器不会自动跟随应用调色板（实测仍为浅色），
                # 显式下发给页签页与滚动视口，否则左栏在深色下一直是浅底深字。
                try:
                    from PyQt5.QtWidgets import QScrollArea, QTabWidget
                    self.setPalette(pal)
                    for _tw in self.findChildren(QTabWidget):
                        _tw.setPalette(pal)
                        for _i in range(_tw.count()):
                            _pg = _tw.widget(_i)
                            if _pg is not None:
                                _pg.setPalette(pal)
                    for _sa in self.findChildren(QScrollArea):
                        _sa.setPalette(pal)
                        _sa.viewport().setPalette(pal)
                    # 页签内部是 QStackedWidget，不跟着应用调色板走（实测仍浅色）
                    from PyQt5.QtWidgets import QStackedWidget
                    for _sw in self.findChildren(QStackedWidget):
                        _sw.setPalette(pal)
                        for _i2 in range(_sw.count()):
                            _pw = _sw.widget(_i2)
                            if _pw is not None:
                                _pw.setPalette(pal)
                except Exception:
                    pass
                # v3.9.11 palette fan-out to EVERY child
                try:
                    from PyQt5.QtWidgets import QWidget as _QW
                    for _w2 in self.findChildren(_QW):
                        try:
                            _w2.setPalette(pal)
                        except Exception:
                            pass
                except Exception:
                    pass
            self.setStyleSheet("")
            try:
                if hasattr(self, "view"):
                    self.view.set_dark(self._theme_mode == "dark")
            except Exception:
                pass
            # 富文本/表格前景色是"烤进文本"的，切主题后必须重建一次
            # 轻量刷新：只请求重绘，不做全树 unpolish（全树 polish 会卡顿数秒）
            self.update()
            for _fn in ("_update_summary", "_refresh_flow", "_update_calib_badge"):
                try:
                    getattr(self, _fn)()
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_theme(self, mode=None):
        """切换并持久化主题；只换配色，不改任何控件结构与状态。"""
        self._theme_mode = mode or getattr(self, "_theme_mode", "light")
        self._apply_theme_global()
        try:
            act = getattr(self, "act_dark", None)
            if act is not None:
                act.setChecked(self._theme_mode == "dark")
        except Exception:
            pass
        self._save_theme_mode(self._theme_mode)
        return self._theme_mode

    def _on_toggle_theme(self, checked=False):
        self._apply_theme("dark" if checked else "light")
        self._log(tr("main.theme_switched", mode=tr("main.theme_dark") if checked else tr("main.theme_light")))

    def _on_manual(self):
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices
            from gui.manual import write_manual
            p = write_manual(get_lang())
            if p and os.path.isfile(p):
                QDesktopServices.openUrl(QUrl.fromLocalFile(p))
                self.statusBar().showMessage(tr("main.help_manual_opened", path=p), 6000)
            else:
                QMessageBox.information(self, tr("main.help_manual"), tr("main.help_manual_unavailable"))
        except Exception as e:
            try:
                QMessageBox.warning(self, tr("main.help_manual"), str(e))
            except Exception:
                pass

    def _on_about(self):
        """帮助 → 关于（v3.8 重构）：卡片式，含版本、构建信息、日志/崩溃文件路径。"""
        try:
            import datetime
            import sys as _sys
            from gui import ui_kit
            from PyQt5.QtWidgets import QHBoxLayout, QLabel

            dlg = ui_kit.dialog(self, tr("kit.about_title"), 480)
            lay = dlg.layout()

            head = QHBoxLayout()
            head.setSpacing(14)
            logo = QLabel("GCV")
            logo.setObjectName("cardHeader")
            logo.setFixedSize(64, 64)
            logo.setObjectName("aboutLogo")
            from PyQt5.QtCore import Qt as _Qt
            logo.setAlignment(_Qt.AlignCenter)
            head.addWidget(logo)
            col = QVBoxLayout()
            col.setSpacing(2)
            name = QLabel("GalvoCalibVision")
            name.setObjectName("cardHeader")
            ver = QLabel(tr("main.app_startup_version"))
            ver.setObjectName("rowLabel")
            col.addWidget(name)
            col.addWidget(ver)
            head.addLayout(col, 1)
            lay.addLayout(head)

            box = ui_kit.card(tr("kit.log_dir"))
            bl = box.layout()
            base = os.path.dirname(_sys.executable) if getattr(_sys, "frozen", False) \
                else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base, "logs")
            ui_kit.add_row(bl, tr("kit.log_dir"), "logs/app_*.log")
            ui_kit.add_row(bl, tr("kit.crash_dir"), "logs/crash_*.txt")
            ui_kit.add_row(bl, "Python", _sys.version.split()[0])
            lay.addWidget(box)
            lay.addStretch(1)

            b_open = ui_kit.btn(tr("kit.open_log_dir"), "secondary")
            b_copy = ui_kit.btn(tr("kit.copy_version"), "secondary")
            b_ok = ui_kit.btn(tr("kit.ok"), "primary")
            b_open.clicked.connect(lambda: self._open_dir(log_dir))
            b_copy.clicked.connect(lambda: self._copy_text(
                "GalvoCalibVision\n%s\nlogs: %s" % (tr("main.app_startup_version"), log_dir)))
            b_ok.clicked.connect(dlg.accept)
            lay.addLayout(ui_kit.btn_row(b_open, b_copy, b_ok))
            if os.environ.get("GCV_NOEXEC"):
                self._last_dialog = dlg
                return
            dlg.exec_()
        except Exception:
            try:
                QMessageBox.information(self, tr("main.about"), tr("main.about_text"))
            except Exception:
                pass

    def _open_dir(self, path: str):
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices
            os.makedirs(path, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            pass

    def _copy_text(self, text: str):
        try:
            from PyQt5.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage(tr("kit.copied"), 4000)
        except Exception:
            pass

    # ---------------- UI ----------------
    def _build_ui(self):
        self._main_toolbar = tb = self.addToolBar(tr("main.main_toolbar"))
        tb.setMovable(False)
        act_open = QAction(tr("main.import_scan"), self); act_open.triggered.connect(self._on_open_image)
        act_save_proj = QAction(tr("main.save_project"), self); act_save_proj.triggered.connect(self._on_save_project)
        act_load_proj = QAction(tr("main.load_project"), self); act_load_proj.triggered.connect(self._on_load_project)
        act_export = QAction(tr("main.export_excel"), self); act_export.triggered.connect(self._on_export)
        act_load_tpl = QAction(tr("main.load_template"), self); act_load_tpl.triggered.connect(self._on_load_template)
        act_load_resfb = QAction(tr("main.load_residual_correction"), self)
        act_load_resfb.triggered.connect(self._on_load_residual_correction)
        act_load_resfb.setToolTip(tr("main.load_residual_correction_file_res_fb_npz_fitted_from"))
        act_multi_pose = QAction(tr("main.multi_pose_residual_correction"), self)
        act_multi_pose.triggered.connect(self._on_multi_pose_correction)
        act_multi_pose.setToolTip(tr("main.import_1_5_scans_of_the_reference_plate_in_different"))
        act_reg = QAction(tr("main.same_frame_registration"), self)
        act_reg.triggered.connect(self._on_fit_registration)
        act_reg.setToolTip(tr("main.same_frame_registration_tip"))
        act_load_reg = QAction(tr("main.load_registration_model"), self)
        act_load_reg.triggered.connect(self._on_load_registration)
        act_load_reg.setToolTip(tr("main.load_registration_model_tip"))
        act_clear_reg = QAction(tr("main.clear_registration_model"), self)
        act_clear_reg.triggered.connect(self._on_clear_registration)
        act_clear_reg.setToolTip(tr("main.clear_registration_model_tip"))
        # v3.4 信息架构：原先 10 个动作平铺在一条工具栏上（4+3+3 三段），
        # 现在收进 3 个菜单（文件 / 工具 / 帮助），工具栏只保留最高频的导入与导出。
        # 动作对象与 connect 全部沿用，功能不变，只改归属。
        # v3.5：工具栏不再承载动作（全部动作已收进 文件/工具/帮助 菜单），
        # 直接隐藏工具栏，把垂直空间让给预览区；高频动作用快捷键。
        tb.setVisible(False)
        act_open.setShortcut("Ctrl+O")
        act_export.setShortcut("Ctrl+E")
        act_save_proj.setShortcut("Ctrl+S")
        mb = self.menuBar()
        m_file = mb.addMenu(tr("main.menu_file"))
        for a in (act_open, act_save_proj, act_load_proj, act_export):
            m_file.addAction(a)
        m_tools = mb.addMenu(tr("main.menu_tools"))
        for a in (act_load_tpl, act_load_resfb, act_multi_pose):
            m_tools.addAction(a)
        m_tools.addSeparator()
        for a in (act_reg, act_load_reg, act_clear_reg):
            m_tools.addAction(a)
        m_help = mb.addMenu(tr("main.menu_help"))
        # v3.9 方案A：语言切换与"关于"放到菜单栏右上角
        try:
            _corner = QWidget()
            _chb = QHBoxLayout(_corner)
            _chb.setContentsMargins(0, 0, 10, 0)
            _chb.setSpacing(8)
            _chb.addWidget(self.lbl_lang)
            _chb.addWidget(self.cb_lang)
            self.btn_about_link = QPushButton(tr("main.about"))
            self.btn_about_link.setObjectName("btnGhost")
            self.btn_about_link.setCursor(Qt.PointingHandCursor)
            self.btn_about_link.clicked.connect(self._on_about)
            _chb.addWidget(self.btn_about_link)
            self.menuBar().setCornerWidget(_corner)
        except Exception:
            pass
        act_manual = QAction(tr("main.help_manual"), self)
        act_manual.setShortcut("F1")
        act_manual.triggered.connect(self._on_manual)
        m_help.addAction(act_manual)
        act_about = QAction(tr("main.about"), self)
        act_about.triggered.connect(self._on_about)
        m_help.addAction(act_about)
        self.act_manual = act_manual
        self.act_about = act_about
        act_columns = QAction(tr("main.column_settings"), self)
        act_columns.triggered.connect(self._on_column_settings)
        m_tools.addSeparator()
        m_tools.addAction(act_columns)
        self.act_columns = act_columns
        act_dark = QAction(tr("main.dark_mode"), self)
        act_dark.setCheckable(True)
        act_dark.setChecked(getattr(self, "_theme_mode", "light") == "dark")
        act_dark.toggled.connect(self._on_toggle_theme)
        m_tools.addAction(act_dark)
        self.act_dark = act_dark
        self._menu_file, self._menu_tools, self._menu_help = m_file, m_tools, m_help
        self.act_export = act_export
        self.act_load_tpl = act_load_tpl
        self.act_load_resfb = act_load_resfb
        self.act_multi_pose = act_multi_pose
        self.act_reg = act_reg
        self.act_load_reg = act_load_reg
        self.act_clear_reg = act_clear_reg
        self.act_open, self.act_save_proj, self.act_load_proj = act_open, act_save_proj, act_load_proj

        # 语言切换
        tb.addSeparator()
        self.lbl_lang = QLabel(tr("main.language")); tb.addWidget(self.lbl_lang)
        self.cb_lang = QComboBox()
        self.cb_lang.addItems([tr("main.language_chinese"), tr("main.english")])
        self.cb_lang.setCurrentIndex(0 if get_lang() == "zh" else 1)
        self.cb_lang.setToolTip(tr("main.switch_ui_language"))
        self.cb_lang.currentIndexChanged.connect(self._on_lang_changed)
        tb.addWidget(self.cb_lang)

        self.view = ImageView(self)
        self.view.pointPicked.connect(self._on_point_picked)
        self.view.pointDragged.connect(self._on_point_dragged)
        self.view.dragFinished.connect(self._on_drag_finished)
        self.view.manualPicked.connect(self._on_manual_picked)
        self.view.manualNudge.connect(self._on_manual_nudge)
        self.view.undoRequested.connect(self._on_undo_manual)

        # 左面板
        self.calib_widget = CalibrationWidget(self)
        self.calib_widget.passed.connect(self._set_calibrated)
        self.calib_widget.message.connect(self.statusBar().showMessage)
        self.calib_widget.previewRequest.connect(self._show_calib_preview)
        self.calib_widget.progress.connect(self._on_calib_progress)
        self.calib_widget.stage.connect(self.statusBar().showMessage)
        self.calib_widget.cornersFound.connect(self._on_calib_corners)
        self.calib_widget.extentFound.connect(self._on_calib_extent)

        # ============ 阵列与检测参数（步骤卡片布局） ============
        # ① 定义阵列
        g = QGroupBox(tr("main.define_array")); self._grp_def = g
        f = QFormLayout(g)
        f.setVerticalSpacing(5); f.setHorizontalSpacing(10)
        # v3.6.1：标签右对齐 + 数值框限宽（原来输入框横跨整栏，值左对齐显散）
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.cb_spec = QComboBox()
        # v3.6.1：填充预设时屏蔽信号，避免初始化阶段把行列改成 3×3（与状态卡不一致）
        self.cb_spec.blockSignals(True)
        self.cb_spec.addItems(ARRAY_SPECS + [tr("main.custom")])
        self.cb_spec.blockSignals(False)
        self.cb_spec.setCurrentIndex(1)  # 默认 5×5，与行列默认值一致
        self.cb_spec.currentTextChanged.connect(self._on_spec_changed)
        self.sp_rows = QSpinBox(); self.sp_rows.setRange(1, 99); self.sp_rows.setValue(5)
        self.sp_cols = QSpinBox(); self.sp_cols.setRange(1, 99); self.sp_cols.setValue(5)
        self.sp_pitch = QDoubleSpinBox(); self.sp_pitch.setRange(0.1, 500.0)
        self.sp_pitch.setValue(20.0); self.sp_pitch.setDecimals(2); self.sp_pitch.setSuffix(" mm")
        self.sp_dpi = QDoubleSpinBox(); self.sp_dpi.setRange(72, 9600)
        self.sp_dpi.setValue(4800.0); self.sp_dpi.setDecimals(0); self.sp_dpi.setSuffix(" dpi")
        self.chk_p1 = QCheckBox(tr("main.include_center_reference_p1"))
        self.chk_p1.setChecked(True)
        self.chk_p1.setToolTip(tr("main.checked_nominal_origin_at_array_geometric_center_inc"))
        f.addRow(tr("main.rows"), self.sp_rows)
        f.addRow(tr("main.cols"), self.sp_cols)
        f.addRow("", self.chk_p1)
        # 编号顺序：4 种编号模式参数化（见 core/point_order.py）。影响图上标号、
        # 结果表序号、测量图片命名与 Excel 导出 P 编号，不改变物理位置与名义值。
        self.cb_order = QComboBox()
        zh = get_lang() == "zh"
        self.cb_order.addItems([mode_label(m[0], zh) for m in POINT_ORDER_MODES])
        self.cb_order.setCurrentIndex(next((i for i, m in enumerate(POINT_ORDER_MODES)
                                            if m[0] == DEFAULT_MODE), 0))
        self.cb_order.setToolTip(tr("main.numbering_order_traversal_direction_of_p_indices_on_"))
        self.cb_order.currentIndexChanged.connect(self._on_order_changed)
        f.addRow(tr("main.numbering_order"), self.cb_order)
        self._array_form = f
        for _w in (getattr(self, "sp_rows", None), getattr(self, "sp_cols", None),
                   getattr(self, "sp_pitch", None), getattr(self, "sp_dpi", None)):
            try:
                if _w is not None:
                    _w.setMaximumWidth(16777215)
            except Exception:
                pass

        # ② 检测设置
        g2 = QGroupBox(""); g2.setObjectName("flatCard"); self._grp_detect = g2
        # v3.6.1：折叠行已提供「检测设置」标题，GroupBox 不再重复显示标题
        f2 = QFormLayout(g2); self._detect_form = f2
        f2.setVerticalSpacing(6); f2.setHorizontalSpacing(10)
        f2.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        f2.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        f2.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.cb_thresh = QComboBox(); self.cb_thresh.addItems([tr("main.otsu"), tr("main.adaptive"), tr("main.fixed_128")])
        self.cb_polarity = QComboBox(); self.cb_polarity.addItems([tr("common.auto"), tr("main.dark_on_light"), tr("main.light_on_dark")])
        self.cb_polarity.setToolTip(tr("main.auto_decide_polarity_by_otsu_dark_pixel_ratio_choose"))
        self.sp_win = QDoubleSpinBox(); self.sp_win.setRange(1.0, 3.0)
        self.sp_win.setValue(1.2); self.sp_win.setDecimals(2)
        self.chk_full = QCheckBox(tr("common.full_image_processing_no_downscale"))
        # 默认整图处理（精度优先）：物理内存 >=16GB 时默认勾选；内存不足时保持
        # 降采样加载，避免低配机器整图加载超大扫描图 OOM。加载工程会还原工程内
        # 保存的勾选状态（_on_load_project），旧工程行为不变。
        if _system_ram_gb() >= 16.0:
            self.chk_full.setChecked(True)
        self.chk_full.setToolTip(tr("main.when_checked_oversized_images_load_at_full_resolutio") +
                                 (tr("main.enough_ram_on_this_pc_full_image_processing_is_check")
                                  if self.chk_full.isChecked() else
                                  tr("main.pc_ram_under_16gb_downsampling_by_default_to_save_me")))
        self.cb_method = QComboBox()
        self.cb_method.addItems([tr("main.projection_centroid_auto"), tr("main.edge_line_midpoint_edge_center")])
        self.cb_method.setToolTip(tr("main.projection_centroid_threshold_projection_centroid_st"))
        f2.addRow(tr("main.threshold"), self.cb_thresh)
        f2.addRow(tr("main.polarity"), self.cb_polarity)
        f2.addRow(tr("main.window_scale"), self.sp_win)
        f2.addRow("", self.chk_full)
        f2.addRow(tr("main.center_method"), self.cb_method)

        # v3.6.1：检测表单控件限宽（放在控件创建之后）
        for _w in (self.sp_win, self.cb_thresh, self.cb_polarity, self.cb_method):
            try:
                _w.setMaximumWidth(170)
            except Exception:
                pass

        # ③ 点选参考点
        g3 = QGroupBox(tr("main.pick_reference_points")); self._grp_ref = g3
        v3 = QVBoxLayout(g3); v3.setSpacing(8)
        self.lbl_hint = QLabel(tr("main.finish_chessboard_calibration_first_left_tab"))
        self.lbl_hint.setWordWrap(True)
        _set_role(self.lbl_hint, "hintWarn")
        # ---- 建系向导（v3.4）：自动跳到下一个点 + 可连续回退 ----
        # 原实现是 3 个点选按钮 + 3 个"清除该点"按钮（共 6 个），操作员必须记住
        # "哪个点代表哪个方向"，点错后也只能先想清楚该清哪一个。改为向导式：
        #   主按钮 = 点选"下一个空缺槽位"（点完自动进入下一槽位）；
        #   副按钮 = 删除上一个点（可连续点击，一直退回到空）；
        #   进度文案 = "已选 n/3"，让状态可见。
        self.btn_pick_next = QPushButton(tr("main.pick_origin"))
        self.btn_pick_next.clicked.connect(self._on_pick_next)
        self.btn_pick_undo = QPushButton(tr("main.delete_last_point"))
        self.btn_pick_undo.setObjectName("btnSecondary")
        self.btn_pick_undo.setEnabled(False)
        self.btn_pick_undo.clicked.connect(self._on_pick_undo)
        self.lbl_pick_progress = QLabel(tr("main.picked_n_of_3", n=0))
        self.lbl_pick_progress.setWordWrap(True)
        btn_reset = QPushButton(tr("main.reset_picks")); btn_reset.setObjectName("btnSecondary")
        btn_reset.clicked.connect(self._reset_pick)
        btn_auto_frame = QPushButton(tr("main.auto_frame_corners"))
        btn_auto_frame.setObjectName("btnSecondary")
        btn_auto_frame.setToolTip(tr("main.auto_detect_all_inner_corners_and_least_squares_fit_"))
        btn_auto_frame.clicked.connect(self._on_auto_frame)
        v3.addWidget(self.lbl_hint)
        self.btn_pick_next.setObjectName("btnPick")
        v3.addWidget(self.btn_pick_next)
        hl = QHBoxLayout()
        hl.addWidget(self.btn_pick_undo)
        hl.addWidget(btn_reset)
        v3.addLayout(hl)
        v3.addWidget(self.lbl_pick_progress)
        v3.addWidget(btn_auto_frame)
        self.btn_auto_frame = btn_auto_frame
        # v3.5：规格/间距/DPI 变化即时刷新状态卡
        for _w in (getattr(self, "sp_rows", None), getattr(self, "sp_cols", None),
                   getattr(self, "sp_pitch", None), getattr(self, "sp_dpi", None)):
            try:
                if _w is not None:
                    _w.valueChanged.connect(lambda *_: self._on_array_param_changed())
            except Exception:
                pass
        try:
            self.chk_p1.toggled.connect(lambda *_: self._on_array_param_changed())
        except Exception:
            pass
        self.btn_reset = btn_reset
        # 建系正交化(B1)：把手点/自动建系得到的两个方向轴收拢为正交基（角平分线不变）。
        # 背景：_finish_pick 里 x=点1-点0、y=点2-点0 无正交约束，两轴夹角 gamma 完全由
        # 点击误差决定；而精化只做"两轴同角度旋转"，gamma 是旋转不变量 => 全程冻结、
        # 无法纠正。两份真机报表实测 gamma = 89.7061° / 89.6563°（仅换点选就差 0.0498°），
        # 离线归因实验显示自由夹角的最优值恰为 90.00°，即该非正交量属手点噪声，
        # 而非设备/样件的方形度误差。本开关只把"点选歪基"收成"规范正交基"，
        # 不引入任何拟合自由度，也不改动 pitch_mm / 名义值 / 网格原点 / 校准 npz。
        self.chk_ortho = QCheckBox(tr("main.frame_orthogonalization_recommended_force_axes_perpe"))
        self.chk_ortho.setChecked(True)
        self.chk_ortho.setToolTip(tr(
            "main.checked_collapse_the_angle_between_the_two_picked_au"))
        v3.addWidget(self.chk_ortho)
        self.chk_ortho.stateChanged.connect(self._on_ortho_toggled)

        # ④ 检测与微调
        g4 = QGroupBox(tr("main.detect_fine_tune")); self._grp_manual = g4
        v4 = QVBoxLayout(g4); v4.setSpacing(8)
        # v3.9.12：工具栏取消后，导出 Excel 在界面上没有入口（只剩菜单/快捷键），
        # 按渲染图第 4 步在"检测与导出"页加回主按钮。
        self.btn_export_excel = QPushButton(tr("main.export_excel"))
        self.btn_export_excel.setObjectName("btnPrimary")
        self.btn_export_excel.clicked.connect(self._on_export)
        self.btn_export_excel.setEnabled(False)
        v4.addWidget(self.btn_export_excel)
        btn_detect = QPushButton(tr("main.detect")); btn_detect.setObjectName("btnPrimary")
        btn_detect.clicked.connect(self._on_detect)
        btn_drag = QPushButton(tr("main.fine_tune_drag")); btn_drag.setObjectName("btnSecondary")
        btn_drag.setCheckable(True)   # 模式开关：选中态由 QSS :checked 表现
        btn_drag.clicked.connect(self._toggle_drag)
        btn_manual = QPushButton(tr("main.manual_identification_click_refine")); btn_manual.setObjectName("btnSecondary")
        btn_manual.setCheckable(True)   # 模式开关
        btn_manual.clicked.connect(self._toggle_manual)
        btn_manual.setToolTip(tr("main.click_to_snap_refine_arrow_keys_fine_tune_selection_"))
        btn_clear_manual = QPushButton(tr("main.clear_manual_points")); btn_clear_manual.setObjectName("btnDanger"); btn_clear_manual.setObjectName("btnSecondary")
        btn_clear_manual.clicked.connect(self._on_clear_manual)
        btn_save_measure = QPushButton(tr("main.save_measurement_image_comparisons")); btn_save_measure.setObjectName("btnSecondary")
        btn_save_measure.clicked.connect(self._on_save_measure_images)
        btn_save_measure.setToolTip(tr("main.crops_original_image_per_point_overlays_cross_index_"))
        btn_export_traces = QPushButton(tr("main.export_trace_image_full")); btn_export_traces.setObjectName("btnSecondary")
        btn_export_traces.clicked.connect(self._on_export_traces)
        btn_export_traces.setToolTip(tr("main.renders_the_current_view_image_numbered_green_crosse"))
        v4.addWidget(btn_detect)
        btn_refine_axis = QPushButton(tr("main.rigid_refinement_basis_after_detection")); btn_refine_axis.setObjectName("btnSecondary")
        btn_refine_axis.clicked.connect(self._on_refine_axis)
        btn_refine_axis.setToolTip(tr("main.after_detection_run_svd_rigid_fit_on_successful_poin"))
        v4.addWidget(btn_refine_axis)
        v4.addWidget(btn_drag)
        v4.addWidget(btn_manual)
        v4.addWidget(btn_clear_manual)
        v4.addWidget(btn_save_measure)
        v4.addWidget(btn_export_traces)
        self.btn_detect = btn_detect
        self.btn_refine_axis = btn_refine_axis
        self.btn_drag = btn_drag
        self.btn_manual = btn_manual
        self.btn_clear_manual = btn_clear_manual
        self.btn_save_measure = btn_save_measure
        self.btn_export_traces = btn_export_traces
        self.btn_reset = btn_reset

        # 组装左侧（两个 Tab 各自包滚动区，避免内容超高/超宽显示不全）
        param_box = QWidget()
        vp = QVBoxLayout(param_box); vp.setContentsMargins(4, 4, 4, 4); vp.setSpacing(10)
        # ---- v3.5 左栏视觉：关键数据前置（阵列规格状态卡）----
        # ---- v3.9.2 方案A：阵列规格做成"行内摘要卡"（规格下拉 + 间距/DPI 同排） ----
        self.status_card = QFrame(); self.status_card.setObjectName("statusCard")
        _sc = QVBoxLayout(self.status_card)
        _sc.setContentsMargins(14, 10, 14, 10); _sc.setSpacing(6)
        self.lbl_card_title = QLabel(tr("main.array_spec")); self.lbl_card_title.setObjectName("cardTitle")
        _sc.addWidget(self.lbl_card_title)
        _r1 = QHBoxLayout(); _r1.setSpacing(8)
        self.cb_spec.setMinimumWidth(96)
        _r1.addWidget(self.cb_spec, 1)
        _sc.addLayout(_r1)
        _r2 = QHBoxLayout(); _r2.setSpacing(6)
        _r2.addWidget(QLabel(tr("main.pitch"))); self.sp_pitch.setMaximumWidth(92); _r2.addWidget(self.sp_pitch)
        _r2.addSpacing(6)
        _r2.addWidget(QLabel(tr("main.scan_dpi"))); self.sp_dpi.setMaximumWidth(84); _r2.addWidget(self.sp_dpi)
        _r2.addStretch(1)
        _sc.addLayout(_r2)
        self.lbl_card_hint = QLabel(""); self.lbl_card_hint.setObjectName("cardHint")
        _sc.addWidget(self.lbl_card_hint)
        vp.addWidget(self.status_card)
        vp.addWidget(g)
        # ---- v3.5：检测设置改为可折叠（默认展开，标题行点击收起）----
        self.btn_collapse_detect = QPushButton(tr("main.detection_settings_click_to_expand"))
        self.btn_collapse_detect.setObjectName("collapseRow")
        self.btn_collapse_detect.setCheckable(True)
        self.btn_collapse_detect.setChecked(True)
        self.btn_collapse_detect.toggled.connect(lambda c: (g2.setVisible(not c), self._sync_collapse_label()))
        g2.setVisible(False)
        vp.addWidget(self.btn_collapse_detect)
        vp.addWidget(g2)
        self.btn_collapse_ref = QPushButton(tr("main.pick_ref_click_to_expand"))
        self.btn_collapse_ref.setObjectName("collapseRow")
        self.btn_collapse_ref.setCheckable(True)
        self.btn_collapse_ref.setChecked(True)
        self.btn_collapse_ref.toggled.connect(lambda c: (g3.setVisible(not c), self._sync_collapse_label()))
        g3.setVisible(False)
        vp.addWidget(self.btn_collapse_ref)
        vp.addWidget(g3)

        # ---- v3.9.2 方案A：本步操作卡（主按钮 + 上一步/下一步 + 提示） ----
        try:
            from gui import ui_kit
            self.op_card = ui_kit.card(tr("main.this_step_actions"))
            _ol = self.op_card.layout()
            self.btn_apply_next = ui_kit.btn(tr("main.apply_array_next_step"), "primary")
            self.btn_apply_next.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
            _ol.addWidget(self.btn_apply_next)
            _nav = QHBoxLayout()
            _nav.setSpacing(8)
            self.btn_prev_step = ui_kit.btn(tr("main.prev_step"), "ghost")
            self.btn_prev_step.clicked.connect(lambda: self.tabs.setCurrentIndex(max(0, self.tabs.currentIndex() - 1)))
            self.btn_next_step = ui_kit.btn(tr("main.next_step"), "ghost")
            self.btn_next_step.clicked.connect(lambda: self.tabs.setCurrentIndex(min(2, self.tabs.currentIndex() + 1)))
            _nav.addWidget(self.btn_prev_step)
            _nav.addWidget(QLabel("<-->"))
            _nav.addWidget(self.btn_next_step)
            _nav.addStretch(1)
            _ol.addLayout(_nav)
            _hint = QLabel(tr("main.advanced_default_collapsed"))
            _hint.setObjectName("hintWarn")
            _hint.setWordWrap(True)
            _ol.addWidget(_hint)
            vp.addWidget(self.op_card)
        except Exception:
            pass
        vp.addStretch(1)
        # v3.4：新增第三页「检测与导出」——把执行区 g4（检测/精化/微调/手动/存图/导出痕迹）
        # 从"阵列与检测"页拆出来，让"设参数"和"执行动作"不再挤在同一页。
        exec_box = QWidget()
        ve = QVBoxLayout(exec_box); ve.setContentsMargins(4, 4, 4, 4); ve.setSpacing(10)
        ve.addWidget(g4)
        ve.addStretch(1)

        tabs = QTabWidget()
        calib_scroll = QScrollArea()
        calib_scroll.setWidgetResizable(True)
        calib_scroll.setFrameShape(QFrame.NoFrame)
        calib_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        calib_scroll.setWidget(self.calib_widget)
        param_scroll = QScrollArea()
        param_scroll.setWidgetResizable(True)
        param_scroll.setFrameShape(QFrame.NoFrame)
        param_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        param_scroll.setWidget(param_box)
        tabs.addTab(calib_scroll, tr("main.chessboard_calibration_required"))
        tabs.addTab(param_scroll, tr("main.array_detection"))
        exec_scroll = QScrollArea()
        exec_scroll.setWidgetResizable(True)
        exec_scroll.setFrameShape(QFrame.NoFrame)
        exec_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        exec_scroll.setWidget(exec_box)
        tabs.addTab(exec_scroll, tr("main.tab_detect_export"))
        # v3.9 方案A：左栏不再显示页签栏，改为随顶部步骤切换（单栏）
        try:
            tabs.tabBar().hide()
        except Exception:
            pass
        tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs = tabs

        # 右侧：预览 + 结果面板（汇总条 + 结果表），预览占大、表格占下，行多时表格内滚动
        self.table = QTableWidget(0, 15)
        self.table.setHorizontalHeaderLabels([tr("main._2"), tr("main.row"), tr("main.col"), tr("main.px_x"), tr("main.px_y"),
                                              tr("main.phys_x_mm"), tr("main.phys_y_mm"), tr("main.theoretical_x_mm"), tr("main.theoretical_y_mm"),
                                              tr("main.x_mm"), tr("main.y_mm"), tr("main.deviation_mm"), tr("main.result"), tr("main.confidence"), tr("main.status")])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setEditTriggers(self.table.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        hh = self.table.horizontalHeader()
        hh.setMinimumSectionSize(44)
        # 列宽策略：序号/行/列/判定/状态固定窄列，其余数值列弹性铺满，窗口缩放不溢出
        for ci, w in {0: 46, 1: 44, 2: 44, 12: 64, 13: 84}.items():
            hh.setSectionResizeMode(ci, QHeaderView.Fixed)
            self.table.setColumnWidth(ci, w)
        for ci in (3, 4, 5, 6, 7, 8, 9, 10, 11):
            hh.setSectionResizeMode(ci, QHeaderView.Stretch)
        hh.setStretchLastSection(False)
        self._apply_default_columns()
        _uniform_form(self._array_form)
        _uniform_form(self._detect_form)
        # 汇总条：一眼可见 检出/NG/最大偏差/校准RMS
        self.lbl_summary = QLabel(tr("main.not_detected_yet"))
        self.lbl_summary.setObjectName("summaryLabel")
        self.lbl_summary.setTextFormat(Qt.RichText)
        self.lbl_summary.setWordWrap(True)
        summary_bar = QFrame()
        summary_bar.setObjectName("summaryBar")
        sh = QHBoxLayout(summary_bar)
        sh.setContentsMargins(12, 5, 12, 5)
        sh.setSpacing(8)
        sh.addWidget(self.lbl_summary)
        sh.addStretch(1)
        result_panel = QWidget()
        vres = QVBoxLayout(result_panel)
        vres.setContentsMargins(0, 0, 0, 0)
        vres.setSpacing(6)
        vres.addWidget(summary_bar)
        vres.addWidget(self.table, 1)
        right = QWidget()
        vr = QVBoxLayout(right)
        vr.setContentsMargins(0, 0, 0, 0)
        vr.setSpacing(6)
        # ---- v3.9.3 方案A：预览工具条（缩放 / 适配 / 叠加标记开关） ----
        from gui import ui_kit
        self.preview_bar = QFrame()
        self.preview_bar.setObjectName("previewBar")
        _pb = QHBoxLayout(self.preview_bar)
        _pb.setContentsMargins(10, 3, 10, 3)
        _pb.setSpacing(6)
        self.lbl_preview_title = QLabel(tr("main.preview"))
        self.lbl_preview_title.setObjectName("cardHeader")
        _pb.addWidget(self.lbl_preview_title)
        _pb.addStretch(1)
        self.btn_zoom_out = ui_kit.btn(tr("main.zoom_out"), "ghost")
        self.btn_zoom_out.clicked.connect(lambda: self._zoom_by(1 / 1.25))
        self.btn_zoom_in = ui_kit.btn(tr("main.zoom_in"), "ghost")
        self.btn_zoom_in.clicked.connect(lambda: self._zoom_by(1.25))
        self.btn_zoom_fit = ui_kit.btn(tr("main.zoom_fit"), "ghost")
        self.btn_zoom_fit.clicked.connect(lambda: self.view._fit())
        self.chk_overlay = QCheckBox(tr("main.overlay_marks"))
        self.chk_overlay.setChecked(True)
        self.chk_overlay.toggled.connect(self._toggle_overlay)
        for _w in (self.btn_zoom_out, self.btn_zoom_in, self.btn_zoom_fit, self.chk_overlay):
            _pb.addWidget(_w)
        vr.addWidget(self.preview_bar)
        self.lbl_preview_placeholder = QLabel(tr("main.preview_placeholder"), self.view)
        self.lbl_preview_placeholder.setObjectName("previewPlaceholder")
        self.lbl_preview_placeholder.setAlignment(Qt.AlignCenter)
        self.lbl_preview_placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lbl_preview_placeholder.resize(620, 24)
        self.lbl_preview_placeholder.move(180, 150)
        vr.addWidget(self.view, 3)
        vr.addWidget(result_panel, 2)

        # 顶部步骤流程条：直观提示"导入 → 定义 → 检测 → 导出"当前所处阶段
        step_bar = QFrame()
        step_bar.setObjectName("stepBar")
        _svb = QVBoxLayout(step_bar)
        _svb.setContentsMargins(16, 10, 16, 8)
        _svb.setSpacing(6)
        hb = QHBoxLayout()
        hb.setSpacing(8)
        self._step_buttons = []
        # v3.9：按方案A 做成"四个等宽胶囊"，中间 --> 分隔，下方一行操作提示
        step_defs = [tr("main.step1_calibrate"), tr("main.step2_import_paper"),
                     tr("main.step3_frame"), tr("main.step4_detect_export")]
        for i, txt in enumerate(step_defs):
            if i > 0:
                ar = QLabel("-->")
                ar.setObjectName("stepArrow")
                hb.addWidget(ar)
            b = QPushButton(txt)
            b.setObjectName("stepPill")
            b.setCheckable(True)
            b.setMinimumHeight(38)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self._on_step_clicked(idx))
            hb.addWidget(b, 1)
            self._step_buttons.append(b)
        _svb.addLayout(hb)
        _hintrow = QHBoxLayout()
        _hintrow.setSpacing(10)
        self.lbl_step_hint = QLabel(tr("main.step_hint_click_to_jump"))
        self.lbl_step_hint.setObjectName("stepHint")
        _hintrow.addWidget(self.lbl_step_hint)
        _hintrow.addStretch(1)
        # 校准前置徽标（原来在流程条右端，重排后必须保留：_set_calibrated 会更新它）
        self.lbl_calib_badge = QLabel(tr("main.calibration_prerequisite_not_complete"))
        self.lbl_calib_badge.setObjectName("calibBadgeWarn")
        _hintrow.addWidget(self.lbl_calib_badge)
        _svb.addLayout(_hintrow)
        self._step_bar = step_bar

        split = QSplitter()
        split.addWidget(tabs)
        split.addWidget(right)
        tabs.setMaximumWidth(430)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([430, 970])
        split.setChildrenCollapsible(False)
        # v3.4：英文标签比中文长，左栏过窄会裁字（实测英文模式 3 个 QCheckBox 溢出）
        tabs.setMinimumWidth(404)
        container = QWidget()
        vc = QVBoxLayout(container)
        vc.setContentsMargins(0, 0, 0, 0)
        vc.setSpacing(0)
        vc.addWidget(step_bar)
        vc.addWidget(split, 1)
        # v3.6 日志抽屉：默认收起（只占一行标题），点「展开日志」看全部；
        # 旧实现底部常驻 120px，长期挤压预览区高度。
        self.log_drawer = QFrame()
        try:
            self.lbl_statusinfo = QLabel(tr("main.status_ready"))
            self.lbl_statusinfo.setObjectName("statusInfo")
            self.statusBar().addWidget(self.lbl_statusinfo, 1)
        except Exception:
            pass
        self.log_drawer.setObjectName("logDrawer")
        _ld = QVBoxLayout(self.log_drawer)
        _ld.setContentsMargins(10, 4, 10, 4)
        _ld.setSpacing(2)
        _lhd = QHBoxLayout()
        _lhd.setSpacing(8)
        self.lbl_log = QLabel(tr("main.log"))
        self.lbl_log.setObjectName("cardTitle")
        self.btn_log_toggle = QPushButton(tr("main.log_expand"))
        self.btn_log_toggle.setObjectName("collapseRow")
        self.btn_log_toggle.setCheckable(True)
        self.btn_log_toggle.setChecked(False)
        self.btn_log_toggle.setFixedWidth(120)
        self.btn_log_toggle.toggled.connect(self._on_log_toggle)
        _lhd.addWidget(self.lbl_log)
        _lhd.addStretch(1)
        _lhd.addWidget(self.btn_log_toggle)
        _ld.addLayout(_lhd)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFixedHeight(110)
        self.log_view.setPlaceholderText(tr("main.zh_b224efc3"))
        self.log_view.setVisible(False)
        _ld.addWidget(self.log_view)
        vc.addWidget(self.log_drawer)
        self.setCentralWidget(container)

        # v3.9 方案A：语言切换 + 关于挂到菜单栏右上角（此处所有控件均已创建，稳定生效）
        self._wire_menu_corner()
        self._update_status_card()


        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().clearMessage()
    def _on_lang_changed(self, idx: int):
        set_lang("en" if idx == 1 else "zh")
        self.retranslate()

    def retranslate(self):
        """切换语言后刷新全部界面文本。"""
        self.setWindowTitle(tr("main.galvo_calibration_vision_software"))
        self.act_open.setText(tr("main.import_scan"))
        self.act_save_proj.setText(tr("main.save_project"))
        self.act_load_proj.setText(tr("main.load_project"))
        self.act_export.setText(tr("main.export_excel"))
        self.act_load_tpl.setText(tr("main.load_template"))
        self.act_load_resfb.setText(tr("main.load_residual_correction"))
        self.act_load_resfb.setToolTip(tr("main.load_residual_correction_file_res_fb_npz_fitted_from"))
        self.act_multi_pose.setText(tr("main.multi_pose_residual_correction"))
        self.act_multi_pose.setToolTip(tr("main.import_1_5_scans_of_the_reference_plate_in_different"))
        self._main_toolbar.setWindowTitle(tr("main.main_toolbar"))
        try:
            self._menu_file.setTitle(tr("main.menu_file"))
            self._menu_tools.setTitle(tr("main.menu_tools"))
            self._menu_help.setTitle(tr("main.menu_help"))
            # v3.5：菜单动作文案必须随语言刷新，否则切英文后工具菜单仍显示中文
            self.act_about.setText(tr("main.about"))
            self.act_open.setText(tr("main.import_scan"))
            self.act_save_proj.setText(tr("main.save_project"))
            self.act_load_proj.setText(tr("main.load_project"))
            self.act_export.setText(tr("main.export_excel"))
            self.btn_export_excel.setText(tr("main.export_excel"))
            self.act_load_tpl.setText(tr("main.load_template"))
            self.act_load_resfb.setText(tr("main.load_residual_correction"))
            self.act_multi_pose.setText(tr("main.multi_pose_residual_correction"))
            self.act_reg.setText(tr("main.same_frame_registration"))
            self.act_load_reg.setText(tr("main.load_registration_model"))
            self.act_clear_reg.setText(tr("main.clear_registration_model"))
            self.act_columns.setText(tr("main.column_settings"))
            self.act_dark.setText(tr("main.dark_mode"))
        except Exception:
            pass
        self.lbl_lang.setText(tr("main.language"))
        self.cb_lang.blockSignals(True)
        self.cb_lang.setItemText(0, tr("main.language_chinese"))
        self.cb_lang.setItemText(1, tr("main.english"))
        self.cb_lang.blockSignals(False)
        self.btn_reset.setText(tr("main.reset_picks"))
        self.btn_pick_undo.setText(tr("main.delete_last_point"))
        self._update_pick_ui()
        self.btn_detect.setText(tr("main.detect"))
        self.btn_refine_axis.setText(tr("main.rigid_refinement_basis_after_detection"))
        self.btn_refine_axis.setToolTip(tr("main.after_detection_run_svd_rigid_fit_on_successful_poin"))
        self.btn_drag.setText(tr("main.fine_tune_drag"))
        self.btn_manual.setText(tr("main.manual_identification_click_refine"))
        self.btn_clear_manual.setText(tr("main.clear_manual_points"))
        self.btn_save_measure.setText(tr("main.save_measurement_image_comparisons"))
        self.btn_export_traces.setText(tr("main.export_trace_image_full"))
        self.chk_full.setText(tr("common.full_image_processing_no_downscale"))
        self.chk_p1.setText(tr("main.include_center_reference_p1"))
        self.cb_method.setItemText(0, tr("main.projection_centroid_auto"))
        self.cb_method.setItemText(1, tr("main.edge_line_midpoint_edge_center"))
        self.tabs.setTabText(0, tr("main.chessboard_calibration_required"))
        self.tabs.setTabText(1, tr("main.array_detection"))
        self.tabs.setTabText(2, tr("main.tab_detect_export"))
        self._grp_def.setTitle(tr("main.define_array"))
        self._grp_detect.setTitle(tr("main.detection_settings"))
        self._grp_ref.setTitle(tr("main.pick_reference_points"))
        self._grp_manual.setTitle(tr("main.detect_fine_tune"))
        for i, key in enumerate(["main.step1_calibrate", "main.step2_import_paper", "main.step3_frame", "main.step4_detect_export"]):
            if i < len(getattr(self, "_step_buttons", [])):
                self._step_buttons[i].setText(tr(key))
        if self.calibrated:
            self.lbl_calib_badge.setText(tr("main.calibration_prerequisite_passed"))
        else:
            self.lbl_calib_badge.setText(tr("main.calibration_prerequisite_not_complete"))
        self.btn_auto_frame.setText(tr("main.auto_frame_corners"))
        try:
            self.lbl_card_title.setText(tr("main.array_spec"))
            self.btn_collapse_detect.setText(tr("main.detection_settings"))
            self._update_status_card()
        except Exception:
            pass
        try:
            self.chk_ortho.setText(tr("main.frame_orthogonalization_recommended_force_axes_perpe"))
            self.chk_ortho.setToolTip(tr(
                "main.checked_collapse_the_angle_between_the_two_picked_au"))
        except Exception:
            pass
        self.btn_auto_frame.setToolTip(tr("main.auto_detect_all_inner_corners_and_least_squares_fit_"))
        self.btn_manual.setToolTip(tr("main.click_to_snap_refine_arrow_keys_fine_tune_selection_"))
        self.btn_save_measure.setToolTip(tr("main.crops_original_image_per_point_overlays_cross_index_"))
        self.btn_export_traces.setToolTip(tr("main.renders_the_current_view_image_numbered_green_crosse"))
        self.cb_lang.setToolTip(tr("main.switch_ui_language"))
        self.chk_p1.setToolTip(tr("main.checked_nominal_origin_at_array_geometric_center_inc"))
        self.chk_full.setToolTip(tr("main.when_checked_oversized_images_load_at_full_resolutio"))
        self.cb_polarity.setToolTip(tr("main.auto_decide_polarity_by_otsu_dark_pixel_ratio_choose"))
        self.cb_method.setToolTip(tr("main.projection_centroid_threshold_projection_centroid_st"))
        self.table.setHorizontalHeaderLabels([tr("main._2"), tr("main.row"), tr("main.col"), tr("main.px_x"), tr("main.px_y"),
                                              tr("main.phys_x_mm"), tr("main.phys_y_mm"), tr("main.theoretical_x_mm"), tr("main.theoretical_y_mm"),
                                              tr("main.x_mm"), tr("main.y_mm"), tr("main.deviation_mm"), tr("main.result"), tr("main.confidence"), tr("main.status")])
        # 阵列表单标签
        for w, key in [(self.cb_spec, "main.array_spec"), (self.sp_rows, "main.rows"), (self.sp_cols, "main.cols"),
                       (self.sp_pitch, "main.pitch"), (self.sp_dpi, "main.scan_dpi"), (self.cb_order, "main.numbering_order")]:
            lb = self._array_form.labelForField(w)
            if lb is not None:
                lb.setText(tr(key))
        # 检测设置表单标签
        for w, key in [(self.cb_thresh, "main.threshold"), (self.cb_polarity, "main.polarity"),
                       (self.sp_win, "main.window_scale"), (self.cb_method, "main.center_method")]:
            lb = self._detect_form.labelForField(w)
            if lb is not None:
                lb.setText(tr(key))
        self.cb_spec.setItemText(len(ARRAY_SPECS), tr("main.custom"))
        self.cb_order.setToolTip(tr("main.numbering_order_traversal_direction_of_p_indices_on_"))
        # 编号顺序下拉：按当前语言重填，保持已选模式
        zh = get_lang() == "zh"
        cur = getattr(self, "order_mode", DEFAULT_MODE)
        self.cb_order.blockSignals(True)
        self.cb_order.clear()
        self.cb_order.addItems([mode_label(m[0], zh) for m in POINT_ORDER_MODES])
        self.cb_order.setCurrentIndex(next((i for i, m in enumerate(POINT_ORDER_MODES)
                                            if m[0] == cur), 0))
        self.cb_order.blockSignals(False)
        self.cb_thresh.setItemText(0, tr("main.otsu"))
        self.cb_thresh.setItemText(1, tr("main.adaptive"))
        self.cb_thresh.setItemText(2, tr("main.fixed_128"))
        self.cb_polarity.setItemText(0, tr("common.auto"))
        self.cb_polarity.setItemText(1, tr("main.dark_on_light"))
        self.cb_polarity.setItemText(2, tr("main.light_on_dark"))
        # 状态提示
        if self.calibrated:
            self.lbl_hint.setText(tr("main.calibration_passed_import_a_scan_and_define_the_arra"))
        else:
            self.lbl_hint.setText(tr("main.calibration_not_done_detection_locked_required_step"))
        self.statusBar().showMessage(tr("main.ready") if not self.src else self.statusBar().currentMessage())
        self.calib_widget.retranslate()
        # 刷新表格状态列（G20：不再按中文字面量比对，直接按当前语言整表重建，
        # 否则 中→英→中 反复切换后状态列会停在英文）
        try:
            self._fill_table()
        except Exception:
            pass
        self._update_summary()
        # v3.7: refresh shell widgets here (menus / log drawer / status card).
        # This block used to be inserted into construction code by mistake, where these
        # widgets did not exist yet and the exception was swallowed -> language switch
        # never refreshed menu items and log captions.
        try:
            self._menu_file.setTitle(tr("main.menu_file"))
            self._menu_tools.setTitle(tr("main.menu_tools"))
            self._menu_help.setTitle(tr("main.menu_help"))
            self.act_about.setText(tr("main.about"))
            self.act_manual.setText(tr("main.help_manual"))
            self.act_columns.setText(tr("main.column_settings"))
            self.act_dark.setText(tr("main.dark_mode"))
        except Exception:
            pass
        try:
            self.lbl_log.setText(tr("main.log"))
            self.btn_log_toggle.setText(tr("main.log_collapse") if self.btn_log_toggle.isChecked()
                                        else tr("main.log_expand"))
        except Exception:
            pass
        try:
            self.lbl_card_title.setText(tr("main.array_spec"))
            self.btn_collapse_detect.setText(tr("main.detection_settings"))
            self._update_status_card()
        except Exception:
            pass


    def _display_src(self):
        """当前预览中显示的图（棋盘格预览时=标定图；正常流程=相纸图）。
        与主流程用的 self.src 分离，避免标定预览覆盖导致检测/导出用错图。"""
        return getattr(self, "_preview_src", None) or self.src

    def _pv(self, lx: float, ly: float) -> Tuple[float, float]:
        """加载分辨率坐标 → 预览坐标（按当前显示图的降采样比例换算）。"""
        src = self._display_src()
        ps = getattr(src, "preview_scale", 1.0) if src else 1.0
        return lx / ps, ly / ps

    # ---------------- 编号顺序模式 ----------------
    def _order_mode(self) -> str:
        """当前编号顺序模式；未知/异常一律回退默认（左上起行主序，与旧版一致）。"""
        m = getattr(self, "order_mode", DEFAULT_MODE)
        if not any(m == mod[0] for mod in POINT_ORDER_MODES):
            return DEFAULT_MODE
        return m

    def _order_display_no(self, rank: int) -> int:
        """内部秩（rank）→ 当前模式下的显示编号。grid 缺失或异常时回退 rank+1。"""
        if self.grid is None:
            return rank + 1
        try:
            return display_no(rank, self.grid.rows, self.grid.cols, self._order_mode())
        except Exception:
            return rank + 1

    def _on_order_changed(self, idx: int):
        """切换编号顺序模式：立即重绘图上标号与结果表（坐标/名义值不变）。"""
        if 0 <= idx < len(POINT_ORDER_MODES):
            self.order_mode = POINT_ORDER_MODES[idx][0]
        self._redraw_marks()
        self._fill_table()
        self.statusBar().showMessage(
            tr("main.numbering_order_changed_m", m=mode_label(self._order_mode(), get_lang() == "zh")), 6000)

    # ---------------- 校准过程可视化 ----------------
    def _on_calib_progress(self, v: float):
        self.progress.setValue(int(v * 100))

    def _on_calib_corners(self, pts):
        """识别/检测到的角点以绿色十字叠加显示（识别过程可视化）。"""
        if self._display_src() is None:
            return
        self.view.clear_overlay()
        try:
            for (x, y) in pts:
                px, py = self._pv(float(x), float(y))
                self.view.add_cross_mark(px, py, size=6, color="#34c759")
            self.statusBar().showMessage(tr("main.n_corners_found_green_crosses", n=len(pts)), 6000)
        except Exception:
            pass

    def _on_calib_extent(self, ext):
        """自动识别范围以红色虚线框叠加显示，用于核对识别范围是否准确。"""
        if self._display_src() is None or not ext or len(ext) < 4:
            return
        try:
            x0, y0, x1, y1 = ext
            p0 = self._pv(float(x0), float(y0))
            p1 = self._pv(float(x1), float(y1))
            self.view.add_rect(p0[0], p0[1], p1[0], p1[1], color="#ff3b30", pen_w=2.5)
            self.statusBar().showMessage(tr("main.detection_range_red_dashed_box"), 6000)
        except Exception:
            pass

    # ---------------- 校准状态 ----------------
    FLOW_STEP_KEYS = ("main.step1_calibrate", "main.step2_import_paper",
                      "main.step3_frame", "main.step4_detect_export")

    def _flow_state(self):
        """(done, active). Order: calibrate -> import paper -> frame -> detect.
        Later states are not counted until earlier ones finish."""
        if not getattr(self, "calibrated", False):
            return 0, 0
        if getattr(self, "src", None) is None:
            return 1, 1
        if getattr(self, "grid", None) is None:
            return 2, 2
        if getattr(self, "results", None):
            return 4, -1
        return 3, 3

    def _refresh_flow(self):
        try:
            done, active = self._flow_state()
            self._update_steps(done, active)
        except Exception:
            pass

    def _update_steps(self, done: int = 0, active: int = 0):
        """顶部步骤流程条状态：done 为已完成步骤数，active 为当前步骤索引。
        已完成步骤徽章显示绿色 ✓，当前步骤蓝色数字，待办灰色数字。"""
        for i, b in enumerate(getattr(self, "_step_buttons", [])):
            try:
                if i < done:
                    b.setObjectName("stepPillDone")
                    b.setChecked(False)
                elif i == active:
                    b.setObjectName("stepPillActive")
                    b.setChecked(True)
                else:
                    b.setObjectName("stepPill")
                    b.setChecked(False)
                b.style().unpolish(b)
                b.style().polish(b)
            except Exception:
                pass

    def _zoom_by(self, factor: float):
        try:
            cur = self.view.transform().m11()
            target = cur * factor
            if target < 0.02 or target > 40.0:
                return
            self.view.scale(factor, factor)
        except Exception:
            pass

    def _toggle_overlay(self, on: bool):
        """预览工具条：显示/隐藏叠加标记（G16：覆盖序号/点选/锚点/手动悬停与放大镜）。"""
        try:
            v = self.view
            for it in getattr(v, "_label_items", []):
                it.setVisible(bool(on))
            for grp in getattr(v, "_pick_graphics", []):
                for gi, _off in grp:
                    gi.setVisible(bool(on))
            for it in getattr(v, "_anchor_items", []):
                it.setVisible(bool(on))
            for it in getattr(v, "_overlay_items", []):
                it.setVisible(bool(on))
            for it in getattr(v, "_manual_hover_lines", []):
                it.setVisible(bool(on))
            for attr in ("_manual_roi", "_manual_loupe", "_manual_loupe_border"):
                it = getattr(v, attr, None)
                if it is not None:
                    it.setVisible(bool(on))
        except Exception:
            pass

    def _set_manual_btn_active(self, active: bool):
        """G13：手动识别按钮统一入口（objectName + 勾选态一起复位，避免只清内联样式）。"""
        try:
            _set_role(self.btn_manual, "btnManualActive" if active else "btnSecondary")
            self.btn_manual.setChecked(bool(active))
        except Exception:
            pass

    def closeEvent(self, event):
        """G12：关窗前请求中断并等待后台线程，避免 QThread destroyed while running 崩溃。"""
        try:
            self._closing = True
        except Exception:
            pass
        try:
            self.calib_widget.shutdown()
        except Exception:
            pass
        for attr in ("_detect_thread", "_image_loader", "_deskew_loader", "_auto_frame_worker",
                     "_gray_loader", "_manual_thread", "_drag_save_thread"):
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
        try:
            super().closeEvent(event)
        except Exception:
            event.accept()

    def _on_step_clicked(self, idx: int):
        """点顶部步骤胶囊：切到对应单栏页（方案A：可直接跳转）。"""
        try:
            target = 0 if idx == 0 else (2 if idx >= 3 else 1)
            if hasattr(self, "tabs"):
                self.tabs.setCurrentIndex(target)
            if idx == 1:
                # 第 2 步=导入相纸扫描图：直接弹导入，避免用户再走菜单误选到棋盘格
                self._on_open_image()
            self._refresh_flow()   # G15: 统一复位胶囊高亮，避免同时点亮多个
        except Exception:
            pass

    def _apply_verdict_style(self, item: QTableWidgetItem, verdict: str):
        """判定列着色：Pass 绿、NG 红加粗，结果一眼可辨。"""
        if verdict == "NG":
            item.setForeground(QBrush(QColor(C("err"))))
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        elif verdict == "Pass":
            item.setForeground(QBrush(QColor(C("ok"))))

    def _update_summary(self):
        """刷新结果汇总条：总点数 / 检出 / NG / 最大偏差 / 校准RMS，一眼看到检测结论。"""
        n = len(self.results)
        if n == 0:
            self.lbl_summary.setText(tr("main.not_detected_yet"))
            return
        nominal = getattr(self, "_nominal_cache", None)
        center = getattr(self, "_center_cache", None)
        if nominal is None:
            nominal = self._nominal_mm_list()
            center = self._center_measured_mm(self.results)
        det = 0
        ng = 0
        max_dev = 0.0
        for i, r in enumerate(self.results):
            if r is None or not r.ok:
                continue
            det += 1
            vals = self._row_values(i, r, nominal, center)
            if len(vals) > 12 and vals[12] == "NG":
                ng += 1
            if len(vals) > 11 and vals[11] not in ("", "nan", "NaN"):
                try:
                    d = float(vals[11])
                except ValueError:
                    d = 0.0
                max_dev = max(max_dev, d)
        fail = n - det  # F 模块：失败计数独立（未检出/检测失败点）
        ok = det - ng
        rms = getattr(self, "calib_rms", None)
        rms_txt = tr("main.calibration_rms_r_mm", r=f"{rms:.4f}") if rms else tr("common.not_calibrated")
        html = (
            f'<b style="color:{C('text')};">{tr("main.detection_summary")}</b>'
            f'<span style="color:{C('dim')};">&nbsp;|&nbsp;</span>'
            f'<span style="color:{C('text')};font-weight:600;">{tr("main.total_points")} {n}</span>'
            f'<span style="color:{C('dim')};">&nbsp;·&nbsp;</span>'
            f'<span style="color:{C('info')};font-weight:600;">{tr("main.detected")} {det}</span>'
            f'<span style="color:{C('dim')};">&nbsp;·&nbsp;</span>'
            f'<span style="color:{C('ok')};font-weight:600;">{tr("main.pass")} {ok}</span>'
            f'<span style="color:{C('dim')};">&nbsp;·&nbsp;</span>'
            f'<span style="color:{C("err") if ng else C("ok")};font-weight:600;">{tr("main.ng")} {ng}</span>'
            f'<span style="color:{C('dim')};">&nbsp;·&nbsp;</span>'
            f'<span style="color:{C("warn") if fail else C("dim")};font-weight:600;">{tr("main.failed")} {fail}</span>'
            f'<span style="color:{C('dim')};">&nbsp;·&nbsp;</span>'
            f'<span style="color:{C('text')};font-weight:600;">{tr("main.max_deviation")} {max_dev:.4f} mm</span>'
            f'<span style="color:{C('dim')};">&nbsp;·&nbsp;</span>'
            f'<span style="color:{C('text')};">{rms_txt}</span>'
        )
        try:
            self.lbl_summary.setTextFormat(Qt.RichText)
        except Exception:
            pass
        self.lbl_summary.setText(html)

    def _set_calibrated(self, ok: bool):
        self.calibrated = bool(ok)
        res = self.calib_widget.result
        # 分区校正场优先（阶段三）；全局多项式系数作为兼容回退
        self.piecewise = (res.piecewise if ok and res is not None else None)
        if ok and res is not None and (res.coeff_x is not None or res.piecewise is not None):
            self.coeff_x = res.coeff_x
            self.coeff_y = res.coeff_y
            self.order = self.calib_widget.params().order
            self.calib_rms = res.rms_mm
        else:
            self.coeff_x = None
            self.coeff_y = None
        self.btn_detect.setEnabled(self.calibrated)
        self.act_export.setEnabled(False)
        # 校准徽章状态
        self.lbl_calib_badge.setObjectName("calibBadgeOk" if ok else "calibBadgeWarn")
        self.lbl_calib_badge.setText(tr("main.calibration_prerequisite_passed") if ok else tr("main.calibration_prerequisite_not_complete"))
        self.lbl_calib_badge.style().unpolish(self.lbl_calib_badge)
        self.lbl_calib_badge.style().polish(self.lbl_calib_badge)
        if ok:
            self.lbl_hint.setText(tr("main.calibration_passed_import_a_scan_and_define_the_arra"))
            _set_role(self.lbl_hint, "okText")
        else:
            self.lbl_hint.setText(tr("main.calibration_not_done_detection_locked_required_step"))
            _set_role(self.lbl_hint, "errText")

    def _on_tab_changed(self, idx: int):
        if idx == 1 and not self.calibrated:
            self.lbl_hint.setText(tr("main.finish_chessboard_calibration_first_left_tab"))

    # ---------------- 图片导入 ----------------
    def _on_open_image(self):
        """导入相纸扫描图。记住自己上次的目录，且不复用"标定棋盘格"的目录，
        否则 Qt 对话框会带着棋盘格文件再次被打开（实测会误导入棋盘格）。"""
        d = getattr(self, "_last_paper_dir", "") or ""
        path, _ = qfd_open(self, tr("main.import_scan_image"), d,
                           tr("common.image_files_png_tif_tiff_jpg_jpeg_bmp"))
        if not path:
            return
        try:
            self._last_paper_dir = os.path.dirname(path)
        except Exception:
            pass
        self._log(tr("main.imported_file_n", name=os.path.basename(path)))
        # 防御：相纸导入与标定棋盘格是同一个文件时提示（两者常放同一目录，容易选错）
        try:
            calib_src = getattr(self.calib_widget, "src", None)
            if calib_src is not None and getattr(calib_src, "path", None) and \
                    os.path.normcase(os.path.abspath(calib_src.path)) == os.path.normcase(os.path.abspath(path)):
                self._log(tr("main.same_file_as_calibration_warning"))
                self.statusBar().showMessage(tr("main.same_file_as_calibration_warning"), 8000)
        except Exception:
            pass
        self._load_image(path)

    def _load_image(self, path: str):
        """异步后台加载图片：超大图解码移出主线程，避免窗口长时间无响应（“干死”）。

        旧实现：主线程同步 ImageSource() + preview()，4800dpi 全幅图需反复全量
        解压 PNG，耗时数十秒，界面完全冻结。现在由 ImageLoaderThread 后台完成，
        完成后经 _on_image_loaded 回主线程更新界面。
        """
        self._image_loader = ImageLoaderThread(path, self.chk_full.isChecked(), parent=self)
        self._image_loader.done.connect(self._on_image_loaded)
        self._log(tr("main.zh_f8fe4381", path=path))
        self.statusBar().showMessage(tr("main.loading_image_in_background_large_images_may_take_te"))
        self._image_loader.start()

    def _on_image_loaded(self, obj):
        # 若期间用户又导入了新图，旧线程结果作废，避免旧图覆盖新图
        if self.sender() is not getattr(self, "_image_loader", None):
            return
        if isinstance(obj, Exception):
            self._log(tr("main.image_import_failed_err", err=str(obj)))
            QMessageBox.warning(self, tr("common.import_failed"), str(obj))
            self.statusBar().showMessage(tr("main.image_import_failed_err", err=str(obj)))
            return
        src, preview = obj
        self.src = src
        try:
            self.lbl_preview_placeholder.hide()
        except Exception:
            pass
        self.view.show_image(preview, src.origin_w, src.origin_h)
        self._src_gen = getattr(self, "_src_gen", 0) + 1   # G05/G06: 新图代次
        self._reset_pick()
        self.results = []
        self._fill_table()
        self._refresh_flow()
        # G08/G09 修复：换图必须清掉上一张图的手动模式缓存与锚点，避免吸附用旧灰度、
        # 或在新图上画出旧图的原点锚点。
        self._manual_gray = None
        self._manual_preview_gray = None
        self._auto_snapshot = None
        self._manual_active = False
        self.origin_xy = None
        try:
            self.btn_manual.setChecked(False)
            self.chk_overlay.setChecked(True)
        except Exception:
            pass
        self.statusBar().showMessage(tr("main.loaded_path_w_h_px",
                                        path=src.path, w=src.origin_w, h=src.origin_h) +
                                     (("（" + src.warning + "）") if src.warning else ""))
        # 工程加载路径：图片为异步加载，工程状态（阵列/结果/参数）暂存在
        # _pending_project，待图片就绪后应用，避免上面 _reset_pick()/results=[]
        # 把工程数据清空（旧实现加载工程后阵列与结果丢失）
        pending = getattr(self, "_pending_project", None)
        if pending:
            self._pending_project = None
            self._apply_project_state(pending, pending.get("_proj_path", ""))
        # B1 摆正：图片就绪后自动估角（后台线程），角度只进换算层旋转矩阵
        self._deskew_ang = None
        self._deskew_shape = (src.origin_h, src.origin_w)
        self._deskew_loader = DeskewAngleThread(src, parent=self)
        self._deskew_loader.done.connect(self._on_deskew_angle)
        self._deskew_loader.start()

    def _on_deskew_angle(self, obj):
        """B1 估角回调：写回摆正角，log 栏输出角度；失败/未检出则跳过摆正。"""
        if self.sender() is not getattr(self, "_deskew_loader", None):
            return
        if isinstance(obj, Exception):
            self._deskew_ang = None
            self._log(tr("main.deskew_angle_estimation_failed_err", err=str(obj)))
            return
        if obj is None:
            self._deskew_ang = None
            self._log(tr("main.no_cross_array_detected_deskew_skipped_this_time"))
            return
        self._deskew_ang = float(obj)
        self._log(tr("main.deskew_angle_ang_3f_written_into_mapping_rotation_ma",
                     ang=self._deskew_ang))

    def _show_calib_preview(self, src: ImageSource):
        # G07 修复：棋盘格预览只用于显示，绝不覆盖 self.src / 不清网格，
        # 否则"导入相纸→再标定"会让后续检测/导出拿棋盘格图配相纸网格（静默错值）。
        self._calib_preview_src = src
        self._preview_src = src          # 仅影响显示与叠加坐标换算
        self.view.show_image(src.preview(), src.origin_w, src.origin_h)
        self.statusBar().showMessage(tr("main.chessboard_preview_path", path=src.path))

    # ---------------- 阵列定义 ----------------
    def _is_stale_result(self, obj) -> bool:
        """代次校验：obj 带 _gen 且与当前图片来源代次不一致 => 旧结果。"""
        gen = getattr(obj, "_gen", None)
        if gen is None:
            return False
        return int(gen) != int(getattr(self, "_src_gen", 0))

    def _on_array_param_changed(self):
        """G10：阵列参数（行/列/间距/DPI/含中心点）变更后，旧网格与旧结果必须失效，
        否则报表会用新参数解释旧坐标（静默错值）。工程加载期间用 _suppress_invalidate 屏蔽。"""
        try:
            if getattr(self, "_suppress_invalidate", False):
                return
            had = bool(self.results) or (self.grid is not None)
            self._update_status_card()
            if not had:
                return
            self._reset_pick()
            self._log(tr("main.array_params_changed_results_cleared"))
            self.statusBar().showMessage(tr("main.array_params_changed_results_cleared"), 8000)
        except Exception:
            pass

    def _update_status_card(self):
        """阵列规格状态卡：把最常用的三个数（规格/间距/DPI）大字号前置。"""
        try:
            self.lbl_card_hint.setText(tr("main.card_pitch_dpi_hint",
                                          pitch="%.3f" % float(self.sp_pitch.value()),
                                          dpi="%d" % int(self.sp_dpi.value())))
            # v3.6.1：规格下拉与「行数×列数」保持一致（原来下拉显示 3×3、状态卡却是 5×5）
            try:
                txt = "%d×%d" % (int(self.sp_rows.value()), int(self.sp_cols.value()))
                idx = -1
                for _i, _spec in enumerate(ARRAY_SPECS):
                    if _spec == txt:
                        idx = _i
                        break
                self.cb_spec.blockSignals(True)
                self.cb_spec.setCurrentIndex(idx if idx >= 0 else len(ARRAY_SPECS))
                self.cb_spec.blockSignals(False)
            except Exception:
                pass
        except Exception:
            pass

    def _on_spec_changed(self, text: str):
        if text not in ARRAY_SPECS:
            return
        n = int(text.split("×")[0])
        self.sp_rows.setValue(n)
        self.sp_cols.setValue(n)
        self._update_status_card()

    # ---------------- 建系向导（v3.4） ----------------
    _PICK_LABELS = ("main.pick_origin", "main.pick_x_direction_point", "main.pick_y_direction_point")

    def _on_pick_next(self):
        """主按钮：点选下一个空缺槽位；三个都齐了则从头重来。"""
        nxt = next((i for i in range(3) if self._picked[i] is None), None)
        if nxt is None:
            if getattr(self, "results", None):
                ret = QMessageBox.question(
                    self, tr("main.confirm"),
                    tr("main.repick_will_clear_current_frame_and_results"),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ret != QMessageBox.Yes:
                    return
            self._reset_pick()
            nxt = 0
        self._start_pick(nxt)

    def _on_pick_undo(self):
        """删除上一个点：可连续点击，一直退回到空（不保留任何点）。"""
        last = None
        for i in (2, 1, 0):
            if self._picked[i] is not None:
                last = i
                break
        if last is None:
            return
        self._picked[last] = None
        self._pick_state = -1
        self.view.set_mode(MODE_NONE)
        self._refresh_pick_marks()
        self._update_pick_ui()
        remaining = sum(1 for p in self._picked if p is not None)
        if remaining == 0:
            self.lbl_hint.setText(tr("main.pick_the_array_to_define_the_search_grid"))
        else:
            self.lbl_hint.setText(tr("main.undo_last_point_hint"))

    def _update_pick_ui(self):
        """按当前已选点刷新主按钮文案 / 删除按钮可用性 / 进度文案。"""
        try:
            picked = getattr(self, "_picked", None) or [None, None, None]
            n = sum(1 for p in picked if p is not None)
            nxt = next((i for i in range(3) if picked[i] is None), None)
            if getattr(self, "btn_pick_next", None) is None:
                return
            self.btn_pick_next.setText(tr(self._PICK_LABELS[nxt] if nxt is not None else "main.repick_from_origin"))
            self.btn_pick_undo.setEnabled(n > 0)
            self.lbl_pick_progress.setText(tr("main.picked_n_of_3", n=n))
        except Exception:
            pass

    def _start_pick(self, slot: int):
        """进入点选状态：slot 0=起始点 1=X方向 2=Y方向。"""
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("main.please_import_a_scan_image_first"))
            return
        self._pick_state = slot
        self.view.set_mode(MODE_PICK)
        if slot == 0:
            self.lbl_hint.setText(tr("main.click_the_array_origin_center_of_the_1st_cross"))
        elif slot == 1:
            self.lbl_hint.setText(tr("main.click_the_2nd_cross_along_x_direction"))
        else:
            self.lbl_hint.setText(tr("main.click_the_2nd_cross_along_y_direction"))

    def _reset_pick(self):
        self._pick_state = -1
        self._picked = [None, None, None]
        self.view.set_mode(MODE_NONE)
        self.grid = None
        # G02/G09 修复：重置建系时必须同步清结果、清原点锚点，并复位导出可用性，
        # 否则旧结果会与新网格错配（导出用新参数解释旧坐标）或残留旧锚点。
        self.results = []
        self.origin_xy = None
        self._manual_gray = None
        self._auto_snapshot = None
        self._manual_active = False
        try:
            self._fill_table()
            self.act_export.setEnabled(False)
            self.btn_export_excel.setEnabled(False)
        except Exception:
            pass
        self.view.clear_overlay()
        self._update_pick_ui()
        self.lbl_hint.setText(tr("main.pick_the_array_to_define_the_search_grid"))

    def _on_point_picked(self, px: float, py: float):
        if self._pick_state < 0 or self._pick_state > 2:
            return
        slot = self._pick_state
        ox, oy = self.view.to_orig(px, py)
        self._picked[slot] = (ox, oy)
        self._pick_state = -1
        self.view.set_mode(MODE_NONE)
        self._refresh_pick_marks()
        if all(p is not None for p in self._picked):
            self._update_pick_ui()
            self._finish_pick()
        else:
            # 向导：点完自动跳到下一个空缺槽位，用户无需再点按钮
            nxt = next((i for i in range(3) if self._picked[i] is None), None)
            if nxt is not None:
                self.lbl_hint.setText(tr("main.label_recorded_continue_picking_the_remaining_points",
                                         label=[tr("main.origin"), tr("main.x"), tr("main.y")][slot]))
                self._start_pick(nxt)
            self._update_pick_ui()

    def _refresh_pick_marks(self):
        """重绘已点选的三点标记（含红蓝紫颜色与文字标签），未点的槽位置灰提示。"""
        self.view.clear_overlay()
        colors = ["#ff3b30", "#007aff", "#af52de"]
        labels = [tr("main.origin"), tr("main.x"), tr("main.y")]
        s = self.src.scale if self.src else 1.0
        for i, pt in enumerate(self._picked):
            if pt is not None:
                ox, oy = pt
                px, py = self._pv(ox / s, oy / s)
                self.view.add_marker(px, py, colors[i], label=labels[i])
        self._update_pick_ui()

    # ---------------- 建系正交化(B1) ----------------
    def _ortho_on(self) -> bool:
        """界面开关：建系正交化是否启用。"""
        try:
            return bool(getattr(self, "chk_ortho", None) is not None and self.chk_ortho.isChecked())
        except Exception:
            return False

    def _apply_ortho(self, g, why: str = ""):
        """按开关把 g 的两方向轴对称收拢为正交基（就地），并留痕。幂等。

        只改 x_dir_px / y_dir_px 的夹角与 orthogonalize 标志，不动 origin_px / pitch_mm /
        measured_pitch_mm / 名义值 / 校准 npz —— 不破坏红线 1、3、7。

        安全护栏（监督 #3 要求）：|gamma-90| 超过 ORTHO_MAX_DEV_DEG，或夹角落到
        <45° / >135° 的退化区，说明点选本身有误、或可能隐含真实方形度误差，
        此时**拒绝正交化并告警**，绝不静默"治好"（避免把真实不方度吃掉/掩盖点选错误）。
        """
        if g is None:
            return g
        # 无论开关如何，先把标志同步为"本网格是否已按正交基使用"，保证 to_dict 如实存盘
        g.orthogonalize = bool(self._ortho_on())
        if not self._ortho_on():
            return g
        before = float(g.axis_gamma_deg)
        dev = abs(before - 90.0)
        if dev > ORTHO_MAX_DEV_DEG or before < 45.0 or before > 135.0:
            self._gamma_click_deg = before
            self._ortho_corr_deg = 0.0
            self._ortho_rejected = True
            self._log(tr("main.frame_orthogonalization_skipped_picked_axis_angle_g_",
                         g=before, d=dev, m=ORTHO_MAX_DEV_DEG, s=why))
            self.statusBar().showMessage(
                tr("main.abnormal_picked_axis_angle_g_3f_frame_orthogonalizat", g=before), 8000)
            return g
        self._ortho_rejected = False
        x2, y2, corr = GridDefinition.orthonormalize(g.x_dir_px, g.y_dir_px)
        if abs(corr) > 1e-9:
            g.x_dir_px = tuple(float(v) for v in x2)
            g.y_dir_px = tuple(float(v) for v in y2)
            g._ortho_correction_deg = float(corr)
        # 只在"本次真的做了修正"或"尚无记录"时写点选夹角，
        # 避免检测前兜底（二次调用，corr=0）把原始点选夹角覆盖成 90.0000
        if abs(corr) > 1e-9 or getattr(self, "_gamma_click_deg", None) is None:
            self._gamma_click_deg = before
            self._ortho_corr_deg = float(corr)
        if abs(corr) > 1e-9:
            self._log(tr("main.frame_orthogonalization_picked_axis_angle_g_4f_force",
                         g=before, c=corr, w=(tr("main.s", s=why) if why else "")))
        return g

    def _on_ortho_toggled(self, *_args):
        """建系正交化开关切换：关闭时提示需重新建系（已正交的基不会自动回滚）。"""
        try:
            if self.grid is None:
                return
            if not self._ortho_on():
                self._log(tr("main.frame_orthogonalization_disabled_the_current_grid_is"))
                self.statusBar().showMessage(
                    tr("main.frame_orthogonalization_disabled_current_grid_not_ro"), 8000)
            else:
                self._log(tr("main.frame_orthogonalization_enabled_the_two_axes_will_be"))
        except Exception:
            pass

    def _record_ortho_into(self, params: dict):
        """把建系正交化结果并入导出参数表（参数记录页）。不改列结构，只增键。"""
        try:
            if getattr(self, "_gamma_click_deg", None) is None:
                return
            params[tr("main.picked_axis_angle")] = "%.4f" % float(self._gamma_click_deg)
            params[tr("main.frame_orthogonalization")] = "%+.4f" % float(getattr(self, "_ortho_corr_deg", 0.0) or 0.0)
            params[tr("main.frame_orthogonalization_enabled")] = tr("main.zh_0a60ac8f") if self._ortho_on() else tr("main.zh_c9744f45")
            if getattr(self, "_ortho_rejected", False):
                params[tr("main.frame_orthogonalization_skipped")] = tr("main.yes_abnormal_picked_angle")
        except Exception:
            pass

    def _finish_pick(self):
        self._pick_state = -1
        self.view.set_mode(MODE_NONE)
        o = self._picked[0]
        x = (self._picked[1][0] - o[0], self._picked[1][1] - o[1])
        y = (self._picked[2][0] - o[0], self._picked[2][1] - o[1])
        self.grid = GridDefinition(rows=self.sp_rows.value(), cols=self.sp_cols.value(),
                                   pitch_mm=self.sp_pitch.value(), dpi=self.sp_dpi.value(),
                                   origin_px=o, x_dir_px=x, y_dir_px=y)
        # B1：手点三点的两轴夹角无正交约束，按开关收拢为正交基（先建后修，便于留痕原夹角）
        self._apply_ortho(self.grid, tr("main.manual_3_point_frame"))
        # 手动基准快照：供自动建系手动优先（G1/G2/G5）对比与克隆基准使用
        self._manual_grid_snapshot = self.grid
        pts = self.grid.points_px()
        s = self.src.scale
        n = len(pts)
        stride = max(1, math.ceil(n / MAX_OVERLAY_ITEMS)) if n > MAX_OVERLAY_ITEMS else 1
        for i in range(0, n, stride):
            gx, gy = pts[i]
            px, py = self._pv(gx / s, gy / s)
            self.view.add_marker(px, py, "#8e8e93", radius=3.0, pen_w=1.0)
        if n > MAX_OVERLAY_ITEMS:
            self.statusBar().showMessage(
                tr("main.array_has_n_points_only_sampled_on_image_1_per_k_ful",
                   n=n, k=stride))
        self.lbl_hint.setText(tr("main.grid_ready_rows_cols_pitch_pitch_mm_n_points_click_d",
                                 rows=self.grid.rows, cols=self.grid.cols,
                                 pitch=self.grid.pitch_mm, n=len(pts)))
        self.statusBar().showMessage(tr("main.array_defined"))
        self._log(tr("main.zh_61b6594b",
                     rows=self.grid.rows, cols=self.grid.cols,
                     pitch=self.grid.pitch_mm, n=len(pts)))
        self._refresh_flow()

    # ---------------- 自动建系（D4：内角点建系 + 手动走向融合） ----------------
    def _on_auto_frame(self):
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("main.please_import_a_scan_image_first"))
            return
        if not self.calibrated:
            QMessageBox.warning(self, tr("main.prerequisite_missing"), tr("main.chessboard_calibration_not_passed_auto_framing_disab"))
            return
        if getattr(self, "_auto_frame_worker", None) is not None and self._auto_frame_worker.isRunning():
            return
        # 检测/拟合全部移入后台线程：4800dpi 大图角点识别耗时数十秒，
        # 留在主线程会整窗无响应（表现为“卡在棋盘格找角点”）。
        self._auto_frame_worker = AutoFrameThread(
            self.src, float(self.sp_pitch.value()), float(self.sp_dpi.value()),
            self._current_params(), parent=self)
        self._auto_frame_worker._gen = getattr(self, "_src_gen", 0)
        self._auto_frame_worker.done.connect(self._on_auto_frame_done)
        self._auto_frame_worker.finished.connect(self._on_auto_frame_finished)
        self.btn_auto_frame.setEnabled(False)
        self.statusBar().showMessage(tr("main.auto_detecting_inner_corners_and_fitting_grid_basis"))
        self._auto_frame_worker.start()

    def _on_auto_frame_finished(self):
        # G05: 只有当前线程结束时才清引用/复位按钮
        if self.sender() is getattr(self, "_auto_frame_worker", None):
            self._auto_frame_worker = None
            self.btn_auto_frame.setEnabled(True)

    def _on_auto_frame_done(self, payload):
        # G05: 旧图代次的结果直接丢弃，避免写到新图上
        if self._is_stale_result(self.sender()):
            self._log(tr("main.stale_async_result_discarded"))
            return
        if isinstance(payload, Exception):
            self._log(tr("main.zh_edb9a977", msg=str(payload)))
            self.statusBar().showMessage(tr("main.auto_framing_failed"))
            QMessageBox.warning(self, tr("main.auto_framing_failed"), tr("main.auto_detection_error_msg", msg=str(payload)))
            return
        if isinstance(payload, tuple):
            res, fit, origin_xy = payload
            if not fit.ok:
                self._log(tr("main.zh_edb9a977", msg=fit.message))
                self.statusBar().showMessage(tr("main.auto_framing_failed"))
                QMessageBox.warning(self, tr("main.auto_framing_failed"), fit.message)
                return
        else:
            res = payload
            self._log(tr("main.zh_d0eb983d", msg=res.message or tr("main.not_enough_corner_points")))
            self.statusBar().showMessage(tr("main.auto_framing_failed"))
            QMessageBox.warning(self, tr("main.auto_framing_failed"),
                                tr("main.no_regular_array_detected_msg", msg=res.message or tr("main.not_enough_corner_points")))
            return
        # 手动优先（P0/P1：G1/G2/G5）：三点齐全且已有手动网格时，
        # 手动为基准克隆 origin/x_dir/y_dir，自动补 rows/cols/measured_pitch_mm；
        # 差异超阈值弹窗让用户选基准，未超阈值直接以手动为准。
        manual_grid = getattr(self, "_manual_grid_snapshot", None)
        dpi = float(self.sp_dpi.value())
        nominal_pitch = float(self.sp_pitch.value())
        if manual_grid is not None and all(p is not None for p in self._picked[:3]):
            report = compare_manual_vs_auto(fit, manual_grid, dpi=dpi)
            warn = (report.ok and (report.origin_delta_mm > 0.05
                                   or report.angle_delta_deg > 0.5
                                   or report.pitch_delta_pct > 5.0))
            if warn:
                box = QMessageBox(self)
                box.setWindowTitle(tr("main.auto_framing_manual_vs_auto_deviation"))
                box.setText(tr("main.deviation_between_manual_baseline_and_auto_fit_msg_c",
                               msg=report.message))
                btn_manual = box.addButton(tr("main.use_manual_recommended"), QMessageBox.AcceptRole)
                btn_auto = box.addButton(tr("main.use_auto_fit"), QMessageBox.AcceptRole)
                btn_cancel = box.addButton(tr("main.cancel"), QMessageBox.RejectRole)
                box.exec_()
                clicked = box.clickedButton()
                if clicked == btn_cancel:
                    self.statusBar().showMessage(tr("main.auto_framing_cancelled_original_grid_kept"))
                    return
                use_manual = clicked == btn_manual
                if use_manual:
                    self.grid = build_grid_manual_first(
                        fit, manual_grid, res.rows, res.cols, dpi, nominal_pitch,
                        use_manual=True)
                    status_extra = tr("main.grid_built_from_manual_baseline")
                else:
                    mp = float((fit.pitch_x_px + fit.pitch_y_px) / 2.0) * 25.4 / max(1e-6, dpi)
                    self.grid = build_grid_from_frame(
                        fit, res.rows, res.cols, dpi=dpi,
                        nominal_pitch_mm=nominal_pitch, pick_vec=None,
                        measured_pitch_mm=mp)
                    status_extra = tr("main.grid_built_from_auto_fit")
                msg_extra = status_extra + ("；" + report.message if report.ok else "")
            else:
                self.grid = build_grid_manual_first(
                    fit, manual_grid, res.rows, res.cols, dpi, nominal_pitch,
                    use_manual=True)
                msg_extra = tr("main.manual_and_auto_agree_grid_built_from_manual_baselin")
        else:
            # 全自动保底（原逻辑：内角点建系 + 手动走向消歧/校验）
            pick_vec = None
            if all(p is not None for p in self._picked[:3]):
                pick_vec = (self._picked[1][0] - self._picked[0][0],
                            self._picked[1][1] - self._picked[0][1])
            _cand, warn = validate_frame_vs_pick(fit, pick_vec)
            mp = float((fit.pitch_x_px + fit.pitch_y_px) / 2.0) * 25.4 / max(1e-6, dpi)
            self.grid = build_grid_from_frame(fit, res.rows, res.cols,
                                              dpi=dpi,
                                              nominal_pitch_mm=nominal_pitch,
                                              pick_vec=pick_vec,
                                              measured_pitch_mm=mp)
            msg_extra = warn or tr("main.auto_framing_done")
        # v3.4 wizard consistency: auto-framing builds the grid directly and does not
        # write _picked; explain that in the wizard progress line.
        try:
            self.lbl_pick_progress.setText(tr("main.auto_frame_built_frame_no_manual_points"))
        except Exception:
            pass
        # 刷新预览标记（与 _finish_pick 一致）
        pts = self.grid.points_px()
        s = self.src.scale
        n = len(pts)
        self.view.clear_overlay()
        stride = max(1, math.ceil(n / MAX_OVERLAY_ITEMS)) if n > MAX_OVERLAY_ITEMS else 1
        for i in range(0, n, stride):
            gx, gy = pts[i]
            px, py = self._pv(gx / s, gy / s)
            self.view.add_marker(px, py, "#8e8e93", radius=3.0, pen_w=1.0)
        if n > MAX_OVERLAY_ITEMS:
            self.statusBar().showMessage(
                tr("main.auto_framing_done_rows_cols_sampled_on_image_full_da",
                   rows=self.grid.rows, cols=self.grid.cols, warn=msg_extra))
        else:
            self.statusBar().showMessage(
                tr("main.auto_framing_done_rows_cols_pitch_pitch_mm_n_pts_tot",
                   rows=self.grid.rows, cols=self.grid.cols,
                   pitch=self.grid.pitch_mm, n=len(pts), warn=msg_extra))
        self.lbl_hint.setText(tr("main.grid_generated_auto_framing_rows_cols_measured_pitch",
                                 rows=self.grid.rows, cols=self.grid.cols,
                                 p=self.grid.measured_pitch_mm, n=len(pts)))
        self._log(tr("main.zh_cd673482", rows=self.grid.rows,
                     cols=self.grid.cols, extra=msg_extra))
        self._refresh_flow()
        self._on_center_origin(origin_xy)

    def _on_center_origin(self, origin_xy):
        """B3：写入会话级精测原点（阵列几何中心十字亚像素坐标），建系后任何流程不得覆盖。"""
        self.origin_xy = origin_xy
        if origin_xy is not None:
            self._log(tr("main.origin_refined_array_center_cross_subpixel_coord_x_2",
                         x=origin_xy[0], y=origin_xy[1]))
        else:
            self._log(tr("main.origin_refinement_not_converged_fallback_to_affine_f"))

    # ---------------- 检测 ----------------
    def _on_detect(self):
        # 手动发起检测时重置自动校正计数（防 auto 模式防循环阈值残留）
        self._auto_correct_count = 0
        # P0-3：重置本次诊断记录，导出时并入参数区，事后可追溯决策链
        self._diag = {}
        # 入口仅负责重置计数，实际检测逻辑在 _run_detect 中执行；
        # 自动网格校正后的重测直接调用 _run_detect，不清空计数，保证防循环上限可达。
        self._run_detect()

    def _run_detect(self):
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("main.please_import_a_scan_image_first"))
            return
        if not self.calibrated:
            QMessageBox.warning(self, tr("main.prerequisite_missing"), tr("main.chessboard_calibration_failed_cross_detection_is_dis"))
            return
        if self.grid is None:
            QMessageBox.information(self, tr("common.info"), tr("main.please_define_the_array_first"))
            return
        # B1：检测前统一兜底正交化（覆盖自动建系/网格自校正等所有建系路径）。
        # _apply_ortho 幂等：已正交时修正量为 0，不会累积，也不会改动 origin/pitch。
        self._apply_ortho(self.grid, tr("main.pre_detection_fallback"))
        if self._manual_active:
            self._manual_active = False
            self._set_manual_btn_active(False)
            self.view.set_mode(MODE_NONE)
        pts = self.grid.points_px()
        pitch = self.grid.pitch_px
        p = self._current_params()
        self.btn_detect.setEnabled(False)
        self.btn_manual.setEnabled(False)  # 检测期间禁用手动识别，避免与检测线程抢 CPU
        self.progress.setValue(0)
        # 限流：检测线程池至多占一半物理核（封顶 4），避免全核满载饿死 GUI 线程。
        # 旧实现 min(8, cpu_count) 在 12 核机器上起 8 个线程并发 OpenCV 重计算，
        # Windows 调度器无暇响应 GUI 消息队列，表现为“能多动两下又卡死”。
        n_workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        # 异常/超时兜底后旧线程可能仍在后台运行，让位等待再开新检测，避免双线程竞争。
        # 注意：不能用 getattr(self,"thread",...) 读旧线程——QObject 自带 thread() 方法，
        # 会取到 built-in method 而非线程对象，调用 isRunning() 直接崩溃；统一用 _detect_thread。
        old = getattr(self, "_detect_thread", None)
        if old is not None and old.isRunning():
            old.wait(500)
        self._detect_thread = DetectorThread(self.src, pts, pitch, p, max_workers=n_workers,
                                             parent=self, ref_angle_deg=self._grid_ref_angle_deg())
        self._detect_thread._gen = getattr(self, "_src_gen", 0)   # G06: 代次
        self._detect_thread.setPriority(QThread.LowPriority)  # 让出调度优先级，保障界面响应
        self._detect_thread.progress.connect(lambda v: self.progress.setValue(int(v * 100)))
        self._detect_thread.done.connect(self._on_detect_done)
        self._detect_thread.error.connect(self._on_detect_error)
        # 8s 看门狗：极端场景（线程卡死不发任何信号）下强制恢复 UI，避免永久"重新检测中…"
        self._detect_guard = QTimer(self)
        self._detect_guard.setSingleShot(True)
        self._detect_guard.timeout.connect(self._on_detect_timeout)
        self._detect_guard.start(8000)
        _round = int(getattr(self, "_auto_correct_count", 0))
        _suffix = tr("main.detect_auto_round_suffix", n=_round) if _round > 0 else ""
        self._log(tr("main.zh_24023d4a", n=len(pts), w=n_workers) + _suffix)
        self._detect_thread.start()

    def _on_detect_error(self, tb: str):
        # 检测线程异常/超时兜底：恢复 UI 并如实报错，严禁静默失败（用户红线）。
        g = getattr(self, "_detect_guard", None)
        if g is not None:
            g.stop()
        self.btn_detect.setEnabled(True)
        self.btn_manual.setEnabled(True)
        self.progress.setValue(0)
        self.statusBar().showMessage(tr("main.detection_failed_ui_restored_traceback_written_to_te"))
        lines = [ln for ln in (tb or "").splitlines() if ln.strip()]
        brief = lines[-1] if lines else tb
        self._log(tr("main.zh_44fe6b61", err=brief))
        QMessageBox.critical(self, tr("main.detection_failed"), tr("main.exception_in_cross_detection_thread_ui_restored_err_", err=brief))

    def _on_detect_timeout(self):
        # 8s 看门狗：线程未发出任何信号视为卡死，断开连接避免后台结果再覆盖 UI，强制恢复。
        th = getattr(self, "_detect_thread", None)
        if th is not None and th.isRunning():
            try:
                th.done.disconnect(self._on_detect_done)
                th.error.disconnect(self._on_detect_error)
            except Exception:
                pass
            self._on_detect_error(tr("main.cross_detection_timed_out_8s_ui_forcibly_restored_an"))

    def _on_detect_done(self, results: List[Optional[CrossResult]]):
        # G06: 检测期间换图 -> 旧代次结果丢弃
        if self._is_stale_result(self.sender()):
            self._log(tr("main.stale_async_result_discarded"))
            return
        if self.grid is None:
            # G02: detection finished after the frame was reset/re-picked (grid=None);
            # downstream code dereferences grid, so discard the stale result instead of crashing.
            self._log(tr("main.stale_detection_discarded_grid_reset"))
            try:
                self.btn_detect.setEnabled(self.calibrated)
                self.progress.setValue(0)
            except Exception:
                pass
            return
        g = getattr(self, "_detect_guard", None)
        if g is not None:
            g.stop()
        self.results = results
        # ---- 方案A：串行校验与最近邻重匹配 ----
        # 网格原点/方向偏差较大时检测窗口会框住邻点十字，表现为全部检到但点号串行。
        # 先把每个成功检测点重匹配到最近的网格理论点，修正点号错位后再进入网格自校正，
        # 避免用错位的行列拟合出错误的"校正网格"。
        rematch_note = ""
        if self.grid is not None:
            gp = self.grid.points_px()
            if gp.shape[0] == len(results) and len(results) > 0:
                new_res, _m, n_rematch = rematch_results_to_grid(
                    results, gp, self.grid.pitch_px, max_shift_ratio=0.5)
                if n_rematch > 0:
                    self.results = new_res
                    results = new_res
                    rematch_note = tr("main.detected_point_index_shuffling_rematched_n_points_to",
                                      n=n_rematch)
        self.btn_detect.setEnabled(self.calibrated)
        self.btn_manual.setEnabled(True)
        self.progress.setValue(100)
        # 自动检测完成后重置手动状态：手动点清空并恢复为本次自动结果快照
        self._manual_active = False
        self._manual_history = []
        self._auto_snapshot = list(results)
        self._manual_sel_idx = -1
        self.view.clear_manual_selection()
        self.view.set_mode(MODE_NONE)
        self._set_manual_btn_active(False)
        self._redraw_marks()
        n_ok = sum(1 for r in results if r is not None and r.ok)
        n_lowconf = sum(1 for r in results if r is not None and not r.ok)
        self._log(tr("main.zh_dfa3b41a",
                     ok=n_ok, total=len(results), fail=len(results) - n_ok))
        if len(results) > MAX_OVERLAY_ITEMS:
            stride = max(1, math.ceil(len(results) / MAX_OVERLAY_ITEMS))
            self.statusBar().showMessage(
                tr("main.detection_done_ok_total_ok_too_many_points_sampled_o",
                   ok=n_ok, total=len(results), k=stride), 10000)
        self._fill_table()
        self._refresh_flow()
        if n_ok < len(results):
            # 改造2：找不到十字必须明确报错，严禁静默失败、严禁强制手动拖拽。
            # 弹窗列出具体失败点编号与位置（行/列 + 理论像素 + 名义值），
            # 结果表该点标记"未检出"；手动拖拽仅作为可选辅助（按钮仍在）。
            # 方案D：低置信十字（ok=False, fail_reason=LOW_CONF）同样计入失败清单。
            fail_idx = [i for i, r in enumerate(results) if r is None or not r.ok]
            rows_, cols_ = self.grid.rows, self.grid.cols
            nom = self._nominal_mm_list()
            lines = []
            for i in fail_idx[:15]:
                rr, cc = i // cols_ + 1, i % cols_ + 1
                gx, gy = self.grid.points_px()[i]
                nm = f"({nom[i][0]:.3f}, {nom[i][1]:.3f})" if nom and i < len(nom) else "-"
                lines.append(tr("main.point_idx_row_row_col_col_theoretical_pixel_px_0f_py",
                                idx=self._order_display_no(i), row=rr, col=cc, px=gx, py=gy, nm=nm))
            more = len(fail_idx) - len(lines)
            detail = "\n".join(lines) + ("\n" + tr("main.and_n_more", n=more) if more > 0 else "")
            extra = tr("main.n_low_confidence_marked_detect_fail", n=n_lowconf) if n_lowconf else ""
            msg = tr("main.detection_done_ok_total_ok_fail_crosses_not_detected",
                     ok=n_ok, total=len(results), fail=len(fail_idx), extra=extra, detail=detail)
            QMessageBox.warning(self, tr("main.undetected_cross_points"), msg)
            self.statusBar().showMessage(tr("main.fail_cross_points_not_detected_see_popup_for_details", fail=len(fail_idx)))
        else:
            # P0-1 修复：网格自动校正与刚体精化不得互斥短路。
            # 原实现在校正返回 True 时立即 return，精化永远执行不到——三测 RMS 0.6447
            # 与首测 0.6449 持平（精化未生效）的直接原因。现改为：无论是否触发网格
            # 重测，本次检测结果都先过一遍刚体精化（θ≈0 时幂等 no-op），再决定是否
            # return 等重测回调；重测结果会再次进入本函数，最多收敛而非叠加。
            corrected = False
            try:
                corrected = bool(self._maybe_correct_grid(results, auto=True))
            except Exception as exc:  # P0-4：校正异常严禁吞掉后续精化与结果呈现
                self._log(tr("main.zh_4a9133e6", err=exc))
                try:
                    import traceback as _tb
                    self._log(_tb.format_exc())
                except Exception:
                    pass
                corrected = False
            try:
                self._on_refine_axis(auto=True)
            except Exception as exc:
                self._log(tr("main.zh_30859292", err=exc))
                try:
                    import traceback as _tb
                    self._log(_tb.format_exc())
                except Exception:
                    pass
            if corrected:
                return  # 网格已按实际阵列校正并触发重测，最终结果由重测回调再次进入本函数
            # P1-2：本轮检测流程完整走完（未再触发重测），自动校正计数归零，
            # 避免上一轮残留的计数把后续首次校正直接顶到 3 次上限而静默放弃。
            self._auto_correct_count = 0
            note = rematch_note + tr("main.detection_done_ok_total_ok", ok=n_ok, total=len(results))
            self.statusBar().showMessage(note)

    def _maybe_correct_grid(self, results: List[Optional[CrossResult]], auto: bool = False) -> bool:
        """网格自校正：检测结果与当前网格偏差较大时，用成功点拟合实际阵列
        （原点/间距/方向），经用户确认后更新网格并重新检测。返回 True 表示已触发重测。

        auto=True：跳过弹窗直接校正并重测（无人值守自动执行时使用，例如
        串行重匹配后系统已确认网格整体偏移，无需人工确认）。
        """
        # 防循环：auto 模式连续校正上限 3 次，超过则放弃（避免校正-重测死循环）
        self._diag[tr("main.zh_601030df")] = int(getattr(self, "_auto_correct_count", 0))
        if auto and getattr(self, "_auto_correct_count", 0) >= 3:
            # 达上限不能静默放弃：必须落盘日志 + 报表留痕 + 明确告警并建议重建系，
            # 否则现场会拿到一份"网格没校正过、但也没人知道"的结果（v3.2 的缺陷）。
            _prev = self._diag.get(tr("main.zh_65153154"), "N/A")
            self._diag[tr("main.zh_ff26e9e0")] = tr("main.zh_fd558674")
            self._diag[tr("main.grid_correction_hit_limit")] = tr("main.zh_0a60ac8f")
            self._diag[tr("main.predicted_edge_deviation_when_giving_up_mm")] = _prev
            self._log(tr("main.auto_grid_correction_reached_its_limit_3_rounds_furt",
                         p=_prev))
            self.statusBar().showMessage(
                tr("main.auto_grid_correction_hit_its_limit_and_has_been_aban"), 15000)
            return False
        if self.grid is None or self.src is None:
            return False
        ok_pts = [(i, r) for i, r in enumerate(results) if r is not None and r.ok]
        if len(ok_pts) < 3:
            return False
        cols = self.grid.cols
        # 成功点已是原始分辨率坐标（DetectorThread 始终在原始灰度上检测）
        data = []
        for i, r in ok_pts:
            data.append((r.x, r.y, i // cols, i % cols))
        A, b = [], []
        for x, y, rc, cc in data:
            A.append([1, cc, rc, 0, 0, 0]); b.append(x)
            A.append([0, 0, 0, 1, cc, rc]); b.append(y)
        A = np.array(A, np.float64); b = np.array(b, np.float64)
        try:
            sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        except Exception:
            return False
        ox, vx_x, vy_x, oy, vx_y, vy_y = sol
        vx = np.array([vx_x, vx_y], np.float64)
        vy = np.array([vy_x, vy_y], np.float64)
        pitch_new = float(np.linalg.norm(vx))
        if pitch_new <= 0:
            return False
        pitch_old = float(self.grid.pitch_px)
        # 拟合残差过大说明模型不适配（如非规则阵列），不校正
        res = (A @ sol) - b
        rms = float(np.sqrt((res * res).mean()))
        if rms > 0.15 * pitch_new:
            return False
        # 退化分布保护：成功点必须至少覆盖 2 行 且 2 列，否则行/列方向不可估。
        # lstsq 对退化矩阵仍会给"精确"解（rms≈0），但网格会被拟合歪（单行/对角分布典型）。
        rc_set = {rc for _, _, rc, _ in data}
        cc_set = {cc for _, _, _, cc in data}
        if len(rc_set) < 2 or len(cc_set) < 2:
            return False
        # 方向合理性：拟合方向应与原网格方向基本一致，且 x/y 方向接近正交，
        # 避免拟合出旋转/压扁的假网格
        gx = np.asarray(self.grid.x_dir_px, np.float64)
        gy = np.asarray(self.grid.y_dir_px, np.float64)
        nvy = float(np.linalg.norm(vy))
        if nvy <= 0:
            return False
        def _ang(u, v):
            nu = float(np.linalg.norm(u)); nv = float(np.linalg.norm(v))
            if nu <= 0 or nv <= 0:
                return 90.0
            return float(np.degrees(np.arccos(np.clip(abs(np.dot(u, v)) / (nu * nv), 0.0, 1.0))))
        if _ang(vx, gx) > 8.0 or _ang(vy, gy) > 8.0:
            return False
        if _ang(vx, vy) < 80.0 or _ang(vx, vy) > 100.0:
            return False
        # 间距合理性：拟合间距应与原网格同一量级
        if not (0.5 <= pitch_new / pitch_old <= 2.0):
            return False
        dpi = self.sp_dpi.value()
        pitch_mm_new = pitch_new * 25.4 / dpi
        half_r = (self.grid.rows - 1) / 2.0
        half_c = (self.grid.cols - 1) / 2.0
        half_max = max(half_r, half_c)
        # 间距偏差取 x/y 两方向较大者；方向偏差取两轴夹角较大者
        pct_x = abs(float(np.linalg.norm(vx)) - pitch_old) / pitch_old
        pct_y = abs(float(np.linalg.norm(vy)) - pitch_old) / pitch_old
        pct = max(pct_x, pct_y)
        ang_x = _ang(vx, gx); ang_y = _ang(vy, gy)
        ang_rad = max(ang_x, ang_y) * np.pi / 180.0
        d_origin = float(np.linalg.norm(np.array([ox, oy]) - np.asarray(self.grid.origin_px, np.float64)))
        d_origin_mm = d_origin * 25.4 / dpi
        # 预测：沿用当前网格不加校正时，阵列边缘点相对名义值预计最大偏差
        # （间距/方向偏差沿阵列线性累积，原点偏移为平移项）
        pred_edge = pct * half_max * pitch_mm_new + ang_rad * half_max * pitch_mm_new + d_origin_mm
        TOL_MM = 0.10  # 2× 表格公差(±0.05mm)；预计偏差不超过此值则无需校正
        self._diag[tr("main.zh_65153154")] = float(pred_edge)
        if pred_edge <= TOL_MM:
            self._diag[tr("main.zh_ff26e9e0")] = tr("main.zh_2cc3c0e1", v=pred_edge, t=TOL_MM)
            return False
        if auto:
            pass  # 无人值守自动模式：跳过弹窗直接校正重测
        else:
            ret = QMessageBox.question(
                self, tr("main.grid_correction"),
                tr("main.grid_parameters_deviate_significantly_from_the_actua",
                   old=pitch_old, new=pitch_new, pct=pct * 100, ang=max(ang_x, ang_y),
                   d=d_origin, pred=pred_edge),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret != QMessageBox.Yes:
                return False
        nvx = np.linalg.norm(vx); nvy = np.linalg.norm(vy)
        # 名义值红线：pitch_mm（设计名义间距）严禁被实测反推覆盖，恒为用户在界面设定的设计值；
        # 实测阵列间距只写入 measured_pitch_mm（供检测窗口对准真实十字、状态栏展示实测间距）。
        self.grid = GridDefinition(
            rows=self.grid.rows, cols=self.grid.cols,
            pitch_mm=self.sp_pitch.value(), dpi=dpi,
            origin_px=(float(ox), float(oy)),
            x_dir_px=(float(vx_x / nvx), float(vx_y / nvx)),
            y_dir_px=(float(vy_x / nvy), float(vy_y / nvy)),
            measured_pitch_mm=round(pitch_mm_new, 4))
        # 不更新 sp_pitch：名义值恒为用户设计间距（红线：名义值严禁反推）
        # 状态栏提示带 8s 超时自动消失（配合 _run_detect 内的 8s 看门狗，
        # 即使线程异常/卡死也不会永久停留在"重新检测中…"）
        self.statusBar().showMessage(tr("main.grid_corrected_to_actual_array_measured_pitch_mm_mm_", mm=pitch_mm_new), 8000)
        self._auto_correct_count = getattr(self, "_auto_correct_count", 0) + 1
        self._diag[tr("main.zh_2ac41737")] = tr("main.zh_9c86f1a5",
                                        mm=pitch_mm_new, pred=pred_edge)
        self._diag[tr("main.zh_ff26e9e0")] = tr("main.zh_4d37172e",
                                            n=self._auto_correct_count)
        self._diag[tr("main.zh_601030df")] = int(self._auto_correct_count)
        QTimer.singleShot(0, self._run_detect)
        return True

    # ---------------- 刚体精化辅助（P0-2 择优自检用） ----------------
    @staticmethod
    def _dirs_rotated(xdir, ydir, deg_ccw: float):
        """返回把 (xdir, ydir) 绕原点逆时针旋转 deg_ccw 度后的单位向量对（不改原对象）。"""
        a = math.radians(deg_ccw)
        c, s = math.cos(a), math.sin(a)
        out = []
        for d in (xdir, ydir):
            vx, vy = float(d[0]), float(d[1])
            n = math.hypot(vx, vy)
            if n < 1e-9:
                out.append((vx, vy))
                continue
            rx = vx * c - vy * s
            ry = vx * s + vy * c
            out.append((rx / n, ry / n))
        return out[0], out[1]

    def _table_rms_mm(self) -> Optional[float]:
        """按当前 grid 方向轴计算表格合成 RMS（mm），计算口径与导出表格完全一致
        （实测中心归零 → grid.to_phys 投影 → 与固定名义值比较）。"""
        if self.grid is None or not self.results:
            return None
        nominal = self._nominal_mm_list()
        if not nominal:
            return None
        center = self._center_measured_mm(self.results)
        sq = []
        for i, r in enumerate(self.results):
            if r is None or not r.ok or i >= len(nominal):
                continue
            mm_x, mm_y = self._to_mm(r.x, r.y)
            if center:
                mm_x, mm_y = mm_x - center[0], mm_y - center[1]
            a, b = self.grid.to_phys(mm_x, mm_y)
            tx, ty = nominal[i]
            sq.append((a - tx) ** 2 + (b - ty) ** 2)
        if not sq:
            return None
        return float(math.sqrt(sum(sq) / len(sq)))

    def _rms_with_dirs(self, xdir, ydir) -> Optional[float]:
        """临时切换方向轴计算表格 RMS 后还原（用于旋转符号择优，不污染 grid）。"""
        if self.grid is None:
            return None
        old = (tuple(self.grid.x_dir_px), tuple(self.grid.y_dir_px))
        try:
            self.grid.x_dir_px = tuple(xdir)
            self.grid.y_dir_px = tuple(ydir)
            return self._table_rms_mm()
        finally:
            self.grid.x_dir_px, self.grid.y_dir_px = old

    def _on_refine_axis(self, auto: bool = False):
        """方案 A：检测后刚体建系精化。

        用成功检测点（亚像素，原始分辨率）对名义行列网格做 SVD 刚体拟合
        （仅旋转+平移 3 DOF，禁剪切/缩放），得到阵列真实方向角 θ_refine；
        经用户确认后旋转 to_phys 方向轴（grid.x_dir/y_dir）并刷新表格。
        中心基准仍由实测拟合阵列中心归零（_center_measured_mm），不反推
        名义值、不改写校准 npz、不触碰 pitch_mm 与检测网格原点。

        auto=True：检测完成后自动精化（无人值守），跳过所有弹窗与确认；
        防护失败（方向角过大/残差过大/数据不足）时静默放弃，仅日志提示。
        """
        if self.grid is None:
            if not auto:
                QMessageBox.information(self, tr("common.info"), tr("main.define_the_array_first"))
            return
        if not self.results:
            if not auto:
                QMessageBox.information(self, tr("common.info"), tr("main.please_run_detection_first"))
            return
        ok_pts = [(i, r) for i, r in enumerate(self.results) if r is not None and r.ok]
        if len(ok_pts) < 3:
            if not auto:
                QMessageBox.warning(self, tr("main.refinement_failed"), tr("main.fewer_than_3_detected_points_rigid_refinement_unavai"))
            else:
                self._log(tr("main.zh_2c5bc373"))
            return
        cols = self.grid.cols
        rows = self.grid.rows
        # 实测阵列中心像素（复用 _center_measured_mm 同一仿射模型，中心归零基准保持实测拟合）
        data = [(r.x, r.y, i // cols, i % cols) for i, r in ok_pts]
        A, b = [], []
        for x, y, rc, cc in data:
            A.append([1, cc, rc, 0, 0, 0]); b.append(x)
            A.append([0, 0, 0, 1, cc, rc]); b.append(y)
        try:
            sol, *_ = np.linalg.lstsq(np.array(A, np.float64), np.array(b, np.float64), rcond=None)
        except Exception:
            if not auto:
                QMessageBox.warning(self, tr("main.refinement_failed"), tr("main.array_center_fit_failed"))
            return
        ox, vx_x, vy_x, oy, vx_y, vy_y = sol
        # P1-1 修复：几何中心取 (n-1)/2（与 nominal_points_mm 的 c0/r0 同口径）。
        # 原 cols//2 对偶数行列会偏移半个格（与 _center_measured_mm 归零基准错位，
        # 引入半格级的伪平移），奇数阵列（如 5×5）两者数值相同。
        cx = ox + vx_x * ((cols - 1) / 2.0) + vy_x * ((rows - 1) / 2.0)
        cy = oy + vx_y * ((cols - 1) / 2.0) + vy_y * ((rows - 1) / 2.0)
        # 名义行列 × 设计标称像素间距（红线：名义值用设计值，禁实测反推）
        pitch_px = self.grid.pitch_mm * self.grid.dpi / 25.4
        xv = np.asarray(self.grid.x_dir_px, np.float64)
        yv = np.asarray(self.grid.y_dir_px, np.float64)
        nxv = float(np.linalg.norm(xv))
        nyv = float(np.linalg.norm(yv))
        if nxv < 1e-9 or nyv < 1e-9:
            if not auto:
                QMessageBox.warning(self, tr("main.refinement_failed"), tr("main.direction_axis_not_defined"))
            return
        xu = xv / nxv
        yu = yv / nyv
        cos = float(xu @ yu)
        det = 1.0 - cos * cos
        if abs(det) < 1e-6:
            if not auto:
                QMessageBox.warning(self, tr("main.refinement_failed"), tr("main.x_y_direction_axes_are_parallel_cannot_project"))
            return
        src, dst = [], []
        for x, y, rc, cc in data:
            v = np.array([x - cx, y - cy], np.float64)
            a = ((v @ xu) - cos * (v @ yu)) / det
            b_ = ((v @ yu) - cos * (v @ xu)) / det
            src.append([a, b_])
            dst.append([cc * pitch_px, rc * pitch_px])
        src = np.array(src, np.float64)
        dst = np.array(dst, np.float64)
        # SVD 刚体（相似变换，无缩放）：aligned = src @ R.T + t
        cs_ = src.mean(axis=0)
        cd_ = dst.mean(axis=0)
        H = (src - cs_).T @ (dst - cd_)
        U, _, Vt = np.linalg.svd(H)
        Rm = Vt.T @ U.T
        if np.linalg.det(Rm) < 0:
            Vt[-1, :] *= -1.0
            Rm = Vt.T @ U.T
        t = cd_ - Rm @ cs_
        aligned = src @ Rm.T + t
        res = dst - aligned
        rms_px = float(np.sqrt((res * res).mean()))
        theta_deg = float(np.degrees(np.arctan2(Rm[1, 0], Rm[0, 0])))
        t_mm_x = t[0] * 25.4 / self.grid.dpi
        t_mm_y = t[1] * 25.4 / self.grid.dpi
        # 防护：方向偏差过大或残差过大说明数据不适配刚体模型（离群点/点序错位），拒绝精化
        if abs(theta_deg) > 2.0:
            if not auto:
                QMessageBox.warning(self, tr("main.refinement_failed"),
                                    tr("main.fitted_direction_deviation_ang_3f_too_large_possible",
                                       ang=theta_deg))
            else:
                self._log(tr("main.zh_23f4e4d3", ang=theta_deg))
            return
        if rms_px > 0.15 * pitch_px:
            if not auto:
                QMessageBox.warning(self, tr("main.refinement_failed"),
                                    tr("main.rigid_fit_residual_rms_rms_3f_px_exceeds_15_of_pitch",
                                       rms=rms_px))
            else:
                self._log(tr("main.zh_9034640c", rms=rms_px))
            return
        half = max((rows - 1) / 2.0, (cols - 1) / 2.0)
        edge_improve_mm = abs(theta_deg) * np.pi / 180.0 * half * self.grid.pitch_mm
        if auto:
            ret = QMessageBox.Yes  # 无人值守自动精化：跳过确认，直接应用
        else:
            ret = QMessageBox.question(
                self, tr("main.rigid_frame_refinement"),
                tr("main.detection_done_array_vs_picked_axis_deviation_ang_4f",
                   ang=theta_deg, edge=edge_improve_mm, tx=t_mm_x, ty=t_mm_y),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return
        # P0-2 修复：旋转符号择优自检。原实现固定 rotate_dirs(-theta_deg)，
        # 三测真实数据验证该符号会把合成 RMS 由 0.6526 放大到 1.2831（符号写反，
        # 代码注释里的推导结论错误）。现改为三候选实测择优：不旋转 / −θ / +θ
        # 各算一次表格合成 RMS，取最优且必须优于不旋转（阈值 0.1%）才应用，
        # 否则保持原方向轴——宁可不精化，也绝不让误差变大。
        base_x, base_y = tuple(self.grid.x_dir_px), tuple(self.grid.y_dir_px)
        rms_no = self._rms_with_dirs(base_x, base_y)
        # v3.2 方案A：候选池由「三候选」扩为「角度细扫」。
        # 原因（3.1 首测实测）：SVD 拟合发生在投影后的 (a,b) 基上，而该基本身非正交
        # （本例方向轴张角 89.706°），相似变换被约束后给出折中角 0.1765°，真实最优为
        # 0.4318°；三候选只在 ±拟合角里挑，候选池太窄，永远够不到最优点。
        # 改为纯实测扫描：±2° 粗扫 0.1° → 最优附近细扫 0.01°，仍只旋转不剪切。
        cands = []
        rms_by_ang = {}
        for ang in (-theta_deg, theta_deg):
            rx, ry = self._dirs_rotated(base_x, base_y, ang)
            rms_c = self._rms_with_dirs(rx, ry)
            if rms_c is not None:
                rms_by_ang[float(ang)] = rms_c
        if rms_no is not None:
            n_scan = 0
            for ang in np.arange(-2.0, 2.0001, 0.1):
                rx, ry = self._dirs_rotated(base_x, base_y, float(ang))
                r = self._rms_with_dirs(rx, ry)
                n_scan += 1
                if r is not None:
                    cands.append((r, float(ang), rx, ry))
            if cands:
                best_c = min(cands, key=lambda t: t[0])
                for ang in np.arange(best_c[1] - 0.1, best_c[1] + 0.1001, 0.01):
                    rx, ry = self._dirs_rotated(base_x, base_y, float(ang))
                    r = self._rms_with_dirs(rx, ry)
                    n_scan += 1
                    if r is not None:
                        cands.append((r, float(ang), rx, ry))
            self._scan_count = n_scan
        best = min(cands, key=lambda t: t[0]) if cands else None
        applied = bool(best is not None and rms_no is not None and best[0] < rms_no * 0.999)
        if applied:
            self.grid.x_dir_px, self.grid.y_dir_px = best[2], best[3]
            ang_applied = float(best[1])
            rms_after = float(best[0])
        else:
            self.grid.x_dir_px, self.grid.y_dir_px = base_x, base_y
            ang_applied = 0.0
            rms_after = rms_no
        scan_best = float(best[1]) if best is not None else None
        scan_rms = float(best[0]) if best is not None else None
        rms_minus = rms_by_ang.get(float(-theta_deg))
        rms_plus = rms_by_ang.get(float(theta_deg))
        self._diag.update({
            tr("main.zh_1f29ab41"): float(theta_deg),
            tr("main.zh_c243a6f0"): float(rms_px),
            tr("main.refine_scan_best_angle"): scan_best,
            tr("main.refine_scan_best_rms_mm"): scan_rms,
            tr("main.refine_scan_candidates"): int(getattr(self, "_scan_count", 0) or 0),
            tr("main.zh_802539c8"): ang_applied,
            tr("main.zh_6cbb6199"): (float(rms_no) if rms_no is not None else None),
            tr("main.zh_a784c8fa"): (float(rms_after) if rms_after is not None else None),
            tr("main.zh_404f2bad"): tr("main.zh_0a60ac8f") if applied else tr("main.zh_c9744f45"),
        })
        self._log(tr("main.rigid_refinement_fitted_ang_4f_fit_rms_rms_3f_px_ang",
                     ang=theta_deg, rms=rms_px,
                     n=int(getattr(self, "_scan_count", 0) or 0),
                     sb=("%.4f" % scan_best) if scan_best is not None else "N/A",
                     no=("%.4f" % rms_no) if rms_no is not None else "N/A",
                     sr=("%.4f" % scan_rms) if scan_rms is not None else "N/A",
                     app=ang_applied, yn=tr("main.applied_yes") if applied else tr("main.applied_no")))
        self._fill_table()
        self._redraw_marks()  # 方向轴已旋转：未检出点红标按新理论位置重绘，保持图上标号与表格基准一致
        if applied:
            self.statusBar().showMessage(
                tr("main.applied_rigid_frame_refinement_direction_axis_rotate", ang=ang_applied), 8000)
        else:
            self.statusBar().showMessage(
                tr("main.zh_b5b7e490",
                   ang=theta_deg), 8000)

    def _toggle_drag(self):
        if not self.results:
            QMessageBox.information(self, tr("common.info"), tr("main.please_run_detection_first"))
            return
        if self._manual_active:
            self._manual_active = False
            self._set_manual_btn_active(False)
        self.view.set_mode(MODE_DRAG)
        s = self.src.scale if self.src else 1.0
        pts = []
        for i, r in enumerate(self.results):
            if r is not None:
                pts.append(self._pv(r.x / s, r.y / s))
            else:
                # 失败点用网格理论位置（与 _on_detect_done 红标一致），便于拖到实际十字中心
                gx, gy = self.grid.points_px()[i]
                pts.append(self._pv(gx / s, gy / s))
        self.view._pick_points = pts
        self.lbl_hint.setText(tr("main.drag_green_markers_to_fine_tune_drag_red_failed_poin"))

    def _on_point_dragged(self, idx: int, px: float, py: float):
        """拖拽高频逐帧回调：仅更新坐标并移动标记，不做表格/状态栏等重活
        （收尾在 _on_drag_finished 一次性执行，避免大阵列逐帧全表/全场景刷新卡死）。"""
        if idx < 0 or idx >= len(self.results) or self.src is None:
            return
        ps = getattr(self.src, "preview_scale", 1.0)
        s = self.src.scale if self.src else 1.0
        r = self.results[idx]
        if r is None:
            r = CrossResult(px * ps * s, py * ps * s, True, "manual", 0.0)
            self.results[idx] = r
        else:
            r.x, r.y = px * ps * s, py * ps * s
            if not r.ok:
                # 低置信点（ok=False, fail_reason=LOW_CONF）被拖拽修正 → 转手动 OK
                r.ok = True
                r.method = "manual"
                r.conf = 1.0
                r.fail_reason = ""
            r.method = "manual"
        self.view.set_pick_point(idx, px, py)

    def _on_drag_finished(self, idx: int):
        """拖拽结束（鼠标松开）低频收尾：改色/刷新单行表格/状态栏。"""
        if idx < 0 or idx >= len(self.results):
            return
        self.view.update_pick_style(idx, "#ff9500")
        self._update_table_row(idx)
        self.statusBar().showMessage(tr("main.manually_corrected_point_n", n=self._order_display_no(idx)))

    # ---------------- 手动识别 ----------------
    def _grid_ref_angle_deg(self) -> Optional[float]:
        """网格参考方向（纸面X轴在图像中的角度°）：由当前网格 X 方向单位向量计算。
        手动点选与自动建系均以实测方向为基准；默认 (1,0) 对应 0°（图像轴，旧行为）。"""
        if self.grid is None:
            return None
        x = np.asarray(self.grid.x_dir_px, np.float64)
        n = float(np.linalg.norm(x))
        if n < 1e-9:
            return None
        return float(np.degrees(np.arctan2(x[1], x[0])))

    def _current_params(self) -> CrossDetectParams:
        """按界面控件构造检测参数（自动检测 / 手动吸附精化共用）。"""
        return CrossDetectParams(
            threshold_mode=["otsu", "adaptive", "fixed"][self.cb_thresh.currentIndex()],
            polarity=["auto", "dark_on_light", "light_on_dark"][self.cb_polarity.currentIndex()],
            search_window_scale=self.sp_win.value(),
            method="edge_center" if self.cb_method.currentIndex() == 1 else "projection")

    def _toggle_manual(self):
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("main.please_import_a_scan_image_first"))
            return
        if self.grid is None:
            QMessageBox.information(self, tr("common.info"), tr("main.pick_the_array_to_generate_the_grid_first"))
            return
        self._manual_active = not self._manual_active
        if self._manual_active:
            # 首次进入：初始化结果表（若尚未检测则全部为未检出红标，可手动补点）
            if not self.results:
                self.results = [None] * (self.grid.rows * self.grid.cols)
            if self._auto_snapshot is None:
                self._auto_snapshot = list(self.results)
            self.view.set_mode(MODE_MANUAL)
            # 识别区域圆圈半径 = 手动精化窗口半宽（预览坐标），悬停/点击时提示吸附范围
            self.view.set_manual_roi_radius(_refine_win_for(self.grid) / 2.0 / self.src.scale)
            _set_role(self.btn_manual, "btnManualActive")
            self.lbl_hint.setText(tr("main.manual_mode_click_to_snap_refine_arrow_keys_fine_tun"))
            self._ensure_manual_gray()
        else:
            self.view.set_mode(MODE_NONE)
            self.view.hide_manual_roi()
            self.view.clear_manual_selection()
            self._manual_sel_idx = -1
            self._set_manual_btn_active(False)
            self.lbl_hint.setText(tr("main.exited_manual_identification_mode"))

    def _ensure_manual_gray(self):
        """手动精化需要原始分辨率灰度图；未缓存时后台加载，避免大图解码阻塞 UI。"""
        if self._manual_gray is not None:
            return
        g = getattr(self.src, "_orig_gray", None)
        if g is not None:
            self._manual_gray = g
            return
        self.statusBar().showMessage(
            tr("main.loading_original_grayscale_in_background_large_image"))
        self._gray_loader = GrayLoaderThread(self.src, parent=self)
        self._gray_loader.done.connect(self._on_manual_gray_loaded)
        self._gray_loader.start()

    def _on_manual_gray_loaded(self, obj):
        if isinstance(obj, Exception):
            self._manual_gray = None
            self.statusBar().showMessage(
                tr("main.original_grayscale_load_failed_manual_clicks_use_cli"), 8000)
            return
        self._manual_gray = obj
        self.statusBar().showMessage(tr("main.original_grayscale_ready_manual_clicks_auto_snap_and"), 5000)

    def _on_manual_picked(self, px: float, py: float):
        if self.src is None or self.grid is None or not self._manual_active:
            return
        # 预览坐标 → 原始分辨率坐标
        ox, oy = self.view.to_orig(px, py)
        # 最近网格索引（限制在半个间距内，避免误写无关位置）
        pts = self.grid.points_px()
        pitch = self.grid.pitch_px
        best, bd = -1, 0.45 * pitch
        for i, (gx, gy) in enumerate(pts):
            d = ((gx - ox) ** 2 + (gy - oy) ** 2) ** 0.5
            if d < bd:
                bd, best = d, i
        if best < 0:
            self.statusBar().showMessage(tr("main.click_too_far_from_any_grid_point_not_applied_click_"))
            return
        rx, ry = ox, oy
        refined_ok = False
        if self._manual_gray is not None:
            try:
                # 精化窗口自适应：0.25×间距（clamp[81,2400]px）。4800dpi 下 24.5mm 间距
                # 对应约 1158px（≈6.1mm），远大于固定 41px（仅≈0.22mm），可覆盖手动点击容差
                refined = refine_manual_point(self._manual_gray, ox, oy,
                                              self._current_params(),
                                              win=_refine_win_for(self.grid))
                if refined is not None:
                    rx, ry = refined
                    refined_ok = True
            except Exception:
                pass
        # 撤销历史：记录修改前的旧值（None 表示未检出）
        self._manual_history.append((best, self.results[best]))
        self.results[best] = CrossResult(rx, ry, True, "manual", 0.0, refined=refined_ok)
        self._manual_sel_idx = best
        self.view.set_manual_selection(best)
        # 仅就地重建该点标记（橙十字+白圈+序号），不触发全量 _redraw_marks 重建数万 item
        s = self.src.scale
        ox, oy = self._pv(rx / s, ry / s)
        self.view.set_pick_mark(best, ox, oy, kind="cross",
                                color="#ff9500", size=12, label=str(self._order_display_no(best)), selected=True)
        self._update_table_row(best)
        self.statusBar().showMessage(
            tr("main.manually_marked_point_n_x_2f_y_2f_px_ref",
               n=self._order_display_no(best), x=rx, y=ry,
               ref=tr("main.snap_refine") if refined_ok else tr("main.click_to_locate")))

    def _on_manual_nudge(self, idx: int, dx: float, dy: float):
        if idx < 0 or idx >= len(self.results) or self.src is None:
            return
        r = self.results[idx]
        if r is None:
            return
        r.x += dx
        r.y += dy
        r.method = "manual"
        # 方向键微调：仅移动既有标记并着色，不重建全量叠加层
        s = self.src.scale
        npx, npy = self._pv(r.x / s, r.y / s)
        self.view.set_pick_point(idx, npx, npy)
        self.view.update_pick_style(idx, "#ff9500")
        self._update_table_row(idx)
        self.statusBar().showMessage(
            tr("main.fine_tuned_point_n_x_2f_y_2f_px", n=self._order_display_no(idx), x=r.x, y=r.y))

    def _on_undo_manual(self):
        if not self._manual_history:
            self.statusBar().showMessage(tr("main.no_manual_action_to_undo"))
            return
        idx, prev = self._manual_history.pop()
        self.results[idx] = prev
        self._manual_sel_idx = -1
        self.view.clear_manual_selection()
        self._redraw_marks()
        self._fill_table()
        self.statusBar().showMessage(tr("main.undid_manual_point_n", n=self._order_display_no(idx)))

    def _on_clear_manual(self):
        if not self._manual_active or self._auto_snapshot is None:
            return
        ret = QMessageBox.question(
            self, tr("main.clear_manual_points"),
            tr("main.this_clears_all_manual_corrections_and_restores_auto"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self.results = list(self._auto_snapshot)
        self._manual_history = []
        self._manual_sel_idx = -1
        self.view.clear_manual_selection()
        self._redraw_marks()
        self._fill_table()
        self.statusBar().showMessage(tr("main.cleared_all_manual_points_auto_detection_restored"))

    def _redraw_marks(self):
        """重绘结果叠加：自动点绿十字、手动点橙十字、未检出红标、选中点白圈。

        大阵列保护：QGraphicsScene 中数万 item 的创建与重绘会长时间阻塞主线程
        （表现为“能多动两下又卡死”）。点数超过 MAX_OVERLAY_ITEMS 时自动抽样——
        手动点 / 未检出点 / 当前选中点始终绘制（关键信息不丢失），自动成功点按
        步长抽样；完整数据始终在结果表中。
        """
        if self.grid is None or self.src is None:
            return
        s = self.src.scale
        total = len(self.results)
        stride = max(1, math.ceil(total / MAX_OVERLAY_ITEMS)) if total > MAX_OVERLAY_ITEMS else 1
        grid_pts = self.grid.points_px()
        self.view.clear_overlay()
        self.view._pick_points = []
        # 序号显示策略：小阵列（<=500 点，如 3×3/5×5/9×9/11×11/17×17）序号始终
        # 可见；大阵列由 ImageView LOD 控制（缩放不够时隐藏序号文本防卡）。
        self.view._label_force = total <= 500
        for i, r in enumerate(self.results):
            is_manual = r is not None and r.method == "manual"
            is_fail = r is None or (r is not None and not r.ok)
            is_sel = (i == self._manual_sel_idx)
            if not (is_manual or is_fail or is_sel) and total > MAX_OVERLAY_ITEMS and (i % stride != 0):
                continue
            if r is not None and r.ok:
                ox, oy = self._pv(r.x / s, r.y / s)
                color = "#ff9500" if is_manual else "#34c759"
                self.view.add_cross_mark(ox, oy, size=12, color=color, pick_idx=i,
                                         label=str(self._order_display_no(i)))
                if is_sel:
                    self.view.add_marker(ox, oy, "#ffffff", radius=16, pen_w=1.5)
                self.view._pick_points.append((ox, oy))
            else:
                # 未检出：网格理论位置红标；低置信点（ok=False）用检测坐标红标
                if r is not None:
                    gx, gy = r.x, r.y
                else:
                    gx, gy = grid_pts[i]
                px, py = self._pv(gx / s, gy / s)
                self.view.add_marker(px, py, "#c62828", radius=6, label=f"!{self._order_display_no(i)}", pick_idx=i)
                self.view._pick_points.append((px, py))
        # B5：核对锚点（原点 O + 起始点角标）——常数个 item，不参与抽样、不进 _pick_points
        self._draw_anchor_marks(s, grid_pts)

    def _draw_anchor_marks(self, s: float, grid_pts):
        """B5：绘制核对锚点——原点 O（阵列几何中心）与起始点角标（当前模式 1 号点）。

        坐标口径：原图系坐标一律先 / s 转加载系，再经 _pv() 转预览系，与
        _redraw_marks 内 r.x / s 完全一致（preview_scale 分母为加载图，见
        core/image_loader.py:113-135）。锚点恒为 7 个 item，不受 MAX_OVERLAY_ITEMS
        抽样影响；不注册 pick_idx、不进 _pick_points，避免污染拖拽索引。
        """
        g = self.grid
        if g is None or self.src is None or grid_pts is None or len(grid_pts) == 0:
            return
        rows, cols = int(g.rows), int(g.cols)
        if rows <= 0 or cols <= 0:
            return
        try:
            xv = np.asarray(g.x_dir_px, np.float64) * float(g.pitch_px)
            yv = np.asarray(g.y_dir_px, np.float64) * float(g.pitch_px)
            ox0, oy0 = float(g.origin_px[0]), float(g.origin_px[1])
        except Exception:  # noqa: BLE001
            return
        # 阵列几何中心（原图系）：行0列0 + 半幅行列向量
        cx = ox0 + xv[0] * (cols - 1) / 2.0 + yv[0] * (rows - 1) / 2.0
        cy = oy0 + xv[1] * (cols - 1) / 2.0 + yv[1] * (rows - 1) / 2.0
        measured = getattr(self, "origin_xy", None) is not None
        if measured:
            cx, cy = float(self.origin_xy[0]), float(self.origin_xy[1])
        ox, oy = self._pv(cx / s, cy / s)
        # 起始点：取「当前编号模式下显示编号最小」的角点（左下起→左下角）
        cand = [0, cols - 1, (rows - 1) * cols, rows * cols - 1]
        try:
            k = min(cand, key=lambda r_: self._order_display_no(r_))
        except Exception:  # noqa: BLE001
            k = 0
        k = max(0, min(int(k), len(grid_pts) - 1))
        bx, by = self._pv(float(grid_pts[k][0]) / s, float(grid_pts[k][1]) / s)
        inward = (1.0 if ox >= bx else -1.0, 1.0 if oy >= by else -1.0)
        try:
            self.view.add_origin_mark(ox, oy, x_dir=(float(xv[0]), float(xv[1])),
                                     y_dir=(float(yv[0]), float(yv[1])), label="O")
            self.view.add_start_badge(bx, by,
                                      label=tr("main.zh_c168310d", n=self._order_display_no(k)),
                                      inward=inward)
        except Exception:  # noqa: BLE001
            return
        if not getattr(self, "_anchor_logged", False):
            self._anchor_logged = True
            self._log(tr("main.zh_67ff0869",
                         src=tr("main.zh_8e3585d9") if measured else tr("main.zh_1b614c8b"),
                         n=self._order_display_no(k)))

    def _on_export_traces(self):
        """⑥ 导出整图痕迹图：将当前视图（原图+绿十字序号+红/橙标）2 倍渲染导出 PNG。

        针对振镜初期打点不佳、点位难查找的工况：导出图强制显示全部序号并保留
        未检出红标，便于整板抓取情况一眼核对与汇报。"""
        if self.src is None or self.grid is None or not self.results:
            QMessageBox.information(self, tr("main.export_trace_image_full_2"),
                                    tr("main.load_a_scan_and_run_detection_first_export_needs_res"))
            return
        path, _ = qfd_save(
            self, tr("main.export_trace_image_full_2"), "traces_full.png", tr("main.png_image_png"))
        if not path:
            return
        try:
            pm = self.view.grab_scene_image(scale=2.0, force_labels=True)
            pm.save(path)
            self.statusBar().showMessage(tr("main.trace_image_exported_p", p=path), 6000)
        except Exception as e:
            QMessageBox.critical(self, tr("main.export_trace_image_full_2"),
                                 tr("main.export_failed_e", e=str(e)))

    # ---------------- 表格与导出 ----------------
    def _row_values(self, i: int, r, nominal, center) -> list:
        """构造结果表第 i 行的 14 个显示值（全量刷新与单行更新共用）。"""
        ox_, oy_ = r.x, r.y  # 检测结果已是原始分辨率坐标
        mm_x, mm_y = self._to_mm(ox_, oy_)
        # 实测值中心归零后投影到点选方向轴，与固定名义值同坐标系
        # （名义值固定为行列设计坐标，反向点选时仅实测按方向投影，见 grid.to_phys）
        if center:
            mm_xc, mm_yc = mm_x - center[0], mm_y - center[1]
        else:
            mm_xc, mm_yc = mm_x, mm_y
        if self.grid is not None:
            mm_xc, mm_yc = self.grid.to_phys(mm_xc, mm_yc)
        if nominal and i < len(nominal):
            tx, ty = nominal[i]
            dx, dy = mm_xc - tx, mm_yc - ty
            dev = math.hypot(dx, dy)
            verdict = "Pass" if (self._component_ok(mm_xc, tx) and self._component_ok(mm_yc, ty)) else "NG"
        else:
            tx = ty = dx = dy = dev = float("nan")
            verdict = ""
        return [self._order_display_no(i),
                i // self.grid.cols + 1 if self.grid else "",
                i % self.grid.cols + 1 if self.grid else "",
                f"{ox_:.3f}", f"{oy_:.3f}", f"{mm_xc:.4f}", f"{mm_yc:.4f}",
                "" if math.isnan(tx) else f"{tx:.4f}",
                "" if math.isnan(ty) else f"{ty:.4f}",
                "" if math.isnan(dx) else f"{dx:.4f}",
                "" if math.isnan(dy) else f"{dy:.4f}",
                "" if math.isnan(dev) else f"{dev:.4f}",
                verdict,
                f"{r.conf:.3f}" if getattr(r, "conf", None) is not None else "",
                {"projection": "OK", "projection+subpix": "OK",
                 "projection+sym": "OK", "projection+sym+subpix": "OK",
                 "projection+gauss": "OK", "projection+gauss+subpix": "OK",
                 "edge_center": "OK", "edge_center+subpix": "OK",
                 "manual": tr("main.manual")}.get(r.method, r.method)]

    def _fill_table(self):
        self.table.setRowCount(len(self.results))
        # 固定名义值（中心归零坐标系）：模板固定值优先，否则设计网格中心归零
        nominal = self._nominal_mm_list()
        # 中心归零基准优先用实测拟合阵列中心（消除点选偏移导致的整体平移偏差），
        # 不足 3 个成功点或拟合异常时 _center_measured_mm 内部回退 _center_theo_mm()
        center = self._center_measured_mm(self.results)
        # 缓存名义值/中心，供高频单行更新复用（避免拖拽/微调时反复重建全量名义列表）
        self._nominal_cache = nominal
        self._center_cache = center
        for i, r in enumerate(self.results):
            if r is None or not r.ok:
                # F 模块：失败点保留行列排入表格（未检出实测列 "--"；
                # 低置信点 DETECT_FAIL 保留检测坐标与置信度），状态 DETECT_FAIL
                self._fill_fail_row(i, nominal, r)
                continue
            vals = self._row_values(i, r, nominal, center)
            for j, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if j == 12 and v in ("Pass", "NG"):
                    self._apply_verdict_style(item, v)
                self.table.setItem(i, j, item)
        _has = any(r is not None for r in self.results)
        self.act_export.setEnabled(_has)
        try:
            self.btn_export_excel.setEnabled(_has)
        except Exception:
            pass
        self._update_summary()

    def _fill_fail_row(self, i: int, nominal=None, r=None):
        """F 模块：失败行——保留行列号与名义坐标，状态列 DETECT_FAIL（中英并列），
        浅灰斜体与成功点区分。未检出（r=None）实测列 "--"；低置信点（r 非 None，
        fail_reason=LOW_CONF）保留检测坐标与置信度，便于判断后手动修正。"""
        tx, ty = (nominal[i] if nominal and i < len(nominal) else (None, None))
        r_ = i // self.grid.cols + 1 if self.grid else ""
        c_ = i % self.grid.cols + 1 if self.grid else ""
        if r is not None and not r.ok:
            # 低置信：有检测坐标（原始分辨率），实测列给实际值，状态 DETECT_FAIL(低置信)
            try:
                mm_x, mm_y = self._to_mm(r.x, r.y)
                if self._center_cache:
                    mm_x, mm_y = mm_x - self._center_cache[0], mm_y - self._center_cache[1]
                if self.grid is not None:
                    mm_x, mm_y = self.grid.to_phys(mm_x, mm_y)
                msx, msy = f"{mm_x:.4f}", f"{mm_y:.4f}"
            except Exception:
                msx = msy = "--"
            status = tr("main.detection_failed_detect_fail_low_confidence")
            vals = [self._order_display_no(i), r_, c_, f"{r.x:.3f}", f"{r.y:.3f}", msx, msy,
                    "" if tx is None else f"{tx:.4f}",
                    "" if ty is None else f"{ty:.4f}",
                    "", "", "",
                    "DETECT_FAIL",
                    f"{r.conf:.3f}" if getattr(r, "conf", None) is not None else "--",
                    status]
        else:
            vals = [self._order_display_no(i), r_, c_, "--", "--", "--", "--",
                    "" if tx is None else f"{tx:.4f}",
                    "" if ty is None else f"{ty:.4f}",
                    "", "", "",
                    "DETECT_FAIL", "--", tr("main.detection_failed_detect_fail")]
        for j, v in enumerate(vals):
            item = QTableWidgetItem(str(v))
            f = item.font()
            f.setItalic(True)
            item.setFont(f)
            item.setForeground(QBrush(QColor(C("dim"))))
            self.table.setItem(i, j, item)

    def _update_table_row(self, i: int):
        """仅刷新结果表第 i 行。拖拽/方向键微调属高频操作（鼠标移动逐帧触发），
        全表重建数万 item 会阻塞主线程，此处只更新受影响的一行。"""
        if i < 0 or i >= len(self.results):
            return
        r = self.results[i]
        if r is None or not r.ok:
            nominal = getattr(self, "_nominal_cache", None)
            self._fill_fail_row(i, nominal, r)
            return
        nominal = getattr(self, "_nominal_cache", None)
        center = getattr(self, "_center_cache", None)
        if nominal is None:
            nominal = self._nominal_mm_list()
            center = self._center_measured_mm(self.results)
        vals = self._row_values(i, r, nominal, center)
        for j, v in enumerate(vals):
            item = QTableWidgetItem(str(v))
            if j == 12 and v in ("Pass", "NG"):
                self._apply_verdict_style(item, v)
            self.table.setItem(i, j, item)
        self._update_summary()

    def _theory_mm_list(self) -> List[Tuple[float, float]]:
        """网格理论坐标（mm）：由点选+间距+DPI 生成的设计位置（绝对 mm）。"""
        out = []
        if self.grid is None:
            return out
        for gx, gy in self.grid.points_px():
            out.append(self._to_mm(gx, gy))
        return out

    def _center_theo_mm(self) -> Optional[Tuple[float, float]]:
        """网格几何中心的理论坐标（绝对 mm），用作中心归零基准。

        优先行列几何中心（与模板 P1 基准语义一致）；grid 缺失时返回 None。
        """
        if self.grid is None:
            return None
        theo = self._theory_mm_list()
        if not theo:
            return None
        r, c = self.grid.rows, self.grid.cols
        cidx = (r // 2) * c + (c // 2)
        if cidx < len(theo):
            return theo[cidx]
        return theo[len(theo) // 2] if theo else None

    def _center_measured_mm(self, results: List[Optional[CrossResult]]) -> Optional[Tuple[float, float]]:
        """实测拟合阵列中心（mm），用作中心归零基准。

        用成功检测点（r.x, r.y，已是原始分辨率坐标）做最小二乘仿射拟合
        （与 _maybe_correct_grid 同一模型：A=[[1,cc,rc,0,0,0],[0,0,0,1,cc,rc]]
        解 [ox,vx_x,vy_x,oy,vx_y,vy_y]），取拟合网格几何中心像素
        (ox + vx_x*(cols//2) + vy_x*(rows//2),
         oy + vx_y*(cols//2) + vy_y*(rows//2))，经 _to_mm 转物理坐标返回。

        修复背景：用户点选阵列角点存在像素偏差时，设计网格中心（_center_theo_mm）
        会整体偏移，导致全部实测点 ΔX 均匀显示约 -0.68mm（0.82mm 级平移偏差）。
        改以实测拟合阵列中心归零后，基准与实际阵列对齐，平移偏差消除。

        成功点不足 3 个或拟合异常时回退 _center_theo_mm()。
        不动名义值模板（固定 D 列/设计坐标）与 res_fb、_to_mm_base、多摆放拟合链路。
        """
        if self.grid is None or self.src is None:
            return self._center_theo_mm()
        ok_pts = [(i, r) for i, r in enumerate(results) if r is not None and r.ok]
        if len(ok_pts) < 3:
            return self._center_theo_mm()
        cols = self.grid.cols
        rows = self.grid.rows
        data = [(r.x, r.y, i // cols, i % cols) for i, r in ok_pts]
        A, b = [], []
        for x, y, rc, cc in data:
            A.append([1, cc, rc, 0, 0, 0]); b.append(x)
            A.append([0, 0, 0, 1, cc, rc]); b.append(y)
        A = np.array(A, np.float64); b = np.array(b, np.float64)
        try:
            sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        except Exception:
            return self._center_theo_mm()
        ox, vx_x, vy_x, oy, vx_y, vy_y = sol
        # 拟合网格几何中心像素：中心行列 (rows//2, cols//2) 对应的仿射位置
        # P1-1 修复：几何中心取 (n-1)/2，与 nominal_points_mm 的 c0/r0 及精化 cx/cy 同口径
        # （原 cols//2 对偶数行列偏移半格；奇数阵列数值相同，不影响 5×5 现状）。
        cc_c = (cols - 1) / 2.0
        rc_c = (rows - 1) / 2.0
        cx = float(ox + vx_x * cc_c + vy_x * rc_c)
        cy = float(oy + vx_y * cc_c + vy_y * rc_c)
        try:
            return self._to_mm(cx, cy)
        except Exception:
            return self._center_theo_mm()

    def _nominal_mm_list(self) -> List[Tuple[float, float]]:
        """与 self.results 逐点对应的固定名义值（以阵列几何中心为原点，mm）。

        加载模板 → 直接取模板 D 列固定值（跳过 P1 基准点，即 P3..P27）；
        未加载模板 → 按"阵列几何中心为原点 + 用户配置间隔 pitch_mm"生成固定
        设计坐标（行主序，与模板 P3..P27 语义一致：中心点名义=(0,0)）。
        名义值一旦生成即为固定值，与点选起点/检测结果完全无关，
        任何情况下都禁止用检测拟合坐标反推名义值。
        """
        n = len(self.results)
        if n == 0:
            return []
        if self.template_nominal_points and len(self.template_nominal_points) == n + 1:
            # P1 + P3..P27 共 n+1 项，取 P3..P27 与检测点一一对应
            return list(self.template_nominal_points[1:])
        if self.grid is None:
            return []
        nom = self.grid.nominal_points_mm(include_p1=self.chk_p1.isChecked())
        if len(nom) == n + 1:
            # 含 P1 时 nom=[P1, P3..]，检测点对应 P3..，去掉 P1
            return list(nom[1:])
        return list(nom)

    def _ng_tolerance_mm(self) -> float:
        """NG 判定阈值（mm）：与棋盘格校准残差门槛一致，可在此调整。"""
        return getattr(self, "ng_tol_mm", 0.15)

    def _to_mm(self, px_x: float, px_y: float) -> Tuple[float, float]:
        """像素→物理坐标：分区校正场优先（阶段三，精度更高），否则全局多项式/像素比例；
        最后叠加外部基准残差校正层（res_fb，二次元实测拟合），修正后坐标 = 基础换算 + 残差增量。"""
        x, y = self._to_mm_base(px_x, px_y)
        if self.res_fb is not None:
            dx, dy = self.res_fb.apply(x, y)
            x, y = x + dx, y + dy
        return x, y

    def _to_mm_base(self, px_x: float, px_y: float) -> Tuple[float, float]:
        """像素→物理坐标（仅基础校准换算，不含 res_fb 残差层）。
        供多摆放系统级残差拟合使用：拟合坐标必须基于基础校准输出，避免与待拟合层叠加。"""
        if self._deskew_ang is not None and self._deskew_shape is not None:
            # B1 摆正：入口先绕图中心旋转 -ang（原始灰度系 → 回正系），再走基础换算
            px_x, px_y = deskew_point(px_x, px_y, self._deskew_ang, self._deskew_shape)
        if self.piecewise is not None:
            x, y = apply_piecewise(px_x, px_y, self.piecewise)
        elif self.coeff_x is not None:
            x, y = apply_correction(px_x, px_y, self.coeff_x, self.coeff_y, self.order)
        else:
            dpi = self.grid.dpi if self.grid else self.sp_dpi.value()
            mm = 25.4 / dpi
            x, y = px_x * mm, px_y * mm
        if self.reg_fit is not None:
            # B9 配准模型变换（红线 3：叠加在基础换算末尾，res_fb 残差层仍独立在 _to_mm）
            x, y = self.reg_fit.predict(np.array([[x, y]], np.float64))[0]
            x, y = float(x), float(y)
        return x, y

    def _component_ok(self, meas: float, nominal: float) -> bool:
        """分量公差判定：测量值落在 [名义+下公差, 名义+上公差] 内。"""
        return (nominal + self.tol_lower_mm) <= meas <= (nominal + self.tol_upper_mm)

    def _on_load_template(self):
        """加载质检模板：读取判定公差与表头元数据作为导出/判定口径来源。"""
        path, _ = qfd_open(self, tr("main.load_qc_template"), "",
                                              tr("main.excel_files_xlsx"))
        if not path:
            return
        try:
            tpl = parse_template(path)
        except Exception as e:
            QMessageBox.warning(self, tr("main.template_parse_failed"), str(e))
            return
        self.template_path = path
        self.template_meta = tpl["meta"]
        self.tol_upper_mm = tpl["tol_upper"]
        self.tol_lower_mm = tpl["tol_lower"]
        # 名义值固定采用模板 D 列（用户二次元测量结果），严禁检测反推
        self.template_nominal_points = tpl.get("nominal_points")
        self._fill_table()
        n_nom = len(self.template_nominal_points) if self.template_nominal_points else 0
        self.statusBar().showMessage(
            tr("main.template_loaded_path_tolerance_upper_3f_lower_3f_mm_",
               path=path, upper=self.tol_upper_mm, lower=self.tol_lower_mm, n=n_nom))

    def _on_load_residual_correction(self):
        """加载外部基准残差校正文件（res_fb_*.npz，由二次元实测拟合生成）。
        叠加到 _to_mm 基础换算输出上：最终坐标 = 基础换算 + 残差增量。"""
        path, _ = qfd_open(self, tr("main.load_residual_correction_file"), "res_fb.npz",
                                              tr("common.residual_correction_files_npz"))
        if not path:
            return
        try:
            rc = load_residual_correction(path)
        except Exception as e:
            QMessageBox.warning(self, tr("main.load_failed"), str(e))
            return
        self.res_fb = rc
        stats = rc.stats or {}
        rms_a = stats.get("rms_after_mm")
        rms_b = stats.get("rms_before_mm")
        extra = ""
        if rms_a is not None and rms_b is not None:
            extra = tr("main.compensated_rms_a_4f_mm_before_b_4f_mm", a=rms_a, b=rms_b)
        self.statusBar().showMessage(
            tr("main.residual_correction_loaded_order_o_extra", o=rc.order, extra=extra))
        self.lbl_hint.setText(tr("main.external_reference_residual_correction_enabled_order", o=rc.order))

    def _on_multi_pose_correction(self):
        """打开多摆放系统级残差校正对话框：导入 1~5 张摆放图，SVD 自动配准拟合，无需输入角度。"""
        if not self.calibrated:
            QMessageBox.information(
                self, tr("common.info"),
                tr("main.please_complete_chessboard_calibration_first_import_"))
            return
        dlg = MultiPoseDialog(self, self)
        dlg.exec_()

    # ---------------- B9 配准模型层（实测点→名义网格） ----------------
    def _frame_id(self) -> str:
        """生成幅面指纹（图像路径 + 文件大小/mtime + DPI 的哈希），红线 9 禁止跨幅复用。"""
        import hashlib
        try:
            if self.src is None or not getattr(self.src, "path", None):
                return "frame_unknown"
            st = os.stat(self.src.path)
            dpi = self.grid.dpi if self.grid else self.sp_dpi.value()
            raw = f"{self.src.path}|{st.st_size}|{int(st.st_mtime)}|{dpi:.4f}"
            return "frame_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        except Exception:
            return "frame_unknown"

    def _center_measured_mm_base(self, results: List[Optional[CrossResult]]) -> Optional[Tuple[float, float]]:
        """同 _center_measured_mm，但中心像素用 _to_mm_base（不含 res_fb/B9）转 mm，
        保证 B9 拟合的实测点与 res_fb 解耦（红线 3：残差吸收解耦）。"""
        if self.grid is None or self.src is None:
            return self._center_theo_mm()
        ok_pts = [(i, r) for i, r in enumerate(results) if r is not None and r.ok]
        if len(ok_pts) < 3:
            return self._center_theo_mm()
        cols = self.grid.cols
        rows = self.grid.rows
        data = [(r.x, r.y, i // cols, i % cols) for i, r in ok_pts]
        A, b = [], []
        for x, y, rc, cc in data:
            A.append([1, cc, rc, 0, 0, 0]); b.append(x)
            A.append([0, 0, 0, 1, cc, rc]); b.append(y)
        A = np.array(A, np.float64); b = np.array(b, np.float64)
        try:
            sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        except Exception:
            return self._center_theo_mm()
        ox, vx_x, vy_x, oy, vx_y, vy_y = sol
        # P1-1 修复：几何中心取 (n-1)/2，与 nominal_points_mm 的 c0/r0 及精化 cx/cy 同口径
        # （原 cols//2 对偶数行列偏移半格；奇数阵列数值相同，不影响 5×5 现状）。
        cc_c = (cols - 1) / 2.0
        rc_c = (rows - 1) / 2.0
        cx = float(ox + vx_x * cc_c + vy_x * rc_c)
        cy = float(oy + vx_y * cc_c + vy_y * rc_c)
        try:
            return self._to_mm_base(cx, cy)
        except Exception:
            return self._center_theo_mm()

    def _collect_registration_pairs(self):
        """采集同幅标定数据对：实测点（_to_mm_base 口径，中心归零+方向投影）→ 名义网格。

        返回 (meas_mm Nx2, nom_mm Nx2, note)；失败返回 (None, None, reason)。
        实测点口径与 _row_values 一致：基础换算 → 减中心 → 按点选方向投影，
        名义值固定取 _nominal_mm_list（设计值，红线 1 禁反推）。
        """
        if self.grid is None:
            return None, None, tr("main.zh_aef6a850")
        if not self.results:
            return None, None, tr("main.zh_1f4341fd")
        ok_idx = [i for i, r in enumerate(self.results) if r is not None and r.ok]
        if len(ok_idx) < 4:
            return None, None, tr("main.zh_e04e76a6", n=len(ok_idx))
        # 采集时临时禁用旧配准模型：重新标定必须取纯基础换算口径，不被上一轮模型污染
        saved_reg = self.reg_fit
        self.reg_fit = None
        try:
            center = self._center_measured_mm_base(self.results)
            if center is None:
                return None, None, tr("main.zh_f25ec42a")
            nominal = self._nominal_mm_list()
            meas, nom = [], []
            for i in ok_idx:
                r = self.results[i]
                x, y = self._to_mm_base(r.x, r.y)
                x, y = x - center[0], y - center[1]
                x, y = self.grid.to_phys(x, y)
                meas.append((x, y))
                if nominal and i < len(nominal):
                    nom.append(nominal[i])
        finally:
            self.reg_fit = saved_reg
        if len(nom) != len(meas):
            return None, None, tr("main.zh_1c8f21aa")
        return np.array(meas, np.float64), np.array(nom, np.float64), ""

    def _on_fit_registration(self):
        """B9 同幅配准标定：实测点→名义网格拟合高阶变换并叠加到换算层。

        对照实验（验收 1）：跑全部模型出 LOO 对照表，选首个 LOO-RMS ≤ 0.12mm 者。
        红线 9：保存时绑定幅面指纹，跨幅加载默认拒绝。
        """
        if not self.calibrated:
            QMessageBox.information(self, tr("common.info"), tr("main.zh_eabb9c84"))
            return
        meas, nom, err = self._collect_registration_pairs()
        if meas is None:
            QMessageBox.warning(self, tr("main.zh_7960ac09"), err)
            return
        n = len(meas)
        if n < 10:
            QMessageBox.warning(
                self, tr("main.zh_b54e8c7d"),
                tr("main.zh_b8a6ed42", n=n))
        fits = compare_models(meas, nom)
        self._log(tr("main.zh_1fddf1e1", n=n))
        for line in compare_table(fits, target_mm=0.12).splitlines():
            self._log(line)
        selected, _tried = select_model(meas, nom, target_loo_rms_mm=0.12)
        if selected is None or not selected.ok:
            QMessageBox.warning(self, tr("main.zh_5c88558f"), tr("main.zh_bc4002ec"))
            return
        frame_id = self._frame_id()
        default_name = f"reg_{selected.kind}_{n}pt.npz"
        path, _ = qfd_save(
            self, tr("main.zh_b0fe4739"), default_name, tr("main.zh_a8699934"))
        if not path:
            return
        try:
            save_fit(path, selected, frame_id)
        except Exception as e:
            QMessageBox.warning(self, tr("common.save_failed"), str(e))
            return
        self.reg_fit = selected
        self.reg_kind = selected.kind
        self.reg_frame_id = frame_id
        self._log(tr("main.zh_9e018400",
                     kind=selected.kind, rms=selected.loo_rms_mm, fid=frame_id))
        self._fill_table()
        self.statusBar().showMessage(
            tr("main.zh_911f833c",
               kind=selected.kind, rms=selected.loo_rms_mm))

    def _on_load_registration(self):
        """加载已保存的配准模型；幅面指纹不匹配默认拒绝（红线 9）。"""
        path, _ = qfd_open(self, tr("main.load_registration_model"), "",
                                              tr("main.zh_a8699934"))
        if not path:
            return
        frame_id = self._frame_id()
        try:
            fit = load_fit(path, frame_id)
        except ValueError as e:
            QMessageBox.warning(self, tr("main.load_failed"),
                                str(e) + tr("main.zh_9bb48e96"))
            return
        except Exception as e:
            QMessageBox.warning(self, tr("main.load_failed"), str(e))
            return
        self.reg_fit = fit
        self.reg_kind = fit.kind
        self.reg_frame_id = frame_id
        self._log(tr("main.zh_4f573763", kind=fit.kind, rms=fit.loo_rms_mm))
        self._fill_table()
        self.statusBar().showMessage(
            tr("main.zh_4f573763", kind=fit.kind, rms=fit.loo_rms_mm))

    def _on_clear_registration(self):
        """清除当前配准模型，换算恢复为基础校准。"""
        self.reg_fit = None
        self.reg_kind = None
        self.reg_frame_id = None
        self._log(tr("main.zh_0452df8e"))
        self._fill_table()

    def _on_save_measure_images(self):
        """⑤ 保存测量图片比对结果：后台线程逐点裁剪原图+叠加标注，避免大图卡死。"""
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("main.please_import_a_scan_image_first"))
            return
        if not self.results or not any(r is not None for r in self.results):
            QMessageBox.information(self, tr("common.info"), tr("main.run_detection_or_mark_points_manually_first"))
            return
        if self.grid is None:
            QMessageBox.information(self, tr("common.info"), tr("main.set_array_spec_on_the_array_params_page_first"))
            return
        out_dir = qfd_dir(
            self, tr("main.select_save_folder_generates_p1_png_per_point"))
        if not out_dir:
            return
        nominal = self._nominal_mm_list()
        center = self._center_measured_mm(self.results)
        self.btn_save_measure.setEnabled(False)
        self.progress.setRange(0, len(self.results))
        self.progress.setValue(0)
        self._measure_saver = DragSaveThread(
            out_dir, self.src, list(self.results), self.grid, self.sp_win.value(),
            nominal, center, self.coeff_x, self.coeff_y, self.order,
            self.sp_dpi.value(), self.tol_upper_mm, self.tol_lower_mm,
            "edge_center" if self.cb_method.currentIndex() == 1 else "projection",
            piecewise=getattr(self, "piecewise", None),
            res_fb=getattr(self, "res_fb", None),
            order_mode=self._order_mode(),
            lang=get_lang(),
            parent=self)
        self._measure_saver.progress.connect(self._on_save_measure_progress)
        self._measure_saver.done.connect(self._on_save_measure_done)
        self._measure_saver.start()

    def _on_save_measure_progress(self, n: int, text: str):
        self.progress.setValue(n)
        self.statusBar().showMessage(tr("main.saving_measurement_image_t", t=text))

    def _on_save_measure_done(self, res):
        self._measure_saver = None
        self.btn_save_measure.setEnabled(True)
        self.progress.setValue(0)
        if isinstance(res, Exception):
            QMessageBox.warning(self, tr("common.save_failed"), str(res))
            return
        saved, missing, csv_path = res
        QMessageBox.information(
            self, tr("main.saved"),
            tr("main.saved_s_comparison_images_m_points_not_detected_logg",
               s=saved, m=missing, p=csv_path))

    def _on_export(self):
        if not self.results:
            return
        if self.grid is None:
            # G02: reset_pick clears grid; bare grid.cols below would AttributeError
            QMessageBox.warning(self, tr("main.export_excel"),
                                tr("main.array_not_defined_export_aborted"))
            return
        path, _ = qfd_save(self, tr("main.export_excel"), "galvo_calib_result.xlsx",
                                              tr("main.excel_files_xlsx"))
        if not path:
            return
        rows = []
        # 固定名义值（以阵列几何中心为原点）：模板固定值优先，否则设计网格固定值
        nominal = self._nominal_mm_list()
        # 导出与界面表格同基准：实测拟合阵列中心（消除点选偏移导致的整体平移假象）
        center = self._center_measured_mm(self.results)
        for i, r in enumerate(self.results):
            if r is None:
                # F 模块：失败点保留行列位置——名义值列仍写固定名义值，
                # 实测坐标列为空（不写伪造值），状态列显式标 DETECT_FAIL
                tx, ty = (nominal[i] if nominal and i < len(nominal) else (None, None))
                rows.append({"row": i // self.grid.cols + 1, "col": i % self.grid.cols + 1,
                             "px_x": None, "px_y": None,
                             "mm_x": None, "mm_y": None,
                             "theo_x": tx, "theo_y": ty,
                             "dx": None, "dy": None, "dev": None, "verdict": "DETECT_FAIL",
                             "conf": None, "status": "DETECT_FAIL"})
                continue
            ox_, oy_ = r.x, r.y  # 原始分辨率坐标
            mm_x, mm_y = self._to_mm(ox_, oy_)
            # 实测值中心归零后投影到点选方向轴，与固定名义值同坐标系
            if center:
                mm_xc, mm_yc = mm_x - center[0], mm_y - center[1]
            else:
                mm_xc, mm_yc = mm_x, mm_y
            if self.grid is not None:
                mm_xc, mm_yc = self.grid.to_phys(mm_xc, mm_yc)
            if nominal and i < len(nominal):
                tx, ty = nominal[i]
                dx, dy = mm_xc - tx, mm_yc - ty
                dev = math.hypot(dx, dy)
                verdict = "Pass" if (self._component_ok(mm_xc, tx) and self._component_ok(mm_yc, ty)) else "NG"
            else:
                tx = ty = dx = dy = dev = None
                verdict = ""
            rows.append({"row": i // self.grid.cols + 1, "col": i % self.grid.cols + 1,
                         "px_x": ox_, "px_y": oy_, "mm_x": mm_xc, "mm_y": mm_yc,
                         "theo_x": tx, "theo_y": ty, "dx": dx, "dy": dy, "dev": dev,
                         "verdict": verdict, "conf": r.conf,
                         "status": tr("main.manual") if r.method == "manual" else "OK"})
        method_name = tr("main.edge_line_midpoint") if self.cb_method.currentIndex() == 1 else tr("main.projection_centroid")
        params = {"DPI": self.sp_dpi.value(), tr("main.array_spec"): f"{self.grid.rows}×{self.grid.cols}",
                  tr("main.pitch_mm"): self.grid.pitch_mm, tr("main.method"): method_name,
                  tr("main.calib_rms_mm"): getattr(self, "calib_rms", ""),
                  tr("main.numbering_mode"): self._order_mode()}
        # 中心原点坐标（mm，相对图像原点，实测拟合阵列中心）——供振镜校正直接取用
        if center:
            params[tr("main.center_origin_mm")] = f"{center[0]:.4f}, {center[1]:.4f}"
        else:
            params[tr("main.center_origin_mm")] = "N/A"
        # P0-3：并入本次检测的诊断记录（网格校正次数/预计边缘偏差/网格重建/
        # 精化拟合 θ 与 rms、精化前后 RMS、精化是否应用），让报表自身可复盘
        # 「精化到底有没有生效」，不再依赖界面日志（日志已同时落盘 logs/app_*.log）。
        for _k, _v in (getattr(self, "_diag", {}) or {}).items():
            if _v is None:
                params[_k] = "N/A"
            elif isinstance(_v, float):
                params[_k] = f"{_v:.4f}"
            else:
                params[_k] = _v
        # 方向轴（度，相对图像 X 轴），用于判断建系是否真的被旋转过
        try:
            _vx, _vy = self.grid.x_dir_px, self.grid.y_dir_px
            params[tr("main.zh_86b1810b")] = f"{math.degrees(math.atan2(_vx[1], _vx[0])):.4f}"
            params[tr("main.zh_dac88155")] = f"{math.degrees(math.atan2(_vy[1], _vy[0])):.4f}"
        except Exception:
            pass
        # B1：建系正交化留痕（点选夹角 / 修正量 / 是否启用）
        self._record_ortho_into(params)
        # 名义值来源：模板固定值（含 P1 基准行）优先；否则按界面配置
        # "含中心基准点 P1" 生成固定设计网格名义值（含 P1=26 项 / 不含=25 项）
        if self.template_nominal_points:
            export_nominal = self.template_nominal_points
            export_include_p1 = True
        else:
            export_nominal = self.grid.nominal_points_mm(
                include_p1=self.chk_p1.isChecked()) if self.grid else None
            export_include_p1 = self.chk_p1.isChecked()
        try:
            export_excel(path, rows, params,
                         meta=self.template_meta,
                         tol_upper=self.tol_upper_mm,
                         tol_lower=self.tol_lower_mm,
                         rows=self.grid.rows, cols=self.grid.cols,
                         nominal_points=export_nominal,
                         include_p1=export_include_p1,
                         order=self._order_mode(),
                         lang=get_lang())
        except Exception as e:
            self._log(tr("main.zh_a969f37d", err=str(e)))
            QMessageBox.warning(self, tr("main.export_failed"), str(e))
            return
        self._refresh_flow()
        self._log(tr("main.exported_path", path=path))
        self.statusBar().showMessage(tr("main.exported_path", path=path))

    # ---------------- 工程保存/加载 ----------------
    def _on_save_project(self):
        if self.src is None:
            QMessageBox.information(self, tr("common.info"), tr("main.nothing_to_save"))
            return
        path, _ = qfd_save(self, tr("main.save_project"), "project.json", tr("main.project_files_json"))
        if not path:
            return
        data = {"image": self.src.path,
                "grid": self.grid.to_dict() if self.grid else None,
                "results": [None if r is None else [r.x, r.y, r.method, r.ok, r.conf, r.fail_reason]
                            for r in self.results],
                "calibrated": self.calibrated,
                "params": {"thresh": self.cb_thresh.currentIndex(),
                           "win": self.sp_win.value(),
                           "method": self.cb_method.currentIndex(),
                           "use_full": self.chk_full.isChecked(),
                           "include_p1": self.chk_p1.isChecked(),
                           "order": self._order_mode()}}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            # G14: 只读目录/磁盘满时给出明确提示，不再无提示中断
            self._log(tr("main.project_save_failed_err", err=str(exc)))
            QMessageBox.warning(self, tr("common.save_failed"), str(exc))
            return
        self.statusBar().showMessage(tr("main.project_saved_path", path=path))

    def _apply_project_state(self, data: dict, path: str = ""):
        self._suppress_invalidate = True
        try:
            """应用工程文件状态（阵列定义/检测结果/参数）。

            供 _on_load_project 两条路径共用：图片文件存在时异步加载、本方法在
            _on_image_loaded 之后执行（保证图像已就绪、_pv 换算正确）；图片缺失时
            同步直接执行。阵列标记按当前加载分辨率换算，结果与参数原样恢复。
            """
            if data.get("grid"):
                self.grid = GridDefinition.from_dict(data["grid"])
                pts = self.grid.points_px()
                s = self.src.scale if self.src else 1.0
                for gx, gy in pts:
                    px, py = self._pv(gx / s, gy / s)
                    self.view.add_marker(px, py, "#8e8e93", radius=3.0, pen_w=1.0)
            if data.get("results"):
                self.results = []
                for item in data["results"]:
                    if item is None:
                        self.results.append(None)
                    else:
                        # 兼容旧工程（[x,y,method]）：缺省 ok=True；新工程含 [x,y,method,ok,conf,fail_reason]
                        ok = bool(item[3]) if len(item) > 3 else True
                        conf = float(item[4]) if len(item) > 4 else 1.0
                        fail_reason = str(item[5]) if len(item) > 5 else ""
                        self.results.append(CrossResult(item[0], item[1], ok, item[2], 0.0,
                                                        conf=conf, fail_reason=fail_reason))
            self.cb_thresh.setCurrentIndex(data.get("params", {}).get("thresh", 0))
            self.sp_win.setValue(data.get("params", {}).get("win", 1.2))
            self.cb_method.setCurrentIndex(data.get("params", {}).get("method", 0))
            if "use_full" in data.get("params", {}):
                self.chk_full.setChecked(bool(data["params"]["use_full"]))
            if "include_p1" in data.get("params", {}):
                self.chk_p1.setChecked(bool(data["params"]["include_p1"]))
            # 编号模式（旧工程无此字段时保持默认，不中断加载）
            if "order" in data.get("params", {}):
                mode = str(data["params"]["order"])
                # 下拉项由 addItems(mode_label) 填充，未 setItemData，findData 恒为 -1；
                # 需按显示名 findText 匹配（模式名→显示名双向唯一）。
                lbl = mode_label(mode, get_lang() == "zh")
                idx = self.cb_order.findText(lbl)
                if idx < 0:
                    # 兜底：显示名可能因语言/文案差异变化，改按模式值精确定位
                    for i in range(self.cb_order.count()):
                        item_mode = POINT_ORDER_MODES[i][0] if i < len(POINT_ORDER_MODES) else ""
                        if item_mode == mode:
                            idx = i
                            break
                if idx >= 0:
                    self.cb_order.setCurrentIndex(idx)
            self._fill_table()
            self.statusBar().showMessage(tr("main.project_loaded_path", path=path or data.get("image", "")))
        finally:
            self._suppress_invalidate = False

    def _on_load_project(self):
        path, _ = qfd_open(self, tr("main.load_project"), "", tr("main.project_files_json"))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            QMessageBox.warning(self, tr("main.load_failed"), str(e))
            return
        if data.get("image") and os.path.isfile(data["image"]):
            # 图片异步加载：先暂存工程状态，_on_image_loaded 完成后统一应用，
            # 保证阵列标记按最终加载分辨率换算且不被回调内的 _reset_pick() 清空
            data["_proj_path"] = path
            self._pending_project = data
            self._load_image(data["image"])
        else:
            # 图片缺失：仅恢复阵列/结果/参数，不弹加载图片
            self._apply_project_state(data, path)


class DragSaveThread(QThread):
    """后台保存测量图片比对结果：在子线程逐点裁剪原图+叠加标注，
    主线程仅接收进度/完成信号，避免大阵列、超大图同步保存卡死界面。"""

    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(object)

    def __init__(self, out_dir, src, results, grid, sp_win, nominal, center,
                 coeff_x, coeff_y, order, dpi_value, tol_upper, tol_lower,
                 method_name, piecewise=None, res_fb=None, order_mode="top_left_row_major",
                 lang: str = "zh", parent=None):
        super().__init__(parent)
        self._args = (out_dir, src, results, grid, sp_win, nominal, center,
                      coeff_x, coeff_y, order, dpi_value, tol_upper, tol_lower,
                      method_name, piecewise, res_fb, order_mode, lang)

    def run(self):
        try:
            from core.measurement_saver import save_all
            (out_dir, src, results, grid, sp_win, nominal, center,
             coeff_x, coeff_y, order, dpi_value, tol_upper, tol_lower,
             method_name, piecewise, res_fb, order_mode, lang) = self._args
            res = save_all(out_dir, src, results, grid, sp_win, nominal, center,
                           coeff_x, coeff_y, order, dpi_value, tol_upper, tol_lower,
                           method_name=method_name, piecewise=piecewise, res_fb=res_fb,
                           order_mode=order_mode, lang=lang,
                           callback=lambda n, t, s: self.progress.emit(n, t, s))
            self.done.emit(res)
        except Exception as e:
            self.done.emit(e)
