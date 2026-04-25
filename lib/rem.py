"""REM (Relative Elevation Model) functions — HyRiver-style IDW approach.

Extracted from river_rem_s3dep_v2.py (which has the UTM projection fix for smoothing).
Shared by: river_rem_h3.py, river_rem_s3dep.py, river_rem_s3dep_v2.py

All heavy imports are inside function bodies — no import-time deps.
"""


def get_flowlines(bbox, dem_crs):
    """Get NHDPlus flowlines for bbox, extract main stem, smooth.

    Uses UTM projection for smoothing to avoid GEOSException crash
    with geographic coordinates (the fix from river_rem_s3dep_v2.py).
    """
    import numpy as np
    import geopandas as gpd
    import pygeoutils as geoutils
    import pynhd
    from shapely import ops

    wd = pynhd.WaterData("nhdflowline_network")
    flw = wd.bybox(bbox)
    flw = pynhd.prepare_nhdplus(flw, 0, 0, 0, remove_isolated=True)
    flw = flw[flw.levelpathi == flw.levelpathi.min()].to_crs(dem_crs).copy()
    print(f"Main stem: {len(flw)} segments")

    river_line = ops.linemerge(flw.geometry.tolist())

    # Project to UTM for smoothing (avoids issues with geographic coordinates)
    bounds = river_line.bounds
    lon_center = (bounds[0] + bounds[2]) / 2
    lat_center = (bounds[1] + bounds[3]) / 2
    utm_zone = int((lon_center + 180) / 6) + 1
    utm_epsg = 32600 + utm_zone if lat_center >= 0 else 32700 + utm_zone

    gdf_temp = gpd.GeoDataFrame([1], geometry=[river_line], crs=dem_crs)
    gdf_utm = gdf_temp.to_crs(f"EPSG:{utm_epsg}")
    river_utm = gdf_utm.geometry.iloc[0]

    npts = max(10, int(np.ceil(river_utm.length / 10)))
    river_smooth = geoutils.smooth_linestring(river_utm, 0.1, npts)

    # Project back to dem_crs
    gdf_result = gpd.GeoDataFrame([1], geometry=[river_smooth], crs=utm_epsg)
    river_line = gdf_result.to_crs(dem_crs).geometry.iloc[0]

    print(f"Smoothed river: {npts} points")
    return river_line


def sample_river_elevation(dem, river_line):
    """Sample DEM elevation along the river centerline."""
    import numpy as np
    import xarray as xr

    coords = np.array(river_line.coords)
    # Deduplicate tile-overlap coordinates so interp works
    _, ux = np.unique(dem.x.values, return_index=True)
    _, uy = np.unique(dem.y.values, return_index=True)
    dem = dem.isel(x=np.sort(ux), y=np.sort(uy))
    x_da = xr.DataArray(coords[:, 0], dims="points")
    y_da = xr.DataArray(coords[:, 1], dims="points")
    z = dem.interp(x=x_da, y=y_da, method="nearest").values
    mask = np.isfinite(z)
    river_elev = np.c_[coords[mask], z[mask]]
    print(f"Sampled {len(river_elev)} river elevation points ({mask.sum()} valid)")
    return river_elev


def compute_rem(dem, river_elev):
    """IDW-interpolate river surface elevation, subtract from DEM."""
    import numpy as np
    import xarray as xr
    import opt_einsum as oe
    from scipy.spatial import KDTree

    # Deduplicate tile-overlap coordinates
    _, ux = np.unique(dem.x.values, return_index=True)
    _, uy = np.unique(dem.y.values, return_index=True)
    dem = dem.isel(x=np.sort(ux), y=np.sort(uy))
    print("Building KDTree and computing IDW weights...")
    dem_points = np.dstack(np.meshgrid(dem.x.values, dem.y.values)).reshape(-1, 2)

    k = min(200, len(river_elev))
    distances, idxs = KDTree(river_elev[:, :2]).query(
        dem_points, k=k, workers=-1
    )

    w = np.reciprocal(np.power(distances, 2) + np.isclose(distances, 0))
    w_sum = np.sum(w, axis=1)
    w_norm = oe.contract(
        "ij,i->ij", w, np.reciprocal(w_sum + np.isclose(w_sum, 0)), optimize="optimal"
    )
    elevation = oe.contract("ij,ij->i", w_norm, river_elev[idxs, 2], optimize="optimal")
    elevation = elevation.reshape((dem.sizes["y"], dem.sizes["x"]))

    river_surface = xr.DataArray(elevation, dims=("y", "x"), coords={"x": dem.x, "y": dem.y})
    rem = dem - river_surface
    print(f"REM range: {float(rem.min()):.1f}m to {float(rem.max()):.1f}m")
    return rem
