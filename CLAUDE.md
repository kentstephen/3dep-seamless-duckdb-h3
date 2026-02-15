# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Marimo notebook project for visualizing USGS 3DEP seamless elevation data as extruded H3 hexagons. The pipeline: query raster DEM via STAC (Planetary Computer) -> process with xarray -> aggregate to H3 hexagons via DuckDB -> render with lonboard's H3HexagonLayer.

Target data: 3DEP seamless 10m DEM with fallback to 30m when unavailable.

## Environment & Running

- **Python env managed by UV** (`.venv/`, Python 3.11)
- **Marimo notebooks** are `.py` files with PEP 723 inline script metadata:
  ```
  uv run marimo edit <notebook>.py --sandbox
  ```
- **Jupyter reference notebooks** (in `refrences/`) run via:
  ```
  uvx juv run refrences/<notebook>.ipynb
  ```
- No formal test suite, linter, or build system exists yet

## Architecture: Data Pipeline Pattern

All reference implementations follow this flow:

1. **Acquire** - STAC catalog query (Planetary Computer) -> `odc.stac.load()` for rasters
2. **Process** - xarray/dask computation (reproject, compute indices)
3. **Aggregate** - DuckDB with H3 extension: `h3_latlng_to_cell()` + `GROUP BY` with stats
4. **Visualize** - lonboard `H3HexagonLayer` with palettable colormaps, Marimo reactivity

Key libraries: `pystac-client`, `planetary-computer`, `odc-stac`, `duckdb` (H3 extension), `h3`, `lonboard`, `palettable`, `pyarrow`, `morecantile`

Arrow tables are used as the interchange format between DuckDB and lonboard (zero-copy).

DuckDB extensions: install once globally (`duckdb.sql("INSTALL h3 FROM community")`), then each worker connection only does `LOAD h3`. See `get_con()` pattern in `elevation_h3_v3.ipynb` and `refrences/new_schema_for_ept_duckdb_h3.ipynb`.

### Resolution / H3 Relationship

`calculate_resolution_for_h3(h3_res, native_resolution, pixels_per_hex_edge=6)` computes the odc-stac pixel resolution to get ~6 pixels per H3 hex edge. For COGs, coarser resolution reads smaller internal overviews (less I/O). The resolution is clamped to multiples of native_resolution (10m for 3DEP). Key combos:
- H3 res 9 + 30m → 6.7 px/edge (COG 3x overview, fast)
- H3 res 10 + 10m → 7.6 px/edge (native read, good detail)
- H3 res 12 + 10m → 1.1 px/edge (near 1:1 pixel-to-hex, max detail but minimal aggregation)

## Notebooks

- **`river_rem_s3dep.py`** - River REM notebook using `seamless-3dep` (USGS National Map) instead of Planetary Computer STAC. Full 10m CONUS coverage without gaps. Default bbox: Willamette River, OR. Branch: `feature/river-rem-s3dep`.
- **`river_rem_h3.py`** - Original River REM notebook using Planetary Computer STAC + odc-stac. Branch: `feature/river-rem-hyriver`.

## Key Reference Files (`refrences/`)

- **`3dep_fused_udf.py`** - Primary reference for the 3DEP pipeline. Shows STAC query, DEM loading, H3 aggregation via DuckDB, and WhiteboxTools flow accumulation. Port this to Marimo.
- **`nyc_taxi_trips.py`** - Marimo notebook example. Shows reactive map interaction (bounding box selection triggers query re-execution) and H3HexagonLayer usage.
- **`new_schema_for_ept_duckdb_h3.ipynb`** - Shows mercantile tile-based parallel processing with DuckDB, two-stage H3 aggregation, and multiple lonboard layers.
- **`landsat_vegetation_change_h3.ipynb`** - Shows memory-efficient streaming (process per-year, aggregate immediately to H3), DuckDB persistence, and linked map views.
- **`overture_core.py`** - Shared Overture Maps data functions (from lidar-h3-notebooks). GeoParquet loading via `obstore` + `geoarrow-rust`, geometry type splitting, lonboard layer building. Reference for Overture building/infrastructure joins.

## Project Goals & TODOs

