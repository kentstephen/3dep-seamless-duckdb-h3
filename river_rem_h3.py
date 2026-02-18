# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3==4.4.2",
#     "lonboard==0.13.0",
#     "marimo",
#     "matplotlib==3.10.8",
#     "morecantile==7.0.3",
#     "numpy==2.4.2",
#     "odc==0.1.3",
#     "odc-stac==0.5.0",
#     "opt-einsum",
#     "palettable==3.3.3",
#     "planetary-computer==1.0.0",
#     "pyarrow==18.1.0",
#     "pygeoutils",
#     "pynhd",
#     "pyproj==3.7.2",
#     "pystac-client==0.8.6",
#     "scipy==1.17.0",
#     "shapely==2.1.2",
#     "sqlglot",
# ]
# ///

import marimo

__generated_with = "0.19.11"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md(r"""
    # River REM → H3 Hexagons

    Relative Elevation Model pipeline: query 3DEP DEM via STAC (Planetary Computer) →
    get NHDPlus flowlines via `pynhd` → IDW-interpolate river surface →
    subtract to get height-above-river → aggregate to H3 hexagons via DuckDB →
    render with lonboard + custom cubehelix colormap.

    Based on the [HyRiver REM recipe](https://docs.hyriver.io/examples/notebooks/rem.html)
    with STAC/odc-stac DEM retrieval and H3 hexagon aggregation.

    **Run with:** `uv run marimo edit river_rem_h3.py --sandbox`
    """)
    return


@app.cell
def _():
    import sys
    sys.path.insert(0, "lib")

    import numpy as np
    import duckdb
    import h3
    import marimo as mo
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from matplotlib.colors import Normalize
    from pyproj import Transformer
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.colormap import apply_continuous_cmap
    from lonboard.basemap import CartoBasemap, MaplibreBasemap
    from lonboard.controls import FullscreenControl, NavigationControl, ScaleControl

    from pipeline import calculate_resolution_for_h3, query_stac, get_tiles, install_h3
    from h3_aggregation import aggregate_to_h3
    from rem import get_flowlines, sample_river_elevation, compute_rem

    install_h3()

    import warnings
    warnings.filterwarnings("ignore", message="Dataset has no geotransform", category=UserWarning)
    return (
        CartoBasemap,
        FullscreenControl,
        H3HexagonLayer,
        Map,
        MaplibreBasemap,
        NavigationControl,
        Normalize,
        ScaleControl,
        Table,
        ThreadPoolExecutor,
        Transformer,
        aggregate_to_h3,
        apply_continuous_cmap,
        as_completed,
        calculate_resolution_for_h3,
        compute_rem,
        get_flowlines,
        get_tiles,
        h3,
        mo,
        np,
        query_stac,
        sample_river_elevation,
    )


@app.cell
def _(ThreadPoolExecutor, Transformer, as_completed, get_tiles, np):
    def load_dem_tile(tile, tms, items, band, resolution):
        """Load one tile's DEM via odc-stac. Returns xarray DataArray or None."""
        tile_bounds = tms.bounds(tile)
        tile_bbox = [tile_bounds.left, tile_bounds.bottom, tile_bounds.right, tile_bounds.top]
        try:
            import odc.stac
            ds = odc.stac.load(
                items,
                crs="EPSG:3857",
                resolution=resolution,
                bands=[band],
                bbox=tile_bbox,
            ).astype(float)
            return ds[band].max(dim="time")
        except Exception:
            return None

    def load_dem(items, bbox, band, resolution, tile_zoom=12, max_workers=4):
        """Load full DEM via tiled odc-stac reads, stitch into single xarray."""
        import xarray as xr
        tiles, tms = get_tiles(bbox, tile_zoom)
        tile_arrays = []
        completed = 0
        total = len(tiles)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(load_dem_tile, tile, tms, items, band, resolution): tile
                for tile in tiles
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    tile_arrays.append(result)
                completed += 1
                if completed % 10 == 0 or completed == total:
                    print(f"  Loaded {completed}/{total} tiles")

        if not tile_arrays:
            raise RuntimeError("No tiles produced data")

        dem = xr.combine_by_coords(tile_arrays, combine_attrs="drop_conflicts")
        if hasattr(dem, "data_vars"):
            dem = dem[list(dem.data_vars)[0]]
        print(f"DEM shape: {dem.shape}, CRS: {dem.rio.crs}")
        return dem

    return load_dem, load_dem_tile


