<script lang="ts">
	import SearchForm from '#lib/components/SearchForm.svelte';
</script>

<svelte:head>
	<title>JendHub · Search Bend definitions</title>
	<meta
		name="description"
		content="Search the definitions of Base and of every package on BendHub by task or by name."
	/>
</svelte:head>

<section class="hero">
	<h1>Search Bend definitions</h1>
	<p class="muted">
		Base and the latest valid release of every package on BendHub. Describe what you need to do, or
		type the name of a definition.
	</p>
	<SearchForm large />
</section>

<section>
	<h2>For agents</h2>
	<p>
		The JendHub API gives the same data in JSON, or in compact text with <code
			>Accept: text/plain</code
		>.
	</p>
	<div class="table">
		<table>
			<tbody>
				<tr
					><td><code>GET /v1/search?q=&lt;query&gt;&amp;limit=10</code></td><td
						>The best definitions for a query.</td
					></tr
				>
				<tr
					><td><code>GET /v1/definitions/&lt;id&gt;</code></td><td
						>The text of a definition, what it uses and what uses it.</td
					></tr
				>
				<tr
					><td><code>GET /v1/packages/&lt;name&gt;</code></td><td
						>The releases of a package and the definitions of its indexed release.</td
					></tr
				>
				<tr
					><td><code>GET /v1/status</code></td><td
						>The index generations and the invalid releases.</td
					></tr
				>
			</tbody>
		</table>
	</div>
</section>

<style>
	.hero {
		padding: 3rem 0 1rem;
	}

	.hero h1 {
		font-size: 2rem;
	}

	.hero p {
		max-width: 40rem;
		margin-bottom: 1.5rem;
	}

	td:first-child {
		white-space: nowrap;
	}
</style>
