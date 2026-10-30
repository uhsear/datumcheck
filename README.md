# datumcheck

Exit non-zero when PROJ moves your data with a null-shift operation. Report the error in US survey
feet and in acres.

A nightly job on a Linux server reprojects a parcel layer from NAD83 to NAD83(2011) State Plane.
The Python there came from `pip install pyproj`. The job logs no warning and exits 0. The output
looks right on a map.

It is not right. The pip wheel carries no datum grids, so PROJ could not use the operation that
needs one. It fell back to a zero translation, or to a "ballpark" offset that moves nothing.
Every point landed about 1.6 feet from where the grid operation puts it. In the same run PROJ
used two different fallbacks for two parts of the same layer. Older data is worse. A NAD27 layer
that falls back lands 12 feet off, and a 640 acre section that moves 12 feet claims 1.5 acres
that belong to its neighbours. Nobody sees this, because no step in the pipeline compares the
result with anything.

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
PASS  a prefix --hel of --help is refused  <-- pinned defect
...
PASS  NAD83 to NAD83(2011) without grids is a null shift and exits 1
...
PASS  a default probe that falls offshore on a ballpark offset exits 2 and asks for --point instead of failing a good host  <-- pinned defect
...
PASS  Oahu alone is degraded, Nihoa alone is clean, and the pair exits 3 in either order  <-- pinned defect
...
PASS  a proj4 NAD27 definition whose optional grids are missing does not move the point and exits 1  <-- pinned defect
...
PASS  Florida West to North without grids is degraded and exits 3
...
PASS  a control file 30 ft east and 40 ft north reports a 50.00 ftUS miss and 0.3078 acres
...
PASS  a host without the grids refuses to emit control values and writes nothing  <-- pinned defect
...
--------------------------------------------------------------------
161 assertions, 0 failed
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
  With pyproj 3.8.0 and PROJ 9.8.1 and no grids, a plain call returned `(inf, inf)` and
  `transform(..., errcheck=True)` raised `ProjError` ("No operation matching criteria found"). The
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

Python 3.9 or newer, and `pyproj` 3.4 or newer with PROJ 9.1 or newer. 3.4 is the first pyproj with
`Transformer.get_last_used_operation`. The tool exits 2 and says so when it is missing or too old.

`--self-test` also needs pyproj, because half of it runs real PROJ. It needs pyproj 3.5 and PROJ
9.2 or newer, because it passes `only_best` and expects NADCON5 operations in `proj.db`. On an
older stack it prints what it needs and exits 2. The check itself still runs there.

```
python -m pip install pyproj
git clone https://github.com/uhsear/datumcheck.git
```

Tested on:

| Host | Python | pyproj | PROJ | Self-test |
|---|---|---|---|---|
| Windows 11 | 3.13.2 (venv) | 3.8.0 | 9.8.1 | 161 assertions, 0 failed |
| Windows 11 | 3.13 (`C:\Python313`) | 3.7.2 | 9.5.1 | 161 assertions, 0 failed |
| Windows 11 | 3.9.25 (venv) | 3.6.1 | 9.3.0 | 161 assertions, 0 failed |
| Windows 11 | 3.9.25 (venv) | 3.5.0 | 9.2.0 | 161 assertions, 0 failed |
| Windows 11 | 3.9.25 (venv) | 3.4.0 | 9.1.0 | refused, exit 2; the check runs |
| Linux (Ubuntu) | 3.12.3 (venv) | 3.8.0 | 9.8.1 | 161 assertions, 0 failed |

The count is the same on every host that runs the self-test. The pyproj 3.4.0 row ran
`--from EPSG:2236 --to EPSG:6438 --point 600000 1200000` and exited 1, the same as on the others.

## Quick start

```
python datumcheck.py --self-test
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --point 600000 1200000
```

## Usage

The check writes no file, and nothing in this tool changes your data, so there is no `--apply`.
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
| `--point X Y` | centre of both areas of use | A probe point in source CRS units. Two numbers with a space between. Repeat the flag for more points. Pass it for any real check: the default can lie offshore (see Limits). |
| `--control CSV` | none | Known-good coordinates. Columns `sx,sy,tx,ty`. Adds the miss in feet and acres. The `sx,sy` values are the probes when you pass no `--point`. |
| `--emit-control CSV` | none | Read points (columns `sx,sy`) and print control rows to standard output. Refuses unless this host used the best available operation (exit 0). |
| `--parcel-acres N` | `1.0` | Parcel size for the acres figure. |
| `--self-test` | off | Run the assertions and exit. |

Write a west longitude as two words: `--point -82.0 29.2`. The form `--point -82.0,29.2` is a usage
error, because argparse reads a leading minus sign as a flag. The self-test pins this.

Every parser sets `allow_abbrev=False`. `--hel`, `--emit`, `--cont`, `--po`, `--par` and `--fr`
are all refused with exit 64.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | PROJ used a real operation at every probe. |
| 1 | PROJ used a null-shift operation at one probe or more, or skipped a missing optional grid (below). `--self-test` also exits 1 when an assertion fails. |
| 2 | The check could not run: pyproj missing or old, unknown CRS, unreadable or bad CSV, a point PROJ cannot transform, or a default probe that fell on a ballpark offset (pass `--point`). |
| 3 | The operation is not a null shift, but a more accurate one needs grids this host lacks. |
| 64 | Usage error, including an abbreviated or unknown flag. |
| 70 | A fault in the tool. It is never a finding about your data. |

