import { describe, expect, it } from 'vitest';
import { offsetFrom, pageLinks, withOffset } from '../src/lib/paging.ts';

describe('pageLinks', () => {
	it('links the first page to the next page only', () => {
		expect(pageLinks({ offset: 0, shown: 100, total: 250 }, 100)).toEqual({
			first: 1,
			last: 100,
			previous: null,
			next: 100
		});
	});

	it('links the last page to the previous page only', () => {
		expect(pageLinks({ offset: 200, shown: 50, total: 250 }, 100)).toEqual({
			first: 201,
			last: 250,
			previous: 100,
			next: null
		});
	});

	it('does not go before the first item', () => {
		expect(pageLinks({ offset: 30, shown: 100, total: 250 }, 100).previous).toBe(0);
	});
});

describe('offsetFrom', () => {
	const at = (search: string) => ({ pathname: '/p', search });

	it('reads an offset', () => {
		expect(offsetFrom(at('?o=40'), 'o')).toBe(40);
	});

	it.each(['', '?o=-1', '?o=x', '?o=1.5'])('falls back to zero for %j', (search) => {
		expect(offsetFrom(at(search), 'o')).toBe(0);
	});
});

describe('withOffset', () => {
	it('keeps the other parameters', () => {
		expect(withOffset({ pathname: '/p', search: '?a=1' }, 'o', 100)).toBe('/p?a=1&o=100');
	});

	it('drops a zero offset', () => {
		expect(withOffset({ pathname: '/p', search: '?o=100' }, 'o', 0)).toBe('/p');
	});
});
