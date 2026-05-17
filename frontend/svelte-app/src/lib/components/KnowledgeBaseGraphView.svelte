<script>
	import { tick } from 'svelte';
	import {
		curateGraphConcept,
		curateGraphRelationship,
		editGraphRelationship,
		getGraphSnapshot,
		listGraphChanges,
		mergeGraphConcepts,
		renameGraphConcept,
		revertGraphChange
	} from '$lib/services/graphService';
	import { user } from '$lib/stores/userStore';
	import {
		buildExpungedConcepts,
		buildExpungedRelationships,
		changeDetail,
		changeMetaLine,
		changeOperationLabel,
		stateClass,
		stateLabel,
		stateRank
	} from '$lib/utils/graphCuration';

	/** @typedef {{ [key: string]: any }} GraphData */
	/** @typedef {{ id: string, type: string, label: string, data: GraphData, x?: number, y?: number }} GraphNode */
	/** @typedef {{ id: string, type: string, source: string, target: string, label?: string, weight?: number, data: GraphData }} GraphEdge */
	/** @typedef {{ concepts?: number, documents?: number, chunks?: number, edges?: number }} GraphCounts */
	/** @typedef {{ collection_id: string | number | null, nodes: GraphNode[], edges: GraphEdge[], filters: GraphData, counts: GraphCounts }} GraphSnapshot */
	/** @typedef {{ value: string, label: string, node: GraphNode }} GraphOption */
	/** @typedef {{ concept?: string, relationship_source?: string, relationship_target?: string, relationship_relation?: string, document_id?: string }} HistoryOverrides */
	/** @typedef {{ event_id: string, operation?: string, actor?: string, timestamp?: string, filename?: string, document_id?: string, concepts?: string[], payload_json?: string }} GraphChange */
	/** @typedef {{ GraphClass: any, SigmaClass: any, forceAtlas2: any }} SigmaModules */
	/** @typedef {{ graphologyGraph: any, nodeLookup: Map<string, GraphNode>, edgeLookup: Map<string, GraphEdge> }} SigmaGraphModel */

	let { kbId = '', canModify = false } = $props();

	let graph = $state(/** @type {GraphSnapshot} */ ({ collection_id: null, nodes: [], edges: [], filters: {}, counts: {} }));
	let optionNodes = $state(/** @type {GraphNode[]} */ ([]));
	let expungedChanges = $state(/** @type {GraphChange[]} */ ([]));
	let loadedKbId = $state('');
	let graphLoading = $state(false);
	let graphError = $state('');
	let graphLimit = $state(140);
	let showExplorer = $state(false);

	let reviewTab = $state('relationships');
	let curationSearch = $state('');
	let statusFilter = $state('all');
	let evidenceFilter = $state('all');
	let selectedConceptName = $state('');
	let selectedRelationshipId = $state('');

	let relationshipRelation = $state('');
	let relationshipWeight = $state('');
	let relationshipDescription = $state('');
	let relationshipEvidenceText = $state('');
	let relationshipNotes = $state('');
	let relationshipTags = $state('');
	let relationshipVerification = $state('unverified');

	let renameName = $state('');
	let mergeSources = $state('');
	let mergeTarget = $state('');
	let conceptNotes = $state('');
	let conceptTags = $state('');
	let conceptVerification = $state('unverified');

	let curationMessage = $state('');
	let curationError = $state('');
	let actionLoading = $state(false);

	let changes = $state(/** @type {GraphChange[]} */ ([]));
	let historyLoading = $state(false);
	let historyError = $state('');
	let historyFilters = $state(/** @type {HistoryOverrides} */ ({}));
	let historyTitle = $state('Relationship / Concept History');
	let historyRequestId = 0;

	let conceptFilter = $state('');
	let documentFilter = $state('');
	let chunkFilter = $state('');
	let selectedDocumentValues = $state(/** @type {string[]} */ ([]));
	let selectedChunkValues = $state(/** @type {string[]} */ ([]));
	let onlyConcepts = $state(false);
	let selectedNode = $state(/** @type {GraphNode | null} */ (null));
	let selectedEdge = $state(/** @type {GraphEdge | null} */ (null));
	let hoveredNodeId = $state('');
	let hoveredEdgeId = $state('');
	let conceptSearchActive = $state(false);
	let documentSearchActive = $state(false);
	let chunkSearchActive = $state(false);
	let sigmaContainer = $state(/** @type {HTMLDivElement | null} */ (null));
	let sigmaReady = $state(false);
	let sigmaError = $state('');

	const sigmaDefaultHeight = 620;
	const sigmaGoldenAngle = Math.PI * (3 - Math.sqrt(5));
	const graphCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
	let sigmaModulesPromise = /** @type {Promise<SigmaModules> | null} */ (null);
	let sigmaRenderer = /** @type {any} */ (null);
	let sigmaGraphInstance = /** @type {any} */ (null);
	let sigmaNodeLookup = /** @type {Map<string, GraphNode>} */ (new Map());
	let sigmaEdgeLookup = /** @type {Map<string, GraphEdge>} */ (new Map());
	let sigmaRenderRequestId = 0;

	$effect(() => {
		if (kbId && kbId !== loadedKbId) {
			loadedKbId = kbId;
			selectedConceptName = '';
			selectedRelationshipId = '';
			selectedNode = null;
			selectedEdge = null;
			void loadGraph();
			expungedChanges = [];
			changes = [];
			historyFilters = {};
			historyTitle = 'Relationship / Concept History';
		}
	});

	let conceptNodes = $derived.by(() =>
		(graph.nodes || [])
			.filter((node) => node.type === 'concept')
			.sort((left, right) => conceptSortScore(right) - conceptSortScore(left) || compareNodesByLabel(left, right))
	);
	let conceptByName = $derived.by(() => new Map(conceptNodes.map((node) => [String(node.data?.name || node.label || ''), node])));
	let conceptOptions = $derived(buildNodeOptions(optionNodes, 'concept'));
	let documentOptions = $derived(buildNodeOptions(optionNodes, 'document'));
	let chunkOptions = $derived(buildNodeOptions(optionNodes, 'chunk'));
	let selectedDocumentSet = $derived.by(() => new Set(selectedDocumentValues));
	let selectedChunkSet = $derived.by(() => new Set(selectedChunkValues));
	let scopedChunkOptions = $derived.by(() => {
		if (selectedDocumentValues.length === 0) return chunkOptions;
		return chunkOptions.filter((option) => selectedDocumentSet.has(String(option.node.data?.document_id || '')));
	});
	let conceptSuggestions = $derived(filterOptions(conceptOptions, conceptFilter, [], 8));
	let documentSuggestions = $derived(filterOptions(documentOptions, documentFilter, selectedDocumentValues, 8));
	let chunkSuggestions = $derived(filterOptions(scopedChunkOptions, chunkFilter, selectedChunkValues, 8));
	let hasGraphFilters = $derived(Boolean(conceptFilter.trim() || selectedDocumentValues.length || selectedChunkValues.length));

	let relationships = $derived.by(() =>
		(graph.edges || [])
			.filter((edge) => edge.type === 'RELATES_TO')
			.map((edge) => enrichRelationship(edge))
			.sort(compareRelationships)
	);
	let expungedRelationships = $derived.by(() => buildExpungedRelationships(expungedChanges, relationships));
	let reviewRelationships = $derived.by(() => [...relationships, ...expungedRelationships].sort(compareRelationships));
	let expungedConcepts = $derived.by(() => buildExpungedConcepts(expungedChanges, conceptNodes));
	let reviewConcepts = $derived.by(() =>
		[...conceptNodes, ...expungedConcepts]
			.sort((left, right) => conceptSortScore(right) - conceptSortScore(left) || compareNodesByLabel(left, right))
	);
	let selectedConcept = $derived.by(() => reviewConcepts.find((node) => String(node.data?.name || '') === selectedConceptName) || null);
	let selectedRelationship = $derived.by(() => reviewRelationships.find((edge) => edge.id === selectedRelationshipId) || null);
	let selectedConceptRelationships = $derived.by(() => {
		if (!selectedConcept) return [];
		const name = String(selectedConcept.data?.name || '');
		return reviewRelationships.filter((edge) => edge.data?.source === name || edge.data?.target === name);
	});
	let filteredConcepts = $derived.by(() => reviewConcepts.filter((node) => matchesConceptReview(node)));
	let filteredRelationships = $derived.by(() => reviewRelationships.filter((edge) => matchesRelationshipReview(edge)));
	let reviewStats = $derived.by(() => buildReviewStats(reviewConcepts, reviewRelationships));

	let filteredGraph = $derived.by(() => filterGraph(graph));
	let visibleGraph = $derived.by(() => (onlyConcepts ? conceptsOnlyGraph(filteredGraph) : filteredGraph));
	let visibleNodeCount = $derived((visibleGraph.nodes || []).length);
	let visibleRelationshipCount = $derived((visibleGraph.edges || []).filter((edge) => edge.type === 'RELATES_TO').length);

	$effect(() => {
		const container = sigmaContainer;
		const snapshot = visibleGraph;
		const conceptMode = onlyConcepts;
		if (!showExplorer || !container || graphLoading || (snapshot.nodes || []).length === 0) {
			destroySigmaGraph();
			return;
		}
		void renderSigmaGraph(snapshot, conceptMode, container);
	});

	$effect(() => {
		const ready = sigmaReady;
		const selectedNodeId = selectedNode?.id || '';
		const selectedRelationshipKey = selectedRelationshipId || selectedEdge?.id || '';
		const selectedConcept = selectedConceptName;
		const hoveredNode = hoveredNodeId;
		const hoveredEdge = hoveredEdgeId;
		const relationshipCount = visibleRelationshipCount;
		if (ready) updateSigmaReducers({ selectedNodeId, selectedRelationshipKey, selectedConcept, hoveredNode, hoveredEdge, relationshipCount });
	});

	async function loadGraph() {
		if (!kbId) return;
		graphLoading = true;
		graphError = '';
		try {
			const [rawSnapshot, rawRelationshipExpunges, rawConceptExpunges] = await Promise.all([
				getGraphSnapshot(kbId, { include_chunks: true, limit: Number(graphLimit) || 140 }),
				listGraphChanges(kbId, { operation: 'manual_expunge_relationship', limit: 200 }),
				listGraphChanges(kbId, { operation: 'manual_expunge_concept', limit: 200 })
			]);
			const snapshot = /** @type {GraphSnapshot} */ (rawSnapshot);
			const relationshipExpunges = /** @type {GraphChange[]} */ (rawRelationshipExpunges || []);
			const conceptExpunges = /** @type {GraphChange[]} */ (rawConceptExpunges || []);
			const nextExpungedChanges = [...(relationshipExpunges || []), ...(conceptExpunges || [])];
			graph = snapshot;
			expungedChanges = nextExpungedChanges;
			rememberGraphOptions(snapshot);
			const activeConceptNodes = (snapshot.nodes || []).filter((node) => node.type === 'concept');
			const expungedConceptNames = new Set(buildExpungedConcepts(nextExpungedChanges, activeConceptNodes).map((node) => String(node.data?.name || '')));
			if (selectedRelationshipId && !snapshot.edges?.some((edge) => edge.id === selectedRelationshipId) && !selectedRelationshipId.startsWith('expunged-relationship-')) selectedRelationshipId = '';
			if (selectedConceptName && !activeConceptNodes.some((node) => node.data?.name === selectedConceptName) && !expungedConceptNames.has(selectedConceptName)) selectedConceptName = '';
		} catch (err) {
			console.error('Error loading graph snapshot:', err);
			graphError = err instanceof Error ? err.message : 'Failed to load graph snapshot';
			graph = { collection_id: kbId, nodes: [], edges: [], filters: {}, counts: {} };
			expungedChanges = [];
		} finally {
			graphLoading = false;
		}
	}

	/** @param {HistoryOverrides} [overrides] */
	async function loadHistory(overrides = historyFilters) {
		if (!kbId) return;
		const requestId = ++historyRequestId;
		const nextFilters = compactHistoryFilters(overrides);
		historyFilters = nextFilters;
		historyError = '';
		if (Object.keys(nextFilters).length === 0) {
			changes = [];
			historyLoading = false;
			return;
		}
		historyLoading = true;
		changes = [];
		try {
			const nextChanges = await listGraphChanges(kbId, {
				concept: nextFilters.concept,
				relationship_source: nextFilters.relationship_source,
				relationship_target: nextFilters.relationship_target,
				relationship_relation: nextFilters.relationship_relation,
				document_id: nextFilters.document_id,
				limit: 20
			});
			if (requestId === historyRequestId) changes = nextChanges;
		} catch (err) {
			if (requestId === historyRequestId) {
				console.error('Error loading graph history:', err);
				historyError = err instanceof Error ? err.message : 'Failed to load graph history';
				changes = [];
			}
		} finally {
			if (requestId === historyRequestId) historyLoading = false;
		}
	}

	/** @param {HistoryOverrides} filters */
	function compactHistoryFilters(filters = {}) {
		return Object.fromEntries(
			Object.entries(filters)
				.map(([key, value]) => [key, typeof value === 'string' ? value.trim() : value])
				.filter(([, value]) => value !== undefined && value !== null && value !== '')
		);
	}

	/** @param {GraphSnapshot} snapshot */
	function rememberGraphOptions(snapshot) {
		const byId = new Map(optionNodes.map((node) => [node.id, node]));
		for (const node of snapshot.nodes || []) {
			if (node.type === 'concept' || node.type === 'document' || node.type === 'chunk') byId.set(node.id, node);
		}
		optionNodes = Array.from(byId.values());
	}

	/** @param {GraphEdge} edge @returns {GraphEdge} */
	function enrichRelationship(edge) {
		const sourceName = String(edge.data?.source || '').trim();
		const targetName = String(edge.data?.target || '').trim();
		const sourceNode = conceptByName.get(sourceName);
		const targetNode = conceptByName.get(targetName);
		return {
			...edge,
			data: {
				...edge.data,
				source: sourceName,
				target: targetName,
				source_label: sourceNode?.label || sourceName,
				target_label: targetNode?.label || targetName,
				verification_state: edge.data?.verification_state || 'unverified',
				relation: edge.data?.relation || edge.label || 'related_to'
			}
		};
	}

	/** @param {GraphEdge} left @param {GraphEdge} right */
	function compareRelationships(left, right) {
		return stateRank(left.data?.verification_state) - stateRank(right.data?.verification_state)
			|| Number(right.weight || 0) - Number(left.weight || 0)
			|| graphCollator.compare(String(left.data?.source_label || ''), String(right.data?.source_label || ''))
			|| graphCollator.compare(String(left.data?.target_label || ''), String(right.data?.target_label || ''));
	}

	/** @param {GraphNode} node */
	function selectConceptForReview(node) {
		selectedConceptName = String(node.data?.name || '');
		selectedRelationshipId = '';
		reviewTab = 'concepts';
		renameName = node.label || node.data?.name || '';
		mergeSources = '';
		mergeTarget = node.data?.name || node.label || '';
		conceptNotes = node.data?.notes || '';
		conceptTags = (node.data?.tags || []).join(', ');
		conceptVerification = node.data?.verification_state || 'unverified';
		historyTitle = 'Concept History';
		void loadHistory({ concept: node.data?.name || '' });
	}

	/** @param {GraphEdge} edge */
	function selectRelationshipForReview(edge) {
		selectedRelationshipId = edge.id;
		selectedConceptName = '';
		reviewTab = 'relationships';
		relationshipRelation = edge.data?.relation || edge.label || '';
		relationshipWeight = edge.weight === undefined || edge.weight === null ? '' : String(edge.weight);
		relationshipDescription = edge.data?.description || '';
		relationshipEvidenceText = edge.data?.evidence || '';
		relationshipNotes = edge.data?.notes || '';
		relationshipTags = (edge.data?.tags || []).join(', ');
		relationshipVerification = edge.data?.verification_state || 'unverified';
		historyTitle = 'Relationship History';
		void loadHistory(relationshipHistoryFilters(edge));
	}

	/** @param {GraphNode} node */
	function selectGraphNode(node) {
		selectedNode = node;
		selectedEdge = null;
		if (node.type === 'concept') selectConceptForReview(node);
		if (node.type === 'document') {
			selectedDocumentValues = addSelectedValue(selectedDocumentValues, nodeSelectionValue(node));
			historyTitle = 'Document History';
			void loadHistory({ document_id: node.data?.document_id || '' });
		}
		if (node.type === 'chunk') {
			selectedChunkValues = addSelectedValue(selectedChunkValues, nodeSelectionValue(node));
			historyTitle = 'Document History';
			void loadHistory({ document_id: node.data?.document_id || '' });
		}
	}

	/** @param {GraphEdge} edge */
	function selectGraphEdge(edge) {
		selectedEdge = edge;
		selectedNode = null;
		if (edge.type === 'RELATES_TO') selectRelationshipForReview(enrichRelationship(edge));
	}

	function clearGraphSelection() {
		selectedNode = null;
		selectedEdge = null;
		selectedConceptName = '';
		selectedRelationshipId = '';
		hoveredNodeId = '';
		hoveredEdgeId = '';
	}

	async function submitConceptCuration() {
		if (!selectedConcept || actionLoading) return;
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			const result = await curateGraphConcept(kbId, selectedConcept.data.name, {
				notes: conceptNotes,
				tags: parseTags(conceptTags),
				verification_state: conceptVerification,
				actor: currentAuditActor(),
				reason: 'Educator graph curation'
			});
			curationMessage = recordedMessage(result, 'concept curation');
			if (hasRecordedChange(result)) await refreshAfterCuration();
		} catch (err) {
			console.error('Error curating concept:', err);
			curationError = err instanceof Error ? err.message : 'Failed to update concept';
		} finally {
			actionLoading = false;
		}
	}

	async function submitRename() {
		if (!selectedConcept || actionLoading) return;
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			const result = await renameGraphConcept(kbId, selectedConcept.data.name, {
				new_name: renameName,
				actor: currentAuditActor(),
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'rename'}`;
			selectedConceptName = '';
			await refreshAfterCuration();
		} catch (err) {
			console.error('Error renaming concept:', err);
			curationError = err instanceof Error ? err.message : 'Failed to rename concept';
		} finally {
			actionLoading = false;
		}
	}

	async function submitMerge() {
		if (actionLoading) return;
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			const result = await mergeGraphConcepts(kbId, {
				source_names: parseTags(mergeSources),
				target_name: mergeTarget,
				actor: currentAuditActor(),
				reason: 'Educator graph curation'
			});
			curationMessage = `Recorded ${result.operation || 'merge'}`;
			selectedConceptName = '';
			await refreshAfterCuration();
		} catch (err) {
			console.error('Error merging concepts:', err);
			curationError = err instanceof Error ? err.message : 'Failed to merge concepts';
		} finally {
			actionLoading = false;
		}
	}

	async function submitRelationshipEdit() {
		if (!selectedRelationship || actionLoading) return;
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			const result = await editGraphRelationship(kbId, relationshipPayload(selectedRelationship, {
				new_relation: relationshipRelation,
				weight: relationshipWeight === '' ? undefined : Number(relationshipWeight),
				description: relationshipDescription,
				evidence: relationshipEvidenceText,
				notes: relationshipNotes,
				tags: parseTags(relationshipTags),
				verification_state: relationshipVerification
			}));
			curationMessage = recordedMessage(result, 'relationship edit');
			if (hasRecordedChange(result)) await refreshAfterCuration();
		} catch (err) {
			console.error('Error editing relationship:', err);
			curationError = err instanceof Error ? err.message : 'Failed to edit relationship';
		} finally {
			actionLoading = false;
		}
	}

	/** @param {GraphEdge} edge @param {string} state */
	async function setRelationshipState(edge, state) {
		if (!canModify || actionLoading) return;
		const filters = relationshipHistoryFilters(edge);
		historyFilters = filters;
		historyTitle = 'Relationship History';
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			const result = await curateGraphRelationship(kbId, relationshipPayload(edge, { verification_state: state }));
			curationMessage = recordedMessage(result, 'relationship curation');
			if (hasRecordedChange(result)) {
				if (state === 'rejected') {
					reviewTab = 'relationships';
					statusFilter = 'rejected';
					selectedRelationshipId = '';
					selectedEdge = null;
					relationshipVerification = 'rejected';
				} else {
					selectedRelationshipId = edge.id;
					relationshipVerification = state;
				}
				await refreshAfterCuration(filters);
				if (state === 'rejected' && result.event_id) selectedRelationshipId = `expunged-relationship-${result.event_id}`;
			}
		} catch (err) {
			console.error('Error updating relationship state:', err);
			curationError = err instanceof Error ? err.message : 'Failed to update relationship state';
		} finally {
			actionLoading = false;
		}
	}

	/** @param {GraphNode} node @param {string} state */
	async function setConceptState(node, state) {
		if (!canModify || actionLoading) return;
		const filters = { concept: node.data.name };
		historyFilters = filters;
		historyTitle = 'Concept History';
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			const result = await curateGraphConcept(kbId, node.data.name, {
				verification_state: state,
				actor: currentAuditActor(),
				reason: 'Educator graph curation'
			});
			curationMessage = recordedMessage(result, 'concept curation');
			if (hasRecordedChange(result)) {
				if (state === 'rejected') {
					reviewTab = 'concepts';
					statusFilter = 'rejected';
					selectedConceptName = '';
					selectedNode = null;
					conceptVerification = 'rejected';
				} else {
					selectedConceptName = node.data.name;
					conceptVerification = state;
				}
				await refreshAfterCuration(filters);
				if (state === 'rejected') selectedConceptName = node.data.name;
			}
		} catch (err) {
			console.error('Error updating concept state:', err);
			curationError = err instanceof Error ? err.message : 'Failed to update concept state';
		} finally {
			actionLoading = false;
		}
	}

	/** @param {string | undefined} eventId */
	async function restoreExpungedItem(eventId) {
		if (!canModify || actionLoading || !eventId) return;
		actionLoading = true;
		curationMessage = '';
		curationError = '';
		try {
			await revertGraphChange(kbId, eventId, {
				actor: currentAuditActor(),
				reason: 'Educator graph curation'
			});
			curationMessage = 'Restored as approved';
			statusFilter = 'verified';
			selectedConceptName = '';
			selectedRelationshipId = '';
			selectedNode = null;
			selectedEdge = null;
			await refreshAfterCuration({});
		} catch (err) {
			console.error('Error restoring graph item:', err);
			curationError = err instanceof Error ? err.message : 'Failed to restore graph item';
		} finally {
			actionLoading = false;
		}
	}

	/** @param {HistoryOverrides} [filters] */
	async function refreshAfterCuration(filters = historyFilters) {
		await loadGraph();
		await loadHistory(filters);
	}

	/** @param {{ reason?: string, details?: GraphData } | null | undefined} result */
	function hasRecordedChange(result) {
		return result?.reason !== 'no_change' && result?.details?.changed !== false;
	}

	/** @param {{ operation?: string, reason?: string, details?: GraphData } | null | undefined} result @param {string} fallback */
	function recordedMessage(result, fallback) {
		if (!hasRecordedChange(result)) return 'No changes to record';
		return `Recorded ${result?.operation || fallback}`;
	}

	/** @param {GraphEdge} edge */
	function relationshipHistoryFilters(edge) {
		return {
			relationship_source: edge.data?.source || '',
			relationship_target: edge.data?.target || ''
		};
	}

	/** @param {GraphEdge} edge @param {GraphData} updates */
	function relationshipPayload(edge, updates = {}) {
		return {
			source_concept: edge.data?.source,
			target_concept: edge.data?.target,
			relation: edge.data?.relation || edge.label || 'related_to',
			actor: currentAuditActor(),
			reason: 'Educator graph curation',
			...updates
		};
	}

	function currentAuditActor() {
		const currentUser = $user || {};
		const name = String(currentUser.name || currentUser.data?.name || '').trim();
		const email = String(currentUser.email || currentUser.data?.email || '').trim();
		if (name && email && name !== email) return `${name} <${email}>`;
		return email || name || 'Unknown user';
	}

	/** @param {unknown} value */
	function parseTags(value) {
		return String(value || '')
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	/** @param {unknown} value @param {number} [length] */
	function truncate(value, length = 64) {
		const text = String(value || '');
		return text.length > length ? `${text.slice(0, length - 1)}...` : text;
	}

	/** @param {unknown} value */
	function normalizeSearch(value) {
		return String(value || '').trim().toLowerCase();
	}

	/** @param {unknown} value */
	function formatDate(value) {
		if (!value) return 'N/A';
		try {
			return new Date(String(value)).toLocaleString();
		} catch {
			return String(value);
		}
	}

	/** @param {unknown} value */
	function formatConfidence(value) {
		const numeric = Number(value);
		if (!Number.isFinite(numeric)) return 'N/A';
		if (numeric >= 0 && numeric <= 1) return `${Math.round(numeric * 100)}%`;
		return numeric.toFixed(2);
	}

	/** @param {GraphEdge} edge */
	function relationshipEvidence(edge) {
		return edge.data?.evidence || edge.data?.description || '';
	}

	/** @param {GraphEdge} edge */
	function relationshipSearchText(edge) {
		return normalizeSearch([
			edge.data?.source_label,
			edge.data?.source,
			edge.data?.target_label,
			edge.data?.target,
			edge.data?.relation,
			edge.data?.description,
			edge.data?.evidence,
			edge.data?.chunk_id,
			(edge.data?.tags || []).join(' ')
		].join(' '));
	}

	/** @param {GraphNode} node */
	function conceptSearchText(node) {
		return normalizeSearch([
			node.label,
			node.data?.name,
			node.data?.entity_type,
			node.data?.description,
			node.data?.notes,
			(node.data?.tags || []).join(' ')
		].join(' '));
	}

	/** @param {GraphEdge} edge */
	function matchesRelationshipReview(edge) {
		const needle = normalizeSearch(curationSearch);
		const state = String(edge.data?.verification_state || 'unverified');
		if (statusFilter !== 'all' && state !== statusFilter) return false;
		if (evidenceFilter === 'with_evidence' && !relationshipEvidence(edge)) return false;
		if (evidenceFilter === 'missing_evidence' && relationshipEvidence(edge)) return false;
		return !needle || relationshipSearchText(edge).includes(needle);
	}

	/** @param {GraphNode} node */
	function matchesConceptReview(node) {
		const needle = normalizeSearch(curationSearch);
		const state = String(node.data?.verification_state || 'unverified');
		if (statusFilter !== 'all' && state !== statusFilter) return false;
		return !needle || conceptSearchText(node).includes(needle);
	}

	/** @param {GraphNode[]} nodes @param {GraphEdge[]} edges */
	function buildReviewStats(nodes, edges) {
		const relationshipStates = countStates(edges.map((edge) => edge.data?.verification_state || 'unverified'));
		const conceptStates = countStates(nodes.map((node) => node.data?.verification_state || 'unverified'));
		return {
			concepts: nodes.length,
			relationships: edges.length,
			unreviewedRelationships: relationshipStates.unverified || 0,
			needsReviewRelationships: relationshipStates.needs_review || 0,
			approvedRelationships: relationshipStates.verified || 0,
			rejectedRelationships: relationshipStates.rejected || 0,
			unreviewedConcepts: conceptStates.unverified || 0,
			approvedConcepts: conceptStates.verified || 0,
			rejectedConcepts: conceptStates.rejected || 0
		};
	}

	/** @param {string[]} states */
	function countStates(states) {
		return states.reduce((counts, state) => {
			counts[state] = (counts[state] || 0) + 1;
			return counts;
		}, /** @type {Record<string, number>} */ ({}));
	}

	/** @param {GraphNode[]} nodes @param {string} type */
	function buildNodeOptions(nodes, type) {
		const byValue = new Map();
		for (const node of nodes.filter((item) => item.type === type).sort(compareNodesByLabel)) {
			const value = nodeSelectionValue(node);
			if (!value || byValue.has(value)) continue;
			byValue.set(value, { value, label: optionLabel(node), node });
		}
		return Array.from(byValue.values());
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

	/** @param {GraphOption} option @param {string} needle */
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

	/** @param {GraphOption[]} options @param {unknown} query @param {string[]} selectedValues @param {number} limit */
	function filterOptions(options, query, selectedValues, limit) {
		const needle = normalizeSearch(query);
		const selectedValuesSet = new Set(selectedValues);
		return options.filter((option) => !selectedValuesSet.has(option.value) && optionMatches(option, needle)).slice(0, limit);
	}

	/** @param {GraphOption[]} options @param {string} value */
	function selectedOptionLabel(options, value) {
		return options.find((option) => option.value === value)?.label || value;
	}

	/** @param {string[]} values @param {string} value */
	function addSelectedValue(values, value) {
		if (!value || values.includes(value)) return values;
		return [...values, value];
	}

	/** @param {GraphOption} option */
	function selectConceptOption(option) {
		conceptFilter = option.label;
		conceptSearchActive = false;
	}

	/** @param {GraphOption} option */
	function selectDocumentOption(option) {
		selectedDocumentValues = addSelectedValue(selectedDocumentValues, option.value);
		documentFilter = '';
		documentSearchActive = false;
	}

	/** @param {GraphOption} option */
	function selectChunkOption(option) {
		selectedChunkValues = addSelectedValue(selectedChunkValues, option.value);
		chunkFilter = '';
		chunkSearchActive = false;
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
	}

	function closeSuggestionsSoon() {
		setTimeout(() => {
			conceptSearchActive = false;
			documentSearchActive = false;
			chunkSearchActive = false;
		}, 120);
	}

	/** @param {KeyboardEvent} event @param {GraphOption[]} suggestions @param {(option: GraphOption) => void} selectOption */
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

	/** @param {GraphSnapshot} snapshot @param {GraphNode[]} nodes @param {GraphEdge[]} edges */
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

	/** @param {GraphNode} node @param {string} needle */
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
		const hasScope = selectedDocumentValues.length > 0 || selectedChunkValues.length > 0;
		if (!hasScope && !conceptNeedle) return graphWithCounts(snapshot, nodes, edges);

		const nodeById = new Map(nodes.map((node) => [node.id, node]));
		const documentNodeIdByDocumentId = new Map();
		for (const node of nodes) {
			if (node.type === 'document') documentNodeIdByDocumentId.set(String(node.data?.document_id || node.id), node.id);
		}

		const scopedDocumentNodeIds = new Set();
		const scopedChunkNodeIds = new Set();
		for (const node of nodes) {
			if (node.type === 'document' && selectedDocumentSet.has(nodeSelectionValue(node))) scopedDocumentNodeIds.add(node.id);
			if (node.type === 'chunk') {
				const documentId = String(node.data?.document_id || '');
				if (selectedChunkSet.has(nodeSelectionValue(node)) || selectedDocumentSet.has(documentId)) {
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
				if (edge.type === 'MENTIONS' && scopedChunkNodeIds.has(edge.source)) visibleConceptIds.add(edge.target);
				if (edge.type === 'DOCUMENT_MENTIONS' && scopedDocumentNodeIds.has(edge.source)) visibleConceptIds.add(edge.target);
			}
		} else {
			for (const node of nodes) {
				if (node.type === 'concept' && nodeMatchesSearch(node, conceptNeedle)) visibleConceptIds.add(node.id);
			}
		}

		const relevantChunkNodeIds = new Set();
		for (const edge of edges) {
			if (edge.type === 'MENTIONS' && scopedChunkNodeIds.has(edge.source) && visibleConceptIds.has(edge.target)) relevantChunkNodeIds.add(edge.source);
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
			for (const documentNodeId of scopedDocumentNodeIds) visibleNodeIds.add(documentNodeId);
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

	/** @param {GraphNode} node */
	function nodeSortLabel(node) {
		return String(node.data?.filename || node.data?.source_label || node.label || node.id || '');
	}

	/** @param {GraphNode} left @param {GraphNode} right */
	function compareNodesByLabel(left, right) {
		return graphCollator.compare(nodeSortLabel(left), nodeSortLabel(right));
	}

	/** @param {GraphNode} node */
	function conceptSortScore(node) {
		return Number(node.data?.chunk_count || 0);
	}

	/** @param {GraphNode} node */
	function sigmaNodeSize(node) {
		if (node.type === 'document') return 8;
		if (node.type === 'chunk') return 5.5;
		return Math.min(13, 7 + Math.log2(Math.max(1, Number(node.data?.chunk_count || 1))));
	}

	/** @param {GraphNode} node @param {boolean} conceptMode */
	function sigmaNodeLabel(node, conceptMode) {
		if (conceptMode) return truncate(node.label, 42);
		return truncate(node.label, node.type === 'concept' ? 34 : 24);
	}

	/** @param {GraphNode} node */
	function sigmaNodeBaseColor(node) {
		if (node.type === 'document') return '#4f46e5';
		if (node.type === 'chunk') return '#f59e0b';
		const state = node.data?.verification_state;
		if (state === 'verified') return '#059669';
		if (state === 'rejected') return '#dc2626';
		if (state === 'needs_review') return '#d97706';
		return '#2271b3';
	}

	/** @param {GraphEdge} edge */
	function sigmaEdgeBaseColor(edge) {
		if (edge.type === 'CONTAINS') return '#64748b';
		if (edge.type === 'DOCUMENT_MENTIONS') return '#7c3aed';
		if (edge.type === 'MENTIONS') return '#d97706';
		return '#2271b3';
	}

	/** @param {GraphEdge} edge */
	function sigmaEdgeBaseSize(edge) {
		if (edge.type === 'RELATES_TO') return 1.4;
		if (edge.type === 'CONTAINS') return 0.9;
		return 0.7;
	}

	/** @param {GraphEdge} edge @param {boolean} conceptMode */
	function sigmaEdgeLabel(edge, conceptMode) {
		if (edge.type !== 'RELATES_TO') return '';
		return truncate(edge.data?.relation || edge.label || edge.type, conceptMode ? 34 : 26);
	}

	/** @returns {Promise<SigmaModules>} */
	function loadSigmaModules() {
		if (!sigmaModulesPromise) {
			sigmaModulesPromise = Promise.all([
				import('graphology'),
				import('sigma'),
				import('graphology-layout-forceatlas2')
			]).then(([graphologyModule, sigmaModule, forceAtlasModule]) => ({
				GraphClass: graphologyModule.MultiDirectedGraph || graphologyModule.default,
				SigmaClass: sigmaModule.default,
				forceAtlas2: forceAtlasModule.default
			}));
		}
		return sigmaModulesPromise;
	}

	/** @param {GraphSnapshot} snapshot @param {boolean} conceptMode @param {HTMLDivElement} container */
	async function renderSigmaGraph(snapshot, conceptMode, container) {
		const requestId = ++sigmaRenderRequestId;
		sigmaReady = false;
		sigmaError = '';
		resetSigmaGraph();
		await tick();
		try {
			const modules = await loadSigmaModules();
			if (requestId !== sigmaRenderRequestId || sigmaContainer !== container || !showExplorer) return;
			const model = buildSigmaGraphModel(snapshot, modules.GraphClass, modules.forceAtlas2, conceptMode);
			sigmaGraphInstance = model.graphologyGraph;
			sigmaNodeLookup = model.nodeLookup;
			sigmaEdgeLookup = model.edgeLookup;
			sigmaRenderer = new modules.SigmaClass(model.graphologyGraph, container, {
				allowInvalidContainer: true,
				zIndex: true,
				renderEdgeLabels: true,
				enableEdgeClickEvents: true,
				enableEdgeHoverEvents: true,
				labelDensity: conceptMode ? 0.85 : 0.45,
				labelGridCellSize: conceptMode ? 80 : 96,
				labelRenderedSizeThreshold: conceptMode ? 7 : 9,
				labelFont: 'Inter, ui-sans-serif, system-ui, sans-serif',
				labelSize: 12,
				labelWeight: '600',
				edgeLabelSize: 10,
				edgeLabelWeight: '600',
				defaultEdgeColor: '#94a3b8',
				defaultNodeColor: '#2271b3'
			});
			attachSigmaEvents(sigmaRenderer);
			sigmaReady = true;
			updateSigmaReducers({
				selectedNodeId: selectedNode?.id || '',
				selectedRelationshipKey: selectedRelationshipId || selectedEdge?.id || '',
				selectedConcept: selectedConceptName,
				hoveredNode: hoveredNodeId,
				hoveredEdge: hoveredEdgeId,
				relationshipCount: visibleRelationshipCount,
				conceptMode
			});
		} catch (err) {
			if (requestId !== sigmaRenderRequestId) return;
			console.error('Error rendering Sigma graph:', err);
			sigmaError = err instanceof Error ? err.message : 'Failed to render graph';
			resetSigmaGraph();
		}
	}

	function destroySigmaGraph() {
		sigmaRenderRequestId += 1;
		resetSigmaGraph();
	}

	function resetSigmaGraph() {
		if (sigmaRenderer) sigmaRenderer.kill();
		sigmaRenderer = null;
		sigmaGraphInstance = null;
		sigmaNodeLookup = new Map();
		sigmaEdgeLookup = new Map();
		sigmaReady = false;
	}

	/** @param {GraphSnapshot} snapshot @param {any} GraphClass @param {any} forceAtlas2 @param {boolean} conceptMode @returns {SigmaGraphModel} */
	function buildSigmaGraphModel(snapshot, GraphClass, forceAtlas2, conceptMode) {
		const graphologyGraph = new GraphClass();
		const nodeLookup = /** @type {Map<string, GraphNode>} */ (new Map());
		const edgeLookup = /** @type {Map<string, GraphEdge>} */ (new Map());
		const nodes = snapshot.nodes || [];
		const nodeIds = new Set(nodes.map((node) => String(node.id)));
		const positions = sigmaInitialPositions(nodes, conceptMode);

		for (const node of nodes) {
			const id = String(node.id);
			const position = positions.get(id) || { x: 0, y: 0 };
			nodeLookup.set(id, node);
			graphologyGraph.addNode(id, {
				label: sigmaNodeLabel(node, conceptMode),
				x: position.x,
				y: position.y,
				size: sigmaNodeSize(node),
				color: sigmaNodeBaseColor(node),
				forceLabel: conceptMode || node.type === 'concept',
				nodeType: node.type,
				zIndex: node.type === 'concept' ? 3 : node.type === 'document' ? 2 : 1
			});
		}

		const usedEdgeKeys = new Set();
		for (const edge of snapshot.edges || []) {
			const source = String(edge.source);
			const target = String(edge.target);
			if (!nodeIds.has(source) || !nodeIds.has(target)) continue;
			const edgeKey = uniqueSigmaKey(edge.id || `${source}-${target}`, usedEdgeKeys);
			usedEdgeKeys.add(edgeKey);
			edgeLookup.set(edgeKey, edge);
			graphologyGraph.addDirectedEdgeWithKey(edgeKey, source, target, {
				label: sigmaEdgeLabel(edge, conceptMode),
				size: sigmaEdgeBaseSize(edge),
				color: sigmaEdgeBaseColor(edge),
				edgeType: edge.type,
				forceLabel: conceptMode && edge.type === 'RELATES_TO' && (snapshot.edges || []).length <= 30,
				zIndex: edge.type === 'RELATES_TO' ? 2 : 1
			});
		}

		runSigmaLayout(graphologyGraph, forceAtlas2, conceptMode, nodes.some((node) => node.type !== 'concept'));
		normalizeSigmaPositions(graphologyGraph);
		return { graphologyGraph, nodeLookup, edgeLookup };
	}

	/** @param {GraphNode[]} nodes @param {boolean} conceptMode */
	function sigmaInitialPositions(nodes, conceptMode) {
		const positions = /** @type {Map<string, { x: number, y: number }>} */ (new Map());
		const ordered = [...nodes].sort(compareNodesByLabel);
		if (conceptMode) {
			ordered.forEach((node, index) => {
				const distance = Math.sqrt(index + 1) * 2.2;
				const angle = index * sigmaGoldenAngle;
				positions.set(String(node.id), { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance });
			});
			return positions;
		}

		const grouped = {
			document: ordered.filter((node) => node.type === 'document'),
			chunk: ordered.filter((node) => node.type === 'chunk'),
			concept: ordered.filter((node) => node.type === 'concept')
		};
		for (const [type, group] of Object.entries(grouped)) {
			const x = type === 'document' ? -9 : type === 'chunk' ? 0 : 9;
			group.forEach((node, index) => {
				const centeredIndex = index - (group.length - 1) / 2;
				positions.set(String(node.id), {
					x: x + sigmaJitter(node.id, 0) * 0.6,
					y: centeredIndex * 2.1 + sigmaJitter(node.id, 1) * 0.6
				});
			});
		}
		return positions;
	}

	/** @param {unknown} value @param {number} salt */
	function sigmaJitter(value, salt) {
		const text = `${value || ''}:${salt}`;
		let hash = 0;
		for (let index = 0; index < text.length; index += 1) hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
		return (hash % 1000) / 1000 - 0.5;
	}

	/** @param {any} graphologyGraph @param {any} forceAtlas2 @param {boolean} conceptMode @param {boolean} hasStructuralNodes */
	function runSigmaLayout(graphologyGraph, forceAtlas2, conceptMode, hasStructuralNodes) {
		if (!forceAtlas2 || graphologyGraph.order <= 1) return;
		try {
			const inferredSettings = typeof forceAtlas2.inferSettings === 'function' ? forceAtlas2.inferSettings(graphologyGraph) : {};
			forceAtlas2.assign(graphologyGraph, {
				iterations: conceptMode ? 180 : 260,
				settings: {
					...inferredSettings,
					barnesHutOptimize: graphologyGraph.order > 80,
					scalingRatio: conceptMode ? 18 : hasStructuralNodes ? 28 : 22,
					gravity: conceptMode ? 0.35 : 0.7,
					slowDown: 8,
					strongGravityMode: false
				}
			});
		} catch (err) {
			console.warn('ForceAtlas2 layout failed, using initial graph positions:', err);
		}
	}

	/** @param {any} graphologyGraph */
	function normalizeSigmaPositions(graphologyGraph) {
		let minX = Infinity;
		let maxX = -Infinity;
		let minY = Infinity;
		let maxY = -Infinity;
		graphologyGraph.forEachNode((/** @type {string} */ _, /** @type {GraphData} */ attributes) => {
			minX = Math.min(minX, Number(attributes.x));
			maxX = Math.max(maxX, Number(attributes.x));
			minY = Math.min(minY, Number(attributes.y));
			maxY = Math.max(maxY, Number(attributes.y));
		});
		if (![minX, maxX, minY, maxY].every(Number.isFinite)) return;
		const centerX = (minX + maxX) / 2;
		const centerY = (minY + maxY) / 2;
		const scale = Math.max(maxX - minX, maxY - minY, 1);
		graphologyGraph.forEachNode((/** @type {string} */ node, /** @type {GraphData} */ attributes) => {
			graphologyGraph.mergeNodeAttributes(node, {
				x: ((Number(attributes.x) - centerX) / scale) * 0.9 + 0.5,
				y: ((Number(attributes.y) - centerY) / scale) * 0.9 + 0.5
			});
		});
	}

	/** @param {any} renderer */
	function attachSigmaEvents(renderer) {
		renderer.on('enterNode', (/** @type {{ node: string }} */ { node }) => (hoveredNodeId = String(node)));
		renderer.on('leaveNode', () => (hoveredNodeId = ''));
		renderer.on('clickNode', (/** @type {{ node: string }} */ { node }) => {
			const graphNode = sigmaNodeLookup.get(String(node));
			if (graphNode) selectGraphNode(graphNode);
		});
		renderer.on('enterEdge', (/** @type {{ edge: string }} */ { edge }) => {
			hoveredEdgeId = sigmaEdgeLookup.get(String(edge))?.id || String(edge);
		});
		renderer.on('leaveEdge', () => (hoveredEdgeId = ''));
		renderer.on('clickEdge', (/** @type {{ edge: string }} */ { edge }) => {
			const graphEdge = sigmaEdgeLookup.get(String(edge));
			if (graphEdge) selectGraphEdge(graphEdge);
		});
		renderer.on('clickStage', clearGraphSelection);
	}

	/** @param {{ selectedNodeId?: string, selectedRelationshipKey?: string, selectedConcept?: string, hoveredNode?: string, hoveredEdge?: string, relationshipCount?: number, conceptMode?: boolean }} state */
	function updateSigmaReducers(state = {}) {
		if (!sigmaRenderer) return;
		const focusNodeIds = sigmaFocusNodeIds(state);
		const hasFocus = Boolean(focusNodeIds.size || state.hoveredEdge || state.selectedRelationshipKey);
		sigmaRenderer.setSetting('nodeReducer', (/** @type {string} */ nodeKey, /** @type {GraphData} */ data) => {
			const node = sigmaNodeLookup.get(String(nodeKey));
			if (!node) return data;
			const highlighted = sigmaNodeHighlighted(node, state);
			const dimmed = hasFocus && focusNodeIds.size > 0 && !focusNodeIds.has(node.id);
			return {
				...data,
				color: highlighted ? '#1d4ed8' : dimmed ? '#cbd5e1' : sigmaNodeBaseColor(node),
				size: highlighted ? sigmaNodeSize(node) + 2.5 : sigmaNodeSize(node),
				label: sigmaShowNodeLabel(node, state, highlighted) ? sigmaNodeLabel(node, Boolean(state.conceptMode)) : '',
				forceLabel: highlighted || (Boolean(state.conceptMode) && node.type === 'concept'),
				zIndex: highlighted ? 10 : node.type === 'concept' ? 3 : 1
			};
		});
		sigmaRenderer.setSetting('edgeReducer', (/** @type {string} */ edgeKey, /** @type {GraphData} */ data) => {
			const edge = sigmaEdgeLookup.get(String(edgeKey));
			if (!edge) return data;
			const highlighted = sigmaEdgeHighlighted(edge, state);
			const dimmed = hasFocus && !highlighted;
			return {
				...data,
				color: highlighted ? '#1d4ed8' : dimmed ? '#cbd5e1' : sigmaEdgeBaseColor(edge),
				size: highlighted ? sigmaEdgeBaseSize(edge) + 1.8 : dimmed ? Math.max(0.4, sigmaEdgeBaseSize(edge) * 0.7) : sigmaEdgeBaseSize(edge),
				label: sigmaShowEdgeLabel(edge, state, highlighted) ? sigmaEdgeLabel(edge, Boolean(state.conceptMode)) : '',
				forceLabel: highlighted || sigmaShowEdgeLabel(edge, state, highlighted),
				zIndex: highlighted ? 9 : edge.type === 'RELATES_TO' ? 2 : 1
			};
		});
		sigmaRenderer.refresh();
	}

	/** @param {{ selectedNodeId?: string, selectedRelationshipKey?: string, selectedConcept?: string, hoveredNode?: string, hoveredEdge?: string }} state */
	function sigmaFocusNodeIds(state) {
		const ids = new Set();
		if (state.hoveredNode) ids.add(String(state.hoveredNode));
		if (state.selectedNodeId) ids.add(String(state.selectedNodeId));
		for (const edge of sigmaEdgeLookup.values()) {
			const matchesNode = state.hoveredNode && (edge.source === state.hoveredNode || edge.target === state.hoveredNode);
			const matchesEdge = sigmaEdgeHighlighted(edge, state);
			if (matchesNode || matchesEdge) {
				ids.add(String(edge.source));
				ids.add(String(edge.target));
			}
		}
		for (const node of sigmaNodeLookup.values()) {
			if (state.selectedConcept && node.data?.name === state.selectedConcept) ids.add(String(node.id));
		}
		return ids;
	}

	/** @param {GraphNode} node @param {{ selectedNodeId?: string, selectedConcept?: string, hoveredNode?: string }} state */
	function sigmaNodeHighlighted(node, state) {
		return state.selectedNodeId === node.id || state.hoveredNode === node.id || state.selectedConcept === node.data?.name;
	}

	/** @param {GraphNode} node @param {{ conceptMode?: boolean }} state @param {boolean} highlighted */
	function sigmaShowNodeLabel(node, state, highlighted) {
		if (highlighted || state.conceptMode) return true;
		return node.type !== 'chunk';
	}

	/** @param {GraphEdge} edge @param {{ selectedRelationshipKey?: string, hoveredNode?: string, hoveredEdge?: string }} state */
	function sigmaEdgeHighlighted(edge, state) {
		if (state.selectedRelationshipKey === edge.id || state.hoveredEdge === edge.id) return true;
		return Boolean(state.hoveredNode && (edge.source === state.hoveredNode || edge.target === state.hoveredNode));
	}

	/** @param {GraphEdge} edge @param {{ conceptMode?: boolean, relationshipCount?: number }} state @param {boolean} highlighted */
	function sigmaShowEdgeLabel(edge, state, highlighted) {
		if (edge.type !== 'RELATES_TO') return false;
		if (highlighted) return true;
		return Boolean(state.conceptMode && Number(state.relationshipCount || 0) <= 28);
	}

	/** @param {string} baseKey @param {Set<string>} usedKeys */
	function uniqueSigmaKey(baseKey, usedKeys) {
		const normalized = String(baseKey);
		if (!usedKeys.has(normalized)) return normalized;
		let index = 2;
		while (usedKeys.has(`${normalized}:${index}`)) index += 1;
		return `${normalized}:${index}`;
	}

</script>

<div class="space-y-5">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div class="flex flex-wrap gap-2 text-xs text-gray-600">
			<span class="rounded-full bg-blue-50 px-2 py-1 text-blue-700 ring-1 ring-blue-200">Concepts: {reviewStats.concepts}</span>
			<span class="rounded-full bg-slate-50 px-2 py-1 text-slate-700 ring-1 ring-slate-200">Relationships: {reviewStats.relationships}</span>
			<span class="rounded-full bg-amber-50 px-2 py-1 text-amber-800 ring-1 ring-amber-200">Needs review: {reviewStats.needsReviewRelationships}</span>
			<span class="rounded-full bg-gray-50 px-2 py-1 text-gray-700 ring-1 ring-gray-200">Unreviewed: {reviewStats.unreviewedRelationships}</span>
			<span class="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700 ring-1 ring-emerald-200">Approved: {reviewStats.approvedRelationships}</span>
			<span class="rounded-full bg-red-50 px-2 py-1 text-red-700 ring-1 ring-red-200">Expunged: {reviewStats.rejectedRelationships + reviewStats.rejectedConcepts}</span>
		</div>
		<div class="flex flex-wrap gap-2">
			<button type="button" onclick={loadGraph} disabled={graphLoading} class="inline-flex items-center rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">{graphLoading ? 'Refreshing...' : 'Refresh'}</button>
			<button type="button" onclick={() => (showExplorer = true)} class="inline-flex items-center rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover">Explore Graph</button>
		</div>
	</div>

	{#if graphError}<div class="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{graphError}</div>{/if}
	{#if curationMessage}<div class="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-700">{curationMessage}</div>{/if}
	{#if curationError}<div class="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{curationError}</div>{/if}

	<div class="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_260px_200px_190px]">
		<div>
			<label for="curation-search" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Search</label>
			<input id="curation-search" bind:value={curationSearch} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="concept, relationship, evidence" />
		</div>
		<div>
			<label for="curation-status" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Status</label>
			<select id="curation-status" bind:value={statusFilter} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand">
				<option value="all">All</option>
				<option value="unverified">Unreviewed</option>
				<option value="needs_review">Needs review</option>
				<option value="verified">Approved</option>
				<option value="rejected">Expunged</option>
			</select>
		</div>
		<div>
			<label for="curation-evidence" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Evidence</label>
			<select id="curation-evidence" bind:value={evidenceFilter} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand">
				<option value="all">All</option>
				<option value="with_evidence">With evidence</option>
				<option value="missing_evidence">Missing evidence</option>
			</select>
		</div>
		<div>
			<label for="graph-limit" class="block text-xs font-medium uppercase tracking-wide text-gray-500">Loaded concepts</label>
			<input id="graph-limit" type="number" min="1" max="200" bind:value={graphLimit} class="mt-1 block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" />
		</div>
	</div>

	<div class="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
		<section class="overflow-hidden rounded-lg border border-gray-200 bg-white">
			<div class="border-b border-gray-200 bg-gray-50 px-4">
				<nav class="flex gap-6" aria-label="Graph curation lists">
					<button type="button" onclick={() => (reviewTab = 'relationships')} class="border-b-2 px-1 py-3 text-sm font-medium {reviewTab === 'relationships' ? 'border-brand text-brand' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'}">Relationships</button>
					<button type="button" onclick={() => (reviewTab = 'concepts')} class="border-b-2 px-1 py-3 text-sm font-medium {reviewTab === 'concepts' ? 'border-brand text-brand' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'}">Concepts</button>
				</nav>
			</div>

			{#if graphLoading}
				<div class="p-8 text-center text-sm text-gray-500">Loading graph curation data...</div>
			{:else if reviewTab === 'relationships'}
				<div class="overflow-x-auto">
					<table class="min-w-full divide-y divide-gray-200 text-sm">
						<thead class="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
							<tr>
								<th class="px-4 py-3 text-left font-medium">Relationship</th>
								<th class="px-4 py-3 text-left font-medium">Evidence</th>
								<th class="px-4 py-3 text-left font-medium">Confidence</th>
								<th class="px-4 py-3 text-left font-medium">Status</th>
								{#if canModify}<th class="px-4 py-3 text-right font-medium">Actions</th>{/if}
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100 bg-white">
							{#if filteredRelationships.length === 0}
								<tr><td colspan={canModify ? 5 : 4} class="px-4 py-8 text-center text-gray-500">No relationships match the current filters.</td></tr>
							{:else}
								{#each filteredRelationships as edge (edge.id)}
									<tr class="cursor-pointer hover:bg-gray-50 {selectedRelationshipId === edge.id ? 'bg-blue-50/60' : ''}" onclick={() => selectRelationshipForReview(edge)}>
										<td class="px-4 py-3 align-top">
											<div class="font-medium text-gray-900">{edge.data.source_label}</div>
											<div class="mt-1 text-xs text-gray-500">{edge.data.relation}</div>
											<div class="mt-1 font-medium text-gray-900">{edge.data.target_label}</div>
										</td>
										<td class="max-w-xl px-4 py-3 align-top text-gray-700">
											{#if relationshipEvidence(edge)}<div>{truncate(relationshipEvidence(edge), 190)}</div>{:else}<span class="text-gray-400">No evidence</span>{/if}
											{#if edge.data.chunk_id}<div class="mt-1 break-all text-xs text-gray-400">{edge.data.chunk_id}</div>{/if}
										</td>
										<td class="px-4 py-3 align-top text-gray-700">{formatConfidence(edge.weight)}</td>
										<td class="px-4 py-3 align-top"><span class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 {stateClass(edge.data.verification_state)}">{stateLabel(edge.data.verification_state)}</span></td>
										{#if canModify}
											<td class="px-4 py-3 align-top text-right" onclick={(event) => event.stopPropagation()}>
												{#if edge.data.expunged}
													<button type="button" onclick={() => restoreExpungedItem(edge.data.expunge_event_id)} disabled={actionLoading} class="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Restore approved</button>
												{:else}
													<div class="flex justify-end gap-2">
														<button type="button" onclick={() => setRelationshipState(edge, 'verified')} disabled={actionLoading} class="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Approve</button>
														<button type="button" onclick={() => setRelationshipState(edge, 'needs_review')} disabled={actionLoading} class="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50">Review</button>
														<button type="button" onclick={() => setRelationshipState(edge, 'rejected')} disabled={actionLoading} class="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50">Expunge</button>
													</div>
												{/if}
											</td>
										{/if}
									</tr>
								{/each}
							{/if}
						</tbody>
					</table>
				</div>
			{:else}
				<div class="overflow-x-auto">
					<table class="min-w-full divide-y divide-gray-200 text-sm">
						<thead class="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
							<tr>
								<th class="px-4 py-3 text-left font-medium">Concept</th>
								<th class="px-4 py-3 text-left font-medium">Type</th>
								<th class="px-4 py-3 text-left font-medium">Mentions</th>
								<th class="px-4 py-3 text-left font-medium">Relationships</th>
								<th class="px-4 py-3 text-left font-medium">Confidence</th>
								<th class="px-4 py-3 text-left font-medium">Status</th>
								{#if canModify}<th class="px-4 py-3 text-right font-medium">Actions</th>{/if}
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100 bg-white">
							{#if filteredConcepts.length === 0}
								<tr><td colspan={canModify ? 7 : 6} class="px-4 py-8 text-center text-gray-500">No concepts match the current filters.</td></tr>
							{:else}
								{#each filteredConcepts as node (node.id)}
									{@const relCount = node.data.expunged ? Number(node.data.relationship_count || 0) : relationships.filter((edge) => edge.data.source === node.data.name || edge.data.target === node.data.name).length}
									<tr class="cursor-pointer hover:bg-gray-50 {selectedConceptName === node.data.name ? 'bg-blue-50/60' : ''}" onclick={() => selectConceptForReview(node)}>
										<td class="px-4 py-3 align-top font-medium text-gray-900">{node.label}</td>
										<td class="px-4 py-3 align-top text-gray-700">{node.data.entity_type || 'concept'}</td>
										<td class="px-4 py-3 align-top text-gray-700">{node.data.chunk_count || 0}</td>
										<td class="px-4 py-3 align-top text-gray-700">{relCount}</td>
										<td class="px-4 py-3 align-top text-gray-700">{formatConfidence(node.data.confidence)}</td>
										<td class="px-4 py-3 align-top"><span class="inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 {stateClass(node.data.verification_state)}">{stateLabel(node.data.verification_state)}</span></td>
										{#if canModify}
											<td class="px-4 py-3 align-top text-right" onclick={(event) => event.stopPropagation()}>
												{#if node.data.expunged}
													<button type="button" onclick={() => restoreExpungedItem(node.data.expunge_event_id)} disabled={actionLoading} class="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Restore approved</button>
												{:else}
													<div class="flex justify-end gap-2">
														<button type="button" onclick={() => setConceptState(node, 'verified')} disabled={actionLoading} class="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Approve</button>
														<button type="button" onclick={() => setConceptState(node, 'needs_review')} disabled={actionLoading} class="rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50">Review</button>
														<button type="button" onclick={() => setConceptState(node, 'rejected')} disabled={actionLoading} class="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50">Expunge</button>
													</div>
												{/if}
											</td>
										{/if}
									</tr>
								{/each}
							{/if}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		<aside class="space-y-4">
			<div class="rounded-lg border border-gray-200 bg-white p-4">
				<h4 class="text-sm font-semibold text-gray-900">Review Detail</h4>
				{#if selectedRelationship}
					<div class="mt-3 space-y-3 text-sm text-gray-700">
						<div>
							<div class="font-medium text-gray-900">{selectedRelationship.data.source_label}</div>
							<div class="text-xs text-gray-500">{selectedRelationship.data.relation}</div>
							<div class="font-medium text-gray-900">{selectedRelationship.data.target_label}</div>
						</div>
						<div class="flex flex-wrap gap-2 text-xs">
							<span class="rounded-full bg-gray-50 px-2 py-0.5 text-gray-700 ring-1 ring-gray-200">Confidence {formatConfidence(selectedRelationship.weight)}</span>
							<span class="rounded-full px-2 py-0.5 ring-1 {stateClass(selectedRelationship.data.verification_state)}">{stateLabel(selectedRelationship.data.verification_state)}</span>
						</div>
						<div>
							<div class="text-xs font-medium uppercase tracking-wide text-gray-500">Evidence</div>
							<p class="mt-1 rounded bg-gray-50 p-2 text-gray-700">{relationshipEvidence(selectedRelationship) || 'No evidence recorded.'}</p>
							{#if selectedRelationship.data.chunk_id}<div class="mt-1 break-all text-xs text-gray-400">{selectedRelationship.data.chunk_id}</div>{/if}
						</div>
					</div>

					{#if canModify && selectedRelationship.data.expunged}
						<div class="mt-4 border-t border-gray-200 pt-4">
							<button type="button" onclick={() => restoreExpungedItem(selectedRelationship.data.expunge_event_id)} disabled={actionLoading} class="inline-flex rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Restore approved</button>
						</div>
					{:else if canModify}
						<form class="mt-4 space-y-3 border-t border-gray-200 pt-4" onsubmit={(event) => { event.preventDefault(); submitRelationshipEdit(); }}>
							<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
								<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Relation<input bind:value={relationshipRelation} class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand" /></label>
								<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Confidence<input type="number" step="0.01" bind:value={relationshipWeight} class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand" /></label>
							</div>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Evidence<textarea bind:value={relationshipEvidenceText} rows="3" class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand"></textarea></label>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Description<textarea bind:value={relationshipDescription} rows="2" class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand"></textarea></label>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Notes<textarea bind:value={relationshipNotes} rows="2" class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand"></textarea></label>
							<input bind:value={relationshipTags} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="tags, comma separated" />
							<select bind:value={relationshipVerification} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand">
								<option value="unverified">Unreviewed</option>
								<option value="verified">Approved</option>
								<option value="needs_review">Needs review</option>
								<option value="rejected">Reject and expunge</option>
							</select>
							<div class="flex flex-wrap gap-2">
								<button type="submit" disabled={actionLoading} class="inline-flex rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50">Save</button>
								<button type="button" onclick={() => setRelationshipState(selectedRelationship, 'verified')} disabled={actionLoading} class="inline-flex rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Approve</button>
								<button type="button" onclick={() => setRelationshipState(selectedRelationship, 'rejected')} disabled={actionLoading} class="inline-flex rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50">Expunge</button>
							</div>
						</form>
					{/if}
				{:else if selectedConcept}
					<div class="mt-3 space-y-3 text-sm text-gray-700">
						<div>
							<div class="font-medium text-gray-900">{selectedConcept.label}</div>
							<div class="text-xs text-gray-500">{selectedConcept.data.name}</div>
						</div>
						<div class="flex flex-wrap gap-2 text-xs">
							<span class="rounded-full bg-gray-50 px-2 py-0.5 text-gray-700 ring-1 ring-gray-200">Mentions {selectedConcept.data.chunk_count || 0}</span>
							<span class="rounded-full bg-gray-50 px-2 py-0.5 text-gray-700 ring-1 ring-gray-200">Relationships {selectedConceptRelationships.length}</span>
							<span class="rounded-full px-2 py-0.5 ring-1 {stateClass(selectedConcept.data.verification_state)}">{stateLabel(selectedConcept.data.verification_state)}</span>
						</div>
						{#if selectedConcept.data.description}<p class="rounded bg-gray-50 p-2 text-gray-700">{selectedConcept.data.description}</p>{/if}
						<div class="max-h-48 overflow-y-auto divide-y divide-gray-100 rounded border border-gray-100">
							{#each selectedConceptRelationships as edge (edge.id)}
								<button type="button" onclick={() => selectRelationshipForReview(edge)} class="block w-full px-3 py-2 text-left text-xs hover:bg-gray-50">
									<span class="font-medium text-gray-900">{edge.data.source_label}</span>
									<span class="text-gray-500"> {edge.data.relation} </span>
									<span class="font-medium text-gray-900">{edge.data.target_label}</span>
								</button>
							{/each}
						</div>
					</div>

					{#if canModify && selectedConcept.data.expunged}
						<div class="mt-4 border-t border-gray-200 pt-4">
							<button type="button" onclick={() => restoreExpungedItem(selectedConcept.data.expunge_event_id)} disabled={actionLoading} class="inline-flex rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Restore approved</button>
						</div>
					{:else if canModify}
						<form class="mt-4 space-y-3 border-t border-gray-200 pt-4" onsubmit={(event) => { event.preventDefault(); submitConceptCuration(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Notes<textarea bind:value={conceptNotes} rows="3" class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand"></textarea></label>
							<input bind:value={conceptTags} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="tags, comma separated" />
							<select bind:value={conceptVerification} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand">
								<option value="unverified">Unreviewed</option>
								<option value="verified">Approved</option>
								<option value="needs_review">Needs review</option>
								<option value="rejected">Reject and expunge</option>
							</select>
							<div class="flex flex-wrap gap-2">
								<button type="submit" disabled={actionLoading} class="inline-flex rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50">Save</button>
								<button type="button" onclick={() => setConceptState(selectedConcept, 'verified')} disabled={actionLoading} class="inline-flex rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50">Approve</button>
								<button type="button" onclick={() => setConceptState(selectedConcept, 'rejected')} disabled={actionLoading} class="inline-flex rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50">Expunge</button>
							</div>
						</form>

						<form class="mt-4 space-y-3 border-t border-gray-200 pt-4" onsubmit={(event) => { event.preventDefault(); submitRename(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Rename<input bind:value={renameName} class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand" /></label>
							<button type="submit" disabled={actionLoading} class="inline-flex rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">Rename concept</button>
						</form>

						<form class="mt-4 space-y-3 border-t border-gray-200 pt-4" onsubmit={(event) => { event.preventDefault(); submitMerge(); }}>
							<label class="block text-xs font-medium uppercase tracking-wide text-gray-500">Merge Sources<input bind:value={mergeSources} class="mt-1 block w-full rounded-md border-gray-300 text-sm normal-case shadow-sm focus:border-brand focus:ring-brand" placeholder="source one, source two" /></label>
							<input bind:value={mergeTarget} class="block w-full rounded-md border-gray-300 text-sm shadow-sm focus:border-brand focus:ring-brand" placeholder="target concept" />
							<button type="submit" disabled={actionLoading} class="inline-flex rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">Merge concepts</button>
						</form>
					{/if}
				{:else}
					<p class="mt-3 text-sm text-gray-500">Select a relationship or concept.</p>
				{/if}
			</div>

			<div class="rounded-lg border border-gray-200 bg-white p-4">
				<div class="flex items-center justify-between gap-3">
					<h4 class="text-sm font-semibold text-gray-900">{historyTitle}</h4>
					<button type="button" onclick={() => loadHistory()} disabled={historyLoading} class="text-xs font-medium text-brand hover:text-brand-hover disabled:opacity-50">Refresh</button>
				</div>
				{#if historyError}<div class="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">{historyError}</div>{/if}
				<div class="mt-3 max-h-72 overflow-y-auto divide-y divide-gray-100">
					{#if historyLoading}
						<div class="py-4 text-sm text-gray-500">Loading history...</div>
					{:else if Object.keys(historyFilters).length === 0}
						<div class="py-4 text-sm text-gray-500">Select a relationship or concept to see its creation, updates, and expunge events.</div>
					{:else if changes.length === 0}
						<div class="py-4 text-sm text-gray-500">No changes found for this selection.</div>
					{:else}
						{#each changes as change (change.event_id)}
							{@const metaLine = changeMetaLine(change)}
							<div class="py-3 text-sm">
								<div class="flex items-center justify-between gap-3">
									<span class="font-medium text-gray-900">{changeOperationLabel(change)}</span>
									<span class="text-xs text-gray-500">{formatDate(change.timestamp)}</span>
								</div>
								{#if changeDetail(change)}<div class="mt-1 text-xs text-gray-700">{changeDetail(change)}</div>{/if}
								{#if metaLine}<div class="mt-1 text-xs text-gray-500">{metaLine}</div>{/if}
							</div>
						{/each}
					{/if}
				</div>
			</div>
		</aside>
	</div>

	{#if showExplorer}
		<div class="fixed inset-0 z-50 flex flex-col bg-white">
			<div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-5 py-3">
				<div>
					<h3 class="text-base font-semibold text-gray-900">Graph Explorer</h3>
					<div class="mt-1 flex flex-wrap gap-2 text-xs text-gray-500">
						<span>Concepts {visibleGraph.counts?.concepts || 0}</span>
						<span>Documents {visibleGraph.counts?.documents || 0}</span>
						<span>Chunks {visibleGraph.counts?.chunks || 0}</span>
						<span>Edges {visibleGraph.counts?.edges || 0}</span>
					</div>
				</div>
				<div class="flex flex-wrap gap-2">
					<button type="button" onclick={loadGraph} disabled={graphLoading} class="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">Refresh</button>
					<button type="button" onclick={() => (showExplorer = false)} class="rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-700">Close</button>
				</div>
			</div>

			<div class="grid flex-1 min-h-0 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px]">
				<div class="flex min-h-0 flex-col">
					<div class="border-b border-gray-200 bg-gray-50 px-5 py-3">
						<div class="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_auto]">
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

							<div class="flex flex-wrap items-end gap-3">
								<label class="inline-flex items-center gap-2 pb-2 text-sm text-gray-700"><input type="checkbox" bind:checked={onlyConcepts} class="rounded border-gray-300 text-brand focus:ring-brand" />Only concepts</label>
								{#if hasGraphFilters}<button type="button" onclick={clearGraphFilters} class="pb-2 text-sm font-medium text-gray-500 hover:text-gray-800">Clear</button>{/if}
							</div>
						</div>
					</div>

					<div class="relative min-h-0 flex-1 bg-white">
						{#if graphLoading}<div class="absolute inset-0 z-10 flex items-center justify-center bg-white/70 text-sm text-gray-600">Loading graph...</div>{/if}
						{#if visibleNodeCount === 0 && !graphLoading}
							<div class="flex h-full min-h-96 items-center justify-center text-sm text-gray-500">No graph data available.</div>
						{:else}
							<div bind:this={sigmaContainer} class="h-full w-full" style={`min-height: ${sigmaDefaultHeight}px;`} role="img" aria-label="Knowledge graph visualization"></div>
							{#if !sigmaReady && !sigmaError && !graphLoading}<div class="absolute inset-0 flex items-center justify-center bg-white/70 text-sm text-gray-600">Rendering graph...</div>{/if}
							{#if sigmaError}<div class="absolute inset-x-5 top-5 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{sigmaError}</div>{/if}
						{/if}
					</div>
				</div>

				<aside class="min-h-0 overflow-y-auto border-l border-gray-200 bg-gray-50 p-4">
					<h4 class="text-sm font-semibold text-gray-900">Selection</h4>
					{#if selectedRelationship}
						<div class="mt-3 space-y-2 text-sm text-gray-700">
							<div class="font-medium text-gray-900">{selectedRelationship.data.source_label}</div>
							<div>{selectedRelationship.data.relation}</div>
							<div class="font-medium text-gray-900">{selectedRelationship.data.target_label}</div>
							<p class="rounded bg-white p-2 text-xs text-gray-600">{relationshipEvidence(selectedRelationship) || 'No evidence recorded.'}</p>
							<button type="button" onclick={() => { showExplorer = false; reviewTab = 'relationships'; }} class="rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover">Review relationship</button>
						</div>
					{:else if selectedConcept}
						<div class="mt-3 space-y-2 text-sm text-gray-700">
							<div class="font-medium text-brand">{selectedConcept.label}</div>
							<div>Mentions: {selectedConcept.data.chunk_count || 0}</div>
							<div>Relationships: {selectedConceptRelationships.length}</div>
							<button type="button" onclick={() => { showExplorer = false; reviewTab = 'concepts'; }} class="rounded-md bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand-hover">Review concept</button>
						</div>
					{:else if selectedNode}
						<div class="mt-3 space-y-2 text-sm text-gray-700">
							<div class="font-medium text-gray-900">{selectedNode.label}</div>
							<div>{selectedNode.type}</div>
							{#if selectedNode.data?.text_preview}<p class="rounded bg-white p-2 text-xs text-gray-600">{selectedNode.data.text_preview}</p>{/if}
						</div>
					{:else}
						<p class="mt-3 text-sm text-gray-500">Select a node or relationship.</p>
					{/if}
				</aside>
			</div>
		</div>
	{/if}
</div>
