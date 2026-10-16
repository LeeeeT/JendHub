import { searchReady } from '#lib/server/search.ts';
import type { RequestHandler } from './$types';

const TEXT = { 'content-type': 'text/plain' };

export const GET: RequestHandler = async ({ fetch }) =>
	(await searchReady(fetch))
		? new Response('ready\n', { headers: TEXT })
		: new Response('not ready\n', { status: 503, headers: TEXT });
