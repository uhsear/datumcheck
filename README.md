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
that falls back lands 11 feet off, and a 640 acre section that moves 11 feet claims 1.6 acres
that belong to its neighbours. Nobody sees this, because no step in the pipeline compares the
result with anything.

This tool asks PROJ which operation it used for real points, and refuses when that operation
shifts nothing. Give it a control file of known-good coordinates and it also says how far off the
result is.

```
$ python datumcheck.py --self-test
datumcheck self-test: temporary files only, no network, no credentials
--------------------------------------------------------------------
PASS  1200/3937 of a metre is exactly one US survey foot
PASS  one metre is 3.2808333 US survey feet
...
PASS  a zero offset of accuracy 0 is the same datum and is not flagged  <-- pinned defect
...
PASS  a prefix --hel of --help is refused  <-- pinned defect
...
PASS  NAD83 to NAD83(2011) without grids is a null shift and exits 1
...
PASS  Florida West to North without grids is degraded and exits 3
...
PASS  a control file 30 ft east and 40 ft north reports a 50.00 ftUS miss and 0.3078 acres
...
PASS  a host without the grids refuses to emit control values and writes nothing  <-- pinned defect
...
--------------------------------------------------------------------
103 assertions, 0 failed
```

## What already exists

PROJ and pyproj can already tell you most of this. They do the hard part, which is the fetching.

- `pyproj.transformer.TransformerGroup` lists the candidate operations. Its `best_available`
  attribute is `False` when the best operation needs a missing grid, and `unavailable_operations`
  names them.
- `Transformer.from_crs(..., only_best=True)` asks PROJ to fail instead of using a lesser
  operation. It needs PROJ 9.2 or newer.
- `projsync` and `pyproj sync` download the grids. `PROJ_NETWORK=ON` lets PROJ read them from the
  PROJ CDN as it needs them.
- `OSGeo/PROJ-data` on GitHub holds the grids themselves.

What they do not do, as far as I could test:

- `TransformerGroup` is not the operation PROJ used. On a host with no grids it listed one
  candidate, a ballpark offset with unknown accuracy. `Transformer.from_crs` then used a different
  operation, a chain through WGS 84 with a declared accuracy of 5 m. Run on the Linux host below:

  ```
  best_available: False
  group[0]: Inverse of SPCS83 Florida West zone (US survey foot) + Ballpark geographic  ...
  from_crs used: Inverse of SPCS83 Florida West zone (US survey foot) + NAD83 to WGS 84 (1)  ...
  accuracy: group[0] -1.0 m, used 5.0 m
  ```

  datumcheck reads the operation from `Transformer.get_last_used_operation()`, after it has
  transformed a real point.
- `only_best=True` does not give you an exit code. With pyproj 3.8.0 and PROJ 9.8.1 the call
  returned `(inf, inf)` and raised nothing. With pyproj 3.7.2 and PROJ 9.5.1 it returned the same
  fallback coordinate as the default call. You must test the result yourself.
- None of them state the miss in US survey feet or acres. The numbers need ground truth, and PROJ
  has none on a host that lacks the grids.

I searched GitHub with `gh search repos` for `datumcheck`, `projsync`, `ballpark`, `nadcon`,
`datum-shift` and `proj-data`. No repository named `datumcheck` came back. The closest were
`OSGeo/PROJ-data` (the grids) and `nc5ng/nadcon5-ng` (a NADCON5 tool). Neither gates a pipeline.

## Requirements

Python 3.9 or newer, and `pyproj` 3.4 or newer with PROJ 9.1 or newer. 3.4 is the first pyproj with
`Transformer.get_last_used_operation`. The tool exits 2 and says so when it is missing or too old.

`--self-test` also needs pyproj, because half of it runs real PROJ.

```
python -m pip install pyproj
git clone https://github.com/uhsear/datumcheck.git
```

Tested on:

| Host | Python | pyproj | PROJ | Self-test |
|---|---|---|---|---|
| Windows 11 | 3.13.2 (venv) | 3.8.0 | 9.8.1 | 103 assertions, 0 failed |
| Windows 11 | 3.13 (`C:\Python313`) | 3.7.2 | 9.5.1 | 103 assertions, 0 failed |
| Linux (Ubuntu) | 3.12.3 (venv) | 3.8.0 | 9.8.1 | 103 assertions, 0 failed |

