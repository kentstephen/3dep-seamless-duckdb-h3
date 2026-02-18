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

DuckDB extensions: install once globally (`duckdb.sql("INSTALL h3 FROM community")`), then each worker connection only does `LOAD h3`. See `get_con()` pattern in `lib/pipeline.py`.

### Shared Modules (`lib/`)

Shared pipeline functions live in `lib/` to avoid copy-paste across notebooks:

- **`lib/pipeline.py`** — STAC query, morecantile tiling, concurrent tile processing (`calculate_resolution_for_h3`, `query_stac`, `get_tiles`, `install_h3`, `get_con`, `process_tile_to_h3`, `process_all_tiles`). All heavy imports inside function bodies.
- **`lib/h3_aggregation.py`** — `aggregate_to_h3(data_array, h3_res, value_column, memory_limit)` — flattens xarray DataArray to lat/lng/value, aggregates to H3 via DuckDB. Handles CRS detection/reprojection. Replaces both `aggregate_to_h3` and `aggregate_rem_to_h3`.
- **`lib/rem.py`** — `get_flowlines`, `sample_river_elevation`, `compute_rem`. HyRiver-style IDW REM with UTM projection fix for smoothing.

Import pattern (notebooks add `lib/` to sys.path):
```python
import sys
sys.path.insert(0, "lib")
from pipeline import calculate_resolution_for_h3, query_stac, get_tiles, install_h3, process_all_tiles
```

### Responsive Map Controls (Split Trait Cells)

Trait update cells are split into two for responsive controls:
- **Cell A (colormap)** — re-runs when `cmap_dropdown` (or `rem_max_input` for REM) changes. Recomputes the color array.
- **Cell B (scalar traits)** — re-runs when `elevation_scale_input`, `opacity_input`, or `extruded_toggle` changes. Instant, no array recomputation.

This prevents the expensive colormap recomputation when you just want to tweak elevation scale or opacity.

### Resolution / H3 Relationship

`calculate_resolution_for_h3(h3_res, native_resolution, pixels_per_hex_edge=6)` computes the odc-stac pixel resolution to get ~6 pixels per H3 hex edge. For COGs, coarser resolution reads smaller internal overviews (less I/O). The resolution is clamped to multiples of native_resolution (10m for 3DEP). Key combos:
- H3 res 9 + 30m → 6.7 px/edge (COG 3x overview, fast)
- H3 res 10 + 10m → 7.6 px/edge (native read, good detail)
- H3 res 12 + 10m → 1.1 px/edge (near 1:1 pixel-to-hex, max detail but minimal aggregation)

## Notebooks

- **`river_rem_s3dep.py`** - River REM notebook using `seamless-3dep` (USGS National Map) instead of Planetary Computer STAC. Full 10m CONUS coverage without gaps. Default bbox: Willamette River, OR. Branch: `feature/river-rem-s3dep`.
- **`river_rem_h3.py`** - Original River REM notebook using Planetary Computer STAC + odc-stac. Branch: `feature/river-rem-hyriver`.
- **`elevation_1m.py`** - 1m DEM → H3 hexagons via `seamless-3dep` `get_map(res=1)`. Branch: `feature/river-rem-1m`.
- **`elevation_h3_clean_with_fused_census.py`** - Elevation + Fused census H3 join. Two layers, different colormaps. Queries Source Coop Parquet via DuckDB.

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

### Fused Census + H3 Elevation Joins
- **Goal**: Join Fused H3 census data (res 7 or 8) from Source Coop with elevation H3 hexes. Two layers, different colormaps — elevation + population/demographics side by side.
- **Data**: Fused publishes pre-aggregated US Census data at H3 resolution on Source Coop as Parquet. Query directly with DuckDB: `SELECT * FROM read_parquet('s3://source-coop/fused/...')` or via HTTPS URL.
- **Join**: Simple H3 index join — elevation hexes may be finer resolution (res 10-12), so either re-aggregate elevation to res 7/8 to match census, or use `h3_cell_to_parent()` in DuckDB.
- **Colormaps**: Expand colormap options — add more scientific/sequential cmaps (cmocean, cubehelix variants) to both this notebook and the 1m elevation notebook.
- **Notebook**: `elevation_h3_clean_with_fused_census.py` — extends `elevation_h3_clean.py` with a second DuckDB query for census data and a second H3HexagonLayer.

