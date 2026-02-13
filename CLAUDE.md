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

## Key Reference Files (`refrences/`)

- **`3dep_fused_udf.py`** - Primary reference for the 3DEP pipeline. Shows STAC query, DEM loading, H3 aggregation via DuckDB, and WhiteboxTools flow accumulation. Port this to Marimo.
- **`nyc_taxi_trips.py`** - Marimo notebook example. Shows reactive map interaction (bounding box selection triggers query re-execution) and H3HexagonLayer usage.
- **`new_schema_for_ept_duckdb_h3.ipynb`** - Shows mercantile tile-based parallel processing with DuckDB, two-stage H3 aggregation, and multiple lonboard layers.
- **`landsat_vegetation_change_h3.ipynb`** - Shows memory-efficient streaming (process per-year, aggregate immediately to H3), DuckDB persistence, and linked map views.

## Project Goals & TODOs

- Use `obstore` with Planetary Computer auth (https://developmentseed.org/obstore/latest/api/auth/planetary-computer/) alongside pystac
- WhiteboxTools (pywbt) flow accumulation on DEM is a future TODO
- Explore CARTO cartocolors continuous colormaps (web service API)
- Use `morecantile` (https://github.com/developmentseed/morecantile) for tile-based memory management with DuckDB + xarray
- Consider lonboard raster layer or National Map tool for coverage visualization
- Investigate Development Seed's async GeoTIFF reader (includes COG support) for async tile loading
- Future: async or concurrent.futures for parallel tile processing in `process_all_tiles`
- Keep lonboard map construction outside pipeline functions (interactive, not pipeline logic)

## Git Workflow

- Always create a new branch for every feature
- Update plans and discourse in this file when making progress
