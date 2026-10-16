<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import ReleaseState from '#lib/components/ReleaseState.svelte';
	import { definitionHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const shown = $derived(data.package);
	const indexed = $derived(shown.releases.find((release) => release.state === 'indexed'));
</script>

<svelte:head>
	<title>{shown.name} · JendHub</title>
	<meta
		name="description"
		content="The releases and definitions of the Bend package {shown.name}."
	/>
</svelte:head>

<h1 class="id">{shown.name}</h1>
<p class="muted">
	{#if indexed === undefined}
		No release of this package is in the index.
	{:else}
		Search uses release {indexed.version}.
	{/if}
</p>

<h2>Releases <span class="muted">({shown.releases.length})</span></h2>
<div class="table">
	<table>
		<thead>
			<tr><th>Version</th><th>State</th><th>Hash</th></tr>
		</thead>
		<tbody>
			{#each shown.releases as release (release.hash)}
				<tr>
					<td>{release.version}</td>
					<td>
						<ReleaseState state={release.state} />
						{#if release.reason !== null}
							<details>
								<summary>Reason</summary>
								<pre>{release.reason}</pre>
							</details>
						{/if}
					</td>
					<td><code>{release.hash}</code></td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<h2>Definitions <span class="muted">({shown.definitions_total})</span></h2>
{#if shown.definitions_total === 0}
	<p class="muted">The index has no definition of this package.</p>
{:else}
	<ul class="list">
		{#each shown.definitions as definition (definition.id)}
			<li>
				<a class="id" href={definitionHref(definition.id)}>{definition.id}</a>
				<span class="badge neutral">{definition.kind}</span>
				<pre><code>{definition.head}</code></pre>
			</li>
		{/each}
	</ul>
	<Pager
		parameter="definitions_offset"
		offset={shown.definitions_offset}
		shown={shown.definitions.length}
		total={shown.definitions_total}
		size={data.pageSize}
	/>
{/if}
