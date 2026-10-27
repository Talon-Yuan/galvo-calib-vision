# -*- coding: utf-8 -*-
"""一键自检：改动后先跑这个，不通过就不要打包。"""
import hashlib
import json, os, py_compile, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.environ.get("GCV_BASELINE_SRC", "")  # 可选：用于 core 基线字节对比
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
    print("SKIP baseline missing:", BASELINE)

print()
print("RESULT =", "PASS" if not fails else ("FAIL: " + "; ".join(fails)))
sys.exit(0 if not fails else 1)
