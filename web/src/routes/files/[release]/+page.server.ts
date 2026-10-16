import { releaseFiles } from '#lib/server/search.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => ({
	files: await releaseFiles(fetch, params.release)
});
