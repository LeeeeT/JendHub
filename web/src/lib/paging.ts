export interface Page {
	offset: number;
	shown: number;
	total: number;
}

export interface PageLinks {
	first: number;
	last: number;
	previous: number | null;
	next: number | null;
}

export function pageLinks({ offset, shown, total }: Page, size: number): PageLinks {
	return {
		first: shown === 0 ? offset : offset + 1,
		last: offset + shown,
		previous: offset > 0 ? Math.max(0, offset - size) : null,
		next: offset + shown < total ? offset + shown : null
	};
}

export interface Location {
	pathname: string;
	search: string;
}

export function offsetFrom(url: Location, name: string): number {
	const value = Number(new URLSearchParams(url.search).get(name) ?? '0');
	return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

export function withOffset(url: Location, name: string, offset: number): string {
	const search = new URLSearchParams(url.search);
	if (offset === 0) {
		search.delete(name);
	} else {
		search.set(name, String(offset));
	}
	const query = search.toString();
	return query === '' ? url.pathname : `${url.pathname}?${query}`;
}
