# 'Shift + Enter' to execute this UDF as you pan the map or change the code directly
@fused.udf
def udf(
    bounds: fused.types.Bounds,
    collection="3dep-seamless",
    band="data",
    res_factor:int=4,
    res:int=11
):

    import odc.stac
    
    import palettable
    import planetary_computer
    import pystac_client
    from pystac.extensions.eo import EOExtension as eo
    # from utils import aggregate_df_hex, df_to_hex

    # Load pinned versions of utility functions.
    common = fused.load("https://github.com/fusedio/udfs/tree/b7637ee/public/common/")
    zoom = common.estimate_zoom(bounds)

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )
    items = catalog.search( 
        collections=[collection],
        bbox=bounds,
    ).item_collection()
    if not items or len(items) == 0:
        print("No items found for the given bounds and collection")
        return
    # print(items[0].assets.keys()) 
    # print(f"Returned {len(items)} Items")
    resolution = int(20/res_factor * 2 ** (max(0, 13 - zoom)))
    print(f"{resolution=}")
    ds = odc.stac.load(
        items,
        crs="EPSG:3857",
        bands=[band],
        resolution=resolution,
        bbox=bounds,
    ).astype(float)
    
    # Use data from the most recent time.
    arr = ds[band].max(dim="time")
    if arr is None:
        return
    # utils = fused.load('https://github.com/fusedio/udfs/tree/a18669/public/common/').utils
    # bounds_gdf = utils.bounds_to_gdf(bounds)
    # bounds_values = bounds_gdf.bounds.values[0]
    # df_arr_latlng = common.arr_to_latlng(arr.values, bounds)
    df_flow_latlng = get_flow(bounds, arr)
    # print(df.columns)
    df = aggregate_df_hex(df_flow_latlng, res, latlng_cols=("lat", "lng"), stats_type='max')
    # gdf = fused.run('fsh_2GwHfrChKC8BMStvcqvI61', bounds=bounds, res=res)
    # df['metric'] = df["metric"] - 1000
    print(df)
    return df

def get_flow(bounds, arr):
    import xarray as xr
    import numpy as np
    import tempfile
    from pyproj import Transformer
    from rasterio.transform import from_bounds
    import rasterio
    import pywbt
    import pandas as pd
    
    # Create xarray from numpy array with coordinates
    x_coords = np.linspace(bounds[0], bounds[2], arr.shape[-1])
    y_coords = np.linspace(bounds[1], bounds[3], arr.shape[-2])
    
    da = xr.DataArray(
        arr,
        coords={"y": y_coords, "x": x_coords},
        dims=["y", "x"]
    )
    
    # Buffer by 1000m in native CRS (3857)
    da_buffered = da.sel(
        x=slice(bounds[0] - 1000, bounds[2] + 1000),
        y=slice(bounds[1] - 1000, bounds[3] + 1000)
    )
    
    with tempfile.TemporaryDirectory() as tmp:
        # Write to temporary GeoTIFF for WhiteboxTools
        transform = from_bounds(
            da_buffered.x.min().item(), da_buffered.y.min().item(),
            da_buffered.x.max().item(), da_buffered.y.max().item(),
            len(da_buffered.x), len(da_buffered.y)
        )
        
        with rasterio.open(f"{tmp}/dem.tif", 'w', driver='GTiff',
                          height=da_buffered.shape[0], width=da_buffered.shape[1],
                          count=1, dtype=da_buffered.dtype, crs='EPSG:3857',
                          transform=transform) as dst:
            dst.write(da_buffered.values, 1)
        
        wbt_args = {
            "BreachDepressions": ["-i=dem.tif", "--fill_pits", "-o=dem_corr.tif"],
            "D8FlowAccumulation": ["-i=dem_corr.tif", "-o=flow.tif"]
        }
        pywbt.whitebox_tools(tmp, wbt_args, save_dir=tmp, 
                            wbt_root="WBT", zip_path="wbt_binaries.zip")
        
        # Read result and transform to 4326
        flow = xr.open_dataarray(f"{tmp}/flow.tif", engine="rasterio").squeeze()
        
        transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        X, Y = np.meshgrid(flow.x.values, flow.y.values)
        lons, lats = transformer.transform(X.flatten(), Y.flatten())
        
        df = pd.DataFrame({
            "lat": lats,
            "lng": lons,
            "data": flow.values.flatten()
        })
        
        return df.dropna()
        
# @fused.cache
# def df_to_hex(bounds, df, res, latlng_cols=("lat", "lng"), stats_type="mean"):  
#     common = fused.load("https://github.com/fusedio/udfs/tree/b7637ee/public/common/")
#     bounds = common.bounds_to_gdf(bounds) 
#     bounds_values = bounds.bounds.values[0]
#     print(latlng_cols)
#     xmin, ymin, xmax, ymax = bounds_values
#     qr = f"""
#             SELECT h3_latlng_to_cell({latlng_cols[0]}, {latlng_cols[1]}, {res}) AS hex, 
#                    ARRAY_AGG(data) as agg_data
#             FROM df
#             WHERE h3_cell_to_lat(hex) >= {ymin}
#               AND h3_cell_to_lat(hex) < {ymax}
#               AND h3_cell_to_lng(hex) >= {xmin}
#               AND h3_cell_to_lng(hex) < {xmax}
#             GROUP BY 1
#         """
#     con = common.duckdb_connect()
#     return con.query(qr).fetchnumpy()

def df_to_hex(df, res, latlng_cols=("lat", "lng")):  
    common = fused.load("https://github.com/fusedio/udfs/tree/b7637ee/public/common/")
    qr = f"""
            SELECT h3_latlng_to_cell({latlng_cols[0]}, {latlng_cols[1]}, {res}) AS hex, ARRAY_AGG(data) as agg_data
            FROM df
            group by 1
            
          --  order by 1
          
        """
    con = common.duckdb_connect()
    return con.query(qr).fetchnumpy()

@fused.cache
def aggregate_df_hex(df, res, latlng_cols, stats_type):
    import numpy as np
    import pandas as pd
    
    result = df_to_hex(df, res, latlng_cols)
    
    hex_arr = result['hex']
    agg_data_arr = result['agg_data']
    
    if stats_type == "max":
        metric = np.array([np.max(x) for x in agg_data_arr])
    else:
        metric = np.array([np.mean(x) for x in agg_data_arr])
    
    return pd.DataFrame({'hex': hex_arr, 'metric': metric})
def arr_to_latlng(arr, bounds):
    import numpy as np
    import pandas as pd
    from rasterio.transform import from_bounds
    from pyproj import Transformer
    
    # Create transformer from EPSG:3857 to EPSG:4326
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    
    transform = from_bounds(*bounds, arr.shape[-1], arr.shape[-2])
    x_list, y_list = shape_transform_to_xycoor(arr.shape[-2:], transform)
    X, Y = np.meshgrid(x_list, y_list)
    
    # Transform coordinates from 3857 to 4326
    X_4326, Y_4326 = transformer.transform(X.flatten(), Y.flatten())
    
    df = pd.DataFrame(X_4326, columns=["lng"])
    df["lat"] = Y_4326
    df["data"] = arr.flatten()
    return df