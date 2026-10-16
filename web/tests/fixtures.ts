import type {
	DefinitionPage,
	PackagePage,
	SearchResults,
	StatusPage
} from '../src/lib/api/types.ts';

export const DOUBLE = 'kit@1.0.0.0/main:double';
export const ODD = 'kit@1.0.0.0/deep/odd:a?b';

export const results: SearchResults = {
	results: [
		{
			id: DOUBLE,
			kind: 'def',
			name: 'double',
			package: 'kit',
			version: '1.0.0.0',
			module: 'main',
			head: '# Doubles a number.\ndef double(n: Nat) -> Nat:',
			score: 0.9
		},
		{
			id: 'Nat.add',
			kind: 'def',
			name: 'Nat.add',
			package: null,
			version: null,
			module: 'Base',
			head: 'def Nat.add(a: Nat, b: Nat) -> Nat:',
			score: 0.5
		}
	]
};

export const definition: DefinitionPage = {
	id: DOUBLE,
	kind: 'def',
	name: 'double',
	package: 'kit',
	version: '1.0.0.0',
	module: 'main',
	text: '# Doubles a number.\ndef double(n: Nat) -> Nat:\n  Nat.add(n, n)',
	first_line: 2,
	end_line: 5,
	uses: ['Nat', 'Nat.add'],
	used_by: [ODD],
	used_by_total: 150,
	used_by_offset: 100
};

export const kit: PackagePage = {
	name: 'kit',
	releases: [
		{ version: '2.0.0.0', hash: '0x2', state: 'invalid', reason: 'main.bend does not load' },
		{ version: '1.0.0.0', hash: '0x1', state: 'indexed', reason: null }
	],
	definitions: [{ id: DOUBLE, kind: 'def', name: 'double', module: 'main', head: 'def double:' }],
	definitions_total: 1,
	definitions_offset: 0
};

export const status: StatusPage = {
	generations: [
		{
			id: 3,
			state: 'live',
			base_tag: '0.1.2',
			indexer_version: 2,
			created_at: '2026-10-16T00:00:00Z',
			releases: 107,
			definitions: 56650,
			jobs: { queued: 0, running: 0, done: 107, superseded: 2, invalid: 10, failed: 0 }
		}
	],
	invalid_releases: [
		{
			name: 'kit',
			version: '2.0.0.0',
			hash: '0x2',
			base_tag: '0.1.2',
			reason: 'main.bend does not load',
			found_at: '2026-10-15T12:00:00Z'
		}
	],
	invalid_releases_total: 1,
	invalid_releases_offset: 0
};