### HIGH PRIORITY: Decouple H3 Resolution from DEM Loading
- **Problem**: Changing H3 res re-runs the entire pipeline (STAC query, tile loading, DEM assembly, REM computation) — the slow parts that don't depend on H3 res at all
- **Fix**: Separate the marimo cell graph so DEM/REM arrays are cached independently of H3 res:
  - **Cell A** (slow, runs once per bbox): bbox + collection + pixel resolution → STAC query → load DEM → compute REM. Pixel resolution should be fixed (e.g. 10m native) regardless of H3 res
  - **Cell B** (fast, re-runs on res change): H3 res as `mo.ui.number` → DuckDB H3 aggregation on cached DEM/REM arrays → produces `table`
  - **Cell C** (existing): map + colormap/layer controls
- **Key insight**: Decouple pixel resolution from H3 resolution. `calculate_resolution_for_h3` currently ties them together, but for REM work you want native DEM detail for IDW quality — H3 res only affects GROUP BY granularity
- **Applies to all notebooks** (`elevation_h3_clean.py`, `river_rem_h3.py`)

### Overture + H3 Elevation Joins
- Join Overture buildings to H3 elevation hexes using `h3_polygon_wkt_to_cells_experimental` — convert building footprint polygons to H3 cell sets, then join on hex index to get elevation per building
- Use DEM-derived H3 elevation as a lightweight alternative to lidar — easier to acquire, covers CONUS via 3DEP seamless
- See `refrences/overture_core.py` for Overture data loading patterns (obstore + geoarrow-rust + GeoParquet)

### River REMs (Relative Elevation Models)
- **Goal**: Programmatic floodplain visualization — detrend a DEM relative to the river water surface so values represent height above river level
- **HyRiver approach** (preferred): Use `pynhd` to get NHDPlus flowlines (authoritative USGS data via NLDI/WaterData), then IDW-interpolate river surface elevation across the DEM, subtract to get REM. HyRiver has a documented REM recipe: https://docs.hyriver.io/examples/notebooks/rem.html
  - `pynhd.NLDI().navigate_byid()` — get upstream/downstream flowlines from a USGS gage
  - `pynhd.WaterData("nhdflowline_network").bybox()` — flowlines by bounding box
  - `pynhd.prepare_nhdplus()` + filter by `levelpathi` for main stem
  - `pygeoutils.smooth_linestring()` — smooth jagged NHD geometries before sampling
  - `scipy.spatial.KDTree` + IDW (k=200, 1/d^2) to interpolate river surface elevation
  - `seamless-3dep` is the new lightweight replacement for `py3dep` (thread-safe connection pooling, downloads to disk as GeoTIFFs)
- **RiverREM** (alternative): OpenTopography's automated tool (https://github.com/OpenTopography/RiverREM) — fully automated but uses OSM for centerlines instead of NHDPlus. `pip install riverrem`, then `REMMaker(dem=path).make_rem()`
- **Integration**: After computing REM, aggregate *relative* elevation to H3 with existing DuckDB pipeline — REM values per hex make excellent floodplain visualizations
- **Deps to add**: `pynhd`, `pygeoutils`, `scipy`, `opt-einsum` (for efficient IDW weight computation)
- **Branch**: `feature/river-rem-hyriver` — start with HyRiver approach, bbox-based flowline query + IDW surface interpolation, feed REM values into existing H3 aggregation pipeline
- **Notebook**: `river_rem_h3.py` — Carson River, NV bbox from HyRiver example, `py3dep` for DEM, `pynhd` for flowlines, IDW REM, DuckDB H3 aggregation, cubehelix colormap

### Pre-Run Hex Count Estimation
- Before running the full pipeline, estimate the number of H3 hexagons that will be produced from a given bbox + H3 resolution as a safety check
- Use `h3.average_hexagon_area()` to compute: `estimated_hexes = bbox_area_km2 / h3.average_hexagon_area(h3_res, unit='km^2')`
- Warn if estimated count exceeds a threshold (e.g., 50M hexes) — at that scale memory pressure is real (both Arrow table and lonboard rendering)
- Could also estimate from tile count: `num_tiles * avg_hexes_per_tile` based on a calibration run

