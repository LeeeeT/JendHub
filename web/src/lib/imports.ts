const IMPORT = /^(import )([^./\s]\S*\/\S+\.bend)( as .*)$/;
const BASE = /^(import )(Base)(\s*)$/;
const BASE_FILE = 'Base/base.bend';

export interface SourceLine {
	text: string;
	link: { before: string; label: string; file: string; after: string } | null;
}

export function sourceLine(text: string): SourceLine {
	const base = BASE.exec(text);
	if (base !== null) {
		return { text, link: { before: base[1] ?? '', label: 'Base', file: BASE_FILE, after: '' } };
	}
	const found = IMPORT.exec(text);
	if (found === null) {
		return { text, link: null };
	}
	const [, before = '', file = '', after = ''] = found;
	return { text, link: { before, label: file, file, after } };
}
