# -*- coding: utf-8 -*-
"""全局主题样式（现代浅色科技风 · 步骤化卡片布局）。
V2 升级点：
- 补充横向滚动条样式（此前仅 vertical，横向滚动条为系统默认导致显示不佳）
- 滚动条加宽、滑块渐变、hover 反馈
- 按钮分级：btnPrimary / btnSuccess / btnWarning / btnDanger / btnSecondary
- 步骤卡片 stepCard + 编号徽章 stepBadge，界面更直观
- 表格表头与分区优化
"""

QSS = """
/* ---------- 全局 ---------- */
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    outline: none;
}
QMainWindow, QWidget#Central {
    background: #eef1f6;
}
QToolBar {
    background: #ffffff;
    border: none;
    border-bottom: 1px solid #dde3ec;
    padding: 4px 10px;
    spacing: 6px;
}
QToolBar QToolButton {
    background: transparent;
    color: #1f2d3d;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 600;
}
QToolBar QToolButton:hover {
    background: #e8f0fe;
    color: #1565c0;
}
QToolBar QToolButton:pressed {
    background: #d6e4fb;
}
QToolBar QLabel {
    color: #3d4d63;
    padding: 0 2px 0 10px;
}

/* ---------- 步骤流程条 ---------- */
QFrame#stepBar {
    background: #ffffff;
    border-bottom: 1px solid #dde3ec;
}
QLabel#stepText {
    color: #6b7a90;
    font-size: 12px;
    font-weight: 600;
}
QLabel#stepText[active="true"] {
    color: #1565c0;
    font-size: 13px;
    font-weight: 700;
}
QLabel#stepArrow {
    color: #b6c2d4;
    font-size: 14px;
    font-weight: 700;
}
QLabel#calibBadgeOk {
    background: #dcfce7;
    color: #15803d;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 700;
}
QLabel#calibBadgeWarn {
    background: #fef3c7;
    color: #b45309;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 700;
}

/* ---------- 分组卡片 ---------- */
QGroupBox {
    background: #ffffff;
    border: 1px solid #dde3ec;
    border-radius: 10px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 700;
    color: #1f2d3d;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background: #ffffff;
    color: #1565c0;
}

/* ---------- 步骤卡片 ---------- */
QFrame#stepCard {
    background: #ffffff;
    border: 1px solid #dde3ec;
    border-radius: 10px;
}
QLabel#stepBadge {
    background: #3b82f6;
    color: #ffffff;
    border-radius: 11px;
    min-width: 22px;
    max-width: 22px;
    min-height: 22px;
    max-height: 22px;
    font-size: 12px;
    font-weight: 700;
    qproperty-alignment: AlignCenter;
}
QLabel#stepTitle {
    color: #1f2d3d;
    font-size: 14px;
    font-weight: 700;
}
QLabel#stepHint {
    color: #6b7a90;
    font-size: 12px;
}

/* ---------- Tab ---------- */
QTabWidget::pane {
    border: 1px solid #dde3ec;
    border-radius: 10px;
    background: transparent;
    top: -1px;
}
QTabBar::tab {
    background: #e4e9f2;
    color: #3d4d63;
    border: none;
    border-radius: 8px 8px 0 0;
    padding: 9px 20px;
    margin-right: 4px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #1565c0;
    border: 1px solid #dde3ec;
    border-bottom: none;
}
QTabBar::tab:hover:!selected {
    background: #d6e4fb;
}

/* ---------- 按钮 ---------- */
QPushButton {
    background: #3b82f6;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background: #2f6fe0;
}
QPushButton:pressed {
    background: #2758c0;
}
QPushButton:disabled {
    background: #c4cdd9;
    color: #f0f3f8;
}
QPushButton#btnPrimary {
    background: #3b82f6;
    color: #ffffff;
    font-size: 14px;
    padding: 10px 20px;
    border-radius: 9px;
}
QPushButton#btnPrimary:hover {
    background: #2f6fe0;
}
QPushButton#btnPrimary:disabled {
    background: #c4cdd9;
}
QPushButton#btnSuccess {
    background: #16a34a;
    color: #ffffff;
}
QPushButton#btnSuccess:hover {
    background: #128a3f;
}
QPushButton#btnSuccess:disabled {
    background: #b7dcc6;
}
QPushButton#btnWarning {
    background: #f59e0b;
    color: #ffffff;
}
QPushButton#btnWarning:hover {
    background: #d97706;
}
QPushButton#btnDanger {
    background: #ef4444;
    color: #ffffff;
}
QPushButton#btnDanger:hover {
    background: #dc2626;
}
QPushButton#btnSecondary {
    background: #f0f4fa;
    color: #1f2d3d;
    border: 1px solid #ccd6e4;
}
QPushButton#btnSecondary:hover {
    background: #e2eaf6;
}
QPushButton#btnSecondary:disabled {
    background: #f4f6fa;
    color: #a8b3c2;
    border-color: #e3e8f0;
}
QPushButton#btnPick {
    background: #eef2ff;
    color: #4338ca;
    border: 1px solid #c7d2fe;
}
QPushButton#btnPick:hover {
    background: #e0e7ff;
}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #f8fafc;
    border: 1px solid #ccd6e4;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 20px;
    color: #1f2d3d;
    selection-background-color: #3b82f6;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #3b82f6;
    background: #ffffff;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #ccd6e4;
    border-radius: 6px;
    selection-background-color: #e8f0fe;
    selection-color: #1565c0;
    padding: 4px;
}

/* ---------- 复选框 / 单选 ---------- */
QCheckBox {
    color: #1f2d3d;
    spacing: 6px;
}

/* ---------- 表格 ---------- */
QTableWidget {
    background: #ffffff;
    border: 1px solid #dde3ec;
    border-radius: 8px;
    gridline-color: #eef1f6;
    alternate-background-color: #f8fafc;
    selection-background-color: #dbeafe;
    selection-color: #1f2d3d;
}
QTableWidget::item {
    padding: 3px 6px;
    border: none;
}
QTableWidget::item:selected {
    background: #dbeafe;
    color: #1f2d3d;
}
QTableWidget::item:hover {
    background: #f0f7ff;
}
QHeaderView::section {
    background: #f0f4fa;
    color: #3d4d63;
    border: none;
    border-right: 1px solid #dde3ec;
    border-bottom: 1px solid #dde3ec;
    padding: 6px 8px;
    font-weight: 700;
}
QHeaderView::section:first {
    border-left: none;
}
QTableCornerButton::section {
    background: #f0f4fa;
    border: none;
    border-bottom: 1px solid #dde3ec;
}

/* ---------- 进度条 ---------- */
QProgressBar {
    background: #e4e9f2;
    border: none;
    border-radius: 6px;
    text-align: center;
    color: #1f2d3d;
    min-height: 14px;
    max-height: 16px;
    font-size: 11px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #3b82f6, stop:1 #60a5fa);
    border-radius: 6px;
}

/* ---------- 状态栏 ---------- */
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #dde3ec;
    color: #3d4d63;
    min-height: 24px;
}
QStatusBar::item {
    border: none;
}
QStatusBar QLabel {
    padding: 2px 6px;
}

/* ---------- 滚动条（垂直 + 水平统一美化） ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #b3c0d1, stop:1 #9aa8ba);
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #7d97b5, stop:1 #6b87a8);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    width: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #b3c0d1, stop:1 #9aa8ba);
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #7d97b5, stop:1 #6b87a8);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0;
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ---------- 分割器 ---------- */
QSplitter::handle {
    background: transparent;
    width: 5px;
}
QSplitter::handle:hover {
    background: #d6e4fb;
}

/* ---------- 消息框 ---------- */
QMessageBox {
    background: #ffffff;
}
QMessageBox QLabel {
    color: #1f2d3d;
    font-size: 13px;
}
QMessageBox QPushButton {
    min-width: 72px;
}

/* ---------- 工具提示 ---------- */
QToolTip {
    background: #1f2d3d;
    color: #ffffff;
    border: none;
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 12px;
}

/* ---------- 顶部步骤流程条 ---------- */
QFrame#stepBar {
    background: #ffffff;
    border-bottom: 1px solid #e4e9f2;
}
QLabel#stepArrow {
    color: #b6c2d4;
    font-size: 15px;
    font-weight: bold;
    padding: 0 2px;
}
QLabel#stepBadge {
    background: #e4e9f2;
    color: #6b7a90;
    border-radius: 11px;
    font-size: 12px;
    font-weight: 700;
    qproperty-alignment: AlignCenter;
}
QLabel#stepText {
    color: #6b7a90;
    font-size: 13px;
    font-weight: 600;
}
QLabel#stepText[active="true"] {
    color: #2563eb;
    font-weight: 700;
}
QLabel#calibBadgeOk {
    background: #e6f6ec;
    color: #16a34a;
    border: 1px solid #b7e6c9;
    border-radius: 11px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
}
QLabel#calibBadgeWarn {
    background: #fff3e0;
    color: #ea580c;
    border: 1px solid #f5d9ac;
    border-radius: 11px;
    padding: 2px 10px;
    font-size: 12px;
    font-weight: 600;
}

/* ---------- 结果汇总条 ---------- */
QFrame#summaryBar {
    background: #ffffff;
    border: 1px solid #dde3ec;
    border-radius: 8px;
}
QLabel#summaryLabel {
    color: #1f2d3d;
    font-size: 13px;
}
"""


