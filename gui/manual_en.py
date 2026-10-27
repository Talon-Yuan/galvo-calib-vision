# -*- coding: utf-8 -*-
"""English half of the built-in manual (kept separate to avoid huge single files)."""

EN_HTML = """
<div id="en" style="display:none">
<h1>GalvoCalibVision User Manual</h1>
<p>This tool measures the true coordinate deviation of a galvo-marked array by vision. Four steps:
<b>calibrate chessboard &rarr; import paper scan &rarr; define array &amp; frame &rarr; detect &amp; export</b>.</p>
<div class="warn"><b>Order matters</b>: finish chessboard calibration before working on the paper scan.
The step bar lights up the current step from real state.</div>

<h2>1. Step 1: Calibrate the chessboard (prerequisite)</h2>
<ol>
<li>Switch the left panel to <b>Chessboard calibration</b> and click "Import chessboard image".</li>
<li>Fill in the chessboard parameters (inner corners, square size, scan DPI) and click "Detect corners &amp; verify".</li>
<li>When <b>Passed</b> and RMS is acceptable, click "Save calibration".</li>
</ol>
<div class="tip">Calibration RMS should normally be &le; 0.05 mm. If much larger, re-check DPI and square size.</div>

<h2>2. Step 2: Import the paper scan</h2>
<p>File &rarr; Import scan (<code>Ctrl+O</code>). Very large images ask whether to process at full resolution;
full resolution is more accurate but uses much more memory.</p>

<h2>3. Step 3: Define the array and frame</h2>
<h3>3.1 Array specification</h3>
<p>Set the array spec (e.g. 5x5), the pitch (the nominal design value) and the scan DPI;
tick P1 if the array centre should be the origin.</p>
<div class="warn"><b>Always enter the design pitch</b> (e.g. 30.000), never a measured value.
Using a measured value means back-deriving the datum from the result and makes the judgement meaningless.</div>
<h3>3.2 Frame (wizard)</h3>
<p>Pick three points in order; the wizard <b>advances automatically</b>:</p>
<table><tr><th>#</th><th>Point</th><th>Purpose</th></tr>
<tr><td>1</td><td>Start point (origin cross)</td><td>Defines the grid origin</td></tr>
<tr><td>2</td><td>Second cross along X</td><td>Defines the X axis</td></tr>
<tr><td>3</td><td>Second cross along Y</td><td>Defines the Y axis</td></tr></table>
<p>Picked the wrong point? Click <b>Delete last point</b> (repeatable), or <b>Reset all</b>.</p>
<div class="tip">"Frame orthogonalisation" is on by default: the two hand-picked axes are snapped to an
orthonormal basis. It does not change nominal values, pitch or grid origin.</div>

<h2>4. Step 4: Detect and export</h2>
<ol>
<li>Switch the left panel to <b>Detect &amp; export</b>, choose the centre algorithm
(projection centroid by default) and click "Detect".</li>
<li>The result table shows 8 columns by default: index / row / col / X / Y / deviation / verdict / status.
Tools &rarr; Columns lets you add or remove columns.</li>
<li>Export: File &rarr; Export report (<code>Ctrl+E</code>); comparison and trace images can also be saved.</li>
</ol>
<div class="ok"><b>Acceptance</b>: RMS &le; 0.15 mm and Max &le; 0.40 mm.</div>

<h2>5. Common pitfalls</h2>
<h3>5.1 RMS is large before refinement and small after</h3>
<p>The three-point frame was off; "rigid refinement" corrected it. Before judging, confirm
"refine applied = yes" in the report.</p>
<h3>5.2 Grid auto-correction reached its limit</h3>
<p>The real pitch differs a lot from the design value. Auto-correction runs at most 3 rounds and then
gives up &mdash; that is why the log shows four detection runs. Re-pick the direction points or check the pitch.</p>
<h3>5.3 DETECT_FAIL / low confidence</h3>
<p>The point could not be detected or is not trustworthy; it is <b>excluded</b> from statistics.
Use "Manual identification" to add the point by hand.</p>

<h2>6. Dark mode and language</h2>
<p>Tools &rarr; Dark mode (the choice is remembered). The language switch is at the top right of the window;
this manual has its own switch at the top right as well.</p>

<h2>7. Troubleshooting</h2>
<table><tr><th>Symptom</th><th>What to do</th></tr>
<tr><td>Double-click does nothing / closes at once</td><td>Check <code>logs\\crash_*.txt</code> (crash stack) and <code>logs\\app_*.log</code></td></tr>
<tr><td>Image import fails</td><td>Check the path for unusual characters; try full-resolution processing</td></tr>
<tr><td>All results NG</td><td>Confirm calibration is done and the pitch is the design value</td></tr>
<tr><td>Suspicious numbers</td><td>Check the parameter record: calibration RMS, axis angle, orthogonalisation, refine applied</td></tr></table>
<div class="tip">When reporting an issue, attach <code>logs\\app_*.log</code>, <code>crash_*.txt</code> (if any),
the exported report and the steps you performed.</div>

<h2>8. Shortcuts</h2>
<table><tr><th>Key</th><th>Action</th></tr>
<tr><td>Ctrl+O</td><td>Import scan</td></tr>
<tr><td>Ctrl+E</td><td>Export Excel report</td></tr>
<tr><td>Ctrl+S</td><td>Save project</td></tr>
<tr><td>F1</td><td>Open this manual</td></tr></table>

<p>GalvoCalibVision v3.7 &middot; Built into the application; open it via Help &rarr; User Manual (F1).</p>
</div>
"""
