# -*- coding: utf-8 -*-
"""内置使用说明（HTML）。随程序一起打包，通过 帮助 -> 使用说明 或 F1 打开。

设计：不依赖外部 .html 文件（避免 PyInstaller datas 漏打包），
首次调用时写到临时目录再用系统浏览器打开。
"""
import os
import tempfile

try:
    from gui.manual_en import EN_HTML
except Exception:
    try:
        from manual_en import EN_HTML
    except Exception:
        EN_HTML = ""

MANUAL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>GalvoCalibVision 使用说明</title>
<style>
body{font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;max-width:900px;margin:32px auto;padding:0 20px;
     color:#1f2d3d;line-height:1.75;font-size:15px;background:#fbfcfe}
h1{font-size:26px;border-bottom:3px solid #3b82f6;padding-bottom:10px}
h2{font-size:19px;margin-top:34px;color:#1565c0}
h3{font-size:16px;margin-top:22px;color:#1f2d3d}
code{background:#eef2f7;padding:1px 6px;border-radius:4px;font-size:13px}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid #dde3ec;padding:8px 10px;text-align:left;font-size:14px}
th{background:#f0f4fa;color:#3d4d63}
.tip{background:#eff6ff;border-left:4px solid #3b82f6;padding:10px 14px;margin:14px 0;border-radius:6px}
.warn{background:#fffbeb;border-left:4px solid #f59e0b;padding:10px 14px;margin:14px 0;border-radius:6px}
.ok{background:#ecfdf5;border-left:4px solid #16a34a;padding:10px 14px;margin:14px 0;border-radius:6px}
</style></head><body>
<h1>GalvoCalibVision 使用说明</h1>
<p>本软件用视觉方式测量振镜打标阵列的真实坐标偏差。四步走：<b>标定棋盘格 → 导入相纸扫描图 → 定义阵列与建系 → 检测与导出</b>。</p>

<div class="warn"><b>顺序很重要</b>：必须先完成棋盘格标定，再识别相纸。顶部流程条会按真实状态自动点亮当前步骤。</div>

<h2>一、第 1 步：标定棋盘格（必需前置）</h2>
<ol>
<li>左栏切到 <b>标定棋盘格</b>，点「导入棋盘格图像」。</li>
<li>填写棋盘格参数（行列内角点、单格尺寸、扫描 DPI），点「检测角点并校验」。</li>
<li>看「校准状态」卡：<b>已通过</b> 且 RMS 满足要求后，点「保存标定参数」。</li>
</ol>
<div class="tip">校准 RMS 通常应 ≤ 0.05 mm。若明显偏大，先检查 DPI 与格间间隔是否填错。</div>

<h2>二、第 2 步：导入相纸扫描图</h2>
<p>文件 → 导入扫描图（<code>Ctrl+O</code>）。超大图会提示是否整图处理；整图处理精度更高但内存占用大。</p>

<h2>三、第 3 步：定义阵列与建系</h2>
<h3>3.1 阵列规格</h3>
<p>设置阵列规格（如 5×5）、间距（设计名义值）、扫描 DPI；需要以阵列中心为原点时勾选 P1。</p>
<div class="warn"><b>间距必须填设计值</b>（例如 30.000），不要填实测值。填实测值等于用结果反推基准，会导致判定失真。</div>
<h3>3.2 建系（向导）</h3>
<p>按提示依次点选三个点，<b>点完自动进入下一个</b>：</p>
<table><tr><th>顺序</th><th>点选对象</th><th>作用</th></tr>
<tr><td>①</td><td>起始点（原点十字）</td><td>定义网格原点</td></tr>
<tr><td>②</td><td>X 方向第二个十字</td><td>定义 X 轴方向</td></tr>
<tr><td>③</td><td>Y 方向第二个十字</td><td>定义 Y 轴方向</td></tr></table>
<p>点错了点「<b>删除上一个点</b>」，可连续点击一路退回到空；「全部重置」清空重来。</p>
<div class="tip">「建系正交化」默认开启：把手点得到的两个方向轴收拢为正交基（修正量写入报表参数记录）。它不改变名义值、间距与网格原点。</div>

<h2>四、第 4 步：检测与导出</h2>
<ol>
<li>左栏切到 <b>检测与导出</b>，选中心算法（默认投影质心），点「开始检测」。</li>
<li>结果表默认显示 8 列：序号/行/列/物理X/物理Y/偏差/判定/状态。工具 → 列设置 可增删列。</li>
<li>导出：文件 → 导出报表（<code>Ctrl+E</code>）；也可保存测量比对图、导出整图痕迹图。</li>
</ol>
<div class="ok"><b>验收口径</b>：合成 RMS ≤ 0.15 mm 且 Max ≤ 0.40 mm 为达标。若「精化是否应用」为否，请看下方说明。</div>

<h2>五、几个容易踩的坑</h2>
<h3>5.1 精化前 RMS 很大、精化后很小</h3>
<p>说明三点建系把方向点歪了（例如偏 0.7°），是「刚体精化」纠正回来的。验收时请先确认报表里「精化是否应用 = 是」，再看最终 RMS。</p>
<h3>5.2 网格自动校正已达上限</h3>
<p>表示样件实际间距与设计名义值偏差较大，自动校正连跑 3 轮后放弃。此时结果仍保留，但建议重新点选方向点或核对间距后再测。</p>
<h3>5.3 检测出现 DETECT_FAIL / 低置信</h3>
<p>该点识别失败或可信度不足，<b>不会</b>参与统计。可点「手动修正」人工补点。</p>

<h2>六、深色模式与主题</h2>
<p>工具 → 深色模式，可随时切换，选择会记住。用于夜间或强光环境减少眩光。</p>

<h2>七、出问题时怎么办（重要）</h2>
<table><tr><th>现象</th><th>处理</th></tr>
<tr><td>双击无反应 / 闪退</td><td>查看程序目录下 <code>logs\crash_*.txt</code>（崩溃堆栈）与 <code>logs\app_*.log</code>（运行日志）</td></tr>
<tr><td>导入图片失败</td><td>确认路径无异常字符；超大图可先勾选整图处理</td></tr>
<tr><td>检测结果全 NG</td><td>先确认是否已完成棋盘格标定、间距是否为设计值</td></tr>
<tr><td>报表数字可疑</td><td>检查「参数记录」：校准RMS、点选夹角、建系正交化、精化是否应用</td></tr></table>
<div class="tip">反馈问题时请附：<code>logs\app_*.log</code>、<code>crash_*.txt</code>（若有）、导出报表、以及操作步骤。</div>

<h2>八、快捷键</h2>
<table><tr><th>快捷键</th><th>功能</th></tr>
<tr><td>Ctrl+O</td><td>导入扫描图</td></tr>
<tr><td>Ctrl+E</td><td>导出 Excel 报表</td></tr>
<tr><td>Ctrl+S</td><td>保存工程</td></tr>
<tr><td>F1</td><td>打开本使用说明</td></tr></table>

<p style="margin-top:40px;color:#6b7a90;font-size:13px;border-top:1px solid #dde3ec;padding-top:12px">
GalvoCalibVision v3.5 · 本说明随程序内置，可通过 帮助 → 使用说明（F1）随时打开。</p>
</body></html>
"""


_BAR_CSS = """
<style>
.langbar{position:fixed;top:10px;right:18px;z-index:9}
.langbar button{border:1px solid #dde3ec;background:#fff;color:#3d4d63;border-radius:6px;
  padding:5px 12px;margin-left:6px;cursor:pointer;font-size:13px}
.langbar button.on{background:#3b82f6;border-color:#3b82f6;color:#fff}
</style>
"""

_BAR_HTML = """
<div class="langbar">
  <button id="btn-zh" class="on" onclick="setLang('zh')">\u4e2d\u6587</button>
  <button id="btn-en" onclick="setLang('en')">English</button>
</div>
<script>
function setLang(l){
  document.getElementById('zh').style.display = (l==='zh') ? 'block' : 'none';
  document.getElementById('en').style.display = (l==='en') ? 'block' : 'none';
  document.getElementById('btn-zh').className = (l==='zh') ? 'on' : '';
  document.getElementById('btn-en').className = (l==='en') ? 'on' : '';
}
</script>
"""


def _bilingual_html() -> str:
    """把中文说明与英文说明合成一份可页内切换的 HTML。"""
    html = MANUAL_HTML
    if "langbar" in html:
        return html
    try:
        html = html.replace("</head>", _BAR_CSS + "</head>", 1)
        html = html.replace("<body>", "<body>\n" + _BAR_HTML, 1)
        html = html.replace("<h1>GalvoCalibVision", '<div id="zh">\n<h1>GalvoCalibVision', 1)
        html = html.replace("</body>", "</div>\n" + (EN_HTML or "") + "\n</body>", 1)
    except Exception:
        pass
    return html


def write_manual(lang: str = "zh") -> str:
    """把内置说明写到临时文件并返回路径。失败返回空串。"""
    try:
        d = os.path.join(tempfile.gettempdir(), "GalvoCalibVision")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "manual.html")
        html = _bilingual_html()
        if str(lang).lower().startswith("en"):
            html = html.replace('<div id="en" style="display:none">', '<div id="en">')
            html = html.replace('<div id="zh">', '<div id="zh" style="display:none">')
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        return p
    except Exception:
        return ""