# ---------- 暗色主题（v3.5）----------
# 只做"浅色 -> 深色"的确定性颜色映射，布局/字体/圆角全部沿用浅色主题，风险最低。
_DARK_MAP = {
    "#ffffff": "#1b2129", "#f8fafc": "#151a21", "#f4f6fa": "#171c24",
    "#f0f4fa": "#171d25", "#f0f7ff": "#18222e", "#eef2ff": "#1a2233",
    "#eef1f6": "#0f1319", "#e8f0fe": "#22303f", "#e6f6ec": "#1b2b21",
    "#e4e9f2": "#242c36", "#e4e6eb": "#242c36", "#dde3ec": "#2b3440",
    "#dbeafe": "#1e2f47", "#d6e4fb": "#26374b", "#ccd6e4": "#3a4450",
    "#c4cdd9": "#3f4a57", "#b6c2d4": "#4a5563", "#b3c0d1": "#4d5866",
    "#1f2d3d": "#e6ebf2", "#3d4d63": "#c2cbd6", "#6b7a90": "#9aa7b6",
    "#6b87a8": "#8fa0b3", "#7d97b5": "#94a5b8", "#9aa8ba": "#adbac8",
    "#3b82f6": "#4c9aff", "#1565c0": "#8ab8ff", "#2f6fe0": "#5c9bff",
    "#16a34a": "#35c66f", "#15803d": "#4ade80", "#ea580c": "#f59e0b",
    "#ef4444": "#f87171",
}


