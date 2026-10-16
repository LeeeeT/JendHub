<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import { definitionHref, fileHref, packageHref } from '#lib/paths.ts';
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
	<link rel="alternate" type="text/plain" href={data.agents} />
</svelte:head>

<h1 class="id">{shown.id}</h1>
<p class="muted where">
	{shown.kind} ·
	{#if shown.package === null}
		Base
	{:else}
		<a href={packageHref(shown.package)}>{shown.package}</a>@{shown.version} · {shown.module}.bend
	{/if}
	·
	<a href="{fileHref(shown.file)}#L{shown.first_line + 1}"
		>lines {shown.first_line + 1} to {shown.end_line}</a
	>
</p>

<pre class="import"><code>{shown.import_line}</code></pre>
<p class="muted use">Then call <code>{shown.call_name}</code>.</p>

<pre class="source"><code
		>{#each shown.imports as line (line)}<span class="line"
				>{line}
</span>{/each}{#if shown.imports.length > 0}<span class="line"
			></span>{/if}{#each lines as line, index (index)}<span
				class="line"
				data-line={shown.first_line + 1 + index}
				>{line}
</span>{/each}</code
	></pre>

<section>
	<h2>Uses <span>({shown.uses.length})</span></h2>
	{#if shown.uses.length === 0}
		<p class="muted">This definition uses no other definition.</p>
	{:else}
		<ul class="ids">
			{#each shown.uses as id (id)}
				<li><a class="id" href={definitionHref(id)}>{id}</a></li>
			{/each}
		</ul>
	{/if}
</section>

<section>
	<h2>Used by <span>({shown.used_by_total})</span></h2>
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
</section>

<style>
	.where {
		overflow-wrap: anywhere;
	}

	.use {
		margin: 0.4em 0 0;
		font-size: 0.87em;
	}

	.use code {
		color: var(--text);
	}

	.ids {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.ids li {
		padding: 0.1em 0;
	}

	.ids li::before {
		content: '- ';
		color: var(--muted);
	}
</style>
