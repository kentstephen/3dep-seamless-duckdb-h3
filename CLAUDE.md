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

## Key Reference Files (`refrences/`)

- **`3dep_fused_udf.py`** - Primary reference for the 3DEP pipeline. Shows STAC query, DEM loading, H3 aggregation via DuckDB, and WhiteboxTools flow accumulation. Port this to Marimo.
- **`nyc_taxi_trips.py`** - Marimo notebook example. Shows reactive map interaction (bounding box selection triggers query re-execution) and H3HexagonLayer usage.
- **`new_schema_for_ept_duckdb_h3.ipynb`** - Shows mercantile tile-based parallel processing with DuckDB, two-stage H3 aggregation, and multiple lonboard layers.
- **`landsat_vegetation_change_h3.ipynb`** - Shows memory-efficient streaming (process per-year, aggregate immediately to H3), DuckDB persistence, and linked map views.
- **`overture_core.py`** - Shared Overture Maps data functions (from lidar-h3-notebooks). GeoParquet loading via `obstore` + `geoarrow-rust`, geometry type splitting, lonboard layer building. Reference for Overture building/infrastructure joins.

## Project Goals & TODOs

### Overture + H3 Elevation Joins
- Join Overture buildings to H3 elevation hexes using `h3_polygon_wkt_to_cells_experimental` — convert building footprint polygons to H3 cell sets, then join on hex index to get elevation per building
- Use DEM-derived H3 elevation as a lightweight alternative to lidar — easier to acquire, covers CONUS via 3DEP seamless
- See `refrences/overture_core.py` for Overture data loading patterns (obstore + geoarrow-rust + GeoParquet)

### Memory Management
- `del hex_result` after converting to arro3 Table — avoids holding both PyArrow and arro3 copies of large datasets (40M+ hexes at res 12)
- Consider DuckDB persistent storage for large aggregations instead of in-memory Arrow tables

### Pipeline & Infra
- Use `obstore` with Planetary Computer auth (https://developmentseed.org/obstore/latest/api/auth/planetary-computer/) alongside pystac
- WhiteboxTools (pywbt) flow accumulation on DEM is a future TODO
- Explore CARTO cartocolors continuous colormaps (web service API)
- Use `morecantile` (https://github.com/developmentseed/morecantile) for tile-based memory management with DuckDB + xarray
- Consider lonboard raster layer or National Map tool for coverage visualization
- Investigate Development Seed's async GeoTIFF reader (includes COG support) for async tile loading
- ~~Future: async or concurrent.futures for parallel tile processing in `process_all_tiles`~~ Done: `elevation_h3_v3.ipynb` uses `ThreadPoolExecutor` with configurable `MAX_WORKERS`
- Keep lonboard map construction outside pipeline functions (interactive, not pipeline logic)

## Git Workflow

- Always create a new branch for every feature
- Update plans and discourse in this file when making progress