### Polygon Clipping (instead of bbox-only)
- Bounding box tools always export axis-aligned envelopes — rotated/drawn shapes become their enclosing rectangle
- **Workaround**: Load DEM with the axis-aligned bbox (required by data service), then clip to actual polygon before H3 aggregation: `dem.rio.clip([shapely_polygon], crs=4326)`
- Could add a polygon input option to notebooks (GeoJSON text input, or draw-on-map if lonboard adds drawing tools)

### Lonboard Raster Layer Option
- For users who just want a flat DEM visualization (no H3 hexagons), use lonboard's `BitmapLayer` or raster layer directly from the xarray DataArray
- Skips the entire DuckDB H3 aggregation step — much faster, lower memory
- Could offer a toggle in notebooks: "H3 hexagons" vs "Raster" visualization mode
- H3 is still needed for aggregation stats, joins (Overture buildings), and 3D extrusion — raster is just for quick previews

### Memory Management
- `del hex_result` after converting to arro3 Table — avoids holding both PyArrow and arro3 copies of large datasets (40M+ hexes at res 12)
- Consider DuckDB persistent storage for large aggregations instead of in-memory Arrow tables

### Viewport-Based Hex Filtering (elevation_h3_clean.py)
- **Goal**: Don't load every hexagon into lonboard at once — filter to what's in view
- **Pattern**: NYC taxi example (`refrences/nyc_taxi_trips.py`) uses `selected_bounds` + reactive SQL re-query
- **DuckDB filter approach**: Store full hex result in DuckDB table after pipeline, filter with `h3_cell_to_lat()`/`h3_cell_to_lng()` against viewport bounds
- **`selected_bounds` approach**: User draws box on map → fires callback → DuckDB filters hexes in bounds → layer updates. Proven pattern, discrete user action, no debounce needed
- **`view_state` approach**: Observe map `view_state` traitlet (lon/lat/zoom/pitch/bearing) for automatic viewport filtering. Challenge: fires every frame during pan/zoom, needs debounce or manual "Refresh View" button trigger
- **Re-aggregation (future idea)**: Re-aggregate to different H3 resolutions based on zoom level — the real Fused-style play. Not in scope yet, separate architecture discussion

### 1m DEM from USGS
- **Working**: `s3dep.get_map("DEM", bbox, save_dir, res=1)` pulls 1m data from USGS ArcGIS export service. Tested on Carson River NV, Snake River WY, Pittsburgh PA.
- **Known issue**: USGS ArcGIS endpoint times out on larger bboxes or under load. `seamless-3dep` uses `tiny_retriever` which fires concurrent requests with no retry. Connection timeouts are common for ~0.2°×0.2° bboxes at 1m.
- **Workaround**: Retry, or use a smaller bbox. The service is variable — same bbox may work later.
- **Better path (future)**: Direct S3 COG reads from `s3://prd-tnm/StagedProducts/Elevation/1m/Projects/` — bypasses ArcGIS entirely. Needs tile discovery (TNM API or STAC). Also watch USGS S1M (Seamless 1m) product rollout.
- **Notebook**: `elevation_1m.py` on branch `feature/river-rem-1m`

### WhiteboxTools (WBT) Integration
- Implement WBT flow accumulation / hydrological analysis on DEM
- Reference code in https://github.com/kentstephen/fused_udfs — user will locate the specific UDF when ready
- See also `refrences/3dep_fused_udf.py` which shows WBT flow accumulation pattern

