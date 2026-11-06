# -*- coding: utf-8 -*-
"""一键自检：改动后先跑这个，不通过就不要打包。

用法
----
    python tools/selfcheck.py                     # 默认：基线缺失时 SKIP（不计失败）
    python tools/selfcheck.py --require-baseline  # 严格：基线缺失即 FAIL（发布/打包前必用）

基线目录解析顺序
----------------
    环境变量 GCV_BASELINE_SRC  >  下面硬编码的 _DEFAULT_BASELINE

说明：`--require-baseline` 只影响"基线目录不存在"这一种情况的行为。
不带该参数时保持历史行为（打印 SKIP，RESULT 不受影响），以免打断日常自检；
带该参数时把"基线缺失"升级为 FAIL，防止外部机器/换盘后静默 SKIP 仍然 PASS。
"""
import hashlib
import json, os, py_compile, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_BASELINE = r"E:\GalvoCalibVision_项目交接_20261025\03_源码工程\GalvoCalibVision_src_v3.3_正交化"
REQUIRE_BASELINE = "--require-baseline" in sys.argv[1:]
_ENV_BASELINE = (os.environ.get("GCV_BASELINE_SRC") or "").strip()
BASELINE = _ENV_BASELINE or _DEFAULT_BASELINE
BASELINE_SOURCE = "env:GCV_BASELINE_SRC" if _ENV_BASELINE else "default(hardcoded)"
fails = []
def say(ok, msg):
    print(("PASS  " if ok else "FAIL  ") + msg)
    if not ok: fails.append(msg)

bad = []
n = 0
for dp, dn, fns in os.walk(ROOT):
    if "__pycache__" in dp: continue
    for fn in fns:
        if fn.endswith(".py"):
            n += 1
            p = os.path.join(dp, fn)
            try: py_compile.compile(p, doraise=True)
            except Exception as e: bad.append((os.path.relpath(p, ROOT), str(e)[:70]))
say(not bad, "compileall（%d 个 .py）" % n)
for b in bad[:5]: print("      ", b)

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ROOT)
try:
    from PyQt5.QtWidgets import QApplication
    from gui.main_window import MainWindow
    from gui.theme import QSS_FULL, QSS_FULL_DARK
    app = QApplication.instance() or QApplication([])
    w = MainWindow(); w.show(); app.processEvents()
    say(True, "offscreen startup MainWindow")
    say(w.tabs.count() == 3, "left panel 3 tabs")
    w.act_dark.setChecked(True); app.processEvents()
    d1 = QApplication.instance().styleSheet() == QSS_FULL_DARK
    w.act_dark.setChecked(False); app.processEvents()
    d2 = QApplication.instance().styleSheet() == QSS_FULL
    say(d1 and d2, "theme toggle light->dark->light (app level)")
except Exception as e:
    import traceback; traceback.print_exc()
    say(False, "startup/theme exception: %s" % e)

try:
    from gui.i18n import CATALOG, missing_en
    import re
    CJK = re.compile(r"[\u4e00-\u9fff]")
    call = re.compile(r'(?:_?tr)\(\s*"([^"\n]*)"')
    bad_cjk, unknown = [], []
    gd = os.path.join(ROOT, "gui")
    for fn in sorted(os.listdir(gd)):
        if not fn.endswith(".py") or fn == "i18n.py": continue
        for i, line in enumerate(open(os.path.join(gd, fn), encoding="utf-8"), 1):
            for m in call.finditer(line):
                v = m.group(1)
                if CJK.search(v): bad_cjk.append((fn, i, v))
                if v not in CATALOG: unknown.append((fn, i, v))
    PH = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^}]*)?\}")
    ph_bad = [k for k, (zh, en) in CATALOG.items() if set(PH.findall(zh)) != set(PH.findall(en))]
    say(not bad_cjk, "tr arg no CJK (%d)" % len(bad_cjk))
    say(not unknown, "all tr keys in CATALOG (miss %d)" % len(unknown))
    say(not ph_bad, "placeholders zh/en consistent (%d)" % len(ph_bad))
    print("       CATALOG=%d  missing_en=%d" % (len(CATALOG), len(missing_en())))
except Exception as e:
    say(False, "i18n check exception: %s" % e)

def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()
print("[baseline] require=%s source=%s exists=%s\n           path=%s"
      % ("true(--require-baseline)" if REQUIRE_BASELINE else "false",
         BASELINE_SOURCE, os.path.isdir(BASELINE), BASELINE))

if os.path.isdir(BASELINE):
    # core 校验三态：未改(byte-identical) / 授权守卫补丁(allowlist+哈希匹配) / 越权改动(FAIL)
    # 授权补丁必须满足：只加输入校验或异常兜底，合法输入数值不变（tools/core_ab.py 已验 A/B 一致）。
    allow = {}
    ap = os.path.join(ROOT, "tools", "core_guards_allow.json")
    if os.path.isfile(ap):
        try:
            allow = json.load(open(ap, encoding="utf-8")).get("files", {})
        except Exception:
            allow = {}
    diff, patched, total = [], [], 0
    for dp, dn, fns in os.walk(os.path.join(BASELINE, "core")):
        if "__pycache__" in dp: continue
        for fn in fns:
            if not fn.endswith(".py"): continue
            rel = os.path.relpath(os.path.join(dp, fn), BASELINE).replace("\\", "/")
            mine = os.path.join(ROOT, rel.replace("/", os.sep)); total += 1
            if not os.path.isfile(mine):
                diff.append(rel); continue
            if md5(mine) == md5(os.path.join(dp, fn)):
                continue
            ent = allow.get(rel)
            if ent and ent.get("sha256") == _sha256(mine):
                patched.append(rel)
            else:
                diff.append(rel)
    say(not diff, "core/ baseline check (%d files: unchanged %d, authorized guard-only %d, unauthorized %d)"
        % (total, total - len(patched) - len(diff), len(patched), len(diff)))
    for d in patched[:8]: print("      PATCH(OK)", d)
    for d in diff[:8]: print("      DIFF(UNAUTHORIZED)", d)
else:
    if REQUIRE_BASELINE:
        say(False, "baseline missing (--require-baseline 严格模式): " + BASELINE)
        print("       → 严格模式要求基线必须存在：请设 GCV_BASELINE_SRC 指向 v3.3 基线树，")
        print("         或去掉 --require-baseline 回到默认的 SKIP 行为。")
    else:
        print("SKIP baseline missing:", BASELINE)
        print("       （默认模式：基线缺失只 SKIP，不计入失败；发布/打包前请改用 --require-baseline）")

print()
print("RESULT =", "PASS" if not fails else ("FAIL: " + "; ".join(fails)))
sys.exit(0 if not fails else 1)