### Overture + H3 Elevation Joins
- Use DEM-derived H3 elevation as a lightweight alternative to lidar — easier to acquire, covers CONUS via 3DEP seamless
- `h3_polygon_wkt_to_cells_experimental` in DuckDB for polygon-to-H3 polyfill (buildings, road buffers)
- See `refrences/overture_core.py` for Overture data loading patterns (obstore + geoarrow-rust + GeoParquet)

#### Overture Road Segments (`elevation_h3_overture_roads.py`)
- Filter to **motorways** (not just highways) — 3D motorways with the DEM looks insane
- 100m buffer is far too wide for this AOI — reduce significantly
- The trick with roads is **elevation + variance** — both are natural H3 aggregation stats (segment polyfill → H3 → join with DEM elevation)
- Layer setup modeled on `refrences/landsat_vegetation_change_h3.ipynb` with toggleable layers:
  1. Elevation only (full DEM H3)
  2. Roads as solid color (e.g. yellow) over elevation
  3. Roads colored by elevation cmap
  4. Roads colored by variance cmap
- Aspiration ref: `Screenshot 2025-05-01 at 9.05.18 PM.png` — S2 hexagons with Overture motorways

#### Overture Buildings
- Ref: `Screenshot 2025-07-18 at 11.49.32 AM.jpg` — flat buildings with vivid color
- **H3 polyfill join**: `h3_polygon_wkt_to_cells` with 'center' or 'overlap' mode to polyfill building footprints → join with elevation H3 → smooth color gradient across buildings
- **Geometry overlay**: Keep actual Overture building polygons, join elevation via H3, render as SolidPolygonLayer on top of H3HexagonLayer — preserves building shapes
- **3D extrusion**: Use Overture `height` attribute to extrude buildings off the extruded H3 elevation surface — mixed results on Fused.io with their deck.gl, worth testing in lonboard. Flat color is more vivid; 3D is the stretch goal

#### Sentinel-2 + DEM (future)
- S2 broken into hexagons with Overture motorways — the original aspiration from the screenshot
- More of an LPC thing; the DEM version is current focus

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

### H3 Rendering Quality at High Resolution
- **Problem**: Visual distortion/artifacts on H3 hexagons at res 12+ with extruded rendering, especially with pitch. `high_precision=True` helps but doesn't fully resolve it.
- **Tested**: `use_device_pixels=2.0`, `parameters={"depthTest": True, "blend": True}` — marginal improvement
- **Root cause**: lonboard 0.13 wraps deck.gl's `H3HexagonLayer` which uses instanced drawing. At high res with many hexes, GPU precision limits and instanced approximations cause artifacts. Fused (which Kyle Barron also built) renders cleaner — likely newer deck.gl build or different H3 implementation.
- **`parameters` prop**: Passes GPU settings to luma.gl — can set `depthTest`, `blend`, etc. Antialiasing options limited by WebGL context.
- **`coverage`**: Setting to 0.95 adds tiny gaps between hexes, can reduce visual overlap artifacts.
- **TODO**: Watch lonboard releases for deck.gl version bumps. Consider filing issue with Kyle.

### Lonboard Map Controls in Marimo
- **Problem**: lonboard's default `controls=(FullscreenControl(), NavigationControl(), ScaleControl())` don't appear in marimo unless explicitly passed. When we added `controls=[FullscreenControl()]` we lost `NavigationControl` (compass, zoom, pitch reset / "go flat") and `ScaleControl` (scale bar).
- **Fix**: Always pass all three explicitly: `controls=[FullscreenControl(position="top-right"), NavigationControl(), ScaleControl()]`
- **Import**: `from lonboard.controls import FullscreenControl, NavigationControl, ScaleControl`
- **`NavigationControl` options**: `show_compass=True`, `show_zoom=True`, `visualize_pitch=True` (all default True)
- **Applies to all notebooks** — every notebook currently only passes `[fullscreen]`

### Bbox Picker Tool (TODO)
- Build a bbox picker as a marimo cell or standalone HTML — replace dependence on finicky boundingbox.klokantech.com
- Could use lonboard's `selected_bounds` draw interaction — user draws rectangle on map, outputs `(west, south, east, north)` tuple
- Or minimal standalone: Leaflet + draw control + text box, ~50 lines HTML

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
- **Known issue**: USGS ArcGIS endpoint times out on larger bboxes or under load. `seamless-3dep` uses `tiny_retriever` which fires concurrent requests with no retry. Connection timeouts are common for ~0.2degx0.2deg bboxes at 1m.
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
