import { definition, PAGE_SIZE } from '#lib/server/api.ts';
import { offsetFrom } from '#lib/paging.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const offset = offsetFrom(url, 'used_by_offset');
	return { definition: await definition(fetch, params.id, offset), pageSize: PAGE_SIZE };
};