A Python crash before the tool starts, such as an interpreter too old to compile the file, also
exits 1. Read the output text, or run `--self-test` as a separate step.

If a probe is 1 and another is 3, the run exits 1. Code 3 is not code 1, so a gate that tests only
for 1 passes the NAD27 fallback that lands about 12 feet off. Treat any non-zero code as a failure.

## What counts as a null shift

The tool reads the operation PROJ used as PROJJSON and looks at each datum step. A step moves
nothing when:

- its name starts with `Ballpark`, which is how PROJ names an offset it made up, or
- it is a transformation whose every parameter is the number zero. `NAD83 to NAD83(2011) (1)`
  is one. It is an EPSG entry with a declared accuracy of 1 m and three zero translations.

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
`EPSG:4269` needs two missing grids and exits 3. At Nihoa the same operation name needs none and
exits 0. The self-test pins that the pair exits 3 in either order. The lookup costs about 10 ms
per probe: a 200 row file took 2.0 seconds on the Linux host.

A proj4 string can name its grids, as in `+nadgrids=@conus,@alaska`. PROJ treats a grid with a
leading `@` as optional and does not complain when it is missing
(<https://proj.org/en/stable/operations/transformations/hgridshift.html>). It applies no shift. The
step holds a file name, not a zero, so the zero test above cannot see it. When the operation holds
an optional grid, datumcheck checks whether the point moved. A point that moved less than
0.000000001 degrees is a null shift and exits 1. This check works between two geographic CRS only.
With a projected CRS on either side the run exits 2, because it cannot tell. Use EPSG codes.

A compound CRS, such as `EPSG:6438+5703`, is reduced to its horizontal part before the check. A
change of vertical unit makes PROJ merge the datum step into one step with no parameters, which
hid a zero shift. `EPSG:6438+5703` to `EPSG:4326+6360` now exits 1, the same as without the
vertical part.

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

Results for two pairs, all measured. Both use the three synthetic points above (600000 1200000,
610000 1210000 and 620000 1220000, read in each pair's own zone), and the last column adds
`--parcel-acres 640`:

| Pair | Host | Exit | Worst miss | Acres, 1 acre parcel | Acres, 640 acre parcel |
|---|---|---|---|---|---|
| NAD83 to NAD83(2011), Florida East ftUS (`2236` to `6438`) | Linux, no grids | 1 | 1.59 ftUS | 0.0096 | not run |
| NAD27 to NAD83, Florida North ftUS (`26760` to `2238`) | Linux, no grids | 3 | 12.27 ftUS | 0.0600 | 1.5198 |
| both | Windows, grids installed | 0 | 0.00 ftUS | 0.0000 | not run |

The Windows rows prove the control file is sound: with the grids, PROJ reproduces its own truth.
The NAD27 pair used a chain with a declared accuracy of 14 m and exits 3, not 1, because that
chain holds real translations.

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
  it. With the network on and an endpoint that cannot be reached (tested with `http://127.0.0.1:9`),
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
  missing. It never means that the control file agrees. With a control file the miss is reported
  but never gated: a control row 50 ft off still prints `worst miss 49.95 ftUS` and exits 0 on a
  host with the grids. This holds for a bad control file too, such as swapped columns or values
  in the wrong units. The closing `RESULT:` line does not mention the control result, so read the
  `control:` line too.
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
- Only a grid whose name starts with `@` is checked for a skipped optional grid. A proj4 string
  with `+nadgrids=null` (no `@`) moves nothing and exits 0.
- An empty `--control ""` or `--emit-control ""` value, such as an unset shell variable, is
  silently ignored. The run prints no control line and exits by the probe verdict alone.
- An operation counts as missing when any one of its grids is missing, and the report lists every
  grid of that operation under `needs`, including grids the host already has. After a partial
  `projsync` it still advises installing grids you hold. The exit code is right. Measured with 3 of
  the 4 NADCON5 conus files for `EPSG:2236` to `EPSG:6438` in an otherwise empty
  `PROJ_USER_WRITABLE_DIRECTORY`: all 4 were listed.
- The probe takes X first (longitude first for a geographic source). A latitude-first point such
  as `--point 29.2 -82` is not rejected and probes the wrong place.
- The default probe is the centre of the overlap of the two areas of use, and the report says so.
  It can lie offshore or far from your data. For `EPSG:4269` to `EPSG:6318` it is 44.815 N,
  128.115 W, in the Pacific off Oregon, where PROJ falls back to a ballpark offset even on a host
  that holds every grid. When the default probe lands on a ballpark offset and no better operation
  is listed, the run exits 2 and asks for `--point`, instead of failing a good host. A default probe
  on a declared zero shift still exits 1, because that holds anywhere. For `EPSG:4269` to
  `EPSG:4326` the default is 50.685 N, 110.365 W, in Canada. Pass `--point` for your own data.
- A probe outside the area of use of the source CRS is not rejected. The verdict describes PROJ's
  choice at that place, not at your data. `--from EPSG:2236 --to EPSG:6438 --point 5000000
  5000000` exits 1 and says that no grid can fix the pair, which is untrue for Florida.
  A latitude above 90 or a longitude above 180 is reported the same way, not as a bad input.
- `--emit-control` writes `%.6f`. On a geographic target that rounds by about 0.2 ftUS, so reading
  an emitted file back with `--control` shows a small non-zero miss (0.19 ftUS for `EPSG:6318`).
  The zero-miss round trip holds for projected targets only.
- A control file with a header column twice (`sx,sy,tx,ty,sx`) takes the last value of that column.
  A row with more cells than the header drops the extra cells. Check your header yourself.
- A geographic target measured against control values in projected units prints `worst miss nan
  ftUS` and a worst displacement of the whole parcel, and still exits 0 on a host with the grids.
  Give control values in the target CRS units, X (longitude) first.
- A cell such as `1_000` is read as 1000, because Python's `float` accepts digit-group underscores.
- `--emit-control` takes an input file of points, despite its name. In `--emit-control pts.csv >
  truth.csv` the shell truncates an existing `truth.csv` before the tool starts. If this host then
  refuses, `truth.csv` stays empty and an earlier good control file is lost. Write to a new name and
  move it when the exit code is 0. `--emit-control truth.csv > truth.csv` destroys the input and
  exits 2.
- Control, point and emit files must be UTF-8, and a byte order mark is accepted. A file in
  a legacy code page, such as cp1252 with an accented letter, stops the run with exit 2. A header
  such as `sx, sy`, with a space after the comma, stops the run with a missing-column error.
- In Windows PowerShell 5.1, `--emit-control pts.csv > truth.csv` writes UTF-16, and the next
  `--control` run exits 2 with a UTF-8 decode error on byte 0xff. Use `| Out-File -Encoding
  ascii` or run the command in cmd.exe.
- A field larger than 128 KiB in a control or point file makes the csv module raise an error that
  the tool does not catch as bad input. It exits 70, not 2.
- A fault inside the check that raises `ValueError`, `RuntimeError` or `OSError` is reported as exit
  2, so it cannot be told apart from bad input.
- An absurd `--parcel-acres`, such as `1e300`, loses precision: the area minus the overlap cancels
  and the report prints `0.0000 acres` for a miss of 49.95 ftUS.
- The whole control or emit file is read into memory, and each row costs one PROJ transform and one
  grid lookup, with no cache for a repeated probe. A 200 row file took 2.0 seconds on the Linux
  host and 2.1 seconds on the Windows venv (one run each). At that rate a 10,000 row file takes
  about 100 seconds; I did not run it.
- It does not test ArcGIS Pro, ArcGIS Server or any Esri geoprocessing. Those use their own
  transformation engine. It checks the PROJ stack that pyproj, geopandas and GDAL builds use.
- The self-test hides only the grids in PROJ's user directory, by setting
  `PROJ_USER_WRITABLE_DIRECTORY` before PROJ loads. The PROJ source marks it with the
  comment "For testing purposes only" (`src/filemanager.cpp`), and it is not on the official
  environment variable page. If a future PROJ drops
  it, the self-test fails on its isolation check instead of passing with grids present. Grids
  in PROJ's data directory stay visible. With the data directory pointed at a folder that holds
  grids (a conda install with `proj-data`), 13 assertions failed on 2026-10-29 (Linux conda
  environment, Python 3.12.14, pyproj 3.8.0, PROJ 9.8.1, 387 `.tif` files). The count follows the
  grids and the tool version. The check itself is
  unaffected.
- The self-test's `PROJ_NETWORK=ON` assertions fail when an HTTP proxy variable such as
  `http_proxy` is set without a 127.0.0.1 `no_proxy` entry, because libcurl sends the loopback
  request to the proxy. Measured with `http_proxy=http://127.0.0.1:9`: 2 assertions failed.
- `only_best` behaviour differs between PROJ 9.3.0, 9.5.1 and 9.8.1 (above). The self-test accepts
  all of them.
- On Linux the README commands need `python3` on a stock Ubuntu, which has no `python`, and the
  shebang `#!/usr/bin/env python` fails there unless `python-is-python3` is installed. A Windows
  checkout with `core.autocrlf=true` holds CRLF line endings, which break the shebang on Linux. Copy
  the file from git, or run `python3 datumcheck.py`.
- The Oahu pair (`EPSG:4152` to `EPSG:4269`) exits 3 only on a host whose PROJ user directory lacks
  `us_noaa_hihpgn.tif`. With that grid installed, the same probe exits 1 on a Ballpark step.
- Branch coverage is 100 percent (798 statements, 140 branches, coverage 7.16.1 on the Windows
  venv), from `coverage run --branch datumcheck.py --self-test`. No line is excluded. The network
  lines run against the loopback stub. The Windows venv, `C:\Python313` and Linux all run the same
  161 assertions, because the real PROJ part runs on every host with the grids hidden. Linux
  coverage was not measured.

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
