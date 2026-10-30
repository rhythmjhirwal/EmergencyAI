"""Explainable emergency vehicle filtering, routing, and assignment."""

from collections.abc import Mapping, Sequence
import math
from typing import Any

import networkx as nx

from algorithms.a_star import find_optimal_route
from data.road_network import load_drive_graph, snap_to_nearest_road_node

EMERGENCY_TYPES = {'accident', 'medical', 'fire'}
SEVERITY_LEVELS = {'low', 'medium', 'high', 'critical'}

RESPONSE_TARGET_MINUTES = {
    'critical': 5.0,
    'high': 10.0,
    'medium': 20.0,
    'low': 30.0,
}
LATE_RESPONSE_WEIGHTS = {
    'critical': 3.0,
    'high': 2.0,
    'medium': 1.0,
    'low': 0.5,
}
COMPATIBLE_VEHICLE_PENALTY = 2.0

VEHICLE_PROFILES = {
    'ambulance': {'supports': {'medical', 'accident'}, 'primary': {'medical'}},
    'medical_unit': {'supports': {'medical', 'accident'}, 'primary': {'medical'}},
    'paramedic_unit': {'supports': {'medical', 'accident'}, 'primary': {'medical'}},
    'fire_engine': {'supports': {'fire', 'accident'}, 'primary': {'fire'}},
    'fire_truck': {'supports': {'fire', 'accident'}, 'primary': {'fire'}},
    'rescue_vehicle': {'supports': {'accident', 'medical', 'fire'}, 'primary': {'accident'}},
    'rescue_unit': {'supports': {'accident', 'medical', 'fire'}, 'primary': {'accident'}},
}


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number.')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{label} must be a finite number.')
    return number


def _location(location: Any, label: str) -> tuple[float, float]:
    if not isinstance(location, Mapping):
        raise ValueError(f'{label} must be an object with latitude and longitude.')

    latitude = location.get('lat', location.get('latitude'))
    longitude = location.get('lng', location.get('longitude'))
    lat = _number(latitude, f'{label} latitude')
    lng = _number(longitude, f'{label} longitude')
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError(f'{label} coordinates are outside valid latitude/longitude bounds.')
    return lat, lng


def _nearest_intersection(
    graph: nx.Graph,
    location: tuple[float, float],
    label: str,
    max_snap_distance_km: float,
) -> tuple[Any, float]:
    closest_node, closest_distance = snap_to_nearest_road_node(graph, *location)
    if closest_distance > max_snap_distance_km:
        raise ValueError(
            f'{label} is {closest_distance:.1f} km from the road network, '
            f'beyond the {max_snap_distance_km:.1f} km snapping limit.'
        )
    return closest_node, closest_distance


def _normalized_type(vehicle_type: Any) -> str:
    return str(vehicle_type).strip().lower().replace('-', '_').replace(' ', '_')


def _suitability(vehicle: Mapping[str, Any], emergency_type: str) -> str | None:
    vehicle_type = _normalized_type(vehicle.get('type', ''))
    profile = VEHICLE_PROFILES.get(vehicle_type, {'supports': set(), 'primary': set()})
    supported_types = vehicle.get('supported_emergency_types')

    if supported_types is None:
        supported = profile['supports']
    elif isinstance(supported_types, Sequence) and not isinstance(supported_types, (str, bytes)):
        supported = {str(item).strip().lower() for item in supported_types}
    else:
        return None

    if emergency_type not in supported:
        return None

    primary_types = vehicle.get('primary_emergency_types')
    if primary_types is not None:
        if not isinstance(primary_types, Sequence) or isinstance(primary_types, (str, bytes)):
            primary = set()
        else:
            primary = {str(item).strip().lower() for item in primary_types}
    else:
        primary = profile['primary']
    return 'specialist' if emergency_type in primary else 'compatible'


def _is_available(vehicle: Mapping[str, Any]) -> bool:
    if 'status' in vehicle:
        return str(vehicle['status']).strip().lower() == 'available'
    return vehicle.get('available') is True


def _vehicle_id(vehicle: Mapping[str, Any], fallback_index: int) -> Any:
    return vehicle.get('id', f'vehicle-{fallback_index + 1}')


def _rank_score(eta_minutes: float, severity: str, suitability: str) -> tuple[float, float]:
    target = RESPONSE_TARGET_MINUTES[severity]
    lateness = max(0.0, eta_minutes - target)
    urgency_penalty = lateness * LATE_RESPONSE_WEIGHTS[severity]
    suitability_penalty = 0.0 if suitability == 'specialist' else COMPATIBLE_VEHICLE_PENALTY
    return eta_minutes + urgency_penalty + suitability_penalty, urgency_penalty


