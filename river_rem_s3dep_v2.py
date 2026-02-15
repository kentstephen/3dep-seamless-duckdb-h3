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
    from pathlib import Path

    import numpy as np
    import pyarrow as pa
    import duckdb
    import h3
    import marimo as mo
    import opt_einsum as oe
    import seamless_3dep as s3dep
    from matplotlib.colors import Normalize
    from pyproj import Transformer
    from scipy.spatial import KDTree
    from shapely import ops
    from arro3.core import Table

    import pygeoutils as geoutils
    import pynhd

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
        KDTree,
        Map,
        Normalize,
        Path,
        Table,
        Transformer,
        apply_continuous_cmap,
        duckdb,
        geoutils,
        h3,
        mo,
        np,
        oe,
        ops,
        pa,
        pynhd,
        s3dep,
    )


@app.cell
def _(
    KDTree,
    Path,
    Transformer,
    duckdb,
    geoutils,
    np,
    oe,
    ops,
    pa,
    pynhd,
    s3dep,
):
    def load_dem(bbox, save_dir, res=10):
        """Load DEM from USGS 3DEP via seamless-3dep. Returns xarray DataArray in EPSG:4326."""
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        tiff_files = s3dep.get_dem(bbox, save_dir, res=res)
        dem = s3dep.tiffs_to_da(tiff_files, bbox, crs=4326)
        print(f"DEM shape: {dem.shape}, CRS: {dem.rio.crs}")
        return dem

    # --- REM functions (HyRiver-style) ---

    def get_flowlines(bbox, dem_crs):
        """Get NHDPlus flowlines for bbox, extract main stem, smooth."""
        import geopandas as gpd

        wd = pynhd.WaterData("nhdflowline_network")
        flw = wd.bybox(bbox)
        flw = pynhd.prepare_nhdplus(flw, 0, 0, 0, remove_isolated=True)
        flw = flw[flw.levelpathi == flw.levelpathi.min()].to_crs(dem_crs).copy()
        print(f"Main stem: {len(flw)} segments")

        river_line = ops.linemerge(flw.geometry.tolist())

        # Project to UTM for smoothing (avoids issues with geographic coordinates)
        bounds = river_line.bounds
        lon_center = (bounds[0] + bounds[2]) / 2
        lat_center = (bounds[1] + bounds[3]) / 2
        utm_zone = int((lon_center + 180) / 6) + 1
        utm_epsg = 32600 + utm_zone if lat_center >= 0 else 32700 + utm_zone

        gdf_temp = gpd.GeoDataFrame([1], geometry=[river_line], crs=dem_crs)
        gdf_utm = gdf_temp.to_crs(f"EPSG:{utm_epsg}")
        river_utm = gdf_utm.geometry.iloc[0]

        npts = max(10, int(np.ceil(river_utm.length / 10)))
        river_smooth = geoutils.smooth_linestring(river_utm, 0.1, npts)

        # Project back to dem_crs
        gdf_result = gpd.GeoDataFrame([1], geometry=[river_smooth], crs=utm_epsg)
        river_line = gdf_result.to_crs(dem_crs).geometry.iloc[0]

        print(f"Smoothed river: {npts} points")
        return river_line

    def sample_river_elevation(dem, river_line):
        """Sample DEM elevation along the river centerline."""
        coords = np.array(river_line.coords)
        _, ux = np.unique(dem.x.values, return_index=True)
        _, uy = np.unique(dem.y.values, return_index=True)
        dem = dem.isel(x=np.sort(ux), y=np.sort(uy))
        import xarray as xr
        x_da = xr.DataArray(coords[:, 0], dims="points")
        y_da = xr.DataArray(coords[:, 1], dims="points")
        z = dem.interp(x=x_da, y=y_da, method="nearest").values
        mask = np.isfinite(z)
        river_elev = np.c_[coords[mask], z[mask]]
        print(f"Sampled {len(river_elev)} river elevation points ({mask.sum()} valid)")
        return river_elev

    def compute_rem(dem, river_elev):
        """IDW-interpolate river surface elevation, subtract from DEM."""
        _, ux = np.unique(dem.x.values, return_index=True)
        _, uy = np.unique(dem.y.values, return_index=True)
        dem = dem.isel(x=np.sort(ux), y=np.sort(uy))
        print("Building KDTree and computing IDW weights...")
        dem_points = np.dstack(np.meshgrid(dem.x.values, dem.y.values)).reshape(-1, 2)

        k = min(200, len(river_elev))
        distances, idxs = KDTree(river_elev[:, :2]).query(
            dem_points, k=k, workers=-1
        )

        w = np.reciprocal(np.power(distances, 2) + np.isclose(distances, 0))
        w_sum = np.sum(w, axis=1)
        w_norm = oe.contract(
            "ij,i->ij", w, np.reciprocal(w_sum + np.isclose(w_sum, 0)), optimize="optimal"
        )
        elevation = oe.contract("ij,ij->i", w_norm, river_elev[idxs, 2], optimize="optimal")
        elevation = elevation.reshape((dem.sizes["y"], dem.sizes["x"]))

        import xarray as xr
        river_surface = xr.DataArray(elevation, dims=("y", "x"), coords={"x": dem.x, "y": dem.y})
        rem = dem - river_surface
        print(f"REM range: {float(rem.min()):.1f}m to {float(rem.max()):.1f}m")
        return rem

    # --- H3 aggregation ---

    duckdb.sql("INSTALL h3 FROM community")

    def get_con():
        con = duckdb.connect()
        con.sql("SET memory_limit = '2GB'; LOAD h3;")
        return con

    def aggregate_rem_to_h3(rem, h3_res):
        """Flatten REM to lat/lng/value, aggregate to H3 via DuckDB.

        If the DEM is already in EPSG:4326, coordinates are used directly.
        Otherwise, reprojects to 4326 for H3 cell assignment.
        """
        crs = str(rem.rio.crs)
        X, Y = np.meshgrid(rem.x.values, rem.y.values)

        if crs and "4326" not in crs:
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            lons, lats = transformer.transform(X.flatten(), Y.flatten())
        else:
            lons, lats = X.flatten(), Y.flatten()

        vals = rem.values.flatten()
        mask = np.isfinite(vals)
        tile_pa = pa.table({
            "lat": pa.array(lats[mask], type=pa.float64()),
            "lng": pa.array(lons[mask], type=pa.float64()),
            "rem": pa.array(vals[mask], type=pa.float64()),
        })

        con = get_con()
        hex_result = con.sql(f"""
            SELECT
                h3_latlng_to_cell_string(lat, lng, {h3_res}) AS hex,
                AVG(rem) AS metric
            FROM tile_pa
            GROUP BY 1
        """).fetch_arrow_table()
        con.close()
        print(f"H3 hexagons: {len(hex_result):,}")
        return hex_result

    return (
        aggregate_rem_to_h3,
        compute_rem,
        get_flowlines,
        load_dem,
        sample_river_elevation,
    )


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
    # Willamette South of corvalis
    # bbox = (-123.262032,44.320005,-123.195634,44.46278)
    # Willamette between junction city and corvalis OR
    # bbox = (-123.28918,44.273662,-123.17244,44.513128)
    # bbox = (-123.277738,44.301413,-123.174705,44.491532) # larger south of corvalis
    # Mobile delta
    # bbox = (-88.035005,30.925685,-87.821484,31.300268) # cant make a line
    # John Day River OR
    # bbox = (-120.6097,45.1734,-120.4277,45.454)
    # Coburg OR McKenzie River
    # bbox = (-123.231178,44.110579,-123.061516,44.292868)
    # bigger mckenzie
    # bbox = (-123.397126,44.090777,-122.982165,44.529905) # kills the kenrel
    ## North of Harrisburg very south of corvalis
    # bbox = (-123.253962,44.128614,-123.062916,44.317384)
    # Carson River, NV (from HyRiver REM example)
    bbox = (-119.59, 39.24, -119.47, 39.30)  # original
    H3_RES = 12
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
    aggregate_rem_to_h3,
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
    rem = compute_rem(dem, river_elev)
    hex_result = aggregate_rem_to_h3(rem, H3_RES)

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
    from palettable.scientific.sequential import Bamako_20, Bamako_20_r, Imola_20, Imola_20_r, LaJolla_20, LaJolla_20_r
    from palettable.matplotlib import Viridis_20, Viridis_20_r, Inferno_20, Inferno_20_r
    from palettable.cartocolors.sequential import Emrld_7, Emrld_7_r
    from palettable.cmocean.sequential import Haline_20, Haline_20_r
    cmap_dropdown = mo.ui.dropdown(
        options={
            "Haline": Haline_20,
            "Haline (reversed)": Haline_20_r,
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
    view_state = {
        "longitude": lng,
        "latitude": lat,
        "zoom": 12,
        "pitch": 45,
        "bearing": 0,
    }

    m = Map(layers=[layer], view_state=view_state, basemap_style=CartoBasemap.Positron, controls=[fullscreen])

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
    elevation_scale_input,
    extruded_toggle,
    layer,
    np,
    opacity_input,
    rem_max_input,
    table,
):
    _elev_values = np.array(table["metric"].to_pylist())
    _clipped = np.clip(_elev_values, 0, rem_max_input.value)
    _normalizer = Normalize(0, rem_max_input.value)

    layer.get_fill_color = apply_continuous_cmap(_normalizer(_clipped), cmap_dropdown.value, alpha=1)
    layer.get_elevation = _clipped
    layer.elevation_scale = elevation_scale_input.value
    layer.opacity = opacity_input.value
    layer.extruded = extruded_toggle.value
    return


if __name__ == "__main__":
    app.run()
