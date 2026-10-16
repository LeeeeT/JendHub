<script lang="ts">
	import '../app.css';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import type { Snippet } from 'svelte';
	import SearchForm from '#lib/components/SearchForm.svelte';

	let { children }: { children: Snippet } = $props();

	const query = $derived(page.url.searchParams.get('q') ?? '');
	const home = $derived(page.route.id === '/');
</script>

<header>
	<div class="bar">
		<a class="brand" href={resolve('/')}>JendHub</a>
		{#if !home}
			<div class="search"><SearchForm {query} /></div>
		{/if}
		<nav><a href={resolve('/status')}>Status</a></nav>
	</div>
</header>

<main>
	{@render children()}
</main>

<style>
	header {
		border-bottom: 1px solid var(--border);
		background: var(--surface);
	}

	.bar {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem 1.5rem;
		align-items: center;
		max-width: 64rem;
		margin: 0 auto;
		padding: 0.75rem 1rem;
	}

	.brand {
		font-weight: 700;
		color: var(--text);
	}

	.search {
		flex: 1 1 20rem;
		order: 3;
	}

	nav {
		margin-left: auto;
	}

	@media (min-width: 40rem) {
		.search {
			order: 0;
		}
	}

	main {
		max-width: 64rem;
		margin: 0 auto;
		padding: 1.5rem 1rem 4rem;
	}
</style>
