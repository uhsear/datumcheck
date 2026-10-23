#!/usr/bin/env python
"""Exit non-zero when PROJ moves your data with a null-shift operation.

A pip-installed pyproj ships no datum grids. Ask it to move a layer from one
datum to another and PROJ quietly falls back: either to a "ballpark" offset, or
to a Helmert operation whose translation is zero. Nothing raises. The layer
lands a few feet to a few metres from where it belongs, and every area figure
computed afterwards is quietly wrong.

datumcheck asks PROJ the question the other way round. It transforms real
probe points, asks PROJ which operation it actually used for each one, and
refuses when that operation shifts nothing. With a control file of known-good
coordinates it also reports how far off the result is, in US survey feet and in
acres of a parcel that moved that far.

    python datumcheck.py --self-test
    python datumcheck.py --from EPSG:2237 --to EPSG:2883 --point 500000 1500000
    python datumcheck.py --from EPSG:2237 --to EPSG:2883 --control truth.csv
    python datumcheck.py --from EPSG:2237 --to EPSG:2883 --emit-control pts.csv

The check writes no file. --emit-control prints to standard output, so a host
that has the grids can hand the truth to a host that has not. Two things write
outside this tool's control: --self-test puts CSV fixtures in a temporary
directory that it removes, and PROJ itself creates cache.db in its user
directory when PROJ_NETWORK=ON.

Exit codes: 0 no problem found, 1 a null-shift operation was used, 2 the check
could not run, 3 a more accurate operation exists but its grids are missing,
64 usage error, 70 a fault in this tool.
"""

from __future__ import print_function

import argparse
import contextlib
import csv
import http.server
import importlib.util
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import types
import warnings

# =============================================================================
# CONFIGURATION. Deliberately not flags. Change here, not at the call site.
# =============================================================================

# Exact by definition: the US survey foot is 1200/3937 of a metre. EPSG records
# the same ratio as the unit conversion factor of every "ftUS" coordinate system.
METRES_PER_FTUS = 1200.0 / 3937.0

# Square feet in an acre. Areas here are in square US survey feet.
SQFT_PER_ACRE = 43560.0

# Parcel size, in acres, used to turn a positional miss into displaced acres.
DEFAULT_PARCEL_ACRES = 1.0

# Half-width in degrees of the box around a probe that PROJ is asked about when
# it lists the operations that need grids. Small, so that an operation which only
# covers another state or country is not reported as missing here.
PROBE_BOX_DEGREES = 0.01

# =============================================================================
# End of CONFIGURATION.
# =============================================================================

OK = "OK"
DEGRADED = "DEGRADED"
NULL_SHIFT = "NULL SHIFT"

EXIT_OK = 0
EXIT_NULL = 1
EXIT_ERROR = 2
EXIT_DEGRADED = 3
EXIT_USAGE = 64
EXIT_FAULT = 70

CONTROL_COLUMNS = ("sx", "sy", "tx", "ty")
POINT_COLUMNS = ("sx", "sy")

# Test-only switch of PROJ. PROJ labels it "for testing purposes only"; the
# self-test uses it to hide the grids of the machine it runs on.
ISOLATION_ENV = ("PROJ_USER_WRITABLE_DIRECTORY", "PROJ_NETWORK")


# ----------------------------------------------------------------- pure core

def metres_to_ftus(metres):
    """Convert metres to US survey feet."""
    return metres / METRES_PER_FTUS


def usable_parcel_acres(acres):
    """True for a positive size whose area in square feet is a finite number.

    nan, zero, a negative size and infinity fail. So does a size so large that
    its area overflows to infinity, which would print as nan acres.
    """
    return 0 < acres * SQFT_PER_ACRE < math.inf


def displaced_acres(parcel_acres, dx_ft, dy_ft):
    """Acres of a square parcel that fall outside its true position.

    The parcel is a square of parcel_acres acres. It moved by (dx_ft, dy_ft)
    US survey feet. The part that no longer overlaps the true square is the
    ground that is now claimed wrongly. A move of one side or more displaces
    the whole parcel.
    """
    if not usable_parcel_acres(parcel_acres):
        raise ValueError("the parcel size must be a positive number of acres")
    area = parcel_acres * SQFT_PER_ACRE
    side = math.sqrt(area)
    overlap = max(0.0, side - abs(dx_ft)) * max(0.0, side - abs(dy_ft))
    return (area - overlap) / SQFT_PER_ACRE


def miss_ft(dx_ft, dy_ft):
    """Length of an offset, in US survey feet."""
    return math.hypot(dx_ft, dy_ft)


def summarize_misses(misses, parcel_acres):
    """Worst and mean miss in feet, and the worst displaced acres."""
    lengths = [miss_ft(dx, dy) for dx, dy in misses]
    acres = [displaced_acres(parcel_acres, dx, dy) for dx, dy in misses]
    return {"rows": len(misses), "worst_ft": max(lengths),
            "mean_ft": sum(lengths) / len(lengths),
            "worst_acres": max(acres), "parcel_acres": parcel_acres}


def is_null_step(step, op_accuracy):
    """True when one step of an operation moves nothing.

    A step is a null shift when PROJ synthesised it as a "Ballpark" offset, or
    when it is a transformation whose every parameter is the number zero.
    An operation PROJ states to be exact (accuracy 0) is the same datum seen
    twice, and a zero there is correct, not a fallback. A grid step carries a
    file name, which is a string, so it is never zero.
    """
    if step.get("type") != "Transformation":
        return False
    if str(step.get("name", "")).startswith("Ballpark"):
        return True
    if op_accuracy == 0.0:
        return False
    params = step.get("parameters") or []
    if not params:
        return False
    for param in params:
        value = param.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if value != 0:
            return False
    return True


def is_null_op(steps, op_accuracy):
    """True when the operation has datum steps and all of them are null."""
    datum = [s for s in steps if s.get("type") == "Transformation"]
    if not datum:
        return False
    return all(is_null_step(s, op_accuracy) for s in datum)


def better_missing(used_accuracy, unavailable):
    """The unavailable operations that are more accurate than the one used.

    PROJ reports accuracy -1 when it does not know it. An unknown accuracy is
    never better than a known one, and any known accuracy beats an unknown one.
    """
    out = []
    for op in unavailable:
        acc = op.get("accuracy", -1)
        if acc < 0:
            continue
        if used_accuracy < 0 or acc < used_accuracy:
            out.append(op)
    return out


def judge(null, used_accuracy, unavailable):
    """The verdict for one probe."""
    if null:
        return NULL_SHIFT
    if better_missing(used_accuracy, unavailable):
        return DEGRADED
    return OK


def exit_code(verdicts):
    """Collapse the verdicts of all probes into one exit code."""
    if NULL_SHIFT in verdicts:
        return EXIT_NULL
    if DEGRADED in verdicts:
        return EXIT_DEGRADED
    return EXIT_OK


def finite_pair(a, b):
    """True when both numbers are finite."""
    return math.isfinite(a) and math.isfinite(b)


def check_point(x, y):
    """Return (x, y) when both are finite numbers."""
    if not finite_pair(x, y):
        raise ValueError("a point must be finite, got %r %r" % (x, y))
    return x, y


