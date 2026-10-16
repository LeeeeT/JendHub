import { publicApiUrl } from '#lib/server/public.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = () => ({ api: publicApiUrl('') });
