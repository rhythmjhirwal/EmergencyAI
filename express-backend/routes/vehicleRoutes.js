import { Router } from 'express';

const router = Router();

// Simulated fleet for development; replace with the agreed vehicle data source later.
const simulatedVehicles = [
  {
    id: 'AMB-01',
    label: 'Ambulance 01',
    type: 'ambulance',
    status: 'available',
    location: { lat: 28.6221, lng: 77.2084 },
  },
  {
    id: 'FIRE-01',
    label: 'Fire unit 01',
    type: 'fire_engine',
    status: 'available',
    location: { lat: 28.6018, lng: 77.2186 },
  },
  {
    id: 'RESCUE-01',
    label: 'Response unit 01',
    type: 'rescue_vehicle',
    status: 'available',
    location: { lat: 28.6324, lng: 77.1932 },
  },
];

function isValidVehicle(vehicle) {
  return vehicle
    && typeof vehicle.id === 'string'
    && vehicle.id.trim() !== ''
    && typeof vehicle.type === 'string'
    && vehicle.type.trim() !== ''
    && ['available', 'busy', 'offline'].includes(vehicle.status)
    && vehicle.location
    && Number.isFinite(vehicle.location.lat)
    && vehicle.location.lat >= -90
    && vehicle.location.lat <= 90
    && Number.isFinite(vehicle.location.lng)
    && vehicle.location.lng >= -180
    && vehicle.location.lng <= 180;
}

export function getSimulatedVehicles() {
  if (!simulatedVehicles.every(isValidVehicle)) {
    throw new Error('Configured simulated vehicle data is invalid.');
  }
  return simulatedVehicles.map((vehicle) => ({
    ...vehicle,
    location: { ...vehicle.location },
  }));
}

router.get('/', (request, response) => {
  try {
    return response.json({ vehicles: getSimulatedVehicles() });
  } catch (error) {
    return response.status(500).json({ error: error.message });
  }
});

export default router;