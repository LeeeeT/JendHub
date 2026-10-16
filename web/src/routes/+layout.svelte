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
	<a class="brand" href={resolve('/')}>~/jendhub</a>
	<nav>
		<a href="{resolve('/')}#agents">agents</a>
		<a href={resolve('/status')}>status</a>
		<a href="https://bend-lang.org">bend</a>
	</nav>
</header>

{#if !home}
	<div class="search"><SearchForm {query} /></div>
{/if}

<main>
	{@render children()}
</main>

<style>
	header {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: 0 2ch;
		padding: 1.2em 0 0;
		font-size: 0.87em;
	}

	header a {
		border: 0;
	}

	.brand {
		font-weight: 700;
	}

	nav {
		display: flex;
		gap: 2ch;
	}

	.search {
		margin: 2em 0 0;
	}
</style>
