"""Flask API for simulated EmergencyAI dispatch and route planning."""

from collections.abc import Mapping, Sequence
import logging
import os
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from services.adaptive_routing import calculate_adaptive_route
from services.dispatch import assign_emergency_vehicle
from services.explainability import generate_explanation
from data.road_network import RoadNetworkUnavailableError, load_drive_graph

logger = logging.getLogger(__name__)
TRAFFIC_SCENARIOS = {
    'normal': 'Low',
    'low': 'Low',
    'moderate': 'Moderate',
    'heavy': 'Heavy',
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_location(location: Any, label: str) -> str | None:
    if not isinstance(location, Mapping):
        return f'{label} must be an object with lat/lng coordinates.'
    latitude = location.get('lat', location.get('latitude'))
    longitude = location.get('lng', location.get('longitude'))
    if not _is_number(latitude) or not -90 <= latitude <= 90:
        return f'{label} latitude must be between -90 and 90.'
    if not _is_number(longitude) or not -180 <= longitude <= 180:
        return f'{label} longitude must be between -180 and 180.'
    return None


def _validate_incident(incident: Any) -> str | None:
    if not isinstance(incident, Mapping):
        return 'incident must be a JSON object.'
    if not isinstance(incident.get('type'), str) or incident['type'].strip().lower() not in {'accident', 'medical', 'fire'}:
        return 'incident type must be accident, medical, or fire.'
    if not isinstance(incident.get('severity'), str) or incident['severity'].strip().lower() not in {'low', 'medium', 'high', 'critical'}:
        return 'incident severity must be low, medium, high, or critical.'
    return _validate_location(incident.get('location'), 'incident location')


def _validate_vehicles(vehicles: Any) -> str | None:
    if not isinstance(vehicles, list):
        return 'vehicles must be a JSON array.'
    for index, vehicle in enumerate(vehicles):
        label = f'vehicles[{index}]'
        if not isinstance(vehicle, Mapping):
            return f'{label} must be a JSON object.'
        identifier = vehicle.get('id', vehicle.get('vehicleId'))
        if not isinstance(identifier, (str, int)) or isinstance(identifier, bool) or str(identifier).strip() == '':
            return f'{label} must include a non-empty id.'
        if not isinstance(vehicle.get('type'), str) or not vehicle['type'].strip():
            return f'{label} must include a vehicle type.'
        if 'status' not in vehicle and 'available' not in vehicle:
            return f'{label} must include status or available.'
        if 'status' in vehicle and not isinstance(vehicle['status'], str):
            return f'{label} status must be a string.'
        if 'available' in vehicle and not isinstance(vehicle['available'], bool):
            return f'{label} available must be a boolean.'
        location_error = _validate_location(vehicle.get('location'), f'{label} location')
        if location_error:
            return location_error
    return None


def _get_traffic_scenario(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    traffic = payload.get('traffic')
    if isinstance(traffic, Mapping):
        scenario = traffic.get('scenario', traffic.get('trafficScenario'))
    elif isinstance(traffic, str):
        scenario = traffic
    elif traffic is None:
        scenario = payload.get('trafficScenario', 'normal')
    else:
        return None, 'traffic must be a scenario string or an object containing scenario.'

    if not isinstance(scenario, str) or scenario.strip().lower() not in TRAFFIC_SCENARIOS:
        return None, 'traffic scenario must be Low, Normal, Moderate, or Heavy.'
    return TRAFFIC_SCENARIOS[scenario.strip().lower()], None


def _get_blocked_roads(payload: Mapping[str, Any], graph) -> tuple[list[Any] | None, bool, str | None]:
    traffic = payload.get('traffic')
    traffic_closures = traffic.get('roadClosures') if isinstance(traffic, Mapping) else None
    explicit = payload.get('roadClosures', payload.get('blockedRoads', traffic_closures))
    if explicit is None:
        explicit = []
    if not isinstance(explicit, list):
        return None, False, 'roadClosures must be a JSON array.'

    for index, segment in enumerate(explicit):
        if isinstance(segment, Mapping):
            start = segment.get('from')
            end = segment.get('to')
            key = segment.get('key')
        elif isinstance(segment, list) and len(segment) in {2, 3}:
            start, end = segment[0], segment[1]
            key = segment[2] if len(segment) == 3 else None
        else:
            return None, False, f'roadClosures[{index}] must be a {{from, to, key?}} object or a two/three-item array.'
        try:
            exists = graph.has_edge(start, end) or graph.has_edge(end, start)
            if key is not None and graph.is_multigraph():
                exists = graph.has_edge(start, end, key) or graph.has_edge(end, start, key)
        except TypeError:
            exists = False
        if not exists:
            return None, False, f'roadClosures[{index}] references an unknown road segment.'

    closure_toggle = payload.get('roadClosure', False)
    if not isinstance(closure_toggle, bool):
        return None, False, 'roadClosure must be a boolean.'

    return explicit, closure_toggle, None


def _candidate_response(dispatch_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected = dispatch_result.get('assignedVehicle')
    selected_data = dispatch_result.get('selectedCandidate') or {}
    selected_route = dispatch_result.get('route') or {}
    candidates = []
    if isinstance(selected, Mapping):
        candidates.append({
            'vehicleId': selected_data.get('vehicleId', selected.get('id')),
            'vehicle': selected,
            'selected': True,
            'etaMinutes': selected_route.get('etaMinutes'),
            'distanceKm': selected_route.get('distanceKm'),
            'suitability': selected_data.get('suitability'),
            'score': selected_data.get('score'),
            'route': selected_route,
        })

    for candidate in dispatch_result.get('alternatives', []):
        if isinstance(candidate, Mapping):
            candidates.append({**candidate, 'selected': False})
    return candidates


def create_app(road_graph=None) -> Flask:
    app = Flask(__name__)
    graph_state = {'graph': road_graph}

    def get_road_graph():
        if graph_state['graph'] is None:
            graph_state['graph'] = load_drive_graph()
        return graph_state['graph']

    @app.get('/health')
    def health():
        try:
            graph = get_road_graph()
        except RoadNetworkUnavailableError as error:
            return jsonify({
                'status': 'unavailable',
                'service': 'EmergencyAI Python API',
                'roadNetwork': 'OpenStreetMap drive graph',
                'error': str(error),
            }), 503
        return jsonify({
            'status': 'ok',
            'service': 'EmergencyAI Python API',
            'dataMode': 'OpenStreetMap drive graph with simulated traffic/closures',
            'roadNetwork': graph.graph.get('road_network_source', 'injected test graph'),
            'nodeCount': graph.number_of_nodes(),
            'edgeCount': graph.number_of_edges(),
        })

    @app.post('/optimize')
    def optimize():
        payload = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            return jsonify({'error': 'Request body must be a valid JSON object.'}), 400

        incident = payload.get('incident', payload)
        vehicles = payload.get('vehicles')
        validation_error = _validate_incident(incident) or _validate_vehicles(vehicles)
        if validation_error:
            return jsonify({'error': validation_error}), 400

        traffic_scenario, traffic_error = _get_traffic_scenario(payload)
        if traffic_error:
            return jsonify({'error': traffic_error}), 400

        try:
            graph = get_road_graph()
        except RoadNetworkUnavailableError as error:
            return jsonify({'error': str(error)}), 503

        blocked_roads, simulate_closure, closure_error = _get_blocked_roads(payload, graph)
        if closure_error:
            return jsonify({'error': closure_error}), 400

        normalized_incident = {
            **incident,
            'type': incident['type'].strip().lower(),
            'severity': incident['severity'].strip().lower(),
        }
        try:
            dispatch_result = assign_emergency_vehicle(normalized_incident, vehicles, graph)
            adaptive_result = None
            selected_route = dispatch_result.get('route')
            used_default_closure = False
            if simulate_closure and not blocked_roads and selected_route:
                route_edges = selected_route.get('edgeSegments', [])
                if route_edges:
                    selected_segment = {
                        name: value
                        for name, value in route_edges[len(route_edges) // 2].items()
                        if name != 'key' or value is not None
                    }
                    blocked_roads = [selected_segment]
                    used_default_closure = True

            traffic = payload.get('traffic')
            congested_roads = payload.get('congestedRoads')
            if congested_roads is None and isinstance(traffic, Mapping):
                congested_roads = traffic.get('congestedRoads', [])
            if congested_roads is None:
                congested_roads = []

            if selected_route and (traffic_scenario != 'Low' or blocked_roads):
                nodes = selected_route.get('nodes', [])
                if not nodes:
                    raise ValueError('The selected dispatch route does not contain valid endpoints.')
                adaptive_result = calculate_adaptive_route(graph, {
                    'startNode': nodes[0],
                    'destinationNode': nodes[-1],
                    'trafficScenario': traffic_scenario,
                    'blockedRoads': blocked_roads,
                    'congestedRoads': congested_roads,
                })
                if adaptive_result['status'] == 'route_updated':
                    selected_route = adaptive_result['newRoute']
                elif adaptive_result['status'] == 'destination_unreachable':
                    selected_route = None

            explanation = generate_explanation(
                dispatch_result,
                adaptive_result,
                traffic_scenario=traffic_scenario,
            )
        except ValueError as error:
            return jsonify({'error': str(error)}), 400

        selected_vehicle = dispatch_result.get('assignedVehicle')
        if selected_vehicle is None:
            status = 'no_suitable_vehicle'
        elif selected_route is None:
            status = 'route_unavailable'
        else:
            status = 'assigned'

        if adaptive_result is None:
            route_change_information = {
                'status': 'not_required',
                'alternateRouteSelected': False,
                'blockedRoads': [],
                'originalEtaMinutes': selected_route.get('etaMinutes') if selected_route else None,
                'newEtaMinutes': selected_route.get('etaMinutes') if selected_route else None,
                'originalDistanceKm': selected_route.get('distanceKm') if selected_route else None,
                'newDistanceKm': selected_route.get('distanceKm') if selected_route else None,
                'etaDifferenceMinutes': 0 if selected_route else None,
            }
        else:
            original_route = adaptive_result.get('originalRoute') or {}
            updated_route = adaptive_result.get('newRoute') or {}
            original_nodes = original_route.get('nodes', [])
            updated_nodes = updated_route.get('nodes', [])
            original_edges = original_route.get('edgeSegments', [])
            updated_edges = updated_route.get('edgeSegments', [])
            route_change_information = {
                **adaptive_result,
                'alternateRouteSelected': (
                    adaptive_result.get('status') == 'route_updated'
                    and (original_nodes != updated_nodes or original_edges != updated_edges)
                ),
            }

        reasons = explanation['reasons']
        response_body = {
            'status': status,
            'recommended_vehicle': selected_vehicle,
            'eta_minutes': selected_route.get('etaMinutes') if selected_route else None,
            'distance_km': selected_route.get('distanceKm') if selected_route else None,
            'route': selected_route,
            'candidates': _candidate_response(dispatch_result),
            'explanation': explanation,
            'traffic': {
                'scenario': traffic_scenario,
                'source': 'simulated',
                'road_closures': blocked_roads,
                'used_default_simulated_closure': used_default_closure,
                'used_route_based_simulated_closure': used_default_closure,
            },
            'route_change_information': route_change_information,
            'incident': dispatch_result.get('incident'),
            'reasons': reasons,
            'assignedVehicle': selected_vehicle,
            'recommendation': {
                'vehicle': selected_vehicle,
                'assignedVehicle': selected_vehicle,
                'route': selected_route,
                'reasons': reasons,
            },
        }
        return jsonify(response_body), 200

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return jsonify({'error': error.description}), error.code or 500

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        logger.exception('Unhandled error in EmergencyAI API', exc_info=error)
        return jsonify({'error': 'An unexpected server error occurred.'}), 500

    return app


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5001'))
    create_app().run(host='127.0.0.1', port=port)