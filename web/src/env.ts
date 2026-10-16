import { defineEnvVars } from '@sveltejs/kit/env';
import { building } from '$app/env';

export const variables = defineEnvVars({
	JENDHUB_API_URL: {
		description: 'The base URL of the JendHub API service, for example http://api:8000.',
		schema: (value) => {
			if (value === undefined || value === '') {
				if (building) {
					return '';
				}
				throw new Error('set JENDHUB_API_URL to the base URL of the JendHub API');
			}
			return new URL(value).href;
		}
	}
});
