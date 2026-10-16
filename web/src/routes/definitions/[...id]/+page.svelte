<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import { definitionHref, packageHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const shown = $derived(data.definition);
	const lines = $derived(shown.text.split('\n'));
</script>

<svelte:head>
	<title>{shown.id} · JendHub</title>
	<meta
		name="description"
		content="The Bend {shown.kind} {shown.id}, what it uses and what uses it."
	/>
</svelte:head>

<h1 class="id">{shown.id}</h1>
<p class="muted where">
	<span class="badge neutral">{shown.kind}</span>
	{#if shown.package === null}
		Base
	{:else}
		<a href={packageHref(shown.package)}>{shown.package}</a>@{shown.version} · {shown.module}.bend
	{/if}
	· lines {shown.first_line + 1} to {shown.end_line}
</p>

<pre class="source"><code
		>{#each lines as line, index (index)}<span class="line" data-line={shown.first_line + 1 + index}
				>{line}
</span>{/each}</code
	></pre>

<h2>Uses <span class="muted">({shown.uses.length})</span></h2>
{#if shown.uses.length === 0}
	<p class="muted">This definition uses no other definition.</p>
{:else}
	<ul class="ids">
		{#each shown.uses as id (id)}
			<li><a class="id" href={definitionHref(id)}>{id}</a></li>
		{/each}
	</ul>
{/if}

<h2>Used by <span class="muted">({shown.used_by_total})</span></h2>
{#if shown.used_by_total === 0}
	<p class="muted">No indexed definition uses this definition.</p>
{:else}
	<ul class="ids">
		{#each shown.used_by as id (id)}
			<li><a class="id" href={definitionHref(id)}>{id}</a></li>
		{/each}
	</ul>
	<Pager
		parameter="used_by_offset"
		offset={shown.used_by_offset}
		shown={shown.used_by.length}
		total={shown.used_by_total}
		size={data.pageSize}
	/>
{/if}

<style>
	.where {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 0.5rem;
		align-items: baseline;
		overflow-wrap: anywhere;
	}

	.source code {
		display: block;
	}

	.line {
		display: block;
		white-space: pre;
	}

	.line::before {
		content: attr(data-line);
		display: inline-block;
		min-width: 4ch;
		margin-right: 2ch;
		text-align: right;
		color: var(--muted);
		user-select: none;
	}

	.ids {
		margin: 0;
		padding-left: 1.25rem;
	}

	.ids li {
		padding: 0.125rem 0;
	}
</style>
