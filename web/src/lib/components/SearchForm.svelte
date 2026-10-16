<script lang="ts">
	import { resolve } from '$app/paths';

	let { query = '', large = false }: { query?: string; large?: boolean } = $props();
</script>

<form method="GET" action={resolve('/search')} role="search" class:large>
	<input
		type="search"
		name="q"
		value={query}
		maxlength="1000"
		required
		placeholder="Describe a task, or type a name"
		aria-label="Search Bend definitions"
	/>
	<button type="submit">Search</button>
</form>

<style>
	form {
		display: flex;
		gap: 0.5rem;
		width: 100%;
	}

	input {
		flex: 1;
		min-width: 0;
		padding: 0.5rem 0.75rem;
		font: inherit;
		color: var(--text);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 6px;
	}

	input:focus {
		outline: 2px solid var(--accent);
		outline-offset: -1px;
	}

	button {
		padding: 0.5rem 1rem;
		font: inherit;
		color: var(--background);
		background: var(--text);
		border: none;
		border-radius: 6px;
		cursor: pointer;
	}

	.large input,
	.large button {
		padding: 0.75rem 1rem;
		font-size: 1.0625rem;
	}
</style>
