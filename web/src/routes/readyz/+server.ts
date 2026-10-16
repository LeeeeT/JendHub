import { apiReady } from '#lib/server/api.ts';
import type { RequestHandler } from './$types';

const TEXT = { 'content-type': 'text/plain' };

export const GET: RequestHandler = async ({ fetch }) =>
	(await apiReady(fetch))
		? new Response('ready\n', { headers: TEXT })
		: new Response('not ready\n', { status: 503, headers: TEXT });
