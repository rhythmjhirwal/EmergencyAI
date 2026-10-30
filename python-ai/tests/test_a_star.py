import sys
from pathlib import Path
import unittest

import networkx as nx
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from algorithms.a_star import find_optimal_route
from tests.road_fixtures import create_test_road_graph


class AStarTests(unittest.TestCase):
    def test_osm_multidigraph_route_preserves_multiple_geometry_turns(self):
        graph = nx.MultiDiGraph()
        graph.add_node(101, x=77.2000, y=28.6000)
        graph.add_node(202, x=77.2100, y=28.6100)
        graph.add_node(303, x=77.2200, y=28.6200)
        graph.add_edge(
            101,
            202,
            key=0,
            length=1500.0,
            travel_time=180.0,
            geometry=LineString([
                (77.2000, 28.6000),
                (77.2060, 28.6040),
                (77.2100, 28.6100),
            ]),
        )
        graph.add_edge(
            202,
            303,
            key=0,
            length=1600.0,
            travel_time=120.0,
            geometry=LineString([
                (77.2200, 28.6200),
                (77.2160, 28.6160),
                (77.2100, 28.6100),
            ]),
        )

        result = find_optimal_route(graph, 101, 303)

        self.assertEqual(result['nodes'], [101, 202, 303])
        self.assertEqual(result['coordinates'], [
            [28.6, 77.2],
            [28.604, 77.206],
            [28.61, 77.21],
            [28.616, 77.216],
            [28.62, 77.22],
        ])
        self.assertEqual(result['edgeSegments'], [
            {'from': 101, 'to': 202, 'key': 0},
            {'from': 202, 'to': 303, 'key': 0},
        ])
        self.assertAlmostEqual(result['etaMinutes'], 5.0)
        self.assertAlmostEqual(result['distanceKm'], 3.1)

    def test_astar_matches_dijkstra_on_same_road_graph(self):
        graph = create_test_road_graph()

        result = find_optimal_route(graph, 'A', 'D')
        dijkstra_cost = nx.dijkstra_path_length(graph, 'A', 'D', weight='route_cost_min')

        self.assertAlmostEqual(result['etaMinutes'], dijkstra_cost)
        self.assertEqual(result['nodes'], ['A', 'B', 'D'])
        self.assertEqual(result['coordinates'][0], [28.6139, 77.209])
        self.assertAlmostEqual(result['distanceKm'], 2.3)

    def test_closed_road_is_avoided(self):
        graph = create_test_road_graph()
        graph['A']['B'][0]['closed'] = True

        result = find_optimal_route(graph, 'A', 'D')

        self.assertNotEqual(result['nodes'], ['A', 'B', 'D'])

    def test_invalid_location_is_rejected(self):
        graph = create_test_road_graph()

        with self.assertRaisesRegex(ValueError, 'Unknown start location'):
            find_optimal_route(graph, 'missing', 'D')

    def test_unreachable_destination_raises_networkx_error(self):
        graph = create_test_road_graph()
        graph.remove_edges_from(list(graph.edges))

        with self.assertRaises(nx.NetworkXNoPath):
            find_optimal_route(graph, 'A', 'D')

    def test_missing_edge_geometry_uses_only_adjacent_osm_node_segment(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, x=77.2, y=28.6)
        graph.add_node(2, x=77.21, y=28.61)
        graph.add_edge(1, 2, key=0, length=1000, travel_time=120, speed_kph=30)

        result = find_optimal_route(graph, 1, 2)

        self.assertEqual(result['coordinates'], [[28.6, 77.2], [28.61, 77.21]])

    def test_equal_cost_parallel_edges_keep_networkx_geometry_tie_order(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, x=77.2, y=28.6)
        graph.add_node(2, x=77.22, y=28.62)
        graph.add_edge(
            1,
            2,
            key=2,
            length=2000,
            travel_time=240,
            speed_kph=30,
            geometry=LineString([(77.2, 28.6), (77.21, 28.615), (77.22, 28.62)]),
        )
        graph.add_edge(
            1,
            2,
            key=10,
            length=2000,
            travel_time=240,
            speed_kph=30,
            geometry=LineString([(77.2, 28.6), (77.215, 28.605), (77.22, 28.62)]),
        )

        result = find_optimal_route(graph, 1, 2)

        self.assertEqual(result['edgeSegments'][0]['key'], 2)
        self.assertEqual(result['coordinates'][1], [28.615, 77.21])


if __name__ == '__main__':
    unittest.main()