def build_dark(qss):
    out = qss
    for k in sorted(_DARK_MAP, key=len, reverse=True):
        out = out.replace(k, _DARK_MAP[k])
    return out


QSS_DARK = build_dark(QSS)


def qss_for(mode):
    return QSS_DARK if str(mode).lower() == "dark" else QSS


# ================= v3.5 视觉增强（左栏卡片化 / 状态卡 / 折叠行 / 间距节奏） =================
# 说明：只覆盖"观感"属性（圆角/间距/字号/颜色），不改任何控件结构，因此不影响业务逻辑。
QSS_V35 = """
/* 卡片：更柔的圆角 + 更松的内边距，标题不再抢眼 */
QGroupBox {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
    margin-top: 18px; padding: 18px 14px 14px 14px;
    font-size: 13px; font-weight: 600; color: #1f2d3d;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 14px; padding: 0 6px;
    background: #ffffff; color: #1f2d3d; font-size: 13px; font-weight: 600;
}
/* 蓝色强调卡（阵列规格等关键数据） */
QFrame#statusCard { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 12px; }
QFrame#okCard { background: #ecfdf5; border: 1px solid #bbf7d0; border-radius: 12px; }
QFrame#warnCard { background: #fffbeb; border: 1px solid #fde68a; border-radius: 12px; }
QLabel#cardTitle { color: #1d4ed8; font-size: 12px; font-weight: 600; }
QLabel#cardValue { color: #1d4ed8; font-size: 24px; font-weight: 700; }
QLabel#cardHint { color: #1e40af; font-size: 11px; }
QLabel#okTitle { color: #15803d; font-size: 12px; font-weight: 600; }
QLabel#okValue { color: #15803d; font-size: 20px; font-weight: 700; }
QLabel#okHint { color: #166534; font-size: 11px; }
QLabel#badge { color: #6b7a90; font-size: 11px; }
/* 折叠行 */
QPushButton#collapseRow {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    text-align: left; padding: 0 12px; color: #6b7a90; font-size: 12px; min-height: 36px;
}
QPushButton#collapseRow:hover { background: #f1f5f9; color: #334155; }
QPushButton#collapseRow:checked { background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }
/* 表单控件统一高度与圆角 */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    min-height: 28px; border-radius: 6px; border: 1px solid #dde3ec;
    background: #ffffff; color: #1f2d3d; padding: 0 8px;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #3b82f6; }
/* 表格：表头更淡、行高更松、选中更明显 */
QTableWidget { gridline-color: #eef1f6; selection-background-color: #dbeafe; selection-color: #1f2d3d; }
QHeaderView::section {
    background: #f0f4fa; color: #6b7a90; border: none; border-bottom: 1px solid #e2e8f0;
    padding: 6px 8px; font-size: 12px; font-weight: 600;
}
/* 滚动区透明、滚动条更细 */
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #ccd6e4; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #b6c2d4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
/* 分隔条 */
/* v3.6 日志抽屉：默认收起，顶部一条分隔线 */
QFrame#logDrawer { background: #ffffff; border-top: 1px solid #e2e8f0; }
/* ===== v3.8 组件层（ui_kit）：卡片 / 徽章 / 行标签 / 危险按钮 / 进度条 ===== */
QFrame#cardBox { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; }
QLabel#cardHeader { color: #1f2d3d; font-size: 14px; font-weight: 700; }
QLabel#rowLabel { color: #6b7a90; font-size: 12px; }
QLabel#rowValue { color: #1f2d3d; font-size: 12px; font-weight: 600; }
QLabel#rowUnit { color: #9aa8ba; font-size: 11px; }
QLabel#chipOk { background: #ecfdf5; color: #15803d; border: 1px solid #bbf7d0;
                border-radius: 10px; padding: 1px 8px; font-size: 11px; }
QLabel#chipWarn { background: #fffbeb; color: #b45309; border: 1px solid #fde68a;
                  border-radius: 10px; padding: 1px 8px; font-size: 11px; }
QLabel#chipInfo { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;
                  border-radius: 10px; padding: 1px 8px; font-size: 11px; }
QLabel#chipMuted { background: #f4f6fa; color: #6b7a90; border: 1px solid #e3e8f0;
                   border-radius: 10px; padding: 1px 8px; font-size: 11px; }
QPushButton#btnDanger { background: #dc2626; color: #ffffff; border: none;
                        border-radius: 9px; min-height: 34px; padding: 0 16px; font-weight: 700; }
QPushButton#btnDanger:hover { background: #b91c1c; }
QPushButton#btnGhost { background: transparent; border: none; color: #6b7a90; padding: 0 8px; }
QPushButton#btnGhost:hover { color: #3b82f6; }
QProgressBar { border: none; background: #eef1f6; border-radius: 4px; max-height: 8px; min-height: 8px;
               text-align: center; color: transparent; }
QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
QLabel#aboutLogo { background: #3b82f6; color: #ffffff; border-radius: 14px; font-size: 16px; font-weight: 700; }

/* ===== v3.9 方案A：菜单栏 / 步骤胶囊 / 输入框去边框 ===== */
QMenuBar { background: #ffffff; border-bottom: 1px solid #e2e8f0; color: #1f2d3d; padding: 2px 6px; }
QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 6px; }
QMenuBar::item:selected { background: #eff6ff; color: #1d4ed8; }
QPushButton#stepPill { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px;
                       color: #3d4d63; font-size: 13px; font-weight: 600; min-height: 38px; }
QPushButton#stepPill:hover { border-color: #bfdbfe; color: #1d4ed8; }
QPushButton#stepPillActive { background: #3b82f6; border: 1px solid #3b82f6; border-radius: 10px;
                             color: #ffffff; font-size: 13px; font-weight: 700; min-height: 38px; }
QPushButton#stepPillDone { background: #ecfdf5; border: 1px solid #bbf7d0; border-radius: 10px;
                           color: #15803d; font-size: 13px; font-weight: 600; min-height: 38px; }
QLabel#stepArrow { color: #c4cdd9; font-size: 13px; font-weight: 700; }
QLabel#stepHint { color: #9aa8ba; font-size: 11px; }
/* 输入控件去重边框（方案A 的观感：值几乎裸排，聚焦才出现描边） */
QSpinBox, QDoubleSpinBox, QComboBox {
    border: 1px solid transparent; background: transparent; padding: 0 4px; border-radius: 6px;
}
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: #dfe6ee; background: #ffffff; }
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #3b82f6; background: #ffffff; }

/* v3.9.3 预览工具条 + 结果汇总条 */
QFrame#previewBar { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; }
QLabel#summaryStrip { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px;
                      padding: 6px 10px; }
QPlainTextEdit#fitReport { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
                           color: #1f2d3d; font-size: 12px; padding: 8px; }

/* v3.7.2 语义角色：替代代码里写死的颜色，深浅主题各一套 */
QLabel#hintWarn { color: #b26a00; font-weight: 700; }
QLabel#okText { color: #2e7d32; font-weight: 700; }
QLabel#errText { color: #c62828; font-weight: 700; }
QLabel#dimText { color: #6b7a90; }
QLabel#badgeOk { background: #16a34a; color: #ffffff; border-radius: 11px; }
QLabel#badgeActive { background: #3b82f6; color: #ffffff; border-radius: 11px; }
QLabel#badgeTodo { background: #e4e9f2; color: #6b7a90; border-radius: 11px; }
QPushButton#btnManualActive { background: #ff9500; color: #ffffff; font-weight: 700; }

/* v3.6.1 次级按钮的选中态（用于"微调/手动识别"这类模式开关） */
QPushButton#btnSecondary:checked {
    background: #dbeafe; border-color: #3b82f6; color: #1d4ed8; font-weight: 700;
}
QSplitter::handle { background: transparent; }
"""


