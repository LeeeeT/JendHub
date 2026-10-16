import { agentsUrl } from '#lib/server/agents.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = () => ({ agents: agentsUrl('') });
