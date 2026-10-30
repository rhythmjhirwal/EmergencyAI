"""Travel-time A* routing over a NetworkX road graph."""

from collections.abc import Mapping
import math
from typing import Any

import networkx as nx

EARTH_RADIUS_KM = 6371.0088
DEFAULT_MAX_ASSUMED_SPEED_KMH = 130.0


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number.')
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f'{name} must be a finite number.')
    return converted


def _valid_coordinates(latitude: Any, longitude: Any, node_id: Any) -> tuple[float, float]:
    lat = _number(latitude, f'Latitude for node {node_id!r}')
    lng = _number(longitude, f'Longitude for node {node_id!r}')
    if not -90 <= lat <= 90:
        raise ValueError(f'Latitude for node {node_id!r} must be between -90 and 90.')
    if not -180 <= lng <= 180:
        raise ValueError(f'Longitude for node {node_id!r} must be between -180 and 180.')
    return lat, lng


def _haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    first_lat, first_lng = map(math.radians, first)
    second_lat, second_lng = map(math.radians, second)
    latitude_delta = second_lat - first_lat
    longitude_delta = second_lng - first_lng
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(first_lat) * math.cos(second_lat) * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, haversine)))


def _node_coordinates(graph: nx.Graph, node_id: Any) -> tuple[float, float]:
    attributes = graph.nodes[node_id]
    latitude = attributes.get('y', attributes.get('latitude'))
    longitude = attributes.get('x', attributes.get('longitude'))
    if latitude is None or longitude is None:
        raise ValueError(f'Node {node_id!r} is missing OSM y/x or latitude/longitude coordinates.')
    return _valid_coordinates(latitude, longitude, node_id)


def _single_edge_cost(attributes: Mapping[str, Any]) -> float | None:
    if attributes.get('closed', False):
        return None

    if attributes.get('estimated_travel_time_min') is not None:
        travel_time = _number(attributes['estimated_travel_time_min'], 'estimated_travel_time_min')
    elif attributes.get('travel_time') is not None:
        travel_time = _number(attributes['travel_time'], 'travel_time') / 60
    elif attributes.get('length') is not None and attributes.get('speed_kph') is not None:
        length_km = _number(attributes['length'], 'length') / 1000
        speed_kph = _number(attributes['speed_kph'], 'speed_kph')
        if speed_kph <= 0:
            raise ValueError('speed_kph must be greater than zero.')
        travel_time = length_km * 60 / speed_kph
    else:
        raise ValueError('Road edge is missing travel time or length/speed attributes.')

    traffic_cost = _number(attributes.get('traffic_cost_min', 0), 'traffic_cost_min')
    if travel_time <= 0 or traffic_cost < 0:
        raise ValueError('Road travel time must be positive and traffic cost cannot be negative.')
    return travel_time + traffic_cost


def _edge_cost(_start: Any, _end: Any, attributes: Mapping[str, Any]) -> float | None:
    if 'estimated_travel_time_min' in attributes or 'travel_time' in attributes or 'length' in attributes:
        return _single_edge_cost(attributes)

    edge_costs = [
        cost
        for edge_attributes in attributes.values()
        if isinstance(edge_attributes, Mapping)
        if (cost := _single_edge_cost(edge_attributes)) is not None
    ]
    return min(edge_costs) if edge_costs else None


def _iter_edges(graph: nx.Graph):
    if graph.is_multigraph():
        yield from graph.edges(keys=True, data=True)
    else:
        for start, end, attributes in graph.edges(data=True):
            yield start, end, None, attributes


def _best_edge(graph: nx.Graph, start: Any, end: Any) -> tuple[Any, Mapping[str, Any]]:
    edge_data = graph.get_edge_data(start, end)
    if edge_data is None:
        raise ValueError(f'Route edge {start!r} to {end!r} is missing from the graph.')
    if graph.is_multigraph():
        candidates = [
            (key, attributes, _single_edge_cost(attributes))
            for key, attributes in edge_data.items()
        ]
        candidates = [candidate for candidate in candidates if candidate[2] is not None]
        if not candidates:
            raise ValueError(f'Route edge {start!r} to {end!r} has no traversable edge.')
        key, attributes, _cost = min(candidates, key=lambda item: item[2])
        return key, attributes
    return None, edge_data


def _edge_geometry_coordinates(
    graph: nx.Graph,
    start: Any,
    end: Any,
    attributes: Mapping[str, Any],
) -> list[tuple[float, float]]:
    start_latitude, start_longitude = _node_coordinates(graph, start)
    end_latitude, end_longitude = _node_coordinates(graph, end)
    geometry = attributes.get('geometry')

    if geometry is None:
        # OSMnx omits geometry on some direct, adjacent-node road edges. Keep
        # this fallback local to that edge; never replace the whole route path.
        points = [(start_longitude, start_latitude), (end_longitude, end_latitude)]
    else:
        if isinstance(geometry, str):
            try:
                from shapely import wkt

                geometry = wkt.loads(geometry)
            except Exception as error:
                raise ValueError(f'Road edge {start!r} to {end!r} has invalid geometry.') from error
        if not hasattr(geometry, 'coords'):
            raise ValueError(f'Road edge {start!r} to {end!r} geometry is not a coordinate line.')
        points = [(float(longitude), float(latitude)) for longitude, latitude, *_rest in geometry.coords]
        if len(points) < 2:
            raise ValueError(f'Road edge {start!r} to {end!r} geometry has fewer than two coordinates.')

        start_xy = (start_longitude, start_latitude)
        end_xy = (end_longitude, end_latitude)
        forward_error = _haversine_km((points[0][1], points[0][0]), (start_xy[1], start_xy[0]))
        reverse_error = _haversine_km((points[-1][1], points[-1][0]), (start_xy[1], start_xy[0]))
        if reverse_error < forward_error:
            points.reverse()

    return points


