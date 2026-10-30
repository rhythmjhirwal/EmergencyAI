import { Router } from 'express';
import { getSimulatedVehicles } from './vehicleRoutes.js';
import { optimizeEmergency, PythonServiceError } from '../services/pythonService.js';

const router = Router();
const emergencyTypes = new Set(['accident', 'medical', 'fire']);
const severityLevels = new Set(['low', 'medium', 'high', 'critical']);
const trafficScenarios = new Set(['normal', 'moderate', 'heavy']);

function isValidLocation(location) {
  return location !== null
    && typeof location === 'object'
    && !Array.isArray(location)
    && typeof location.lat === 'number'
    && Number.isFinite(location.lat)
    && location.lat >= -90
    && location.lat <= 90
    && typeof location.lng === 'number'
    && Number.isFinite(location.lng)
    && location.lng >= -180
    && location.lng <= 180;
}

function validateEmergency(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return 'Request body must be a JSON object.';
  }
  if (!emergencyTypes.has(body.type)) {
    return 'type must be accident, medical, or fire.';
  }
  if (!severityLevels.has(body.severity)) {
    return 'severity must be low, medium, high, or critical.';
  }
  if (!isValidLocation(body.location)) {
    return 'location must contain valid numeric latitude and longitude coordinates.';
  }
  if (body.trafficScenario !== undefined && !trafficScenarios.has(body.trafficScenario)) {
    return 'trafficScenario must be normal, moderate, or heavy.';
  }
  if (body.roadClosure !== undefined && typeof body.roadClosure !== 'boolean') {
    return 'roadClosure must be a boolean.';
  }
  return null;
}

export function validateOptimizationResponse(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new PythonServiceError('The Python service returned an invalid response.', 502);
  }
  if (data.status === 'no_suitable_vehicle') {
    return data;
  }

  const recommendation = data.recommendation ?? data.assignment ?? data;
  const vehicle = recommendation.assignedVehicle ?? recommendation.vehicle ?? data.assignedVehicle;
  const route = recommendation.route ?? data.route;
  const vehicleId = vehicle?.id ?? vehicle?.vehicleId;

  if (!vehicle || (typeof vehicleId !== 'string' && typeof vehicleId !== 'number')
      || typeof vehicle.type !== 'string' || vehicle.type.trim() === '') {
    throw new PythonServiceError('The Python service response is missing valid assigned vehicle information.', 502);
  }
  if (data.status === 'route_unavailable' && route == null) {
    return data;
  }
  if (!route || typeof route !== 'object' || Array.isArray(route)) {
    throw new PythonServiceError('The Python service response is missing route information.', 502);
  }

  return data;
}

router.post('/', async (request, response, next) => {
  const validationError = validateEmergency(request.body);
  if (validationError) {
    return response.status(400).json({ error: validationError });
  }

  const incident = {
    type: request.body.type,
    severity: request.body.severity,
    location: request.body.location,
    trafficScenario: request.body.trafficScenario ?? 'normal',
    roadClosure: request.body.roadClosure ?? false,
  };

  try {
    // The backend owns the simulated fleet; Python performs all dispatch and route calculations.
    const data = await optimizeEmergency(incident, getSimulatedVehicles());
    return response.json(validateOptimizationResponse(data));
  } catch (error) {
    if (error instanceof PythonServiceError) {
      return response.status(error.statusCode).json({ error: error.message });
    }
    return next(error);
  }
});

export default router;