The count is the same on all three. Python 3.9 was used only to compile the file with
`python -W error`; it has no pyproj, so the self-test did not run there.

## Quick start

```
python datumcheck.py --self-test
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --point 600000 1200000
```

## Usage

Nothing in this tool writes a file. There is no `--apply`.

```
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --control truth.csv
python datumcheck.py --from EPSG:2236 --to EPSG:6438 --emit-control points.csv > truth.csv
```

| Flag | Default | What it does |
|---|---|---|
| `--from CRS` | none | Source CRS, anything pyproj reads, such as `EPSG:2236`. Required. |
| `--to CRS` | none | Target CRS. Required. |
| `--point X Y` | centre of both areas of use | A probe point in source CRS units. Two numbers with a space between. Repeat the flag for more points. |
| `--control CSV` | none | Known-good coordinates. Columns `sx,sy,tx,ty`. Adds the miss in feet and acres. The `sx,sy` values are the probes when you pass no `--point`. |
| `--emit-control CSV` | none | Read points (columns `sx,sy`) and print control rows to standard output. Refuses unless this host used a full-accuracy operation. |
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
| 1 | PROJ used a null-shift operation at one probe or more. |
| 2 | The check could not run: pyproj missing or old, unknown CRS, unreadable or bad CSV, or a point PROJ cannot transform. |
| 3 | The operation is not a null shift, but a more accurate one needs grids this host lacks. |
| 64 | Usage error, including an abbreviated or unknown flag. |
| 70 | A fault in the tool. It is never a finding about your data. |

If a probe is 1 and another is 3, the run exits 1.

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
One lookup of the unavailable operations runs for each distinct operation, around the first probe
that used it, in a box of 0.01 degrees.

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

Results for two pairs, all on synthetic points, all measured:

| Pair | Host | Exit | Worst miss | Acres, 1 acre parcel | Acres, 640 acre parcel |
|---|---|---|---|---|---|
| NAD83 to NAD83(2011), Florida East ftUS (`2236` to `6438`) | Linux, no grids | 1 | 1.59 ftUS | 0.0096 | not run |
| NAD27 to NAD83, Florida North ftUS (`26760` to `2238`) | Linux, no grids | 3 | 10.83 ftUS | 0.0630 | 1.6100 |
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
  2 parts in a million.
- Displaced acres is the part of a square parcel of the given size that no longer overlaps its true
  position. A move of one side or more displaces the whole parcel. It is a measure of the ground
  now claimed wrongly. It is not the change in a parcel's computed area, which a rigid shift does
  not alter.

## Limits

- Probes are samples. A layer that spans two areas of use can use a different operation between
  your probes. Pass `--point` for each corner, or pass a control file with a spread of points.
- The default probe is the centre of the overlap of the two CRS areas of use. For a CRS pair that
  spans a continent it may sit far from your data.
- datumcheck makes no network call. PROJ's own network mode (`PROJ_NETWORK=ON`) is outside it and
  was not exercised here. If you enable it, PROJ may fetch grids and the verdict will change.
- Control values are only as good as their source. Control made on a host that also fell back is
  refused by `--emit-control`.
- Horizontal only. A vertical datum is not checked.
- It does not test ArcGIS Pro, ArcGIS Server or any Esri geoprocessing. Those use their own
  transformation engine. It checks the PROJ stack that pyproj, geopandas and GDAL builds use.
- The self-test hides the grids of the host by setting `PROJ_USER_WRITABLE_DIRECTORY` before PROJ
  loads. PROJ developers call this variable "for testing purposes only", and it is not on the
  official environment variable page. If a future PROJ drops it, the self-test fails on its
  isolation check instead of passing with grids present.
- `only_best` behaviour differs between PROJ 9.5.1 and 9.8.1 (above). The self-test accepts both.
- Branch coverage is 100 percent (591 statements, 120 branches) on Windows and on Linux, from
  `coverage run --branch datumcheck.py --self-test`. No line is excluded and no line needs a
  network. Windows and Linux run the same 103 assertions because the real PROJ part runs on every
  host, with the grids hidden.

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
- `PROJ_USER_WRITABLE_DIRECTORY`, "for testing purposes only":
  <https://lists.osgeo.org/pipermail/proj/2020-July/009709.html>
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
