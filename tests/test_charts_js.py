"""Regression tests for the chart axis code in ``webapp/static/charts.js``.

A nearly constant series (e.g. a COP of 2.8999999999999995 vs 2.9 from
floating-point noise) once produced a tick step below the numbers'
precision, so ``v += step`` never advanced and the browser froze. These
tests run the axis functions in Node with such values. Skipped without Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CHARTS_JS = Path(__file__).resolve().parent.parent / "webapp" / "static" / "charts.js"
NODE = shutil.which("node")

SCRIPT = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");
eval(src.slice(src.indexOf("function niceStep"), src.indexOf("function tickFmt")));
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([lo, hi]) => {
  const d = niceDomain(lo, hi, 5);
  const t = ticks(d.lo, d.hi, d.step);
  return { lo: d.lo, hi: d.hi, n: t.length, first: t[0], last: t[t.length - 1] };
});
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize(
    "lo, hi",
    [
        (2.8999999999999995, 2.9000000000000004),  # COP at a fixed coil temperature
        (399.99999999999994, 400.0),  # power held at the limit
        (25.0, 25.000000000000004),
        (0.0, 0.0),
        (5.0, 5.0),
        (-1e-300, 1e-300),
        (1e15, 1e15 + 1),
        (0.0, 100.0),
        (-12.5, 37.0),
    ],
)
def test_axis_ticks_are_finite_and_cover_the_data(lo, hi):
    result = subprocess.run(
        [NODE, "-e", SCRIPT, str(CHARTS_JS), json.dumps([[lo, hi]])],
        capture_output=True, text=True, timeout=10, check=True,
    )
    (axis,) = json.loads(result.stdout)
    assert 2 <= axis["n"] <= 61  # bounded tick count, never an endless loop
    assert axis["lo"] <= lo and axis["hi"] >= hi  # the domain contains the data
    assert axis["first"] <= lo + 1e-9 * max(1.0, abs(lo))
