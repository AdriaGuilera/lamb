import { describe, expect, it } from 'vitest';

import {
	buildExpungedConcepts,
	buildExpungedRelationships,
	changeDetail,
	changeMetaLine,
	changeOperationLabel,
	changePayload,
	stateLabel,
	verificationTransition
} from './graphCuration.js';

describe('graph curation history helpers', () => {
	it('renders status transitions while hiding the generic curation reason', () => {
		const change = {
			operation: 'manual_curate_relationship',
			payload_json: JSON.stringify({
				reason: 'Educator graph curation',
				verification_state: 'verified'
			})
		};

		expect(changeOperationLabel(change)).toBe('Relationship status updated');
		expect(changeDetail(change)).toBe('Unreviewed -> Approved');
		expect(
			verificationTransition({
				old_verification_state: 'verified',
				verification_state: 'needs_review'
			})
		).toBe('Approved -> Needs review');
	});

	it('keeps useful actor attribution and hides internal API actors', () => {
		expect(
			changeMetaLine({
				filename: 'resilience.md',
				actor: 'Admin User <admin@owi.com>'
			})
		).toBe('resilience.md / Admin User <admin@owi.com>');
		expect(changeMetaLine({ filename: 'resilience.md', actor: 'graph-curation-api' })).toBe(
			'resilience.md'
		);
		expect(changeMetaLine({ actor: 'lamb-ingestion-pipeline' })).toBe('Ingestion pipeline');
	});

	it('parses malformed change payloads defensively', () => {
		expect(changePayload({ payload_json: '{not-json' })).toEqual({});
		expect(changeDetail({ operation: 'manual_curate_concept', payload_json: '{not-json' })).toBe(
			''
		);
		expect(stateLabel('rejected')).toBe('Expunged');
	});

	it('rebuilds expunged relationships from relationship and concept audit events', () => {
		const changes = [
			{
				event_id: 'rel-1',
				operation: 'manual_expunge_relationship',
				timestamp: '2026-05-17T10:00:00Z',
				payload_json: JSON.stringify({
					source: 'harbor aquarium flood',
					target: 'city resilience register',
					relation: 'not_connected_to',
					old_weight: 0.7,
					old_evidence: 'No registry entry exists.',
					old_tags: ['manual']
				})
			},
			{
				event_id: 'concept-1',
				operation: 'manual_expunge_concept',
				payload_json: JSON.stringify({
					concept: 'flood office',
					removed_relationships: [
						{
							source: 'flood office',
							target: 'harbor aquarium flood',
							relation: 'owned_by',
							weight: 1
						}
					]
				})
			}
		];

		const rows = buildExpungedRelationships(changes, [
			{ data: { source: 'active', target: 'edge', relation: 'related_to' } }
		]);

		expect(rows).toHaveLength(2);
		expect(rows[0]).toMatchObject({
			id: 'expunged-relationship-rel-1',
			type: 'RELATES_TO',
			weight: 0.7,
			data: {
				source: 'harbor aquarium flood',
				target: 'city resilience register',
				relation: 'not_connected_to',
				verification_state: 'rejected',
				expunged: true,
				expunge_event_id: 'rel-1'
			}
		});
		expect(rows[1].data.expunged_by_concept).toBe('flood office');
	});

	it('does not show expunged audit rows after the active graph item is restored', () => {
		const changes = [
			{
				event_id: 'rel-1',
				operation: 'manual_expunge_relationship',
				payload_json: JSON.stringify({
					source: 'harbor aquarium flood',
					target: 'city resilience register',
					relation: 'not_connected_to'
				})
			}
		];

		const rows = buildExpungedRelationships(changes, [
			{
				data: {
					source: 'Harbor Aquarium Flood',
					target: 'City Resilience Register',
					relation: 'not_connected_to'
				}
			}
		]);

		expect(rows).toEqual([]);
	});

	it('rebuilds expunged concepts with mention and relationship counts', () => {
		const rows = buildExpungedConcepts(
			[
				{
					event_id: 'concept-1',
					operation: 'manual_expunge_concept',
					timestamp: '2026-05-17T10:00:00Z',
					payload_json: JSON.stringify({
						concept: 'flood office',
						old_notes: 'Duplicate concept',
						old_tags: ['duplicate'],
						removed_chunk_mentions: ['chunk-1', 'chunk-2'],
						removed_relationships: [
							{ source: 'flood office', target: 'harbor aquarium flood', relation: 'owned_by' }
						]
					})
				}
			],
			[]
		);

		expect(rows).toEqual([
			expect.objectContaining({
				id: 'expunged-concept-concept-1',
				label: 'flood office',
				data: expect.objectContaining({
					chunk_count: 2,
					relationship_count: 1,
					verification_state: 'rejected',
					expunged: true,
					expunge_event_id: 'concept-1'
				})
			})
		]);
	});
});
