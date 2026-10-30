const DEFAULT_CENTER = [28.6139, 77.209];

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

function getCoordinates(location) {
  if (!location || typeof location !== 'object') return null;
  const lat = Number(location.lat);
  const lng = Number(location.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
  return [lat, lng];
}

export function initializeMap(containerId, onLocationSelect) {
  if (!window.L) throw new Error('Leaflet did not load. Check the network connection.');

  const map = window.L.map(containerId, { zoomControl: false }).setView(DEFAULT_CENTER, 12);
  window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);
  window.L.control.zoom({ position: 'topright' }).addTo(map);

  const vehicleLayer = window.L.layerGroup().addTo(map);
  const incidentLayer = window.L.layerGroup().addTo(map);
  const routeLayer = window.L.layerGroup().addTo(map);
  let selectedLocationMarker = null;

  map.on('click', (event) => {
    const location = { lat: event.latlng.lat, lng: event.latlng.lng };
    if (selectedLocationMarker) map.removeLayer(selectedLocationMarker);
    selectedLocationMarker = window.L.marker(location, {
      icon: window.L.divIcon({
        className: '',
        html: '<span class="selected-map-marker"></span>',
        iconSize: [25, 25],
        iconAnchor: [12, 12],
      }),
      keyboard: true,
      title: 'Selected incident location',
    }).addTo(map);
    onLocationSelect(location);
  });

  function setSelectedLocation(location) {
    if (!location) {
      if (selectedLocationMarker) map.removeLayer(selectedLocationMarker);
      selectedLocationMarker = null;
      return;
    }
    const coordinates = getCoordinates(location);
    if (!coordinates) return;
    if (selectedLocationMarker) map.removeLayer(selectedLocationMarker);
    selectedLocationMarker = window.L.marker(coordinates, {
      icon: window.L.divIcon({
        className: '',
        html: '<span class="selected-map-marker"></span>',
        iconSize: [25, 25],
        iconAnchor: [12, 12],
      }),
      title: 'Selected incident location',
    }).addTo(map);
  }

  function renderVehicles(vehicles) {
    vehicleLayer.clearLayers();
    vehicles.forEach((vehicle) => {
      const coordinates = getCoordinates(vehicle.location);
      if (!coordinates) return;
      const label = vehicle.label ?? vehicle.registration ?? vehicle.id ?? 'Emergency vehicle';
      const type = vehicle.type ?? 'Emergency vehicle';
      window.L.marker(coordinates, {
        icon: window.L.divIcon({
          className: '',
          html: '<span class="vehicle-map-marker" aria-hidden="true">+</span>',
          iconSize: [25, 25],
          iconAnchor: [12, 12],
        }),
        title: String(label),
      })
        .bindPopup(`<strong>${escapeHTML(label)}</strong><br>${escapeHTML(type)}`)
        .addTo(vehicleLayer);
    });
  }

  function renderIncident(incident) {
    incidentLayer.clearLayers();
    const coordinates = getCoordinates(incident?.location);
    if (!coordinates) return;
    const type = incident.type ?? 'Emergency';
    const severity = incident.severity ?? 'Unspecified';
    const marker = window.L.marker(coordinates, {
      icon: window.L.divIcon({
        className: '',
        html: '<span class="incident-map-marker" aria-hidden="true">!</span>',
        iconSize: [25, 25],
        iconAnchor: [12, 12],
      }),
      title: `${type} incident, ${severity} severity`,
      zIndexOffset: 500,
    });
    marker.bindPopup(`<strong>${escapeHTML(type)}</strong><br>Severity: ${escapeHTML(severity)}`);
    incidentLayer.addLayer(marker);
  }

  function renderRoute(coordinates) {
    routeLayer.clearLayers();
    if (!Array.isArray(coordinates) || coordinates.length === 0) return false;
    const validCoordinates = coordinates.map((point) => {
      if (!Array.isArray(point) || point.length < 2) return null;
      const lat = Number(point[0]);
      const lng = Number(point[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
      if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;
      return [lat, lng];
    });
    if (validCoordinates.some((point) => point === null)) return false;

    if (validCoordinates.length === 1) {
      window.L.circleMarker(validCoordinates[0], {
        radius: 7,
        color: '#ffffff',
        weight: 2,
        fillColor: '#31866a',
        fillOpacity: 1,
      }).addTo(routeLayer);
      map.setView(validCoordinates[0], Math.max(map.getZoom(), 15));
      return true;
    }

    const route = window.L.polyline(validCoordinates, {
      color: '#31866a',
      weight: 5,
      opacity: 0.9,
      lineJoin: 'round',
    }).addTo(routeLayer);
    map.fitBounds(route.getBounds(), { padding: [34, 34], maxZoom: 15 });
    return true;
  }

  function fitToFleet(vehicles) {
    const points = vehicles
      .map((vehicle) => getCoordinates(vehicle.location))
      .filter(Boolean);
    if (selectedLocationMarker) points.push(selectedLocationMarker.getLatLng());
    if (points.length > 1) map.fitBounds(window.L.latLngBounds(points), { padding: [30, 30], maxZoom: 14 });
  }

  return { renderVehicles, renderIncident, renderRoute, setSelectedLocation, fitToFleet };
}