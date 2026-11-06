# v4.1 建系/数值口径独立复核（core 口径，只读）

对象：`12_开源_repo/galvo-calib-vision` 提交 `10d3a3c`（基线 `723d193`），复核 `gui/main_window.py`、`gui/image_view.py` 的数值口径及 `core/grid_definition.py`、`core/coordinate_frame.py` 的对应实现。仅静态阅读 + 一次无副作用的纯函数数值验证（只 import core，不写文件），未改任何代码。行号为当前 HEAD 真实行号。GUI 交互类问题已由 `_review_v41_gui.md` 覆盖，本文只在与数值口径相关处交叉引用，不重复计数。

## 结论（5 行）

1. 四条声明中 **1) 名义=设计 pitch、3) overlay=设计 pitch、4) 偏差链** 均自洽成立；**2) 检测窗=实测 pitch** 代码成立，但用户文案/注释写成"按设计间距铺设"（`i18n.py:567`、`main_window.py:2520-2521`、`:1737`），是**文案级误导，不是计算串道**。
2. 两套间距**不互相污染**：名义/overlay 只读 `pitch_mm`（`core/grid_definition.py:142-168`、`main_window.py:1748`），检测窗只读 `grid_pitch_mm`（`core:121-140`、`main_window.py:2605-2606`）；实测 pitch=25/measured=25.134 时 nominal 首点仍 (-50,-50)、检测 `pitch_px`=4749.73（设计 4724.41，+0.536%）。仅两条窄通道把 measured 带进报表（`<3` 点归零回退、rematch 阈值），见 CORE-06。
3. `_center_measured_mm` **确实等价"整板平移被吸收"**，与"名义以几何中心为原点"语义一致（`core:158-168` 对 `(n-1)/2` 中心），但验收 RMS 因此**不含整板平移**，绝对中心只出现在 Excel 参数页（`main_window.py:4112-4116`），需在报表/验收口径声明；`<3` 点回退 `_center_theo_mm` 还有偶数阵列半格陷阱（CORE-05）。
4. `_grid_correction_suggestion.pitch_dev_pct` 以 `grid.pitch_px`（实测优先）为基（`:2826,2863-2865`）→ 自动建系后恒≈0，对"设计 pitch 与真实间距失配"不敏感；且该 dict 只赋值不消费（仅 `:2882`）、提示文案与 Excel 的"间距偏差(%)"（相对设计，`:4110-4111`）同名不同基 → 误导。
5. 看门狗 30s 与 `original_gray()` 解码无心跳配合（解码发生在首个 progress 之前，`DetectorThread:209-222`）→ 2.23Gpx 解码 >30s 时首次检测仍必超时且**重试死循环**；预热后固定 8s 对 1..99 阵列（`:928-929`）必误杀。这是本次 P0；另 7 条 P1 见问题表。

## 四条口径核对

| 声明 | 代码证据 | 结论 |
|---|---|---|
| 1) 名义值=设计 pitch | `core/grid_definition.py:142-168`（只读 `self.pitch_mm`，`:165` 注释禁反推）；`main_window.py:3716-3737`、`:4138-4144` | ✅ 成立。实测 `pitch_mm=25 / measured=25.134` → nominal 首点 (-50,-50)、中心 (0,0) 不变；加载模板时优先 D 列固定值（同为"固定设计值"，非反推） |
| 2) 检测窗=实测 pitch | `core:121-140`（`grid_pitch_mm` 实测优先→`pitch_px`）；`main_window.py:2605-2606`；`core/coordinate_frame.py:170-176,253-261` | ✅ 代码成立（4749.73 vs 设计 4724.41）；❌ 文案相反（CORE-09） |
| 3) overlay=设计 pitch | `main_window.py:1746-1757`：`dp=float(g.pitch_mm)*dpi/25.4` 独立重算，不走 `points_px()` | ✅ 成立；但 `grid_pts` 形参未用（`:1734,1746`），手动建系/工程加载路径不触发绘制（GUI 报告 V41-07） |
| 4) 偏差链 | `main_window.py:3518-3531`（`_to_mm`→−center→`to_phys`→−nominal）；导出同链 `:4075-4095` | ✅ 成立；center 为实测拟合（`:3670-3714`），语义见结论 3 |

## 三条建系路径的 GridDefinition 字段核对（红线 1 关键）

| 路径 | pitch_mm | measured_pitch_mm | orthogonalize | origin_px 语义 |
|---|---|---|---|---|
| 手动三点 `main_window.py:2285-2289` | `sp_pitch` | `None`（`:2292-2293`） | 由 `_apply_ortho` 置为开关值（`:2222`） | 点选第 0 点 |
| 按参数 `:2357-2363` | `sp_pitch` | `None`（`:2364-2365`） | 同上 | 点选第 0 点或图像中心 |
| 自动建系 `core/coordinate_frame.py:170-176,253-261` + `main_window.py:2489-2518` | `nominal_pitch`(=sp_pitch) | 拟合 `mp` | **默认 False**（不继承手动网格） | 拟合角点 |
| 工程加载 `core:228-235` + `main_window.py:4208-4224` | 存档值 | 存档值 | 存档值→构造时**再次**正交化 | 存档值 |

