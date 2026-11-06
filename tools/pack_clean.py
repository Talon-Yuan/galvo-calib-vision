# -*- coding: utf-8 -*-
"""打包前洁净度清理：把 `_dist/GalvoCalibVision/` 里不该进交付包的东西删掉。

清什么
------
  · `logs/`                （应用运行日志，含 `app_YYYYMMDD.log`）
  · `_internal/logs/`      （PyInstaller onedir 下应用写出的日志/崩溃标记，v4.1.1 泄漏就出在这里）
  · `temp/`                （临时目录，含 `_internal/temp`）
  · 任意 `*.npz`            （校准数据，绝不能进交付包）
  · 任意 `*.xlsx`           （测试报表，绝不能进交付包）

为什么需要它
------------
v4.1.1 交付 zip 被查出带 `GalvoCalibVision/logs/app_20261106.log` 与
`GalvoCalibVision/_internal/logs/crash_20261106_163611.txt`。根因：门2 冒烟用 taskkill 结束进程，
每次都会在 `_internal/logs/` 留下一个 0 字节 crash 标记；打包时直接压缩整个 `_dist`，就把脏东西带进去了。
因此 v4.2 起固定流程：门2 → **pack_clean.py** → 打 zip → 门3（门3 会再断言一次洁净度）。

用法
----
    python tools/pack_clean.py                     # 清理默认 _dist
    python tools/pack_clean.py --dry-run           # 只报告，不删
    python tools/pack_clean.py --dist <onedir路径>  # 指定其它构建目录
    python tools/pack_clean.py --quiet

退出码：0 = 清理后确认干净；1 = 仍有残留或删除失败；3 = 目标目录不像 onedir（拒绝执行）
"""
import argparse
import fnmatch
import os
import shutil
import sys

DEFAULT_DIST = r"E:\GalvoCalibVision_项目交接_20261025\10_v3.5_UI重构\_dist\GalvoCalibVision"
# 需要删除的目录（相对 DIST）
TARGET_DIRS = ["logs", "temp", os.path.join("_internal", "logs"), os.path.join("_internal", "temp")]
# 需要删除的文件通配
TARGET_GLOBS = ["*.npz", "*.xlsx"]


def _walk_files(root):
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            yield os.path.join(dp, fn)


def _scan(dist):
    """返回 (命中目录列表, 命中文件列表)。"""
    dirs, files = [], []
    for rel in TARGET_DIRS:
        p = os.path.join(dist, rel)
        if os.path.isdir(p):
            dirs.append(p)
    for p in _walk_files(dist):
        name = os.path.basename(p).lower()
        for pat in TARGET_GLOBS:
            if fnmatch.fnmatch(name, pat):
                files.append(p)
                break
    return dirs, files


def _count(dist):
    return sum(len(fns) for _dp, _dn, fns in os.walk(dist))


def main():
    ap = argparse.ArgumentParser(description="打包前清理 _dist/GalvoCalibVision 的日志/临时文件/校准数据")
    ap.add_argument("--dist", default=os.environ.get("GCV_DIST", DEFAULT_DIST),
                    help="onedir 构建目录（默认 %(default)s）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不删除")
    ap.add_argument("--quiet", action="store_true", help="只输出最终结论")
    args = ap.parse_args()

    dist = os.path.abspath(args.dist)
    if not os.path.isdir(dist):
        print("DIST not found:", dist)
        return 3
    if not os.path.isfile(os.path.join(dist, "GalvoCalibVision.exe")):
        print("拒绝清理：%s 下没有 GalvoCalibVision.exe，不像 PyInstaller onedir 产物。" % dist)
        return 3

    before = _count(dist)
    dirs, files = _scan(dist)

    if not args.quiet:
        print("[pack_clean] dist =", dist)
        print("[pack_clean] 清理前文件数 = %d" % before)
        print("[pack_clean] 命中目录 %d 个：" % len(dirs))
        for d in dirs:
            print("    DIR  %s" % os.path.relpath(d, dist))
        print("[pack_clean] 命中文件 %d 个：" % len(files))
        for p in files:
            print("    FILE %s" % os.path.relpath(p, dist))

    removed_files, failed = 0, []
    do = not args.dry_run

    for d in dirs:
        rel = os.path.relpath(d, dist)
        n_in_dir = sum(len(fns) for _dp, _dn, fns in os.walk(d))
        if not do:
            removed_files += n_in_dir
            continue
        try:
            shutil.rmtree(d)
            removed_files += n_in_dir
            print("    REMOVED DIR  %s (%d files)" % (rel, n_in_dir)) if not args.quiet else None
        except Exception as e:
            failed.append((rel, repr(e)))

    for p in files:
        rel = os.path.relpath(p, dist)
        if not do:
            removed_files += 1
            continue
        try:
            os.remove(p)
            removed_files += 1
            print("    REMOVED FILE %s" % rel) if not args.quiet else None
        except Exception as e:
            failed.append((rel, repr(e)))

    after = _count(dist) if do else before
    rd, rf = _scan(dist) if do else (dirs, files)

    if not args.quiet:
        print("[pack_clean] %s：目录 %d 个、文件 %d 个" % (
            "预计删除" if args.dry_run else "已删除", len(dirs), removed_files))
        print("[pack_clean] 清理后文件数 = %d（减少 %d）" % (after, before - after))
        print("[pack_clean] 残留检查：目录 %d 个、文件 %d 个" % (len(rd), len(rf)))
        for p in rd:
            print("    LEFT DIR  %s" % os.path.relpath(p, dist))
        for p in rf:
            print("    LEFT FILE %s" % os.path.relpath(p, dist))

    if failed:
        print("[pack_clean] 删除失败 %d 项：" % len(failed))
        for rel, err in failed:
            print("    FAIL %s -> %s" % (rel, err))

    if args.dry_run:
        dirty = bool(dirs or files)
        print("[pack_clean] RESULT =", "DIRTY（dry-run，未删除）" if dirty else "CLEAN")
        return 0

    ok = (not rd) and (not rf) and (not failed)
    print("[pack_clean] RESULT =", "CLEAN" if ok else "FAIL")
    if ok:
        print("[pack_clean] 下一步：打 zip → python final_check_v42.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
