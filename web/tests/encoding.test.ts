import { describe, expect, it } from 'vitest';
import { encodeId, encodeSegment } from '../src/lib/encoding.ts';

describe('encodeId', () => {
	it('keeps the separators of a definition ID readable', () => {
		expect(encodeId('bend-kit-json@0.5.0.1/json/parse:parse.bytes')).toBe(
			'bend-kit-json@0.5.0.1/json/parse:parse.bytes'
		);
	});

	it('encodes the characters that a path cannot hold', () => {
		expect(encodeId('kit@1.0.0.0/main:a?b#c%d e')).toBe('kit@1.0.0.0/main:a%3Fb%23c%25d%20e');
	});
});

describe('encodeSegment', () => {
	it('encodes a slash', () => {
		expect(encodeSegment('a/b')).toBe('a%2Fb');
	});
});
