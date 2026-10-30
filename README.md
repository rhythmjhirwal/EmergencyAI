# EmergencyAI

EmergencyAI is an academic emergency dispatch and route-planning simulation. Road connectivity, lengths, and available geometries come from OpenStreetMap; vehicle locations, fallback speeds, congestion scenarios, and closures are simulated or estimated. It is not connected to emergency services or live traffic and must not be used for real dispatch.

## Setup

Prerequisites: Node.js 18 or newer, Python 3.10 or newer, and internet access for OpenStreetMap/Overpass downloads, map tiles, and package installation.

From the project root, install the Node dependency:

```powershell
npm install
```

If you do not already have a project virtual environment, create one and install the Python dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r python-ai/requirements.txt
```

If `.venv` already exists, use its interpreter for the install command rather than recreating the environment.

The first successful Flask health check downloads a drivable OSMnx graph for central Delhi and caches it as GraphML outside the repository. Later requests reuse the in-memory graph; later service starts load the cached graph. Set `EMERGENCYAI_OSM_CACHE_DIR` to change the cache directory. If OSMnx, its geospatial dependencies, or the Overpass service is unavailable, the API reports an unavailable road network and does not fall back to a synthetic or straight-line route.

Open two terminals in the project root. Start Flask in the first:

```powershell
.\.venv\Scripts\python.exe python-ai/app.py
```

Start Express in the second:

```powershell
npm start
```

Open the dashboard at <http://127.0.0.1:3000>. Flask listens on <http://127.0.0.1:5001>; its health endpoint is <http://127.0.0.1:5001/health>. Set `PYTHON_SERVICE_URL` in the Express environment to change the Flask base URL. The Express timeout defaults to 120 seconds for first-time OSM graph loading and can be changed with `PYTHON_SERVICE_TIMEOUT_MS`.

## Tests

Run the Python routing, dispatch, adaptive-routing, explainability, and Flask API tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s python-ai/tests -v
```

Run the Express response-contract tests:

```powershell
node --test express-backend/tests/emergencyRoutes.test.js
```

The current suite has 43 passing Python tests and 4 passing Express tests. It covers OSM-shaped `MultiDiGraph` routing, a multi-turn Shapely geometry route, dispatch, adaptive keyed-edge costs/closures, Flask input handling, graph caching, and loader failures. A* is compared with Dijkstra on the same graph. A live central-Delhi download produced 6,663 nodes and 15,954 directed edges; a route test returned 26 nodes and 94 geometry coordinates. Browser checks verify normal dispatch rendering, no-responder and unreachable-route panels, Flask-unavailable behavior, and the rendered route line.

## Architecture

- `frontend/` contains the Leaflet dashboard. It sends incident reports to Express and displays returned vehicle, route, ETA, distance, and explanations; it does not calculate routes.
- `express-backend/` serves the frontend, validates HTTP input, supplies the simulated fleet, proxies JSON requests to Flask with `fetch`, and reports combined service health.
- `python-ai/app.py` validates requests and orchestrates the imported Python modules. It returns the requested snake_case fields and compatibility fields used by the current Express/frontend contract.
- `python-ai/data/road_network.py` downloads a `drive` network for central Delhi with OSMnx, estimates speed and travel time, caches GraphML on disk and in memory, and snaps coordinates to connected road nodes.
- `python-ai/algorithms/a_star.py` owns NetworkX A* routing over OSMnx `MultiDiGraph` edges, travel-time cost, keyed parallel-edge selection, and ordered Shapely geometry coordinates.
- `python-ai/services/dispatch.py` filters availability and suitability, calculates an A* route for each eligible vehicle, and ranks candidates with ETA, severity, and suitability factors.
- `python-ai/services/adaptive_routing.py` copies the OSM road graph, changes simulated travel costs on selected route edges and removes keyed/bidirectional closed segments, then recalculates with A*.
- `python-ai/services/explainability.py` describes dispatch values and optional route changes without recalculating the decision.

## Scenario Results

- A, normal accident and normal traffic: passed through the Flask API and Express proxy.
- B, heavy traffic: passed. The API test confirms edge-cost adjustments and an increased ETA without a closure.
- C, road closure: passed. The UI toggle closes a selected edge from the current route; direct API callers can provide OSM node pairs and optional edge keys. Disconnected destinations are reported as unreachable.
- D, nearest ambulance unavailable: passed in the dispatch test. The unavailable unit is rejected and an available suitable unit is selected.
- E, multiple available ambulances: passed in the dispatch test. The lower network ETA ranks first.
- F, no suitable vehicle: passed at the Flask API and Express response-validator levels; the browser no-responder state was verified. The current Express-owned sample fleet always includes a rescue vehicle suitable for the supported emergency types, so this cannot be triggered with the default fleet through the UI without changing fleet data.
- G, competing emergencies: not implemented. There is no shared vehicle reservation, incident queue, or persistent availability state, so simultaneous requests are independent and may recommend the same simulated vehicle.
- A* versus Dijkstra: passed with equal travel-time costs on the same OSM-shaped test graph.
- Flask unavailable: passed. Express returns a service error and the dashboard displays the failure state and updates its health indicator.

## Demonstration

1. Start Flask and Express using the commands above.
2. Open <http://127.0.0.1:3000> and click the map near the displayed simulated fleet. Incident points must be within 10 km of the downloaded drive graph.
3. Submit an accident with Normal traffic and observe the API-selected vehicle, ETA, route, and explanation.
4. Change traffic to Heavy and submit again to see the recalculated route and ETA.
5. Enable Road closure and submit. The UI toggle closes a middle edge from the selected OSM route; the response reports whether A* selected a different geometry/path.
6. Stop Flask and submit once more to demonstrate the service-unavailable message. Restart Flask to continue.

## Limitations

- The selected central-Delhi bbox is a limited OSM extract, not a city-wide road database. Coordinates snap to the nearest drive-graph node; addresses are not geocoded.
- When OSM supplies a Shapely edge geometry, the complete geometry is returned. For direct OSM edges without a geometry attribute, the route includes that edge's adjacent OSM nodes only; an entire route is never replaced with an origin-to-destination chord.
- Edge speed and travel time are estimates from OSM `maxspeed` tags with OSMnx imputation and a documented 30 km/h fallback. They are not observed travel times.
- Low, Moderate, and Heavy apply simulated multipliers to selected baseline-route edges; these are not live traffic measurements.
- The UI road-closure toggle closes a middle edge on the selected route. Direct Flask callers can send explicit OSM node pairs and optional parallel-edge keys.
- Express serves a static fleet whose entries are all marked available. Unavailable-vehicle behavior is covered in Python dispatch tests, not by a fleet-management workflow in the dashboard.
- There is no multi-incident scheduling, persistent state, reservation, authentication, or real-world emergency integration.
- OSM data downloads and OpenStreetMap tiles require network access and can fail due to Overpass limits, local dependency policies, or connectivity; the API reports failures instead of using fake routes. Flask's built-in server is for local development, not production deployment.
