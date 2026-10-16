<script lang="ts">
	import type { ReleaseSummary } from '#lib/api/types.ts';

	let { state }: { state: ReleaseSummary['state'] } = $props();

	const tone = $derived({ indexed: 'good', invalid: 'bad', unindexed: 'neutral' }[state]);
</script>

<span class="badge {tone}">{state}</span>
