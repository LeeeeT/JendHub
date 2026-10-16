import { fileText } from '#lib/server/search.ts';
import { encodeId } from '#lib/encoding.ts';
import { agentsUrl } from '#lib/server/agents.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, fetch }) => ({
	file: await fileText(fetch, params.release, params.path),
	agents: agentsUrl(`files/${encodeId(`${params.release}/${params.path}`)}`)
});
