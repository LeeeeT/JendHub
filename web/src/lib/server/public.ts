import { JENDHUB_PUBLIC_API_URL } from '$app/env/private';

export function publicApiUrl(path: string, parameters: Record<string, string> = {}): string {
	const url = new URL(path, JENDHUB_PUBLIC_API_URL);
	for (const [name, value] of Object.entries(parameters)) {
		url.searchParams.set(name, value);
	}
	return url.href;
}
