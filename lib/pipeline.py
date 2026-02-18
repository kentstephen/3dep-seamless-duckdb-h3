"""STAC + concurrent tile processing pipeline.

Shared by: elevation_h3_clean.py, elevation_h3_clean_with_fused_census.py,
elevation_h3_overture_roads.py, river_rem_h3.py

All heavy imports are inside function bodies — no import-time deps.
"""


def calculate_resolution_for_h3(h3_res, native_resolution=10, pixels_per_hex_edge=6):
    """Calculate odc-stac resolution to get ~pixels_per_hex_edge pixels per H3 hex edge."""
    import h3 as _h3

    hex_edge_m = _h3.average_hexagon_edge_length(h3_res, unit='m')
    target = hex_edge_m / pixels_per_hex_edge
    resolution = max(round(target / native_resolution) * native_resolution, native_resolution)
    px_per_edge = hex_edge_m / resolution
    print(f"H3 res {h3_res}: hex edge {hex_edge_m:.0f}m, resolution {resolution}m, {px_per_edge:.1f} px/edge")
    return resolution


def query_stac(bbox, collection):
    """Query Planetary Computer STAC catalog for items covering bbox."""
    import planetary_computer
    import pystac_client

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
    import morecantile

    tms = morecantile.tms.get("WebMercatorQuad")
    tiles = list(tms.tiles(*bbox, zooms=[zoom]))
    print(f"{len(tiles)} tiles at zoom {zoom}")
    return tiles, tms


def install_h3():
    """Install DuckDB H3 extension globally (once per session)."""
    import duckdb

    duckdb.sql("INSTALL h3 FROM community")


def get_con(memory_limit="512MB", extensions=("h3",)):
    """In-memory DuckDB connection for workers. LOAD only, no INSTALL.

    Args:
        memory_limit: DuckDB memory limit string.
        extensions: Tuple of extensions to LOAD (must be pre-installed).
    """
    import duckdb

    con = duckdb.connect()
    loads = "; ".join(f"LOAD {ext}" for ext in extensions)
    con.sql(f"""
        SET temp_directory = './tmp';
        SET memory_limit = '{memory_limit}';
        {loads};
    """)
    return con


def process_tile_to_h3(tile, tms, items, band, h3_res, resolution,
                        h3_func="h3_latlng_to_cell_string",
                        value_column="elevation",
                        con_kwargs=None):
    """Load one tile's DEM, reproject to 4326, aggregate to H3.

    Args:
        h3_func: DuckDB H3 function name. Use "h3_latlng_to_cell_string" for
            lonboard string indices, "h3_latlng_to_cell" for integer indices.
        value_column: Name for the raw value column in the intermediate table.
        con_kwargs: Extra kwargs for get_con() (e.g. extensions).

    Returns Arrow table (hex, metric) or None on failure.
    """
    import numpy as np
    import pyarrow as pa
    from pyproj import Transformer

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
        value_column: pa.array(vals.flatten(), type=pa.float64()),
    })

    _con_kwargs = con_kwargs or {}
    con = get_con(**_con_kwargs)
    result = con.sql(f"""
        SELECT
            {h3_func}(lat, lng, {h3_res}) AS hex,
            AVG({value_column}) AS metric
        FROM tile_pa
        GROUP BY 1
    """).fetch_arrow_table()
    return result


def process_all_tiles(items, tiles, tms, band, h3_res, resolution,
                      max_workers=4, **tile_kwargs):
    """Process all tiles concurrently, then merge edge hexagons.

    Extra kwargs are forwarded to process_tile_to_h3 (e.g. h3_func, con_kwargs).
    """
    import duckdb
    import pyarrow as pa
    from concurrent.futures import ThreadPoolExecutor, as_completed

    batches = []
    completed = 0
    total = len(tiles)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(process_tile_to_h3, tile, tms, items, band, h3_res, resolution, **tile_kwargs): tile
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
