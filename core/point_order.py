# -*- coding: utf-8 -*-
"""编号顺序参数化：4 种编号模式（方案A配套功能）。

语义约定：
- rank = 内部索引（0..N-1），与检测结果 self.results 及网格 points_px 的固定
  顺序一致（内部恒为"左上起·行主序"，即 points_px 生成顺序，rank=row*cols+col）。
- row/col = 0-based 网格行列：row=0 为阵列顶端、row=rows-1 为底端；
  col=0 为最左列、col=cols-1 为最右列。
- display_no = 对外显示编号（1-based），由编号模式决定：同一物理位置在不同
  模式下编号不同。显示编号与 rank 的解耦由 display_no(rank) 完成。

4 种模式（与调研评估项目书一致）：
- top_left_row_major    左上起·行主序（OpenCV 惯例，默认，行为与旧版完全一致）：
  1 号在左上角，每行内从左到右，行自上而下；
- top_left_col_major    左上起·列主序：1 号在左上角，每列内自上而下，列从左到右；
- bottom_left_row_major 左下起·行主序：1 号在左下角，每行内从左到右，行自下而上；
- bottom_left_col_major 左下起·列主序：1 号在左下角，每列内自下而上，列从左到右。

红线：本模块只影响"编号/序号/行列展示与导出顺序"，不改变网格物理位置、
名义值（nominal_points_mm 仍按空间行列生成）、不触碰校准 npz 与 _to_mm_base。
"""
from typing import List, Tuple

MODE_TOP_LEFT_ROW_MAJOR = "top_left_row_major"
MODE_TOP_LEFT_COL_MAJOR = "top_left_col_major"
MODE_BOTTOM_LEFT_ROW_MAJOR = "bottom_left_row_major"
MODE_BOTTOM_LEFT_COL_MAJOR = "bottom_left_col_major"
DEFAULT_MODE = MODE_TOP_LEFT_ROW_MAJOR

# 有序模式表（UI 下拉顺序）：label 中文/英文，tooltip 简释
POINT_ORDER_MODES = [
    (MODE_BOTTOM_LEFT_ROW_MAJOR, "左下起·行主序", "Bottom-left · Row-major",
     "1 号在左下角，每行从左到右，行自下而上"),
    (MODE_TOP_LEFT_COL_MAJOR, "左上起·列主序", "Top-left · Column-major",
     "1 号在左上角，每列自上而下，列从左到右"),
    (MODE_TOP_LEFT_ROW_MAJOR, "左上起·行主序(默认)", "Top-left · Row-major (default)",
     "OpenCV 惯例：1 号在左上角，每行从左到右，行自上而下"),
    (MODE_BOTTOM_LEFT_COL_MAJOR, "左下起·列主序", "Bottom-left · Column-major",
     "1 号在左下角，每列自下而上，列从左到右"),
]


def mode_label(mode: str, zh: bool = True) -> str:
    """模式显示名（UI 下拉项用）。"""
    for m, lz, le, _ in POINT_ORDER_MODES:
        if m == mode:
            return lz if zh else le
    for m, lz, le, _ in POINT_ORDER_MODES:
        if m == DEFAULT_MODE:
            return lz if zh else le
    return mode


def mode_tooltip(mode: str, zh: bool = True) -> str:
    """模式提示（tooltip 用）。"""
    for m, _lz, _le, tp in POINT_ORDER_MODES:
        if m == mode:
            return tp if zh else tp
    return ""


def row_col_to_rank(row: int, col: int, rows: int, cols: int, mode: str = DEFAULT_MODE) -> int:
    """物理位置 (row, col) → 该位置在目标编号顺序中的编号（0-based）。

    显示编号 = 返回值 + 1。任何模式都是双射：编号 0..rows*cols-1 与物理位置一一对应。
    """
    if mode == MODE_TOP_LEFT_COL_MAJOR:
        return col * rows + row
    if mode == MODE_BOTTOM_LEFT_ROW_MAJOR:
        return (rows - 1 - row) * cols + col
    if mode == MODE_BOTTOM_LEFT_COL_MAJOR:
        return col * rows + (rows - 1 - row)
    # top_left_row_major（默认）
    return row * cols + col


