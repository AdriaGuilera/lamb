<script>
	import { onMount } from 'svelte';
	import {
		listBenchmarkDatasets,
		runAllCollectionBenchmarks,
		runCollectionBenchmark
	} from '$lib/services/benchmarkService';

	let { kbId = '' } = $props();

	/** @type {any[]} */
	let datasets = $state([]);
	let selectedDatasetId = $state('educational');
	let topK = $state(5);
	let graphDepth = $state(2);
	let threshold = $state(0);
	let loadingDatasets = $state(false);
	let running = $state(false);
	let error = $state('');
	/** @type {any[]} */
	let runs = $state([]);
	let selectedRunIndex = $state(0);
	/** @type {{ id: number, timestamp: string, message: string }[]} */
	let logs = $state([]);
	let logId = 0;

	let selectedDataset = $derived(
		datasets.find((dataset) => dataset.id === selectedDatasetId) || null
	);
	let selectedRun = $derived(runs[selectedRunIndex] || runs[0] || null);

	onMount(() => {
		loadDatasets();
	});

	/**
	 * @param {number | undefined | null} value
	 * @returns {string}
	 */
	function formatPercent(value) {
		return `${(Number(value || 0) * 100).toFixed(1)}%`;
	}

	/**
	 * @param {number | undefined | null} value
	 * @returns {string}
	 */
	function formatDecimal(value) {
		return Number(value || 0).toFixed(3);
	}

	/**
	 * @param {number | undefined | null} value
	 * @returns {string}
	 */
	function formatMs(value) {
		return `${Number(value || 0).toFixed(1)} ms`;
	}

	/**
	 * @param {number | undefined | null} value
	 * @returns {string}
	 */
	function deltaClass(value) {
		const number = Number(value || 0);
		if (number > 0.0001) return 'text-emerald-700 bg-emerald-50 ring-emerald-200';
		if (number < -0.0001) return 'text-red-700 bg-red-50 ring-red-200';
		return 'text-slate-700 bg-slate-50 ring-slate-200';
	}

	/**
	 * @param {string} message
	 */
	function appendLog(message) {
		logs = [
			{
				id: ++logId,
				timestamp: new Date().toLocaleTimeString(),
				message
			},
			...logs
		].slice(0, 40);
	}

	async function loadDatasets() {
		loadingDatasets = true;
		error = '';
		try {
			datasets = await listBenchmarkDatasets();
			if (datasets.length > 0 && !datasets.some((dataset) => dataset.id === selectedDatasetId)) {
				selectDataset(datasets[0]);
			} else if (selectedDataset) {
				topK = selectedDataset.recommended_top_k || topK;
				graphDepth = selectedDataset.recommended_graph_depth || graphDepth;
			}
			appendLog(`Loaded ${datasets.length} benchmark datasets`);
		} catch (err) {
			console.error('Error loading benchmark datasets:', err);
			error = err instanceof Error ? err.message : 'Failed to load benchmark datasets';
		} finally {
			loadingDatasets = false;
		}
	}

	/**
	 * @param {any} dataset
	 */
	function selectDataset(dataset) {
		selectedDatasetId = dataset.id;
		topK = dataset.recommended_top_k || topK;
		graphDepth = dataset.recommended_graph_depth || graphDepth;
	}

	async function runSelectedBenchmark() {
		if (!kbId || !selectedDatasetId) return;
		running = true;
		error = '';
		appendLog(`Running ${selectedDatasetId} benchmark`);
		try {
			const result = await runCollectionBenchmark(kbId, {
				dataset_id: selectedDatasetId,
				top_k: Number(topK),
				graph_depth: Number(graphDepth),
				threshold: Number(threshold)
			});
			runs = [result];
			selectedRunIndex = 0;
			appendLog(
				`${result.dataset_id} complete: Recall ${formatPercent(result.baseline?.recall_at_k)} vs ${formatPercent(result.kg_rag?.recall_at_k)}, graph ${formatMs(result.kg_rag?.avg_graph_ms)}`
			);
		} catch (err) {
			console.error('Error running benchmark:', err);
			error = err instanceof Error ? err.message : 'Failed to run benchmark';
			appendLog(`Benchmark failed: ${error}`);
		} finally {
			running = false;
		}
	}

	async function runAllBenchmarks() {
		if (!kbId) return;
		running = true;
		error = '';
		appendLog('Running all benchmark datasets');
		try {
			const result = await runAllCollectionBenchmarks(kbId, {
				dataset_ids: datasets.map((dataset) => dataset.id),
				threshold: Number(threshold)
			});
			runs = result.runs || [];
			selectedRunIndex = 0;
			appendLog(
				`Run all complete: ${runs.length} datasets, avg MRR delta ${formatDecimal(result.summary?.avg_delta_mrr)}`
			);
		} catch (err) {
			console.error('Error running all benchmarks:', err);
			error = err instanceof Error ? err.message : 'Failed to run all benchmarks';
			appendLog(`Run all failed: ${error}`);
		} finally {
			running = false;
		}
	}
