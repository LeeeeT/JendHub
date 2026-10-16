import { error } from '@sveltejs/kit';
import { JENDHUB_API_URL } from '$app/env/private';
import type {
	DefinitionPage,
	FileList,
	FileText,
	PackagePage,
	SearchResults,
	StatusPage
} from '#lib/api/types.ts';
import { encodeId, encodeSegment } from '#lib/encoding.ts';

const TIMEOUT_MS = 30_000;
const FAILED = [502, 'The JendHub API failed.'] as const;
const FAILURES = new Map<number, readonly [number, string]>([
	[404, [404, 'Not found.']],
	[422, [400, 'The request is not valid.']],
	[503, [503, 'The search index is not ready. Try again later.']]
]);

type Fetch = typeof fetch;
type Parameters = Record<string, string | number>;

async function request(
	fetcher: Fetch,
	path: string,
	parameters: Parameters,
	accept: string
): Promise<Response> {
	const url = new URL(path, JENDHUB_API_URL);
	for (const [name, value] of Object.entries(parameters)) {
		url.searchParams.set(name, String(value));
	}
	let response: Response;
	try {
		response = await fetcher(url, { headers: { accept }, signal: AbortSignal.timeout(TIMEOUT_MS) });
	} catch {
		error(502, 'The JendHub API does not answer.');
	}
	if (response.ok) {
		return response;
	}
	const [status, message] = FAILURES.get(response.status) ?? FAILED;
	error(status, message);
}

async function get<T>(fetcher: Fetch, path: string, parameters: Parameters): Promise<T> {
	const response = await request(fetcher, path, parameters, 'application/json');
	return (await response.json()) as T;
}

export const SEARCH_LIMIT = 20;
export const PAGE_SIZE = 100;

export function search(fetcher: Fetch, query: string, offset: number): Promise<SearchResults> {
	return get(fetcher, 'search', { q: query, limit: SEARCH_LIMIT, offset });
}

export function definition(fetcher: Fetch, id: string, offset: number): Promise<DefinitionPage> {
	return get(fetcher, `definitions/${encodeId(id)}`, {
		used_by_offset: offset,
		used_by_limit: PAGE_SIZE
	});
}

export function packagePage(fetcher: Fetch, name: string, offset: number): Promise<PackagePage> {
	return get(fetcher, `packages/${encodeSegment(name)}`, {
		definitions_offset: offset,
		definitions_limit: PAGE_SIZE
	});
}

export function status(fetcher: Fetch, offset: number): Promise<StatusPage> {
	return get(fetcher, 'status', {
		invalid_releases_offset: offset,
		invalid_releases_limit: PAGE_SIZE
	});
}

export function releaseFiles(fetcher: Fetch, release: string): Promise<FileList> {
	return get(fetcher, `files/${encodeSegment(release)}`, {});
}

export function fileText(fetcher: Fetch, release: string, path: string): Promise<FileText> {
	return get(fetcher, `files/${encodeSegment(release)}/${encodeId(path)}`, {});
}

export async function agentGuide(fetcher: Fetch): Promise<string> {
	return (await request(fetcher, 'llms.txt', {}, 'text/plain')).text();
}

export async function apiReady(fetcher: Fetch): Promise<boolean> {
	try {
		const response = await fetcher(new URL('readyz', JENDHUB_API_URL), {
			signal: AbortSignal.timeout(2_000)
		});
		return response.ok;
	} catch {
		return false;
	}
}
