# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3==4.4.2",
#     "lonboard==0.13.0",
#     "marimo",
#     "matplotlib==3.10.8",
#     "numpy==2.4.2",
#     "opt-einsum",
#     "palettable==3.3.3",
#     "pyarrow==18.1.0",
#     "pygeoutils",
#     "pynhd",
#     "pyproj==3.7.2",
#     "rioxarray",
#     "scipy==1.17.0",
#     "seamless-3dep",
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
    # River REM → H3 Hexagons (seamless-3dep)

    Relative Elevation Model pipeline: query 3DEP DEM via USGS National Map (`seamless-3dep`) →
    get NHDPlus flowlines via `pynhd` → IDW-interpolate river surface →
    subtract to get height-above-river → aggregate to H3 hexagons via DuckDB →
    render with lonboard + custom colormap.

    Uses `seamless-3dep` instead of Planetary Computer STAC — full 10m coverage
    across CONUS without gaps.

    **Run with:** `uv run marimo edit river_rem_s3dep.py --sandbox`
    """)
    return


@app.cell
def _():
    import sys
    sys.path.insert(0, "lib")

    from pathlib import Path

    import numpy as np
    import duckdb
    import h3
    import marimo as mo
    import seamless_3dep as s3dep
    from matplotlib.colors import Normalize
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.colormap import apply_continuous_cmap
    from lonboard.basemap import CartoBasemap, MaplibreBasemap
    from lonboard.controls import FullscreenControl, NavigationControl, ScaleControl

    from h3_aggregation import aggregate_to_h3
    from rem import get_flowlines, sample_river_elevation, compute_rem

    duckdb.sql("INSTALL h3 FROM community")

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
        Path,
        ScaleControl,
        Table,
        aggregate_to_h3,
        apply_continuous_cmap,
        compute_rem,
        get_flowlines,
        h3,
        mo,
        np,
        s3dep,
        sample_river_elevation,
    )


@app.cell
def _(Path, s3dep):
    def load_dem(bbox, save_dir, res=10):
        """Load DEM from USGS 3DEP via seamless-3dep. Returns xarray DataArray in EPSG:4326."""
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        tiff_files = s3dep.get_dem(bbox, save_dir, res=res)
        dem = s3dep.tiffs_to_da(tiff_files, bbox, crs=4326)
        print(f"DEM shape: {dem.shape}, CRS: {dem.rio.crs}")
        return dem

    return (load_dem,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Configuration

    Willamette River between Corvallis and Albany, OR — an area where
    Planetary Computer has gaps in 10m 3DEP coverage but USGS National Map
    has full coverage via `seamless-3dep`.

    bbox from [Bounding Box Tool](https://boundingbox.klokantech.com/) in CSV format.
    """)
    return


@app.cell
def _(h3):
    # Willamette River between Corvallis and Albany, OR
    # bbox = (-123.236629, 44.573999, -123.122856, 44.653502)
    # Carson River, NV (from HyRiver REM example)
    bbox = (-119.59, 39.24, -119.47, 39.30)  # original
    H3_RES = 11
    DEM_RES = 10
    SAVE_DIR = "cache/dem"

    _hex_edge = h3.average_hexagon_edge_length(H3_RES, unit='m')
    _px_per_edge = _hex_edge / DEM_RES
    print(f"H3 res {H3_RES}: hex edge {_hex_edge:.0f}m, DEM {DEM_RES}m, {_px_per_edge:.1f} px/edge")
    return DEM_RES, H3_RES, SAVE_DIR, bbox


@app.cell
def _(
    DEM_RES,
    H3_RES,
    SAVE_DIR,
    Table,
    aggregate_to_h3,
    bbox,
    compute_rem,
    get_flowlines,
    load_dem,
    sample_river_elevation,
):
    dem = load_dem(bbox, SAVE_DIR, res=DEM_RES)
    print(dem)
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
        start=0.0, stop=1.0, step=0.05, value=1, label="Opacity"
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
        opacity=1,
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
