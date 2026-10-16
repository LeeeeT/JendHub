import { Histogram, Registry, collectDefaultMetrics } from '@prometheus-io/client';

export const registry = new Registry();

collectDefaultMetrics({ register: registry });

export const requestSeconds = new Histogram({
	name: 'jendhub_http_request_seconds',
	help: 'The time to answer an HTTP request.',
	labelNames: ['service', 'route', 'status'] as const,
	registers: [registry]
});
