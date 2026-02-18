"""Flat xarray DataArray -> H3 hexagon aggregation via DuckDB.

Shared by: elevation_1m.py, river_rem_s3dep.py, river_rem_s3dep_v2.py, river_rem_h3.py

All heavy imports are inside function bodies — no import-time deps.
"""


def aggregate_to_h3(data_array, h3_res, value_column="elevation", memory_limit="2GB"):
    """Flatten xarray DataArray to lat/lng/value, aggregate to H3 via DuckDB.

    Handles CRS detection: if data is not EPSG:4326, reprojects coordinates.
    Replaces both aggregate_to_h3 and aggregate_rem_to_h3 from individual notebooks.

    Args:
        data_array: xarray DataArray with x/y coordinates and rio.crs set.
        h3_res: H3 resolution (0-15).
        value_column: Name for the value in the intermediate Arrow table.
        memory_limit: DuckDB memory limit string.

    Returns:
        PyArrow Table with columns (hex, metric).
    """
    import numpy as np
    import pyarrow as pa
    import duckdb
    from pyproj import Transformer

    crs = str(data_array.rio.crs)
    X, Y = np.meshgrid(data_array.x.values, data_array.y.values)

    if crs and "4326" not in crs:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lons, lats = transformer.transform(X.flatten(), Y.flatten())
    else:
        lons, lats = X.flatten(), Y.flatten()

    vals = data_array.values.flatten()
    mask = np.isfinite(vals)
    tile_pa = pa.table({
        "lat": pa.array(lats[mask], type=pa.float64()),
        "lng": pa.array(lons[mask], type=pa.float64()),
        value_column: pa.array(vals[mask], type=pa.float64()),
    })

    con = duckdb.connect()
    con.sql(f"SET memory_limit = '{memory_limit}'; LOAD h3;")
    hex_result = con.sql(f"""
        SELECT
            h3_latlng_to_cell_string(lat, lng, {h3_res}) AS hex,
            AVG({value_column}) AS metric
        FROM tile_pa
        GROUP BY 1
    """).fetch_arrow_table()
    con.close()
    print(f"H3 hexagons: {len(hex_result):,}")
    return hex_result
