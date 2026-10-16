import { PAGE_SIZE, status } from '#lib/server/search.ts';
import { offsetFrom } from '#lib/paging.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, fetch }) => {
	const offset = offsetFrom(url, 'invalid_releases_offset');
	return { status: await status(fetch, offset), pageSize: PAGE_SIZE };
};
