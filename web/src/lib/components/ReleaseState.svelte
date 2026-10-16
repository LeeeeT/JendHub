<script lang="ts">
	import type { ReleaseSummary } from '#lib/api/types.ts';

	let { state }: { state: ReleaseSummary['state'] } = $props();
</script>

<span class={state}>{state}</span>

<style>
	.indexed {
		font-weight: 700;
	}

	.invalid {
		padding: 0 0.6ch;
		color: var(--background);
		background: var(--text);
	}

	.unindexed {
		color: var(--muted);
	}
</style>