@app.cell
def _(mo):
    mo.md(r"""
    ## Configuration

    Carson River, NV — the same area used in the
    [HyRiver REM recipe](https://docs.hyriver.io/examples/notebooks/rem.html).
    bbox from [Bounding Box Tool](https://boundingbox.klokantech.com/) in CSV format.
    """)
    return


@app.cell
def _(calculate_resolution_for_h3):
    # Carson River, NV (from HyRiver REM example)
    # bbox = (-119.59, 39.24, -119.47, 39.30)  # original
    # bbox = (-119.61, 39.22, -119.45, 39.32)  # slightly wider for floodplain context
    # bbox = (-90.629396,32.803845,-90.213597,33.21969) # Yazoo city
    # bbox = (-88.058075,30.668152,-87.843363,31.150244) # Mobile AL delta
    # bbox = (-123.236629,44.573999,-123.122856,44.653502) # Willamette between Corvalis and Albany OR -- missing data in 3dep in mpsc
    #mobile AL delta didnt work with python 3dep
    bbox = (-88.011768,30.807783,-87.854518,31.004204)
    COLLECTION = "3dep-seamless"
    BAND = "data"
    H3_RES = 11
    NATIVE_RESOLUTION = 10
    RESOLUTION = calculate_resolution_for_h3(H3_RES, NATIVE_RESOLUTION)
    TILE_ZOOM = 14
    MAX_WORKERS = 8
    return BAND, COLLECTION, H3_RES, MAX_WORKERS, RESOLUTION, TILE_ZOOM, bbox


@app.cell
def _(
    BAND,
    COLLECTION,
    H3_RES,
    MAX_WORKERS,
    RESOLUTION,
    TILE_ZOOM,
    Table,
    aggregate_to_h3,
    bbox,
    compute_rem,
    get_flowlines,
    load_dem,
    query_stac,
    sample_river_elevation,
):
    items = query_stac(bbox, COLLECTION)
    dem = load_dem(items, bbox, BAND, RESOLUTION, tile_zoom=TILE_ZOOM, max_workers=MAX_WORKERS)
    river_line = get_flowlines(bbox, dem.rio.crs)
    river_elev = sample_river_elevation(dem, river_line)
    _rem = compute_rem(dem, river_elev)
    hex_result = aggregate_to_h3(_rem, H3_RES, value_column="rem")

    table = Table.from_arrow(hex_result)
    del hex_result
    return (table,)


