import { defineEnvVars } from '@sveltejs/kit/env';
import { building } from '$app/env';

function requiredUrl(name: string, purpose: string) {
	return {
		description: `${purpose}, for example http://api:8000.`,
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
	JENDHUB_API_URL: requiredUrl('JENDHUB_API_URL', 'The base URL of the JendHub API service'),
	JENDHUB_PUBLIC_API_URL: requiredUrl(
		'JENDHUB_PUBLIC_API_URL',
		'The base URL of the JendHub API that agents use'
	)
});
