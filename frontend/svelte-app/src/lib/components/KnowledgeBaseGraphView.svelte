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

	/** @typedef {{ [key: string]: any }} GraphData */
	/** @typedef {{ id: string, type: string, label: string, data: GraphData, x?: number, y?: number }} GraphNode */
	/** @typedef {GraphNode & { x: number, y: number }} PositionedGraphNode */
	/** @typedef {{ id: string, type: string, source: string, target: string, label?: string, weight?: number, data: GraphData }} GraphEdge */
	/** @typedef {GraphEdge & { sourceNode: PositionedGraphNode, targetNode: PositionedGraphNode }} RenderedGraphEdge */
	/** @typedef {{ concepts?: number, documents?: number, chunks?: number, edges?: number }} GraphCounts */
	/** @typedef {{ collection_id: string | number | null, nodes: GraphNode[], edges: GraphEdge[], filters: GraphData, counts: GraphCounts }} GraphSnapshot */
	/** @typedef {{ value: string, label: string, node: GraphNode }} GraphOption */
	/** @typedef {{ concept?: string, document_id?: string }} HistoryOverrides */
	/** @typedef {{ event_id: string, operation?: string, actor?: string, timestamp?: string, filename?: string, document_id?: string, concepts?: string[] }} GraphChange */
	/** @typedef {{ trace?: { entry_concepts?: string[], expanded_chunk_ids?: string[], traversed_edges?: GraphData[] } }} GraphTraceResult */

	let { kbId = '', canModify = false } = $props();

	let conceptFilter = $state('');
	let documentFilter = $state('');
	let chunkFilter = $state('');
	let selectedDocumentValues = $state(/** @type {string[]} */ ([]));
	let selectedChunkValues = $state(/** @type {string[]} */ ([]));
	let onlyConcepts = $state(false);
	let graphLimit = $state(60);
	let graph = $state(/** @type {GraphSnapshot} */ ({ collection_id: null, nodes: [], edges: [], filters: {}, counts: {} }));
	let optionNodes = $state(/** @type {GraphNode[]} */ ([]));
	let conceptSearchActive = $state(false);
	let documentSearchActive = $state(false);
	let chunkSearchActive = $state(false);
	let graphLoading = $state(false);
	let graphError = $state('');

	let selectedNode = $state(/** @type {GraphNode | null} */ (null));
	let selectedEdge = $state(/** @type {GraphEdge | null} */ (null));

	let changes = $state(/** @type {GraphChange[]} */ ([]));
	let historyLoading = $state(false);
	let historyError = $state('');
	let historyConcept = $state('');
	let historyDocumentId = $state('');
	let historyOperation = $state('');

	let traceSeedInput = $state('');
	let traceDepth = $state(2);
	let traceLimit = $state(20);
	let traceResult = $state(/** @type {GraphTraceResult | null} */ (null));
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
	const graphCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
	let filteredGraph = $derived.by(() => filterGraph(graph));
	let visibleGraph = $derived.by(() => (onlyConcepts ? conceptsOnlyGraph(filteredGraph) : filteredGraph));
	let graphHeight = $derived.by(() => {
		const nodes = visibleGraph.nodes || [];
		const conceptCount = nodes.filter((node) => node.type === 'concept').length;
		const documentCount = nodes.filter((node) => node.type === 'document').length;
		const chunkCount = nodes.filter((node) => node.type === 'chunk').length;
		const conceptRows = Math.ceil(conceptCount / conceptColumnCount(conceptCount));
		const rows = Math.max(6, documentCount, chunkCount, conceptRows);
		return Math.min(1800, Math.max(560, 130 + rows * 58));
	});

	$effect(() => {
		if (kbId && kbId !== loadedKbId) {
			loadedKbId = kbId;
			conceptFilter = '';
			documentFilter = '';
			chunkFilter = '';
			selectedDocumentValues = [];
			selectedChunkValues = [];
			optionNodes = [];
			untrack(() => {
				loadGraph();
				loadHistory();
			});
		}
	});

	/** @type {GraphOption[]} */
	let conceptOptions = $derived(buildNodeOptions(optionNodes, 'concept'));
	/** @type {GraphOption[]} */
	let documentOptions = $derived(buildNodeOptions(optionNodes, 'document'));
	/** @type {GraphOption[]} */
	let chunkOptions = $derived(buildNodeOptions(optionNodes, 'chunk'));
	let selectedDocumentSet = $derived.by(() => new Set(selectedDocumentValues));
	let selectedChunkSet = $derived.by(() => new Set(selectedChunkValues));
	/** @type {GraphOption[]} */
	let scopedChunkOptions = $derived.by(() => {
		if (selectedDocumentValues.length === 0) return chunkOptions;
		return chunkOptions.filter((option) => selectedDocumentSet.has(String(option.node.data?.document_id || '')));
	});
	/** @type {GraphOption[]} */
	let conceptSuggestions = $derived(filterOptions(conceptOptions, conceptFilter, [], 8));
	/** @type {GraphOption[]} */
	let documentSuggestions = $derived(filterOptions(documentOptions, documentFilter, selectedDocumentValues, 8));
	/** @type {GraphOption[]} */
	let chunkSuggestions = $derived(filterOptions(scopedChunkOptions, chunkFilter, selectedChunkValues, 8));
	let hasGraphFilters = $derived(Boolean(conceptFilter.trim() || selectedDocumentValues.length || selectedChunkValues.length));

	/**
	 * @param {unknown} value
	 * @param {number} [length]
	 */
	function truncate(value, length = 32) {
		const text = String(value || '');
		return text.length > length ? `${text.slice(0, length - 1)}...` : text;
	}

	/** @param {unknown} value */
	function formatDate(value) {
		if (!value) return 'N/A';
		try {
			const dateValue = value instanceof Date || typeof value === 'number' || typeof value === 'string' ? value : String(value);
			return new Date(dateValue).toLocaleString();
		} catch {
			return value;
		}
	}

	/** @param {unknown} value */
	function parseTags(value) {
		return String(value || '')
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	/** @param {unknown} value */
	function parseSeedChunkIds(value) {
		return String(value || '')
			.split(/[\n,]+/)
			.map((item) => item.trim())
			.filter(Boolean);
	}

	/** @param {GraphSnapshot} snapshot */
	function rememberGraphOptions(snapshot) {
		const byId = new Map(optionNodes.map((node) => [node.id, node]));
		for (const node of snapshot.nodes || []) {
			if (node.type === 'concept' || node.type === 'document' || node.type === 'chunk') {
				byId.set(node.id, node);
			}
		}
		optionNodes = Array.from(byId.values());
	}

	/**
	 * @param {GraphNode[]} nodes
	 * @param {string} type
	 * @returns {GraphOption[]}
	 */
	function buildNodeOptions(nodes, type) {
		const byValue = new Map();
		for (const node of nodes.filter((item) => item.type === type).sort(compareNodesByLabel)) {
			const value = optionValue(node);
			if (!value || byValue.has(value)) continue;
			byValue.set(value, { value, label: optionLabel(node), node });
		}
		return Array.from(byValue.values());
	}

	/** @param {GraphNode} node */
	function optionValue(node) {
		return nodeSelectionValue(node);
	}

	/** @param {GraphNode} node */
	function optionLabel(node) {
		if (node.type === 'chunk') {
			const label = String(node.data?.source_label || node.label || node.data?.chunk_id || node.id || '');
			return node.data?.filename ? `${label} (${node.data.filename})` : label;
		}
		if (node.type === 'document') return String(node.data?.filename || node.label || node.data?.document_id || node.id);
		return String(node.data?.name || node.label || node.id);
	}

	/** @param {GraphNode} node */
	function nodeSelectionValue(node) {
		if (node.type === 'document') return String(node.data?.document_id || node.id || '');
		if (node.type === 'chunk') return String(node.data?.chunk_id || node.id || '');
		return String(node.data?.name || node.label || node.id || '');
	}

	/** @param {unknown} value */
	function normalizeSearch(value) {
		return String(value || '').trim().toLowerCase();
	}

	/**
	 * @param {GraphOption} option
	 * @param {string} needle
	 */
	function optionMatches(option, needle) {
		if (!needle) return true;
		return normalizeSearch([
			option.value,
			option.label,
			option.node.id,
			option.node.data?.filename,
			option.node.data?.document_id,
			option.node.data?.chunk_id,
			option.node.data?.source_label,
			option.node.data?.name
		].join(' ')).includes(needle);
	}

	/**
	 * @param {GraphOption[]} options
	 * @param {unknown} query
	 * @param {string[]} selectedValues
	 * @param {number} limit
	 */
	function filterOptions(options, query, selectedValues, limit) {
		const needle = normalizeSearch(query);
		const selectedValuesSet = new Set(selectedValues);
		return options
			.filter((option) => !selectedValuesSet.has(option.value) && optionMatches(option, needle))
			.slice(0, limit);
	}

	/**
	 * @param {GraphOption[]} options
	 * @param {string} value
	 */
	function selectedOptionLabel(options, value) {
		return options.find((option) => option.value === value)?.label || value;
	}

	/**
	 * @param {string[]} values
	 * @param {string} value
	 */
	function addSelectedValue(values, value) {
		if (!value || values.includes(value)) return values;
		return [...values, value];
	}

	/** @param {GraphOption} option */
	function selectConceptOption(option) {
		conceptFilter = option.label;
		conceptSearchActive = false;
		selectedNode = null;
		selectedEdge = null;
	}

	/** @param {GraphOption} option */
	function selectDocumentOption(option) {
		selectedDocumentValues = addSelectedValue(selectedDocumentValues, option.value);
		documentFilter = '';
		documentSearchActive = false;
		selectedNode = null;
		selectedEdge = null;
	}

	/** @param {GraphOption} option */
	function selectChunkOption(option) {
		selectedChunkValues = addSelectedValue(selectedChunkValues, option.value);
		chunkFilter = '';
		chunkSearchActive = false;
		selectedNode = null;
		selectedEdge = null;
	}

	/** @param {string} value */
	function removeDocumentValue(value) {
		selectedDocumentValues = selectedDocumentValues.filter((item) => item !== value);
	}

	/** @param {string} value */
	function removeChunkValue(value) {
		selectedChunkValues = selectedChunkValues.filter((item) => item !== value);
	}

	function clearGraphFilters() {
		conceptFilter = '';
		documentFilter = '';
		chunkFilter = '';
		selectedDocumentValues = [];
		selectedChunkValues = [];
		selectedNode = null;
		selectedEdge = null;
	}

	function closeSuggestionsSoon() {
		setTimeout(() => {
			conceptSearchActive = false;
			documentSearchActive = false;
			chunkSearchActive = false;
		}, 120);
	}

	/**
	 * @param {KeyboardEvent} event
	 * @param {GraphOption[]} suggestions
	 * @param {(option: GraphOption) => void} selectOption
	 */
	function handleSuggestionKeydown(event, suggestions, selectOption) {
		if (event.key === 'Enter' && suggestions.length > 0) {
			event.preventDefault();
			selectOption(suggestions[0]);
		} else if (event.key === 'Escape') {
			conceptSearchActive = false;
			documentSearchActive = false;
			chunkSearchActive = false;
		}
	}

	/**
	 * @param {GraphSnapshot} snapshot
	 * @param {GraphNode[]} nodes
	 * @param {GraphEdge[]} edges
	 */
	function graphWithCounts(snapshot, nodes, edges) {
		return {
			...snapshot,
			nodes,
			edges,
			counts: {
				concepts: nodes.filter((node) => node.type === 'concept').length,
				documents: nodes.filter((node) => node.type === 'document').length,
				chunks: nodes.filter((node) => node.type === 'chunk').length,
				edges: edges.length
			}
		};
	}

	/**
	 * @param {GraphNode} node
	 * @param {string} needle
	 */
	function nodeMatchesSearch(node, needle) {
		if (!needle) return true;
		return normalizeSearch([
			node.id,
			node.label,
			node.data?.name,
			node.data?.filename,
			node.data?.document_id,
			node.data?.chunk_id,
			node.data?.source_label,
			node.data?.text_preview
		].join(' ')).includes(needle);
	}

	/** @param {GraphSnapshot} snapshot */
	function filterGraph(snapshot) {
		const nodes = snapshot.nodes || [];
		const edges = snapshot.edges || [];
		const conceptNeedle = normalizeSearch(conceptFilter);
		const hasDocumentScope = selectedDocumentValues.length > 0;
		const hasChunkScope = selectedChunkValues.length > 0;
		const hasScope = hasDocumentScope || hasChunkScope;

		if (!hasScope && !conceptNeedle) {
			return graphWithCounts(snapshot, nodes, edges);
		}

		const nodeById = new Map(nodes.map((node) => [node.id, node]));
		const documentNodeIdByDocumentId = new Map();
		for (const node of nodes) {
			if (node.type === 'document') {
				documentNodeIdByDocumentId.set(String(node.data?.document_id || node.id), node.id);
			}
		}

		const scopedDocumentNodeIds = new Set();
		const scopedChunkNodeIds = new Set();
		const selectedDocumentIds = selectedDocumentSet;
		const selectedChunkIds = selectedChunkSet;

		for (const node of nodes) {
			if (node.type === 'document' && selectedDocumentIds.has(nodeSelectionValue(node))) {
				scopedDocumentNodeIds.add(node.id);
			}

			if (node.type === 'chunk') {
				const documentId = String(node.data?.document_id || '');
				if (selectedChunkIds.has(nodeSelectionValue(node)) || selectedDocumentIds.has(documentId)) {
					scopedChunkNodeIds.add(node.id);
					const documentNodeId = documentNodeIdByDocumentId.get(documentId);
					if (documentNodeId) scopedDocumentNodeIds.add(documentNodeId);
				}
			}
		}

		const visibleConceptIds = new Set();
		if (hasScope) {
			for (const edge of edges) {
				const targetNode = nodeById.get(edge.target);
				if (targetNode?.type !== 'concept' || !nodeMatchesSearch(targetNode, conceptNeedle)) continue;
				if (edge.type === 'MENTIONS' && scopedChunkNodeIds.has(edge.source)) {
					visibleConceptIds.add(edge.target);
				} else if (edge.type === 'DOCUMENT_MENTIONS' && scopedDocumentNodeIds.has(edge.source)) {
					visibleConceptIds.add(edge.target);
				}
			}
		} else {
			for (const node of nodes) {
				if (node.type === 'concept' && nodeMatchesSearch(node, conceptNeedle)) {
					visibleConceptIds.add(node.id);
				}
			}
		}

		const relevantChunkNodeIds = new Set();
		for (const edge of edges) {
			if (edge.type === 'MENTIONS' && scopedChunkNodeIds.has(edge.source) && visibleConceptIds.has(edge.target)) {
				relevantChunkNodeIds.add(edge.source);
			}
		}
		if (hasScope && !conceptNeedle) {
			for (const chunkNodeId of scopedChunkNodeIds) relevantChunkNodeIds.add(chunkNodeId);
		}

		const visibleNodeIds = new Set(visibleConceptIds);
		if (hasScope) {
			for (const chunkNodeId of relevantChunkNodeIds) {
				visibleNodeIds.add(chunkNodeId);
				const chunkNode = nodeById.get(chunkNodeId);
				const documentNodeId = documentNodeIdByDocumentId.get(String(chunkNode?.data?.document_id || ''));
				if (documentNodeId) visibleNodeIds.add(documentNodeId);
			}
			for (const documentNodeId of scopedDocumentNodeIds) {
				if (!conceptNeedle || edges.some((edge) => edge.source === documentNodeId && visibleConceptIds.has(edge.target))) {
					visibleNodeIds.add(documentNodeId);
				}
			}
		}

		const visibleNodes = nodes.filter((node) => visibleNodeIds.has(node.id));
		const visibleEdges = edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target));
		return graphWithCounts(snapshot, visibleNodes, visibleEdges);
	}

	/** @param {GraphSnapshot} snapshot */
	function conceptsOnlyGraph(snapshot) {
		const nodes = (snapshot.nodes || []).filter((node) => node.type === 'concept');
		const conceptIds = new Set(nodes.map((node) => node.id));
		const edges = (snapshot.edges || []).filter((edge) => conceptIds.has(edge.source) && conceptIds.has(edge.target));
		return graphWithCounts(snapshot, nodes, edges);
	}

	/** @param {number} count */
	function conceptColumnCount(count) {
		if (count > 36) return 4;
		if (count > 18) return 3;
		if (count > 8) return 2;
		return 1;
	}

	/** @param {GraphNode} node */
	function nodeSortLabel(node) {
		return String(node.data?.filename || node.data?.source_label || node.label || node.id || '');
	}

	/**
	 * @param {GraphNode} left
	 * @param {GraphNode} right
	 */
	function compareNodesByLabel(left, right) {
		return graphCollator.compare(nodeSortLabel(left), nodeSortLabel(right));
	}

	/** @param {GraphNode} node */
	function conceptSortScore(node) {
		return Number(node.data?.chunk_count || 0);
	}

	/** @param {GraphNode[]} chunks */
	function averageChunkY(chunks) {
		if (chunks.length === 0) return null;
		return chunks.reduce((sum, chunk) => sum + Number(chunk.y || 0), 0) / chunks.length;
	}

	/**
	 * @param {number} index
	 * @param {number} total
	 */
	function distributedY(index, total) {
		if (total <= 1) return graphHeight / 2;
		return 70 + (index * (graphHeight - 140)) / Math.max(1, total - 1);
	}

	/** @param {GraphNode} node */
	function nodeRadius(node) {
		if (node.type === 'document') return 22;
		return node.type === 'chunk' ? 18 : 25;
	}

	/** @param {GraphNode} node */
	function nodeLabelY(node) {
		return nodeRadius(node) + 17;
	}

	/** @param {GraphEdge} edge */
	function showEdgeLabel(edge) {
		return selectedEdge?.id === edge.id && edge.type !== 'MENTIONS' && edge.type !== 'CONTAINS' && edge.type !== 'DOCUMENT_MENTIONS';
	}

	/**
	 * @param {GraphNode[]} nodes
	 * @returns {PositionedGraphNode[]}
	 */
	function layoutNodes(nodes) {
		const conceptNodes = nodes
			.filter((node) => node.type === 'concept')
			.sort((left, right) => conceptSortScore(right) - conceptSortScore(left) || compareNodesByLabel(left, right));
		const documentNodes = nodes.filter((node) => node.type === 'document').sort(compareNodesByLabel);
		const documentOrder = new Map(documentNodes.map((node, index) => [String(node.data?.document_id || node.id), index]));
		const chunkNodes = nodes
			.filter((node) => node.type === 'chunk')
			.sort((left, right) => {
				const leftDocument = String(left.data?.document_id || '');
				const rightDocument = String(right.data?.document_id || '');
				const leftDocumentIndex = documentOrder.has(leftDocument) ? documentOrder.get(leftDocument) : Number.MAX_SAFE_INTEGER;
				const rightDocumentIndex = documentOrder.has(rightDocument) ? documentOrder.get(rightDocument) : Number.MAX_SAFE_INTEGER;
				return (leftDocumentIndex ?? 0) - (rightDocumentIndex ?? 0) || compareNodesByLabel(left, right);
			});
		const hasDocumentColumn = documentNodes.length > 0;
		const documentX = 95;
		const chunkX = hasDocumentColumn ? 335 : 165;
		const conceptX = hasDocumentColumn ? 690 : 640;
		const conceptColumns = conceptColumnCount(conceptNodes.length);
		const conceptRows = Math.max(1, Math.ceil(conceptNodes.length / conceptColumns));
		const positionedChunks = chunkNodes.map((node, index) => ({
			...node,
			x: chunkX,
			y: distributedY(index, chunkNodes.length)
		}));
		const chunksByDocument = new Map();
		for (const chunk of positionedChunks) {
			const documentId = String(chunk.data?.document_id || '');
			if (!documentId) continue;
			if (!chunksByDocument.has(documentId)) chunksByDocument.set(documentId, []);
			chunksByDocument.get(documentId).push(chunk);
		}

		return nodes.map((node) => {
			if (node.type === 'concept') {
				const index = conceptNodes.findIndex((item) => item.id === node.id);
				const column = index % conceptColumns;
				const row = Math.floor(index / conceptColumns);
				const columnGap = conceptColumns >= 4 ? 82 : conceptColumns === 3 ? 105 : 125;
				return {
					...node,
					x: conceptX + (column - (conceptColumns - 1) / 2) * columnGap,
					y: distributedY(row, conceptRows)
				};
			}

			if (node.type === 'document') {
				const index = documentNodes.findIndex((item) => item.id === node.id);
				const documentId = String(node.data?.document_id || node.id);
				const chunkY = averageChunkY(chunksByDocument.get(documentId) || []);
				return {
					...node,
					x: documentX,
					y: chunkY ?? distributedY(index, documentNodes.length)
				};
			}

			return positionedChunks.find((item) => item.id === node.id) || { ...node, x: chunkX, y: graphHeight / 2 };
		});
	}

	/** @type {PositionedGraphNode[]} */
	let positionedNodes = $derived(layoutNodes(visibleGraph.nodes || []));
	/** @type {Record<string, PositionedGraphNode>} */
	let positionById = $derived(
		Object.fromEntries(positionedNodes.map((node) => [node.id, node]))
	);
	/** @type {RenderedGraphEdge[]} */
	let renderedEdges = $derived(
		(visibleGraph.edges || [])
			.map((edge) => ({
				...edge,
				sourceNode: positionById[edge.source],
				targetNode: positionById[edge.target]
			}))
			.filter((edge) => edge.sourceNode && edge.targetNode)
	);
	let selectedConcept = $derived(selectedNode?.type === 'concept' ? selectedNode : null);
	let selectedDocument = $derived(selectedNode?.type === 'document' ? selectedNode : null);
	let selectedChunk = $derived(selectedNode?.type === 'chunk' ? selectedNode : null);
	let selectedEditableRelationship = $derived(
		selectedEdge?.type === 'RELATES_TO' ? selectedEdge : null
	);

	/** @param {GraphNode} node */
	function nodeFill(node) {
		if (selectedNode?.id === node.id) return '#1d4ed8';
		if (node.type === 'document') return '#4f46e5';
		if (node.type === 'chunk') return '#f59e0b';
		const state = node.data?.verification_state;
		if (state === 'verified') return '#059669';
		if (state === 'rejected') return '#dc2626';
		return '#2271b3';
	}

	/** @param {GraphEdge} edge */
	function edgeStroke(edge) {
		if (selectedEdge?.id === edge.id) return '#1d4ed8';
		if (edge.type === 'CONTAINS') return '#475569';
		if (edge.type === 'DOCUMENT_MENTIONS') return '#7c3aed';
		if (edge.type === 'MENTIONS') return '#d97706';
		if (edge.type === 'CO_OCCURS_WITH') return '#64748b';
		return '#2271b3';
	}

	/** @param {GraphEdge} edge */
	function edgeWidth(edge) {
		if (selectedEdge?.id === edge.id) return 3.5;
		if (edge.type === 'RELATES_TO') return 1.8;
		if (edge.type === 'CO_OCCURS_WITH') return 1.4;
		return 1.1;
	}

	/** @param {GraphEdge} edge */
	function edgeOpacity(edge) {
		if (selectedEdge?.id === edge.id) return 0.95;
		if (edge.type === 'CONTAINS') return 0.3;
		if (edge.type === 'MENTIONS') return 0.2;
		if (edge.type === 'DOCUMENT_MENTIONS') return 0.24;
		if (edge.type === 'CO_OCCURS_WITH') return 0.28;
		return 0.4;
	}

	/** @param {GraphEdge} edge */
	function edgeDash(edge) {
		if (edge.type === 'CO_OCCURS_WITH') return '6 5';
		if (edge.type === 'MENTIONS') return '3 4';
		if (edge.type === 'DOCUMENT_MENTIONS') return '4 5';
		return '';
	}

	async function loadGraph() {
		if (!kbId) return;
		graphLoading = true;
		graphError = '';
		try {
			graph = await getGraphSnapshot(kbId, {
				include_chunks: true,
				limit: graphLimit
			});
			rememberGraphOptions(graph);
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

	/** @param {HistoryOverrides} [overrides] */
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

	/** @param {GraphNode} node */
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
			selectedChunkValues = addSelectedValue(selectedChunkValues, nodeSelectionValue(node));
			chunkFilter = '';
			traceSeedInput = node.data?.chunk_id || '';
			historyDocumentId = node.data?.document_id || '';
			loadHistory({ document_id: historyDocumentId });
		} else if (node.type === 'document') {
			selectedDocumentValues = addSelectedValue(selectedDocumentValues, nodeSelectionValue(node));
			documentFilter = '';
			historyDocumentId = node.data?.document_id || '';
			loadHistory({ document_id: historyDocumentId });
		}
	}

	/** @param {GraphEdge} edge */
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

	/**
	 * @param {KeyboardEvent} event
	 * @param {() => void} callback
	 */
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
		<div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_7rem_auto]">
			<div class="relative">
				<label for="graph-concept-filter" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Concept</label>
				<input id="graph-concept-filter" bind:value={conceptFilter} autocomplete="off" onfocus={() => (conceptSearchActive = true)} onblur={closeSuggestionsSoon} onkeydown={(event) => handleSuggestionKeydown(event, conceptSuggestions, selectConceptOption)} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="concept name" />
				{#if conceptSearchActive && conceptSuggestions.length > 0}
					<div class="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-md border border-gray-200 bg-white py-1 text-sm shadow-lg">
						{#each conceptSuggestions as option (option.value)}
							<button type="button" class="block w-full px-3 py-2 text-left hover:bg-gray-50" onmousedown={(event) => { event.preventDefault(); selectConceptOption(option); }}>
								<span class="block truncate font-medium text-gray-900">{option.label}</span>
								<span class="block truncate text-xs text-gray-500">{option.value}</span>
							</button>
						{/each}
					</div>
				{/if}
			</div>
			<div class="relative">
				<label for="graph-document-filter" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Documents</label>
				<div class="mt-1 flex min-h-9 w-full flex-wrap items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm shadow-sm focus-within:border-brand focus-within:ring-1 focus-within:ring-brand">
					{#each selectedDocumentValues as value (value)}
						<span class="inline-flex max-w-full items-center gap-1 rounded bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200">
							<span class="truncate">{truncate(selectedOptionLabel(documentOptions, value), 24)}</span>
							<button type="button" class="text-indigo-500 hover:text-indigo-800" aria-label={`Remove ${selectedOptionLabel(documentOptions, value)}`} onclick={() => removeDocumentValue(value)}>x</button>
						</span>
					{/each}
					<input id="graph-document-filter" bind:value={documentFilter} autocomplete="off" onfocus={() => (documentSearchActive = true)} onblur={closeSuggestionsSoon} onkeydown={(event) => handleSuggestionKeydown(event, documentSuggestions, selectDocumentOption)} class="min-w-28 flex-1 border-0 p-0 text-sm focus:ring-0" placeholder="document name" />
				</div>
				{#if documentSearchActive && documentSuggestions.length > 0}
					<div class="absolute z-30 mt-1 max-h-60 w-full overflow-auto rounded-md border border-gray-200 bg-white py-1 text-sm shadow-lg">
						{#each documentSuggestions as option (option.value)}
							<button type="button" class="block w-full px-3 py-2 text-left hover:bg-gray-50" onmousedown={(event) => { event.preventDefault(); selectDocumentOption(option); }}>
								<span class="block truncate font-medium text-gray-900">{option.label}</span>
								<span class="block truncate text-xs text-gray-500">{option.value}</span>
							</button>
					{/each}
					</div>
				{/if}
			</div>
			<div class="relative">
				<label for="graph-chunk-filter" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Chunks</label>
				<div class="mt-1 flex min-h-9 w-full flex-wrap items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm shadow-sm focus-within:border-brand focus-within:ring-1 focus-within:ring-brand">
					{#each selectedChunkValues as value (value)}
						<span class="inline-flex max-w-full items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-amber-200">
							<span class="truncate">{truncate(selectedOptionLabel(chunkOptions, value), 24)}</span>
							<button type="button" class="text-amber-600 hover:text-amber-900" aria-label={`Remove ${selectedOptionLabel(chunkOptions, value)}`} onclick={() => removeChunkValue(value)}>x</button>
						</span>
					{/each}
					<input id="graph-chunk-filter" bind:value={chunkFilter} autocomplete="off" onfocus={() => (chunkSearchActive = true)} onblur={closeSuggestionsSoon} onkeydown={(event) => handleSuggestionKeydown(event, chunkSuggestions, selectChunkOption)} class="min-w-28 flex-1 border-0 p-0 text-sm focus:ring-0" placeholder="chunk label" />
				</div>
				{#if chunkSearchActive && chunkSuggestions.length > 0}
					<div class="absolute z-30 mt-1 max-h-60 w-full overflow-auto rounded-md border border-gray-200 bg-white py-1 text-sm shadow-lg">
						{#each chunkSuggestions as option (option.value)}
							<button type="button" class="block w-full px-3 py-2 text-left hover:bg-gray-50" onmousedown={(event) => { event.preventDefault(); selectChunkOption(option); }}>
								<span class="block truncate font-medium text-gray-900">{option.label}</span>
								<span class="block truncate text-xs text-gray-500">{option.value}</span>
							</button>
					{/each}
					</div>
				{/if}
			</div>
			<div>
				<label for="graph-limit" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Limit</label>
				<input id="graph-limit" type="number" min="1" max="200" bind:value={graphLimit} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
			</div>
			<div class="mt-6 flex flex-wrap items-center gap-3">
				<label class="inline-flex items-center gap-2 text-sm text-gray-700">
					<input type="checkbox" bind:checked={onlyConcepts} class="rounded border-gray-300 text-brand focus:ring-brand" />
					Only concepts
				</label>
				{#if hasGraphFilters}
					<button type="button" onclick={clearGraphFilters} class="text-sm font-medium text-gray-500 hover:text-gray-800">Clear</button>
				{/if}
			</div>
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
					<span class="rounded-full bg-blue-50 px-2 py-1 text-blue-700 ring-1 ring-blue-200">Concepts: {visibleGraph.counts?.concepts || 0}</span>
					{#if !onlyConcepts}
						<span class="rounded-full bg-indigo-50 px-2 py-1 text-indigo-700 ring-1 ring-indigo-200">Documents: {visibleGraph.counts?.documents || 0}</span>
						<span class="rounded-full bg-amber-50 px-2 py-1 text-amber-700 ring-1 ring-amber-200">Chunks: {visibleGraph.counts?.chunks || 0}</span>
					{/if}
					<span class="rounded-full bg-slate-50 px-2 py-1 text-slate-700 ring-1 ring-slate-200">Edges: {visibleGraph.counts?.edges || 0}</span>
				</div>
				<div class="flex flex-wrap gap-3 text-xs text-gray-500">
					<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-brand"></span>Concept</span>
					{#if !onlyConcepts}
						<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-indigo-600"></span>Document</span>
						<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-amber-500"></span>Chunk</span>
					{/if}
					<span class="inline-flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-emerald-600"></span>Verified</span>
				</div>
			</div>

			<div class="relative min-h-130 overflow-auto bg-white">
				{#if graphLoading}
					<div class="absolute inset-0 z-10 flex items-center justify-center bg-white/70 text-sm text-gray-600">Loading graph...</div>
				{/if}

				{#if positionedNodes.length === 0 && !graphLoading}
					<div class="flex min-h-130 items-center justify-center text-sm text-gray-500">No graph data available.</div>
				{:else}
					<svg class="w-full" style={`height: ${graphHeight}px; min-width: ${graphWidth}px;`} viewBox={`0 0 ${graphWidth} ${graphHeight}`} role="img" aria-label="Knowledge graph visualization">
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
									stroke-width={edgeWidth(edge)}
									stroke-opacity={edgeOpacity(edge)}
									stroke-dasharray={edgeDash(edge)}
									marker-end={edge.type === 'RELATES_TO' && selectedEdge?.id === edge.id ? 'url(#graph-arrow)' : undefined}
									class="cursor-pointer"
									role="button"
									tabindex="0"
									aria-label={`Select ${edge.label || edge.type} edge`}
									onclick={() => selectEdge(edge)}
									onkeydown={(event) => activateWithKeyboard(event, () => selectEdge(edge))}
								/>
								{#if showEdgeLabel(edge)}
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
								<circle r={nodeRadius(node)} fill={nodeFill(node)} stroke="#ffffff" stroke-width="3"></circle>
								{#if node.type === 'document'}
									<rect x="-8" y="-10" width="16" height="20" rx="2" fill="white" opacity="0.92"></rect>
									<path d="M4 -10 L8 -6 L4 -6 Z" fill="#c7d2fe"></path>
									<line x1="-5" y1="-2" x2="5" y2="-2" stroke="#4f46e5" stroke-width="1"></line>
									<line x1="-5" y1="3" x2="5" y2="3" stroke="#4f46e5" stroke-width="1"></line>
								{:else if node.type === 'concept'}
									<text text-anchor="middle" y="4" class="pointer-events-none fill-white text-[10px] font-semibold">{node.data?.chunk_count || 0}</text>
								{:else}
									<rect x="-8" y="-9" width="16" height="18" rx="2" fill="white" opacity="0.9"></rect>
									<line x1="-5" y1="-3" x2="5" y2="-3" stroke="#f59e0b" stroke-width="1"></line>
									<line x1="-5" y1="2" x2="5" y2="2" stroke="#f59e0b" stroke-width="1"></line>
								{/if}
								<text text-anchor="middle" y={nodeLabelY(node)} class="pointer-events-none select-none fill-gray-700 text-[11px] font-medium">
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
				{#if selectedDocument}
					<div class="mt-3 space-y-2 text-sm text-gray-700">
						<div class="font-medium text-indigo-700">{selectedDocument.label}</div>
						<div class="break-all">Document: {selectedDocument.data.document_id}</div>
						<div>Chunks: {selectedDocument.data.chunk_count || 0}</div>
						{#if selectedDocument.data.concepts?.length}
							<div>Concepts: {selectedDocument.data.concepts.join(', ')}</div>
						{/if}
					</div>
				{:else if selectedConcept}
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
