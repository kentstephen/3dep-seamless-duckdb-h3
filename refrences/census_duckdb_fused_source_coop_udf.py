@fused.udf
def udf(bounds: fused.types.Bounds= [-126.6,20.9,-64.9,50.4], 
        res:int=None, 
        stats_type:str="mean_w_filter",
        png:bool=False,
        color_scale:float=1
):

    import pandas as pd
    # import duckdb
    common = fused.load("https://github.com/fusedio/udfs/tree/b7637ee/public/common/")
    tile = common.get_tiles(bounds, clip=True)


    df = run_query(tile)
    return df
    
def run_query(tile):
    common = fused.load("https://github.com/fusedio/udfs/tree/b7637ee/public/common/")
    # import duckdb
    # minx, miny, maxx, maxy = bounds
    # cali_clipped = cali_tiles(tile)
    xmin, ymin, xmax, ymax = tile.geometry.iloc[0].bounds
    query=f"""select * 
                from read_parquet('https://data.source.coop/fused/hex/release_2025_04_beta/census/2020_partitioned_h7.parquet')
            WHERE
            POP20 >= 1 and --state = '06'
    and (
        h3_cell_to_lat(hex) >= {ymin} -- make sure we don't have overlap bewtween tiles
        AND h3_cell_to_lat(hex) < {ymax}
        AND h3_cell_to_lng(hex) >= {xmin}
        AND h3_cell_to_lng(hex) < {xmax}
    )
                """
    con = common.duckdb_connect()
    return con.sql(query).df()
# @fused.cache
# def get_california_boundary():
#     """Load and dissolve California census tracts into single boundary."""
#     import geopandas as gpd

#     print("Loading California tracts...")
#     gdf = gpd.read_file(
#         "https://www2.census.gov/geo/tiger/TIGER_RD18/STATE/06_CALIFORNIA/06/tl_rd22_06_tract.zip"
#     )
#     gdf = gdf.dissolve().to_crs(4326)
#     if gdf is None or len(gdf)<0:
#         return None
#     return gdf
# def cali_tiles(tile):
#     import geopandas as gpd
#     gdf = get_california_boundary()
#     if gdf is None or len(gdf)<0:
#         return
#     # gdf.geometry = gdf.geometry.simplify(0.01)
#     # simplify for speed]
#     cali_clipped = gpd.clip(gdf, tile)
#     if cali_clipped is None or len(cali_clipped)<0:
#         return None
#     print("California boundary created")
#     return cali_clipped