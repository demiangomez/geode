"""
Cartopy-based map renderer for field visualization.
"""

import numpy as np
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from typing import Tuple, Optional, List
import geopandas as gpd
from shapely.geometry import Point, shape


class CartopyRenderer:
    """
    Handles Cartopy map setup, coordinate transforms, and map features.
    """

    # Plate boundary line segments (lon, lat), loaded once and shared across instances
    _plate_boundaries: Optional[List[Tuple[np.ndarray, np.ndarray]]] = None

    def __init__(self, resolution: str = '50m', show_plate_boundaries: bool = True,
                 plate_boundary_color: str = 'darkred', plate_boundary_width: float = 0.8):
        """
        Initialize the renderer.

        Parameters
        ----------
        resolution : str
            Cartopy feature resolution: '10m', '50m', or '110m'
        show_plate_boundaries : bool
            Overlay the Bird (2003) PB2002 tectonic plate boundaries
        plate_boundary_color : str
            Line color for plate boundaries
        plate_boundary_width : float
            Line width for plate boundaries
        """
        self.resolution = resolution
        self.show_plate_boundaries = show_plate_boundaries
        self.plate_boundary_color = plate_boundary_color
        self.plate_boundary_width = plate_boundary_width
        self.data_crs = ccrs.PlateCarree()
        self.projection = None
        self._extent = None

    def get_projection(self, lon: np.ndarray, lat: np.ndarray) -> ccrs.Projection:
        """
        Get the map projection centered on the data.

        Parameters
        ----------
        lon, lat : np.ndarray
            Longitude and latitude coordinates

        Returns
        -------
        ccrs.Projection
            Mercator projection centered on the data
        """
        central_lon = (lon.min() + lon.max()) / 2
        self.projection = ccrs.Mercator(central_longitude=central_lon)
        return self.projection

    def setup(self, ax, lon: np.ndarray, lat: np.ndarray, pad_fraction: float = 0.1) -> None:
        """
        Set up the map on an axis.

        Parameters
        ----------
        ax : matplotlib.axes.Axes
            Axis with Cartopy projection
        lon, lat : np.ndarray
            Data coordinates for setting extent
        pad_fraction : float
            Padding as fraction of data range
        """
        lon_min, lon_max = lon.min(), lon.max()
        lat_min, lat_max = lat.min(), lat.max()

        lon_pad = (lon_max - lon_min) * pad_fraction
        lat_pad = (lat_max - lat_min) * pad_fraction

        self._extent = [
            lon_min - lon_pad,
            lon_max + lon_pad,
            lat_min - lat_pad,
            lat_max + lat_pad
        ]

        ax.set_extent(self._extent, crs=self.data_crs)
        self.add_features(ax)

    def add_features(self, ax) -> None:
        """
        Add map features (coastlines, borders, etc.) to an axis.

        Parameters
        ----------
        ax : matplotlib.axes.Axes
            Axis with Cartopy projection
        """
        # Add land with fill
        ax.add_feature(
            cfeature.LAND.with_scale(self.resolution),
            facecolor='wheat',
            alpha=0.3
        )

        # Add ocean
        ax.add_feature(
            cfeature.OCEAN.with_scale(self.resolution),
            facecolor='lightblue',
            alpha=0.3
        )

        # Add coastlines
        ax.add_feature(
            cfeature.COASTLINE.with_scale(self.resolution),
            linewidth=0.5
        )

        # Add country borders
        ax.add_feature(
            cfeature.BORDERS.with_scale(self.resolution),
            linewidth=0.5
        )

        # Add state/province borders (international - admin level 1)
        # Use 10m scale for better coverage of all countries' administrative boundaries
        # Use polygon version (admin_1_states_provinces) which has better coverage than lines
        states_provinces = cfeature.NaturalEarthFeature(
            category='cultural',
            name='admin_1_states_provinces',
            scale='10m',
            facecolor='none'
        )
        ax.add_feature(states_provinces, edgecolor='dimgray', linewidth=0.4, linestyle=':')

        # Add tectonic plate boundaries
        if self.show_plate_boundaries:
            for lon, lat in self._load_plate_boundaries():
                ax.plot(
                    lon, lat,
                    color=self.plate_boundary_color,
                    linewidth=self.plate_boundary_width,
                    linestyle='-',
                    alpha=0.8,
                    zorder=2,
                    transform=self.data_crs
                )

        # Add gridlines with labels
        gl = ax.gridlines(
            draw_labels=True,
            linewidth=0.3,
            color='gray',
            alpha=0.5,
            linestyle='--'
        )
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {'size': 8}
        gl.ylabel_style = {'size': 8}

    def transform(self, lon: np.ndarray, lat: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Transform lon/lat coordinates to projection coordinates.

        Parameters
        ----------
        lon, lat : np.ndarray
            Geographic coordinates

        Returns
        -------
        x, y : np.ndarray
            Projected coordinates
        """
        if self.projection is None:
            raise ValueError("Projection not set. Call get_projection() first.")

        # Transform coordinates
        coords = self.projection.transform_points(self.data_crs, lon, lat)
        return coords[:, 0], coords[:, 1]

    def transform_grid(self, lon_grid: np.ndarray, lat_grid: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Transform a 2D grid of lon/lat coordinates to projection coordinates.

        Parameters
        ----------
        lon_grid, lat_grid : np.ndarray
            2D arrays of geographic coordinates

        Returns
        -------
        x_grid, y_grid : np.ndarray
            2D arrays of projected coordinates
        """
        if self.projection is None:
            raise ValueError("Projection not set. Call get_projection() first.")

        original_shape = lon_grid.shape
        lon_flat = lon_grid.flatten()
        lat_flat = lat_grid.flatten()

        coords = self.projection.transform_points(self.data_crs, lon_flat, lat_flat)

        x_grid = coords[:, 0].reshape(original_shape)
        y_grid = coords[:, 1].reshape(original_shape)

        return x_grid, y_grid

    def inverse_transform(self, x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Transform projection coordinates back to lon/lat.

        Parameters
        ----------
        x, y : np.ndarray
            Projected coordinates

        Returns
        -------
        lon, lat : np.ndarray
            Geographic coordinates
        """
        if self.projection is None:
            raise ValueError("Projection not set. Call get_projection() first.")

        coords = self.data_crs.transform_points(self.projection, x, y)
        return coords[:, 0], coords[:, 1]

    def inverse_transform_grid(self, x_grid: np.ndarray, y_grid: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Transform a 2D grid of projection coordinates back to lon/lat.

        Parameters
        ----------
        x_grid, y_grid : np.ndarray
            2D arrays of projected coordinates

        Returns
        -------
        lon_grid, lat_grid : np.ndarray
            2D arrays of geographic coordinates
        """
        if self.projection is None:
            raise ValueError("Projection not set. Call get_projection() first.")

        original_shape = x_grid.shape
        x_flat = x_grid.flatten()
        y_flat = y_grid.flatten()

        coords = self.data_crs.transform_points(self.projection, x_flat, y_flat)

        lon_grid = coords[:, 0].reshape(original_shape)
        lat_grid = coords[:, 1].reshape(original_shape)

        return lon_grid, lat_grid

    @classmethod
    def _load_plate_boundaries(cls) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Load the Bird (2003) PB2002 tectonic plate polygons and return their
        boundaries as a flat list of (lon, lat) line segments, ready to plot
        with ax.plot(). Loaded once and cached on the class.

        Returns
        -------
        list of (lon, lat) tuples
            One entry per plate polygon exterior (adjacent plates sharing a
            boundary each contribute their own copy of it).
        """
        if cls._plate_boundaries is not None:
            return cls._plate_boundaries

        import json

        try:
            # Python 3.9+
            from importlib.resources import files
            data_path = files('geode.elasticity.data').joinpath('PB2002_plates.json')
            with data_path.open('r') as f:
                geojson = json.load(f)
        except (ImportError, AttributeError):
            # Python 3.7-3.8 fallback
            from importlib.resources import path as resource_path
            import geode.elasticity.data

            with resource_path(geode.elasticity.data, 'PB2002_plates.json') as p:
                with open(p) as f:
                    geojson = json.load(f)

        lines = []
        for feature in geojson['features']:
            geom = shape(feature['geometry'])
            polygons = geom.geoms if geom.geom_type == 'MultiPolygon' else [geom]
            for poly in polygons:
                x, y = poly.exterior.coords.xy
                lines.append((np.array(x), np.array(y)))

        cls._plate_boundaries = lines
        return lines

    @staticmethod
    def mask_ocean_points(lon: np.ndarray, lat: np.ndarray, buffer_distance: float = 0.0) -> np.ndarray:
        """
        Use GeoPandas for land/ocean masking.

        Parameters
        ----------
        lon, lat : array-like
            Point coordinates
        buffer_distance : float
            Distance to buffer land polygons (in km).
            Use 0 for exact land boundary.

        Returns
        -------
        is_land : np.ndarray
            Boolean array, True for points on land
        """
        # Load Natural Earth land polygons
        try:
            # Python 3.9+
            from importlib.resources import files
            data_path = files('geode.elasticity.data').joinpath('ne_50m_land.shp')
            filename = str(data_path)
        except (ImportError, AttributeError):
            # Python 3.7-3.8 fallback
            from importlib.resources import path as resource_path
            import geode.elasticity.data

            with resource_path(geode.elasticity.data, 'ne_50m_land.shp') as p:
                filename = str(p)

        world = gpd.read_file(filename)

        # Buffer the land polygons if offset requested
        if buffer_distance > 0:
            # Project to equal-area projection for accurate buffering
            world_proj = world.to_crs('ESRI:54009')  # World Mollweide
            world_proj['geometry'] = world_proj.geometry.buffer(buffer_distance * 1000.)
            world = world_proj.to_crs('EPSG:4326')  # Back to lat/lon

        # Create GeoDataFrame of points
        points = gpd.GeoDataFrame(
            geometry=[Point(x, y) for x, y in zip(lon, lat)],
            crs="EPSG:4326"
        )

        # Spatial join to find points on/near land
        points_on_land = gpd.sjoin(points, world, how='left', predicate='within')
        # Remove duplicates caused by overlapping polygons
        points_on_land = points_on_land[~points_on_land.index.duplicated(keep='first')]
        is_land = ~points_on_land.index_right.isna()

        return is_land.values
