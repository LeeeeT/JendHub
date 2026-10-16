<script lang="ts">
	import { definitionHref, packageHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>{data.query} · JendHub</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<h1 class="sr-only">Results for “{data.query}”</h1>

{#if data.results.length === 0}
	<p>No definition matches “{data.query}”.</p>
{:else}
	<ol class="list">
		{#each data.results as hit (hit.id)}
			<li>
				<div class="title">
					<a class="id" href={definitionHref(hit.id)}>{hit.id}</a>
					<span class="badge neutral">{hit.kind}</span>
				</div>
				<div class="muted where">
					{#if hit.package === null}
						Base
					{:else}
						<a href={packageHref(hit.package)}>{hit.package}</a>@{hit.version} · {hit.module}.bend
					{/if}
				</div>
				<pre><code>{hit.head}</code></pre>
			</li>
		{/each}
	</ol>
{/if}

<style>
	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	.title {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		align-items: baseline;
	}

	.where {
		font-size: 0.875rem;
		overflow-wrap: anywhere;
	}
</style>
