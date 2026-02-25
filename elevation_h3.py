# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb",
#     "lonboard>=0.13.0",
#     "marimo",
#     "matplotlib",
#     "morecantile",
#     "numpy",
#     "odc-stac",
#     "palettable",
#     "planetary-computer",
#     "pyarrow",
#     "pyproj",
#     "pystac-client",
# ]
# ///

import marimo

__generated_with = "0.19.11"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md(r"""
    # 3DEP Seamless Elevation — H3 Extruded Hexagons

    Queries USGS 3DEP seamless DEM from [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
    via STAC, aggregates elevation to H3 hexagons with DuckDB, and renders extruded
    hexagons with [lonboard](https://github.com/developmentseed/lonboard).

    Uses [morecantile](https://github.com/developmentseed/morecantile) to split the
    bounding box into web mercator tiles for memory-efficient processing.

    Reference: [lonboard marimo examples](https://github.com/developmentseed/lonboard/blob/main/examples/marimo/nyc_taxi_trips.py)
    """)
    return


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import pyarrow as pa
    import duckdb
    import morecantile
    import odc.stac
    import planetary_computer
    import pystac_client
    from matplotlib.colors import Normalize
    from palettable.scientific.sequential import Imola_20_r
    from pyproj import Transformer
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.colormap import apply_continuous_cmap

    return (
        H3HexagonLayer,
        Imola_20_r,
        Map,
        Normalize,
        Table,
        Transformer,
        apply_continuous_cmap,
        duckdb,
        mo,
        morecantile,
        np,
        odc,
        pa,
        planetary_computer,
        pystac_client,
    )


@app.cell
def _(mo):
    mo.md(r"""
    ## Configuration
    """)
    return


@app.cell
def _():
    # Grand Canyon bbox: west, south, east, north
    BBOX = [-112.23047, 35.926336, -111.749034, 36.268266]
    COLLECTION = "3dep-seamless"
    BAND = "data"
    H3_RES = 11
    TILE_ZOOM = 12
    return BAND, BBOX, COLLECTION, H3_RES, TILE_ZOOM


@app.cell
def _(mo):
    mo.md(r"""
    ## Query STAC catalog
    """)
    return


@app.cell
def _(BBOX, COLLECTION, planetary_computer, pystac_client):
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    items = catalog.search(
        collections=[COLLECTION],
        bbox=BBOX,
    ).item_collection()
    print(f"Found {len(items)} STAC items")
    return (items,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Split bbox into morecantile tiles
    """)
    return


@app.cell
def _(BBOX, TILE_ZOOM, morecantile):
    tms = morecantile.tms.get("WebMercatorQuad")
    tiles = list(tms.tiles(*BBOX, zooms=[TILE_ZOOM]))
    print(f"Processing {len(tiles)} tiles at zoom {TILE_ZOOM}")
    return tiles, tms


@app.cell
def _(mo):
    mo.md(r"""
    ## Load DEM per tile and convert to lat/lng DataFrame
    """)
    return


@app.cell
def _(BAND, Transformer, items, np, odc, pa, tiles, tms):
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)

    all_lats = []
    all_lngs = []
    all_elevations = []

    for i, tile in enumerate(tiles):
        tile_bounds = tms.bounds(tile)
        tile_bbox = [tile_bounds.left, tile_bounds.bottom, tile_bounds.right, tile_bounds.top]

        try:
            ds = odc.stac.load(
                items,
                crs="EPSG:3857",
                bands=[BAND],
                resolution=30,
                bbox=tile_bbox,
            ).astype(float)
        except Exception as e:
            print(f"  Tile {i}: skipped ({e})")
            continue

        arr = ds[BAND].max(dim="time")
        vals = arr.values

        # Build coordinate grids from xarray coords
        x_coords = arr.coords["x"].values
        y_coords = arr.coords["y"].values
        X, Y = np.meshgrid(x_coords, y_coords)

        # Reproject 3857 -> 4326
        lons, lats = transformer.transform(X.flatten(), Y.flatten())
        all_lats.append(lats)
        all_lngs.append(lons)
        all_elevations.append(vals.flatten())

        if (i + 1) % 10 == 0 or i == len(tiles) - 1:
            print(f"  Processed {i + 1}/{len(tiles)} tiles")

    lat_arr = np.concatenate(all_lats)
    lng_arr = np.concatenate(all_lngs)
    elev_arr = np.concatenate(all_elevations)

    pixels_table = pa.table({
        "lat": pa.array(lat_arr, type=pa.float64()),
        "lng": pa.array(lng_arr, type=pa.float64()),
        "elevation": pa.array(elev_arr, type=pa.float64()),
    })
    print(f"Total pixels: {len(pixels_table):,}")
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Aggregate to H3 hexagons with DuckDB
    """)
    return


@app.cell
def _(H3_RES, duckdb):
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3;")
    query = f"""
        SELECT
            h3_latlng_to_cell(lat, lng, {H3_RES}) AS hex,
            AVG(elevation) AS metric,
            --COUNT(1) AS pixel_count
        FROM pixels_table
        GROUP BY 1
    """
    hex_result = con.sql(query).fetch_arrow_table()
    con.close()
    print(f"H3 hexagons: {len(hex_result):,}")
    return (hex_result,)


@app.cell
def _(mo):
    mo.md(r"""
    ## Visualize with lonboard
    """)
    return


@app.cell
def _(
    H3HexagonLayer,
    Imola_20_r,
    Map,
    Normalize,
    Table,
    apply_continuous_cmap,
    hex_result,
):
    elev_values = hex_result["metric"].to_pylist()
    elev_min = min(elev_values)
    elev_max = max(elev_values)

    normalizer = Normalize(elev_min, elev_max)
    normalized = normalizer(elev_values)
    colors = apply_continuous_cmap(normalized, Imola_20_r, alpha=0.9)
    table = Table.from_arrow(hex_result)
    layer = H3HexagonLayer(
        table=table,
        get_hexagon=table["hex"],
        get_fill_color=colors,
        get_elevation=hex_result["metric"],
        extruded=True,
        elevation_scale=3,
        opacity=0.9,
    )

    view_state = {
        "longitude": -111.99,
        "latitude": 36.10,
        "zoom": 10,
        "pitch": 55,
        "bearing": -20,
    }

    m = Map(layer, view_state=view_state)
    m
    return


if __name__ == "__main__":
    app.run()
