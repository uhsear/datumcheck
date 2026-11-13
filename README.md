# datumcheck

Exit non-zero when PROJ moves your data with a null-shift operation. Report the error in US survey
feet and in acres.

Picture a nightly job on a Linux server. It reprojects a parcel layer from NAD83 to NAD83(2011) State Plane.
The Python there came from `pip install pyproj`. The job logs no warning and exits 0. The output
looks right on a map. The story is an illustration, not a report of a real incident.

It is not right. The pip wheel carries no datum grids, so PROJ cannot use the operation that
needs one. It falls back to a zero translation, or to a "ballpark" offset that moves nothing.
The figures below come from synthetic points on a Linux host with no grids (see "Error in feet
and acres"), not from a production layer. For NAD83 to NAD83(2011) in Florida East, every point
lands about 1.6 feet from where the grid operation puts it. In the same run PROJ used two
different fallbacks for different points of the same layer. Older data is worse. A NAD27 layer
that falls back lands about 15 feet off, and a 640 acre section that moves that far claims about
2.5 acres that belong to its neighbours. Nobody sees this, because no step in the pipeline
compares the result with anything.

This tool asks PROJ which operation it used for real points, and refuses when that operation
shifts nothing. Give it a control file of known-good coordinates and it also says how far off the
result is.

```
$ python datumcheck.py --self-test
datumcheck self-test: temporary files and loopback only, no credentials
--------------------------------------------------------------------
PASS  1200/3937 of a metre is exactly one US survey foot
PASS  one metre is 3.2808333 US survey feet
...
PASS  a zero offset of accuracy 0 is the same datum and is not flagged  <-- pinned defect
...
PASS  probes of one operation and verdict that miss different grids are separate groups  <-- pinned defect
...
PASS  a grid that is already installed is not listed as needed  <-- pinned defect
...
PASS  a prefix --hel of --help is refused  <-- pinned defect
...
PASS  a pyproj older than 3.5, a PROJ older than 9.2, or a pyproj that reports no version exits 2 and names the versions it needs  <-- pinned defect
...
PASS  NAD83 to NAD83(2011) without grids is a null shift and exits 1
...
PASS  Florida West to North without grids is degraded and exits 3
...
PASS  a default probe that falls offshore on a ballpark offset exits 2 and asks for --point instead of failing a good host  <-- pinned defect
...
PASS  Oahu alone is degraded, Nihoa alone is clean, and the pair exits 3 in either order  <-- pinned defect
...
PASS  a proj4 NAD27 definition whose optional grids are missing does not move the point and exits 1  <-- pinned defect
PASS  a credential in an optional grid URL is not echoed in the report body (hunter2)  <-- pinned defect
...
PASS  a skipped optional grid is found when the target step moves the point, source side  <-- pinned defect
...
PASS  a control file 30 ft east and 40 ft north reports a 50.00 ftUS miss and 0.3078 acres
...
PASS  a control miss of 50 ft over --max-miss 10 exits 4, not 0  <-- pinned defect
...
PASS  a probe outside the area of use of the source CRS exits 2  <-- pinned defect
...
PASS  a latitude-first --point from EPSG:4326 to EPSG:4267 exits 2, not a false NULL SHIFT  <-- pinned defect
...
PASS  a longitude written east of 180 that wraps into the area of use is probed, not refused  <-- pinned defect
...
PASS  an ESRI .prj source refuses a northing-first and a lon/lat point like its EPSG twin, not a false exit 0  <-- pinned defect
...
PASS  the default probe of NAD83 to NAD83(2011) lies in the CONUS NADCON5 area, not offshore  <-- pinned defect
...
PASS  a host without the grids refuses to emit control values and writes nothing  <-- pinned defect
...
PASS  a credential in --from URL text is not echoed in the CRS error  <-- pinned defect
...
--------------------------------------------------------------------
217 assertions, 0 failed
```

## What already exists

PROJ and pyproj can already tell you most of this. They do the hard part, which is the fetching.

- `pyproj.transformer.TransformerGroup` lists the candidate operations. Its `best_available`
  attribute is `False` when the best operation needs a missing grid, and `unavailable_operations`
  names them.
- `Transformer.from_crs(..., only_best=True)` asks PROJ to fail instead of using a lesser
  operation. It needs pyproj 3.5 and PROJ 9.2 or newer. PROJ 9.2 also added the
  `PROJ_ONLY_BEST_DEFAULT` environment variable and the `only_best_default` setting of `proj.ini`,
  which turn the same refusal on for every transformation.
- `allow_ballpark=False` refuses a ballpark offset.
- `pyproj.aoi.BBox` has `contains` and `intersects` for area-of-use tests
  (<https://pyproj4.github.io/pyproj/stable/api/aoi.html>). datumcheck uses its own box tests,
  because they must handle a west-greater-than-east box and longitudes written east of 180.
  Measured on pyproj 3.8.0: `BBox(167.65, 14.92, -40.73, 86.45)`, the `EPSG:4269` box, reports
  `contains` and `intersects` as `False` for a small box in Florida, and
  `BBox(-82.33, 24.41, -79.97, 30.83).contains(BBox(278, 25, 279, 26))` is `False`.
- `projsync` and `pyproj sync` download the grids. `PROJ_NETWORK=ON` lets PROJ read them from the
  PROJ CDN as it needs them.
- `OSGeo/PROJ-data` on GitHub holds the grids themselves.

What they do not do, as far as I could test:

- `TransformerGroup` is not the operation PROJ used. On a host with no grids it listed one
  candidate, a ballpark offset with unknown accuracy. `Transformer.from_crs` then used a different
  operation, a chain through WGS 84 with a declared accuracy of 5 m. Run with
  `TransformerGroup("EPSG:2237", "EPSG:4152", always_xy=True)` and
  `Transformer.from_crs("EPSG:2237", "EPSG:4152", always_xy=True)` on the Linux host below:

  ```
  best_available: False
  group[0]: Inverse of SPCS83 Florida West zone (US survey foot) + Ballpark geographic  ...
  from_crs used: Inverse of SPCS83 Florida West zone (US survey foot) + NAD83 to WGS 84 (1)  ...
  accuracy: group[0] -1.0 m, used 5.0 m
  ```

  datumcheck reads the operation from `Transformer.get_last_used_operation()`, after it has
  transformed a real point.
- `only_best=True` is a one-keyword gate on one stack I tested, and does nothing on two others.
  With pyproj 3.8.0 and PROJ 9.8.1 and no grids, a plain call returned the fallback coordinate.
  With `only_best=True`, the same call returned `(inf, inf)` and `transform(..., errcheck=True)`
  raised `ProjError` ("No operation matching criteria found"). The
  same happened with `PROJ_ONLY_BEST_DEFAULT=ON` set before the import, and for the NAD27 fallback
  (`EPSG:26760` to `EPSG:2238`, declared accuracy 14 m). With pyproj 3.7.2 and PROJ 9.5.1, and with
  pyproj 3.6.1 and PROJ 9.3.0, the same calls returned the fallback coordinate and raised nothing.
  Where it does raise, it cannot tell a zero shift from a coarse one: it fails on every missing
  best operation, and it passes a declared zero shift such as NAD83(2011) to WGS 84.
  `allow_ballpark=False` did not refuse the zero-translation `NAD83 to NAD83(2011) (1)` either,
  because that operation is not a ballpark offset. Neither names a grid or states feet or acres.
- None of them state the miss in US survey feet or acres. The numbers need ground truth, and PROJ
  has none on a host that lacks the grids.

I searched GitHub with `gh search repos` for `datumcheck`, `projsync`, `ballpark`, `nadcon`,
`datum-shift` and `proj-data`. On 2026-10-29 the search for `datumcheck` returned only
`uhsear/datumcheck`, which is this repository. The closest other results were `OSGeo/PROJ-data`
(the grids), `nc5ng/nadcon5-ng` (a NADCON5 tool) and `coordinatetransformation/coordinatetransformation`
(Python for cadastral NTv2 and NADCON grid shifts and validation). I did not find one that exits
non-zero when PROJ picks a null-shift operation.

## Requirements

Python 3.9 or newer, and `pyproj` 3.5 or newer with PROJ 9.2 or newer. The tool exits 2 and names
both versions when either is older, or when `pyproj` reports no version. `--self-test` needs
the same stack and also needs pyproj, because half of it runs real PROJ.

The floor is not arbitrary. The PROJ 9.2.0 release notes list "Added support for NADCON5 grids and
transformation method" and the `ONLY_BEST` option. The 3.5.0 wheel reports PROJ 9.2.0 and the 3.4.0 wheel reports PROJ 9.1.0, measured from the version
strings, not from the pyproj release notes. On pyproj 3.4.0 with PROJ 9.1.0 and no grids, version d25c27e of this tool checked
`--from EPSG:2236 --to EPSG:6438 --point 600000 1200000`, exited 1 and printed "no more accurate
operation is known to PROJ here; a grid cannot fix this pair". That sentence is false: four NADCON5
grids fix the pair, and the Windows host with those grids reports `OK` for it. The exit code was
right and the advice was wrong, so the tool now refuses the older stack instead.

```
python -m pip install pyproj
git clone https://github.com/uhsear/datumcheck.git
```

Tested on:

| Host | Python | pyproj | PROJ | Self-test |
|---|---|---|---|---|
| Windows 11 | 3.13.2 (venv) | 3.8.0 | 9.8.1 | 217 assertions, 0 failed |
| Windows 11 | 3.13.2 (`C:\Python313`) | 3.7.2 | 9.5.1 | 217 assertions, 0 failed |
| Linux (Ubuntu) | 3.12.3 (venv) | 3.8.0 | 9.8.1 | 217 assertions, 0 failed |
| Linux (Ubuntu) | 3.9.25 (venv) | 3.6.1 | 9.3.0 | 217 assertions, 0 failed |
| Linux (Ubuntu) | 3.9.25 (venv) | 3.5.0 | 9.2.0 | 217 assertions, 0 failed |
| Linux (Ubuntu) | 3.9.25 (venv) | 3.4.0 | 9.1.0 | refused, exit 2; the check refuses too |
| Linux (Ubuntu) | 3.9.25 (venv) | 3.4.1 | 9.1.0 | refused, exit 2, the same as 3.4.0 |

The count is the same on every host that runs the self-test, because the real PROJ part runs on
every host with the grids hidden. The pyproj 3.4.0 row ran `--self-test` and the check
`--from EPSG:2236 --to EPSG:6438 --point 600000 1200000`. Both exited 2 and printed that the stack
is too old. No host has `arcpy`; the tool does not use it.

## Quick start

```
python datumcheck.py --self-test
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --point 600000 1200000
```

## Usage

The check writes no file of its own, and nothing in this tool changes your data, so there is no `--apply`.
Two things write, and both are outside that rule on purpose. `--self-test` makes a fresh
temporary directory with CSV fixtures and empty PROJ user directories, and removes it when it
ends, even if an assertion raises. A self-test killed with SIGKILL leaves the directory behind.
PROJ itself creates `cache.db` in its user directory when `PROJ_NETWORK=ON`, on a plain check too.

```
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --control truth.csv
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --emit-control points.csv > truth.csv
```

| Flag | Default | What it does |
|---|---|---|
| `--from CRS` | none | Source CRS, anything pyproj reads, such as `EPSG:2236`. Required. |
| `--to CRS` | none | Target CRS. Required. |
| `--point X Y` | centre of both areas of use, moved inside the area of the most accurate operation PROJ lists | A probe point in source CRS units. X first: easting, or longitude for a geographic CRS. Two numbers with a space between. Repeat the flag for more points. A point outside the area of use of the source CRS exits 2. Pass it for any real check: the default can lie offshore (see Limits). |
| `--control CSV` | none | Known-good coordinates. Columns `sx,sy,tx,ty`. Adds the miss in feet and acres. The `sx,sy` values are the probes when you pass no `--point`. For a geographic target the `tx,ty` values must be degrees. |
| `--emit-control CSV` | none | Read points (columns `sx,sy`) and print control rows to standard output. Refuses unless this host used the best available operation (exit 0). |
| `--max-miss FEET` | none | With `--control`, exit 4 when the worst miss is more than this many US survey feet and nothing worse was found. Without it the control result never changes the exit code. A negative, `nan` or infinite value, or a use without `--control`, exits 64. |
| `--parcel-acres N` | `1.0` | Parcel size for the acres figure. |
| `--self-test` | off | Run the assertions and exit. |

Write a west longitude as two words: `--point -82.0 29.2`. The form `--point -82.0,29.2` is a usage
error, because argparse reads a leading minus sign as a flag. The self-test pins this.

Every parser sets `allow_abbrev=False`. `--hel`, `--emit`, `--cont`, `--po`, `--par` and `--fr`
are all refused with exit 64.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | PROJ used a real operation at every probe. It means the control file agrees only when you passed `--max-miss`; otherwise read the `control:` line. |
| 1 | PROJ used a null-shift operation at one probe or more, or skipped a missing optional grid (below). `--self-test` also exits 1 when an assertion fails. |
| 2 | The check could not run: pyproj missing or older than 3.5, PROJ older than 9.2, unknown CRS, unreadable or bad CSV (an empty path counts as unreadable), a point PROJ cannot transform, a point outside the area of use of the source CRS, control values for a geographic target that are not degrees, or a default probe that fell on a ballpark offset (pass `--point`). |
| 3 | The operation is not a null shift, but a more accurate one needs grids this host lacks. |
| 4 | The operation is sound, but with `--control --max-miss` the worst control miss is over the limit. The `RESULT:` line says `CONTROL MISS`. A null shift (1), a missing grid (3) or a failed run (2) keeps its own code. |
| 64 | Usage error, including an abbreviated or unknown flag. |
| 70 | A fault in the tool. It is never a finding about your data. |

A Python crash before the tool starts, such as an interpreter too old to compile the file, also
exits 1. Read the output text, or run `--self-test` as a separate step.

If a probe is 1 and another is 3, the run exits 1. Code 3 is not code 1, so a gate that tests only
for 1 passes the NAD27 fallback that lands about 15 feet off. Treat any non-zero code as a failure.

## What counts as a null shift

The tool reads the operation PROJ used as PROJJSON and looks at each datum step. A step moves
nothing when:

- its name starts with `Ballpark`, which is how PROJ names an offset it made up, or
- it is a transformation whose every parameter is the number zero. `NAD83 to NAD83(2011) (1)`
  is one. It is an EPSG entry with a declared accuracy of 1 m and three zero translations, or
- its only grid is PROJ's built-in `null` grid, as in `+nadgrids=null`. PROJ documents that file
  as "a zero shift for the whole world"
  (<https://proj.org/en/stable/usage/transformation.html>). A list such as `+nadgrids=conus,null`
  is not null, because the other grid shifts.

An operation is a null shift when it has datum steps and all of them move nothing. A chain that
holds one real step, such as the Helmert from `NAD83(HARN) to WGS 84 (3)`, is not a null shift.
It exits 3 instead, when a better operation exists and its grid is missing.

An operation with a declared accuracy of exactly 0 is never flagged. PROJ gives
`Null geographic offset from WGS 84 (geog2D) to WGS 84 (geog3D)` that accuracy. It is the same
datum seen twice, and a zero is correct there. The self-test pins this with a real PROJ call
(`EPSG:4326` to `EPSG:4979`).

PROJ chooses the operation for each point from the point's position. Its documentation says the
operation listed first is not always the one used. So the tool transforms each probe, then asks.
The lookup of the unavailable operations runs for every probe, in a box of 0.01 degrees around it,
because one operation name can cover regions with different grids. At Oahu, `EPSG:4152` to
`EPSG:4269` needs two missing grids and exits 3. At Nihoa the same operation name needs none, and
on a host with an empty PROJ user directory, as in the self-test, the probe exits 0. That depends on
the host: on the Windows venv with its default user directory (it holds `us_noaa_*` CONUS grids)
the Nihoa probe printed `NULL SHIFT`, exit 1, with a Ballpark step (measured 2026-11-12). The
self-test pins that the pair exits 3 in either order, with the user directory emptied. The lookup costs about 10 ms
per probe: a 200 row file took about 2 seconds on both hosts (see Limits).

A proj4 string can name its grids, as in `+nadgrids=@conus,@alaska`. PROJ treats a grid with a
leading `@` as optional and does not complain when it is missing
(<https://proj.org/en/stable/operations/transformations/hgridshift.html>). It applies no shift. The
step holds a file name, not a zero, so the zero test above cannot see it. When the operation holds
an optional grid, datumcheck checks each side alone: it transforms the point from the source CRS
to WGS 84, and from the target CRS to WGS 84, and tests every side that holds an optional grid. A
later step that does move the point cannot hide a skipped grid in an earlier one. Without this, a
NAD27 definition with `+nadgrids=@conus,...` going to `EPSG:4152` printed `OK` and exited 0, because
the target side moved the point. A side that moved less than 0.000000001 degrees is a null shift and
exits 1. This check works between two geographic CRS only.
With a projected CRS on either side the run exits 2, because it cannot tell. Use EPSG codes.

A compound CRS, such as `EPSG:6438+5703`, is reduced to its horizontal part before the check. A
change of vertical unit makes PROJ merge the datum step into one step with no parameters, which
hid a zero shift. `EPSG:6438+5703` to `EPSG:4326+6360` now exits 1, the same as without the
vertical part.

## Probes and areas of use

PROJ chooses the operation from the position of each point. Its documentation says that
`proj_trans()` "can compare this coordinate to the area of use of operations", and it lists the
synthesised ballpark offset with the area "World"
(<https://proj.org/en/stable/operations/operations_computation.html>). A probe outside the area
where your data lives can therefore get a different operation than your data gets, and the verdict
describes the probe, not the data.

Each CRS carries an area of use: a box of west, south, east and north in degrees
(`CRS.area_of_use` in pyproj, typed `AreaOfUse` or `None`). `EPSG:2236` reports west
-82.33, south 24.41, east -79.97, north 30.83. `EPSG:4269` reports west 167.65, south 14.92, east
-40.73, north 86.45: west is greater than east, so the box crosses the antimeridian. datumcheck
turns each probe into longitude and latitude and exits 2 when it lies outside the box of the
source CRS. A longitude counts at any multiple of 360 degrees: `--point 200 50` is 160 W, inside the
`EPSG:4269` box, and is probed.

```
$ python datumcheck.py --from EPSG:2236 --to EPSG:6438 --point 5000000 5000000
error: the probe 5000000.0, 5000000.0 lies outside the area of use of the source CRS (west -82.33, south 24.41, east -79.97, north 30.83). PROJ chooses an operation from the position of each point, and out there only a world-wide ballpark offset or an operation made for another place applies, so the verdict would describe the probe, not your data. Give X first (easting, or longitude for a geographic CRS).
$ echo $?
2
```

The same refusal catches a latitude-first point. `--from EPSG:4269 --to EPSG:4326 --point 29.2 -82`
reads latitude 82 S, outside the box, and exits 2. Version d25c27e of this tool exited 1 for it and
printed "a grid cannot fix this pair". For the probe 5000000 5000000 the same version, on the Windows
host with the grids, printed `OK` and exited 0. That was a clean verdict for a place that is not in
Florida.

The default probe, used when you pass no `--point`, `--control` or `--emit-control`, is the centre
of the overlap of the two areas of use. When PROJ lists a most accurate operation for the pair, the
centre is taken inside that operation's box. `EPSG:4269` to `EPSG:6318` now probes -95.85, 36.6,
in the CONUS NADCON5 area. The overlap centre alone is -128.115, 44.815, in the Pacific off Oregon.
The most accurate operation is a guess about where you work: for `EPSG:4269` to `EPSG:4326` the
default is -68.475, 53.805, in Quebec. The operations with the best declared accuracy (1.5 m) are
`NAD83 to WGS 84 (6)`, `(7)` and `(8)`, for Quebec, Saskatchewan and Alberta, and the widest of those
boxes wins. Pass `--point` with a place in your data.

Two limits of the box. It is a rectangle, so a point inside it can still be at sea. A point just
outside it can be good data, and no flag overrides the refusal.

An ESRI `.prj` (WKT1) or a proj4 string has no area of use of its own. When PROJ identifies it as
an EPSG CRS (`CRS.to_epsg()`, default confidence 70), that CRS lends its area, so a `.prj` of
`EPSG:26717` refuses a northing-first point like `--from EPSG:26717` does. A source that stays
unidentified is checked against the area of the target. When neither CRS gives a bounded area, the
position is not checked: the report prints a note, the verdict line reads `OK (position not checked)`,
and an otherwise clean result reads `RESULT: no null shift found at a probe whose position was not
checked (exit 0)`. The exit code is still 0.

## Error in feet and acres

Grids hide the answer. Without them PROJ cannot say how far the fallback is from the grid result.
You bring the truth in a control file. It can come from survey control, or from a host that has
the grids. The second case is what `--emit-control` is for.

On a host with the grids (Windows, `us_noaa_*.tif` in the PROJ user directory), for three
synthetic points in Florida East State Plane:

```
$ python datumcheck.py --from EPSG:2236 --to EPSG:6438 --emit-control pts.csv > truth.csv
$ cat truth.csv
sx,sy,tx,ty
600000.000000,1200000.000000,600001.532339,1199999.637723
610000.000000,1210000.000000,610001.527886,1209999.562849
620000.000000,1220000.000000,620001.511584,1219999.507166
```

Then on the Linux host, whose pip install has no grids (`find ... -name "*.tif"` found none in
the pyproj package of that venv, of the Windows venv, or of `C:\Python313`):

```
$ python datumcheck.py --from EPSG:2236 --to EPSG:6438 --control truth.csv
datumcheck: EPSG:2236 -> EPSG:6438
NULL SHIFT  1 probe(s), first at 600000.0, 1200000.0
  PROJ used  Inverse of SPCS83 Florida East zone (US survey foot) + NAD83 to NAD83(2011) (1) + SPCS83 Florida East zone (US survey foot) (declared accuracy 1 m)
  needs      us_noaa_nadcon5_nad83_1986_nad83_harn_conus.tif  https://cdn.proj.org/us_noaa_nadcon5_nad83_1986_nad83_harn_conus.tif  (operation accuracy 0.2 m)
  install    projsync --file us_noaa_nadcon5_nad83_1986_nad83_harn_conus.tif
  ... three more grids ...
NULL SHIFT  2 probe(s), first at 610000.0, 1210000.0
  PROJ used  Inverse of SPCS83 Florida East zone (US survey foot) + Ballpark geographic offset from NAD83 to NAD83(2011) + SPCS83 Florida East zone (US survey foot) (declared accuracy unknown)
  ... the same grids ...
control: 3 rows, worst miss 1.59 ftUS, mean miss 1.58 ftUS, worst displacement 0.0096 acres of a 1 acre parcel
RESULT: NULL SHIFT (exit 1)
```

The first point used a zero translation. The other two used a ballpark offset. The report groups
the probes by operation, so the difference shows.

Results for two pairs, all measured. The Florida East pair uses the three synthetic points above.
The Florida North pair uses three synthetic points read in its own zone and inside its area of use
(2069331 523739, 1842206 473103 and 2316543 365046). The last column adds `--parcel-acres 640`:

| Pair | Host | Exit | Worst miss | Acres, 1 acre parcel | Acres, 640 acre parcel |
|---|---|---|---|---|---|
| NAD83 to NAD83(2011), Florida East ftUS (`2236` to `6438`) | Linux, no grids | 1 | 1.59 ftUS | 0.0096 | not run |
| NAD27 to NAD83, Florida North ftUS (`26760` to `2238`) | Linux, no grids | 3 | 15.06 ftUS | 0.0951 | 2.4562 |
| both | Windows, grids installed | 0 | 0.00 ftUS | 0.0000 | not run |

The Windows rows prove the control file is sound: with the grids, PROJ reproduces its own truth.
The NAD27 pair used a chain with a declared accuracy of 14 m and exits 3, not 1, because that
chain holds real translations. An earlier version of this table read 12.27 ftUS and 1.5198 acres.
It read the Florida East points in the Florida North zone, outside the zone's area of use, and the
area-of-use refusal now rejects those points with exit 2.

### The units

- The US survey foot is 1200/3937 of a metre. EPSG stores this as the unit factor of every ftUS
  CRS. The self-test reads it from `proj.db` and compares it with 1200/3937.
- A geographic target is measured with a geodesic on the target's own ellipsoid. A projected
  target uses its axis unit factor. The self-test checks both.
- One acre is 43,560 square feet, here square US survey feet. The international-foot acre is
  4046.8564224 m2, and the survey acre is 43560 x (1200/3937)^2 = 4046.8726 m2. The difference is
  4 parts in a million.
- Displaced acres is the part of a square parcel of the given size that no longer overlaps its true
  position. A move of one side or more displaces the whole parcel. It is a measure of the ground
  now claimed wrongly. It is not the change in a parcel's computed area, which a rigid shift does
  not alter.

## Limits

- Probes are samples. A layer that spans two areas of use can use a different operation between
  your probes. Pass `--point` for each corner, or pass a control file with a spread of points.
- Probes that share an operation but differ in verdict or in missing grids are reported as separate
  groups, so the advice of one probe is never reused for another.
- datumcheck makes no network call of its own. PROJ's network mode (`PROJ_NETWORK=ON`) is outside
  it. With the network on and an endpoint that cannot be reached (run by hand with
  `PROJ_NETWORK_ENDPOINT=http://127.0.0.1:9`, which the self-test does not cover),
  the run exits 2 and its message says the grid could not be read. The self-test runs the tool once with it on, against a stub `http.server` on 127.0.0.1 that
  answers 404 to every request. PROJ asked the stub only for `.tif` grid files and wrote `cache.db`.
  No grid was ever fetched, so the self-test does not show what a real fetch changes. If you enable
  network mode on a real host, PROJ may fetch grids and the verdict will change.
- A CRS definition taken from untrusted data can make PROJ call out when network mode is on. The
  self-test passes `+nadgrids=http://127.0.0.1:PORT/evil.gsb` and the stub receives the request.
  Do not feed CRS text you do not trust to this tool with `PROJ_NETWORK=ON`. This applies to
  `--from` and `--to` only.
- Control values are only as good as their source. Control made on a host that also fell back is
  refused by `--emit-control`.
- Horizontal only. A vertical datum is not checked. A compound CRS is reduced to its horizontal
  part.
- Exit 0 means only that PROJ used no null-shift operation and knows of no better one that is
  missing. With `--control` it does not mean the control file agreed, unless you also passed
  `--max-miss`. With a control file and no `--max-miss` the miss is reported but never gated: a control row 50 ft off still
  prints `worst miss 50.00 ftUS` and exits 0 on a host with the grids. This holds for a bad
  control file too, such as swapped columns or values in the wrong units. Pass `--max-miss FEET` to
  turn a miss over the limit into exit 4 (`RESULT: CONTROL MISS`); the self-test pins this on a 50
  ft miss. The limit gates the worst row only, and a wrong limit passes a bad file.
- The error in feet and acres appears only when you pass `--control` (or an emitted control file).
  Without ground truth the report shows only PROJ's declared accuracy in metres.
- A NAD83(2011) to WGS 84 move (`EPSG:6318` to `EPSG:4326`) and an ETRS89 to WGS 84 move
  (`EPSG:4258` to `EPSG:4326`) can never exit 0. EPSG defines those operations as three zero
  translations and PROJ offers nothing better, so the check exits 1 with grids or without them
  (measured both ways). There is no flag to accept this. Plain NAD83 to WGS 84 (`EPSG:4269` to
  `EPSG:4326`) depends on the host: at -82 29 the Windows host with grids used `NAD83 to WGS 84
  (18)` (declared accuracy 2 m) and exited 0, and the Linux host without grids used `NAD83 to WGS 84
  (1)` (declared accuracy 4 m) and exited 1. Other operations that EPSG declares as zero shifts
  also exit 1 on any host: RGF93 v1 to ETRS89 (`EPSG:4171` to `EPSG:4258`), and CHTRS95 to ETRS89
  and to WGS 84 (`EPSG:4151` to `EPSG:4258` and `EPSG:4326`).
- A skipped optional grid is found by its `@` prefix, and the `null` grid by its name. A required
  grid that is missing makes PROJ refuse the definition, which exits 2. The `+nadgrids=conus,null`
  case exits 2 on a host without the `conus` file (measured).
- An operation counts as missing when any one of its grids is missing. The report lists only the
  grids the host lacks. Measured with 3 of the 4 NADCON5 conus files for `EPSG:2236` to
  `EPSG:6438` in an otherwise empty `PROJ_USER_WRITABLE_DIRECTORY`: one grid was listed,
  `us_noaa_nadcon5_nad83_harn_nad83_fbn_conus.tif`, and version d25c27e listed all 4. With all 4
  held the probe reports `OK` and exits 0. The self-test pins the filter with made-up grid records
  and cannot copy real grids, so the real run above was done by hand.
- The probe takes X first (longitude first for a geographic source). A latitude-first point is
  refused when it lies outside the area of use of the source CRS. A world-wide source such as
  `EPSG:4326` has no such limit, so the area of use of the target CRS is checked instead:
  `--from EPSG:4326 --to EPSG:4269 --point 29.2 -82` exits 2. When both CRS are world-wide,
  `--from EPSG:4326 --to EPSG:4326 --point 29.2 -82` is a valid place in Antarctica and is probed
  (exit 0, measured).
- Only the source area of use is checked, except for a world-wide source. A probe inside the
  source area but outside the target area is probed anyway, because data in one zone may move into
  a zone that does not cover it. Its verdict describes that place. For example `--from EPSG:4269
  --to EPSG:6438 --point -100 40` (Kansas, Florida East target) prints `OK` and exits 0 on a host
  that holds the NADCON5 grids. On a host without them it prints `NULL SHIFT` with a Ballpark
  offset and exits 1 (measured both ways), so the verdict follows the host as well as the place.
- For pairs with several regional operations the default probe can be in another country, so a
  gating job should always pass `--point`. `EPSG:4269` to `EPSG:4326` probes Quebec.
- Python may write its own bytecode cache for the standard library on first import. datumcheck
  writes no file itself.
- Exit 1 from `--self-test` means an assertion failed, not that a null shift was found. Run the
  self-test as a separate step.
- A control file header is case sensitive: `SX,SY,TX,TY` exits 2 with a missing column message.
- The default probe is a guess about where you work (see "Probes and areas of use"). It can lie
  offshore or far from your data. For `EPSG:6318` to `EPSG:6783` it is 44.815 N, 128.115 W, in the
  Pacific off Oregon, where PROJ falls back to a ballpark offset even on a host that holds every
  grid. When the default probe lands on a ballpark offset and no better operation is listed, the
  run exits 2 and asks for `--point`, instead of failing a good host. A default probe on a declared
  zero shift still exits 1, because that holds anywhere. Pass `--point` for your own data.
- The area of use is a rectangle. A probe inside it can lie at sea, and a probe just outside it can
  be good data that the tool refuses with exit 2. No flag overrides the refusal. A CRS without an
  area of use that PROJ cannot identify as an EPSG CRS is checked against the target area, and a
  pair with no bounded area on either side is not checked: a probe there can get an operation made
  for another place. `--from '+proj=longlat +ellps=GRS80 +no_defs' --to '+proj=longlat
  +ellps=GRS80 +no_defs' --point 29.2 -82` exits 0 with the unchecked note (measured). A proj4
  string that PROJ identifies, such as `+proj=longlat +datum=NAD83 +no_defs` (EPSG:4269) or
  `+proj=longlat +datum=NAD27 +no_defs` (EPSG:4267), is checked: `--point 29.2 -82` exits 2.
  Use EPSG codes, with X first.
- When a control or emit row falls outside the area of use of the source CRS, the error shows the
  coordinates, not the line number.
- The version gate (pyproj 3.5, PROJ 9.2) tests both versions. A pyproj 3.4 built against a system
  PROJ 9.2 or newer is refused too. I did not test that stack.
- `--emit-control` writes `%.6f`. On a geographic target that rounds by up to about 0.2 ftUS, so
  reading an emitted file back with `--control` shows a small non-zero miss (measured 0.10 to 0.20
  ftUS, depending on the CRS pair and the points).
  The zero-miss round trip holds for projected targets only.
- A control file with a header column twice (`sx,sy,tx,ty,sx`) takes the last value of that column.
  A row with more cells than the header drops the extra cells. Check your header yourself.
- For a geographic target the `tx,ty` control values must fall in -180 to 360 for X and -90 to 90 for
  Y, or the run exits 2 and names the row. The test is a range. Degrees written latitude first,
  such as `29,-82`, are in range and are scored without a warning: the control line then shows a
  miss of 40 million ftUS and the run still exits 0 on a host with the grids (measured).
- A geographic target in grads or another non-degree angle unit, such as `EPSG:4807`, is not
  supported: `--emit-control` writes grads (54.277855 for latitude 48.85), the range test accepts
  them as degrees, and the miss in feet is wrong.
- A cell such as `1_000` is read as 1000, because Python's `float` accepts digit-group underscores.
- `--emit-control` takes an input file of points, despite its name. In `--emit-control pts.csv >
  truth.csv` the shell truncates an existing `truth.csv` before the tool starts. If this host then
  refuses, `truth.csv` stays empty and an earlier good control file is lost. Write to a new name and
  move it when the exit code is 0. `--emit-control truth.csv > truth.csv` destroys the input and
  exits 2.
- Control, point and emit files must be UTF-8, and a byte order mark is accepted. A file in
  a legacy code page, such as cp1252 with an accented letter, stops the run with exit 2. A header
  such as `sx, sy`, with a space after the comma, stops the run with a missing-column error.
- In Windows PowerShell 5.1, `--emit-control pts.csv > truth.csv` writes UTF-16 (measured), and the next
  `--control` run exits 2 with a UTF-8 decode error on byte 0xff. Use `| Out-File -Encoding
  ascii` or run the command in cmd.exe.
- A field larger than 128 KiB in a control or point file, or a NUL byte in a number, exits 2 with
  the line number. Measured with a 200,000 digit field: `line 1: field larger than field limit
  (131072)`.
- For an oversized field or a NUL byte, the reported line number can be one too low: a field on
  line 3 is reported as line 2. Measured with a header and two data rows. Count from the line named
  in the message and look at the next one.
- A control header with spaces after the commas, such as `sx, sy, tx, ty`, exits 2, and the message
  prints the names with their spaces. Remove the spaces. The tool does not trim header names.
- The reading of `area_of_use` with west greater than east as a box that crosses the antimeridian
  follows the PROJ reference for `proj_area_set_bbox`, which states that for an area of use
  crossing the antimeridian `west_lon_degree` will be greater than `east_lon_degree`, with
  longitudes in the [-180,180] range. The pyproj documentation for `area_of_use` gives the four
  bounds as plain floats and does not say this. The self-test checks the reading.
- Two assertion labels that exist only on `origin/wip/2026-10-24` are not on this branch as
  written. "a pyproj older than 3.5, or a PROJ older than 9.2, exits 2 and names the versions it
  needs" became the label that also names a pyproj that reports no version, and "with no --point
  the centre of both areas of use is probed" gained ", and the report says so". The new
  `version_at_least` and default-probe assertions cover the same behaviour.
- The verdict for a classic proj4 string that names optional grids, such as
  `+nadgrids=@conus,@alaska,@ntv2_0.gsb,@ntv1_can.dat`, depends on which legacy grid files the host
  holds. Measured with `--to EPSG:4269` at -82 29: the Windows venv, which holds the legacy `conus`
  grid, exits 0, and the Linux host with no grids exits 1. Each is correct for its host.
- `--emit-control` writes only fixed-format numbers (`%.6f`, header `sx,sy,tx,ty`), so no cell can
  start with `=`, `+`, `@`, a tab or a carriage return. A negative number starts with a minus sign
  and a spreadsheet reads it as a number.
- A fault (exit 70) prints a Python traceback that includes the local script path and user
  directory names. Keep CI logs of an exit 70 private.
- With standard output closed (`>&-`), the run exits 70 with an `AttributeError` traceback, not 120.
  Only a pipe whose reader has exited gives 120.
- The self-test passes its whole environment, proxy variables with a user name and password
  included, to the child process it starts for the `PROJ_NETWORK=ON` check. It prints none of it.
  Run it with a clean environment on a shared host.
- Setting `PROJ_DATA` does not hide grids kept in the PROJ user directory (the Windows venv found
  its grids there). To test a host without grids, set `PROJ_USER_WRITABLE_DIRECTORY` to an empty
  directory, as the self-test does.
- The working-tree copies of `LICENSE` and `.gitignore` are CRLF on a Windows checkout with
  `core.autocrlf=true`, while the repository holds LF. This does not change the published files.
- Interrupting the run with Ctrl+C exits with the interpreter's own code, not one of the codes above.
- A fault inside the check that raises `ValueError`, `RuntimeError` or `OSError` is reported as exit
  2, so it cannot be told apart from bad input.
- An absurd `--parcel-acres`, such as `1e300`, loses precision: the area minus the overlap cancels
  and the report prints `0.0000 acres` for a miss of 50.00 ftUS. `1e200` prints a large negative
  displacement instead.
- The whole control or emit file is read into memory, and each row costs one PROJ transform and one
  grid lookup, with no cache for a repeated probe. A 200 row file (`EPSG:2236` to
  `EPSG:6438`, `--emit-control`) took 1.9 seconds on the Linux host and 2.0 seconds on the Windows
  venv (two runs each, measured 2026-11-11). At that rate a 10,000 row file takes
  about 100 seconds; I did not run it.
- It does not test ArcGIS Pro, ArcGIS Server or any Esri geoprocessing. It checks only the PROJ
  stack that pyproj, geopandas and GDAL builds use.
- The self-test hides only the grids in PROJ's user directory, by setting
  `PROJ_USER_WRITABLE_DIRECTORY` before PROJ loads. The PROJ source marks it with the
  comment "For testing purposes only" (`src/filemanager.cpp`), and it is not on the official
  environment variable page. If a future PROJ drops
  it, the self-test fails on its isolation check instead of passing with grids present. Grids
  in PROJ's data directory stay visible. With the data directory pointed at a folder that holds
  grids (a conda install with `proj-data`), 11 assertions failed on 2026-11-11 (Windows venv, Python
  3.13.2, pyproj 3.8.0, PROJ 9.8.1, `pyproj.datadir.set_data_dir()` pointed at a copy of the PROJ
  data directory plus 20 `.tif` grids). An earlier version of the tool failed 13 on a Linux conda
  environment with 387 `.tif` files. The count follows the grids and the tool version. The check
  itself is unaffected. Each of those counts comes from one run and was not repeated.
- A killed self-test, or one whose temporary directory cannot be removed, leaves a
  `datumcheck-selftest-*` directory in the temporary directory. The tool ignores a failed removal.
  Delete the directory by hand.
- With `PROJ_NETWORK=ON` on a host that silently drops packets, the tool sets no timeout of its
  own, so a run can stall until libcurl gives up. Only a refused connection was tested. Leave
  `PROJ_NETWORK` off, or put a timeout on the job.
- The self-test needs pyproj on the normal import path. Under `python -I` on a host where pyproj
  sits in the user site-packages, it exits 2 with the install message. Run it without `-I`.
- Three d25c27e self-test labels were replaced, not kept: "a version string reads as its first two
  numbers", "a pyproj without get_last_used_operation exits 2 and names the version it needs" and
  "the self-test refuses pyproj before 3.5 or PROJ before 9.2, and runs on anything newer". The
  version gate is now the function `version_at_least`, and its new assertions cover the same
  behaviour, including the pyproj 3.5 and PROJ 9.2 floor. Every other d25c27e assertion is kept verbatim.
- The self-test's `PROJ_NETWORK=ON` assertions fail when an HTTP proxy variable such as
  `http_proxy` is set without a 127.0.0.1 `no_proxy` entry, because libcurl sends the loopback
  request to the proxy. Measured with `http_proxy=http://127.0.0.1:9`: 2 assertions failed.
- `only_best` behaviour differs between PROJ 9.3.0, 9.5.1 and 9.8.1 (above). The self-test accepts
  all of them.
- On Linux the README commands need `python3` on a stock Ubuntu, which has no `python`, and the
  shebang `#!/usr/bin/env python` fails there unless `python-is-python3` is installed. A Windows
  checkout with `core.autocrlf=true` holds CRLF line endings, which break the shebang on Linux. Copy
  the file from git, or run `python3 datumcheck.py`.
- The line "no more accurate operation is known to PROJ here; a grid cannot fix this pair" describes
  the probe's place and this host only. `--from EPSG:4269 --to EPSG:4326 --point 200 50` exits 1 with
  that line, while the default probe for the same pair (Quebec) shows a grid that fixes it. On the
  Windows venv, whose PROJ user directory holds CONUS grids, the Nihoa probe of `EPSG:4152` to
  `EPSG:4269` prints the line although `TransformerGroup` lists a 6 m operation for the place.
  Treat the line as a statement about the operations PROJ lists for that probe.
- The Oahu pair (`EPSG:4152` to `EPSG:4269`) depends on the host. On a host with no grids it exits 3.
  On the Windows host with grids but without `us_noaa_hihpgn.tif`, the probe at -157.86 21.31 exits
  1 with a Ballpark step. I could not fetch that grid offline, so I did not test the host that has it.
- The default probe can print a coordinate with floating-point noise in the last digits, such as
  `-95.85000000000002`. The value is correct.
- A pair from NAD83(2011) or another zero-translation EPSG datum into the web-map CRS
  `EPSG:3857` exits 1 on any host, for the same reason as the bullet above. Measured for
  `EPSG:6318` to `EPSG:3857` at -82 29. `EPSG:4326` to `EPSG:3857` is a projection with no datum
  step, so it exits 0 (measured, same point).
- A vertical-only CRS such as `EPSG:5703` exits 2, because the tool checks the horizontal move only.
  Give a horizontal or compound CRS.
- Control and point files are echoed in errors. A header problem prints the first
  line of the file, and a bad cell prints the cell. Do not point `--control` or `--emit-control` at
  a file that holds secrets, and keep CI logs of an exit 2 private.
- The tool redacts the user name and password in a URL (`scheme://user:password@host`, also when the
  password holds `/`, `?` or `#`), and the value of any query or proj4 parameter whose name holds
  `password`, `passwd`, `pwd`, `token`, `secret`, `apikey`, `api-key`, `key`, `sig`, `signature` or
  `auth`, so `db_password`, `authToken`, `client_secret` and `+sig=` are hidden. It does this in
  the `--from` and `--to` text before it prints that text in the report header and the CRS
  error, in the optional-grid line, on the `PROJ used` line (PROJ builds that name from the CRS and
  datum names) and in an exit 70 message and traceback. Measured with
  `--from https://user:SECRET@host.invalid/x`: the exit 2 message shows `https://***@host.invalid/x`.
  A bare `user:password@host` with no `://` is echoed, and so is a secret under a name that holds
  none of those words. Usage errors (exit 64) print the offending argument as typed, for example
  `--parcel-acres SECRET` prints `invalid float value: 'SECRET'`. Never pass CRS text or arguments
  that hold a credential. Control and point file cells are not redacted (see the bullet above).
- Read the exit code, not the report text. The report echoes CRS text, which can span several
  lines, so a CRS that holds a `RESULT:` line can imitate the verdict line.
- With no `--point`, the default probe is a guess about where your data lives, and the verdict
  describes that place. Measured with no grids on the host, `--from EPSG:5070 --to EPSG:4326` exits 1
  at a probe in the Quebec and Maine area and names `ca_nrc_NA83SCRS.tif`, a Canadian grid. Always
  pass `--point` in a gating job.
- The `RESULT` line speaks about null shifts only. Measured with `--from EPSG:2237 --to
  EPSG:2883` and a control row `600000,1200000,1,1`: the control line shows a miss of 2765993.09
  ftUS and the run still ends `RESULT: no null shift found (exit 0)`. Read the control line, or
  pass `--max-miss`, before you trust a control file.
- A control file must be comma separated. Measured with a semicolon file: exit 2, "the header lacks
  column sx, sy, tx, ty; it has sx;sy;tx;ty".
- Run datumcheck only under Python 3.9 or newer. I did not run an older interpreter. One may fail
  before the tool starts and exit 1, which is also the code of a null-shift finding. Python 3.9 was
  exercised on Linux only (3.9.25), not on Windows.
- A control file alone never fails the run. Pass `--max-miss` in any gate.
- `--self-test` exits 1 when an assertion fails, the same code as a null shift. A CI step that runs
  `--self-test` and a gating step must be separate steps.
- An ESRI `.prj` or proj4 source that PROJ identifies as an EPSG CRS lends its area to the
  out-of-area refusal but not to the default probe. Measured with the WKT1_ESRI text of
  `EPSG:2236` and `--to EPSG:6438` and no `--point`: exit 2, "a CRS has no area of use; pass
  --point". Pass `--point` with such a source.
- A proj4 string on the WGS 84 ellipsoid that names a required grid file, taken to `EPSG:4326`, is
  treated by PROJ as already in WGS 84, so PROJ drops the grid without a warning. The tool asks the
  CRS for its grids and exits 2 when a required one (a name with no `@`) is not on the host.
  Measured with `--from "+proj=longlat +nadgrids=nosuchfile.gsb" --to EPSG:4326 --point -82 29`:
  exit 2, "the CRS names the grid(s) nosuchfile.gsb and this host does not hold them". An
  optional `@name` grid is judged by the probe, as in the bullets above.
- A control row with longitude and latitude swapped is refused when the source or target CRS has a
  bounded area, such as `EPSG:4269` or `EPSG:2236`. With world-wide CRS it is scored. Measured with
  `--from EPSG:4326 --to EPSG:3857` and the row `29,-82,-82,29`: worst miss 56,667,252 ftUS, exit 0
  without `--max-miss`. Pass `--max-miss` in any gate.
- The figures for the 310 geographic CRS pairs, the 200-row timing and the failure counts with
  grids in the PROJ data directory come from one-off runs. They are not part of `--self-test`.
- A probe exactly on the edge of the area-of-use rectangle is accepted. Measured with
  `--from EPSG:4267 --to EPSG:4269`: south 7.15 gives a verdict and 7.14 exits 2. The self-test
  does not pin the boundary, and a mutant of `in_area` that uses `<` instead of `<=` would pass.
- With no bounded area on either side, a latitude-first point is not refused and the verdict is
  marked "position was not checked", and the verdict line reads `OK (position not checked)`.
  Measured with two `+proj=longlat +ellps=GRS80 +no_defs`
  strings at `--point 29.2 -82`: exit 0. A gate that must catch a latitude-first point should use
  EPSG codes.
- A rectangular area-of-use box can refuse a legitimate point just outside a CRS's published box,
  such as at a UTM zone edge. The refusal is exit 2 (the tool could not tell), not a verdict.
- A control row with more than four fields is accepted, and the extra fields are ignored without a
  warning (measured, a six field row). A control file with wrong target values exits 0 unless you
  pass `--max-miss`, so always pass it in a gating run.
- The rule that only `--apply` may write holds vacuously, because the tool has no `--apply` flag. The self-test
  writes a temporary directory, and PROJ writes `cache.db` to its user directory when
  `PROJ_NETWORK=ON`. Both are described above.
- A closed stdout pipe, such as piping the report to `head`, can replace a finding code of 1 or 3
  with another code. Measured on the Windows venv with the pipe closed before the run: exit 120.
  Do not truncate the output of a gating run. Treat any exit code other than 0, 1, 3 and 4 as a
  tool failure, not as a finding.
- An operation that PROJ lists with unknown accuracy is ignored when the tool looks for a better
  missing operation. A pair could exit 0 on a known-accuracy fallback while such a grid is
  missing. I searched 310 geographic CRS pairs with `TransformerGroup` and found no real case.
- The system `python3` on the Linux host has no pyproj, so `python3 datumcheck.py --self-test` there
  exits 2 and prints no PASS lines. The 217 assertion result needs a virtual environment with pyproj.
- Branch coverage is 100 percent on both hosts, from `coverage run --branch datumcheck.py
  --self-test`: 1030 statements and 190 branches on the Windows venv (coverage 7.16.1) and the same
  1030 and 190 on Linux (coverage 7.17.0 in the `dcvenv` venv),
  with none missed or partly covered. No line is excluded.
  The network lines run against the loopback stub. All hosts run the same 217 assertions, because
  the real PROJ part runs on every host with the grids hidden.

## Sources checked

- pyproj `TransformerGroup`, `get_last_used_operation` (added in pyproj 3.4.0, needs PROJ 9.1),
  `Transformer.accuracy` (-1 when unknown) and `only_best`:
  <https://pyproj4.github.io/pyproj/stable/api/transformer.html>
- pyproj 3 wheels carry no grids, and `pyproj sync`:
  <https://pyproj4.github.io/pyproj/stable/transformation_grids.html>
- PROJ ballpark operations, grid availability in ranking, and "not necessarily the first":
  <https://proj.org/en/stable/operations/operations_computation.html>
- `projsync --file`, `--dry-run` and the default user-writable directory:
  <https://proj.org/en/stable/apps/projsync.html>
- Search order of grid directories, the Windows and Linux user-writable directory:
  <https://proj.org/en/stable/resource_files.html>
- `PROJ_NETWORK`: <https://proj.org/en/stable/usage/environmentvars.html>
- `CRS.area_of_use`, typed `AreaOfUse | None`:
  <https://pyproj4.github.io/pyproj/stable/api/crs/crs.html>, and `pyproj.aoi.AreaOfUse`, whose
  fields are `west`, `south`, `east` and `north`: <https://pyproj4.github.io/pyproj/stable/api/aoi.html>
- `proj_get_area_of_use`, the box in degrees and the unknown value -1000, and `proj_area_set_bbox`,
  whose `west_lon_degree` is greater than `east_lon_degree` for an area crossing the antimeridian,
  with longitudes in [-180,180]: <https://proj.org/en/stable/development/reference/functions.html>
- `CRS.to_epsg()`, the EPSG code best matching a CRS, with `min_confidence` 70 by default:
  <https://pyproj4.github.io/pyproj/stable/api/crs/crs.html>
- `proj_trans()` compares the coordinate with the area of use of operations, and the ballpark
  operation covers "World": <https://proj.org/en/stable/operations/operations_computation.html>
- `Grid.available` of an operation's grids:
  <https://pyproj4.github.io/pyproj/stable/api/crs/coordinate_operation.html>
- PROJ 9.2.0 release notes, NADCON5 and `ONLY_BEST`: <https://proj.org/en/stable/news.html>
- Optional grids, the `@` prefix:
  <https://proj.org/en/stable/operations/transformations/hgridshift.html> and
  <https://proj.org/en/stable/usage/transformation.html>
- `PROJ_USER_WRITABLE_DIRECTORY`, a code comment "For testing purposes only" in
  `proj_context_get_user_writable_directory`:
  <https://github.com/OSGeo/PROJ/blob/9.8/src/filemanager.cpp>
- `only_best_default` in `proj.ini` and the `PROJ_ONLY_BEST_DEFAULT` environment variable (added in
  PROJ 9.2): <https://proj.org/en/stable/resource_files.html>
- Background on the US survey foot (the 1200/3937 ratio is checked against `proj.db` by the self-test):
  <https://www.federalregister.gov/documents/2019/10/17/2019-22414/deprecation-of-the-united-states-us-survey-foot>
- The acre, 43,560 square feet: <https://en.wikipedia.org/wiki/Acre>

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Related

Other single-file tools in this portfolio that pair with this one:

- [idreuse](https://github.com/uhsear/idreuse) - reconciles two layers and expects both in one projected CRS; run datumcheck on the move into that CRS first
- [xydrift](https://github.com/uhsear/xydrift) - names rows whose stored X and Y disagree with their geometry; a datum change can cause exactly that
- [safe-republish](https://github.com/uhsear/safe-republish) - refuses a truncate-and-append when the staged copy fails a plausibility check
