import assert from 'node:assert/strict';
import test from 'node:test';
import { validateOptimizationResponse } from '../routes/emergencyRoutes.js';
import { optimizeEmergency, PythonServiceError } from '../services/pythonService.js';

test('allows an explicit no-suitable-vehicle outcome without a route', () => {
  const response = {
    status: 'no_suitable_vehicle',
    assignedVehicle: null,
    route: null,
  };

  assert.equal(validateOptimizationResponse(response), response);
});

test('allows a selected vehicle when closures make its route unavailable', () => {
  const response = {
    status: 'route_unavailable',
    assignedVehicle: { id: 'RESCUE-01', type: 'rescue_vehicle' },
    route: null,
  };

  assert.equal(validateOptimizationResponse(response), response);
});

test('continues validating successful assignment responses', () => {
  assert.throws(
    () => validateOptimizationResponse({ status: 'assigned', assignedVehicle: null, route: null }),
    PythonServiceError,
  );
});

test('preserves Flask client validation errors through the Python service helper', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ error: 'Incident location is outside the simulated road network.' }),
    { status: 400, headers: { 'Content-Type': 'application/json' } },
  );

  try {
    await assert.rejects(
      () => optimizeEmergency({ type: 'accident' }, []),
      (error) => error instanceof PythonServiceError
        && error.statusCode === 400
        && error.message.includes('outside the simulated road network'),
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});