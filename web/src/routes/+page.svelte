<script lang="ts">
	import { resolve } from '$app/paths';
	import SearchForm from '#lib/components/SearchForm.svelte';
	import { searchHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const examples = ['decompress gzip data', 'base64 encode', 'sha256 hash of bytes', 'parse json'];
</script>

<svelte:head>
	<title>JendHub · Search Bend definitions</title>
	<meta
		name="description"
		content="Search the definitions of Base and of every package on BendHub by task or by name."
	/>
</svelte:head>

<div class="hero">
	<h1>JendHub</h1>
	<p class="sub">a <b>search engine</b> for <b>Bend</b> definitions</p>
	<p class="tag"><b>Base</b> · every <b>BendHub</b> package · by <b>task</b> or by <b>name</b></p>
</div>

<SearchForm large />
<p class="examples muted">
	Try
	{#each examples as example, index (example)}<a href={searchHref(example)}>{example}</a>{index <
		examples.length - 1
			? ', '
			: '.'}
	{/each}
</p>

<section id="agents">
	<h2><span>1.</span> For agents.</h2>
	<p>
		Tell your agent to read <a href="{data.agents}llms.txt">{data.agents}llms.txt</a>. The agent
		interface answers in plain text, with full URLs to the next steps.
	</p>
	<pre><code
			><span class="muted"># the best definitions for a query</span>
GET {data.agents}search?q=&lt;query&gt;&amp;limit=10

<span class="muted"># a definition with the imports that its code uses</span>
GET {data.agents}definitions/&lt;id&gt;

<span class="muted"># a file, with each import at an exact release</span>
GET {data.agents}files/&lt;name@version&gt;/&lt;path&gt;

<span class="muted"># the releases and definitions of a package</span>
GET {data.agents}packages/&lt;name&gt;

<span class="muted"># the index generations and the invalid releases</span>
GET {data.agents}status</code
		></pre>
</section>

<section>
	<h2><span>2.</span> What it indexes.</h2>
	<p>
		JendHub indexes <b>Base</b> and the latest valid release of every package on <b>BendHub</b>. A
		release is invalid when one of its files does not load. The
		<a href={resolve('/status')}>status</a> page shows the index and the invalid releases.
	</p>
</section>

<style>
	.hero {
		padding: 3.4em 0 1.6em;
		text-align: center;
	}

	h1 {
		margin: 0;
		font-size: 2.9em;
		line-height: 1;
	}

	h1::after {
		content: '';
		display: inline-block;
		width: 0.5em;
		height: 0.9em;
		margin-left: 0.12em;
		vertical-align: -0.08em;
		background: var(--text);
		animation: blink 1.1s steps(1) infinite;
	}

	@keyframes blink {
		50% {
			opacity: 0;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		h1::after {
			animation: none;
		}
	}

	.sub {
		margin: 1.2em 0 0;
	}

	.tag {
		margin: 0.5em 0 0;
		color: var(--muted);
		font-size: 0.87em;
	}

	.tag b {
		color: var(--text);
	}

	.examples {
		margin: 0.8em 0 0;
		font-size: 0.87em;
	}
</style>
