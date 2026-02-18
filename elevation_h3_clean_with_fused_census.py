# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3>=4.0.0",
#     "lonboard==0.13.0",
#     "marimo",
#     "matplotlib==3.10.8",
#     "morecantile>=1.0.0",
#     "numpy==2.2.0",
#     "odc-stac==0.5.0",
#     "palettable==3.3.3",
#     "planetary-computer==1.0.0",
#     "pyarrow==18.1.0",
#     "pyproj==3.7.2",
#     "pystac-client==0.9.0",
#     "sqlglot",
# ]
# ///

import marimo

__generated_with = "0.19.11"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md(r"""
    # 3DEP Seamless Elevation → H3 Hexagons

    Concurrent pipeline: query USGS 3DEP seamless DEM via STAC (Planetary Computer) →
    process tiles with odc-stac → aggregate to H3 hexagons via DuckDB → render with lonboard.

    **Run with:** `uv run marimo edit elevation_h3_clean.py --sandbox`
    """)
    return


@app.cell
def _():
    import sys
    sys.path.insert(0, "lib")

    import numpy as np
    import marimo as mo
    from matplotlib.colors import Normalize
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.colormap import apply_continuous_cmap
    from lonboard.basemap import CartoBasemap, MaplibreBasemap
    from lonboard.controls import FullscreenControl, NavigationControl, ScaleControl

    from pipeline import calculate_resolution_for_h3, query_stac, get_tiles, install_h3, process_all_tiles

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
        Table,
        apply_continuous_cmap,
        calculate_resolution_for_h3,
        get_tiles,
        mo,
        np,
        process_all_tiles,
        query_stac,
    )


@app.cell
def _(mo):
    mo.md(r"""
    ## Configuration

    Pick a bounding box and H3 resolution. Get bbox coordinates from
    [Bounding Box Tool](https://boundingbox.klokantech.com/) — use **CSV** format (west, south, east, north).
    """)
    return


@app.cell
def _(calculate_resolution_for_h3):
    # Mount Washington, NH — dramatic terrain, reasonable size
    bbox = [-71.502182, 44.092909, -70.723358, 44.511611]

    # Grand Canyon (large — expect ~5M+ hexes at res 11, may take several minutes)
    # bbox = [-113.0606, 35.8461, -111.7165, 36.7665]

    # Cairo, IL (Mississippi/Ohio confluence)
    # bbox = [-89.4436, 36.91, -89.0463, 37.1834]

    # Pittsfield MA
    # bbox = (-73.352437,42.391465,-73.122745,42.541736)
    # Northest MA Adams Williamstown NA
    bbox = [-73.262028,42.605008,-73.05417,42.744061]

    COLLECTION = "3dep-seamless"
    BAND = "data"
    H3_RES = 11
    NATIVE_RESOLUTION = 10
    RESOLUTION = calculate_resolution_for_h3(H3_RES, NATIVE_RESOLUTION)
    TILE_ZOOM = 12
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
    bbox,
    get_tiles,
    process_all_tiles,
    query_stac,
):
    items = query_stac(bbox, COLLECTION)
    tiles, tms = get_tiles(bbox, TILE_ZOOM)
    hex_result = process_all_tiles(items, tiles, tms, BAND, H3_RES, RESOLUTION, max_workers=MAX_WORKERS)

    table = Table.from_arrow(hex_result)
    del hex_result
    return (table,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    I'm accessing colormaps via `palettable` [you can find more here](https://jiffyclub.github.io/palettable/). You just have to follow the import path conventions, I have some examples below.
    """)
    return


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

    colormap_dropdown = mo.ui.dropdown(
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
        value="LaJolla (reversed)",
        label="Colormap",
    )
    elevation_scale_slider = mo.ui.number(
        start=0.1, stop=20.0, step=0.1, value=3.4, label="Elevation Scale"
    )
    opacity_slider = mo.ui.number(
        start=0.0, stop=1.0, step=0.05, value=0.9, label="Opacity"
    )
    extruded_toggle = mo.ui.switch(value=True, label="Extruded")

    _elev_values = np.array(table["metric"].to_pylist())
    _normalizer = Normalize(_elev_values.min(), _elev_values.max())
    _colors = apply_continuous_cmap(_normalizer(_elev_values), LaJolla_20_r, alpha=1)

    layer = H3HexagonLayer(
        table=table,
        get_hexagon=table["hex"],
        get_fill_color=_colors,
        high_precision=True,
        stroked=False,
        get_elevation=table["metric"],
        extruded=True,
        elevation_scale=3.4,
        opacity=0.9,
    )
    lng= ((bbox[0] + bbox[2]) / 2)
    lat=((bbox[1] + bbox[3]) / 2)
    fullscreen = FullscreenControl(position="top-right")
    nav = NavigationControl()
    view_state = {
        "longitude": lng,
        "latitude": lat,
        "zoom": 10,
        "pitch": 20,
        "bearing": 20,
    }

    m = Map(layers=[layer], view_state=view_state, basemap=MaplibreBasemap(style=CartoBasemap.DarkMatterNoLabels), controls=[fullscreen, nav, ScaleControl()])

    _controls = mo.hstack([colormap_dropdown, elevation_scale_slider, opacity_slider, extruded_toggle])
    mo.vstack([m, _controls])
    return (
        colormap_dropdown,
        elevation_scale_slider,
        extruded_toggle,
        layer,
        opacity_slider,
    )


@app.cell
def _(
    Normalize,
    apply_continuous_cmap,
    colormap_dropdown,
    layer,
    np,
    table,
):
    # Colormap updates only — re-runs when colormap_dropdown changes
    _elev_values = np.array(table["metric"].to_pylist())
    _normalizer = Normalize(_elev_values.min(), _elev_values.max())
    layer.get_fill_color = apply_continuous_cmap(_normalizer(_elev_values), colormap_dropdown.value, alpha=1)
    return


@app.cell
def _(elevation_scale_slider, extruded_toggle, layer, opacity_slider):
    # Scalar trait updates only — instant, no array recomputation
    layer.elevation_scale = elevation_scale_slider.value
    layer.opacity = opacity_slider.value
    layer.extruded = extruded_toggle.value
    return


@app.cell
def _():
    # # 1. Capture the full 'live' state including 3D parameters
    # current_view = m.view_state

    # # 2. Print it to see all the values
    # print(current_view)
    return


if __name__ == "__main__":
    app.run()
