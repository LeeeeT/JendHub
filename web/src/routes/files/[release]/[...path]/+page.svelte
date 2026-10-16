<script lang="ts">
	import { sourceLine } from '#lib/imports.ts';
	import { fileHref, releaseFilesHref } from '#lib/paths.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const shown = $derived(data.file);
	const lines = $derived(shown.text.split('\n').map(sourceLine));
</script>

<svelte:head>
	<title>{shown.release}/{shown.path} · JendHub</title>
	<meta name="description" content="The Bend file {shown.path} of {shown.release}." />
	<link rel="alternate" type="application/json" href={data.api} />
</svelte:head>

<h1 class="id">{shown.path}</h1>
<p class="muted">
	<a href={releaseFilesHref(shown.release)}>{shown.release}</a> · {lines.length} lines
</p>

<pre class="source"><code
		>{#each lines as line, index (index)}<span class="line" id="L{index + 1}" data-line={index + 1}
				>{#if line.link === null}{line.text}{:else}{line.link.before}<a
						href={fileHref(line.link.file)}>{line.link.label}</a
					>{line.link.after}{/if}
</span>{/each}</code
	></pre>
