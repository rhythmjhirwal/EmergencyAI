"""Recalculate emergency routes under simulated traffic and road closures."""

from collections.abc import Mapping, Sequence
import math
from typing import Any

import networkx as nx

from algorithms.a_star import find_optimal_route

TRAFFIC_PENALTY_MULTIPLIERS = {
    'low': 1.0,
    'moderate': 1.3,
    'heavy': 1.75,
}


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number.')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{label} must be a finite number.')
    return number


def _request_value(request: Mapping[str, Any], camel_name: str, snake_name: str) -> Any:
    return request[camel_name] if camel_name in request else request.get(snake_name)


def _parse_road_segments(graph: nx.Graph, segments: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
        raise ValueError(f'{label} must be a list of road segments.')

    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for segment in segments:
        if isinstance(segment, Mapping):
            start = segment.get('from')
            end = segment.get('to')
            key = segment.get('key')
        elif isinstance(segment, Sequence) and not isinstance(segment, (str, bytes)) and len(segment) in {2, 3}:
            start, end = segment[0], segment[1]
            key = segment[2] if len(segment) == 3 else None
        else:
            raise ValueError(f'Each {label} entry must be a {{from, to, key?}} object or a two/three-item pair.')

        try:
            exists = graph.has_edge(start, end) or graph.has_edge(end, start)
            if key is not None and graph.is_multigraph():
                exists = graph.has_edge(start, end, key) or graph.has_edge(end, start, key)
            identity = (str(min(str(start), str(end))), str(max(str(start), str(end))), str(key))
        except TypeError as error:
            raise ValueError(f'{label} endpoints must be valid graph node IDs.') from error
        if not exists:
            raise ValueError(f'{label} segment does not exist: {start!r} to {end!r}.')
        if identity not in seen:
            parsed_segment = {'from': start, 'to': end}
            if key is not None:
                parsed_segment['key'] = key
            parsed.append(parsed_segment)
            seen.add(identity)
    return parsed


def _updated_graph(
    road_graph: nx.Graph,
    traffic_multiplier: float,
    congested_roads: Sequence[Mapping[str, Any]],
    blocked_roads: Sequence[Mapping[str, Any]],
) -> tuple[nx.Graph, list[dict[str, Any]]]:
    updated = road_graph.copy(as_view=False)
    adjustments = []

    def matches(segment: Mapping[str, Any], start: Any, end: Any, key: Any) -> bool:
        same_road = (segment['from'] == start and segment['to'] == end) or (
            segment['from'] == end and segment['to'] == start
        )
        return same_road and (segment.get('key') is None or segment.get('key') == key)

    if updated.is_multigraph():
        edge_items = list(updated.edges(keys=True, data=True))
    else:
        edge_items = [(start, end, None, attributes) for start, end, attributes in updated.edges(data=True)]

    for start, end, key, attributes in edge_items:
        original_time = _finite_number(
            attributes.get('base_estimated_travel_time_min', attributes.get('estimated_travel_time_min')),
            'estimated_travel_time_min',
        )
        original_traffic = _finite_number(
            attributes.get('base_traffic_cost_min', attributes.get('traffic_cost_min', 0)),
            'traffic_cost_min',
        )
        if original_time <= 0 or original_traffic < 0:
            raise ValueError('Road travel time must be positive and traffic cost cannot be negative.')

        congested = any(matches(segment, start, end, key) for segment in congested_roads)
        adjusted_time = original_time * traffic_multiplier if congested else original_time
        attributes['base_estimated_travel_time_min'] = original_time
        attributes['base_traffic_cost_min'] = original_traffic
        attributes['estimated_travel_time_min'] = adjusted_time
        attributes['traffic_cost_min'] = original_traffic
        attributes['route_cost_min'] = adjusted_time + original_traffic
        if congested:
            adjustment = {
                'from': start,
                'to': end,
                'originalTravelTimeMinutes': original_time,
                'updatedTravelTimeMinutes': adjusted_time,
            }
            if key is not None:
                adjustment['key'] = key
            adjustments.append(adjustment)

    if updated.is_multigraph():
        for start, end, key, _attributes in list(updated.edges(keys=True, data=True)):
            if any(matches(segment, start, end, key) for segment in blocked_roads):
                updated.remove_edge(start, end, key)
    else:
        for start, end in list(updated.edges):
            if any(matches(segment, start, end, None) for segment in blocked_roads):
                updated.remove_edge(start, end)

    return updated, adjustments


def calculate_adaptive_route(
    road_graph: nx.Graph,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare a baseline A* route with one under simulated traffic/closures.

    Request fields are startNode, destinationNode, trafficScenario (Low,
    Moderate, or Heavy), and optional blockedRoads. Snake_case equivalents are
    also accepted for Python callers. Each traffic multiplier scales the
    graph's existing per-road traffic_cost_min into estimated travel time.
    The supplied graph is never mutated.
    """
    if not isinstance(road_graph, nx.Graph):
        raise ValueError('road_graph must be a NetworkX graph.')
    if not isinstance(request, Mapping):
        raise ValueError('request must be an object.')

    start_node = _request_value(request, 'startNode', 'start_node')
    destination_node = _request_value(request, 'destinationNode', 'destination_node')
    scenario_value = _request_value(request, 'trafficScenario', 'traffic_scenario')
    if not isinstance(scenario_value, str):
        raise ValueError('trafficScenario must be Low, Moderate, or Heavy.')
    scenario_key = scenario_value.strip().lower()
    if scenario_key not in TRAFFIC_PENALTY_MULTIPLIERS:
        raise ValueError('trafficScenario must be Low, Moderate, or Heavy.')

    blocked_value = _request_value(request, 'blockedRoads', 'blocked_roads')
    blocked_roads = _parse_road_segments(
        road_graph,
        blocked_value if blocked_value is not None else [],
        'blockedRoads',
    )
    congested_value = _request_value(request, 'congestedRoads', 'congested_roads')
    congested_roads = _parse_road_segments(
        road_graph,
        congested_value if congested_value is not None else [],
        'congestedRoads',
    )
    multiplier = TRAFFIC_PENALTY_MULTIPLIERS[scenario_key]

    try:
        original_route = find_optimal_route(road_graph, start_node, destination_node)
    except nx.NetworkXNoPath:
        return {
            'status': 'no_original_route',
            'trafficScenario': scenario_key.title(),
            'trafficPenaltyMultiplier': multiplier,
            'originalRoute': None,
            'newRoute': None,
            'coordinates': [],
            'originalEtaMinutes': None,
            'newEtaMinutes': None,
            'originalDistanceKm': None,
            'newDistanceKm': None,
            'etaDifferenceMinutes': None,
            'blockedRoads': blocked_roads,
            'congestedRoads': congested_roads,
            'trafficAdjustments': {'updatedRoadCount': 0, 'edges': []},
            'message': 'The original road graph has no route between these locations.',
        }

    if scenario_key != 'low' and not congested_roads:
        congested_roads = [
            {'from': segment['from'], 'to': segment['to'], 'key': segment.get('key')}
            for segment in original_route['edgeSegments']
        ]

    scenario_graph, adjustments = _updated_graph(road_graph, multiplier, congested_roads, blocked_roads)
    try:
        new_route = find_optimal_route(scenario_graph, start_node, destination_node)
    except nx.NetworkXNoPath:
        return {
            'status': 'destination_unreachable',
            'trafficScenario': scenario_key.title(),
            'trafficPenaltyMultiplier': multiplier,
            'originalRoute': original_route,
            'newRoute': None,
            'coordinates': [],
            'originalEtaMinutes': original_route['etaMinutes'],
            'newEtaMinutes': None,
            'originalDistanceKm': original_route['distanceKm'],
            'newDistanceKm': None,
            'etaDifferenceMinutes': None,
            'blockedRoads': blocked_roads,
            'congestedRoads': congested_roads,
            'trafficAdjustments': {'updatedRoadCount': len(adjustments), 'edges': adjustments},
            'message': 'The simulated road closures disconnect the destination from the origin.',
        }

    original_eta = original_route['etaMinutes']
    new_eta = new_route['etaMinutes']
    return {
        'status': 'route_updated',
        'trafficScenario': scenario_key.title(),
        'trafficPenaltyMultiplier': multiplier,
        'originalRoute': original_route,
        'newRoute': new_route,
        'coordinates': new_route['coordinates'],
        'originalEtaMinutes': original_eta,
        'newEtaMinutes': new_eta,
        'originalDistanceKm': original_route['distanceKm'],
        'newDistanceKm': new_route['distanceKm'],
        'etaDifferenceMinutes': new_eta - original_eta,
        'blockedRoads': blocked_roads,
        'congestedRoads': congested_roads,
        'trafficAdjustments': {'updatedRoadCount': len(adjustments), 'edges': adjustments},
        'message': 'Route recalculated using simulated traffic and road closures.',
    }