const serviceBaseUrl = (process.env.PYTHON_SERVICE_URL ?? 'http://127.0.0.1:5001').replace(/\/$/, '');
const requestTimeoutMs = Number(process.env.PYTHON_SERVICE_TIMEOUT_MS) || 120000;

export class PythonServiceError extends Error {
  constructor(message, statusCode = 503) {
    super(message);
    this.name = 'PythonServiceError';
    this.statusCode = statusCode;
  }
}

async function fetchJSON(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      signal: AbortSignal.timeout(requestTimeoutMs),
      headers: { Accept: 'application/json', ...options.headers },
    });
  } catch (error) {
    if (error.name === 'TimeoutError' || error.name === 'AbortError') {
      throw new PythonServiceError('The Python service request timed out.', 504);
    }
    throw new PythonServiceError('The Python service is unavailable.', 503);
  }

  let data;
  try {
    data = await response.json();
  } catch {
    throw new PythonServiceError('The Python service returned invalid JSON.', 502);
  }

  if (!response.ok) {
    const message = data.error ?? data.message ?? `The Python service returned HTTP ${response.status}.`;
    const statusCode = response.status >= 400 && response.status < 500 ? response.status : 502;
    throw new PythonServiceError(message, statusCode);
  }
  return data;
}

export async function optimizeEmergency(incident, vehicles) {
  return fetchJSON(`${serviceBaseUrl}/optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...incident, vehicles }),
  });
}

export async function getPythonHealth() {
  try {
    await fetchJSON(`${serviceBaseUrl}/health`);
    return { status: 'ok' };
  } catch (error) {
    return { status: 'unavailable', message: error.message };
  }
}