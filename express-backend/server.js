import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import emergencyRoutes from './routes/emergencyRoutes.js';
import vehicleRoutes from './routes/vehicleRoutes.js';
import { getPythonHealth } from './services/pythonService.js';

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendDirectory = path.resolve(currentDirectory, '..', 'frontend');
const port = Number(process.env.PORT) || 3000;

export function createApp() {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '32kb' }));

  app.get('/api/health', async (request, response) => {
    const python = await getPythonHealth();
    const isHealthy = python.status === 'ok';
    response.status(isHealthy ? 200 : 503).json({
      status: isHealthy ? 'ok' : 'degraded',
      services: {
        express: { status: 'ok' },
        python,
      },
    });
  });

  app.use('/api/emergencies', emergencyRoutes);
  app.use('/api/vehicles', vehicleRoutes);
  app.use(express.static(frontendDirectory));

  app.use((error, request, response, next) => {
    if (response.headersSent) return next(error);

    if (error.type === 'entity.parse.failed') {
      return response.status(400).json({ error: 'Request body contains invalid JSON.' });
    }
    if (error.type === 'entity.too.large') {
      return response.status(413).json({ error: 'Request body exceeds the 32 KB limit.' });
    }

    const statusCode = Number.isInteger(error.statusCode) ? error.statusCode : 500;
    const message = statusCode >= 500 && statusCode !== 502 && statusCode !== 503 && statusCode !== 504
      ? 'An unexpected server error occurred.'
      : error.message;
    return response.status(statusCode).json({ error: message });
  });

  return app;
}

const app = createApp();
const isMainModule = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) {
  app.listen(port, () => {
    console.log(`EmergencyAI Express server listening on http://127.0.0.1:${port}`);
  });
}