<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import { packageHref } from '#lib/paths.ts';
	import type { GenerationSummary } from '#lib/search/types.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const shown = $derived(data.status);
	const numbers = new Intl.NumberFormat('en');
	const dates = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeZone: 'UTC' });
	const counts: [string, (generation: GenerationSummary) => number][] = [
		['Releases', (generation) => generation.releases],
		['Definitions', (generation) => generation.definitions],
		['Queued', (generation) => generation.jobs.queued],
		['Running', (generation) => generation.jobs.running],
		['Done', (generation) => generation.jobs.done + generation.jobs.superseded],
		['Invalid', (generation) => generation.jobs.invalid],
		['Failed', (generation) => generation.jobs.failed]
	];
</script>

<svelte:head>
	<title>Status · JendHub</title>
</svelte:head>

<h1>Index status</h1>

<section>
	<h2>Generations</h2>
	{#if shown.generations.length === 0}
		<p class="muted">No index is built yet.</p>
	{:else}
		<div class="table">
			<table>
				<thead>
					<tr>
						<th>Generation</th>
						{#each shown.generations as generation (generation.id)}
							<th class="number">
								{generation.id}
								<span class={generation.state === 'live' ? 'live' : 'muted'}
									>{generation.state}</span
								>
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					<tr>
						<td>Base</td>
						{#each shown.generations as generation (generation.id)}
							<td class="number">{generation.base_tag}</td>
						{/each}
					</tr>
					<tr>
						<td>Vectors</td>
						{#each shown.generations as generation (generation.id)}
							<td class="number">{generation.vectors}</td>
						{/each}
					</tr>
					{#each counts as [label, count] (label)}
						<tr>
							<td>{label}</td>
							{#each shown.generations as generation (generation.id)}
								<td class="number">{numbers.format(count(generation))}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>

<section>
	<h2>Invalid releases <span>({shown.invalid_releases_total})</span></h2>
	<p class="muted">
		A release is invalid when one of its files does not load with Base. JendHub never indexes an
		invalid release, and search uses the latest valid release of the same package.
	</p>
	{#if shown.invalid_releases_total > 0}
		<ul class="list">
			{#each shown.invalid_releases as release (release.hash)}
				<li>
					{#if release.name === null}
						<code>{release.hash}</code>
					{:else}
						<a class="id" href={packageHref(release.name)}>{release.name}</a>@{release.version}
					{/if}
					<span class="muted"
						>· Base {release.base_tag} · {dates.format(new Date(release.found_at))}</span
					>
					<details>
						<summary>reason</summary>
						<pre>{release.reason}</pre>
					</details>
				</li>
			{/each}
		</ul>
		<Pager
			parameter="invalid_releases_offset"
			offset={shown.invalid_releases_offset}
			shown={shown.invalid_releases.length}
			total={shown.invalid_releases_total}
			size={data.pageSize}
		/>
	{/if}
</section>

<style>
	th span {
		font-weight: 400;
	}

	.live {
		font-weight: 700;
	}

	.list a {
		font-weight: 700;
	}
</style>