def assign_emergency_vehicle(
    incident: Mapping[str, Any],
    vehicles: Sequence[Mapping[str, Any]],
    road_graph: nx.Graph | None = None,
    *,
    max_snap_distance_km: float = 10.0,
) -> dict[str, Any]:
    """Rank available, suitable vehicles using A* travel time and explainable penalties.

    Locations may use {lat, lng} or {latitude, longitude}. They are snapped to
    the nearest road-graph intersections before A* routes are calculated. If no
    graph is supplied, the deterministic simulated graph is used.
    """
    if not isinstance(incident, Mapping):
        raise ValueError('incident must be an object.')
    emergency_type = str(incident.get('type', '')).strip().lower()
    severity = str(incident.get('severity', '')).strip().lower()
    if emergency_type not in EMERGENCY_TYPES:
        raise ValueError('incident type must be accident, medical, or fire.')
    if severity not in SEVERITY_LEVELS:
        raise ValueError('incident severity must be low, medium, high, or critical.')
    if not isinstance(vehicles, Sequence) or isinstance(vehicles, (str, bytes)):
        raise ValueError('vehicles must be a list of vehicle objects.')

    snap_limit = _number(max_snap_distance_km, 'max_snap_distance_km')
    if snap_limit <= 0:
        raise ValueError('max_snap_distance_km must be greater than zero.')

    graph = road_graph if road_graph is not None else load_drive_graph()
    if not isinstance(graph, nx.Graph):
        raise ValueError('road_graph must be a NetworkX graph.')

    incident_location = _location(incident.get('location'), 'incident location')
    destination_node, incident_snap_distance = _nearest_intersection(
        graph,
        incident_location,
        'Incident location',
        snap_limit,
    )

    rejected: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for index, vehicle in enumerate(vehicles):
        vehicle_id = _vehicle_id(vehicle, index) if isinstance(vehicle, Mapping) else f'vehicle-{index + 1}'
        if not isinstance(vehicle, Mapping):
            rejected.append({'vehicleId': vehicle_id, 'reason': 'Vehicle record must be an object.'})
            continue
        if not _is_available(vehicle):
            rejected.append({'vehicleId': vehicle_id, 'reason': 'Vehicle is unavailable.'})
            continue
        if not isinstance(vehicle.get('type'), str) or not vehicle['type'].strip():
            rejected.append({'vehicleId': vehicle_id, 'reason': 'Vehicle type is missing.'})
            continue

        suitability = _suitability(vehicle, emergency_type)
        if suitability is None:
            rejected.append({
                'vehicleId': vehicle_id,
                'reason': f'Vehicle type {vehicle["type"]!r} is not suitable for {emergency_type}.',
            })
            continue

        try:
            vehicle_location = _location(vehicle.get('location'), f'Vehicle {vehicle_id!r} location')
            start_node, vehicle_snap_distance = _nearest_intersection(
                graph,
                vehicle_location,
                f'Vehicle {vehicle_id!r} location',
                snap_limit,
            )
            route = find_optimal_route(graph, start_node, destination_node)
        except nx.NetworkXNoPath:
            rejected.append({'vehicleId': vehicle_id, 'reason': 'No open road route reaches the incident.'})
            continue
        except ValueError as error:
            rejected.append({'vehicleId': vehicle_id, 'reason': str(error)})
            continue

        eta = float(route['etaMinutes'])
        score, urgency_penalty = _rank_score(eta, severity, suitability)
        candidates.append({
            'vehicle': dict(vehicle),
            'vehicleId': vehicle_id,
            'route': route,
            'etaMinutes': eta,
            'score': score,
            'suitability': suitability,
            'urgencyPenaltyMinutes': urgency_penalty,
            'vehicleSnapDistanceKm': vehicle_snap_distance,
        })

    candidates.sort(key=lambda candidate: (candidate['score'], candidate['etaMinutes'], str(candidate['vehicleId'])))
    response_target = RESPONSE_TARGET_MINUTES[severity]
    scoring = {
        'method': 'A* ETA + severity-weighted lateness + vehicle suitability penalty',
        'severity': severity,
        'responseTargetMinutes': response_target,
        'lateResponseWeight': LATE_RESPONSE_WEIGHTS[severity],
        'compatibleVehiclePenaltyMinutes': COMPATIBLE_VEHICLE_PENALTY,
        'specialistVehiclePenaltyMinutes': 0.0,
    }

    normalized_incident = {
        'type': emergency_type,
        'severity': severity,
        'location': {'lat': incident_location[0], 'lng': incident_location[1]},
    }
    if 'id' in incident:
        normalized_incident['id'] = incident['id']

    if not candidates:
        return {
            'status': 'no_suitable_vehicle',
            'incident': normalized_incident,
            'assignedVehicle': None,
            'selectedCandidate': None,
            'route': None,
            'alternatives': [],
            'reasons': ['No available, suitable vehicle has a valid route to this incident.'],
            'rejectedVehicles': rejected,
            'scoring': scoring,
            'incidentSnapDistanceKm': incident_snap_distance,
        }

    selected = candidates[0]
    selected_reasons = [
        f'{selected["vehicle"].get("type")} is available and classified as {selected["suitability"]} for {emergency_type}.',
        f'A* route ETA is {selected["etaMinutes"]:.1f} minutes over {selected["route"]["distanceKm"]:.1f} km, including traffic costs.',
        f'{severity.title()} severity uses a {response_target:.0f}-minute response target; severity-weighted lateness penalty is {selected["urgencyPenaltyMinutes"]:.1f}.',
        f'Total ranking score is {selected["score"]:.1f}; lower scores rank higher.',
    ]

    alternatives = [
        {
            'vehicleId': candidate['vehicleId'],
            'vehicle': candidate['vehicle'],
            'etaMinutes': candidate['etaMinutes'],
            'distanceKm': candidate['route']['distanceKm'],
            'score': candidate['score'],
            'suitability': candidate['suitability'],
            'route': candidate['route'],
        }
        for candidate in candidates[1:]
    ]

    return {
        'status': 'assigned',
        'incident': normalized_incident,
        'assignedVehicle': selected['vehicle'],
        'selectedCandidate': {
            'vehicleId': selected['vehicleId'],
            'suitability': selected['suitability'],
            'score': selected['score'],
            'urgencyPenaltyMinutes': selected['urgencyPenaltyMinutes'],
        },
        'route': selected['route'],
        'alternatives': alternatives,
        'reasons': selected_reasons,
        'rejectedVehicles': rejected,
        'scoring': scoring,
        'incidentSnapDistanceKm': incident_snap_distance,
    }