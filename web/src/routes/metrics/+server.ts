import { registry } from '#lib/server/metrics.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () =>
	new Response(await registry.metrics(), { headers: { 'content-type': registry.contentType } });
