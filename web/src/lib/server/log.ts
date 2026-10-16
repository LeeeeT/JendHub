type Level = 'INFO' | 'WARNING' | 'ERROR';

export function log(level: Level, message: string, fields: Record<string, unknown> = {}): void {
	const entry = { time: new Date().toISOString(), level, logger: 'web', message, ...fields };
	process.stdout.write(`${JSON.stringify(entry)}\n`);
}
