export function encodeSegment(segment: string): string {
	return encodeURIComponent(segment).replaceAll('%40', '@').replaceAll('%3A', ':');
}

export function encodeId(id: string): string {
	return id.split('/').map(encodeSegment).join('/');
}
