"""Build structured, value-grounded explanations for dispatch decisions."""

from collections.abc import Mapping, Sequence
from typing import Any


def _vehicle_label(vehicle: Mapping[str, Any], fallback_id: Any = None) -> str:
    label = vehicle.get('label') or vehicle.get('registration') or vehicle.get('name')
    identifier = vehicle.get('id', fallback_id)
    return str(label or identifier or 'Vehicle')


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _route_value(candidate: Mapping[str, Any], field: str, route_field: str) -> float | None:
    value = candidate.get(field)
    route = candidate.get('route')
    if value is None and isinstance(route, Mapping):
        value = route.get(route_field)
    return _numeric(value)


def _format_minutes(value: float | None) -> str:
    return f'{value:.1f} minutes' if value is not None else 'not provided'


def _format_distance(value: float | None) -> str:
    return f'{value:.1f} km' if value is not None else 'not provided'


def _candidate_detail(
    candidate: Mapping[str, Any],
    selected_eta: float | None,
    selected_distance: float | None,
    *,
    selected: bool,
) -> dict[str, Any]:
    vehicle_value = candidate.get('vehicle')
    vehicle = vehicle_value if isinstance(vehicle_value, Mapping) else candidate
    vehicle_id = candidate.get('vehicleId', vehicle.get('id'))
    eta = _route_value(candidate, 'etaMinutes', 'etaMinutes')
    distance = _route_value(candidate, 'distanceKm', 'distanceKm')
    score = _numeric(candidate.get('score'))
    suitability = candidate.get('suitability')
    availability = vehicle.get('status')
    if availability is None and isinstance(vehicle.get('available'), bool):
        availability = 'available' if vehicle['available'] else 'unavailable'

    return {
        'vehicleId': vehicle_id,
        'vehicle': _vehicle_label(vehicle, vehicle_id),
        'type': vehicle.get('type'),
        'selected': selected,
        'availability': availability,
        'suitability': suitability,
        'etaMinutes': eta,
        'distanceKm': distance,
        'rankingScore': score,
        'etaDifferenceFromSelectedMinutes': None if selected or eta is None or selected_eta is None else eta - selected_eta,
        'distanceDifferenceFromSelectedKm': None if selected or distance is None or selected_distance is None else distance - selected_distance,
    }


def _traffic_details(
    dispatch_result: Mapping[str, Any],
    adaptive_route_result: Mapping[str, Any] | None,
    traffic_scenario: str | None,
) -> dict[str, Any]:
    scenario = traffic_scenario
    if scenario is None and adaptive_route_result is not None:
        scenario = adaptive_route_result.get('trafficScenario')
    if scenario is None:
        scenario = dispatch_result.get('trafficScenario')

    return {
        'scenario': scenario,
        'source': 'simulated' if scenario is not None else 'not provided',
        'description': (
            f'{scenario} traffic scenario was supplied as simulated routing input.'
            if scenario is not None
            else 'Traffic scenario was not included in the decision data.'
        ),
    }


def _route_adaptation(adaptive_result: Mapping[str, Any] | None) -> dict[str, Any]:
    if adaptive_result is None:
        return {
            'evaluated': False,
            'alternateRouteSelected': None,
            'status': 'not_provided',
            'blockedRoads': [],
            'originalEtaMinutes': None,
            'newEtaMinutes': None,
            'originalDistanceKm': None,
            'newDistanceKm': None,
            'etaDifferenceMinutes': None,
            'summary': 'Adaptive route results were not provided.',
        }

    status = adaptive_result.get('status')
    blocked_roads = adaptive_result.get('blockedRoads', [])
    if not isinstance(blocked_roads, list):
        blocked_roads = []
    original_route = adaptive_result.get('originalRoute')
    new_route = adaptive_result.get('newRoute')
    original_nodes = original_route.get('nodes') if isinstance(original_route, Mapping) else None
    new_nodes = new_route.get('nodes') if isinstance(new_route, Mapping) else None
    original_edges = original_route.get('edgeSegments') if isinstance(original_route, Mapping) else None
    new_edges = new_route.get('edgeSegments') if isinstance(new_route, Mapping) else None
    route_changed = (
        isinstance(original_nodes, list)
        and isinstance(new_nodes, list)
        and (
            original_nodes != new_nodes
            or (
                isinstance(original_edges, list)
                and isinstance(new_edges, list)
                and original_edges != new_edges
            )
        )
    )
    has_closure = bool(blocked_roads)

    if status == 'destination_unreachable':
        alternate_selected = False
        summary = 'The reported closures disconnect the destination; no alternate route was available.'
    elif has_closure and route_changed:
        alternate_selected = True
        summary = (
            'A different route was selected after the reported road closure.'
        )
    elif has_closure and isinstance(original_nodes, list) and isinstance(new_nodes, list):
        alternate_selected = False
        summary = 'The route remained unchanged after the reported closure.'
    elif status == 'route_updated':
        alternate_selected = route_changed
        summary = 'A* recalculated the route for the supplied simulated traffic scenario.'
    else:
        alternate_selected = None
        summary = str(adaptive_result.get('message') or 'Adaptive route outcome was not specified.')

    return {
        'evaluated': True,
        'alternateRouteSelected': alternate_selected,
        'status': status,
        'blockedRoads': blocked_roads,
        'originalEtaMinutes': _numeric(adaptive_result.get('originalEtaMinutes')),
        'newEtaMinutes': _numeric(adaptive_result.get('newEtaMinutes')),
        'originalDistanceKm': _numeric(adaptive_result.get('originalDistanceKm')),
        'newDistanceKm': _numeric(adaptive_result.get('newDistanceKm')),
        'etaDifferenceMinutes': _numeric(adaptive_result.get('etaDifferenceMinutes')),
        'summary': summary,
    }