@app.cell
def _(
    CartoBasemap,
    FullscreenControl,
    H3HexagonLayer,
    Map,
    MaplibreBasemap,
    NavigationControl,
    Normalize,
    ScaleControl,
    apply_continuous_cmap,
    bbox,
    mo,
    np,
    table,
):
    from palettable.scientific.sequential import Bamako_20, Bamako_20_r, Imola_20, Imola_20_r, LaJolla_20, LaJolla_20_r, Tokyo_20, Tokyo_20_r
    from palettable.matplotlib import Viridis_20, Viridis_20_r, Inferno_20, Inferno_20_r
    from palettable.cartocolors.sequential import Emrld_7, Emrld_7_r
    from palettable.cmocean.sequential import Solar_20, Solar_20_r, Dense_20, Dense_20_r, Deep_20, Deep_20_r, Haline_20, Haline_20_r
    from palettable.lightbartlein.sequential import Blues10_10, Blues10_10_r

    cmap_dropdown = mo.ui.dropdown(
        options={
            "LaJolla": LaJolla_20,
            "LaJolla (reversed)": LaJolla_20_r,
            "Dense": Dense_20,
            "Dense r": Dense_20_r,
            "Blues 10": Blues10_10,
            "Blues 10r": Blues10_10_r,
            "Deep": Deep_20,
            "Deep r": Deep_20_r,
            "Haline": Haline_20,
            "Haline (reversed)": Haline_20_r,
            "Bamako": Bamako_20,
            "Bamako (reversed)": Bamako_20_r,
            "Imola": Imola_20,
            "Imola (reversed)": Imola_20_r,
            "Viridis": Viridis_20,
            "Viridis (reversed)": Viridis_20_r,
            "Inferno": Inferno_20,
            "Inferno (reversed)": Inferno_20_r,
            "Solar": Solar_20,
            "Solar (reversed)": Solar_20_r,
            "Tokyo": Tokyo_20,
            "Tokyo (reversed)": Tokyo_20_r,
            "Emrld": Emrld_7,
            "Emrld (reversed)": Emrld_7_r,
        },
        value="LaJolla",
        label="Colormap",
    )
    elevation_scale_input = mo.ui.number(
        start=0.1, stop=50.0, step=0.5, value=5.0, label="Elevation Scale"
    )
    opacity_input = mo.ui.number(
        start=0.0, stop=1.0, step=0.05, value=0.9, label="Opacity"
    )
    rem_max_input = mo.ui.number(
        start=1.0, stop=100.0, step=1.0, value=15.0, label="REM Max (m)"
    )
    extruded_toggle = mo.ui.switch(value=False, label="Extruded")

    _elev_values = np.array(table["metric"].to_pylist())
    _clipped = np.clip(_elev_values, 0, 15.0)
    _normalizer = Normalize(0, 15.0)
    _colors = apply_continuous_cmap(_normalizer(_clipped), LaJolla_20, alpha=1)

    layer = H3HexagonLayer(
        table=table,
        get_hexagon=table["hex"],
        get_fill_color=_colors,
        high_precision=True,
        stroked=False,
        get_elevation=_clipped,
        extruded=False,
        elevation_scale=5.0,
        opacity=0.9,
    )
    lng = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    fullscreen = FullscreenControl(position="top-right")
    nav = NavigationControl()
    view_state = {
        "longitude": lng,
        "latitude": lat,
        "zoom": 12,
        "pitch": 45,
        "bearing": 0,
    }

    m = Map(layers=[layer], view_state=view_state, basemap=MaplibreBasemap(style=CartoBasemap.DarkMatterNoLabels), controls=[fullscreen, nav, ScaleControl()])

    _layer_controls = mo.hstack([cmap_dropdown, elevation_scale_input, opacity_input, rem_max_input, extruded_toggle], justify="start", gap=0.5)
    mo.vstack([m, _layer_controls])
    return (
        cmap_dropdown,
        elevation_scale_input,
        extruded_toggle,
        layer,
        opacity_input,
        rem_max_input,
    )


@app.cell
def _(
    Normalize,
    apply_continuous_cmap,
    cmap_dropdown,
    layer,
    np,
    rem_max_input,
    table,
):
    # Colormap + REM clip updates — re-runs when cmap_dropdown or rem_max_input changes
    _elev_values = np.array(table["metric"].to_pylist())
    _clipped = np.clip(_elev_values, 0, rem_max_input.value)
    _normalizer = Normalize(0, rem_max_input.value)
    layer.get_fill_color = apply_continuous_cmap(_normalizer(_clipped), cmap_dropdown.value, alpha=1)
    layer.get_elevation = _clipped
    return


@app.cell
def _(elevation_scale_input, extruded_toggle, layer, opacity_input):
    # Scalar trait updates only — instant, no array recomputation
    layer.elevation_scale = elevation_scale_input.value
    layer.opacity = opacity_input.value
    layer.extruded = extruded_toggle.value
    return


if __name__ == "__main__":
    app.run()
