import { fileText } from '#lib/server/api.ts';
import { encodeId } from '#lib/encoding.ts';
import { publicApiUrl } from '#lib/server/public.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => ({
	file: await fileText(fetch, params.release, params.path),
	api: publicApiUrl(`files/${encodeId(`${params.release}/${params.path}`)}`)
});
