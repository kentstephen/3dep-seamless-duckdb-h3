# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "duckdb==1.4.3",
#     "h3>=4.0.0",
#     "lonboard==0.13.0",
#     "marimo",
#     "numpy==2.4.4",
#     "planetary-computer==1.0.0",
#     "pyarrow==18.1.0",
#     "pyproj==3.7.2",
#     "pystac-client==0.9.0",
#     "rioxarray>=0.15.0",
#     "seamless-3dep",
#     "shapely==2.1.2",
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
    import rioxarray  # noqa: F401 — registers .rio accessor
    import shapely
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
            SET memory_limit = '1GB';
            LOAD h3;
        """)
        return con

    def query_naip(bbox, datetime_range="2003-01-01/2025-12-31"):
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

    def best_naip_year(items, bbox=None):
        """Pick the most recent year where all quads were captured on a single date.

        Single-date years have no color seams between tiles. Falls back to the
        most recent year with maximum quad coverage if no single-date year exists.
        """
        from collections import defaultdict

        year_quads = defaultdict(dict)
        for item in items:
            year = item.datetime.year
            key = tuple(round(x, 4) for x in item.bbox)
            if key not in year_quads[year]:
                year_quads[year][key] = item

        if not year_quads:
            raise RuntimeError("No NAIP items found for bbox")

        # prefer most recent year where every quad shares one capture date
        for year in sorted(year_quads.keys(), reverse=True):
            quads = list(year_quads[year].values())
            dates = {it.datetime.strftime("%Y-%m-%d") for it in quads}
            if len(dates) == 1:
                print(f"NAIP {year}: {len(quads)} quads, all captured {next(iter(dates))} ✓")
                return quads

        # fallback: most recent year with most quads
        max_quads = max(len(q) for q in year_quads.values())
        for year in sorted(year_quads.keys(), reverse=True):
            if len(year_quads[year]) >= max_quads:
                quads = list(year_quads[year].values())
                dates = sorted({it.datetime.strftime("%Y-%m-%d") for it in quads})
                print(f"NAIP {year}: {len(quads)} quads, mixed dates {dates} ⚠")
                return quads

    def load_naip_item_pixels(item, bbox):
        """Load one NAIP quad at native 60cm. Returns raw (lat, lng, r, g, b) table or None."""
        try:
            import rioxarray as rxr
            da = rxr.open_rasterio(item.assets["image"].href, overview_level=0)
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

        return pa.table({
            "lat": pa.array(LATS[mask].flatten(), type=pa.float64()),
            "lng": pa.array(LONS[mask].flatten(), type=pa.float64()),
            "r": pa.array(r[mask].flatten(), type=pa.float32()),
            "g": pa.array(g[mask].flatten(), type=pa.float32()),
            "b": pa.array(b[mask].flatten(), type=pa.float32()),
        })

    def load_all_naip_pixels(items, bbox, max_workers=4):
        """Load all NAIP quads in parallel. Returns combined raw pixel table."""
        batches = []
        empty_items = []
        completed, total = 0, len(items)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(load_naip_item_pixels, item, bbox): item for item in items}
            for future in as_completed(futures):
                item = futures[future]
                result = future.result()
                if result is not None:
                    batches.append(result)
                else:
                    empty_items.append(item.id)
                completed += 1
                print(f"  NAIP: {completed}/{total} items")
        if not batches:
            raise RuntimeError("No NAIP items returned data — mosaic is empty for this bbox")
        if empty_items:
            print(f"WARNING: {len(empty_items)}/{total} quads had no pixel data:")
            for _id in empty_items:
                print(f"  {_id}")
        naip_pixels = pa.concat_tables(batches)
        print(f"NAIP pixels: {len(naip_pixels):,} rows")
        return naip_pixels

    def aggregate_naip_to_h3(naip_pixels, h3_res):
        """Aggregate raw pixel table to H3 cells. Fast — no I/O."""
        con = get_con()
        naip_cache = con.sql(f"""
            SELECT h3_latlng_to_cell(lat, lng, {h3_res}) AS hex,
                AVG(r) AS r, AVG(g) AS g, AVG(b) AS b
            FROM naip_pixels WHERE r > 0 OR g > 0 OR b > 0
            GROUP BY 1
        """).fetch_arrow_table()
        con.close()
        print(f"NAIP cache: {len(naip_cache):,} hexagons at res {h3_res}")
        return naip_cache

    return (
        aggregate_naip_to_h3,
        best_naip_year,
        load_all_naip_pixels,
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
        t_inv = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        b = dem.rio.bounds()
        dw, ds = t_inv.transform(b[0], b[1])
        de, dn = t_inv.transform(b[2], b[3])
        print(f"DEM shape: {dem.shape}, CRS: {dem.rio.crs}")
        print(f"  query bbox : {bbox}")
        print(f"  DEM bounds : ({dw:.6f}, {ds:.6f}, {de:.6f}, {dn:.6f})")
        return dem

    return (load_dem,)


@app.cell
def _():
    # Grand Canyon South Rim
    # bbox = (-112.2, 36.0, -111.8, 36.3)

    # Zion NP
    # bbox = (-113.1478, 37.0926, -112.7502, 37.4311)

    # Zion NP reduced
    # bbox = (-113.064963,37.176314,-112.903574,37.332624)

    # Devil's Tower
    # bbox = (-104.720703,44.586741,-104.709301,44.594649)

    # Yosemite Valley
    # bbox = (-119.61618,37.719767,-119.552052,37.76808)

    #  Monument Valley Navajo Tribal Park 
    # bbox = (-110.259297,36.874219,-109.937367,37.130223)
    # smaller Monument 
    # bbox = (-110.153897,36.959111,-110.046635,37.038107)
    # even smaller monument
    bbox = (-110.119667,36.965153,-110.054825,37.008449)

    # Yosemite
    # bbox = [-119.8017,37.6139,-119.2356,37.9822]

    # Franconia New Hampshire
    # bbox = (-71.876086,44.107368,-71.58563,44.324113)

    # Cathedral Ledge - Conway, NH
    # bbox = (-71.196834,44.046417,-71.151728,44.081825)

    # Bozeman, MT 
    # bbox = (-111.104555,45.535164,-110.838802,45.853691)

    # Niagra Falls NY
    # bbox = (-79.098882,43.058803,-79.042462,43.099923)

    MAX_WORKERS = 8
    H3_RES = 14
    return H3_RES, MAX_WORKERS, bbox


@app.cell
def _(MAX_WORKERS, bbox, best_naip_year, load_all_naip_pixels, query_naip):
    # SLOW — runs once per bbox change. Picks the most recent year with full quad coverage.
    naip_pixels = load_all_naip_pixels(best_naip_year(query_naip(bbox)), bbox, max_workers=MAX_WORKERS)
    return (naip_pixels,)


@app.cell
def _(H3_RES, aggregate_naip_to_h3, naip_pixels):
    # FAST — re-runs when H3_RES changes.
    naip_cache = aggregate_naip_to_h3(naip_pixels, H3_RES)
    return (naip_cache,)


@app.cell
def _(bbox, load_dem):
    # SLOW — runs once per bbox change.
    dem = load_dem(bbox, res=1)
    return (dem,)


@app.cell
def _(H3_RES, aggregate_to_h3, dem):
    # FAST — re-runs when H3_RES changes.
    elev_cache = aggregate_to_h3(dem, H3_RES)
    return (elev_cache,)


@app.cell
def _(Table, duckdb, elev_cache, naip_cache, np):
    print(f"naip_cache: {len(naip_cache):,} hexagons")
    print(f"elev_cache: {len(elev_cache):,} hexagons")
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
        start=0.1, stop=30.0, step=0.1, value=1.1, label="Elevation Scale"
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
        elevation_scale=1.1,
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
