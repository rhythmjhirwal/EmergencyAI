"""Deterministic OSM-shaped road graphs for tests only."""

import networkx as nx
from shapely.geometry import LineString


def create_test_road_graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(crs='epsg:4326', road_network_source='test fixture')
    nodes = {
        'A': (28.6139, 77.2090),
        'B': (28.6229, 77.2090),
        'C': (28.6139, 77.2200),
        'D': (28.6229, 77.2200),
        'E': (28.6050, 77.2200),
        'F': (28.6229, 77.2300),
    }
    for node_id, (latitude, longitude) in nodes.items():
        graph.add_node(node_id, y=latitude, x=longitude)

    roads = [
        ('A', 'B', 1.1, 2.0, 0.5),
        ('B', 'D', 1.2, 2.2, 0.1),
        ('A', 'C', 1.2, 2.8, 0.2),
        ('C', 'D', 1.2, 1.9, 0.1),
        ('A', 'E', 1.1, 2.5, 0.0),
        ('E', 'D', 2.4, 3.6, 0.2),
        ('C', 'E', 1.7, 3.0, 0.0),
        ('D', 'F', 1.0, 1.6, 0.2),
        ('E', 'F', 2.5, 4.0, 0.3),
        ('B', 'F', 2.2, 4.5, 0.4),
    ]
    for start, end, distance_km, time_minutes, traffic in roads:
        start_lat, start_lng = nodes[start]
        end_lat, end_lng = nodes[end]
        bend = ((start_lng + end_lng) / 2 + 0.0004, (start_lat + end_lat) / 2 + 0.0003)
        geometry = LineString([
            (start_lng, start_lat),
            bend,
            (end_lng, end_lat),
        ])
        attributes = {
            'length': distance_km * 1000,
            'distance_km': distance_km,
            'travel_time': time_minutes * 60,
            'speed_kph': distance_km * 60 / time_minutes,
            'base_estimated_travel_time_min': time_minutes,
            'estimated_travel_time_min': time_minutes,
            'base_traffic_cost_min': traffic,
            'traffic_cost_min': traffic,
            'route_cost_min': time_minutes + traffic,
            'closed': False,
            'highway': 'residential',
            'geometry': geometry,
        }
        graph.add_edge(start, end, **attributes)
        graph.add_edge(
            end,
            start,
            **{
                **attributes,
                'geometry': LineString(list(geometry.coords)[::-1]),
            },
        )
    return graph