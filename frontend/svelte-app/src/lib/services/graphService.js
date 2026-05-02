import { apiJson } from '$lib/services/apiClient';

/** @typedef {string | number | boolean | null | undefined} QueryValue */
/** @typedef {Record<string, QueryValue>} QueryParams */
/** @typedef {Record<string, unknown>} GraphPayload */

/**
 * @param {QueryParams} params
 * @returns {string}
 */
function buildQuery(params = {}) {
	const search = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value === undefined || value === null || value === '') continue;
		search.set(key, String(value));
	}
	const query = search.toString();
	return query ? `?${query}` : '';
}

/**
 * @param {string | number} collectionId
 * @param {QueryParams} filters
 */
export function getGraphSnapshot(collectionId, filters = {}) {
	return apiJson(`/graph/collections/${collectionId}/snapshot${buildQuery(filters)}`);
}

/**
 * @param {string | number} collectionId
 * @param {QueryParams} filters
 */
export function listGraphChanges(collectionId, filters = {}) {
	return apiJson(`/graph/collections/${collectionId}/changes${buildQuery(filters)}`);
}

/**
 * @param {string | number} collectionId
 * @param {GraphPayload} payload
 */
export function auditGraphTrace(collectionId, payload) {
	return apiJson(`/graph/collections/${collectionId}/audit-trace`, {
		method: 'POST',
		body: JSON.stringify(payload)
	});
}

/**
 * @param {string | number} collectionId
 * @param {string} concept
 * @param {GraphPayload} payload
 */
export function renameGraphConcept(collectionId, concept, payload) {
	return apiJson(
		`/graph/collections/${collectionId}/concepts/${encodeURIComponent(concept)}/rename`,
		{
			method: 'PATCH',
			body: JSON.stringify(payload)
		}
	);
}

/**
 * @param {string | number} collectionId
 * @param {GraphPayload} payload
 */
export function mergeGraphConcepts(collectionId, payload) {
	return apiJson(`/graph/collections/${collectionId}/concepts/merge`, {
		method: 'POST',
		body: JSON.stringify(payload)
	});
}

/**
 * @param {string | number} collectionId
 * @param {string} concept
 * @param {GraphPayload} payload
 */
export function curateGraphConcept(collectionId, concept, payload) {
	return apiJson(
		`/graph/collections/${collectionId}/concepts/${encodeURIComponent(concept)}/curation`,
		{
			method: 'PATCH',
			body: JSON.stringify(payload)
		}
	);
}

/**
 * @param {string | number} collectionId
 * @param {GraphPayload} payload
 */
export function editGraphRelationship(collectionId, payload) {
	return apiJson(`/graph/collections/${collectionId}/relationships`, {
		method: 'PATCH',
		body: JSON.stringify(payload)
	});
}

/**
 * @param {string | number} collectionId
 * @param {GraphPayload} payload
 */
export function curateGraphRelationship(collectionId, payload) {
	return apiJson(`/graph/collections/${collectionId}/relationships/curation`, {
		method: 'PATCH',
		body: JSON.stringify(payload)
	});
}
