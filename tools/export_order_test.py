# -*- coding: utf-8 -*-
"""Excel 导出顺序回归：编号模式 ≠ 左上起行主序时，名义值必须与检测点同源。

背景（v4.2.4 修复）：build_point_rows 按"编号顺序"取 results[rank]，
但名义值按显示序号 i 索引 nominal_points[i+1] → 左下起行主序时 P3 的名义值
取到左上点、实测取到左下点，表现为"顺序不对/数值对不上"。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def chk(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def make_results(rows, cols, pitch):
    """行主序 results；mm=名义（无偏差），便于断言 Pn 名义==实测。"""
    c0, r0 = (cols - 1) / 2.0, (rows - 1) / 2.0
    out = []
    for r in range(rows):
        for c in range(cols):
            x = (c - c0) * pitch
            y = -(r - r0) * pitch
            out.append({
                "row": r + 1, "col": c + 1,
                "px_x": 0.0, "px_y": 0.0, "mm_x": x, "mm_y": y,
                "theo_x": x, "theo_y": y, "dx": 0.0, "dy": 0.0, "dev": 0.0,
                "verdict": "Pass", "conf": 0.9, "status": "OK",
            })
    return out


def main():
    import openpyxl
    from core.excel_exporter import export_excel
    from core.point_order import display_order

    rows = cols = 3
    pitch = 50.0
    results = make_results(rows, cols, pitch)
    nominal = [(x, y) for (x, y) in [(r["theo_x"], r["theo_y"]) for r in results]]
    nominal_p1 = [(0.0, 0.0)] + nominal
    for order in ("bottom_left_row_major", "top_left_row_major", "bottom_right_row_major"):
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            export_excel(path, results, params={"编号模式": order}, rows=rows, cols=cols,
                         nominal_points=nominal_p1, include_p1=True, order=order)
            wb = openpyxl.load_workbook(path, data_only=True)
            ws1, ws2 = wb.worksheets[0], wb.worksheets[1]
            disp = display_order(rows, cols, order)
            ok1 = True
            detail = []
            for i, rank in enumerate(disp):
                r0 = 10 + i * 2  # P1 占第 8-9 行；P3 从第 10 行起，每点 2 行
                nom_x = ws1.cell(r0, 4).value
                mea_x = ws1.cell(r0, 7).value
                nom_y = ws1.cell(r0 + 1, 4).value
                mea_y = ws1.cell(r0 + 1, 7).value
                exp_x, exp_y = nominal[rank]
                if (abs(float(nom_x) - exp_x) > 1e-6 or abs(float(nom_y) - exp_y) > 1e-6
                        or abs(float(mea_x) - exp_x) > 1e-6 or abs(float(mea_y) - exp_y) > 1e-6):
                    ok1 = False
                    detail.append("P%d nom=(%s,%s) meas=(%s,%s) exp=(%s,%s)"
                                  % (i + 3, nom_x, nom_y, mea_x, mea_y, exp_x, exp_y))
            chk(ok1, "Sheet1 名义=实测=该编号点（%s）" % order, "; ".join(detail[:3]))
            # Sheet2：序号必须 1..N 升序，且名义与同序号点一致
            seqs = []
            ok2 = True
            for rr in range(2, 2 + len(results)):
                seq = ws2.cell(rr, 1).value
                rows_col, col_col = ws2.cell(rr, 2).value, ws2.cell(rr, 3).value
                tx, ty = ws2.cell(rr, 8).value, ws2.cell(rr, 9).value
                seqs.append(int(seq))
                rank = (int(rows_col) - 1) * cols + (int(col_col) - 1)
                ex, ey = nominal[rank]
                if abs(float(tx) - ex) > 1e-6 or abs(float(ty) - ey) > 1e-6:
                    ok2 = False
            chk(ok2, "Sheet2 每行名义与行/列位置一致（%s）" % order)
            chk(seqs == sorted(seqs) and seqs == list(range(1, len(results) + 1)),
                "Sheet2 行序=编号升序（%s）" % order, str(seqs))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    print()
    print("RESULT =", "PASS" if not FAILS else "FAIL: " + "; ".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