def parse_rows(text, columns):
    """Parse CSV text into a list of float tuples, one per row.

    The header must name every column in columns. A row that is short, or has a
    value that is not a finite number, names its line in the error.
    """
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    missing = [c for c in columns if c not in fields]
    if missing:
        raise ValueError("the header lacks column %s; it has %s" % (
            ", ".join(missing), ", ".join(fields) or "no columns"))
    rows = []
    for record in reader:
        line = reader.line_num
        values = []
        for col in columns:
            cell = record.get(col)
            try:
                value = float(cell)
            except (TypeError, ValueError):
                raise ValueError("line %d: %s is %r, not a number" % (
                    line, col, cell))
            if not math.isfinite(value):
                raise ValueError("line %d: %s is not finite" % (line, col))
            values.append(value)
        rows.append(tuple(values))
    if not rows:
        raise ValueError("the file has a header and no rows")
    return rows


def area_centre(bounds_a, bounds_b):
    """Centre of the overlap of two (west, south, east, north) boxes.

    A box whose west is greater than its east crosses the antimeridian. Its
    east is unwrapped by 360 degrees, and the second box is tried at three
    offsets. When two boxes overlap in two places, the wider overlap wins; the
    first one found wins a tie.
    """
    for bounds in (bounds_a, bounds_b):
        if bounds is None:
            raise ValueError("a CRS has no area of use; pass --point")
    east_a = bounds_a[2] + (360.0 if bounds_a[0] > bounds_a[2] else 0.0)
    east_b = bounds_b[2] + (360.0 if bounds_b[0] > bounds_b[2] else 0.0)
    south = max(bounds_a[1], bounds_b[1])
    north = min(bounds_a[3], bounds_b[3])
    best = None
    for shift in (0.0, 360.0, -360.0):
        west = max(bounds_a[0], bounds_b[0] + shift)
        east = min(east_a, east_b + shift)
        if west < east and south < north and (
                best is None or east - west > best[1] - best[0]):
            best = (west, east)
    if best is None:
        raise ValueError("the two CRS areas of use do not overlap; pass "
                         "--point")
    lon = (best[0] + best[1]) / 2.0
    return ((lon + 180.0) % 360.0) - 180.0, (south + north) / 2.0


def probe_box(lon, lat, half):
    """A (west, south, east, north) box of half-width half around a point,
    held inside the longitude and latitude limits of the globe."""
    return (max(-180.0, lon - half), max(-90.0, lat - half),
            min(180.0, lon + half), min(90.0, lat + half))


def collect_points(explicit, control, emit):
    """Every point to probe: the --point values, then the rows of the control
    file or the emit file. PROJ picks the operation from the position, so the
    rows that are scored or written are probed too, not only --point."""
    return (list(explicit) + [(r[0], r[1]) for r in control]
            + [(r[0], r[1]) for r in emit])


def report_header(src, dst):
    """The first report line. CRS text can hold any character; a console may
    not print it."""
    return "datumcheck: %s -> %s" % (ascii_text(src), ascii_text(dst))


def ascii_text(text):
    """PROJ names can hold any character; a console may not print them."""
    return str(text).encode("ascii", "replace").decode("ascii")


def accuracy_text(accuracy):
    """Accuracy in metres, or 'unknown' when PROJ does not state it."""
    if accuracy < 0:
        return "unknown"
    return "%g m" % accuracy


def group_probes(results):
    """One group per distinct operation, in first-seen order, with a count."""
    groups = []
    index = {}
    for res in results:
        key = res["desc"]
        if key not in index:
            index[key] = len(groups)
            groups.append(dict(res, count=0))
        groups[index[key]]["count"] += 1
    return groups


def probe_lines(group):
    """The report lines for one group of probes."""
    lines = ["%s  %d probe(s), first at %r, %r" % (
        group["verdict"], group["count"], group["x"], group["y"]),
        "  PROJ used  %s (declared accuracy %s)" % (
            ascii_text(group["desc"]), accuracy_text(group["accuracy"]))]
    better = better_missing(group["accuracy"], group["missing"])
    if not better:
        if group["verdict"] == NULL_SHIFT:
            lines.append("  no more accurate operation is known to PROJ here; "
                         "a grid cannot fix this pair")
        return lines
    seen = []
    for op in better:
        for grid in op["grids"]:
            name = grid["short_name"]
            if name in seen:
                continue
            seen.append(name)
            where = grid["url"] if grid["direct_download"] else \
                "not on the PROJ CDN"
            lines.append("  needs      %s  %s  (operation accuracy %s)" % (
                ascii_text(name), ascii_text(where),
                accuracy_text(op["accuracy"])))
            if grid["direct_download"]:
                lines.append("  install    projsync --file %s" %
                             ascii_text(name))
    return lines


def control_line(summary):
    """The one-line control summary."""
    return ("control: %d rows, worst miss %.2f ftUS, mean miss %.2f ftUS, "
            "worst displacement %.4f acres of a %g acre parcel" % (
                summary["rows"], summary["worst_ft"], summary["mean_ft"],
                summary["worst_acres"], summary["parcel_acres"]))


# ------------------------------------------------------------------------ I/O

def _import_pyproj():
    """Import pyproj only when a transformation is about to run."""
    try:
        import pyproj
    except ImportError:
        raise RuntimeError(
            "pyproj was not found. Install it with: python -m pip install "
            "pyproj. Only the pure functions run without it.")
    transformer = getattr(pyproj, "Transformer", None)
    if not hasattr(transformer, "get_last_used_operation"):
        raise RuntimeError(
            "pyproj %s cannot report the operation PROJ used. "
            "Transformer.get_last_used_operation needs pyproj 3.4 and PROJ "
            "9.1 or newer." % getattr(pyproj, "__version__", "unknown"))
    return pyproj


def _op_facts(op):
    """Steps, accuracy and name of a PROJ operation."""
    data = op.to_json_dict()
    return data.get("steps") or [data], op.accuracy, op.description


def _unavailable(pyproj, src, dst, to_lonlat, x, y):
    """Operations around a probe that PROJ knows but cannot use."""
    lon, lat = to_lonlat.transform(x, y)
    area = pyproj.transformer.AreaOfInterest(
        *probe_box(lon, lat, PROBE_BOX_DEGREES))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        group = pyproj.transformer.TransformerGroup(
            src, dst, always_xy=True, allow_ballpark=True,
            area_of_interest=area)
    return [{"name": op.name, "accuracy": op.accuracy,
             "grids": [{"short_name": g.short_name, "url": g.url,
                        "direct_download": g.direct_download}
                       for g in op.grids]}
            for op in group.unavailable_operations]


