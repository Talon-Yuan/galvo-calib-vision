# v4.1 GUI 复核（只读）

对象：`12_开源_repo/galvo-calib-vision` 提交 `10d3a3c`（基线 `723d193`），重点 `gui/main_window.py`（+246 行）。工作树干净，HEAD=origin/main。仅静态阅读，未改任何文件；行号为当前 HEAD 真实行号。

## 结论

1. 5 处重点逐一核过：方向轴在 0/1/2/3 个 picked 下**都能构成有效基**（0 点=图像轴、1 点=原点+图像轴、2 点=y 取 x 的 90° 旋转、3 点=用户方向+≤1° 内对称正交化）；`_draw_nominal_grid` 用设计间距重算、与 `points_px()` 实测间距**有意解耦且无重复绘制/清场泄漏**；`_on_apply_next` 三分支无崩溃。
2. 但**宣称的"已点原点→用该点+参数"实际不可达**：参数变更与"阵列数量不一致"确认都会经 `_on_array_param_changed`→`_reset_pick()` 清掉 `_picked`；且无点选时用的是**图像中心**而非 `origin_xy`（精测中心），后者还被 `_reset_pick` 一并清空。
3. 看门狗与 `_gray_warm` 各有 1 个 P1：30s 定时与进度无关且超时文案仍写"8 秒"；`_gray_warm` 在代次校验前被 stale 结果置 True，反之自动建系解码后不置位。
4. 自动校正的改写段已成死代码、`_auto_correct_count` 恒 0；"只提示一次"无去重与处置入口。**未发现 P0**。

## 问题表

