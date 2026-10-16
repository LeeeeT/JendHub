import { resolve } from '$app/paths';
import { encodeId, encodeSegment } from '#lib/encoding.ts';

export function definitionHref(id: string): string {
	return resolve('/definitions/[...id]', { id: encodeId(id) });
}

export function packageHref(name: string): string {
	return resolve('/packages/[name]', { name: encodeSegment(name) });
}

export function releaseFilesHref(release: string): string {
	return resolve('/files/[release]', { release: encodeSegment(release) });
}

export function fileHref(file: string): string {
	const [release = '', ...path] = file.split('/');
	return resolve('/files/[release]/[...path]', {
		release: encodeSegment(release),
		path: path.map(encodeSegment).join('/')
	});
}

export function searchHref(query: string): string {
	return `${resolve('/search')}?${new URLSearchParams({ q: query }).toString()}`;
}
