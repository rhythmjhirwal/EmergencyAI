import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dispatch import assign_emergency_vehicle
from tests.road_fixtures import create_test_road_graph


class DispatchTests(unittest.TestCase):
    def test_selects_by_a_star_time_not_nearest_vehicle(self):
        graph = create_test_road_graph()
        graph['B']['D'][0]['estimated_travel_time_min'] = 12.0
        graph['B']['D'][0]['route_cost_min'] = 12.1
        incident = {
            'type': 'accident',
            'severity': 'high',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }
        vehicles = [
            {
                'id': 'near-but-slow',
                'type': 'rescue_vehicle',
                'status': 'available',
                'location': {'lat': 28.6229, 'lng': 77.2090},
            },
            {
                'id': 'farther-but-faster',
                'type': 'rescue_vehicle',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
        ]

        result = assign_emergency_vehicle(incident, vehicles, graph)

        self.assertEqual(result['assignedVehicle']['id'], 'farther-but-faster')
        self.assertEqual(result['alternatives'][0]['vehicleId'], 'near-but-slow')
        self.assertLess(result['route']['etaMinutes'], result['alternatives'][0]['etaMinutes'])

    def test_filters_unavailable_and_unsuitable_vehicles(self):
        incident = {
            'type': 'fire',
            'severity': 'critical',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }
        vehicles = [
            {
                'id': 'busy-fire-unit',
                'type': 'fire_engine',
                'status': 'busy',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
            {
                'id': 'available-ambulance',
                'type': 'ambulance',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
            {
                'id': 'available-fire-unit',
                'type': 'fire_engine',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
        ]

        result = assign_emergency_vehicle(incident, vehicles, create_test_road_graph())

        self.assertEqual(result['assignedVehicle']['id'], 'available-fire-unit')
        self.assertEqual(len(result['alternatives']), 0)
        self.assertEqual(len(result['rejectedVehicles']), 2)

    def test_unavailable_nearest_ambulance_is_not_selected(self):
        incident = {
            'type': 'medical',
            'severity': 'critical',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }
        vehicles = [
            {
                'id': 'nearest-busy',
                'type': 'ambulance',
                'status': 'busy',
                'location': {'lat': 28.6229, 'lng': 77.2200},
            },
            {
                'id': 'farther-available',
                'type': 'ambulance',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
        ]

        result = assign_emergency_vehicle(incident, vehicles, create_test_road_graph())

        self.assertEqual(result['assignedVehicle']['id'], 'farther-available')
        self.assertEqual(result['rejectedVehicles'][0]['vehicleId'], 'nearest-busy')
        self.assertIn('unavailable', result['rejectedVehicles'][0]['reason'])

    def test_multiple_available_ambulances_rank_by_network_eta(self):
        incident = {
            'type': 'medical',
            'severity': 'high',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }
        vehicles = [
            {
                'id': 'far-ambulance',
                'type': 'ambulance',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            },
            {
                'id': 'near-ambulance',
                'type': 'ambulance',
                'status': 'available',
                'location': {'lat': 28.6229, 'lng': 77.2090},
            },
        ]

        result = assign_emergency_vehicle(incident, vehicles, create_test_road_graph())

        self.assertEqual(result['assignedVehicle']['id'], 'near-ambulance')
        self.assertLess(result['route']['etaMinutes'], result['alternatives'][0]['etaMinutes'])
        self.assertEqual(result['alternatives'][0]['vehicleId'], 'far-ambulance')

    def test_returns_explainable_no_suitable_vehicle_result(self):
        incident = {
            'type': 'fire',
            'severity': 'medium',
            'location': {'lat': 28.6229, 'lng': 77.2200},
        }
        vehicles = [{
            'id': 'medical-only',
            'type': 'ambulance',
            'status': 'available',
            'location': {'lat': 28.6139, 'lng': 77.2090},
        }]

        result = assign_emergency_vehicle(incident, vehicles, create_test_road_graph())

        self.assertEqual(result['status'], 'no_suitable_vehicle')
        self.assertIsNone(result['assignedVehicle'])
        self.assertEqual(result['alternatives'], [])
        self.assertIn('not suitable', result['rejectedVehicles'][0]['reason'])
        json.dumps(result)

    def test_rejects_incident_outside_road_network_snap_limit(self):
        incident = {
            'type': 'medical',
            'severity': 'high',
            'location': {'lat': 34.0, 'lng': -118.0},
        }

        with self.assertRaisesRegex(ValueError, 'beyond the'):
            assign_emergency_vehicle(incident, [], create_test_road_graph())


if __name__ == '__main__':
    unittest.main()