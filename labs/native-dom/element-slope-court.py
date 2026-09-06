#!/usr/bin/env python3
"""The frozen court for the per-element slope guard.

Frozen from `element-slope-guard-design-0.0.1.md` §5, with the thresholds
approved unchanged. It exists because `element-scaling-audit-0.0.1.md` found
that every other guard this lab owns reads a fixture small enough that the
per-element term is invisible: the element-fact rounds spent +232.2 bytes per
element and every ceiling they were measured against passed honestly.

Two numbers are under test together, always: the **slope** in tracked bytes per
element, and the **intercept**, the tracked cost of a realm holding an empty
document. Together, because a refactor that moves cost from one into the other
must not be able to pass by quoting whichever moved down.

Deliberately NOT under test, and a pass here is not a pass on any of them:

  * M1 and M2, which bound fixed live owner bytes per child realm. They are
    correct for what they say, they are untouched, and neither number is
    derived from the other or may be traded against it.
  * RSS and physical footprint. This court reads tracked
    `script_realms.malloc_bytes` only. A change that lowers this number without
    lowering RSS is a saving *in this metric* and the court claims no more.
  * G1 and D6. The slope is a standing metric beside the marginal-target
    figure, not folded into either gate.
  * The `__attrs` direct-write and revision question, which is a separately
    scoped audit item.

The court owns its own measurement rather than importing a probe, so a frozen
criterion cannot drift when an exploratory script changes.

This is a **live guard**: its criteria are pinned to what the tree currently
costs, so its status belongs in a verification receipt naming the current
binary, and the historical `-element-scaling` receipts are never refreshed.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. Fixtures are generated into a
temporary directory; no network is opened and nothing is fetched.

Groups: determinism, model, slope, intercept, decomposition, falsification.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
ALLOCATOR_KNOBS = {"system": None, "arena": "MINICON_SURF_NATIVE_REALM_ARENA"}

# The per-realm limit the derived element ceiling is read against. It is a
# property of the host, restated here so the receipt is self-contained.
REALM_LIMIT_BYTES = 16 * 1024 * 1024

# Frozen from the design's §5, approved unchanged. Every one of these is
# deliberate policy headroom over a metric measured to be exactly
# deterministic: there is no measurement-noise component in any of them.
CAPS = {
    "system": {"slope": 1400.0, "intercept": 336000, "bare": 920.0, "attribute": 240.0},
    "arena": {"slope": 1350.0, "intercept": 326000, "bare": 900.0, "attribute": 215.0},
}
MIN_R2 = 0.9999
# The anti-vacuity ceiling. The court must FAIL at this number on a binary that
# passes CAPS, or a ceiling of 1,400 against a measured 1,329.6 would be a
# criterion that cannot fail. It is also the counterfactual: had this guard
# existed with this ceiling before round C, round C would have failed it.
FALSIFICATION_SLOPE_CEILING = 1150.0

# The sample plan, fixed in advance by the design's §3.
SCALE_COUNTS = (0, 500, 1000, 2000, 4000)
ATTR_COUNTS = (0, 1, 3, 6)
ATTR_ELEMENTS = 2000
RUNS_PER_ARM = 2

CRITERIA = {
    "S1": "slope <= the arm's ceiling, in tracked bytes per element",
    "S2": "intercept (measured empty-document tracked bytes) <= the arm's ceiling",
    "S3": "both slope and intercept are scored, from one run: neither alone is a partial pass, so a refactor cannot trade one for the other",
    "S4a": "bare element (no attributes) <= the arm's ceiling",
    "S4b": "each attribute <= the arm's ceiling",
    "S5": f"linearity: R^2 >= {MIN_R2} over the five slope points",
    "S6": "two independent runs per arm agree on every tracked figure, to the byte",
    "S7": f"falsification: the same scoring at a slope ceiling of {FALSIFICATION_SLOPE_CEILING} fails",
    "S8": "both allocator arms are measured and scored independently",
    "reported_not_scored": "the derived element ceiling, being a function of S1 and S2",
}


def write_fixtures(root):
    """Generated, not committed: there is no fixture to drift and none shared
    with another court that could be edited for an unrelated reason."""
    for n in SCALE_COUNTS:
        rows = "".join(f'<div id="d{i}" class="row" data-k="v{i}"><span>{i}</span></div>'
                       for i in range(n // 2))
        root.joinpath(f"scale-{n}.html").write_text(
            f"<!doctype html><html><head><title>scale {n}</title></head>"
            f"<body>{rows}</body></html>\n")
    for na in ATTR_COUNTS:
        body = "".join("<div" + "".join(f' a{k}="v{i}{k}"' for k in range(na)) + "></div>"
                       for i in range(ATTR_ELEMENTS))
        root.joinpath(f"attr-{na}.html").write_text(
            f"<!doctype html><html><head><title>attr {na}</title></head>"
            f"<body>{body}</body></html>\n")
    root.joinpath("attr-none.html").write_text(
        "<!doctype html><html><head><title>none</title></head><body></body></html>\n")


class Host:
    def __init__(self, binary, directory, fixture_root, allocator):
        environment = dict(os.environ)
        for knob in ALLOCATOR_KNOBS.values():
            if knob:
                environment.pop(knob, None)
        environment.pop(VISIBLE_ENV, None)
        if ALLOCATOR_KNOBS[allocator]:
            environment[ALLOCATOR_KNOBS[allocator]] = "1"
        command = [binary, "serve", "--stdio", "--fixture-root", str(fixture_root),
                   "--config-dir", str(Path(directory) / "config")]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=environment)
        self.counter = 0

    def call(self, operation, arguments):
        self.counter += 1
        request = {"protocol": "minicon-surf.control", "version": "0.0.1",
                   "request_id": f"req_slope_{self.counter}", "deadline_ms": 30000,
                   "operation": operation, "arguments": arguments}
        check_contract.validate_request(request)
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"host exited during {operation}")
        answer = json.loads(line)
        check_contract.validate_response(answer)
        if not answer["ok"]:
            raise RuntimeError(f"{operation} failed: {answer['error']['code']}")
        return answer["result"]

    def finish(self):
        self.process.stdin.close()
        return self.process.wait(timeout=30)


def tracked_for(binary, fixture_root, allocator, fixture):
    """One host and one target per point, a fresh process each time, so no
    allocator history carries from one point into the next."""
    with tempfile.TemporaryDirectory(prefix="minicon-surf-slope-") as directory:
        host = Host(binary, directory, fixture_root, allocator)
        try:
            profile = host.call("profile.create", {"persistence": "ephemeral"})["profile"]
            session = host.call("session.open", {"profile": profile})["session"]
            empty = host.call("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            host.call("target.open", {"session": session, "fixture": fixture})
            time.sleep(0.2)
            live = host.call("memory.report", {})["owners"]["script_realms"]["malloc_bytes"]
            if host.finish() != 0:
                raise RuntimeError("host exited with failure")
            return {"empty": empty, "live": live}
        finally:
            if host.process.poll() is None:
                host.process.kill()
                host.process.wait()


def one_run(binary, fixture_root, allocator):
    """Every tracked figure this court reads, in one dict, so S6 can compare
    two runs field by field rather than comparing only the summaries."""
    figures = {}
    for n in SCALE_COUNTS:
        figures[f"scale-{n}"] = tracked_for(binary, fixture_root, allocator, f"scale-{n}.html")
    for n in ATTR_COUNTS:
        figures[f"attr-{n}"] = tracked_for(binary, fixture_root, allocator, f"attr-{n}.html")
    figures["attr-none"] = tracked_for(binary, fixture_root, allocator, "attr-none.html")
    return figures


def summarize(figures):
    points = [(n, figures[f"scale-{n}"]["live"]) for n in SCALE_COUNTS]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    fitted_intercept = my - slope * mx
    residual = sum((y - (fitted_intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    total = sum((y - my) ** 2 for y in ys)
    r2 = 1 - residual / total if total else 0.0
    # The pinned intercept is the MEASURED empty-document figure, not the fitted
    # one: the fit extrapolates below the smallest document that can exist.
    intercept = figures["scale-0"]["live"]
    bare = (figures["attr-0"]["live"] - figures["attr-none"]["live"]) / ATTR_ELEMENTS
    attribute = (figures["attr-6"]["live"] - figures["attr-0"]["live"]) / (6 * ATTR_ELEMENTS)
    ceiling = int((REALM_LIMIT_BYTES - intercept) / slope) if slope > 0 else 0
    return {"slope": slope, "intercept": intercept, "fitted_intercept": fitted_intercept,
            "r2": r2, "bare_element": bare, "per_attribute": attribute,
            "derived_element_ceiling": ceiling}


def score(summaries, slope_ceilings):
    """The scoring, factored out so S7 can rerun it at a different slope
    ceiling on the same measurements rather than measuring again."""
    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    for arm in sorted(summaries):
        s = summaries[arm]
        caps = CAPS[arm]
        tag = f"{arm}: "
        expect(tag + f"S1 slope {s['slope']:.4f} <= {slope_ceilings[arm]}",
               s["slope"] <= slope_ceilings[arm],
               {"slope": s["slope"], "ceiling": slope_ceilings[arm]})
        expect(tag + f"S2 intercept {s['intercept']:,} <= {caps['intercept']:,}",
               s["intercept"] <= caps["intercept"],
               {"intercept": s["intercept"], "ceiling": caps["intercept"]})
        expect(tag + f"S4a bare element {s['bare_element']:.1f} <= {caps['bare']}",
               s["bare_element"] <= caps["bare"],
               {"bare_element": s["bare_element"], "ceiling": caps["bare"]})
        expect(tag + f"S4b per attribute {s['per_attribute']:.1f} <= {caps['attribute']}",
               s["per_attribute"] <= caps["attribute"],
               {"per_attribute": s["per_attribute"], "ceiling": caps["attribute"]})
        expect(tag + f"S5 linearity R^2 {s['r2']:.8f} >= {MIN_R2}",
               s["r2"] >= MIN_R2, {"r2": s["r2"], "minimum": MIN_R2})
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    runs, summaries, determinism = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="minicon-surf-slope-fixtures-") as fixtures:
        root = Path(fixtures)
        write_fixtures(root)
        for arm in ALLOCATOR_KNOBS:
            runs[arm] = [one_run(args.binary, root, arm) for _ in range(RUNS_PER_ARM)]
            first = runs[arm][0]
            disagreements = [
                {"figure": key, "values": [run[key] for run in runs[arm]]}
                for key in first
                if any(run[key] != first[key] for run in runs[arm][1:])
            ]
            determinism[arm] = disagreements
            summaries[arm] = summarize(first)

    checks = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    # S6 first: every threshold below assumes an exact metric, so if two runs
    # of one arm disagree by a byte the court says `nondeterministic` rather
    # than reporting a number that means nothing.
    for arm in sorted(determinism):
        expect(f"{arm}: S6 two runs agree on every tracked figure, to the byte",
               not determinism[arm],
               {"failure": "nondeterministic", "disagreements": determinism[arm]}
               if determinism[arm] else {"figures_compared": len(runs[arm][0])})

    expect("S8 both allocator arms were measured and are scored independently",
           set(summaries) == set(ALLOCATOR_KNOBS), {"arms": sorted(summaries)})

    scored = score(summaries, {arm: CAPS[arm]["slope"] for arm in CAPS})
    checks.extend(scored)

    # S3, the anti-trade criterion, is structural and is checked against the
    # scored set rather than against the numbers: a court that scored the slope
    # but not the intercept -- or scored them from different runs -- would let a
    # refactor move cost from one into the other and still pass. It fails if
    # either is missing for an arm, or if re-deriving both from that arm's
    # stored figures does not reproduce exactly the two values that were scored.
    for arm in sorted(summaries):
        has_slope = [c for c in scored if c["check"].startswith(f"{arm}: S1")]
        has_intercept = [c for c in scored if c["check"].startswith(f"{arm}: S2")]
        rederived = summarize(runs[arm][0])
        expect(f"{arm}: S3 slope and intercept are both scored, from one run",
               len(has_slope) == 1 and len(has_intercept) == 1
               and rederived["slope"] == summaries[arm]["slope"]
               and rederived["intercept"] == summaries[arm]["intercept"],
               {"slope_checks": len(has_slope), "intercept_checks": len(has_intercept),
                "rederived_slope": rederived["slope"],
                "rederived_intercept": rederived["intercept"]})

    # S7: the same scoring, at the falsification ceiling, on the same
    # measurements. It must fail, or the criteria above cannot fail either.
    falsified = score(summaries, {arm: FALSIFICATION_SLOPE_CEILING for arm in CAPS})
    for arm in sorted(summaries):
        arm_slope_checks = [c for c in falsified
                            if c["check"].startswith(f"{arm}: S1")]
        expect(f"{arm}: S7 the same court fails at a slope ceiling of "
               f"{FALSIFICATION_SLOPE_CEILING}",
               arm_slope_checks and not arm_slope_checks[0]["passed"],
               {"measured_slope": summaries[arm]["slope"],
                "falsification_ceiling": FALSIFICATION_SLOPE_CEILING})

    passed = sum(1 for check in checks if check["passed"])
    receipt = {
        "court": "native-dom: the per-element slope guard",
        "frozen_from": "labs/native-dom/element-slope-guard-design-0.0.1.md §5",
        "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "passed": passed == len(checks),
        "score": f"{passed}/{len(checks)}",
        "criteria": CRITERIA,
        "caps": CAPS,
        "minimum_r2": MIN_R2,
        "falsification_slope_ceiling": FALSIFICATION_SLOPE_CEILING,
        "sample_plan": {"scale_counts": list(SCALE_COUNTS), "attr_counts": list(ATTR_COUNTS),
                        "attr_elements": ATTR_ELEMENTS, "runs_per_arm": RUNS_PER_ARM,
                        "realm_limit_bytes": REALM_LIMIT_BYTES},
        "measured": summaries,
        "tracked_figures": runs,
        "checks": checks,
        "reported_not_scored": {
            arm: {"derived_element_ceiling": summaries[arm]["derived_element_ceiling"]}
            for arm in sorted(summaries)
        },
        "live_guard": (
            "This court's criteria are pinned to what the tree currently costs. Per the "
            "receipt convention in AGENTS.md this receipt records the guard's status on the "
            "binary named above; the historical -element-scaling receipts committed at "
            "382657e are never refreshed."
        ),
        "not_under_test": [
            "M1 and M2, which bound fixed live owner bytes per child realm; untouched, "
            "separate, and neither may be traded against the slope",
            "RSS and physical footprint: this court reads tracked script_realms.malloc_bytes only",
            "G1 and D6: the slope is a standing metric beside the marginal-target figure, "
            "not folded into either gate",
            "the __attrs direct-write and revision question, a separately scoped audit item",
        ],
        "tolerance": (
            "zero measurement-noise tolerance: the metric is exactly deterministic and S6 "
            "proves it per run, so every byte of headroom in the caps is deliberate policy"
        ),
        "headless": "no surface binary, no window, no AppKit; generated fixtures, no network",
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "score": receipt["score"],
                      "receipt": args.receipt}))
    return 0 if receipt["passed"] else 1


sys.exit(main())