| 编号 | 级别 | 文件:行号 | 现象 | 根因 | 建议修法 |
|---|---|---|---|---|---|
| V41-01 | P1 | [main_window.py](../12_开源_repo/galvo-calib-vision/gui/main_window.py):2425-2426 | 自动建系弹窗选"按检测结果"后，先前手点三点与手动基准被清空，`手动优先`分支(2469)永不进入，阵列来源记为 auto | `setValue` 触发 1039/1043 连接的 `_on_array_param_changed`(2033)→2043 `_reset_pick()`→2147/2149 清 `_picked` 与 grid；而 2458 在 2469 之前 | `_resolve_array_shape` 内用 `_suppress_invalidate=True`（或 blockSignals）包住 setValue，只同步控件；`_reset_pick` 拆出"只清网格/结果"轻量版 |
| V41-02 | P1 | main_window.py:2341-2344 | 无点选时"按参数铺网格"用**图像中心**，未用本会话已精测的 `origin_xy`；且 `_reset_pick`(2153) 已把 origin_xy 清空，先自动建系再铺网格必丢中心 | `o = (src.origin_w/2, src.origin_h/2)`；`origin_xy` 仅在 2571（`_on_center_origin`）写入、2153 被 `_reset_pick` 清空 | 优先 `o = self.origin_xy`，其次才图像中心；`_reset_pick` 保留 origin_xy（它属于图像而非网格） |
| V41-03 | P1 | main_window.py:2632-2635 | 30s 看门狗与进度无关：>30s 但仍在推进的检测被判超时，2659-2661 断开 `done` 后结果丢弃、线程继续跑；超时文案仍写"8 秒" | 一次性 `QTimer.start(_guard_ms)` 不随 `DetectorThread.progress` 重置；`i18n.py:294` 文案固定 8 秒 | progress 回调 `restart(15000)`；文案参数化 `{s}`，与 8/30s 同步 |
| V41-04 | P1 | main_window.py:2667 | `_gray_warm` 与真实灰度缓存不同步：旧图 stale 结果会把新图标记为已预热（新图首次检测只给 8s，正是本版要修的场景）；反之自动建系已解码却不置位，白白等 30s | 2667 在 2669 代次校验**之前**无条件置 True；仅 1961(换图)/2667 赋值，建系线程解码后不回写 | 以 `src._orig_gray is not None` 判断预热，或在建系/灰度线程完成时置位；stale 分支放到 2669 之后 |
| V41-05 | P2 | [tools/selfcheck.py](../12_开源_repo/galvo-calib-vision/tools/selfcheck.py):6 | commit 的"selfcheck PASS"不可复现：core 基线校验在他人机器上被 SKIP，PASS 不代表 core 未改；绝对路径还泄漏内部目录 | BASELINE 由环境变量改为硬编码 `E:\GalvoCalibVision_项目交接_20261025\03_源码工程\GalvoCalibVision_src_v3.3_正交化`；78 `isdir` 失败→108-109 SKIP→112 PASS | 改回 `os.environ`；基线缺失时 FAIL，仓库内禁止本机绝对路径 |
| V41-06 | P2 | main_window.py:2901-2931 | 自动校正"改写网格+重测"段已不可达；`_auto_correct_count` 永不递增，2789 的 3 次上限、2636-2637"第 N 轮"、报表校正次数恒 0 | 2780 仅 2754 一个调用点且恒 `auto=True`，2878-2900 已 `return False`，旧代码未删 | 删除 2901-2931 与轮次分支，或改为用户手动触发的 `_apply_grid_suggestion()` |
| V41-07 | P2 | main_window.py:2280-2313 | "名义网格"默认开，但手动三点建系后不显示；工程加载后也消失，直到下次检测 | `_finish_pick` 与 `_apply_project_state`(4226-4231) 只画点标记，未调 `_draw_nominal_grid`（对照自动建系 2545-2546 有调） | 两条路径清场后补调 `_draw_nominal_grid()`（`_finish_pick` 亦可直接改用 `_redraw_marks`） |
| V41-08 | P2 | main_window.py:1716-1717 | 叠加总开关关掉后单独勾"名义网格"仍显示细线；反向 `_redraw_marks`(3397-3412) 不查 `chk_overlay`，任何重绘把标记整体复现 | `_toggle_nominal`(1721-1731) 不判断 `chk_overlay`；`_redraw_marks` 只通过 `_draw_nominal_grid` 判 `chk_nominal` | `on = on and chk_overlay.isChecked()`；`_redraw_marks` 末尾按 `chk_overlay` 统一应用一次显隐 |
| V41-09 | P2 | main_window.py:2520-2521 | 注释称"网格间距一律用设计 pitch…实测间距不参与建网格"，但 2496-2498/2514-2518 仍传 `measured_pitch_mm`，core `grid_pitch_mm/pitch_px`([grid_definition.py](../12_开源_repo/galvo-calib-vision/core/grid_definition.py):122-127) 实测优先→检测窗实际用实测 | 只更新了注释/文档（报告 §2A 承认），未在注释写清"对象同时携带两个 pitch" | 注释改为"名义/显示用 pitch_mm，检测窗用 measured_pitch_mm"，防后续误删字段 |
| V41-10 | P2 | main_window.py:2333-2363 | 方向轴无近共线校验：3 点夹角≈0°/180° 时 `_apply_ortho` 拒绝收拢、原样保留基；core `to_phys`([grid_definition.py](../12_开源_repo/galvo-calib-vision/core/grid_definition.py):211-213) 对平行基静默"退回原值"→ 表格有界但错误 | 建网格只做 `_normalize_dirs`（零向量兜底），不校验 `axis_gamma_deg` | 建系后统一校验 `axis_gamma_deg`（如 <80°/>100° 拒绝并提示重点），与 `ORTHO_MAX_DEV_DEG` 护栏同口径 |
| V41-11 | P2 | main_window.py:2878-2900 | "只提示一次"无去重、无处置入口：每次检测都重发状态栏 12s 提示；`_grid_correction_suggestion`(2882) 只赋值，界面无处"应用/忽略" | 只有状态栏+`_diag`，无 `_suggested_once` 标记、无消费 UI | 加一次性标记或首次弹窗"是否按实测间距重建网格"，并提供应用按钮 |
| V41-12 | P2 | [image_view.py](../12_开源_repo/galvo-calib-vision/gui/image_view.py):134-155 | `clear_overlay` 把 `_manual_loupe/_manual_loupe_border/_manual_roi` 直接置 None 而不 `removeItem`；手动模式悬停中触发 `_redraw_marks` 会留下孤儿放大镜（603 行创建），此后不受清屏/总开关控制 | G01 修复（避免悬空引用）时漏移除图元 | 置 None 前先 `removeItem`（参考 622-634 `_hide_manual_hover` 顺序：先 border 后 loupe） |

## 是否可交付

**有条件可交付**：功能可用、无 P0，但需先修 **V41-01 / V41-02**（手点基准与精测中心被静默丢弃，直接推翻"已点原点→用该点"的宣称路径）、**V41-03 / V41-04**（看门狗误杀与预热标记错位），并把 **V41-05**（selfcheck 不可复现）恢复为环境变量后再对外发布；否则建议以 beta 标签发布并注明限制。
