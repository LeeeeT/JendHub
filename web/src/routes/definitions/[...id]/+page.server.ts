import { definition, PAGE_SIZE } from '#lib/server/search.ts';
import { offsetFrom } from '#lib/paging.ts';
import { encodeId } from '#lib/encoding.ts';
import { agentsUrl } from '#lib/server/agents.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const offset = offsetFrom(url, 'used_by_offset');
	return {
		definition: await definition(fetch, params.id, offset),
		pageSize: PAGE_SIZE,
		agents: agentsUrl(`definitions/${encodeId(params.id)}`)
	};
};
