import type { components } from './schema.d.ts';

type Schemas = components['schemas'];

export type SearchResults = Schemas['SearchResults'];
export type SearchHit = Schemas['SearchHit'];
export type DefinitionPage = Schemas['DefinitionPage'];
export type PackagePage = Schemas['PackagePage'];
export type ReleaseSummary = Schemas['ReleaseSummary'];
export type DefinitionSummary = Schemas['DefinitionSummary'];
export type StatusPage = Schemas['StatusPage'];
export type GenerationSummary = Schemas['GenerationSummary'];
export type InvalidReleaseSummary = Schemas['InvalidReleaseSummary'];
