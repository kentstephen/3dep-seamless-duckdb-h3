# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3>=4.0.0",
#     "lonboard==0.13.0",
#     "marimo",
#     "morecantile>=1.0.0",
#     "numpy==2.2.0",
#     "odc-stac==0.5.0",
#     "planetary-computer==1.0.0",
#     "pyarrow==18.1.0",
#     "pyproj==3.7.2",
#     "pystac-client==0.9.0",
#     "rioxarray>=0.15.0",
#     "sqlglot",
# ]
# ///

import marimo

__generated_with = "0.23.3"
app = marimo.App(width="full")


@app.cell
def _(mo):
    mo.md(r"""
    # NAIP True Color + 3DEP Elevation → H3 Hexagons

    Grand Canyon: NAIP aerial imagery (Planetary Computer) + 3DEP elevation, aggregated to H3.
    Data is cached at `H3_RES`. Changing H3 resolution only re-runs the fast
    DuckDB re-aggregation cells — no re-loading.

    **Run with:** `uv run marimo edit naip_h3_grand_canyon.py --sandbox`

    ---

    **Performance notes**

    - NAIP loads at `overview_level=3` (~16m pixels) regardless of H3 res. For color averaging
      into hexes this is indistinguishable from finer reads and ~4–16× faster.
    - NAIP items are deduplicated to one per geographic quad (most recent year) before processing.
      Without this, a bbox query returns every acquisition year for every quad.
    - The Fused reference UDF renders instantly because it is server-side, viewport-only, and
      processes 256×256px chips per tile. We load the full bbox upfront — fast enough once cached,
      slower on first run.
    - **TODO**: replace `query_3dep` / `process_all_3dep_tiles` (Planetary Computer 10m) with
      `seamless-3dep` 1m (`s3dep.get_map("DEM", bbox, save_dir, res=1)`) for elevation. See
      `elevation_1m.py` for the pattern. Keep NAIP pipeline unchanged.
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
    from pyproj import Transformer
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.basemap import CartoBasemap, MaplibreBasemap
    from lonboard.controls import FullscreenControl, NavigationControl, ScaleControl

    import warnings
    warnings.filterwarnings("ignore", message="Dataset has no geotransform", category=UserWarning)
    duckdb.sql("INSTALL h3 FROM community")
    return (
        CartoBasemap,
        FullscreenControl,
        H3HexagonLayer,
        Map,
        MaplibreBasemap,
        NavigationControl,
        ScaleControl,
        Table,
        ThreadPoolExecutor,
        Transformer,
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
        hex_edge_m = h3.average_hexagon_edge_length(h3_res, unit='m')
        target = hex_edge_m / pixels_per_hex_edge
        resolution = max(round(target / native_resolution) * native_resolution, native_resolution)
        print(f"H3 res {h3_res}: hex edge {hex_edge_m:.0f}m, resolution {resolution}m, {hex_edge_m/resolution:.1f} px/edge")
        return resolution

    def get_tiles(bbox, zoom):
        tms = morecantile.tms.get("WebMercatorQuad")
        tiles = list(tms.tiles(*bbox, zooms=[zoom]))
        print(f"{len(tiles)} tiles at zoom {zoom}")
        return tiles, tms

    def get_con():
        con = duckdb.connect()
        con.sql("""
            SET temp_directory = './tmp';
            SET memory_limit = '768MB';
            LOAD h3;
        """)
        return con

    def query_naip(bbox, datetime_range="2019-01-01/2022-12-31"):
        catalog = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )
        items = catalog.search(
            collections=["naip"],
            bbox=bbox,
            datetime=datetime_range,
            sortby="-datetime",
        ).item_collection()
        print(f"Found {len(items)} NAIP items")
        return items

    def deduplicate_naip_items(items):
        """Keep only the most recent item per geographic quad.

        Items are already sorted newest-first. Dedup by bbox so we don't load
        the same quad multiple times from different acquisition years.
        """
        seen = set()
        unique = []
        for item in items:
            key = tuple(round(x, 4) for x in item.bbox)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        print(f"Deduplicated {len(items)} → {len(unique)} unique quads")
        return unique

    def query_3dep(bbox):
        catalog = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )
        for gsd in [10, 30]:
            items = catalog.search(
                collections=["3dep-seamless"],
                bbox=bbox,
                query={"gsd": {"eq": gsd}},
            ).item_collection()
            if len(items) > 0:
                print(f"Found {len(items)} 3DEP items at {gsd}m GSD")
                return items, gsd
        raise RuntimeError("No 3DEP items found")

    def process_naip_item_to_h3(item, bbox, h3_res):
        """Load one NAIP quad, aggregate RGB to H3. Returns (hex, r, g, b) or None."""
        try:
            import rioxarray  # noqa: F401

            # Level 3 ≈ 16m pixels — fast, and indistinguishable from finer reads for color averaging.
            da = rioxarray.open_rasterio(item.assets["image"].href, overview_level=3)
            rgb = da.sel(band=[1, 2, 3]).astype(float)

            west, south, east, north = bbox
            rgb_clipped = rgb.rio.clip_box(west, south, east, north, crs="EPSG:4326")
            rgb_wgs = rgb_clipped.rio.reproject("EPSG:4326")

        except Exception as e:
            print(f"  item {item.id[:30]} failed: {e}")
            return None

        r = rgb_wgs.sel(band=1).values
        g = rgb_wgs.sel(band=2).values
        b = rgb_wgs.sel(band=3).values

        lons = rgb_wgs.x.values
        lats = rgb_wgs.y.values
        LONS, LATS = np.meshgrid(lons, lats)
        mask = (r > 0) | (g > 0) | (b > 0)
        if not mask.any():
            return None

        tile_pa = pa.table({
            "lat": pa.array(LATS[mask].flatten(), type=pa.float64()),
            "lng": pa.array(LONS[mask].flatten(), type=pa.float64()),
            "r": pa.array(r[mask].flatten(), type=pa.float64()),
            "g": pa.array(g[mask].flatten(), type=pa.float64()),
            "b": pa.array(b[mask].flatten(), type=pa.float64()),
        })

        con = get_con()
        return con.sql(f"""
            SELECT h3_latlng_to_cell(lat, lng, {h3_res}) AS hex,
                AVG(r) AS r, AVG(g) AS g, AVG(b) AS b
            FROM tile_pa WHERE r > 0 OR g > 0 OR b > 0
            GROUP BY 1
        """).fetch_arrow_table()

    def process_all_naip_items(items, bbox, h3_res, max_workers=4):
        batches = []
        empty_items = []
        completed, total = 0, len(items)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(process_naip_item_to_h3, item, bbox, h3_res): item
                for item in items
            }
            for future in as_completed(futures):
                item = futures[future]
                result = future.result()
                if result is not None and len(result) > 0:
                    batches.append(result)
                else:
                    empty_items.append(item.id)
                completed += 1
                if completed % 10 == 0 or completed == total:
                    print(f"  NAIP: {completed}/{total} items")
        if not batches:
            raise RuntimeError("No NAIP items returned data — mosaic is empty for this bbox/datetime")
        if empty_items:
            print(f"WARNING: {len(empty_items)}/{total} quads had no pixel data (incomplete mosaic):")
            for _id in empty_items:
                print(f"  {_id}")
        combined = pa.concat_tables(batches)
        con = duckdb.connect()
        naip_cache = con.sql("""
            SELECT hex, AVG(r) AS r, AVG(g) AS g, AVG(b) AS b
            FROM combined GROUP BY 1
        """).fetch_arrow_table()
        con.close()
        print(f"NAIP cache: {len(naip_cache):,} hexagons")
        return naip_cache

    def process_3dep_tile_to_h3(tile, tms, items, h3_res, resolution):
        tile_bounds = tms.bounds(tile)
        tile_bbox = [tile_bounds.left, tile_bounds.bottom, tile_bounds.right, tile_bounds.top]
        transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        try:
            import odc.stac as _odc_stac
            ds = _odc_stac.load(
                items, crs="EPSG:3857", resolution=resolution,
                bands=["data"], bbox=tile_bbox,
            ).astype(float)
        except Exception:
            return None
        arr = ds["data"].max(dim="time")
        X, Y = np.meshgrid(arr.coords["x"].values, arr.coords["y"].values)
        lons, lats = transformer.transform(X.flatten(), Y.flatten())
        tile_pa = pa.table({
            "lat": pa.array(lats, type=pa.float64()),
            "lng": pa.array(lons, type=pa.float64()),
            "elevation": pa.array(arr.values.flatten(), type=pa.float64()),
        })
        con = get_con()
        return con.sql(f"""
            SELECT h3_latlng_to_cell(lat, lng, {h3_res}) AS hex,
                AVG(elevation) AS elevation
            FROM tile_pa GROUP BY 1
        """).fetch_arrow_table()

    def process_all_3dep_tiles(items, tiles, tms, h3_res, resolution, max_workers=4):
        batches = []
        completed, total = 0, len(tiles)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(process_3dep_tile_to_h3, tile, tms, items, h3_res, resolution): tile
                for tile in tiles
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None and len(result) > 0:
                    batches.append(result)
                completed += 1
                if completed % 10 == 0 or completed == total:
                    print(f"  3DEP: {completed}/{total} tiles")
        if not batches:
            raise RuntimeError("No 3DEP tiles returned data")
        combined = pa.concat_tables(batches)
        con = duckdb.connect()
        elev_cache = con.sql("""
            SELECT hex, AVG(elevation) AS elevation
            FROM combined GROUP BY 1
        """).fetch_arrow_table()
        con.close()
        print(f"3DEP cache: {len(elev_cache):,} hexagons")
        return elev_cache

    return (
        calculate_resolution_for_h3,
        deduplicate_naip_items,
        get_tiles,
        process_all_3dep_tiles,
        process_all_naip_items,
        query_3dep,
        query_naip,
    )


@app.cell
def _():
    # Grand Canyon South Rim
    # bbox = (-112.2, 36.0, -111.8, 36.3)

    # Full Grand Canyon
    # bbox = (-113.0606, 35.8461, -111.7165, 36.7665)

    # Zion NP
    # bbox = (-113.1478, 37.0926, -112.7502, 37.4311)

    # Yosemite
    # bbox = [-119.8017,37.6139,-119.2356,37.9822]

    # Franconia New Hampshire
    # bbox = (-71.876086,44.107368,-71.58563,44.324113)

    # Franconia New Hampshire
    # bbox =(-71.989123,44.049974,-71.515802,44.422375)

    # Cathedral Ledge - Conway, NH
    bbox = (-71.182824,44.051446,-71.143571,44.078063)

    TILE_ZOOM = 13
    MAX_WORKERS = 8
    NAIP_DATETIME = "2019-01-01/2022-12-31"

    H3_RES = 13
    return H3_RES, MAX_WORKERS, NAIP_DATETIME, TILE_ZOOM, bbox


@app.cell
def _(TILE_ZOOM, bbox, get_tiles):
    tiles, tms = get_tiles(bbox, TILE_ZOOM)
    return tiles, tms


@app.cell
def _(
    H3_RES,
    MAX_WORKERS,
    NAIP_DATETIME,
    bbox,
    deduplicate_naip_items,
    process_all_naip_items,
    query_naip,
):
    # SLOW — runs once per bbox/H3_RES change.
    # TODO: switch to seamless-3dep 1m for elevation; keep NAIP here for color.
    _items = deduplicate_naip_items(query_naip(bbox, NAIP_DATETIME))
    naip_cache = process_all_naip_items(_items, bbox, H3_RES, max_workers=MAX_WORKERS)
    return


@app.cell
def _(
    H3_RES,
    MAX_WORKERS,
    bbox,
    calculate_resolution_for_h3,
    process_all_3dep_tiles,
    query_3dep,
    tiles,
    tms,
):
    # SLOW — runs once per bbox/H3_RES change.
    _dep_items, _dep_native_res = query_3dep(bbox)
    _dep_res = calculate_resolution_for_h3(H3_RES, native_resolution=_dep_native_res)
    elev_cache = process_all_3dep_tiles(_dep_items, tiles, tms, H3_RES, _dep_res, max_workers=MAX_WORKERS)
    return


@app.cell
def _(Table, duckdb, np):
    _con = duckdb.connect()
    _joined = _con.sql("""
        SELECT n.hex, n.r, n.g, n.b, e.elevation
        FROM naip_cache n INNER JOIN elev_cache e ON n.hex = e.hex
    """).fetch_arrow_table()
    _con.close()
    print(f"Joined: {len(_joined):,} hexagons")

    _r = np.clip(np.array(_joined["r"]), 0, 255).astype(np.uint8)
    _g = np.clip(np.array(_joined["g"]), 0, 255).astype(np.uint8)
    _b = np.clip(np.array(_joined["b"]), 0, 255).astype(np.uint8)
    naip_colors = np.column_stack([_r, _g, _b, np.full(len(_r), 255, dtype=np.uint8)])

    table = Table.from_arrow(_joined)
    del _joined
    return naip_colors, table


@app.cell
def _(
    CartoBasemap,
    FullscreenControl,
    H3HexagonLayer,
    Map,
    MaplibreBasemap,
    NavigationControl,
    ScaleControl,
    bbox,
    mo,
    naip_colors,
    table,
):
    elevation_scale_input = mo.ui.number(
        start=0.1, stop=30.0, step=0.1, value=2.5, label="Elevation Scale"
    )
    opacity_input = mo.ui.number(
        start=0.0, stop=1.0, step=0.05, value=1.0, label="Opacity"
    )
    extruded_toggle = mo.ui.switch(value=True, label="Extruded")

    layer = H3HexagonLayer(
        table=table,
        get_hexagon=table["hex"],
        get_fill_color=naip_colors,
        high_precision=True,
        stroked=False,
        get_elevation=table["elevation"],
        extruded=True,
        elevation_scale=2.5,
        opacity=1.0,
    )

    _lng = (bbox[0] + bbox[2]) / 2
    _lat = (bbox[1] + bbox[3]) / 2
    m = Map(
        layers=[layer],
        view_state={"longitude": _lng, "latitude": _lat, "zoom": 11, "pitch": 45, "bearing": -20},
        basemap=MaplibreBasemap(style=CartoBasemap.DarkMatterNoLabels),
        controls=[FullscreenControl(position="top-right"), NavigationControl(), ScaleControl()],
        parameters={"depthTest": True, "blend": True},
    )

    mo.vstack([m, mo.hstack([elevation_scale_input, opacity_input, extruded_toggle])])
    return elevation_scale_input, extruded_toggle, layer, opacity_input


@app.cell
def _(elevation_scale_input, extruded_toggle, layer, opacity_input):
    layer.elevation_scale = elevation_scale_input.value
    layer.opacity = opacity_input.value
    layer.extruded = extruded_toggle.value
    return


if __name__ == "__main__":
    app.run()