def _build_dark_v35(qss):
    m = {
        "#eff6ff": "#16233a", "#bfdbfe": "#27436b", "#ecfdf5": "#12281c", "#bbf7d0": "#245c3c",
        "#fffbeb": "#2a2113", "#fde68a": "#6b5320", "#f1f5f9": "#232a34", "#334155": "#c7d2de",
        "#e2e8f0": "#2b3440", "#eef1f6": "#0f1319", "#dbeafe": "#1e2f47", "#ccd6e4": "#3a4450",
        "#b6c2d4": "#4a5563", "#f0f4fa": "#20262f",
        # v3.7：v35/v37 样式里的白底与浅底也必须映射，否则深色下卡片仍是白的
        "#ffffff": "#1b2129", "#f8fafc": "#20262f", "#dfe6ee": "#2b3440",
        "#c4cdd9": "#3a4450", "#f1f3f5": "#151a21",
    }
    out = qss
    for k in sorted(m, key=len, reverse=True):
        out = out.replace(k, m[k])
    return out


QSS_V35_DARK = _build_dark_v35(QSS_V35)


QSS_V37 = """
/* ===== v3.7 观感升级：标题移到卡片外、控件统一高度、复选/按钮重绘 ===== */
QGroupBox {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px;
    margin-top: 0px; padding: 30px 14px 14px 14px;
    font-weight: 400; color: #1f2d3d;
}
QGroupBox::title {
    subcontrol-origin: padding; subcontrol-position: top left;
    top: 9px; left: 14px; padding: 0; background: transparent;
    color: #1f2d3d; font-size: 15px; font-weight: 700;
}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    min-height: 28px; max-height: 28px; border: 1px solid #dfe6ee; border-radius: 7px;
    background: #ffffff; color: #1f2d3d; padding: 0 8px;
}
QSpinBox, QDoubleSpinBox { qproperty-alignment: 'AlignRight | AlignVCenter'; }
QComboBox::drop-down { border: none; width: 18px; }
QCheckBox { spacing: 8px; }
QPushButton#btnPrimary { min-height: 44px; font-size: 14px; font-weight: 700; border-radius: 9px; }
QPushButton#btnSecondary { min-height: 34px; border: 1px solid #dde3ec; border-radius: 9px; background: #ffffff; color: #1f2d3d; }
QPushButton#btnSecondary:hover { background: #f8fafc; border-color: #c4cdd9; }
QLabel#hintLabel, QLabel#dimLabel { color: #6b7a90; font-size: 11px; }
/* v3.7 覆盖基础样式里的"文件夹页签"，改为下划线式（对齐效果图） */
QTabBar::tab {
    background: transparent; color: #6b7a90; padding: 8px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; border-radius: 0; font-size: 13px;
}
QTabBar::tab:selected {
    background: transparent; color: #1d4ed8; border: none;
    border-bottom: 2px solid #3b82f6; font-weight: 600;
}
QTabBar::tab:hover:!selected { background: transparent; color: #1f2d3d; }
QTabWidget::pane { border: none; background: #eef1f6; }

"""

