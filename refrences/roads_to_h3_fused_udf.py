@fused.udf
def udf(bounds: fused.types.Bounds = None,
         buffer_multiple: float = 1.5,
        resolution: int=11):
    import geopandas as gpd
    import shapely
    import pandas as pd

    
    # Using common fused functions as helper
    common = fused.load("https://github.com/fusedio/udfs/tree/0b1bd10/public/common/").utils
    # This helper function turns our bounds into XYZ tiles
    tiles = common.get_tiles(bounds, target_num_tiles=20)
    # Buffering tiles internally
    tiles.geometry = tiles.buffer(buffer_multiple / (tiles.z.iloc[0]) ** 2)
    
    overture_utils = fused.load("https://github.com/fusedio/udfs/tree/ee9bec5/public/Overture_Maps_Example/").utils # Load pinned versions of utility functions.
    res_offset = -1  # lower makes the hex finer
    # res = max(min(int(3 + zoom / 1.5), 12) - res_offset, 2)
    # print(res)
    utils = fused.load("https://github.com/fusedio/udfs/tree/e74035a1/public/common/").utils
    bounds = utils.bounds_to_gdf(bounds)
    gdf = fused.utils.Overture_Maps_Example.get_overture(bounds=tiles, overture_type='segment', min_zoom=0)
    if len(gdf) < 1:
        return
    elif gdf is None or gdf.empty:
        return
    # Filter by walking types
    walking_types = ['walking', 'pedestrian', 'footway', 'path', 'cycleway', 'sidewalk']
    gdf = gdf[gdf['class'].isin(walking_types)]
    
    # Filter out rows with null geometries BEFORE estimating UTM
    # gdf = gdf[~gdf.geometry.isna()]
    
    # Now you can safely estimate UTM and buffer
    if gdf is not None and not gdf.empty:
       gdf['geometry'] = gdf.to_crs(gdf.estimate_utm_crs()).buffer(100).to_crs('EPSG:4326')
    else:
        return
    # Convert to WKT
    gdf['geometry'] = gdf['geometry'].apply(shapely.wkt.dumps)
            

    # df_dem = fused.run("fsh_2bT8AoAunIsV5g7Rj4SC1B", x=x, y=y, z=z)
    df_overture = pd.DataFrame(gdf)

    def get_cells(df_overture, resolution):
        utils = fused.load(
        "https://github.com/fusedio/udfs/tree/be3bc93/public/common/"
        ).utils
        con = utils.duckdb_connect()
        query = f"""
with to_cells as (
  select unnest(h3_polygon_wkt_to_cells(geometry, {resolution})) AS hex
  from df_overture
) select
hex,
count(*) as cnt
from to_cells
group by hex


        """
        # Run the query and return a GeoDataFrame
        return con.sql(query).df()
    df = get_cells(df_overture, resolution)
    print(df)
    return df    
# return gdf_overture