# -*- coding: utf-8 -*-
"""统一 UI 组件（v3.8）：把渲染图里的卡片 / 状态徽章 / 按钮层级 / 对话框收口成一套。

设计原则：
- 只用 objectName + theme.py 的 QSS 上色，代码里不写死颜色（深色主题自动跟随）；
- 卡片 = QFrame#cardBox + 标题行（QLabel#cardHeader）+ 内容区；
- 状态徽章 = QLabel，setObjectName 切换 chipOk / chipWarn / chipInfo / chipMuted；
- 文件对话框使用系统原生实现（中文、系统快捷键）；代价是不跟随深色主题。
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
                             QLabel, QPushButton, QVBoxLayout, QWidget)


# ---------------- 卡片 ----------------
def card(title: str = "", parent=None) -> QFrame:
    box = QFrame(parent)
    box.setObjectName("cardBox")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    if title:
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lab = QLabel(title)
        lab.setObjectName("cardHeader")
        hdr.addWidget(lab)
        hdr.addStretch(1)
        lay.addLayout(hdr)
    return box


def card_body(box: QFrame) -> QVBoxLayout:
    return box.layout()


def add_row(parent_layout, label: str, value: str = "", unit: str = "") -> QLabel:
    """一行"左标签 / 右值"，返回值 QLabel 便于后续更新。"""
    row = QHBoxLayout()
    row.setSpacing(8)
    lb = QLabel(label)
    lb.setObjectName("rowLabel")
    row.addWidget(lb)
    row.addStretch(1)
    val = QLabel(value)
    val.setObjectName("rowValue")
    row.addWidget(val)
    if unit:
        u = QLabel(unit)
        u.setObjectName("rowUnit")
        row.addWidget(u)
    parent_layout.addLayout(row)
    return val


# ---------------- 徽章 ----------------
_KINDS = {"ok": "chipOk", "warn": "chipWarn", "info": "chipInfo", "muted": "chipMuted"}


def chip(text: str, kind: str = "muted", parent=None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setObjectName(_KINDS.get(kind, "chipMuted"))
    lab.setAlignment(Qt.AlignCenter)
    return lab


def set_chip(lab: QLabel, text: str, kind: str = "muted") -> None:
    lab.setText(text)
    lab.setObjectName(_KINDS.get(kind, "chipMuted"))
    _repolish(lab)


def _repolish(w) -> None:
    try:
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()
    except Exception:
        pass


# ---------------- 按钮 ----------------
def btn(text: str, kind: str = "secondary", parent=None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setObjectName({"primary": "btnPrimary", "secondary": "btnSecondary",
                     "danger": "btnDanger", "ghost": "btnGhost"}.get(kind, "btnSecondary"))
    b.setCursor(Qt.PointingHandCursor)
    return b


def btn_row(*buttons) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(10)
    row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    return row


# ---------------- 文件对话框（跟随主题） ----------------
def open_file(parent, caption: str, filt: str = "") -> str:
    path, _ = QFileDialog.getOpenFileName(parent, caption, "", filt,
                                          )
    return path or ""


def save_file(parent, caption: str, filt: str = "", default: str = "") -> str:
    path, _ = QFileDialog.getSaveFileName(parent, caption, default, filt,
                                          )
    return path or ""


def open_files(parent, caption: str, filt: str = "") -> list:
    paths, _ = QFileDialog.getOpenFileNames(parent, caption, "", filt,
                                            )
    return list(paths or [])


# ---------------- 标准对话框 ----------------
def dialog(parent, title: str, w: int = 520, h: int = 0) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(w)
    if h:
        dlg.setMinimumHeight(h)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)
    return dlg


def button_box(dlg, ok_text: str = "", cancel_text: str = "", danger: bool = False):
    bb = QDialogButtonBox()
    ok = bb.addButton(ok_text or "OK", QDialogButtonBox.AcceptRole)
    ok.setObjectName("btnDanger" if danger else "btnPrimary")
    if cancel_text:
        cc = bb.addButton(cancel_text, QDialogButtonBox.RejectRole)
        cc.setObjectName("btnSecondary")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


def form_row(layout, widget: QWidget, label: str = "") -> None:
    row = QHBoxLayout()
    row.setSpacing(10)
    if label:
        lb = QLabel(label)
        lb.setObjectName("rowLabel")
        lb.setMinimumWidth(96)
        lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(lb)
    row.addWidget(widget, 1)
    layout.addLayout(row)
