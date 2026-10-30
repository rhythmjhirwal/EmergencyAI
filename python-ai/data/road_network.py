"""Cached OpenStreetMap drive graph for central Delhi."""

from collections.abc import Mapping
import math
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Any

import networkx as nx

DELHI_BBOX = (77.185, 28.575, 77.255, 28.655)
EARTH_RADIUS_KM = 6371.0088
DEFAULT_ESTIMATED_SPEED_KPH = 30.0


class RoadNetworkUnavailableError(RuntimeError):
    """Raised when the required OSM road graph cannot be loaded or downloaded."""


_graph_lock = Lock()
_cached_graph: nx.MultiDiGraph | None = None


def _cache_path() -> Path:
    cache_root = Path(os.environ.get(
        'EMERGENCYAI_OSM_CACHE_DIR',
        str(Path(tempfile.gettempdir()) / 'EmergencyAI' / 'osm'),
    ))
    return cache_root / 'central_delhi_drive.graphml'


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number.')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{label} must be a finite number.')
    return number


def _prepare_graph(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    if not isinstance(graph, nx.MultiDiGraph):
        raise ValueError('OSMnx must provide a directed multigraph.')
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        raise ValueError('The downloaded OSM drive graph has no routable roads.')

    for node_id, attributes in graph.nodes(data=True):
        latitude = attributes.get('y')
        longitude = attributes.get('x')
        if latitude is None or longitude is None:
            raise ValueError(f'OSM node {node_id!r} is missing coordinates.')
        latitude = _number(latitude, 'OSM node latitude')
        longitude = _number(longitude, 'OSM node longitude')
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f'OSM node {node_id!r} has invalid coordinates.')

    for start, end, key, attributes in graph.edges(keys=True, data=True):
        length_m = _number(attributes.get('length'), 'OSM edge length')
        travel_time_seconds = _number(attributes.get('travel_time'), 'OSM edge travel_time')
        speed_kph = _number(attributes.get('speed_kph'), 'OSM edge speed_kph')
        geometry = attributes.get('geometry')
        if length_m <= 0 or travel_time_seconds <= 0 or speed_kph <= 0:
            raise ValueError(f'OSM edge {start!r}->{end!r} ({key!r}) has non-positive route costs.')
        if isinstance(geometry, str):
            try:
                from shapely import wkt

                geometry = wkt.loads(geometry)
                attributes['geometry'] = geometry
            except Exception as error:
                raise ValueError(f'OSM edge {start!r}->{end!r} ({key!r}) has invalid street geometry.') from error
        if geometry is not None and (not hasattr(geometry, 'coords') or len(geometry.coords) < 2):
            raise ValueError(f'OSM edge {start!r}->{end!r} ({key!r}) has invalid street geometry.')

        travel_time_minutes = travel_time_seconds / 60.0
        attributes['distance_km'] = length_m / 1000.0
        attributes['base_estimated_travel_time_min'] = travel_time_minutes
        attributes['estimated_travel_time_min'] = travel_time_minutes
        attributes['base_traffic_cost_min'] = 0.0
        attributes['traffic_cost_min'] = 0.0
        attributes['route_cost_min'] = travel_time_minutes
        attributes['closed'] = False

    graph.graph['road_network_source'] = 'OpenStreetMap'
    graph.graph['network_type'] = 'drive'
    graph.graph['bbox'] = ','.join(str(value) for value in DELHI_BBOX)
    graph.graph['estimated_speed_source'] = 'OSM maxspeed with OSMnx imputation and a 30 km/h fallback'
    return graph


def load_drive_graph(force_refresh: bool = False) -> nx.MultiDiGraph:
    """Load cached or download a drivable OSM network around central Delhi.

    The returned graph is cached in memory and as GraphML. OSMnx imports are
    lazy so the Flask service can still start and report a clear unavailable
    status if its geospatial dependencies cannot load.
    """
    global _cached_graph

    with _graph_lock:
        if _cached_graph is not None and not force_refresh:
            return _cached_graph

        try:
            import osmnx as ox
        except Exception as error:
            raise RoadNetworkUnavailableError(
                f'OSMnx could not be loaded: {error}'
            ) from error

        cache_file = _cache_path()
        ox.settings.use_cache = True
        ox.settings.cache_folder = str(cache_file.parent / 'http')
        ox.settings.requests_timeout = 90
        cache_error = None

        if cache_file.exists() and not force_refresh:
            try:
                _cached_graph = _prepare_graph(ox.load_graphml(filepath=cache_file))
                return _cached_graph
            except Exception as error:
                cache_error = error

        try:
            graph = ox.graph_from_bbox(
                bbox=DELHI_BBOX,
                network_type='drive',
                simplify=True,
                retain_all=True,
                truncate_by_edge=True,
            )
            graph = ox.add_edge_speeds(graph, fallback=DEFAULT_ESTIMATED_SPEED_KPH)
            graph = ox.add_edge_travel_times(graph)
            graph = _prepare_graph(graph)

            cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = cache_file.with_suffix('.tmp.graphml')
            ox.save_graphml(graph, filepath=temporary_file)
            temporary_file.replace(cache_file)
        except Exception as error:
            cache_detail = f' Cached graph could not be loaded: {cache_error}.' if cache_error else ''
            raise RoadNetworkUnavailableError(
                f'OpenStreetMap drive network for central Delhi is unavailable.{cache_detail} Download failed: {error}'
            ) from error

        _cached_graph = graph
        return graph


def snap_to_nearest_road_node(
    graph: nx.Graph,
    latitude: float,
    longitude: float,
) -> tuple[Any, float]:
    """Return the nearest connected graph node and geodesic snap distance in km."""
    latitude = _number(latitude, 'latitude')
    longitude = _number(longitude, 'longitude')
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('Coordinates are outside valid latitude/longitude bounds.')

    try:
        import osmnx as ox

        nearest_node = ox.distance.nearest_nodes(graph, X=longitude, Y=latitude)
        attributes = graph.nodes[nearest_node]
        node_latitude = _number(attributes.get('y'), 'road-node latitude')
        node_longitude = _number(attributes.get('x'), 'road-node longitude')
        return nearest_node, _geodesic_distance_km(latitude, longitude, node_latitude, node_longitude)
    except Exception:
        # Test-injected graphs can still be snapped without importing OSMnx;
        # production graphs are only created by the OSMnx loader.
        pass

    nearest_node = None
    nearest_distance = math.inf
    for node_id, attributes in graph.nodes(data=True):
        if graph.degree(node_id) == 0:
            continue
        node_latitude = attributes.get('y', attributes.get('latitude'))
        node_longitude = attributes.get('x', attributes.get('longitude'))
        if node_latitude is None or node_longitude is None:
            continue
        node_latitude = _number(node_latitude, 'road-node latitude')
        node_longitude = _number(node_longitude, 'road-node longitude')
        distance = _geodesic_distance_km(latitude, longitude, node_latitude, node_longitude)
        if distance < nearest_distance:
            nearest_node = node_id
            nearest_distance = distance

    if nearest_node is None:
        raise ValueError('The road graph has no connected nodes with valid coordinates.')
    return nearest_node, nearest_distance


def _geodesic_distance_km(first_lat: float, first_lng: float, second_lat: float, second_lng: float) -> float:
    origin_latitude = math.radians(first_lat)
    delta_latitude = math.radians(second_lat) - origin_latitude
    delta_longitude = math.radians(second_lng) - math.radians(first_lng)
    haversine = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(origin_latitude)
        * math.cos(math.radians(second_lat))
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, haversine)))