def find_optimal_route(
    graph: nx.Graph,
    start_node: Any,
    destination_node: Any,
    max_assumed_speed_kmh: float = DEFAULT_MAX_ASSUMED_SPEED_KMH,
) -> dict[str, Any]:
    """Return the minimum travel-time route and its distance/time totals.

    The route objective is the sum of estimated_travel_time_min and
    traffic_cost_min. NetworkX A* uses f(n) = g(n) + h(n), where h is
    straight-line travel time. The heuristic speed is at least the fastest
    implied edge speed, keeping the estimate admissible for the given graph.

    Raises ValueError for invalid graph nodes or location data and
    networkx.NetworkXNoPath when no open route connects the requested nodes.
    """
    if not isinstance(graph, nx.Graph):
        raise ValueError('graph must be a NetworkX graph.')
    if start_node not in graph:
        raise ValueError(f'Unknown start location: {start_node!r}.')
    if destination_node not in graph:
        raise ValueError(f'Unknown destination location: {destination_node!r}.')

    configured_speed = _number(max_assumed_speed_kmh, 'max_assumed_speed_kmh')
    if configured_speed <= 0:
        raise ValueError('max_assumed_speed_kmh must be greater than zero.')

    coordinates = {node: _node_coordinates(graph, node) for node in graph.nodes}
    fastest_implied_speed = 0.0
    for _start, _end, _key, attributes in _iter_edges(graph):
        if attributes.get('closed', False):
            continue
        distance_value = attributes.get('distance_km')
        if distance_value is None:
            distance_value = _number(attributes.get('length'), 'length') / 1000
        distance = _number(distance_value, 'distance_km')
        cost = _single_edge_cost(attributes)
        if distance <= 0 or cost is None:
            raise ValueError('Road distance and travel time must be positive.')
        base_time = attributes.get('estimated_travel_time_min')
        if base_time is None:
            base_time = attributes.get('travel_time')
            if base_time is not None:
                base_time = _number(base_time, 'travel_time') / 60
            else:
                base_time = distance * 60 / _number(attributes.get('speed_kph'), 'speed_kph')
        else:
            base_time = _number(base_time, 'estimated_travel_time_min')
        if base_time <= 0:
            raise ValueError('Road travel time must be positive.')
        fastest_implied_speed = max(fastest_implied_speed, distance * 60 / base_time)

    heuristic_speed = max(configured_speed, fastest_implied_speed)
    destination_coordinates = coordinates[destination_node]

    def heuristic(node: Any, _destination: Any) -> float:
        return _haversine_km(coordinates[node], destination_coordinates) * 60 / heuristic_speed

    nodes = nx.astar_path(
        graph,
        start_node,
        destination_node,
        heuristic=heuristic,
        weight=_edge_cost,
    )

    total_distance = 0.0
    base_travel_time = 0.0
    traffic_cost = 0.0
    route_coordinates = [coordinates[nodes[0]]]
    edge_segments = []
    for start, end in zip(nodes, nodes[1:]):
        key, edge = _best_edge(graph, start, end)
        distance_value = edge.get('distance_km')
        if distance_value is None:
            distance_value = _number(edge.get('length'), 'length') / 1000
        total_distance += _number(distance_value, 'distance_km')
        edge_cost = _single_edge_cost(edge)
        if edge_cost is None:
            raise ValueError(f'Route edge {start!r} to {end!r} is closed.')
        traffic_value = _number(edge.get('traffic_cost_min', 0), 'traffic_cost_min')
        base_travel_time += edge_cost - traffic_value
        traffic_cost += traffic_value

        edge_coordinates = _edge_geometry_coordinates(graph, start, end, edge)
        edge_lat_lng = [(latitude, longitude) for longitude, latitude in edge_coordinates]
        if route_coordinates[-1] == edge_lat_lng[0]:
            route_coordinates.extend(edge_lat_lng[1:])
        else:
            route_coordinates.extend(edge_lat_lng)
        edge_segments.append({'from': start, 'to': end, 'key': key})

    if len(nodes) == 1:
        route_coordinates = [coordinates[nodes[0]]]

    return {
        'nodes': nodes,
        'coordinates': [list(point) for point in route_coordinates],
        'edgeSegments': edge_segments,
        'distanceKm': total_distance,
        'baseTravelTimeMinutes': base_travel_time,
        'trafficCostMinutes': traffic_cost,
        'etaMinutes': base_travel_time + traffic_cost,
    }