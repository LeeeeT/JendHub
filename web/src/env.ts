import { defineEnvVars } from '@sveltejs/kit/env';
import { building } from '$app/env';

function requiredUrl(name: string, purpose: string) {
	return {
		description: `${purpose}, for example http://search:8000.`,
		schema: (value: string | undefined) => {
			if (value === undefined || value === '') {
				if (building) {
					return '';
				}
				throw new Error(`set ${name} to ${purpose.toLowerCase()}`);
			}
			const url = new URL(value);
			url.pathname = url.pathname.endsWith('/') ? url.pathname : `${url.pathname}/`;
			return url.href;
		}
	};
}

export const variables = defineEnvVars({
	JENDHUB_SEARCH_URL: requiredUrl(
		'JENDHUB_SEARCH_URL',
		'The base URL of the JendHub search service'
	),
	JENDHUB_AGENTS_URL: requiredUrl(
		'JENDHUB_AGENTS_URL',
		'The public base URL of the JendHub agent interface'
	)
});
