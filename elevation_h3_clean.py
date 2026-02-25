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
    import numpy as np
    import pyarrow as pa
    import duckdb
    import morecantile
    import h3
    import odc.stac
    import planetary_computer
    import pystac_client
    import marimo as mo
    from concurrent.futures import ThreadPoolExecutor, as_completed
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
        Table,
        ThreadPoolExecutor,
        Transformer,
        apply_continuous_cmap,
        as_completed,
        duckdb,
        h3,
        mo,
        morecantile,
        np,
        pa,
        planetary_computer,
        pystac_client,
    )


@app.cell
def _(
    ThreadPoolExecutor,
    Transformer,
    as_completed,
    duckdb,
    h3,
    morecantile,
    np,
    pa,
    planetary_computer,
    pystac_client,
):
    def calculate_resolution_for_h3(h3_res, native_resolution=10, pixels_per_hex_edge=6):
        """Calculate odc-stac resolution to get ~pixels_per_hex_edge pixels per H3 hex edge."""
        hex_edge_m = h3.average_hexagon_edge_length(h3_res, unit='m')
        target = hex_edge_m / pixels_per_hex_edge
        resolution = max(round(target / native_resolution) * native_resolution, native_resolution)
        px_per_edge = hex_edge_m / resolution
        print(f"H3 res {h3_res}: hex edge {hex_edge_m:.0f}m, resolution {resolution}m, {px_per_edge:.1f} px/edge")
        return resolution

    def query_stac(bbox, collection):
        """Query Planetary Computer STAC catalog for items covering bbox."""
        catalog = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )
        items = catalog.search(
            collections=[collection],
            bbox=bbox,
            query={"gsd": {"eq": 10}}
        ).item_collection()
        print(f"Found {len(items)} STAC items")
        return items

    def get_tiles(bbox, zoom):
        """Split bbox into morecantile tiles at given zoom level."""
        tms = morecantile.tms.get("WebMercatorQuad")
        tiles = list(tms.tiles(*bbox, zooms=[zoom]))
        print(f"{len(tiles)} tiles at zoom {zoom}")
        return tiles, tms

    duckdb.sql("INSTALL h3 FROM community")

    def get_con():
        """In-memory connection for workers. LOAD only, no INSTALL."""
        con = duckdb.connect()
        con.sql("""
            SET temp_directory = './tmp';
            SET memory_limit = '512MB';
            LOAD h3;
        """)
        return con

    def process_tile_to_h3(tile, tms, items, band, h3_res, resolution):
        """Load one tile's DEM, reproject to 4326, aggregate to H3.

        Returns Arrow table (hex, metric) or None on failure.
        """
        tile_bounds = tms.bounds(tile)
        tile_bbox = [tile_bounds.left, tile_bounds.bottom, tile_bounds.right, tile_bounds.top]
        transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

        try:
            import odc.stac
            ds = odc.stac.load(
                items,
                crs="EPSG:3857",
                resolution=resolution,
                bands=[band],
                bbox=tile_bbox,
            ).astype(float)
        except Exception:
            return None

        arr = ds[band].max(dim="time")
        vals = arr.values
        x_coords = arr.coords["x"].values
        y_coords = arr.coords["y"].values
        X, Y = np.meshgrid(x_coords, y_coords)
        lons, lats = transformer.transform(X.flatten(), Y.flatten())

        tile_pa = pa.table({
            "lat": pa.array(lats, type=pa.float64()),
            "lng": pa.array(lons, type=pa.float64()),
            "elevation": pa.array(vals.flatten(), type=pa.float64()),
        })

        con = get_con()
        result = con.sql(f"""
            SELECT
                h3_latlng_to_cell(lat, lng, {h3_res}) AS hex,
                AVG(elevation) AS metric
            FROM tile_pa
            GROUP BY 1
        """).fetch_arrow_table()
        return result

    def process_all_tiles(items, tiles, tms, band, h3_res, resolution, max_workers=4):
        """Process all tiles concurrently, then merge edge hexagons."""
        batches = []
        completed = 0
        total = len(tiles)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(process_tile_to_h3, tile, tms, items, band, h3_res, resolution): tile
                for tile in tiles
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None and len(result) > 0:
                    batches.append(result)
                completed += 1
                if completed % 100 == 0 or completed == total:
                    print(f"  Processed {completed}/{total} tiles")

        if not batches:
            raise RuntimeError("No tiles produced data")

        combined = pa.concat_tables(batches)
        print(f"Pre-merge hex count: {len(combined):,}")

        con = duckdb.connect()
        hex_result = con.sql("""
            SELECT hex, AVG(metric) AS metric
            FROM combined
            GROUP BY 1
        """).fetch_arrow_table()
        con.close()
        print(f"Final H3 hexagons: {len(hex_result):,}")
        return hex_result

    return (
        calculate_resolution_for_h3,
        get_tiles,
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
    Normalize,
    apply_continuous_cmap,
    bbox,
    mo,
    np,
    table,
):
    from palettable.scientific.sequential import Bamako_20, Bamako_20_r, LaJolla_20, LaJolla_20_r
    from palettable.matplotlib import Viridis_20, Viridis_20_r
    from palettable.cartocolors.sequential import Emrld_7, Emrld_7_r

    colormap_dropdown = mo.ui.dropdown(
        options={
            "LaJolla": LaJolla_20,
            "LaJolla (reversed)": LaJolla_20_r,
            "Bamako": Bamako_20,
            "Bamako (reversed)": Bamako_20_r,
            "Viridis": Viridis_20,
            "Viridis (reversed)": Viridis_20_r,
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
    view_state = {
        "longitude": lng,
        "latitude": lat,
        "zoom": 10,
        "pitch": 20,
        "bearing": 20,
    }

    m = Map(layers=[layer], view_state=view_state, basemap_style=CartoBasemap.DarkMatterNoLabels, controls=[fullscreen])

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
    elevation_scale_slider,
    extruded_toggle,
    layer,
    np,
    opacity_slider,
    table,
):
    # Trait updates — modifies layer in-place without re-creating the map
    _elev_values = np.array(table["metric"].to_pylist())
    _normalizer = Normalize(_elev_values.min(), _elev_values.max())

    layer.get_fill_color = apply_continuous_cmap(_normalizer(_elev_values), colormap_dropdown.value, alpha=1)
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
