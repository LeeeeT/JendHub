<script lang="ts">
	import { page } from '$app/state';
	import { pageLinks, withOffset } from '#lib/paging.ts';

	let {
		parameter,
		offset,
		shown,
		total,
		size
	}: { parameter: string; offset: number; shown: number; total: number; size: number } = $props();

	const links = $derived(pageLinks({ offset, shown, total }, size));
</script>

{#if total > shown}
	<nav class="pager" aria-label="Pages">
		{#if links.previous !== null}
			<a href={withOffset(page.url, parameter, links.previous)} rel="prev">← previous</a>
		{/if}
		<span class="muted">{links.first} to {links.last} of {total}</span>
		{#if links.next !== null}
			<a href={withOffset(page.url, parameter, links.next)} rel="next">next →</a>
		{/if}
	</nav>
{/if}

<style>
	.pager {
		display: flex;
		gap: 2ch;
		align-items: baseline;
		margin-top: 1em;
	}
</style>
