import sys
from pathlib import Path
import unittest

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.adaptive_routing import calculate_adaptive_route
from tests.road_fixtures import create_test_road_graph


class AdaptiveRoutingTests(unittest.TestCase):
    def test_moderate_traffic_changes_edge_costs_and_recalculates_route(self):
        graph = create_test_road_graph()
        original_edge_time = graph['A']['B'][0]['estimated_travel_time_min']
        request = {
            'startNode': 'A',
            'destinationNode': 'D',
            'trafficScenario': 'Moderate',
        }

        result = calculate_adaptive_route(graph, request)

        self.assertEqual(result['status'], 'route_updated')
        self.assertGreater(result['newEtaMinutes'], result['originalEtaMinutes'])
        self.assertNotEqual(result['newRoute']['nodes'], result['originalRoute']['nodes'])
        self.assertEqual(result['coordinates'], result['newRoute']['coordinates'])
        self.assertEqual(graph['A']['B'][0]['estimated_travel_time_min'], original_edge_time)
        self.assertGreaterEqual(
            result['trafficAdjustments']['updatedRoadCount'],
            len(result['originalRoute']['edgeSegments']),
        )

    def test_blocked_segment_is_removed_from_updated_connectivity(self):
        graph = create_test_road_graph()
        request = {
            'start_node': 'A',
            'destination_node': 'D',
            'traffic_scenario': 'Low',
            'blocked_roads': [{'from': 'B', 'to': 'D'}],
        }

        result = calculate_adaptive_route(graph, request)

        self.assertEqual(result['status'], 'route_updated')
        self.assertNotEqual(result['newRoute']['nodes'], result['originalRoute']['nodes'])
        self.assertEqual(result['blockedRoads'], [{'from': 'B', 'to': 'D'}])
        self.assertTrue(graph.has_edge('B', 'D'))

    def test_closures_that_disconnect_destination_return_no_route(self):
        graph = create_test_road_graph()
        request = {
            'startNode': 'A',
            'destinationNode': 'D',
            'trafficScenario': 'Heavy',
            'blockedRoads': [['B', 'D'], ['C', 'D'], ['E', 'D'], ['D', 'F']],
        }

        result = calculate_adaptive_route(graph, request)

        self.assertEqual(result['status'], 'destination_unreachable')
        self.assertIsNone(result['newRoute'])
        self.assertIsNone(result['newEtaMinutes'])
        self.assertEqual(result['coordinates'], [])
        self.assertIsNotNone(result['originalRoute'])

    def test_unknown_road_segment_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            calculate_adaptive_route(create_test_road_graph(), {
                'startNode': 'A',
                'destinationNode': 'D',
                'trafficScenario': 'Low',
                'blockedRoads': [['A', 'F']],
            })

    def test_keyed_closure_blocks_the_selected_osm_edge_in_both_directions(self):
        graph = create_test_road_graph()
        result = calculate_adaptive_route(graph, {
            'startNode': 'A',
            'destinationNode': 'D',
            'trafficScenario': 'Low',
            'blockedRoads': [{'from': 'B', 'to': 'D', 'key': 0}],
        })

        self.assertNotEqual(result['newRoute']['nodes'], result['originalRoute']['nodes'])
        self.assertEqual(result['blockedRoads'], [{'from': 'B', 'to': 'D', 'key': 0}])
        self.assertTrue(graph.has_edge('B', 'D', 0))
        self.assertTrue(graph.has_edge('D', 'B', 0))

    def test_explicit_congestion_changes_only_selected_road_segment(self):
        graph = create_test_road_graph()
        result = calculate_adaptive_route(graph, {
            'startNode': 'A',
            'destinationNode': 'D',
            'trafficScenario': 'Moderate',
            'congestedRoads': [['A', 'B']],
        })

        adjusted_segments = {
            (item['from'], item['to'])
            for item in result['trafficAdjustments']['edges']
        }
        self.assertEqual(adjusted_segments, {('A', 'B'), ('B', 'A')})
        self.assertGreater(result['newEtaMinutes'], result['originalEtaMinutes'])
        self.assertEqual(graph['A']['B'][0]['estimated_travel_time_min'], 2.0)

    def test_unreachable_original_route_is_reported(self):
        graph = create_test_road_graph()
        graph.remove_edges_from(list(graph.edges))

        result = calculate_adaptive_route(graph, {
            'startNode': 'A',
            'destinationNode': 'D',
            'trafficScenario': 'Low',
        })

        self.assertEqual(result['status'], 'no_original_route')
        self.assertIsNone(result['originalRoute'])
        self.assertIsNone(result['newRoute'])


if __name__ == '__main__':
    unittest.main()