# ---------- 组合出最终两套样式（基础 + v3.5 增强）----------
QSS_FULL = QSS + QSS_V35 + QSS_V37
QSS_FULL_DARK = build_dark(QSS) + QSS_V35_DARK + _build_dark_v35(QSS_V37)


def qss_for(mode):
    """最终样式：mode='dark' 用深色，其它用浅色。"""
    return QSS_FULL_DARK if str(mode).lower() == "dark" else QSS_FULL

# ---------- v3.7.1：深色补全（审计发现 15 个亮色未映射）----------
# 说明：基础表与 v35/v37 表是分开维护的，容易漏色；这里做一次统一的收口处理，
# 只替换"浅色专用"的底色/边框/弱化文字，高亮强调色（#60a5fa/#f59e0b 等）保持原样。
_EXTRA_DARK = {
    "#fff3e0": "#2a2113", "#f5d9ac": "#6b5320", "#fef3c7": "#2a2113",
    "#dcfce7": "#12281c", "#b7e6c9": "#245c3c", "#b7dcc6": "#245c3c",
    "#e0e7ff": "#1a2233", "#c7d2fe": "#2b3f6b", "#eef2ff": "#1a2233",
    "#f4f6fa": "#171c24", "#a8b3c2": "#728196", "#e2eaf6": "#232a34",
    "#e3e8f0": "#2b3440", "#dde3ec": "#2b3440", "#f0f3f8": "#c7d2de",
    "#b26a00": "#f59e0b", "#2e7d32": "#35c66f", "#c62828": "#f87171",
    "#6b7a90": "#9aa7b6", "#e4e9f2": "#2b3440", "#ff9500": "#f59e0b",
}


def _apply_extra_dark(qss: str) -> str:
    out = qss
    for k in sorted(_EXTRA_DARK, key=len, reverse=True):
        out = out.replace(k, _EXTRA_DARK[k])
    return out


