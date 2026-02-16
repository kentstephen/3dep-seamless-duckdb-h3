# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3==4.4.2",
#     "lonboard==0.13.0",
#     "marimo",
#     "matplotlib==3.10.8",
#     "numpy==2.4.2",
#     "palettable==3.3.3",
#     "pyarrow==18.1.0",
#     "pyproj==3.7.2",
#     "rioxarray",
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
    # 1m Elevation → H3 Hexagons

    Load 1m DEM from USGS 3DEP via `seamless-3dep` `get_map()` →
    aggregate to H3 hexagons via DuckDB → render with lonboard.

    **Run with:** `uv run marimo edit elevation_1m.py --sandbox`
    """)
    return


@app.cell
def _():
    from pathlib import Path

    import numpy as np
    import pyarrow as pa
    import duckdb
    import h3
    import marimo as mo
    import seamless_3dep as s3dep
    from matplotlib.colors import Normalize
    from pyproj import Transformer
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.colormap import apply_continuous_cmap
    from lonboard.basemap import CartoBasemap
    from lonboard.controls import FullscreenControl

    import warnings
    warnings.filterwarnings("ignore", message="Dataset has no geotransform", category=UserWarning)
    return (
        CartoBasemap,
        FullscreenControl,
        H3HexagonLayer,
        Map,
        Normalize,
        Path,
        Table,
        Transformer,
        apply_continuous_cmap,
        duckdb,
        h3,
        mo,
        np,
        pa,
        s3dep,
    )


@app.cell
def _(duckdb):
    duckdb.sql("INSTALL h3 FROM community")
    return


@app.cell
def _(Path, Transformer, duckdb, np, pa, s3dep):
    def _reproject_bbox(bbox, src_crs, dst_crs):
        t = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
        west, south = t.transform(bbox[0], bbox[1])
        east, north = t.transform(bbox[2], bbox[3])
        return (west, south, east, north)

    def load_dem(bbox, res=1):
        """Load DEM from USGS 3DEP via get_map. Returns xarray DataArray in EPSG:3857.

        Downloads to a temp directory that the OS cleans up automatically.
        """
        import tempfile
        save_dir = Path(tempfile.mkdtemp(prefix="3dep_1m_"))
        tiff_files = s3dep.get_map("DEM", bbox, save_dir, res=res)
        bbox_3857 = _reproject_bbox(bbox, 4326, 3857)
        dem = s3dep.tiffs_to_da(tiff_files, bbox_3857, crs=3857)
        print(f"DEM shape: {dem.shape}, CRS: {dem.rio.crs}")
        return dem



    def get_con():
        con = duckdb.connect()
        con.sql("SET memory_limit = '2GB'; LOAD h3;")
        return con

    def aggregate_to_h3(dem, h3_res):
        """Flatten DEM to lat/lng/elevation, aggregate to H3 via DuckDB."""
        crs = str(dem.rio.crs)
        X, Y = np.meshgrid(dem.x.values, dem.y.values)

        if crs and "4326" not in crs:
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            lons, lats = transformer.transform(X.flatten(), Y.flatten())
        else:
            lons, lats = X.flatten(), Y.flatten()

        vals = dem.values.flatten()
        mask = np.isfinite(vals)
        tile_pa = pa.table({
            "lat": pa.array(lats[mask], type=pa.float64()),
            "lng": pa.array(lons[mask], type=pa.float64()),
            "elevation": pa.array(vals[mask], type=pa.float64()),
        })

        con = get_con()
        hex_result = con.sql(f"""
            SELECT
                h3_latlng_to_cell_string(lat, lng, {h3_res}) AS hex,
                AVG(elevation) AS metric
            FROM tile_pa
            GROUP BY 1
        """).fetch_arrow_table()
        con.close()
        print(f"H3 hexagons: {len(hex_result):,}")
        return hex_result

    return aggregate_to_h3, load_dem


@app.cell
def _(mo):
    mo.md(r"""
    ## Configuration

    bbox from [Bounding Box Tool](https://boundingbox.klokantech.com/) in CSV format.
    Keep bbox small — 1m DEM generates massive arrays.
    """)
    return


@app.cell
def _(h3):
    # Carson River, NV (small bbox for 1m)
    # bbox = (-119.56, 39.26, -119.50, 39.29)
    # Snake River WY near Moose
    # bbox = (-110.714551,43.665157,-110.683452,43.694867)
    # bigger snake
    # bbox= (-110.720835,43.663083,-110.670889,43.703449)
    # Pittsburgh
    # bbox = (-80.056754,40.41697,-79.935838,40.47789)
    # Bigger Pitt
    # bbox = (-80.068926,40.388062,-79.914701,40.502822)
    # mount washington
    # bbox= (-71.410315,44.165402,-71.196076,44.377283)
    # Cumberland Gap TN
    # bbox = [-83.897935,36.440658,-83.415711,36.756229]
    # Yosemite
    # bbox = [-119.8017,37.6139,-119.2356,37.9822]
    # Yosemite zoom to village
    bbox = [-119.748015,37.682027,-119.470209,37.786275]
    H3_RES = 12
    DEM_RES = 10 # changed to 10 meter

    _hex_edge = h3.average_hexagon_edge_length(H3_RES, unit='m')
    _px_per_edge = _hex_edge / DEM_RES
    print(f"H3 res {H3_RES}: hex edge {_hex_edge:.0f}m, DEM {DEM_RES}m, {_px_per_edge:.1f} px/edge")
    return DEM_RES, H3_RES, bbox


@app.cell
def _(DEM_RES, H3_RES, Table, aggregate_to_h3, bbox, load_dem):
    dem = load_dem(bbox, res=DEM_RES)
    hex_result = aggregate_to_h3(dem, H3_RES)

    table = Table.from_arrow(hex_result)
    del hex_result
    return (table,)


@app.cell
def _(
    CartoBasemap,
    FullscreenControl,
    H3HexagonLayer,
    Map,
    Normalize,
    apply_continuous_cmap,
    bbox,
    mo,
    np,
    table,
):
    from palettable.scientific.sequential import Bamako_20, Bamako_20_r, Imola_20, Imola_20_r, LaJolla_20, LaJolla_20_r, Tokyo_20, Tokyo_20_r
    from palettable.matplotlib import Viridis_20, Viridis_20_r, Inferno_20, Inferno_20_r
    from palettable.cartocolors.sequential import Emrld_7, Emrld_7_r
    from palettable.cmocean.sequential import Solar_20, Solar_20_r

    cmap_dropdown = mo.ui.dropdown(
        options={
            "LaJolla": LaJolla_20,
            "LaJolla (reversed)": LaJolla_20_r,
            "Bamako": Bamako_20,
            "Bamako (reversed)": Bamako_20_r,
            "Imola": Imola_20,
            "Imola (reversed)": Imola_20_r,
            "Viridis": Viridis_20,
            "Viridis (reversed)": Viridis_20_r,
            "Inferno": Inferno_20,
            "Inferno (reversed)": Inferno_20_r,
            "Solar": Solar_20,
            "Solar_20 (reversed)": Solar_20_r,
            "Tokyo": Tokyo_20,
            "Tokyo (reversed)": Tokyo_20_r,
            "Emrld": Emrld_7,
            "Emrld (reversed)": Emrld_7_r,
        },
        value="LaJolla",
        label="Colormap",
    )
    elevation_scale_input = mo.ui.number(
        start=0.0, stop=20.0, step=0.1, value=1.5, label="Elevation Scale"
    )
    opacity_input = mo.ui.number(
        start=0.0, stop=1.0, step=0.1, value=0.7, label="Opacity"
    )
    extruded_toggle = mo.ui.switch(value=False, label="Extruded")

    _elev_values = np.array(table["metric"].to_pylist())
    _normalizer = Normalize(float(np.min(_elev_values)), float(np.max(_elev_values)))
    # _normalizer = Normalize(float(np.min(_elev_values)), float(1982.0))
    _colors = apply_continuous_cmap(_normalizer(_elev_values), LaJolla_20, alpha=1)

    layer = H3HexagonLayer(
        table=table,
        get_hexagon=table["hex"],
        get_fill_color=_colors,
        high_precision=True,
        stroked=False,
        coverage=1,
        get_elevation=_elev_values,
        extruded=False,
        elevation_scale=1.5,
        opacity=0.7,

    )
    lng = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    fullscreen = FullscreenControl(position="top-right")
    view_state = {
        "longitude": lng,
        "latitude": lat,
        "zoom": 13,
        "pitch": 45,
        "bearing": 0,
    }

    m = Map(layers=[layer], 
            view_state=view_state, 
            basemap_style=CartoBasemap.DarkMatterNoLabels, 
            use_device_pixels=2.0,  
            controls=[fullscreen],
            parameters={"depthTest": True, "blend": True}, 
           )

    _layer_controls = mo.hstack([cmap_dropdown, elevation_scale_input, opacity_input, extruded_toggle], justify="start", gap=0.5)
    mo.vstack([m, _layer_controls])
    return (
        cmap_dropdown,
        elevation_scale_input,
        extruded_toggle,
        layer,
        opacity_input,
    )


@app.cell
def _(Normalize, apply_continuous_cmap, cmap_dropdown, layer, np, table):
    # Only re-runs when colormap changes — the expensive part
    _elev_values = np.array(table["metric"].to_pylist())
    _normalizer = Normalize(float(np.min(_elev_values)), float(np.max(_elev_values)))
    layer.get_fill_color = apply_continuous_cmap(_normalizer(_elev_values), cmap_dropdown.value, alpha=1)
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
