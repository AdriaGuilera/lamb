<script>
	import { untrack } from 'svelte';
	import {
		auditGraphTrace,
		curateGraphConcept,
		curateGraphRelationship,
		editGraphRelationship,
		getGraphSnapshot,
		listGraphChanges,
		mergeGraphConcepts,
		renameGraphConcept
	} from '$lib/services/graphService';

	let { kbId = '', canModify = false } = $props();

	let conceptFilter = $state('');
	let documentFilter = $state('');
	let includeChunks = $state(true);
	let graphLimit = $state(60);
	let graph = $state({ collection_id: null, nodes: [], edges: [], filters: {}, counts: {} });
	let graphLoading = $state(false);
	let graphError = $state('');

	let selectedNode = $state(null);
	let selectedEdge = $state(null);

	let changes = $state([]);
	let historyLoading = $state(false);
	let historyError = $state('');
	let historyConcept = $state('');
	let historyDocumentId = $state('');
	let historyOperation = $state('');

	let traceSeedInput = $state('');
	let traceDepth = $state(2);
	let traceLimit = $state(20);
	let traceResult = $state(null);
	let traceLoading = $state(false);
	let traceError = $state('');

	let curationMessage = $state('');
	let curationError = $state('');
	let renameName = $state('');
	let mergeSources = $state('');
	let mergeTarget = $state('');
	let conceptNotes = $state('');
	let conceptTags = $state('');
	let conceptVerification = $state('unverified');
	let relationshipRelation = $state('');
	let relationshipNewRelation = $state('');
	let relationshipWeight = $state('');
	let relationshipNotes = $state('');
	let relationshipTags = $state('');
	let relationshipVerification = $state('unverified');
	let loadedKbId = $state('');

	const graphWidth = 900;
	const graphHeight = 520;

	$effect(() => {
		if (kbId && kbId !== loadedKbId) {
			loadedKbId = kbId;
			untrack(() => {
				loadGraph();
				loadHistory();
			});
		}
	});

	function truncate(value, length = 32) {
		const text = String(value || '');
		return text.length > length ? `${text.slice(0, length - 1)}...` : text;
	}

	function formatDate(value) {
		if (!value) return 'N/A';
		try {
			return new Date(value).toLocaleString();
		} catch {
			return value;
		}
	}

	function parseTags(value) {
		return String(value || '')
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function parseSeedChunkIds(value) {
		return String(value || '')
			.split(/[\n,]+/)
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function layoutNodes(nodes) {
		const conceptNodes = nodes.filter((node) => node.type === 'concept');
		const chunkNodes = nodes.filter((node) => node.type === 'chunk');
		const conceptRadius = Math.min(180, 70 + conceptNodes.length * 6);
		const centerX = graphWidth / 2;
		const centerY = graphHeight / 2;

		return nodes.map((node) => {
			if (node.type === 'concept') {
				const index = conceptNodes.findIndex((item) => item.id === node.id);
				const angle = conceptNodes.length <= 1 ? 0 : (index / conceptNodes.length) * Math.PI * 2 - Math.PI / 2;
				return {
					...node,
					x: conceptNodes.length <= 1 ? centerX : centerX + Math.cos(angle) * conceptRadius,
					y: conceptNodes.length <= 1 ? centerY : centerY + Math.sin(angle) * conceptRadius
				};
			}

			const index = chunkNodes.findIndex((item) => item.id === node.id);
			const side = index % 2 === 0 ? 0 : 1;
			const row = Math.floor(index / 2);
			const rows = Math.max(1, Math.ceil(chunkNodes.length / 2));
			const y = 70 + (row * (graphHeight - 140)) / Math.max(1, rows - 1);
			return {
				...node,
				x: side === 0 ? 95 : graphWidth - 95,
				y
			};
		});
	}

	let positionedNodes = $derived(layoutNodes(graph.nodes || []));
	let positionById = $derived(
		Object.fromEntries(positionedNodes.map((node) => [node.id, node]))
	);
	let renderedEdges = $derived(
		(graph.edges || [])
			.map((edge) => ({
				...edge,
				sourceNode: positionById[edge.source],
				targetNode: positionById[edge.target]
			}))
			.filter((edge) => edge.sourceNode && edge.targetNode)
	);
	let selectedConcept = $derived(selectedNode?.type === 'concept' ? selectedNode : null);
	let selectedChunk = $derived(selectedNode?.type === 'chunk' ? selectedNode : null);
	let selectedEditableRelationship = $derived(
		selectedEdge?.type === 'RELATES_TO' ? selectedEdge : null
	);

	function nodeFill(node) {
		if (selectedNode?.id === node.id) return '#1d4ed8';
		if (node.type === 'chunk') return '#f59e0b';
		const state = node.data?.verification_state;
		if (state === 'verified') return '#059669';
		if (state === 'rejected') return '#dc2626';
		return '#2271b3';
	}

	function edgeStroke(edge) {
		if (selectedEdge?.id === edge.id) return '#1d4ed8';
		if (edge.type === 'MENTIONS') return '#d97706';
		if (edge.type === 'CO_OCCURS_WITH') return '#64748b';
		return '#2271b3';
	}

	async function loadGraph() {
		if (!kbId) return;
		graphLoading = true;
		graphError = '';
		try {
			graph = await getGraphSnapshot(kbId, {
				concept: conceptFilter.trim(),
				document_id: documentFilter.trim(),
				include_chunks: includeChunks,
				limit: graphLimit
			});
			selectedNode = null;
			selectedEdge = null;
		} catch (err) {
			console.error('Error loading graph snapshot:', err);
			graphError = err instanceof Error ? err.message : 'Failed to load graph snapshot';
			graph = { collection_id: kbId, nodes: [], edges: [], filters: {}, counts: {} };
		} finally {
			graphLoading = false;
		}
	}

	async function loadHistory(overrides = {}) {
		if (!kbId) return;
		historyLoading = true;
		historyError = '';
		try {
			changes = await listGraphChanges(kbId, {
				concept: overrides.concept ?? historyConcept.trim(),
				document_id: overrides.document_id ?? historyDocumentId.trim(),
				operation: historyOperation.trim(),
				limit: 25
			});
		} catch (err) {
			console.error('Error loading graph history:', err);
			historyError = err instanceof Error ? err.message : 'Failed to load graph history';
			changes = [];
		} finally {
			historyLoading = false;
		}
	}

	function selectNode(node) {
		selectedNode = node;
		selectedEdge = null;
		curationMessage = '';
		curationError = '';

		if (node.type === 'concept') {
			renameName = node.label || node.data?.name || '';
			mergeSources = '';
			mergeTarget = node.data?.name || node.label || '';
			conceptNotes = node.data?.notes || '';
			conceptTags = (node.data?.tags || []).join(', ');
			conceptVerification = node.data?.verification_state || 'unverified';
			historyConcept = node.data?.name || '';
			loadHistory({ concept: historyConcept });
		} else if (node.type === 'chunk') {
			traceSeedInput = node.data?.chunk_id || '';
			historyDocumentId = node.data?.document_id || '';
			loadHistory({ document_id: historyDocumentId });
		}
	}

	function selectEdge(edge) {
		selectedEdge = edge;
		selectedNode = null;
		curationMessage = '';
		curationError = '';
		relationshipRelation = edge.data?.relation || edge.label || '';
		relationshipNewRelation = edge.data?.relation || edge.label || '';
		relationshipWeight = edge.weight ? String(edge.weight) : '';
		relationshipNotes = edge.data?.notes || '';
		relationshipTags = (edge.data?.tags || []).join(', ');
		relationshipVerification = edge.data?.verification_state || 'unverified';
		historyConcept = edge.data?.source || '';
		loadHistory({ concept: historyConcept });
	}

	function activateWithKeyboard(event, callback) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			callback();
		}
	}

	async function runTraceAudit() {
		const seedChunkIds = parseSeedChunkIds(traceSeedInput);
		if (seedChunkIds.length === 0) {
			traceError = 'Add at least one seed chunk ID.';
			return;
		}
		traceLoading = true;
		traceError = '';
		traceResult = null;
		try {
			traceResult = await auditGraphTrace(kbId, {
				seed_chunk_ids: seedChunkIds,
				graph_depth: traceDepth,
				limit: traceLimit
			});
		} catch (err) {
			console.error('Error auditing graph trace:', err);
			traceError = err instanceof Error ? err.message : 'Failed to audit graph trace';
		} finally {
			traceLoading = false;
		}
	}

	async function submitRename() {
		if (!selectedConcept) return;
		curationMessage = '';
		curationError = '';
		try {
			const result = await renameGraphConcept(kbId, selectedConcept.data.name, {
				new_name: renameName,
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'rename'} (${result.event_id || 'no event id'})`;
			await loadGraph();
			await loadHistory();
		} catch (err) {
			console.error('Error renaming concept:', err);
			curationError = err instanceof Error ? err.message : 'Failed to rename concept';
		}
	}

	async function submitMerge() {
		curationMessage = '';
		curationError = '';
		try {
			const result = await mergeGraphConcepts(kbId, {
				source_names: parseTags(mergeSources),
				target_name: mergeTarget,
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'merge'} (${result.event_id || 'no event id'})`;
			await loadGraph();
			await loadHistory();
		} catch (err) {
			console.error('Error merging concepts:', err);
			curationError = err instanceof Error ? err.message : 'Failed to merge concepts';
		}
	}

	async function submitConceptCuration() {
		if (!selectedConcept) return;
		curationMessage = '';
		curationError = '';
		try {
			const result = await curateGraphConcept(kbId, selectedConcept.data.name, {
				notes: conceptNotes,
				tags: parseTags(conceptTags),
				verification_state: conceptVerification,
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'curation'} (${result.event_id || 'no event id'})`;
			await loadGraph();
			await loadHistory();
		} catch (err) {
			console.error('Error curating concept:', err);
			curationError = err instanceof Error ? err.message : 'Failed to update concept curation';
		}
	}

	async function submitRelationshipEdit() {
		if (!selectedEditableRelationship) return;
		curationMessage = '';
		curationError = '';
		try {
			const result = await editGraphRelationship(kbId, {
				source_concept: selectedEditableRelationship.data.source,
				target_concept: selectedEditableRelationship.data.target,
				relation: relationshipRelation,
				new_relation: relationshipNewRelation,
				weight: relationshipWeight === '' ? undefined : Number(relationshipWeight),
				notes: relationshipNotes,
				tags: parseTags(relationshipTags),
				verification_state: relationshipVerification,
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'relationship edit'} (${result.event_id || 'no event id'})`;
			await loadGraph();
			await loadHistory();
		} catch (err) {
			console.error('Error editing relationship:', err);
			curationError = err instanceof Error ? err.message : 'Failed to edit relationship';
		}
	}

	async function submitRelationshipCuration() {
		if (!selectedEditableRelationship) return;
		curationMessage = '';
		curationError = '';
		try {
			const result = await curateGraphRelationship(kbId, {
				source_concept: selectedEditableRelationship.data.source,
				target_concept: selectedEditableRelationship.data.target,
				relation: relationshipRelation,
				notes: relationshipNotes,
				tags: parseTags(relationshipTags),
				verification_state: relationshipVerification,
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'relationship curation'} (${result.event_id || 'no event id'})`;
			await loadGraph();
			await loadHistory();
		} catch (err) {
			console.error('Error curating relationship:', err);
			curationError = err instanceof Error ? err.message : 'Failed to update relationship curation';
		}
	}
</script>

<div class="space-y-5">
	<div class="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
		<div class="grid grid-cols-1 gap-3 md:grid-cols-4">
			<div>
				<label for="graph-concept-filter" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Concept</label>
				<input id="graph-concept-filter" bind:value={conceptFilter} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="concept name" />
			</div>
			<div>
				<label for="graph-document-filter" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Document ID</label>
				<input id="graph-document-filter" bind:value={documentFilter} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="document id" />
			</div>
			<div>
				<label for="graph-limit" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Limit</label>
				<input id="graph-limit" type="number" min="1" max="200" bind:value={graphLimit} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
			</div>
			<label class="mt-6 inline-flex items-center gap-2 text-sm text-gray-700">
				<input type="checkbox" bind:checked={includeChunks} class="rounded border-gray-300 text-brand focus:ring-brand" />
				Chunks
			</label>
		</div>
		<button type="button" onclick={loadGraph} disabled={graphLoading} class="inline-flex items-center justify-center gap-2 rounded-md bg-brand px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-brand-hover disabled:opacity-50">
			<svg class="h-4 w-4 {graphLoading ? 'animate-spin' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v6h6M20 20v-6h-6M20 8A8 8 0 006.3 4.7M4 16a8 8 0 0013.7 3.3" />
			</svg>
			Refresh
		</button>
	</div>

	{#if graphError}
		<div class="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{graphError}</div>
	{/if}

	<div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
		<div class="overflow-hidden rounded-lg border border-gray-200 bg-white">
			<div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-gray-50 px-4 py-3">
				<div class="flex flex-wrap gap-2 text-xs text-gray-600">
					<span class="rounded-full bg-blue-50 px-2 py-1 text-blue-700 ring-1 ring-blue-200">Concepts: {graph.counts?.concepts || 0}</span>
					<span class="rounded-full bg-amber-50 px-2 py-1 text-amber-700 ring-1 ring-amber-200">Chunks: {graph.counts?.chunks || 0}</span>
					<span class="rounded-full bg-slate-50 px-2 py-1 text-slate-700 ring-1 ring-slate-200">Edges: {graph.counts?.edges || 0}</span>
				</div>
				<div class="flex flex-wrap gap-3 text-xs text-gray-500">
					<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-brand"></span>Concept</span>
					<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-amber-500"></span>Chunk</span>
					<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-emerald-600"></span>Verified</span>
				</div>
			</div>

			<div class="relative min-h-130 bg-white">
				{#if graphLoading}
					<div class="absolute inset-0 z-10 flex items-center justify-center bg-white/70 text-sm text-gray-600">Loading graph...</div>
				{/if}

				{#if positionedNodes.length === 0 && !graphLoading}
					<div class="flex min-h-130 items-center justify-center text-sm text-gray-500">No graph data available.</div>
				{:else}
					<svg class="h-130 w-full" viewBox={`0 0 ${graphWidth} ${graphHeight}`} role="img" aria-label="Knowledge graph visualization">
						<defs>
							<marker id="graph-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
								<path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"></path>
							</marker>
						</defs>

						{#each renderedEdges as edge (edge.id)}
							<g>
								<line
									x1={edge.sourceNode.x}
									y1={edge.sourceNode.y}
									x2={edge.targetNode.x}
									y2={edge.targetNode.y}
									stroke={edgeStroke(edge)}
									stroke-width={selectedEdge?.id === edge.id ? 4 : edge.type === 'MENTIONS' ? 1.5 : 2.5}
									stroke-opacity={edge.type === 'MENTIONS' ? 0.45 : 0.75}
									stroke-dasharray={edge.type === 'CO_OCCURS_WITH' ? '6 5' : edge.type === 'MENTIONS' ? '3 4' : ''}
									marker-end={edge.type === 'RELATES_TO' ? 'url(#graph-arrow)' : undefined}
									class="cursor-pointer"
									role="button"
									tabindex="0"
									aria-label={`Select ${edge.label || edge.type} edge`}
									onclick={() => selectEdge(edge)}
									onkeydown={(event) => activateWithKeyboard(event, () => selectEdge(edge))}
								/>
								{#if edge.type !== 'MENTIONS'}
									<text x={(edge.sourceNode.x + edge.targetNode.x) / 2} y={(edge.sourceNode.y + edge.targetNode.y) / 2 - 6} text-anchor="middle" class="pointer-events-none select-none fill-slate-600 text-[10px]">
										{truncate(edge.label, 18)}
									</text>
								{/if}
							</g>
						{/each}

						{#each positionedNodes as node (node.id)}
							<g
								transform={`translate(${node.x}, ${node.y})`}
								class="cursor-pointer"
								role="button"
								tabindex="0"
								aria-label={`Select ${node.label} ${node.type}`}
								onclick={() => selectNode(node)}
								onkeydown={(event) => activateWithKeyboard(event, () => selectNode(node))}
							>
								<circle r={node.type === 'chunk' ? 18 : 25} fill={nodeFill(node)} stroke="#ffffff" stroke-width="3"></circle>
								{#if node.type === 'concept'}
									<text text-anchor="middle" y="4" class="pointer-events-none fill-white text-[10px] font-semibold">{node.data?.chunk_count || 0}</text>
								{:else}
									<rect x="-8" y="-9" width="16" height="18" rx="2" fill="white" opacity="0.9"></rect>
									<line x1="-5" y1="-3" x2="5" y2="-3" stroke="#f59e0b" stroke-width="1"></line>
									<line x1="-5" y1="2" x2="5" y2="2" stroke="#f59e0b" stroke-width="1"></line>
								{/if}
								<text text-anchor="middle" y={node.type === 'chunk' ? 34 : 42} class="pointer-events-none select-none fill-gray-700 text-[11px] font-medium">
									{truncate(node.label, 24)}
								</text>
							</g>
						{/each}
					</svg>
				{/if}
			</div>
		</div>

		<aside class="space-y-4">
			<div class="rounded-lg border border-gray-200 bg-white p-4">
				<h4 class="text-sm font-semibold text-gray-900">Selection</h4>
				{#if selectedConcept}
					<div class="mt-3 space-y-2 text-sm text-gray-700">
						<div class="font-medium text-brand">{selectedConcept.label}</div>
						<div>Name: {selectedConcept.data.name}</div>
						<div>Chunks: {selectedConcept.data.chunk_count || 0}</div>
						<div>State: {selectedConcept.data.verification_state || 'unverified'}</div>
						{#if selectedConcept.data.tags?.length}
							<div>Tags: {selectedConcept.data.tags.join(', ')}</div>
						{/if}
					</div>
				{:else if selectedChunk}
					<div class="mt-3 space-y-2 text-sm text-gray-700">
						<div class="font-medium text-amber-700">{selectedChunk.label}</div>
						<div class="break-all">Chunk: {selectedChunk.data.chunk_id}</div>
						<div>{selectedChunk.data.filename || 'No filename'}</div>
						<p class="rounded bg-gray-50 p-2 text-xs text-gray-600">{selectedChunk.data.text_preview || 'No preview'}</p>
					</div>
				{:else if selectedEdge}
					<div class="mt-3 space-y-2 text-sm text-gray-700">
						<div class="font-medium text-brand">{selectedEdge.label || selectedEdge.type}</div>
						<div>{selectedEdge.data?.source || selectedEdge.source} -> {selectedEdge.data?.target || selectedEdge.target}</div>
						<div>Weight: {selectedEdge.weight ?? 'N/A'}</div>
						<div>State: {selectedEdge.data?.verification_state || 'unverified'}</div>
					</div>
				{:else}
					<p class="mt-3 text-sm text-gray-500">Select a node or edge.</p>
				{/if}
			</div>

			{#if canModify}
				<div class="rounded-lg border border-gray-200 bg-white p-4">
					<h4 class="text-sm font-semibold text-gray-900">Curation</h4>
					{#if curationMessage}
						<div class="mt-3 rounded border border-green-200 bg-green-50 p-2 text-xs text-green-700">{curationMessage}</div>
					{/if}
					{#if curationError}
						<div class="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{curationError}</div>
					{/if}

					{#if selectedConcept}
						<form class="mt-3 space-y-3" onsubmit={(event) => { event.preventDefault(); submitRename(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500" for="rename-concept">Rename</label>
							<div class="flex gap-2">
								<input id="rename-concept" bind:value={renameName} class="block min-w-0 flex-1 rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
								<button type="submit" class="inline-flex items-center rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover">Save</button>
							</div>
						</form>

						<form class="mt-4 space-y-3" onsubmit={(event) => { event.preventDefault(); submitConceptCuration(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500" for="concept-notes">Notes</label>
							<textarea id="concept-notes" bind:value={conceptNotes} rows="3" class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand"></textarea>
							<input bind:value={conceptTags} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="tags, comma separated" />
							<select bind:value={conceptVerification} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand">
								<option value="unverified">unverified</option>
								<option value="verified">verified</option>
								<option value="needs_review">needs_review</option>
								<option value="rejected">rejected</option>
							</select>
							<button type="submit" class="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">Update</button>
						</form>

						<form class="mt-4 space-y-3 border-t border-gray-200 pt-4" onsubmit={(event) => { event.preventDefault(); submitMerge(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500" for="merge-sources">Merge Sources</label>
							<input id="merge-sources" bind:value={mergeSources} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="source one, source two" />
							<input bind:value={mergeTarget} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="target concept" />
							<button type="submit" class="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">Merge</button>
						</form>
					{:else if selectedEditableRelationship}
						<form class="mt-3 space-y-3" onsubmit={(event) => { event.preventDefault(); submitRelationshipEdit(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500" for="relationship-relation">Relationship</label>
							<input id="relationship-relation" bind:value={relationshipNewRelation} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
							<input type="number" step="0.1" bind:value={relationshipWeight} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="weight" />
							<textarea bind:value={relationshipNotes} rows="3" class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="notes"></textarea>
							<input bind:value={relationshipTags} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="tags, comma separated" />
							<select bind:value={relationshipVerification} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand">
								<option value="unverified">unverified</option>
								<option value="verified">verified</option>
								<option value="needs_review">needs_review</option>
								<option value="rejected">rejected</option>
							</select>
							<div class="flex gap-2">
								<button type="submit" class="inline-flex items-center rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover">Save</button>
								<button type="button" onclick={submitRelationshipCuration} class="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">Curate</button>
							</div>
						</form>
					{:else}
						<p class="mt-3 text-sm text-gray-500">Select a concept or RELATES_TO edge.</p>
					{/if}
				</div>
			{/if}
		</aside>
	</div>

	<div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
		<section class="rounded-lg border border-gray-200 bg-white p-4">
			<div class="flex flex-wrap items-end justify-between gap-3">
				<div>
					<h4 class="text-sm font-semibold text-gray-900">History</h4>
					<div class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
						<input bind:value={historyConcept} class="rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="concept" />
						<input bind:value={historyDocumentId} class="rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="document id" />
						<input bind:value={historyOperation} class="rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="operation" />
					</div>
				</div>
				<button type="button" onclick={() => loadHistory()} disabled={historyLoading} class="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">Load</button>
			</div>
			{#if historyError}
				<div class="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{historyError}</div>
			{/if}
			<div class="mt-4 max-h-72 overflow-y-auto divide-y divide-gray-100">
				{#if historyLoading}
					<div class="py-4 text-sm text-gray-500">Loading history...</div>
				{:else if changes.length === 0}
					<div class="py-4 text-sm text-gray-500">No changes found.</div>
				{:else}
					{#each changes as change (change.event_id)}
						<div class="py-3 text-sm">
							<div class="flex items-center justify-between gap-3">
								<span class="font-medium text-gray-900">{change.operation}</span>
								<span class="text-xs text-gray-500">{formatDate(change.timestamp)}</span>
							</div>
							<div class="mt-1 text-xs text-gray-500">{change.actor || 'unknown'} · {change.filename || change.document_id || 'graph'}</div>
							{#if change.concepts?.length}
								<div class="mt-2 flex flex-wrap gap-1">
									{#each change.concepts.slice(0, 6) as concept}
										<span class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">{concept}</span>
									{/each}
								</div>
							{/if}
						</div>
					{/each}
				{/if}
			</div>
		</section>

		<section class="rounded-lg border border-gray-200 bg-white p-4">
			<h4 class="text-sm font-semibold text-gray-900">Trace Audit</h4>
			<div class="mt-3 space-y-3">
				<textarea bind:value={traceSeedInput} rows="3" class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="seed chunk ids"></textarea>
				<div class="grid grid-cols-2 gap-2">
					<input type="number" min="1" max="4" bind:value={traceDepth} class="rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
					<input type="number" min="1" max="100" bind:value={traceLimit} class="rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
				</div>
				<button type="button" onclick={runTraceAudit} disabled={traceLoading} class="inline-flex items-center rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50">Run Trace</button>
			</div>
			{#if traceError}
				<div class="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{traceError}</div>
			{/if}
			{#if traceResult}
				<div class="mt-4 space-y-3 text-sm text-gray-700">
					<div>Entry concepts: {(traceResult.trace?.entry_concepts || []).join(', ') || 'none'}</div>
					<div>Expanded chunks: {(traceResult.trace?.expanded_chunk_ids || []).length}</div>
					<div>Edges traversed: {(traceResult.trace?.traversed_edges || []).length}</div>
					<details class="text-xs">
						<summary class="cursor-pointer text-gray-500 hover:text-gray-700">Raw trace</summary>
						<pre class="mt-2 max-h-56 overflow-auto rounded bg-gray-50 p-2 text-xs">{JSON.stringify(traceResult.trace, null, 2)}</pre>
					</details>
				</div>
			{/if}
		</section>
	</div>
</div>
