import { JENDHUB_AGENTS_URL } from '$app/env/private';

export function agentsUrl(path: string, parameters: Record<string, string> = {}): string {
	const url = new URL(path, JENDHUB_AGENTS_URL);
	for (const [name, value] of Object.entries(parameters)) {
		url.searchParams.set(name, value);
	}
	return url.href;
}
