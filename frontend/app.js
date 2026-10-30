import { initializeMap } from './map.js';

/*
 * API contract:
 * POST /api/emergencies accepts { type, severity, location: { lat, lng }, trafficScenario, roadClosure }.
 * GET /api/vehicles returns { vehicles: [{ id, type, status, location: { lat, lng }, label? }] }.
 * A dispatch response contains emergency, assignedVehicle (or recommendation.vehicle), route
 * with coordinates as [lat, lng] pairs and distanceKm/etaMinutes, and reasons (or explanation).
 */
const API = {
  health: '/api/health',
  emergencies: '/api/emergencies',
  vehicles: '/api/vehicles',
};

const form = document.querySelector('#emergency-form');
const submitButton = document.querySelector('#submit-emergency');
const globalError = document.querySelector('#global-error');
const globalErrorText = document.querySelector('#global-error-text');
const vehicleTableBody = document.querySelector('#vehicle-table-body');
const state = { vehicles: [], selectedLocation: null, assignedVehicleId: null };

let responseMap;

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

function setApiStatus(status, message) {
  const indicator = document.querySelector('.status-indicator');
  indicator.className = `status-indicator is-${status}`;
  document.querySelector('#api-status-text').textContent = message;
}

function showError(message) {
  globalErrorText.textContent = message;
  globalError.hidden = false;
}

function clearError() {
  globalError.hidden = true;
  globalErrorText.textContent = '';
}

async function requestJSON(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: { Accept: 'application/json', ...options.headers },
    });
  } catch {
    throw new Error('Could not reach the Express API. Confirm the server is running and try again.');
  }

  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`The API returned an invalid JSON response (${response.status}).`);
  }

  if (!response.ok) {
    throw new Error(data.message ?? data.error ?? `Request failed with status ${response.status}.`);
  }
  return data;
}

function getVehiclesPayload(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.vehicles)) return data.vehicles;
  throw new Error('The vehicle API response must contain a vehicles array.');
}

function displayValue(value, suffix = '') {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return `${value}${suffix}`;
  return `${Number.isInteger(numeric) ? numeric : numeric.toFixed(1)}${suffix}`;
}

function vehicleLabel(vehicle) {
  return vehicle.label ?? vehicle.registration ?? vehicle.name ?? vehicle.id ?? 'Unlabeled vehicle';
}

function renderVehicleTable() {
  document.querySelector('#fleet-count').textContent = `${state.vehicles.length} ${state.vehicles.length === 1 ? 'unit' : 'units'}`;
  if (state.vehicles.length === 0) {
    vehicleTableBody.innerHTML = '<tr class="table-placeholder"><td colspan="5">No vehicles were returned by the API.</td></tr>';
    return;
  }

  vehicleTableBody.innerHTML = state.vehicles.map((vehicle) => {
    const assigned = state.assignedVehicleId !== null
      && String(vehicle.id) === String(state.assignedVehicleId);
    const status = vehicle.status ?? 'unknown';
    const available = String(status).toLowerCase() === 'available';
    const location = vehicle.location && Number.isFinite(Number(vehicle.location.lat))
      && Number.isFinite(Number(vehicle.location.lng))
      ? `${Number(vehicle.location.lat).toFixed(3)}, ${Number(vehicle.location.lng).toFixed(3)}`
      : '—';
    return `<tr>
      <td class="vehicle-name">${escapeHTML(vehicleLabel(vehicle))}</td>
      <td>${escapeHTML(vehicle.type ?? '—')}</td>
      <td><span class="status-pill${available ? '' : ' is-unavailable'}">${escapeHTML(status)}</span></td>
      <td>${escapeHTML(location)}</td>
      <td>${assigned ? '<span class="selected-label">Selected</span>' : '<span aria-label="Not selected">—</span>'}</td>
    </tr>`;
  }).join('');
}

