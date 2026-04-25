# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3>=4.0.0",
#     "lonboard==0.13.0",
#     "marimo",
#     "numpy==2.2.0",
#     "planetary-computer==1.0.0",
#     "pyarrow==18.1.0",
#     "pyproj==3.7.2",
#     "pystac-client==0.9.0",
#     "rioxarray>=0.15.0",
#     "seamless-3dep",
#     "sqlglot",
# ]
# ///

import marimo

__generated_with = "0.23.3"
app = marimo.App(width="full")


@app.cell
def _(mo):
    mo.md(r"""
    # NAIP True Color + 3DEP 1m Elevation → H3 Hexagons

    NAIP aerial imagery (Planetary Computer, native 60cm) + 3DEP 1m elevation
    (USGS seamless-3dep ArcGIS service), aggregated to H3 UBIGINT cells.

    **Run with:** `uv run marimo edit naip_usgs_join_h3_1m.py --sandbox`

    ---

    **Performance notes**

    - NAIP loads at native 60cm (`overview_level=0`). Keep bbox small — each quad is ~28M pixels at native res.
    - NAIP items are deduplicated to one per geographic quad (most recent year).
    - 3DEP 1m uses `seamless-3dep` ArcGIS export service. Can timeout on larger bboxes — retry or reduce bbox.
    - Both sources produce UBIGINT H3 cells via `h3_latlng_to_cell`. Join works at any res where hexes are larger than the coarsest pixel (~1m elevation is the floor).
    """)
    return


@app.cell
def _():
    import sys
    sys.path.insert(0, "lib")

    import numpy as np
    import pyarrow as pa
    import duckdb
    import planetary_computer
    import pystac_client
    import marimo as mo
    import seamless_3dep as s3dep
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pathlib import Path
    from pyproj import Transformer
    from arro3.core import Table

    from lonboard import Map, H3HexagonLayer
    from lonboard.basemap import CartoBasemap, MaplibreBasemap
    from lonboard.controls import FullscreenControl, NavigationControl, ScaleControl

    from h3_aggregation import aggregate_to_h3

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
        Path,
        ScaleControl,
        Table,
        ThreadPoolExecutor,
        Transformer,
        aggregate_to_h3,
        as_completed,
        duckdb,
        mo,
        np,
        pa,
        planetary_computer,
        pystac_client,
        s3dep,
    )


@app.cell
def _(
    ThreadPoolExecutor,
    as_completed,
    duckdb,
    np,
    pa,
    planetary_computer,
    pystac_client,
):
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
        """Keep only the most recent item per geographic quad."""
        seen = set()
        unique = []
        for item in items:
            key = tuple(round(x, 4) for x in item.bbox)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        print(f"Deduplicated {len(items)} → {len(unique)} unique quads")
        return unique

    def process_naip_item_to_h3(item, bbox, h3_res):
        """Load one NAIP quad at native 60cm, aggregate RGB to H3. Returns arrow table or None."""
        try:
            import rioxarray  # noqa: F401

            # overview_level=0 = native 60cm
            da = rioxarray.open_rasterio(item.assets["image"].href, overview_level=0)
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
            print(f"WARNING: {len(empty_items)}/{total} quads had no pixel data:")
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

    return (
        deduplicate_naip_items,
        get_con,
        process_all_naip_items,
        query_naip,
    )


@app.cell
def _(Path, Transformer, s3dep):
    def load_dem(bbox, res=1):
        import tempfile
        save_dir = Path(tempfile.mkdtemp(prefix="3dep_1m_"))
        t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        west, south = t.transform(bbox[0], bbox[1])
        east, north = t.transform(bbox[2], bbox[3])
        bbox_3857 = (west, south, east, north)
        tiff_files = s3dep.get_map("DEM", bbox, save_dir, res=res)
        dem = s3dep.tiffs_to_da(tiff_files, bbox_3857, crs=3857)
        print(f"DEM shape: {dem.shape}, CRS: {dem.rio.crs}")
        return dem

    return (load_dem,)


@app.cell
def _():
    # Grand Canyon South Rim
    # bbox = (-112.2, 36.0, -111.8, 36.3)

    # Zion NP
    # bbox = (-113.1478, 37.0926, -112.7502, 37.4311)

    # Yosemite
    # bbox = [-119.8017,37.6139,-119.2356,37.9822]

    # Franconia New Hampshire
    # bbox = (-71.876086,44.107368,-71.58563,44.324113)

    # Cathedral Ledge - Conway, NH
    bbox = (-71.182824, 44.051446, -71.143571, 44.078063)

    MAX_WORKERS = 8
    NAIP_DATETIME = "2019-01-01/2022-12-31"
    H3_RES = 14
    return H3_RES, MAX_WORKERS, NAIP_DATETIME, bbox


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
    _items = deduplicate_naip_items(query_naip(bbox, NAIP_DATETIME))
    naip_cache = process_all_naip_items(_items, bbox, H3_RES, max_workers=MAX_WORKERS)
    return (naip_cache,)


@app.cell
def _(H3_RES, aggregate_to_h3, bbox, load_dem):
    # SLOW — runs once per bbox/H3_RES change.
    _dem = load_dem(bbox, res=1)
    elev_cache = aggregate_to_h3(_dem, H3_RES)
    del _dem
    return (elev_cache,)


@app.cell
def _(Table, duckdb, elev_cache, naip_cache, np):
    _con = duckdb.connect()
    _joined = _con.sql("""
        SELECT n.hex, n.r, n.g, n.b, e.metric AS elevation
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