**结论：未发现任何路径把 `measured_pitch_mm` 写回 `pitch_mm`（红线 1 无违规）**；但 `orthogonalize`/`measured` 在四路径间不一致，且工程加载会二次正交化（CORE-04 / CORE-11）。

## 问题表（12 条）

| 编号 | 级别 | 文件:行号 | 现象 | 根因 | 建议修法 |
|---|---|---|---|---|---|
| CORE-01 | P0 | main_window.py:2628-2635、209-222、2655-2667 | 30s 定时器在解码阶段无心跳：2.23Gpx 解码 >30s 时首次检测必超时、结果被丢弃，且失败后 `_gray_warm` 仍 False → 每次重试同样 30s，形成死循环；预热后固定 8s，对 9×9 以上（界面允许 1..99，`:928-929`）必误杀有效检测 | 定时器为一次性 wall-clock，不随 `progress` 重置；超时分支只断信号不置位/不终止 | 改为"最后进度时间戳"驱动（progress 每次 `restart(15s)`）；解码前/后各发一次心跳；按点数×分辨率给动态预算；超时须 wait/kill 并提示"解码中" |
| CORE-02 | P1 | main_window.py:2826、2863-2872、2882-2900、4107-4111；i18n.py:568 | 自动校正建议的 `pitch_dev_pct` 以 `pitch_old=grid.pitch_px`（实测优先）为基 → 自动建系后恒≈0，对"设计 pitch 填错/样件真实间距≠设计"不报警；`design_dev_pct`（`:2885`）无人消费；提示把相对实测的 pct 称"偏差"，与 Excel 相对设计的"间距偏差(%)"同名不同基 | 基取错；建议对象只写不读；文案未注明基 | 建议统一以设计 pitch 为基（design_dev_pct 作主字段），文案注明"相对设计/相对当前网格"，并补消费入口（一次性弹窗/报表页） |
| CORE-03 | P1 | main_window.py:2340-2357 | 无点选"按参数铺网格"把图像中心当 `origin_px`（网格第 0 行 0 列**角点**），阵列中心被推到图像中心 +(n−1)/2·pitch → 半幅偏移，检测窗全偏；文档却称该快速模式可用（v4.1 报告 §5.1） | `origin` 语义是角点，误用中心点 | 以阵列几何中心反推 origin：`o=(cx−(rows−1)/2·pitch_px, cy−(cols−1)/2·pitch_px)`；若有 `origin_xy` 中心精测值优先 |
| CORE-04 | P1 | main_window.py:2222、2227-2235；core/grid_definition.py:34、53-55、228-235 | `_apply_ortho` 先置 `orthogonalize=True` 再判 ±1° 护栏；被**明确拒绝**的网格仍带 True 存盘，重载 `from_dict` 无护栏执行 `orthonormalize` → 被拒绝的正交化在保存/加载后静默生效（红线 5 边界） | 标志与动作顺序颠倒；护栏只在 GUI，core 侧缺失 | 拒绝分支置 `g.orthogonalize=False`；或在 `from_dict` 增加同一阈值校验并保留"未正交化"标记 |
| CORE-05 | P1 | main_window.py:3664-3665 vs 3707-3710 | `<3 成功点` 回退 `_center_theo_mm` 用整数 `(r//2,c//2)` 取点，偶数行列偏半格（实测 4×4/25mm → 中心名义 (12.5,12.5)mm），而 `_center_measured_mm` 用 `(n−1)/2` → 两种归零口径不一致，fallback 时整表偏差≈半间距 | 中心索引未按连续几何中心计算 | `_center_theo_mm` 改用 `float((rows−1)/2)*pitch_px` 的仿射中心（与 nominal `c0/r0` 同口径），或对 `points_px()` 中心做双线性插值 |
| CORE-06 | P1 | main_window.py:2694-2695、3650-3651、3686-3690 | 报表偏差虽由设计 pitch 生成名义值，但仍有两处 measured 泄漏：`<3` 点时归零基准经 `_theory_mm_list→points_px()`（实测优先）；rematch 阈值用 `grid.pitch_px`（实测）决定点号归属。正常 25/25 不触发，异常点时偏差失真 | 同一 GridDefinition 同时携带两个 pitch 被复用 | 归零/rematch 显式使用设计 pitch；fallback 切换写入 log + 报表标记"基准=理论中心" |
| CORE-07 | P1 | main_window.py:2424-2431、1039-1045、2033-2045、2469 | 自动建系形状不一致选"按检测结果"：`setValue` 触发参数变更→`_reset_pick()` 清空 `_picked`/grid/结果 → 紧随其后的"手动优先"分支永不进入，手动基准被静默丢弃，origin/方向来源由 manual 变 auto | 确认弹窗内 setValue 未抑制失效回调 | 同 GUI 报告 V41-01：`_suppress_invalidate`（或 blockSignals）包住 setValue，并在分支判定前缓存 picked/manual_grid |
| CORE-08 | P1 | tools/selfcheck.py:6、78、108-109 | 开源仓库提交硬编码本盘绝对路径 `E:\GalvoCalibVision_项目交接_20261025\...\v3.3_正交化`；他人机器 `isdir` 失败→SKIP→仍打印 PASS，"core 未越权修改"校验失效，且泄漏内部交接目录结构 | 基线路径由 `os.environ.get("GCV_BASELINE_SRC")` 改为硬编码 | 恢复环境变量；缺失时 FAIL（或至少 SKIP 并醒目警告）；禁止提交本机绝对路径 |
| CORE-09 | P2 | main_window.py:1734-1746、2520-2521、1737；i18n.py:567 | 双轨间距的文案/注释与实现相反：i18n"网格仍按设计间距铺设"、注释"实测间距不参与建网格"、drawdoc"measured 已不参与建网格"，实际 `points_px()` 用 measured；`_draw_nominal_grid(grid_pts)` 形参未用 | 解耦后未同步文案；参数遗留 | 统一表述"名义/显示=设计，检测窗=实测"；删除未用形参，避免后人按注释误删 measured 字段 |
| CORE-10 | P2 | main_window.py:2625、2634、2667、1961；docs/v4.1 报告 §E | `_gray_warm` 与真实缓存不同步：stale 结果在代次校验**之前**置 True（旧图结果把新图标成已预热→新图首次检测只给 8s，正是本版要修的场景）；反方向自动建系 `original_gray()` 已解码却不置位（文档称"建系解码过算预热"不成立）→ 只多给 30s，不误杀但预期不符 | 置位点单一且在错误位置；未以 `src._orig_gray` 判据 | 以 `src._orig_gray is not None` 判定预热；stale 分支后置；文档与实现对齐 |
| CORE-11 | P2 | main_window.py:2285-2289、2357-2363、2489-2518、4208-4224；core/coordinate_frame.py:170-176、253-261 | 三路径 + 加载路径字段不一致（见上表）：auto 不继承 orthogonalize、manual/params 清空 measured；加载时 `from_dict` 二次正交化；工程同时存 `params.pitch` 与 `grid.pitch_mm`（`:4171-4188`）但不校验一致性 | 构造点分散，无统一工厂；加载信任 JSON | 收口为一个建网格 factory（pitch/measured/ortho/来源一次性传入）；加载时校验 `params.pitch==grid.pitch_mm` 并告警 |
| CORE-12 | P2 | main_window.py:3034、3057 vs 2605-2606 | 精化模型口径与检测窗不一致：`_on_refine_axis` 用**设计** pitch 生成名义像素网格 `dst`（`:3034,3057`），检测窗按**实测** pitch（`:2605-2606`）；+0.536% 时刚体拟合被尺度失配污染（5×5 半幅≈50px），θ/"拟合 rms"诊断失真（护栏 0.15×pitch≈700px 兜不住）；62 候选择优仍只旋转，不违红线 5 | 两套 pitch 并存后精化未对齐基 | 精化 `dst` 改用 `grid.pitch_px`（与检测窗同基），或显式注明用设计 pitch 并按设计 pitch 归一 rms |