function updateLocationUI(location) {
  state.selectedLocation = location;
  const hasLocation = Boolean(location);
  document.querySelector('#latitude').value = hasLocation ? String(location.lat) : '';
  document.querySelector('#longitude').value = hasLocation ? String(location.lng) : '';
  document.querySelector('#location-label').textContent = hasLocation ? 'Selected coordinates' : 'Incident location';
  document.querySelector('#location-value').textContent = hasLocation
    ? `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`
    : 'Select a point on the map';
  document.querySelector('#map-footer-text').textContent = hasLocation
    ? `Incident point ${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`
    : 'No incident location selected';
}

function getRecommendationPayload(data) {
  const recommendation = data.recommendation ?? data.assignment ?? data;
  const vehicle = recommendation.assignedVehicle ?? recommendation.vehicle ?? data.assignedVehicle;
  const route = recommendation.route ?? data.route;
  const reasons = recommendation.reasons ?? recommendation.explanation
    ?? data.reasons ?? data.explanation;
  if (!vehicle || !route) {
    throw new Error('The emergency API response is missing the assigned vehicle or route.');
  }
  return { emergency: data.emergency ?? {}, vehicle, route, reasons };
}

function renderReasons(reasons) {
  const reasonList = document.querySelector('#reason-list');
  reasonList.replaceChildren();
  const reasonItems = Array.isArray(reasons) ? reasons : (typeof reasons === 'string' ? [reasons] : []);
  reasonItems.forEach((reason) => {
    const item = document.createElement('li');
    item.textContent = String(reason);
    reasonList.append(item);
  });
  reasonList.hidden = reasonItems.length === 0;
  document.querySelector('#explanation-empty').hidden = reasonItems.length > 0;
}

function renderDispatchOutcome(title, message, data = {}) {
  state.assignedVehicleId = null;
  renderVehicleTable();
  document.querySelector('#dispatch-empty').hidden = true;
  document.querySelector('#dispatch-result').hidden = true;
  document.querySelector('#dispatch-failure').hidden = false;
  document.querySelector('#dispatch-failure-title').textContent = title;
  document.querySelector('#dispatch-failure-message').textContent = message;
  renderReasons(data.reasons ?? data.explanation?.reasons);
  responseMap.renderRoute([]);

  const incident = data.incident ?? data.emergency ?? {
    type: document.querySelector('#emergency-type').value,
    severity: document.querySelector('#severity').value,
    location: state.selectedLocation,
  };
  responseMap.renderIncident(incident);
}

function renderRecommendation(data) {
  if (data.status === 'no_suitable_vehicle') {
    renderDispatchOutcome(
      'No suitable vehicle available',
      data.explanation?.summary ?? data.reasons?.[0] ?? 'No eligible responder was returned by the dispatch service.',
      data,
    );
    return;
  }
  if (data.status === 'route_unavailable') {
    renderDispatchOutcome(
      'No reachable route',
      data.explanation?.summary ?? 'A vehicle was considered, but no route is available under the current simulated road conditions.',
      data,
    );
    return;
  }

  const { emergency, vehicle, route, reasons } = getRecommendationPayload(data);
  state.assignedVehicleId = vehicle.id ?? vehicle.vehicleId ?? null;
  renderVehicleTable();

  document.querySelector('#dispatch-empty').hidden = true;
  document.querySelector('#dispatch-failure').hidden = true;
  document.querySelector('#dispatch-result').hidden = false;
  document.querySelector('#assigned-vehicle').textContent = vehicleLabel(vehicle);
  document.querySelector('#assigned-type').textContent = vehicle.type ?? 'Emergency vehicle';
  document.querySelector('#assigned-status').textContent = vehicle.status ?? 'Assigned';
  document.querySelector('#result-eta').textContent = displayValue(route.etaMinutes);
  document.querySelector('#result-distance').textContent = displayValue(route.distanceKm);
  document.querySelector('#result-severity').textContent = emergency.severity
    ?? document.querySelector('#severity').value;
  document.querySelector('#result-stamp').textContent = emergency.id
    ? `Incident ${emergency.id} · recommendation returned by API`
    : 'Recommendation returned by API';

  const explanationSummary = typeof data.explanation?.summary === 'string'
    ? [data.explanation.summary]
    : [];
  const trafficReason = data.traffic?.scenario
    ? [`Traffic scenario: simulated ${data.traffic.scenario}.`]
    : [];
  const detailedReasons = Array.isArray(reasons) ? reasons : (typeof reasons === 'string' ? [reasons] : []);
  renderReasons([...explanationSummary, ...trafficReason, ...detailedReasons]);

  const incident = {
    ...emergency,
    location: emergency.location ?? state.selectedLocation,
    severity: emergency.severity ?? document.querySelector('#severity').value,
    type: emergency.type ?? document.querySelector('#emergency-type').value,
  };
  responseMap.renderIncident(incident);
  if (Array.isArray(data.vehicles)) {
    state.vehicles = data.vehicles;
    responseMap.renderVehicles(state.vehicles);
    renderVehicleTable();
  }
  const routeDrawn = responseMap.renderRoute(route.coordinates);
  if (!routeDrawn) {
    showError('The API returned the dispatch recommendation, but its route coordinates are missing or invalid.');
  }
}