def rank_to_row_col(rank: int, rows: int, cols: int, mode: str = DEFAULT_MODE) -> Tuple[int, int]:
    """编号顺序中第 rank 个（0-based）位置对应的物理 (row, col)。"""
    if mode == MODE_TOP_LEFT_COL_MAJOR:
        return rank % rows, rank // rows
    if mode == MODE_BOTTOM_LEFT_ROW_MAJOR:
        r = rank // cols
        return rows - 1 - r, rank % cols
    if mode == MODE_BOTTOM_LEFT_COL_MAJOR:
        r = rank % rows
        return rows - 1 - r, rank // rows
    return rank // cols, rank % cols


def display_no(rank: int, rows: int, cols: int, mode: str = DEFAULT_MODE) -> int:
    """内部 rank → 对外显示编号（1-based）。默认模式恒等（= rank+1）。"""
    row = rank // cols
    col = rank % cols
    return row_col_to_rank(row, col, rows, cols, mode) + 1


def display_order(rows: int, cols: int, mode: str = DEFAULT_MODE) -> List[int]:
    """按显示编号升序排列的 rank 列表（用于导出/报告按编号顺序输出）。"""
    n = rows * cols
    return sorted(range(n), key=lambda i: display_no(i, rows, cols, mode))


def _check_bijection(rows: int, cols: int, mode: str) -> None:
    """自检：display_no 必须是 1..N 双射（用于回归测试）。"""
    n = rows * cols
    nos = [display_no(i, rows, cols, mode) for i in range(n)]
    assert len(set(nos)) == n and min(nos) == 1 and max(nos) == n, (mode, rows, cols, nos)


def self_test() -> List[str]:
    """单元自检：多种阵列尺寸 × 4 模式，验证双射与首末编号位置。"""
    issues: List[str] = []
    for rows, cols in [(3, 3), (5, 5), (4, 7), (1, 5), (5, 1), (9, 13)]:
        for mode, _lz, _le, _tp in POINT_ORDER_MODES:
            try:
                _check_bijection(rows, cols, mode)
            except AssertionError as e:
                issues.append(f"bijection failed: {e}")
            # 首号位置校验
            first = rank_to_row_col(0, rows, cols, mode)
            if mode == MODE_TOP_LEFT_ROW_MAJOR and first != (0, 0):
                issues.append(f"top_left_row_major first not (0,0): {first}")
            if mode == MODE_TOP_LEFT_COL_MAJOR and first != (0, 0):
                issues.append(f"top_left_col_major first not (0,0): {first}")
            if mode == MODE_BOTTOM_LEFT_ROW_MAJOR and first != (rows - 1, 0):
                issues.append(f"bottom_left_row_major first not (rows-1,0): {first}")
            if mode == MODE_BOTTOM_LEFT_COL_MAJOR and first != (rows - 1, 0):
                issues.append(f"bottom_left_col_major first not (rows-1,0): {first}")
            # 互逆一致性：
            # 1) rank_to_row_col 是 row_col_to_rank 的逆（编号→物理位置）；
            # 2) 物理位置 (r,c) 的内部秩为 r*cols+c，其显示编号必为 i+1。
            for i in range(rows * cols):
                r, c = rank_to_row_col(i, rows, cols, mode)
                if row_col_to_rank(r, c, rows, cols, mode) != i:
                    issues.append(f"inverse mismatch {mode} {rows}x{cols} rank={i}")
                if display_no(r * cols + c, rows, cols, mode) != i + 1:
                    issues.append(f"display_no mismatch {mode} {rows}x{cols} rank={i}")
    return issues


if __name__ == "__main__":
    errs = self_test()
    if errs:
        print("FAIL:")
        for e in errs:
            print(" -", e)
        raise SystemExit(1)
    print("point_order self_test OK")
    # 打印 5×5 各模式首行编号便于人工核对
    for mode, lz, _le, _tp in POINT_ORDER_MODES:
        print(mode, lz, [display_no(i, 5, 5, mode) for i in range(5)])
