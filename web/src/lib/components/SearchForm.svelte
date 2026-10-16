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
		placeholder="a task or a name"
		aria-label="Search Bend definitions"
	/>
	<button type="submit">search</button>
</form>

<style>
	form {
		display: flex;
		width: 100%;
		border: 1px solid var(--line);
		border-radius: 3px;
	}

	input {
		flex: 1;
		min-width: 0;
		padding: 0.4em 1ch;
		color: var(--text);
		background: var(--background);
		border: 0;
		border-radius: 3px 0 0 3px;
	}

	input::placeholder {
		color: var(--muted);
		opacity: 1;
	}

	input:focus-visible {
		outline-offset: -3px;
	}

	button {
		padding: 0.4em 1.6ch;
		font-weight: 700;
		color: var(--background);
		background: var(--text);
		border: 0;
		border-radius: 0 1px 1px 0;
		cursor: pointer;
	}

	button:hover {
		text-decoration: underline;
	}

	.large input,
	.large button {
		padding: 0.6em 1.6ch;
		font-size: 1.1em;
	}
</style>
