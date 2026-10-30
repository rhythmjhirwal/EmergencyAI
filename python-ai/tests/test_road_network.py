import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.road_network import (
    RoadNetworkUnavailableError,
    _prepare_graph,
    load_drive_graph,
    snap_to_nearest_road_node,
)


class RoadNetworkTests(unittest.TestCase):
    def test_snap_uses_nearest_connected_osm_node(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, x=77.2, y=28.6)
        graph.add_node(2, x=77.21, y=28.61)
        graph.add_node(3, x=77.22, y=28.62)
        graph.add_edge(1, 2, key=0)
        graph.add_edge(2, 3, key=0)

        node, distance = snap_to_nearest_road_node(graph, 28.6099, 77.2099)

        self.assertEqual(node, 2)
        self.assertLess(distance, 0.02)

    def test_snap_uses_osmnx_nearest_nodes_with_lon_lat_order(self):
        graph = nx.MultiDiGraph()
        graph.add_node(5, x=77.2, y=28.6)
        graph.add_node(6, x=77.21, y=28.61)
        graph.add_edge(5, 6, key=0)
        nearest_nodes = Mock(return_value=6)
        fake_osmnx = SimpleNamespace(distance=SimpleNamespace(nearest_nodes=nearest_nodes))

        with patch.dict(sys.modules, {'osmnx': fake_osmnx}):
            node, distance = snap_to_nearest_road_node(graph, 28.6099, 77.2099)

        self.assertEqual(node, 6)
        self.assertGreaterEqual(distance, 0)
        nearest_nodes.assert_called_once_with(graph, X=77.2099, Y=28.6099)

    def test_snap_rejects_graph_without_connected_coordinate_nodes(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, x=77.2, y=28.6)

        with self.assertRaisesRegex(ValueError, 'no connected nodes'):
            snap_to_nearest_road_node(graph, 28.6, 77.2)

    def test_prepare_graph_converts_osm_length_and_travel_time(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, x=77.2, y=28.6)
        graph.add_node(2, x=77.21, y=28.61)
        from shapely.geometry import LineString

        graph.add_edge(
            1,
            2,
            key=0,
            length=1250.0,
            travel_time=150.0,
            speed_kph=30.0,
            geometry=LineString([(77.2, 28.6), (77.21, 28.61)]),
        )

        prepared = _prepare_graph(graph)
        edge = prepared[1][2][0]

        self.assertEqual(prepared.graph['road_network_source'], 'OpenStreetMap')
        self.assertEqual(edge['distance_km'], 1.25)
        self.assertEqual(edge['estimated_travel_time_min'], 2.5)
        self.assertEqual(edge['base_estimated_travel_time_min'], 2.5)

    def test_prepare_graph_accepts_osm_edges_without_intermediate_geometry(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, x=77.2, y=28.6)
        graph.add_node(2, x=77.2002, y=28.6002)
        graph.add_edge(1, 2, key=0, length=30.0, travel_time=4.0, speed_kph=27.0)

        prepared = _prepare_graph(graph)

        self.assertIsNone(prepared[1][2][0].get('geometry'))

    def test_memory_cached_graph_is_reused(self):
        graph = nx.MultiDiGraph()
        with patch('data.road_network._cached_graph', graph):
            self.assertIs(load_drive_graph(), graph)

    def test_load_graph_reports_osmnx_import_failure_without_fallback(self):
        from data import road_network

        with patch.dict(sys.modules, {'osmnx': None}):
            with self.assertRaisesRegex(RoadNetworkUnavailableError, 'OSMnx could not be loaded'):
                road_network.load_drive_graph(force_refresh=True)


if __name__ == '__main__':
    unittest.main()