import type { Handle, HandleServerError } from '@sveltejs/kit/hooks';
import { log } from '#lib/server/log.ts';
import { requestSeconds } from '#lib/server/metrics.ts';

const QUIET_ROUTES = new Set(['/healthz', '/readyz', '/metrics']);

export const handle: Handle = async ({ event, resolve }) => {
	const start = performance.now();
	const response = await resolve(event);
	const seconds = (performance.now() - start) / 1000;
	const route = event.route.id ?? 'unknown';
	requestSeconds.labels('web', route, String(response.status)).observe(seconds);
	if (!QUIET_ROUTES.has(route)) {
		log('INFO', 'request', {
			method: event.request.method,
			route,
			status: response.status,
			seconds: Math.round(seconds * 1000) / 1000
		});
	}
	return response;
};

export const handleError: HandleServerError = ({ kind, error, event }) => {
	if (kind === 'unknown') {
		log('ERROR', 'request failed', {
			route: event.route.id ?? 'unknown',
			exception: error instanceof Error ? (error.stack ?? error.message) : String(error)
		});
	}
};
