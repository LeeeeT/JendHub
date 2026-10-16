import { agentGuide } from '#lib/server/api.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ fetch }) =>
	new Response(await agentGuide(fetch), {
		headers: { 'content-type': 'text/plain; charset=utf-8' }
	});
