<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import { packageHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const shown = $derived(data.status);
	const numbers = new Intl.NumberFormat('en');
	const dates = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeZone: 'UTC' });
</script>

<svelte:head>
	<title>Status · JendHub</title>
</svelte:head>

<h1>Index status</h1>

<h2>Generations</h2>
{#if shown.generations.length === 0}
	<p class="muted">No index is built yet.</p>
{:else}
	<div class="table">
		<table>
			<thead>
				<tr>
					<th>Generation</th>
					<th>State</th>
					<th>Base</th>
					<th class="number">Releases</th>
					<th class="number">Definitions</th>
					<th class="number">Queued</th>
					<th class="number">Running</th>
					<th class="number">Done</th>
					<th class="number">Invalid</th>
					<th class="number">Failed</th>
				</tr>
			</thead>
			<tbody>
				{#each shown.generations as generation (generation.id)}
					<tr>
						<td>{generation.id}</td>
						<td>
							<span class="badge {generation.state === 'live' ? 'good' : 'neutral'}"
								>{generation.state}</span
							>
						</td>
						<td>{generation.base_tag}</td>
						<td class="number">{numbers.format(generation.releases)}</td>
						<td class="number">{numbers.format(generation.definitions)}</td>
						<td class="number">{numbers.format(generation.jobs.queued)}</td>
						<td class="number">{numbers.format(generation.jobs.running)}</td>
						<td class="number">
							{numbers.format(generation.jobs.done + generation.jobs.superseded)}
						</td>
						<td class="number">{numbers.format(generation.jobs.invalid)}</td>
						<td class="number">{numbers.format(generation.jobs.failed)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<h2>Invalid releases <span class="muted">({shown.invalid_releases_total})</span></h2>
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
					<summary>Reason</summary>
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
