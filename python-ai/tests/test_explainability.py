import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.adaptive_routing import calculate_adaptive_route
from services.dispatch import assign_emergency_vehicle
from services.explainability import generate_explanation
from tests.road_fixtures import create_test_road_graph


class ExplainabilityTests(unittest.TestCase):
    def test_explanation_uses_dispatch_values_and_compares_candidates(self):
        incident = {
            'type': 'accident',
            'severity': 'high',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }
        vehicles = [
            {
                'id': 'RESCUE-01',
                'label': 'Rescue 01',
                'type': 'rescue_vehicle',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
            {
                'id': 'RESCUE-02',
                'label': 'Rescue 02',
                'type': 'rescue_vehicle',
                'status': 'available',
                'location': {'lat': 28.6229, 'lng': 77.2300},
            },
        ]
        graph = create_test_road_graph()
        dispatch = assign_emergency_vehicle(incident, vehicles, graph)
        adaptive = calculate_adaptive_route(graph, {
            'startNode': 'A',
            'destinationNode': 'D',
            'trafficScenario': 'Moderate',
            'blockedRoads': [['B', 'D']],
        })

        explanation = generate_explanation(dispatch, adaptive, traffic_scenario='Moderate')

        selected = explanation['decisionFactors']['selectedVehicle']
        self.assertEqual(selected['vehicleId'], dispatch['assignedVehicle']['id'])
        self.assertEqual(selected['etaMinutes'], dispatch['route']['etaMinutes'])
        self.assertEqual(selected['distanceKm'], dispatch['route']['distanceKm'])
        self.assertEqual(len(explanation['decisionFactors']['candidates']), 2)
        self.assertTrue(explanation['decisionFactors']['routeAdaptation']['alternateRouteSelected'])
        self.assertEqual(explanation['decisionFactors']['trafficConditions']['source'], 'simulated')
        self.assertIn('Updated route ETA is', explanation['summary'])
        json.dumps(explanation)

    def test_no_assignment_does_not_invent_selected_vehicle(self):
        dispatch = assign_emergency_vehicle({
            'type': 'fire',
            'severity': 'critical',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }, [{
            'id': 'AMB-01',
            'type': 'ambulance',
            'status': 'available',
            'location': {'lat': 28.6139, 'lng': 77.2090},
        }], create_test_road_graph())

        explanation = generate_explanation(dispatch)

        self.assertIsNone(explanation['decisionFactors']['selectedVehicle'])
        self.assertIn('No vehicle was assigned.', explanation['summary'])
        self.assertIn('not suitable', explanation['decisionFactors']['rejectedVehicles'][0]['reason'])

    def test_alternative_comparisons_use_returned_eta_and_distance(self):
        dispatch = {
            'assignedVehicle': {'id': 'AMB-02', 'label': 'AMB-02', 'type': 'ambulance', 'status': 'available'},
            'selectedCandidate': {'vehicleId': 'AMB-02', 'suitability': 'specialist', 'score': 4.0},
            'route': {'etaMinutes': 4.0, 'distanceKm': 2.0},
            'alternatives': [{
                'vehicleId': 'AMB-01',
                'vehicle': {'id': 'AMB-01', 'type': 'ambulance', 'status': 'available'},
                'suitability': 'specialist',
                'etaMinutes': 6.0,
                'distanceKm': 3.5,
                'score': 6.0,
            }],
            'rejectedVehicles': [],
            'reasons': [],
        }

        explanation = generate_explanation(dispatch)

        alternative = explanation['decisionFactors']['candidates'][1]
        self.assertEqual(alternative['etaDifferenceFromSelectedMinutes'], 2.0)
        self.assertEqual(alternative['distanceDifferenceFromSelectedKm'], 1.5)
        self.assertIn('AMB-01 was eligible but ranked below AMB-02', explanation['reasons'][0])

    def test_parallel_edge_change_counts_as_alternate_route(self):
        explanation = generate_explanation(
            {'assignedVehicle': {'id': 'AMB-01'}, 'route': {'etaMinutes': 4, 'distanceKm': 2}},
            {
                'status': 'route_updated',
                'blockedRoads': [{'from': 1, 'to': 2, 'key': 0}],
                'originalRoute': {'nodes': [1, 2], 'edgeSegments': [{'from': 1, 'to': 2, 'key': 0}]},
                'newRoute': {'nodes': [1, 2], 'edgeSegments': [{'from': 1, 'to': 2, 'key': 1}]},
                'originalEtaMinutes': 4,
                'newEtaMinutes': 5,
                'originalDistanceKm': 2,
                'newDistanceKm': 2.2,
                'etaDifferenceMinutes': 1,
            },
        )

        self.assertTrue(explanation['decisionFactors']['routeAdaptation']['alternateRouteSelected'])


if __name__ == '__main__':
    unittest.main()