async function loadVehicles() {
  try {
    const data = await requestJSON(API.vehicles);
    state.vehicles = getVehiclesPayload(data);
    responseMap.renderVehicles(state.vehicles);
    renderVehicleTable();
    responseMap.fitToFleet(state.vehicles);
  } catch (error) {
    vehicleTableBody.innerHTML = '<tr class="table-placeholder"><td colspan="5">Vehicle data is unavailable.</td></tr>';
    document.querySelector('#fleet-count').textContent = 'Unavailable';
    setApiStatus('offline', 'API unavailable');
    showError(error.message);
  }
}

async function checkApiHealth() {
  try {
    const health = await requestJSON(API.health);
    if (health.status === 'ok' && health.services?.python?.status === 'ok') {
      setApiStatus('online', 'API connected');
      return;
    }
    setApiStatus('offline', 'AI service unavailable');
  } catch {
    setApiStatus('offline', 'API unavailable');
  }
}

async function submitEmergency(event) {
  event.preventDefault();
  clearError();
  if (!state.selectedLocation) {
    showError('Select the incident location by clicking on the map before dispatching.');
    document.querySelector('#response-map').scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }

  const submitLabel = submitButton.querySelector('.button-label');
  submitButton.disabled = true;
  submitButton.classList.add('is-loading');
  submitLabel.textContent = 'Requesting dispatch';

  const payload = {
    type: document.querySelector('#emergency-type').value,
    severity: document.querySelector('#severity').value,
    location: { lat: state.selectedLocation.lat, lng: state.selectedLocation.lng },
    trafficScenario: document.querySelector('#traffic-scenario').value,
    roadClosure: document.querySelector('#road-closure').checked,
  };

  try {
    const data = await requestJSON(API.emergencies, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    renderRecommendation(data);
    document.querySelector('#dispatch-title').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    showError(error.message);
    renderDispatchOutcome('Dispatch unavailable', error.message);
    if (error.message.includes('Python service')) {
      setApiStatus('offline', 'AI service unavailable');
    }
  } finally {
    submitButton.disabled = false;
    submitButton.classList.remove('is-loading');
    submitLabel.textContent = 'Dispatch response';
  }
}

function initialize() {
  try {
    responseMap = initializeMap('response-map', updateLocationUI);
  } catch (error) {
    setApiStatus('offline', 'Map unavailable');
    showError(error.message);
    return;
  }

  form.addEventListener('submit', submitEmergency);
  document.querySelector('#reset-map').addEventListener('click', () => {
    updateLocationUI(null);
    responseMap.setSelectedLocation(null);
  });
  document.querySelector('#dismiss-error').addEventListener('click', clearError);
  loadVehicles();
  checkApiHealth();
}

initialize();