def probe_one(pyproj, ctx, x, y):
    """Transform one point and judge the operation PROJ used for it."""
    gx, gy = ctx["tf"].transform(x, y)
    if not finite_pair(gx, gy):
        raise ValueError("PROJ returned no coordinate for %r, %r. The point "
                         "is outside what the source CRS supports." % (x, y))
    try:
        op = ctx["tf"].get_last_used_operation()
    except pyproj.exceptions.ProjError:
        # One operation only: PROJ keeps no list of alternatives to report.
        op = ctx["tf"]
    steps, accuracy, desc = _op_facts(op)
    if desc not in ctx["missing"]:
        ctx["missing"][desc] = _unavailable(
            pyproj, ctx["src"], ctx["dst"], ctx["to_lonlat"], x, y)
    missing = ctx["missing"][desc]
    null = is_null_op(steps, accuracy)
    return {"x": x, "y": y, "desc": desc, "accuracy": accuracy,
            "missing": missing, "verdict": judge(null, accuracy, missing)}


def offset_ft(dst, expected, got):
    """The (dx, dy) between two target coordinates, in US survey feet."""
    if dst.is_geographic:
        geod = dst.get_geod()
        dx = geod.inv(expected[0], expected[1], got[0], expected[1])[2]
        dy = geod.inv(expected[0], expected[1], expected[0], got[1])[2]
        return metres_to_ftus(dx), metres_to_ftus(dy)
    factor = dst.axis_info[0].unit_conversion_factor
    return (metres_to_ftus((got[0] - expected[0]) * factor),
            metres_to_ftus((got[1] - expected[1]) * factor))


def default_probe(src, dst, to_src):
    """The centre of the overlap of both areas of use, in source units."""
    bounds = []
    for crs in (src, dst):
        area = crs.area_of_use
        bounds.append(None if area is None else
                      (area.west, area.south, area.east, area.north))
    lon, lat = area_centre(bounds[0], bounds[1])
    return to_src.transform(lon, lat)


def read_text(path):
    """Read a CSV file, tolerating the BOM Excel writes."""
    with io.open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def run_check(args, out):
    """Run the whole check. Returns the exit code; writes the report to out."""
    pyproj = _import_pyproj()
    try:
        src = pyproj.CRS.from_user_input(args.src)
        dst = pyproj.CRS.from_user_input(args.dst)
    except pyproj.exceptions.CRSError as exc:
        raise ValueError("cannot read a CRS: %s" % exc)
    tf = pyproj.Transformer.from_crs(src, dst, always_xy=True)
    ctx = {"src": src, "dst": dst, "tf": tf, "missing": {},
           "to_lonlat": pyproj.Transformer.from_crs(
               src, "EPSG:4326", always_xy=True)}

    control = parse_rows(read_text(args.control), CONTROL_COLUMNS) \
        if args.control else []
    emit = parse_rows(read_text(args.emit_control), POINT_COLUMNS) \
        if args.emit_control else []
    points = collect_points(
        [check_point(x, y) for x, y in args.point or []], control, emit)
    if not points:
        to_src = pyproj.Transformer.from_crs("EPSG:4326", src, always_xy=True)
        points = [default_probe(src, dst, to_src)]

    results = [probe_one(pyproj, ctx, x, y) for x, y in points]
    code = exit_code([r["verdict"] for r in results])
    report = [report_header(args.src, args.dst)]
    for group in group_probes(results):
        report.extend(probe_lines(group))

    if emit:
        if code != EXIT_OK:
            report.append("refused to emit control values: this host did not "
                          "use a full-accuracy operation")
            print("\n".join(report), file=sys.stderr)
            return code
        out.write("sx,sy,tx,ty\n")
        for sx, sy in emit:
            tx, ty = tf.transform(sx, sy)
            out.write("%.6f,%.6f,%.6f,%.6f\n" % (sx, sy, tx, ty))
        return EXIT_OK

    if control:
        misses = []
        for sx, sy, tx, ty in control:
            # Every control row was probed above, so its result is finite.
            misses.append(offset_ft(dst, (tx, ty), tf.transform(sx, sy)))
        report.append(control_line(
            summarize_misses(misses, args.parcel_acres)))
    report.append("RESULT: %s (exit %d)" % (
        {EXIT_OK: "no null shift found",
         EXIT_NULL: NULL_SHIFT, EXIT_DEGRADED: DEGRADED}[code], code))
    out.write("\n".join(report) + "\n")
    return code


# ----------------------------------------------------------------------- CLI

def _parse(argv):
    ap = argparse.ArgumentParser(
        prog="datumcheck.py",
        description="Exit non-zero when PROJ moves data with a null-shift "
                    "operation. Reports the error in US survey feet and acres.",
        epilog="The check writes no file. Needs pyproj.",
        allow_abbrev=False,
    )
    ap.add_argument("--from", dest="src", metavar="CRS",
                    help="source CRS, such as EPSG:2237")
    ap.add_argument("--to", dest="dst", metavar="CRS",
                    help="target CRS, such as EPSG:2883")
    ap.add_argument("--point", action="append", nargs=2, type=float,
                    metavar=("X", "Y"),
                    help="a probe point in source CRS units, two numbers "
                         "with a space between; repeatable. Default: the "
                         "centre of both areas of use")
    ap.add_argument("--control", metavar="CSV",
                    help="known-good coordinates, columns sx,sy,tx,ty")
    ap.add_argument("--emit-control", dest="emit_control", metavar="CSV",
                    help="read points (columns sx,sy) and print control "
                         "rows to standard output")
    ap.add_argument("--parcel-acres", dest="parcel_acres", type=float,
                    default=DEFAULT_PARCEL_ACRES, metavar="ACRES",
                    help="parcel size for the displaced-acres figure "
                         "(default %g)" % DEFAULT_PARCEL_ACRES)
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the assertions and exit")
    return ap.parse_args(argv)


def main(argv=None):
    """Run the CLI. A fault in this tool exits 70, because 1 means a null
    shift was found."""
    try:
        return _run(argv)
    except Exception as exc:
        print("error: unexpected %s: %s. This is a fault in the tool, not a "
              "finding about the data." % (type(exc).__name__, exc),
              file=sys.stderr)
        traceback.print_exc()
        return EXIT_FAULT


