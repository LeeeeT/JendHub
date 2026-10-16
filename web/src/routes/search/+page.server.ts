import { error, redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';
import { search } from '#lib/server/api.ts';
import type { PageServerLoad } from './$types';

const MAX_QUERY = 1000;

export const load: PageServerLoad = async ({ url, fetch }) => {
	const query = (url.searchParams.get('q') ?? '').trim();
	if (query === '') {
		redirect(303, resolve('/'));
	}
	if (query.length > MAX_QUERY) {
		error(400, `A query has at most ${String(MAX_QUERY)} characters.`);
	}
	const found = await search(fetch, query);
	return { query, results: found.results };
};