</script>

<div class="space-y-5">
	<div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
		<section class="rounded-lg border border-gray-200 bg-white p-4">
			<div class="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h3 class="text-lg font-semibold text-gray-900">Benchmarks</h3>
					<p class="mt-1 text-sm text-gray-500">Compare vector RAG and KG-RAG retrieval on this collection.</p>
				</div>
				<button
					type="button"
					onclick={loadDatasets}
					disabled={loadingDatasets || running}
					class="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
				>
					Refresh Sets
				</button>
			</div>

			{#if error}
				<div class="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
			{/if}

			<div class="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
				<div class="md:col-span-2">
					<label for="benchmark-dataset" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Dataset</label>
					<select
						id="benchmark-dataset"
						bind:value={selectedDatasetId}
						onchange={() => {
							const dataset = datasets.find((item) => item.id === selectedDatasetId);
							if (dataset) selectDataset(dataset);
						}}
						class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand"
					>
						{#each datasets as dataset (dataset.id)}
							<option value={dataset.id}>{dataset.name}</option>
						{/each}
					</select>
				</div>
				<div>
					<label for="benchmark-top-k" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Top K</label>
					<input id="benchmark-top-k" type="number" min="1" max="50" bind:value={topK} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
				</div>
				<div>
					<label for="benchmark-depth" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Depth</label>
					<input id="benchmark-depth" type="number" min="1" max="4" bind:value={graphDepth} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
				</div>
			</div>

			<div class="mt-3 max-w-xs">
				<label for="benchmark-threshold" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Similarity Threshold</label>
				<input id="benchmark-threshold" type="number" min="0" max="1" step="0.05" bind:value={threshold} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
			</div>

			{#if selectedDataset}
				<div class="mt-4 rounded-md border border-blue-100 bg-blue-50 p-3 text-sm text-blue-800">
					<div class="font-medium">{selectedDataset.question_count} questions</div>
					<div class="mt-1">{selectedDataset.expected_behavior}</div>
				</div>
			{/if}

			<div class="mt-4 flex flex-wrap gap-2">
				<button
					type="button"
					onclick={runSelectedBenchmark}
					disabled={running || loadingDatasets || !selectedDatasetId}
					class="inline-flex items-center rounded-md bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
				>
					{running ? 'Running...' : 'Run Selected'}
				</button>
				<button
					type="button"
					onclick={runAllBenchmarks}
					disabled={running || loadingDatasets || datasets.length === 0}
					class="inline-flex items-center rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
				>
					Run All Tests
				</button>
			</div>
		</section>

		<aside class="rounded-lg border border-gray-200 bg-white p-4">
			<h4 class="text-sm font-semibold text-gray-900">Run Log</h4>
			<div class="mt-3 max-h-72 space-y-2 overflow-y-auto">
				{#if logs.length === 0}
					<p class="text-sm text-gray-500">No benchmark activity yet.</p>
				{:else}
					{#each logs as entry (entry.id)}
						<div class="rounded bg-gray-50 p-2 text-xs text-gray-700">
							<span class="font-medium text-gray-500">{entry.timestamp}</span> {entry.message}
						</div>
					{/each}
				{/if}
			</div>
		</aside>
	</div>

	{#if runs.length > 1}
		<div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
			{#each runs as run, index (run.dataset_id)}
				<button
					type="button"
					onclick={() => { selectedRunIndex = index; }}
					class="rounded-lg border p-3 text-left text-sm {selectedRunIndex === index ? 'border-brand bg-blue-50' : 'border-gray-200 bg-white hover:bg-gray-50'}"
				>
					<div class="font-medium text-gray-900">{run.dataset_id}</div>
					<div class="mt-1 text-gray-500">Recall {formatPercent(run.baseline?.recall_at_k)} vs {formatPercent(run.kg_rag?.recall_at_k)}</div>
					<div class="mt-1 text-gray-500">MRR {formatDecimal(run.baseline?.mrr)} vs {formatDecimal(run.kg_rag?.mrr)}</div>
				</button>
			{/each}
		</div>
	{/if}

	{#if selectedRun}
		<section class="rounded-lg border border-gray-200 bg-white p-4">
			<div class="flex flex-wrap items-start justify-between gap-3">
				<div>
					<h4 class="text-base font-semibold text-gray-900">{selectedRun.dataset_name}</h4>
					<p class="mt-1 text-sm text-gray-500">Top K {selectedRun.top_k}, graph depth {selectedRun.graph_depth}</p>
				</div>
				<div class="flex flex-wrap gap-2 text-xs">
					<span class="rounded-full px-2 py-1 ring-1 {deltaClass(selectedRun.comparison?.delta_recall_at_k)}">Recall delta {formatPercent(selectedRun.comparison?.delta_recall_at_k)}</span>
					<span class="rounded-full px-2 py-1 ring-1 {deltaClass(selectedRun.comparison?.delta_mrr)}">MRR delta {formatDecimal(selectedRun.comparison?.delta_mrr)}</span>
					<span class="rounded-full bg-slate-50 px-2 py-1 text-slate-700 ring-1 ring-slate-200">Overhead {formatMs(selectedRun.comparison?.graph_overhead_ms)}</span>
				</div>
			</div>

			<div class="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
				<div class="rounded-md border border-gray-200 p-3">
					<h5 class="text-sm font-semibold text-gray-900">Vector Baseline</h5>
					<div class="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
						<div><div class="text-xs text-gray-500">Precision</div><div class="font-semibold">{formatPercent(selectedRun.baseline?.precision_at_k)}</div></div>
						<div><div class="text-xs text-gray-500">Recall</div><div class="font-semibold">{formatPercent(selectedRun.baseline?.recall_at_k)}</div></div>
						<div><div class="text-xs text-gray-500">MRR</div><div class="font-semibold">{formatDecimal(selectedRun.baseline?.mrr)}</div></div>
						<div><div class="text-xs text-gray-500">Vector</div><div class="font-semibold">{formatMs(selectedRun.baseline?.avg_vector_ms)}</div></div>
						<div><div class="text-xs text-gray-500">Total</div><div class="font-semibold">{formatMs(selectedRun.baseline?.avg_total_ms)}</div></div>
					</div>
				</div>

				<div class="rounded-md border border-gray-200 p-3">
					<h5 class="text-sm font-semibold text-gray-900">KG-RAG</h5>
					<div class="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-5">
						<div><div class="text-xs text-gray-500">Precision</div><div class="font-semibold">{formatPercent(selectedRun.kg_rag?.precision_at_k)}</div></div>
						<div><div class="text-xs text-gray-500">Recall</div><div class="font-semibold">{formatPercent(selectedRun.kg_rag?.recall_at_k)}</div></div>
						<div><div class="text-xs text-gray-500">MRR</div><div class="font-semibold">{formatDecimal(selectedRun.kg_rag?.mrr)}</div></div>
						<div><div class="text-xs text-gray-500">Graph</div><div class="font-semibold">{formatMs(selectedRun.kg_rag?.avg_graph_ms)}</div></div>
						<div><div class="text-xs text-gray-500">Total</div><div class="font-semibold">{formatMs(selectedRun.kg_rag?.avg_total_ms)}</div></div>
					</div>
				</div>
			</div>

			<div class="mt-4 overflow-x-auto">
				<table class="min-w-full divide-y divide-gray-200 text-sm">
					<thead class="bg-gray-50">
						<tr>
							<th class="px-3 py-2 text-left font-medium text-gray-500">Question</th>
							<th class="px-3 py-2 text-left font-medium text-gray-500">Kind</th>
							<th class="px-3 py-2 text-left font-medium text-gray-500">Relevant</th>
							<th class="px-3 py-2 text-left font-medium text-gray-500">Baseline Files</th>
							<th class="px-3 py-2 text-left font-medium text-gray-500">KG-RAG Files</th>
							<th class="px-3 py-2 text-right font-medium text-gray-500">MRR</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-gray-100 bg-white">
						{#each selectedRun.results || [] as row (row.question_id)}
							<tr>
								<td class="max-w-sm px-3 py-3 text-gray-900">{row.question}</td>
								<td class="px-3 py-3 text-gray-600">{row.kind}</td>
								<td class="px-3 py-3 text-gray-600">{(row.relevant_files || []).join(', ')}</td>
								<td class="px-3 py-3 text-gray-600">{(row.baseline?.retrieved_files || []).join(', ') || 'none'}</td>
								<td class="px-3 py-3 text-gray-600">{(row.kg_rag?.retrieved_files || []).join(', ') || 'none'}</td>
								<td class="px-3 py-3 text-right text-gray-900">{formatDecimal(row.baseline?.mrr)} vs {formatDecimal(row.kg_rag?.mrr)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}
</div>
