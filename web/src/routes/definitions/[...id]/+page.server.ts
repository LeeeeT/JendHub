import { definition } from '#lib/server/search.ts';
import { encodeId } from '#lib/encoding.ts';
import { agentsUrl } from '#lib/server/agents.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => ({
	definition: await definition(fetch, params.id),
	agents: agentsUrl(`definitions/${encodeId(params.id)}`)
});
