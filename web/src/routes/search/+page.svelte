<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import { definitionHref, packageHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const found = $derived(data.found);
</script>

<svelte:head>
	<title>{data.query} · JendHub</title>
	<meta name="robots" content="noindex" />
	<link rel="alternate" type="text/plain" href={data.agents} />
</svelte:head>

<h1 class="sr-only">Results for “{data.query}”</h1>

{#if found.results.length === 0}
	<p>No definition matches “{data.query}”.</p>
{:else}
	<ol class="list">
		{#each found.results as hit, index (hit.id)}
			<li>
				<div class="title">
					<span class="muted">{found.offset + index + 1}.</span>
					<a class="id" href={definitionHref(hit.id)}>{hit.id}</a>
					<span class="muted">{hit.kind}</span>
				</div>
				<div class="muted where">
					{#if hit.package === null}
						Base
					{:else}
						<a href={packageHref(hit.package)}>{hit.package}</a>@{hit.version} · {hit.module}.bend
					{/if}
				</div>
				<pre><code>{hit.head}</code></pre>
				<pre class="import"><code>{hit.import_line}</code></pre>
			</li>
		{/each}
	</ol>
	<Pager
		parameter="offset"
		offset={found.offset}
		shown={found.results.length}
		total={found.total}
		size={data.pageSize}
	/>
{/if}

<style>
	ol {
		margin-top: 1em;
	}

	.title {
		display: flex;
		flex-wrap: wrap;
		gap: 0 1ch;
		align-items: baseline;
		font-weight: 700;
	}

	.title .muted {
		font-weight: 400;
	}

	.where {
		font-size: 0.87em;
		overflow-wrap: anywhere;
	}
</style>
