import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { definition, DOUBLE, kit, ODD, results, status } from './fixtures.ts';

const SERVER = new URL('../build/index.js', import.meta.url).pathname;

let api: Server;
let web: ChildProcess;
let origin: string;
const requests: string[] = [];

function send(response: ServerResponse, code: number, body: unknown): void {
	response.writeHead(code, { 'content-type': 'application/json' });
	response.end(JSON.stringify(body));
}

function answer(request: IncomingMessage, response: ServerResponse): void {
	const target = request.url ?? '/';
	requests.push(target);
	const url = new URL(target, 'http://api.test');
	const path = url.pathname;
	if (path === '/readyz') {
		response.end('ready\n');
	} else if (path === '/v1/search') {
		const query = url.searchParams.get('q');
		if (query === 'down') {
			send(response, 503, { detail: 'the search index is not ready' });
		} else {
			send(response, 200, query === 'nothing' ? { results: [] } : results);
		}
	} else if (path === `/v1/definitions/${DOUBLE}`) {
		send(response, 200, definition);
	} else if (path === '/v1/definitions/kit@1.0.0.0/deep/odd:a%3Fb') {
		send(response, 200, { ...definition, id: ODD, used_by: [], used_by_total: 0 });
	} else if (path === '/v1/packages/kit') {
		send(response, 200, kit);
	} else if (path === '/v1/status') {
		send(response, 200, status);
	} else {
		send(response, 404, { detail: 'not found' });
	}
}

async function listen(server: Server): Promise<number> {
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	return (server.address() as AddressInfo).port;
}

async function freePort(): Promise<number> {
	const probe = createServer();
	const port = await listen(probe);
	await new Promise((resolve) => probe.close(resolve));
	return port;
}

async function waitUntilHealthy(): Promise<void> {
	for (let attempt = 0; attempt < 100; attempt += 1) {
		try {
			if ((await fetch(`${origin}/healthz`)).ok) {
				return;
			}
		} catch {
			await new Promise((resolve) => setTimeout(resolve, 100));
		}
	}
	throw new Error('the web server did not start');
}

async function page(path: string): Promise<{ status: number; html: string; response: Response }> {
	const response = await fetch(`${origin}${path}`, { redirect: 'manual' });
	return { status: response.status, html: await response.text(), response };
}

beforeAll(async () => {
	if (!existsSync(SERVER)) {
		throw new Error('build the app with `npm run build` before the tests');
	}
	api = createServer(answer);
	const apiPort = await listen(api);
	const port = await freePort();
	origin = `http://127.0.0.1:${String(port)}`;
	web = spawn(process.execPath, [SERVER], {
		env: {
			...process.env,
			HOST: '127.0.0.1',
			PORT: String(port),
			JENDHUB_API_URL: `http://127.0.0.1:${String(apiPort)}`
		},
		stdio: ['ignore', 'ignore', 'inherit']
	});
	await waitUntilHealthy();
});

afterAll(async () => {
	web.kill();
	await new Promise((resolve) => api.close(resolve));
});

describe('the search page', () => {
	it('lists each hit with links to its definition and package', async () => {
		const { status: code, html } = await page('/search?q=double+a+number');
		expect(code).toBe(200);
		expect(html).toContain(`definitions/${DOUBLE}"`);
		expect(html).toContain('packages/kit"');
		expect(html).toContain('# Doubles a number.');
		expect(html).toContain('Base');
		expect(requests).toContain('/v1/search?q=double+a+number&limit=20');
	});

	it('says when nothing matches', async () => {
		expect((await page('/search?q=nothing')).html).toContain('No definition matches');
	});

	it('sends an empty query to the home page', async () => {
		const { status: code, response } = await page('/search?q=+');
		expect(code).toBe(303);
		const location = new URL(response.headers.get('location') ?? '', `${origin}/search`);
		expect(location.pathname).toBe('/');
	});

	it('reports an index that is not ready', async () => {
		const { status: code, html } = await page('/search?q=down');
		expect(code).toBe(503);
		expect(html).toContain('The search index is not ready');
	});
});

describe('the definition page', () => {
	it('shows the source with its line numbers, its uses and a page of its users', async () => {
		const { status: code, html } = await page(`/definitions/${DOUBLE}?used_by_offset=100`);
		expect(code).toBe(200);
		expect(html).toContain('data-line="3"');
		expect(html).toContain('definitions/Nat.add"');
		expect(html).toContain('definitions/kit@1.0.0.0/deep/odd:a%3Fb"');
		expect(html).toContain('101 to 101 of 150');
		expect(html).toContain('rel="prev"');
		expect(requests).toContain(`/v1/definitions/${DOUBLE}?used_by_offset=100&used_by_limit=100`);
	});

	it('passes an encoded ID to the API', async () => {
		const { status: code, html } = await page('/definitions/kit@1.0.0.0/deep/odd:a%3Fb');
		expect(code).toBe(200);
		expect(html).toContain('kit@1.0.0.0/deep/odd:a?b');
	});

	it('reports an unknown definition', async () => {
		expect((await page('/definitions/nothing')).status).toBe(404);
	});
});

describe('the package page', () => {
	it('shows each release with its state and the indexed definitions', async () => {
		const { status: code, html } = await page('/packages/kit');
		expect(code).toBe(200);
		expect(html).toContain('Search uses release 1.0.0.0.');
		expect(html).toContain('invalid');
		expect(html).toContain('main.bend does not load');
		expect(html).toContain(`definitions/${DOUBLE}"`);
	});
});

describe('the status page', () => {
	it('shows the generations and the invalid releases', async () => {
		const { status: code, html } = await page('/status');
		expect(code).toBe(200);
		expect(html).toContain('56,650');
		expect(html).toContain('main.bend does not load');
	});
});

describe('the service endpoints', () => {
	it('answers health, readiness and metrics', async () => {
		expect(await (await fetch(`${origin}/healthz`)).json()).toEqual({ status: 'ok' });
		expect((await fetch(`${origin}/readyz`)).status).toBe(200);
		const metrics = await (await fetch(`${origin}/metrics`)).text();
		expect(metrics).toContain('jendhub_http_request_seconds_bucket{le="0.005",service="web"');
	});

	it('sends pages with no script and a strict content security policy', async () => {
		const { html, response } = await page('/');
		expect(html).not.toContain('<script');
		expect(response.headers.get('content-security-policy')).toContain("script-src 'none'");
	});
});