### Pipeline & Infra
- Use `obstore` with Planetary Computer auth (https://developmentseed.org/obstore/latest/api/auth/planetary-computer/) alongside pystac
- WhiteboxTools (pywbt) flow accumulation on DEM is a future TODO
- Explore CARTO cartocolors continuous colormaps (web service API)
- **Cubehelix colormaps**: Perceptually uniform, monotonically increasing luminance — ideal for elevation. Mike Bostock's d3 cubehelix gist: https://gist.github.com/mbostock/11415064. Python options: `palettable.cubehelix` (already a dep) or `matplotlib.cm.cubehelix`. Cubehelix is especially good for 3D extruded hex maps where you need luminance to track elevation faithfully
- Use `morecantile` (https://github.com/developmentseed/morecantile) for tile-based memory management with DuckDB + xarray
- Consider lonboard raster layer or National Map tool for coverage visualization
- Investigate Development Seed's async GeoTIFF reader (includes COG support) for async tile loading
- ~~Future: async or concurrent.futures for parallel tile processing in `process_all_tiles`~~ Done: `elevation_h3_v3.ipynb` uses `ThreadPoolExecutor` with configurable `MAX_WORKERS`
- Keep lonboard map construction outside pipeline functions (interactive, not pipeline logic)

## Git Workflow

- Always create a new branch for every feature
- Update plans and discourse in this file when making progress

# Note from Stephen, we should look at refrences/rem.ipynb from https://github.com/hyriver/HyRiver-examples/blob/main/notebooks/ when we start up again
## also, we had this runnning with hyriver's tooling today, now it's broken again 
```DEM shape: (647, 1295), CRS: EPSG:4269
<xarray.DataArray (y: 647, x: 1295)> Size: 3MB
array([[1559.0302, 1563.4066, 1565.601 , ..., 1362.3987, 1361.4998,
        1360.7252],
       [1556.7375, 1560.5084, 1562.8693, ..., 1360.0778, 1359.2538,
        1358.2104],
       [1558.328 , 1555.6216, 1557.7102, ..., 1357.6361, 1356.847 ,
        1355.7034],
       ...,
       [1327.268 , 1327.1348, 1326.977 , ..., 1741.7252, 1740.5656,
        1739.8575],
       [1327.3658, 1327.1628, 1326.9523, ..., 1742.2513, 1741.0785,
        1740.1276],
       [1327.1184, 1326.9799, 1326.9714, ..., 1743.0371, 1742.1914,
        1741.1156]], shape=(647, 1295), dtype=float32)
Coordinates:
  * y            (y) float64 5kB 39.3 39.3 39.3 39.3 ... 39.24 39.24 39.24 39.24
  * x            (x) float64 10kB -119.6 -119.6 -119.6 ... -119.5 -119.5 -119.5
    spatial_ref  int64 8B 0
Attributes:
    AREA_OR_POINT:  Area
    scale_factor:   1.0
    add_offset:     0.0
    _FillValue:     nan
Main stem: 2 segments

Traceback (most recent call last):
  File "/var/folders/7c/sjv2kprs3qs3x738ldnw1b040000gn/T/marimo_32823/__marimo__cell_PKri_.py", line 3, in <module>
    river_line = get_flowlines(bbox, dem.rio.crs)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/var/folders/7c/sjv2kprs3qs3x738ldnw1b040000gn/T/marimo_32823/__marimo__cell_vblA_.py", line 33, in get_flowlines
    river_line = geoutils.smooth_linestring(merged, 0.1, npts)
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/stephenk/.cache/uv/archive-v0/aDw4ZvBhGsQTQHvyzsZsX/lib/python3.11/site-packages/pygeoutils/smoothing.py", line 463, in smooth_linestring
    return LineString(np.c_[spl_x(konts), spl_y(konts)])
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/stephenk/.cache/uv/archive-v0/aDw4ZvBhGsQTQHvyzsZsX/lib/python3.11/site-packages/shapely/geometry/linestring.py", line 76, in __new__
    geom = shapely.linestrings(coordinates)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/stephenk/.cache/uv/archive-v0/aDw4ZvBhGsQTQHvyzsZsX/lib/python3.11/site-packages/shapely/decorators.py", line 173, in wrapper
    result = func(*args, **kwargs)
             ^^^^^^^^^^^^^^^^^^^^^
  File "/Users/stephenk/.cache/uv/archive-v0/aDw4ZvBhGsQTQHvyzsZsX/lib/python3.11/site-packages/shapely/decorators.py", line 88, in wrapped
    return func(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^
  File "/Users/stephenk/.cache/uv/archive-v0/aDw4ZvBhGsQTQHvyzsZsX/lib/python3.11/site-packages/shapely/creation.py", line 218, in linestrings
    return lib.linestrings(coords, np.intc(handle_nan), out=out, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
shapely.errors.GEOSException: IllegalArgumentException: point array must contain 0 or >1 elements

```