# 统一收口：把漏映射的亮色在深色样式里补掉（必须在 _apply_extra_dark 定义之后）
QSS_FULL_DARK = _apply_extra_dark(QSS_FULL_DARK)

# ---------- v3.7.2：按主题取色（供富文本 span / 表格前景等代码内用色） ----------
# 背景：很多地方用 f"<span style='color:#15803d'>" 或 QColor("#dc2626") 写死颜色，
# 深色模式下这些深色文字在深底上几乎看不清。统一改走 c("ok"/"err"/...) 取当前主题色。
CURRENT_MODE = "light"

_PALETTE = {
    "light": {"ok": "#15803d", "err": "#dc2626", "warn": "#b26a00", "info": "#2563eb",
              "text": "#1f2d3d", "dim": "#6b7a90"},
    "dark": {"ok": "#4ade80", "err": "#f87171", "warn": "#f59e0b", "info": "#60a5fa",
             "text": "#e6ebf2", "dim": "#9aa7b6"},
}


def c(name: str) -> str:
    """取当前主题下的语义色。name: ok / err / warn / info / text / dim"""
    pal = _PALETTE["dark" if str(CURRENT_MODE).lower() == "dark" else "light"]
    return pal.get(name, pal["text"])


def set_mode(mode: str) -> None:
    """由主窗口在切换主题时调用，保持代码内取色与样式表一致。"""
    global CURRENT_MODE
    CURRENT_MODE = "dark" if str(mode).lower() == "dark" else "light"

# ================= v3.7.3：按属性语义生成深色样式（最终版） =================
# 为什么重做：之前用 dict 做"颜色全局替换"，把 color: #ffffff（白字按钮）也换成了深色，
# 结果深色下按钮文字、卡片标题、输入框数字全都"深字压深底"看不见。
# 正确做法是按属性分别映射：
#   color:*            -> 只换文字色；白/近白色保持不动
#   background*:, gridline-color -> 只换背景；仅当原色是亮色才换
#   border*:           -> 只换边框；仅当原色是亮色才换
_HEX_RE = __import__("re").compile(r"#[0-9a-fA-F]{3,8}")
_DECL_RE = __import__("re").compile(r"([A-Za-z-]+)\s*:\s*([^;{}]+)")


def _lum(h: str) -> float:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 0.0
    return (r * 299 + g * 587 + b * 114) / 1000.0


_SURFACE = {
    "#ffffff": "#1b2129", "#f8fafc": "#20262f", "#f4f6fa": "#171c24", "#f0f4fa": "#171d25",
    "#f0f7ff": "#18222e", "#eef2ff": "#1a2233", "#eff6ff": "#16233a", "#ecfdf5": "#12281c",
    "#fffbeb": "#2a2113", "#dcfce7": "#12281c", "#e0e7ff": "#1a2233", "#fff3e0": "#2a2113",
    "#fef3c7": "#2a2113", "#f1f5f9": "#232a34", "#f1f3f5": "#151a21", "#e2eaf6": "#232a34",
    "#eef1f6": "#0f1319", "#dbeafe": "#1e2f47", "#e4e9f2": "#2b3440", "#fbfcfe": "#0f1319",
    "#d6e4fb": "#22303f", "#e8f0fe": "#22303f", "#e6f6ec": "#12281c", "#b7dcc6": "#245c3c",
    "#c4cdd9": "#3a4450", "#b3c0d1": "#3a4450", "#9aa8ba": "#3a4450",
}
_BORDER = {
    "#dde3ec": "#2b3440", "#e2e8f0": "#2b3440", "#dfe6ee": "#2b3440", "#c4cdd9": "#3a4450",
    "#bfdbfe": "#27436b", "#bbf7d0": "#245c3c", "#fde68a": "#6b5320", "#f5d9ac": "#6b5320",
    "#b7e6c9": "#245c3c", "#c7d2fe": "#2b3f6b", "#e3e8f0": "#2b3440", "#ccd6e4": "#3a4450",
    "#e4e9f2": "#2b3440", "#c4cdd9": "#3a4450",
    "#ffffff": "#2b3440",
}
_TEXT = {
    "#1f2d3d": "#e6ebf2", "#3d4d63": "#c7d2de", "#111827": "#e6ebf2", "#334155": "#c7d2de",
    "#1d4ed8": "#60a5fa", "#1565c0": "#60a5fa", "#2563eb": "#60a5fa", "#1e40af": "#93c5fd",
    "#15803d": "#4ade80", "#166534": "#4ade80", "#2e7d32": "#4ade80", "#16a34a": "#4ade80",
    "#b45309": "#f59e0b", "#b26a00": "#f59e0b", "#c62828": "#f87171", "#4338ca": "#818cf8",
    "#6b7a90": "#9aa7b6", "#4a5563": "#c7d2de", "#a8b3c2": "#728196", "#f0f3f8": "#9aa7b6",
}


