import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from data.road_network import RoadNetworkUnavailableError
from tests.road_fixtures import create_test_road_graph


class FlaskApiTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(create_test_road_graph()).test_client()
        self.payload = {
            'type': 'accident',
            'severity': 'high',
            'location': {'lat': 28.6229, 'lng': 77.2200},
            'trafficScenario': 'normal',
            'roadClosure': False,
            'vehicles': [{
                'id': 'RESCUE-01',
                'label': 'Rescue 01',
                'type': 'rescue_vehicle',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            }],
        }

    def test_health_returns_service_status(self):
        response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'ok')

    def test_missing_osm_graph_returns_clear_service_unavailable(self):
        with patch('app.load_drive_graph', side_effect=RoadNetworkUnavailableError('OSM download unavailable.')):
            client = create_app().test_client()
            health = client.get('/health')
            optimize = client.post('/optimize', json=self.payload)

        self.assertEqual(health.status_code, 503)
        self.assertIn('OSM download unavailable', health.get_json()['error'])
        self.assertEqual(optimize.status_code, 503)
        self.assertIn('OSM download unavailable', optimize.get_json()['error'])

    def test_optimize_returns_agreed_fields_and_explanation(self):
        response = self.client.post('/optimize', json=self.payload)
        result = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result['status'], 'assigned')
        self.assertEqual(result['recommended_vehicle']['id'], 'RESCUE-01')
        self.assertEqual(result['assignedVehicle']['id'], 'RESCUE-01')
        self.assertIsInstance(result['route']['coordinates'], list)
        self.assertEqual(result['eta_minutes'], result['route']['etaMinutes'])
        self.assertIn('decisionFactors', result['explanation'])

    def test_closure_toggle_applies_simulated_segment_and_updates_route(self):
        payload = {**self.payload, 'roadClosure': True, 'trafficScenario': 'moderate'}

        result = self.client.post('/optimize', json=payload).get_json()

        self.assertEqual(result['traffic']['scenario'], 'Moderate')
        self.assertTrue(result['traffic']['used_default_simulated_closure'])
        self.assertEqual(result['traffic']['road_closures'], [{'from': 'B', 'to': 'D', 'key': 0}])
        self.assertTrue(result['route_change_information']['alternateRouteSelected'])

    def test_heavy_traffic_updates_route_costs_without_closure(self):
        normal_result = self.client.post('/optimize', json=self.payload).get_json()
        heavy_payload = {**self.payload, 'trafficScenario': 'heavy'}

        heavy_result = self.client.post('/optimize', json=heavy_payload).get_json()

        self.assertEqual(heavy_result['traffic']['scenario'], 'Heavy')
        self.assertEqual(heavy_result['traffic']['road_closures'], [])
        self.assertEqual(heavy_result['route_change_information']['status'], 'route_updated')
        self.assertGreater(heavy_result['eta_minutes'], normal_result['eta_minutes'])
        self.assertGreater(heavy_result['route_change_information']['trafficAdjustments']['updatedRoadCount'], 0)

    def test_vehicle_at_incident_intersection_returns_zero_distance_route(self):
        payload = {
            'type': 'accident',
            'severity': 'medium',
            'location': {'lat': 28.6050, 'lng': 77.2200},
            'trafficScenario': 'moderate',
            'roadClosure': False,
            'vehicles': [{
                'id': 'FIRE-01',
                'type': 'fire_engine',
                'status': 'available',
                'location': {'lat': 28.6050, 'lng': 77.2200},
            }],
        }

        response = self.client.post('/optimize', json=payload)
        result = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result['status'], 'assigned')
        self.assertEqual(result['route']['nodes'], ['E'])
        self.assertEqual(result['route']['coordinates'], [[28.605, 77.22]])
        self.assertEqual(result['eta_minutes'], 0)

    def test_explicit_road_closures_trigger_adaptive_routing(self):
        payload = {
            'incident': {
                'type': 'accident',
                'severity': 'critical',
                'location': self.payload['location'],
            },
            'vehicles': self.payload['vehicles'],
            'traffic': {'scenario': 'Heavy', 'roadClosures': [['B', 'D']]},
        }

        result = self.client.post('/optimize', json=payload).get_json()

        self.assertEqual(result['traffic']['scenario'], 'Heavy')
        self.assertEqual(result['route_change_information']['status'], 'route_updated')
        self.assertEqual(result['route'], result['route_change_information']['newRoute'])

    def test_disconnect_closures_return_route_unavailable_without_route(self):
        payload = {
            'incident': {
                'type': 'accident',
                'severity': 'critical',
                'location': {'lat': 28.6229, 'lng': 77.2200},
            },
            'vehicles': [{
                'id': 'RESCUE-01',
                'type': 'rescue_vehicle',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            }],
            'traffic': {
                'scenario': 'Heavy',
                'roadClosures': [['B', 'D'], ['C', 'D'], ['E', 'D'], ['D', 'F']],
            },
        }

        response = self.client.post('/optimize', json=payload)
        result = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result['status'], 'route_unavailable')
        self.assertIsNone(result['route'])
        self.assertIsNone(result['eta_minutes'])
        self.assertEqual(result['route_change_information']['status'], 'destination_unreachable')

    def test_invalid_json_and_invalid_incident_return_400(self):
        invalid_json = self.client.post('/optimize', data='{', content_type='application/json')
        invalid_incident = self.client.post('/optimize', json={**self.payload, 'severity': 'extreme'})

        self.assertEqual(invalid_json.status_code, 400)
        self.assertIn('error', invalid_json.get_json())
        self.assertEqual(invalid_incident.status_code, 400)

    def test_invalid_vehicle_data_returns_400(self):
        payload = {
            **self.payload,
            'vehicles': [{'id': 'AMB-01', 'type': 'ambulance', 'status': 'available'}],
        }

        response = self.client.post('/optimize', json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn('location', response.get_json()['error'])

    def test_invalid_closure_is_rejected_when_no_vehicle_is_eligible(self):
        payload = {
            'incident': {
                'type': 'fire',
                'severity': 'high',
                'location': self.payload['location'],
            },
            'vehicles': [],
            'traffic': {'scenario': 'Low', 'roadClosures': [['unknown', 'segment']]},
        }

        response = self.client.post('/optimize', json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn('unknown road segment', response.get_json()['error'])

    def test_no_suitable_vehicle_is_a_valid_dispatch_outcome(self):
        payload = {
            **self.payload,
            'type': 'fire',
            'vehicles': [{
                'id': 'AMB-01',
                'label': 'Ambulance 01',
                'type': 'ambulance',
                'status': 'available',
                'location': {'lat': 28.6139, 'lng': 77.2090},
            }],
        }

        response = self.client.post('/optimize', json=payload)
        result = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result['status'], 'no_suitable_vehicle')
        self.assertIsNone(result['recommended_vehicle'])
        self.assertIsNone(result['route'])


if __name__ == '__main__':
    unittest.main()