def generate_explanation(
    dispatch_result: Mapping[str, Any],
    adaptive_route_result: Mapping[str, Any] | None = None,
    *,
    traffic_scenario: str | None = None,
) -> dict[str, Any]:
    """Explain a dispatch from its calculated result and optional route update.

    The returned dictionary is JSON-serializable. Missing decision fields are
    reported as unavailable; this function does not recalculate rankings or
    infer reasons that are absent from the source result.
    """
    if not isinstance(dispatch_result, Mapping):
        raise ValueError('dispatch_result must be an object.')
    if adaptive_route_result is not None and not isinstance(adaptive_route_result, Mapping):
        raise ValueError('adaptive_route_result must be an object when provided.')

    selected_value = dispatch_result.get('assignedVehicle')
    selected = selected_value if isinstance(selected_value, Mapping) else None
    selected_metadata_value = dispatch_result.get('selectedCandidate')
    selected_metadata = selected_metadata_value if isinstance(selected_metadata_value, Mapping) else {}
    candidate_values = dispatch_result.get('alternatives', [])
    candidates = candidate_values if isinstance(candidate_values, Sequence) and not isinstance(candidate_values, (str, bytes)) else []
    rejected_values = dispatch_result.get('rejectedVehicles', [])
    rejected = rejected_values if isinstance(rejected_values, Sequence) and not isinstance(rejected_values, (str, bytes)) else []
    original_reasons = dispatch_result.get('reasons', [])
    if isinstance(original_reasons, str):
        original_reasons = [original_reasons]
    if not isinstance(original_reasons, Sequence):
        original_reasons = []
    reasons = [str(reason) for reason in original_reasons]

    traffic = _traffic_details(dispatch_result, adaptive_route_result, traffic_scenario)
    route_adaptation = _route_adaptation(adaptive_route_result)
    if selected is None:
        rejected_summaries = [
            f'{_vehicle_label(item, item.get("vehicleId"))}: {item.get("reason", "not selected")}'
            for item in rejected
            if isinstance(item, Mapping)
        ]
        summary = 'No vehicle was assigned.'
        if rejected_summaries:
            summary += f' {len(rejected_summaries)} vehicle(s) were rejected or unavailable.'
        if reasons:
            summary += f' {reasons[0]}'
        if not reasons:
            reasons = rejected_summaries or ['The dispatch result contains no assignment or rejection details.']
        return {
            'summary': summary,
            'reasons': reasons,
            'decisionFactors': {
                'selectedVehicle': None,
                'candidates': [],
                'rejectedVehicles': [dict(item) for item in rejected if isinstance(item, Mapping)],
                'trafficConditions': traffic,
                'routeAdaptation': route_adaptation,
                'mainFactors': ['No assigned vehicle was present in the dispatch result.'],
            },
        }

    selected_id = selected_metadata.get('vehicleId', selected.get('id'))
    selected_route = dispatch_result.get('route')
    selected_eta = _numeric(selected_route.get('etaMinutes')) if isinstance(selected_route, Mapping) else None
    selected_distance = _numeric(selected_route.get('distanceKm')) if isinstance(selected_route, Mapping) else None
    selected_candidate = {
        'vehicle': selected,
        'vehicleId': selected_id,
        'suitability': selected_metadata.get('suitability'),
        'score': selected_metadata.get('score'),
        'etaMinutes': selected_eta,
        'distanceKm': selected_distance,
    }
    candidate_details = [_candidate_detail(
        selected_candidate,
        selected_eta,
        selected_distance,
        selected=True,
    )]

    for candidate in candidates:
        if isinstance(candidate, Mapping):
            candidate_details.append(_candidate_detail(
                candidate,
                selected_eta,
                selected_distance,
                selected=False,
            ))

    alternative_details = candidate_details[1:]
    selected_label = _vehicle_label(selected, selected_id)
    selected_suitability = selected_metadata.get('suitability')
    if selected_suitability:
        selection_reason = f'{selected_label} was selected as a {selected_suitability} vehicle for the incident.'
    else:
        selection_reason = f'{selected_label} was selected by the dispatch engine.'

    eta_values = [detail['etaMinutes'] for detail in candidate_details]
    has_all_etas = bool(eta_values) and all(value is not None for value in eta_values)
    lowest_eta = has_all_etas and selected_eta == min(eta_values)
    if lowest_eta:
        selection_reason += f' It had the lowest estimated travel time at {_format_minutes(selected_eta)}.'
    elif selected_eta is not None and alternative_details:
        selection_reason += f' Its estimated travel time was {_format_minutes(selected_eta)}.'

    if selected_distance is not None:
        selection_reason += f' The planned route distance was {_format_distance(selected_distance)}.'
    selected_score = _numeric(selected_metadata.get('score'))
    if selected_score is not None:
        selection_reason += f' Its calculated ranking score was {selected_score:.1f}.'

    alternative_reasons = []
    for detail in alternative_details:
        label = detail['vehicle']
        reason = f'{label} was eligible but ranked below {selected_label} by the dispatch engine.'
        if detail['etaMinutes'] is not None and selected_eta is not None:
            reason += f' ETA: {_format_minutes(detail["etaMinutes"])} versus {_format_minutes(selected_eta)}.'
        if detail['distanceKm'] is not None and selected_distance is not None:
            reason += f' Route distance: {_format_distance(detail["distanceKm"])} versus {_format_distance(selected_distance)}.'
        if detail['suitability'] is not None:
            reason += f' Suitability: {detail["suitability"]}.'
        if detail['rankingScore'] is not None:
            reason += f' Ranking score: {detail["rankingScore"]:.1f}.'
        alternative_reasons.append(reason)

    rejected_details = [dict(item) for item in rejected if isinstance(item, Mapping)]
    rejected_reasons = [
        f'{_vehicle_label(item, item.get("vehicleId"))} was not considered: {item.get("reason", "reason not provided")}.'
        for item in rejected_details
    ]
    summary = selection_reason
    if alternative_reasons:
        summary += f' {len(alternative_reasons)} other eligible vehicle(s) were ranked as alternatives.'
    if route_adaptation['evaluated']:
        summary += f' {route_adaptation["summary"]}'
        updated_eta = route_adaptation['newEtaMinutes']
        original_eta = route_adaptation['originalEtaMinutes']
        if updated_eta is not None:
            summary += f' Updated route ETA is {_format_minutes(updated_eta)}'
            if original_eta is not None:
                summary += f' (was {_format_minutes(original_eta)} before adaptation).'
            else:
                summary += '.'

    main_factors = []
    if selected.get('status') is not None:
        main_factors.append(f'Selected vehicle availability: {selected["status"]}.')
    if selected_suitability is not None:
        main_factors.append(f'Selected vehicle suitability: {selected_suitability}.')
    if selected_eta is not None:
        main_factors.append(f'Selected route ETA: {_format_minutes(selected_eta)}.')
    if selected_distance is not None:
        main_factors.append(f'Selected route distance: {_format_distance(selected_distance)}.')
    scoring = dispatch_result.get('scoring')
    if isinstance(scoring, Mapping):
        main_factors.append(f'Ranking method: {scoring.get("method", "provided by dispatch engine")}.')
        if scoring.get('severity') is not None:
            main_factors.append(f'Incident severity: {scoring["severity"]}.')
    if traffic['scenario'] is not None:
        main_factors.append(f'Traffic input: simulated {traffic["scenario"]} scenario.')
    if route_adaptation['evaluated']:
        main_factors.append(route_adaptation['summary'])

    detailed_reasons = reasons + alternative_reasons + rejected_reasons
    if not detailed_reasons:
        detailed_reasons = [selection_reason]

    return {
        'summary': summary,
        'reasons': detailed_reasons,
        'decisionFactors': {
            'selectedVehicle': candidate_details[0],
            'candidates': candidate_details,
            'rejectedVehicles': rejected_details,
            'trafficConditions': traffic,
            'routeAdaptation': route_adaptation,
            'mainFactors': main_factors,
        },
    }