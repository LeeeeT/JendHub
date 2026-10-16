<script lang="ts">
	import { fileHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const shown = $derived(data.files);
</script>

<svelte:head>
	<title>{shown.release} · JendHub</title>
	<meta name="description" content="The files of the Bend release {shown.release}." />
</svelte:head>

<h1 class="id">{shown.release}</h1>
{#if shown.hash !== null && shown.hash !== shown.release}
	<p class="muted"><code>{shown.hash}</code></p>
{/if}

<section>
	<h2>Files <span>({shown.paths.length})</span></h2>
	<ul class="files">
		{#each shown.paths as path (path)}
			<li><a href={fileHref(`${shown.release}/${path}`)}>{path}</a></li>
		{/each}
	</ul>
</section>

<style>
	.files {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.files li {
		padding: 0.1em 0;
	}
</style>
