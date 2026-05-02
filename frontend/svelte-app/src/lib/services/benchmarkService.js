import { apiJson } from '$lib/services/apiClient';

/** @typedef {Record<string, unknown>} BenchmarkPayload */

/**
 * @returns {Promise<any[]>}
 */
export function listBenchmarkDatasets() {
	return apiJson('/benchmarks/datasets');
}

/**
 * @param {string} datasetId
 * @returns {Promise<any>}
 */
export function getBenchmarkDataset(datasetId) {
	return apiJson(`/benchmarks/datasets/${encodeURIComponent(datasetId)}`);
}

/**
 * @param {string | number} collectionId
 * @param {BenchmarkPayload} payload
 * @returns {Promise<any>}
 */
export function runCollectionBenchmark(collectionId, payload) {
	return apiJson(`/benchmarks/collections/${collectionId}/run`, {
		method: 'POST',
		body: JSON.stringify(payload)
	});
}

/**
 * @param {string | number} collectionId
 * @param {BenchmarkPayload} payload
 * @returns {Promise<any>}
 */
export function runAllCollectionBenchmarks(collectionId, payload) {
	return apiJson(`/benchmarks/collections/${collectionId}/run-all`, {
		method: 'POST',
		body: JSON.stringify(payload)
	});
}