def _run(argv):
    try:
        args = _parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        # argparse exits 2 for a flag it refuses, and 2 here means the check
        # could not run. A mistyped flag is a usage error.
        return EXIT_USAGE if exc.code == 2 else exc.code
    if args.self_test:
        return self_test()
    if not args.src or not args.dst:
        print("error: --from and --to are required. Use --self-test to verify "
              "the tool without them.", file=sys.stderr)
        return EXIT_USAGE
    if args.control and args.emit_control:
        print("error: --control and --emit-control cannot be used together.",
              file=sys.stderr)
        return EXIT_USAGE
    if not usable_parcel_acres(args.parcel_acres):
        print("error: --parcel-acres must be a positive number.",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        return run_check(args, sys.stdout)
    except (RuntimeError, ValueError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_ERROR


# ------------------------------------------------------------------ self-test

def self_test():
    """Run the assertions with PROJ pointed at an empty grid directory, in a
    temp directory that is removed even when one of them raises."""
    tmp = tempfile.mkdtemp(prefix="datumcheck-selftest-")
    saved = dict((k, os.environ.get(k)) for k in ISOLATION_ENV)
    try:
        user_dir = os.path.join(tmp, "proj-user")
        os.mkdir(user_dir)
        os.environ["PROJ_USER_WRITABLE_DIRECTORY"] = user_dir
        os.environ["PROJ_NETWORK"] = "OFF"
        try:
            _import_pyproj()
        except RuntimeError as exc:
            print("self-test: the real PROJ checks need pyproj. %s" % exc)
            return EXIT_ERROR
        return _self_test(tmp, user_dir)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)


def _self_test(tmp, user_dir):
    """Assertions over the decision core, then over real PROJ with no grids.
    The only network is a stub server on 127.0.0.1, no credentials. The only
    files are the ones under tmp."""
    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label):
        try:
            fn()
        except ValueError:
            check(True, label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    def run_cli(argv):
        """main() with its output captured, so the self-test stays readable."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def write(name, text):
        path = os.path.join(tmp, name)
        with io.open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def step(name, params, kind="Transformation"):
        return {"type": kind, "name": name, "parameters": [
            {"name": "p%d" % i, "value": v} for i, v in enumerate(params)]}

    def listing():
        return sorted(os.listdir(tmp))

    print("datumcheck self-test: temporary files and loopback only, no "
          "credentials")
    print("-" * 68)

    # ---- units and areas
    check(abs(metres_to_ftus(1200.0 / 3937.0) - 1.0) < 1e-12,
          "1200/3937 of a metre is exactly one US survey foot")
    check(abs(metres_to_ftus(1.0) - 3.28083333) < 1e-7,
          "one metre is 3.2808333 US survey feet")
    check(displaced_acres(1.0, 0.0, 0.0) == 0.0,
          "a parcel that did not move displaces no acres")
    side = math.sqrt(43560.0)
    check(abs(displaced_acres(1.0, 30.0, 40.0) - 0.30784) < 1e-4,
          "a one acre parcel moved 30 ft east and 40 ft north displaces "
          "0.3078 acres")
    check(displaced_acres(1.0, -30.0, -40.0)
          == displaced_acres(1.0, 30.0, 40.0),
          "the direction of the move does not change the displaced acres")
    check(displaced_acres(1.0, side, 0.0) == 1.0
          and displaced_acres(1.0, 1e6, 1e6) == 1.0,
          "a move of one side or more displaces the whole parcel, never more "
          "than the parcel")
    check(abs(displaced_acres(4.0, 30.0, 40.0)
              - (174240.0 - (math.sqrt(174240.0) - 30.0)
                 * (math.sqrt(174240.0) - 40.0)) / 43560.0) < 1e-12,
          "a four acre parcel uses a square of 174240 square feet")
    raises(lambda: displaced_acres(0.0, 1.0, 1.0),
           "a parcel of zero acres is refused")
    raises(lambda: displaced_acres(float("nan"), 1.0, 1.0),
           "a parcel of nan acres is refused  <-- pinned defect")
    raises(lambda: displaced_acres(float("inf"), 1.0, 1.0),
           "a parcel of infinite acres is refused")
    raises(lambda: displaced_acres(1e305, 1.0, 1.0),
           "a parcel whose area overflows is refused, not printed as nan "
           "acres  <-- pinned defect")
    check(usable_parcel_acres(1.0) and usable_parcel_acres(1e-5)
          and not usable_parcel_acres(-1.0)
          and not usable_parcel_acres(1e305),
          "a parcel size is usable when it is positive and its area is "
          "finite")
    check(abs(displaced_acres(1e-5, 0.3, 0.0) * 43560.0 - 0.198) < 1e-9
          and abs(displaced_acres(1e-5, 0.0, 0.3) * 43560.0 - 0.198) < 1e-9,
          "a parcel smaller than a square foot displaces its true share")
    check(abs(miss_ft(30.0, 40.0) - 50.0) < 1e-12,
          "a 30 and 40 foot offset is a 50 foot miss")
    summary = summarize_misses([(30.0, 40.0), (0.0, 0.0)], 1.0)
    check(summary["rows"] == 2 and summary["worst_ft"] == 50.0
          and summary["mean_ft"] == 25.0
          and abs(summary["worst_acres"] - 0.30784) < 1e-4,
          "the summary holds the worst miss, the mean and the worst acres")
    check("worst miss 50.00 ftUS" in control_line(summary)
          and "0.3078 acres of a 1 acre parcel" in control_line(summary),
          "the control line states feet and acres")

    # ---- what counts as a null shift
    ballpark = step("Ballpark geographic offset from NAD83 to NAD83(2011)",
                    [0.0, 0.0])
    zero = step("NAD83 to NAD83(2011) (1)", [0, 0, 0])
    real = step("NAD83(HARN) to WGS 84 (3)", [-0.991, 1.9072, 0.5129])
    grid = {"type": "Transformation", "name": "NAD83 to NAD83(HARN) (7)",
            "parameters": [{"name": "file", "value": "us_noaa_FL.tif"}]}
    proj = step("SPCS83 Florida North zone", [29, -84.5], kind="Conversion")
    check(is_null_step(ballpark, -1.0),
          "a Ballpark step is a null shift")
    check(is_null_step(zero, 1.0),
          "a zero translation of declared accuracy 1 m is a null shift")
    check(not is_null_step(zero, 0.0),
          "a zero offset of accuracy 0 is the same datum and is not flagged"
          "  <-- pinned defect")
    check(not is_null_step(real, 3.0),
          "a translation with real values is not a null shift")
    check(not is_null_step(grid, 0.05),
          "a grid step carries a file name and is not a null shift")
    check(not is_null_step(proj, 1.0),
          "a map projection step is not a datum step")
    check(not is_null_step({"type": "Transformation", "name": "x"}, 1.0),
          "a transformation with no parameters is not called null")
    check(not is_null_step(step("x", [0, None]), 1.0)
          and not is_null_step(step("x", [0, True]), 1.0)
          and not is_null_step(step("x", [0, False]), 1.0),
          "a parameter that is not a number is never read as zero")
    check(not is_null_step(step("x", [0, 0, 0.001]), 1.0),
          "one non-zero parameter makes the step real")
    check(not is_null_step(step("x", [-1.0, -2.0, -3.0]), 1.0)
          and not is_null_step(step("x", [1.0, 2.0, 3.0]), 1.0),
          "translations that are all negative, or all positive, are real")
    check(is_null_step(zero, -1.0) and is_null_step(zero, 0.5),
          "a zero translation of unknown or sub-metre accuracy is still a "
          "null shift")
    check(is_null_op([proj, zero, proj], 1.0),
          "an operation whose only datum step is null is null")
    check(is_null_op([zero, ballpark], 1.0),
          "an operation whose datum steps are all null is null")
    check(not is_null_op([zero, real], 5.0),
          "a chain with one real step is not null")
    check(not is_null_op([proj, proj], 0.0),
          "an operation with no datum step shifts nothing and needs nothing")
    grids = [{"short_name": "us_noaa_FL.tif", "url": "https://cdn.example/x",
              "direct_download": True}]
    unavailable = [{"name": "a", "accuracy": 0.05, "grids": grids},
                   {"name": "b", "accuracy": -1.0, "grids": []},
                   {"name": "c", "accuracy": 9.0, "grids": []}]
    check([o["name"] for o in better_missing(5.0, unavailable)] == ["a"],
          "only a known, more accurate unavailable operation counts")
    check([o["name"] for o in better_missing(-1.0, unavailable)]
          == ["a", "c"],
          "against an unknown accuracy every known accuracy is better")
    check(better_missing(0.01, unavailable) == [],
          "an unavailable operation that is less accurate is ignored")
    check(better_missing(5.0, [{"name": "n", "grids": []}]) == [],
          "an unavailable operation with no stated accuracy is ignored")
    exact = [{"name": "e", "accuracy": 0.0, "grids": []}]
    check(better_missing(5.0, exact) == exact
          and better_missing(-1.0, exact) == exact
          and better_missing(0.0, exact) == [],
          "an unavailable operation of accuracy 0 beats any worse operation "
          "and never beats another exact one")
    check(judge(True, 1.0, []) == NULL_SHIFT
          and judge(False, 5.0, unavailable) == DEGRADED
          and judge(False, 0.05, unavailable) == OK
          and judge(False, 1.0, []) == OK,
          "the verdict is null shift, degraded or ok in that order")
    check(exit_code([OK, DEGRADED, NULL_SHIFT]) == 1
          and exit_code([OK, DEGRADED]) == 3 and exit_code([OK]) == 0,
          "one null shift among the probes decides the exit code")

    # ---- input parsing
    check(finite_pair(1.0, 2.0) and not finite_pair(1.0, float("inf"))
          and not finite_pair(float("-inf"), 2.0)
          and not finite_pair(float("nan"), 2.0)
          and not finite_pair(1.0, float("nan")),
          "a pair is finite only when both numbers are")
    check(check_point(-82.0, 29.2) == (-82.0, 29.2),
          "a finite point passes")
    raises(lambda: check_point(1.0, float("nan")),
           "a point with nan is refused  <-- pinned defect")
    raises(lambda: check_point(float("inf"), 2.0),
           "a point with inf is refused")
    check(parse_rows("sx,sy\n1,2\n3,4\n", POINT_COLUMNS)
          == [(1.0, 2.0), (3.0, 4.0)],
          "rows parse to float tuples")
    check(parse_rows("sx,sy,note\n1,2,x\n", POINT_COLUMNS) == [(1.0, 2.0)],
          "extra columns are ignored")
    raises(lambda: parse_rows("sx,ty\n1,2\n", POINT_COLUMNS),
           "a header without the column is refused")
    raises(lambda: parse_rows("sx,sy\n", POINT_COLUMNS),
           "a header with no rows is refused")
    try:
        parse_rows("", POINT_COLUMNS)
    except ValueError as exc:
        check("it has no columns" in str(exc),
              "an empty file says it has no columns")
    raises(lambda: parse_rows("", POINT_COLUMNS),
           "an empty file is refused")
    raises(lambda: parse_rows("sx,sy\n1,2\n3,abc\n", POINT_COLUMNS),
           "a word in a number column is refused")
    try:
        parse_rows("sx,sy\n1,2\n3,abc\n", POINT_COLUMNS)
    except ValueError as exc:
        check("line 3" in str(exc),
              "the error names the line the bad value is on")
    raises(lambda: parse_rows("sx,sy\n1,2\n3\n", POINT_COLUMNS),
           "a short row is refused, not read as zero  <-- pinned defect")
    raises(lambda: parse_rows("sx,sy\n1,nan\n", POINT_COLUMNS),
           "a nan coordinate is refused")
    check(area_centre((-90, 20, -70, 40), (-80, 30, -60, 50)) == (-75.0, 35.0),
          "the default probe is the centre of the overlap")
    raises(lambda: area_centre(None, (0, 0, 1, 1)),
           "a CRS with no area of use is refused")
    check(area_centre((170, 0, -170, 10), (175, 2, -175, 8)) == (-180.0, 5.0),
          "areas that cross the antimeridian overlap across it")
    check(area_centre((-130, 0, -70, 10), (170, 0, -100, 10))[0] == -115.0,
          "a box over the antimeridian meets a box beside it  "
          "<-- pinned defect")
    raises(lambda: area_centre((0, 0, 1, 1), (5, 5, 6, 6)),
           "two areas of use that do not overlap are refused")
    raises(lambda: area_centre((0, 0, 1, 1), (1, 0, 2, 1)),
           "two areas of use that only touch along a meridian are refused")
    raises(lambda: area_centre((0, 0, 1, 1), (0, 1, 1, 2)),
           "two areas of use that only touch along a parallel are refused")
    wide = area_centre((167.65, 14.92, -47.74, 86.46), (-180, -90, 180, 90))
    check(abs(wide[0] + 113.87) < 0.01 and abs(wide[1] - 50.69) < 0.01,
          "a continent box over the antimeridian is centred in its wider "
          "overlap with the globe, not in the Bering Sea sliver  "
          "<-- pinned defect")
    check(area_centre((170, 0, -170, 10), (-180, 0, 180, 10))[0] == 175.0,
          "when two overlaps are as wide as each other the first one wins")
    check(area_centre((150, 0, -170, 10), (-180, 0, 180, 10))[0] == 165.0,
          "the first overlap is kept when a later one is narrower")
    check(area_centre((20, 0, -10, 50), (-180, -90, 180, 90))
          == (-95.0, 25.0)
          and area_centre((-180, -90, 180, 90), (20, 0, -10, 50))
          == (-95.0, 25.0),
          "a box with west east of its east crosses the antimeridian, "
          "whatever its north is, in either argument")
    here = types.SimpleNamespace(area_of_use=types.SimpleNamespace(
        west=-90.0, south=20.0, east=-70.0, north=40.0))
    there = types.SimpleNamespace(area_of_use=types.SimpleNamespace(
        west=-80.0, south=30.0, east=-60.0, north=50.0))
    nowhere = types.SimpleNamespace(area_of_use=None)
    to_src = types.SimpleNamespace(transform=lambda lon, lat: (lon * 2, lat * 3))
    check(default_probe(here, there, to_src) == (-150.0, 105.0),
          "the default probe is the overlap of the source and the target "
          "areas of use, converted to source units")
    raises(lambda: default_probe(here, nowhere, to_src),
           "a CRS without an area of use cannot give a default probe")
    check(probe_box(10.0, 20.0, 0.5) == (9.5, 19.5, 10.5, 20.5),
          "a probe box is the point plus and minus the half width")
    check(probe_box(-179.75, -89.75, 0.5) == (-180.0, -90.0, -179.25, -89.25)
          and probe_box(179.75, 89.75, 0.5) == (179.25, 89.25, 180.0, 90.0),
          "a probe box never leaves the globe")
    edge = probe_box(0.0, 0.0, PROBE_BOX_DEGREES)
    check(0.0 < edge[2] - edge[0] < 0.1 and 0.0 < edge[3] - edge[1] < 0.1,
          "the probe box is under a tenth of a degree wide, so another "
          "region's grids are not asked about")
    check(collect_points([(1.0, 2.0)], [(3.0, 4.0, 5.0, 6.0)], [])
          == [(1.0, 2.0), (3.0, 4.0)]
          and collect_points([], [], [(7.0, 8.0)]) == [(7.0, 8.0)]
          and collect_points([], [], []) == [],
          "the control and emit rows are probed as well as the --point "
          "values  <-- pinned defect")
    check(report_header("a\u03b1", "EPSG:4326")
          == "datumcheck: a? -> EPSG:4326",
          "the report header prints ASCII whatever the CRS text holds  "
          "<-- pinned defect")
    check(ascii_text("R\xe9seau") == "R?seau",
          "a name with an accent prints as ASCII")
    check(accuracy_text(-1.0) == "unknown" and accuracy_text(0.05) == "0.05 m"
          and accuracy_text(0.0) == "0 m",
          "an unknown accuracy prints as unknown and an exact one as 0 m")

    # ---- the report
    one = {"x": 1.0, "y": 2.0, "desc": "op one", "accuracy": 5.0,
           "missing": unavailable, "verdict": DEGRADED}
    two = dict(one, x=3.0, verdict=DEGRADED)
    three = dict(one, desc="op two", missing=[], verdict=NULL_SHIFT,
                 accuracy=-1.0)
    groups = group_probes([one, two, three])
    check([g["count"] for g in groups] == [2, 1]
          and groups[0]["x"] == 1.0,
          "probes group by operation and keep the first point")
    four = dict(one, desc="op three")
    check([g["desc"] for g in group_probes([one, four, two])]
          == ["op one", "op three"],
          "probes with the same verdict but different operations form "
          "separate groups")
    text = "\n".join(probe_lines(groups[0]))
    check("needs      us_noaa_FL.tif  https://cdn.example/x" in text
          and "install    projsync --file us_noaa_FL.tif" in text
          and "(operation accuracy 0.05 m)" in text,
          "a degraded group names the grid, where to get it, and projsync")
    nogrid = "\n".join(probe_lines(groups[1]))
    check("a grid cannot fix this pair" in nogrid,
          "a null shift with nothing better says no grid can fix it")
    check("a grid cannot fix" not in "\n".join(probe_lines(
        dict(groups[0], missing=[], verdict=OK))),
        "a clean group prints no grid advice")
    off_cdn = [{"name": "a", "accuracy": 0.5, "grids": [
        {"short_name": "a.gsb", "url": "", "direct_download": False},
        {"short_name": "a.gsb", "url": "", "direct_download": False}]}]
    off_text = "\n".join(probe_lines(dict(groups[0], missing=off_cdn)))
    check("not on the PROJ CDN" in off_text and "projsync" not in off_text
          and off_text.count("a.gsb") == 1,
          "a grid off the CDN is listed once and gets no projsync line")

    # ---- the parser: nothing here writes, and no flag may be shortened
    code, out, err = run_cli(["--hel"])
    check(code == 64 and "unrecognized arguments: --hel" in err,
          "a prefix --hel of --help is refused  <-- pinned defect")
    code, out, err = run_cli(["--emit", "x.csv", "--from", "a", "--to", "b"])
    check(code == 64 and "unrecognized arguments" in err,
          "a prefix --emit of --emit-control is refused  <-- pinned defect")
    check(run_cli(["--cont", "x.csv", "--from", "a", "--to", "b"])[0] == 64,
          "a prefix --cont of --control is refused  <-- pinned defect")
    check(run_cli(["--po", "1", "2", "--from", "a", "--to", "b"])[0] == 64,
          "a prefix --po of --point is refused  <-- pinned defect")
    check(run_cli(["--par", "2", "--from", "a", "--to", "b"])[0] == 64,
          "a prefix --par of --parcel-acres is refused")
    check(run_cli(["--fr", "a", "--to", "b"])[0] == 64,
          "a prefix --fr of --from is refused")
    code, out, err = run_cli(["--from", "a", "--to", "b", "--apply"])
    check(code == 64 and "unrecognized arguments: --apply" in err,
          "there is no --apply, so none can be shortened to --ap")
    check(run_cli([])[0] == 64, "a run with no CRS is a usage error")
    check(run_cli(["--from", "EPSG:2237"])[0] == 64,
          "a run with one CRS is a usage error")
    check(run_cli(["--help"])[0] == 0, "--help still exits 0")
    base = ["--from", "EPSG:2237", "--to", "EPSG:2883"]
    check(run_cli(base + ["--parcel-acres", "0"])[0] == 64
          and run_cli(base + ["--parcel-acres", "nan"])[0] == 64
          and run_cli(base + ["--parcel-acres", "inf"])[0] == 64
          and run_cli(base + ["--parcel-acres", "1e308"])[0] == 64,
          "a parcel of zero, nan, infinite or overflowing acres is a usage "
          "error  <-- pinned defect")
    check(run_cli(base + ["--control", "a", "--emit-control", "b"])[0] == 64,
          "--control and --emit-control together are a usage error")
    check(run_cli(base + ["--point", "-82.0,29.2"])[0] == 64,
          "a west longitude written as one X,Y word is a usage error, "
          "not a silent zero  <-- pinned defect")

    # ---- pyproj missing or too old
    real_module = _import_pyproj()
    sys.modules["pyproj"] = None
    try:
        code, out, err = run_cli(base)
    finally:
        sys.modules["pyproj"] = real_module
    check(code == 2 and "pyproj was not found" in err,
          "a missing pyproj exits 2 and says how to install it")
    sys.modules["pyproj"] = None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            blocked = self_test()
    finally:
        sys.modules["pyproj"] = real_module
    check(blocked == 2 and "the real PROJ checks need pyproj" in buf.getvalue(),
          "--self-test without pyproj says so and exits 2, not 70  "
          "<-- pinned defect")
    old = types.ModuleType("pyproj")
    old.Transformer = type("Transformer", (), {})
    sys.modules["pyproj"] = old
    try:
        code, out, err = run_cli(base)
    finally:
        sys.modules["pyproj"] = real_module
    check(code == 2 and "needs pyproj 3.4" in err,
          "a pyproj without get_last_used_operation exits 2 and names the "
          "version it needs")

    # ---- real PROJ with the grids hidden
    pyproj = real_module
    same = os.path.normcase(os.path.realpath(pyproj.datadir.get_user_data_dir()))
    check(same == os.path.normcase(os.path.realpath(user_dir)),
          "PROJ looks for grids only in the empty temp directory")
    check(not [f for f in os.listdir(user_dir)
               if f.lower().endswith((".tif", ".gsb"))],
          "that directory holds no grid")
    nad83_2011 = ["--from", "EPSG:4269", "--to", "EPSG:6318",
                  "--point", "-82.0", "29.2"]
    code, out, err = run_cli(nad83_2011)
    check(code == 1 and "NULL SHIFT" in out and "RESULT: NULL SHIFT (exit 1)"
          in out,
          "NAD83 to NAD83(2011) without grids is a null shift and exits 1")
    check("PROJ used  " in out and "declared accuracy 1 m" in out,
          "the report names the operation PROJ used and its stated accuracy")
    check("needs      " in out and "install    projsync --file " in out,
          "the report names a grid and the projsync command that fetches it")
    fl = ["--from", "EPSG:2237", "--to", "EPSG:2883",
          "--point", "500000", "1500000"]
    code, out, err = run_cli(fl)
    check(code == 3 and "DEGRADED" in out and ".tif" in out,
          "Florida West to North without grids is degraded and exits 3")
    code, out, err = run_cli(["--from", "EPSG:6440", "--to", "EPSG:6440",
                              "--point", "600000", "1500000"])
    check(code == 0 and "no null shift found" in out and ".tif" not in out,
          "one datum to itself needs no shift and exits 0")
    code, out, err = run_cli(["--from", "EPSG:4326", "--to", "EPSG:4979",
                              "--point", "-82.0", "29.2"])
    check(code == 0,
          "a single-operation transformer is read through its own JSON and "
          "its Null geographic offset is not flagged  <-- pinned defect")
    code, out, err = run_cli(["--from", "EPSG:6318", "--to", "EPSG:4326",
                              "--point", "-82.0", "29.2"])
    code2, out2, err2 = run_cli(["--from", "EPSG:4258", "--to", "EPSG:4326",
                                 "--point", "10", "50"])
    check(code == 1 and code2 == 1
          and "NAD83(2011) to WGS 84 (1)" in out
          and "ETRS89 to WGS 84 (1)" in out2
          and "a grid cannot fix this pair" in out
          and "a grid cannot fix this pair" in out2,
          "NAD83(2011) and ETRS89 to WGS 84 are declared zero shifts with "
          "no better operation, so they exit 1 on any host")
    code, out, err = run_cli(["--from", "EPSG:4267", "--to", "EPSG:4269"])
    check(code in (1, 3) and "probe(s), first at" in out,
          "with no --point the centre of both areas of use is probed")
    code, out, err = run_cli(["--from", "EPSG:2237", "--to", "EPSG:4326"])
    where = out.split("first at ")[1].split("\n")[0].split(", ")
    lon, lat = pyproj.Transformer.from_crs(
        "EPSG:2237", "EPSG:4326", always_xy=True).transform(
            float(where[0]), float(where[1]))
    area = pyproj.CRS.from_user_input("EPSG:2237").area_of_use
    check(area.west < lon < area.east and area.south < lat < area.north,
          "the default probe of a zone to the globe lies inside the zone")
    code, out, err = run_cli(["--from", "EPSG:4326", "--to", "EPSG:4326",
                              "--point", "-82", "29", "--point", "-81", "30"])
    check(code == 0 and "2 probe(s)" in out,
          "two --point flags are two probes under one operation")

    # a control file whose truth sits 30 ft east and 40 ft north of the result
    tf = pyproj.Transformer.from_crs("EPSG:2237", "EPSG:2883", always_xy=True)
    rows = ["sx,sy,tx,ty"]
    for sx, sy in ((500000.0, 1500000.0), (510000.0, 1510000.0)):
        gx, gy = tf.transform(sx, sy)
        rows.append("%r,%r,%r,%r" % (sx, sy, gx + 30.0, gy + 40.0))
    control = write("control.csv", "\n".join(rows) + "\n")
    before = listing()
    code, out, err = run_cli(base + ["--control", control])
    check(code == 3 and "worst miss 50.00 ftUS" in out
          and "mean miss 50.00 ftUS" in out
          and "worst displacement 0.3078 acres of a 1 acre parcel" in out,
          "a control file 30 ft east and 40 ft north reports a 50.00 ftUS "
          "miss and 0.3078 acres")
    check(listing() == before,
          "checking against a control file writes nothing")
    bom = write("bom.csv", chr(0xFEFF) + open(control).read())
    code, out, err = run_cli(base + ["--control", bom])
    check(code == 3 and "worst miss 50.00 ftUS" in out,
          "a control file saved by Excel with a byte order mark reads the "
          "same  <-- pinned defect")
    code, out, err = run_cli(base + ["--control", control,
                                     "--parcel-acres", "4"])
    check("of a 4 acre parcel" in out
          and "worst displacement 0.6432 acres" in out,
          "the same 50 ft miss displaces 0.6432 acres of a four acre parcel")
    check("first at 500000.0, 1500000.0" in out and "2 probe(s)" in out,
          "the control rows are the probe points when no --point is given")

    # a geographic target measures with a geodesic, not with degrees
    gx, gy = pyproj.Transformer.from_crs(
        "EPSG:2237", "EPSG:4269", always_xy=True).transform(500000.0, 1500000.0)
    geo = write("geo.csv", "sx,sy,tx,ty\n500000.0,1500000.0,%r,%r\n"
                % (gx, gy + 0.0001))
    code, out, err = run_cli(["--from", "EPSG:2237", "--to", "EPSG:4269",
                              "--control", geo])
    worst = float(out.split("worst miss ")[1].split(" ftUS")[0])
    check(36.2 < worst < 36.5,
          "0.0001 degrees of latitude at 29 degrees north is a 36.3 ftUS "
          "miss against a geographic target")
    dst_crs = pyproj.CRS.from_user_input("EPSG:6440")
    check(abs(offset_ft(dst_crs, (0.0, 0.0), (1.0, 0.0))[0]
              - 3.280833333) < 1e-6,
          "a metre target CRS converts one metre to 3.28 feet, not one foot")
    ftus_crs = pyproj.CRS.from_user_input("EPSG:2883")
    check(abs(offset_ft(ftus_crs, (0.0, 0.0), (1.0, 0.0))[0] - 1.0)
          < 1e-12 and abs(ftus_crs.axis_info[0].unit_conversion_factor
                          - 1200.0 / 3937.0) < 1e-15,
          "EPSG records the US survey foot as 1200/3937 of a metre")

    # emit-control is the hand-off from a host that has the grids
    pts = write("pts.csv", "sx,sy\n600000,1500000\n610000,1510000\n")
    code, out, err = run_cli(["--from", "EPSG:6440", "--to", "EPSG:6440",
                              "--emit-control", pts])
    check(code == 0 and out.startswith("sx,sy,tx,ty\n")
          and out.count("\n") == 3,
          "--emit-control prints a header and one row per point")
    emitted = write("emitted.csv", out)
    code, out2, err = run_cli(["--from", "EPSG:6440", "--to", "EPSG:6440",
                               "--control", emitted])
    check(code == 0 and "worst miss 0.00 ftUS" in out2,
          "the emitted rows read back as a control file with a zero miss")
    before = listing()
    code, out, err = run_cli(["--from", "EPSG:2237", "--to", "EPSG:2883",
                              "--emit-control", pts])
    check(code == 3 and "sx,sy" not in out
          and "refused to emit control values" in err and listing() == before,
          "a host without the grids refuses to emit control values and "
          "writes nothing  <-- pinned defect")

    # the operation depends on the position, so every row is probed, not only
    # the --point values (ED50 to ETRS89 is a real operation at 10 E 50 N and
    # a ballpark offset at 3 W 54 N when the grids are missing)
    ed50 = ["--from", "EPSG:4230", "--to", "EPSG:4258", "--point", "10", "50"]
    near = write("near.csv", "sx,sy\n10,50\n")
    far = write("far.csv", "sx,sy\n-3,54\n")
    code, out, err = run_cli(ed50 + ["--emit-control", near])
    check(code == 0 and out.startswith("sx,sy,tx,ty\n10.000000,50.000000,"),
          "an emit row in a covered area is written")
    code, out, err = run_cli(ed50 + ["--emit-control", far])
    check(code == 1 and out == "" and "NULL SHIFT" in err
          and "refused to emit control values" in err,
          "an emit row in a fallback area is refused even when --point sits "
          "in a covered area  <-- pinned defect")
    ctl = write("ctl.csv", "sx,sy,tx,ty\n-3,54,-2.99,54.003\n")
    code, out, err = run_cli(ed50 + ["--control", ctl])
    check(code == 1 and "RESULT: NULL SHIFT (exit 1)" in out
          and "first at -3.0, 54.0" in out,
          "a control row in a fallback area decides the exit code even when "
          "--point sits in a covered area  <-- pinned defect")
    huge = write("huge.csv", "sx,sy\n600000,1500000\n1e30,1e30\n")
    code, out, err = run_cli(base + ["--point", "500000", "1500000",
                                     "--emit-control", huge])
    check(code == 2 and "no coordinate" in err and out == "",
          "an emit row PROJ cannot transform exits 2 and prints no truth  "
          "<-- pinned defect")

    # a CRS name with a character the console cannot print
    greek = ('GEOGCS["\u03b1",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
             '6378137,298.257223563]],PRIMEM["Greenwich",0],'
             'UNIT["Degree",0.0174532925199433]]')
    narrow = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    with contextlib.redirect_stdout(narrow):
        code = main(["--from", greek, "--to", "EPSG:4326",
                     "--point", "-82", "29"])
    narrow.flush()
    check(code == 0 and b"datumcheck: GEOGCS[" in narrow.buffer.getvalue(),
          "a CRS with a Greek letter in its name is checked, not lost to a "
          "console encoding error  <-- pinned defect")

    # PROJ network mode: PROJ calls out and PROJ writes cache.db, this tool
    # does neither. The stub answers 404, so the run reads no grid.
    asked = []

    class Stub(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            self.send_response(404)
            self.end_headers()

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    net_dir = os.path.join(tmp, "proj-net")
    os.mkdir(net_dir)
    env = dict(os.environ, PROJ_NETWORK="ON",
               PROJ_USER_WRITABLE_DIRECTORY=net_dir,
               PROJ_NETWORK_ENDPOINT="http://127.0.0.1:%d" %
               server.server_address[1])
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__)] + fl, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    check(proc.returncode in (0, 1, 2, 3) and len(asked) > 0
          and all(path.endswith(".tif") for path in asked),
          "with PROJ_NETWORK=ON the run asks only the endpoint for grid "
          "files, and the tool takes no URL from its input")
    check(os.listdir(net_dir) == ["cache.db"],
          "with PROJ_NETWORK=ON PROJ writes cache.db to its user directory "
          "and nothing else is written")

    # failures that must stop the run, not pass it
    code, out, err = run_cli(["--from", "EPSG:9999999", "--to", "EPSG:2883"])
    check(code == 2 and "cannot read a CRS" in err,
          "an unknown CRS exits 2")
    check(run_cli(base + ["--control", os.path.join(tmp, "absent.csv")])[0]
          == 2, "a control file that does not exist exits 2")
    bad = write("bad.csv", "sx,sy,tx,ty\n1,2,3\n")
    code, out, err = run_cli(base + ["--control", bad])
    check(code == 2 and "line 2" in err,
          "a control file with a short row exits 2 and names the line")
    code, out, err = run_cli(base + ["--point", "1e30", "1e30"])
    check(code == 2 and "no coordinate" in err,
          "a probe PROJ cannot transform exits 2 instead of passing")
    outside = write("outside.csv", "sx,sy,tx,ty\n500000,1500000,1,1\n"
                                   "1e30,1e30,1,1\n")
    code, out, err = run_cli(base + ["--point", "500000", "1500000",
                                     "--control", outside])
    check(code == 2 and "no coordinate for 1e+30, 1e+30" in err,
          "a control row PROJ cannot transform exits 2 instead of passing")

    # the switch pyproj already has: it never raises, and which value it
    # returns depends on the PROJ version
    default_x = pyproj.Transformer.from_crs(
        "EPSG:2237", "EPSG:2883", always_xy=True).transform(
            500000.0, 1500000.0)[0]
    best_x = pyproj.Transformer.from_crs(
        "EPSG:2237", "EPSG:2883", always_xy=True, only_best=True).transform(
            500000.0, 1500000.0)[0]
    check(math.isinf(best_x) or best_x == default_x,
          "pyproj only_best=True raises nothing here: it returns inf or the "
          "same fallback coordinate, depending on the PROJ version")

    # ---- a fault in this tool is not a finding about the data
    real_run = globals()["_run"]

    def boom(argv):
        raise KeyError("probe")
    globals()["_run"] = boom
    try:
        code, out, err = run_cli(base)
    finally:
        globals()["_run"] = real_run
    check(code == 70 and "fault in the tool" in err,
          "an unexpected exception exits 70, never 1  <-- pinned defect")

    # ---- the module and the harness
    spec = importlib.util.spec_from_file_location(
        "datumcheck_imported", os.path.abspath(__file__))
    imported = importlib.util.module_from_spec(spec)
    buf = io.StringIO()
    cache_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        with contextlib.redirect_stdout(buf):
            spec.loader.exec_module(imported)
    finally:
        sys.dont_write_bytecode = cache_before
    check(buf.getvalue() == "" and callable(imported.is_null_op),
          "importing the module runs nothing and exposes the core")

    seen = []
    real_body = globals()["_self_test"]

    def raising(path, user):
        seen.append(path)
        raise RuntimeError("probe")
    globals()["_self_test"] = raising
    try:
        try:
            self_test()
        except RuntimeError:
            seen.append("raised")
    finally:
        globals()["_self_test"] = real_body
    check(len(seen) == 2 and seen[1] == "raised"
          and not os.path.exists(seen[0])
          and os.environ.get("PROJ_USER_WRITABLE_DIRECTORY") == user_dir,
          "a self-test that raises still removes its temp directory and "
          "restores the environment  <-- pinned defect")

    quiet = sys.stdout
    sys.stdout = io.StringIO()
    mark = len(failed)
    try:
        check(False, "probe: a false condition must be recorded as a failure")
        raises(lambda: None, "probe: a call that raises nothing must fail")
        raises(lambda: [][0], "probe: the wrong exception must fail")
    finally:
        sys.stdout = quiet
    probe = failed[mark:]
    del failed[mark:]
    check(len(probe) == 3,
          "check() and raises() really do record a failure  <-- pinned defect")

    def summary_footer(passed_count, failures):
        print("-" * 68)
        total = passed_count + len(failures)
        if failures:
            print("%d assertions, %d failed" % (total, len(failures)))
            for name in failures:
                print("  FAILED: %s" % name)
            return 1
        print("%d assertions, 0 failed" % total)
        return 0

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        red = summary_footer(1, ["probe one", "probe two"])
    check(red == 1 and "3 assertions, 2 failed" in buf.getvalue()
          and "  FAILED: probe two" in buf.getvalue(),
          "the footer reports failures by count and by name, and exits 1"
          "  <-- pinned defect")

    return summary_footer(passed[0], failed)


if __name__ == "__main__":
    sys.exit(main())