## 红线结论（三条）

- **红线 1（名义值禁反推）：仍成立。** `nominal_points_mm()` 只读 `pitch_mm`（`core/grid_definition.py:142-168`），三条建系路径都以 `sp_pitch`/`nominal_pitch` 写 `pitch_mm`（`main_window.py:2285-2287,2357-2362`；`core/coordinate_frame.py:170-176,253-261`），未发现 measured→pitch_mm 写回；v4.1 仅新增"实测间距"留痕字段（Excel/工程），不参与名义值。残余风险是工程加载信任 JSON 且不校验 `params.pitch` 与 `grid.pitch_mm` 一致（CORE-11），属输入信任边界、非反推。
- **红线 5（建系仅旋转+平移）：仍成立。** v4.1 未改动 `_on_refine_axis`（仍为 SVD 刚体 3DOF + 62 候选纯旋转取舍）；但既有的 `orthogonalize`（`core:24-34,59-92`）本质是对点选平面基做 ≤1° 对称剪切收拢，虽默认关且带拒绝护栏，仍建议列入"方案 A 授权清单"做书面确认；且 CORE-04 会让被拒绝的收拢在保存/加载后生效，需先修。
- **红线 9（导出格式冻结）：成立（结构级）。** `core/excel_exporter.py` 本提交未改，Sheet1 7 列 / Sheet2 15 列结构不变；v4.1 只在 Sheet3"参数记录"以键值追加"阵列来源/实测阵列间距/间距偏差(%)"（`main_window.py:4097-4111`），属附加内容而非版式变更。若"冻结"口径包含参数页键集合，建议向老板报备后发布。
- 附注（红线 4）：v4.1 报告已如实标注真实图表格 RMS 0.2420mm > 0.15mm 为"真实超差（二次元独立 0.2569）"（`docs/v4.1_改动报告_20261106.md:58,84`），未把超差宣告为通过，口径正确；验收时仍需按红线 4 判失败，不得以"差异 +0.0008mm 属预期"代替达标结论。