def _to_dark(qss: str) -> str:
    def fix_decl(m):
        prop, val = m.group(1), m.group(2)
        p = prop.strip().lower()

        def sub(mm):
            v = mm.group(0).lower()
            if "background" in p or "gridline" in p:
                return _SURFACE.get(v, mm.group(0)) if _lum(v) > 150 else mm.group(0)
            if "border" in p:
                return _BORDER.get(v, mm.group(0)) if _lum(v) > 150 else mm.group(0)
            if p.endswith("color"):        # color / selection-color / alternate-color ...
                if v in ("#ffffff", "#fff", "#fafafa"):   # 白字必须保持白
                    return mm.group(0)
                return _TEXT.get(v, mm.group(0))
            return mm.group(0)

        return prop + ": " + _HEX_RE.sub(sub, val)

    return _DECL_RE.sub(fix_decl, qss)


# 以浅色完整样式为基准，一次性生成深色样式（覆盖上文旧实现）
QSS_FULL_DARK = _to_dark(QSS_FULL)

# 强调色：深色下保持原样（蓝按钮、绿成功、橙警示等）
_KEEP = {"#3b82f6", "#60a5fa", "#2563eb", "#1d4ed8", "#16a34a", "#22c55e", "#ff9500",
         "#f59e0b", "#dc2626", "#ef4444", "#f87171", "#e11d48", "#8b5cf6", "#0ea5e9"}

_DARK_BG = "#1b2129"
_DARK_BORDER = "#2b3440"


def _to_dark_final(qss: str) -> str:
    """在 _to_dark 之上再兜底一遍：任何背景/边框里的亮色，只要不在映射表且不是强调色，
    统一压成深色。这样即使以后新增样式漏了映射，也不会出现"白块"。"""
    def fix_decl(m):
        prop, val = m.group(1), m.group(2)
        p = prop.strip().lower()

        def sub(mm):
            v = mm.group(0).lower()
            # 强调色只在"当背景/边框"时保留；当文字色时必须提亮，否则深蓝字压深底
            is_surface_prop = ("background" in p) or ("gridline" in p) or ("border" in p)
            if v in _KEEP and is_surface_prop:
                return mm.group(0)
            if "background" in p or "gridline" in p:
                if _lum(v) > 150:
                    return _SURFACE.get(v, _DARK_BG)
                return mm.group(0)
            if "border" in p:
                if _lum(v) > 150:
                    return _BORDER.get(v, _DARK_BORDER)
                return mm.group(0)
            if p.endswith("color"):
                if v in ("#ffffff", "#fff"):
                    return mm.group(0)
                if _lum(v) < 120:
                    return _TEXT.get(v, "#e6ebf2")
                return mm.group(0)
            return mm.group(0)

        return prop + ": " + _HEX_RE.sub(sub, val)

    return _DECL_RE.sub(fix_decl, qss)


QSS_FULL_DARK = _to_dark_final(QSS_FULL)

# ================= v3.7.5：修正转换器的选择器污染 =================
# 教训：之前的 _to_dark_final 用一条正则 "名字: 值" 扫全表，把选择器里的双冒号
# （QToolButton::hover）也当成声明改成了 "QToolButton: hover"，整张样式表随之非法，
# Qt 会**丢弃整张表**，于是界面看起来"根本没换肤"。
# 现在只对 { } 内部（声明区）做颜色映射，选择器原样保留。
_BODY_RE = __import__("re").compile(r"\{([^{}]*)\}")


def _fix_body(body: str) -> str:
    def fix_decl(m):
        prop, val = m.group(1), m.group(2)
        p = prop.strip().lower()

        def sub(mm):
            v = mm.group(0).lower()
            is_surface_prop = ("background" in p) or ("gridline" in p) or ("border" in p)
            if v in _KEEP and is_surface_prop:
                return mm.group(0)
            if "background" in p or "gridline" in p:
                if _lum(v) > 150:
                    return _SURFACE.get(v, _DARK_BG)
                return mm.group(0)
            if "border" in p:
                if _lum(v) > 150:
                    return _BORDER.get(v, _DARK_BORDER)
                return mm.group(0)
            if p.endswith("color"):
                if v in ("#ffffff", "#fff"):
                    return mm.group(0)
                if _lum(v) < 120:
                    return _TEXT.get(v, "#e6ebf2")
                return mm.group(0)
            return mm.group(0)

        return prop + ": " + _HEX_RE.sub(sub, val)

    return _DECL_RE.sub(fix_decl, body)


def _to_dark_safe(qss: str) -> str:
    return _BODY_RE.sub(lambda m: "{" + _fix_body(m.group(1)) + "}", qss)


QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

