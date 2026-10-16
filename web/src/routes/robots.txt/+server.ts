import type { RequestHandler } from './$types';

const ROBOTS = 'User-agent: *\nDisallow: /search\n';

export const GET: RequestHandler = () =>
	new Response(ROBOTS, { headers: { 'content-type': 'text/plain; charset=utf-8' } });
