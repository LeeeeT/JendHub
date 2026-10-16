import { packagePage, PAGE_SIZE } from '#lib/server/search.ts';
import { offsetFrom } from '#lib/paging.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url, fetch }) => {
	const offset = offsetFrom(url, 'definitions_offset');
	return { package: await packagePage(fetch, params.name, offset), pageSize: PAGE_SIZE };
};