# ================= v3.9.1：收口规则（必须放在最后，靠"后写优先"压过前面所有重复规则） =================
# 背景：QSS 里同一选择器出现多次时，靠后的生效。之前分散在 V35/V37 的规则互相覆盖，
# 导致"去边框输入框""标题在卡片内"看起来没生效。
QSS_FINAL = """
/* 卡片：标题在卡片内部顶端，不再出现"灰色条带" */
QGroupBox {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px;
    margin-top: 0px; padding: 34px 14px 12px 14px;
    font-weight: 400; color: #1f2d3d;
}
QGroupBox::title {
    subcontrol-origin: padding; subcontrol-position: top left;
    top: 11px; left: 14px; padding: 0; background: transparent;
    color: #1f2d3d; font-size: 15px; font-weight: 700;
}
/* 输入控件：默认无边框，值直接排布（方案A 观感）；悬停/聚焦才出现描边 */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    border: 1px solid transparent; background: transparent;
    min-height: 28px; max-height: 28px; border-radius: 6px; padding: 0 4px;
    color: #1f2d3d;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border: 1px solid #dfe6ee; background: #ffffff;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border: 1px solid #3b82f6; background: #ffffff;
}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 16px; border: none; background: transparent; }
/* 下拉弹出列表也跟随主题 */
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #e2e8f0; selection-background-color: #eff6ff;
    selection-color: #1d4ed8; color: #1f2d3d; outline: none;
}
"""

QSS_FULL = QSS_FULL + QSS_FINAL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

# v3.9.5：汇总条着色（最后生效）+ 复选框回退系统绘制（自绘缺对勾）
QSS_TAIL = """
QFrame#summaryBar { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; }
QLabel#summaryLabel { color: #1e40af; font-size: 12px; background: transparent; }
QCheckBox { spacing: 8px; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

# v3.9.7：数值右对齐到卡片右边缘（渲染图观感）
QSS_TAIL = """
QSpinBox, QDoubleSpinBox { padding-right: 2px; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_TAIL = """
QPushButton#collapseRow { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
                          text-align: left; padding: 0 12px; color: #6b7a90; font-size: 12px; min-height: 34px; }
QPushButton#collapseRow:checked { background: #eff6ff; border-color: #bfdbfe; color: #1d4ed8; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_TAIL = """
QMenuBar { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px;
           margin: 6px 10px 0px 10px; padding: 3px 6px; }
QMenuBar::item { background: transparent; padding: 4px 12px; border-radius: 6px; }
QMenuBar::item:selected { background: #eff6ff; color: #1d4ed8; }
QMenuBar::item:pressed { background: #dbeafe; }
QFrame#stepBar { background: transparent; border: none; margin-top: 2px; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_TAIL = """
QGroupBox#flatCard { padding-top: 12px; }
QComboBox { border: 1px solid #dfe6ee; background: #ffffff; padding: 0 6px 0 4px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right;
                       width: 16px; border: none; background: transparent; }
QLabel#statusInfo { color: #6b7a90; font-size: 11px; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_TAIL = """
QLabel#previewPlaceholder { color: #9aa8ba; font-size: 13px; background: transparent; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_TAIL = """
QPushButton#collapseRow { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
                          text-align: left; padding: 0 12px; color: #6b7a90; font-size: 12px; min-height: 34px; }
QPushButton#collapseRow:checked { background: #f8fafc; border-color: #e2e8f0; color: #6b7a90; }
QComboBox::down-arrow { width: 8px; height: 8px; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_TAIL = """
QPushButton#btnDanger { background: #ffffff; color: #dc2626; border: 1px solid #fca5a5;
                        border-radius: 9px; min-height: 34px; padding: 0 14px; }
QPushButton#btnDanger:hover { background: #fef2f2; border-color: #dc2626; }
QLabel#previewPlaceholder { color: #9aa8ba; font-size: 13px; background: transparent; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)

QSS_DARK_EXTRA = """
QPushButton:disabled { background: #2b3440; color: #8b95a5; border: 1px solid #2b3440; }
QPushButton#btnPrimary:disabled { background: #2b3440; color: #8b95a5; }
QPushButton#btnSecondary:disabled { background: transparent; color: #6f7d8c; border-color: #2b3440; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled { color: #6f7d8c; }
QCheckBox:disabled { color: #6f7d8c; }
"""
QSS_FULL_DARK = QSS_FULL_DARK + QSS_DARK_EXTRA

QSS_TAIL = """
QPushButton:disabled { background: #eef1f6; color: #9aa8ba; border: 1px solid #e2e8f0; }
QPushButton#btnPrimary:disabled { background: #c9d3e0; color: #ffffff; }
"""
QSS_FULL = QSS_FULL + QSS_TAIL
QSS_FULL_DARK = _to_dark_safe(QSS_FULL)
