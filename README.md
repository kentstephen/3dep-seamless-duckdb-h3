# 3DEP Seamless Elevation — DuckDB + H3 Hexagons

Visualize USGS 3DEP seamless elevation data as extruded H3 hexagons using DuckDB, lonboard, and Marimo notebooks.

**Status: Active development**

> [!WARNING]
> Be extremely careful with bbox size vs. H3 resolution if you want to try this. Large areas at high resolution can produce 50M+ hexagons. I am working on adding safeguards.

## What It Does

Queries 10m DEM rasters from the [USGS 3DEP Seamless](https://www.usgs.gov/3d-elevation-program) dataset via [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/), aggregates pixel elevations into H3 hexagons using DuckDB's H3 extension, and renders interactive 3D extruded hex maps with [lonboard](https://github.com/developmentseed/lonboard).

### Pipeline

```
STAC Query (Planetary Computer)
  → odc-stac raster load (COG overviews for speed)
    → DuckDB H3 aggregation (h3_latlng_to_cell + GROUP BY)
      → lonboard H3HexagonLayer (extruded 3D viz)
```

### Key Features

- **Concurrent tile processing** — `ThreadPoolExecutor` processes morecantile tiles in parallel with per-tile H3 aggregation, then merges edge hexagons
- **Resolution-aware COG reads** — automatically calculates pixel resolution to match H3 hex size, leveraging COG internal overviews for less I/O
- **Zero-copy Arrow interchange** — DuckDB outputs Arrow tables consumed directly by lonboard via `arro3`
- **DuckDB H3 extension** — all spatial binning happens in SQL (`h3_latlng_to_cell_string`, `GROUP BY`, `AVG`)
- **Fullscreen map control** — lonboard map includes a fullscreen toggle for immersive 3D exploration

## Running

Requires [uv](https://docs.astral.sh/uv/). Notebooks use PEP 723 inline script metadata so dependencies are self-contained:

```bash
uv run marimo edit elevation_h3_clean.py --sandbox
```

The Jupyter reference notebooks in `refrences/` run via:

```bash
uvx juv run refrences/<notebook>.ipynb
```

## Notebooks

| Notebook | Description |
|----------|-------------|
| `elevation_h3_clean.py` | **Marimo notebook** — concurrent H3 pipeline with reactive colormap/layer controls |
| `elevation_h3_clean.ipynb` | Jupyter version of the same pipeline |
| `elevation_h3_v3.ipynb` | Development notebook — concurrent pipeline with `ThreadPoolExecutor` |
| `elevation_h3_v2.ipynb` | Streaming H3 aggregation |
| `elevation_h3.py` | Original Marimo notebook — single-threaded H3 pipeline |

## Resolution Guide

The pipeline calculates pixel resolution to get ~6 pixels per H3 hex edge. For COGs, coarser resolution reads smaller internal overviews (less I/O):

| H3 Res | Pixel Res | px/edge | Notes |
|--------|-----------|---------|-------|
| 9 | 30m | 6.7 | COG 3x overview, fast exploration |
| 10 | 10m | 7.6 | Native resolution, good detail |
| 11 | 10m | 2.9 | High detail, more hexagons |
| 12 | 10m | 1.1 | Near 1:1 pixel-to-hex, max detail |

## Stack

- **Data access**: `pystac-client`, `planetary-computer`, `odc-stac`
- **Spatial aggregation**: `duckdb` (H3 community extension), `h3`, `morecantile`
- **Visualization**: `lonboard`, `palettable`, `matplotlib`
- **Notebooks**: `marimo`

## TODO

- [ ] **Overture building joins** — join Overture Maps building footprints to H3 elevation hexes via `h3_polygon_wkt_to_cells_experimental` for per-building elevation
- [ ] **River REMs (Relative Elevation Models)** — programmatic floodplain visualization using [HyRiver](https://docs.hyriver.io/) (`pynhd` for NHDPlus flowlines, `py3dep`/`seamless-3dep` for DEM) or [RiverREM](https://github.com/OpenTopography/RiverREM) for automated centerline-detrended elevation
- [ ] **Pre-run hex count estimation** — calculate expected H3 hexagon count from bbox + resolution before running the pipeline as a safety check (large areas at high resolution can produce 50M+ hexagons)
- [ ] **View state pitch/bearing** — lonboard `Map(view_state=...)` dict accepts pitch/bearing keys but they don't seem to apply on initial render; needs investigation
- [ ] **WhiteboxTools flow accumulation** — `pywbt` for hydrologic analysis on the DEM
