from collections import defaultdict
from csv import DictReader
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.census_disclosure_inventory import (
    distributions, editions_for_cells, historical_provenance, old_applicability, primary_disclosure,
    provenance_report, run,
)


def cell(state='UNKNOWN', source='SOURCE_ACQUIRED', metadata=''):
    return {'value_state': state, 'source_search_status': source, 'metadata_evidence': metadata}


class DisclosureInventoryTests(unittest.TestCase):
    def test_project_capture_alone_cannot_classify_disclosure_or_applicability(self):
        assert primary_disclosure(cell(), []) == 'SEARCH_HISTORY_UNKNOWN'
        assert primary_disclosure(cell(source='NOT_SEARCHED'), []) == 'NOT_SEARCHED'
        assert primary_disclosure(cell(state='PRESENT'), [{'reason': 'CONFLICT'}]) == 'PRESENT'
        assert primary_disclosure(cell(), [{'reason': 'PARTIAL_DISCLOSURE'}, {'reason': 'CONFLICT'}]) == 'CONFLICT'
        with self.assertRaisesRegex(ValueError, 'affirmative applicability'):
            primary_disclosure(cell(state='NOT_APPLICABLE'), [])
        assert primary_disclosure(cell(state='NOT_APPLICABLE', metadata=json.dumps({
            'applicability': 'AFFIRMATIVELY_INAPPLICABLE',
        })), []) == 'NOT_APPLICABLE'


    def test_hash_on_another_project_or_different_hash_is_not_the_expected_original(self):
        exact, wrong = 'a' * 64, 'b' * 64
        url = 'https://test.example.org/original.pdf'
        key = 'p1', 'Ore'
        old = {key: {'source_sha256': exact, 'source_url': url, 'value': '1', 'locator': 'p2', 'effective_date': '2025-12'}}
        links, absent = editions_for_cells(
            old, {key: cell()}, {'Ore': {'column_id': 'ore_mt', 'display_group': 'resource'}},
            {'p1': {wrong}}, {exact: {'p2'}}, {('p1', url): [{
                'url': url, 'status': 'PUBLIC_CAPTURED', 'acquired_at': '2026-11-28', 'observed_sha256': wrong,
            }]}, {},
        )
        assert links[key]['missing'] == [exact]
        assert absent['p1', exact]['eligible_other_project_ids'] == ['p2']
        assert absent['p1', exact]['edition_status'] == 'EXACT_SHA_NOT_ELIGIBLY_LINKED_TO_PROJECT'
        assert absent['p1', exact]['observed_different_sha256'] == [wrong]
        assert absent['p1', exact]['affected_current_unknown_cells'] == 1


    def test_many_legacy_hashes_and_urls_do_not_imply_same_order_provenance(self):
        have, missing = 'a' * 64, 'b' * 64
        urls = 'https://test.example.org/one.pdf', 'https://test.example.org/two.pdf'
        key = 'p1', 'Ore'
        old = {key: {'source_sha256': f'{have};{missing}', 'source_url': ';'.join(urls), 'value': '1',
                     'locator': 'p2', 'effective_date': '2025-12'}}
        links, absent = editions_for_cells(old, {key: cell()}, {'Ore': {'column_id': 'ore_mt', 'display_group': 'resource'}},
                                           {'p1': {have}}, defaultdict(set), {}, {})
        assert links[key]['coverage'] == 'PARTIAL_EXACT_EDITIONS'
        assert absent['p1', missing]['expected_urls'] == []
        assert absent['p1', missing]['unbound_url_hints'] == list(urls)


    def test_historical_status_is_kept_independent_of_current_disclosure(self):
        row = dict(project_id='p1', field_id='ore_mt', column='Ore', value_state='UNKNOWN', evidence_state='UNSUPPORTED',
                   historical_v2_status='NOT_APPLICABLE_NO_MRE', baseline_reason='NO_SELECTED_MRE',
                   disclosure_reason='SEARCH_HISTORY_UNKNOWN', eligible_project_source_editions=1)
        census = {'science_views': {'verified_resource_presence': {}, 'scoped_reported_resources': {'p1': {}}}}
        audited, = list(old_applicability([row], census))
        assert audited['separate_scoped_resource_on_project'] is True
        assert audited['affirmative_inapplicability_in_baseline'] is False
        assert audited['effect_on_formal_value'] == 'NONE'
        assert {item['scope'] for item in distributions([row])} == {'TOTAL', 'PROJECT', 'FIELD', 'CELL'}


    def test_provenance_links_capture_method_and_catalog_channel_by_exact_project_and_edition(self):
        original, missing, other, capture = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64
        url = 'https://test.example.org/a.pdf'
        event = {'status': 'PUBLIC_CAPTURED', 'receipt_sha256': capture, 'sha256': original,
                 'kind': 'pdf', 'method': 'ANONYMOUS_HTTPS_GET', 'requested_url': url, 'project_ids': ['p1', 'p2']}
        edition = {'receipt_sha256': capture, 'sha256': original, 'kind': 'pdf', 'project_ids': ['p1', 'p2'],
                   'temporal_decision': {'status': 'ELIGIBLE'}}
        manifest = {'events': [event], 'editions': [edition]}
        catalog = {'schema_version': 'source-seed-catalog-v1', 'seed_urls': [
            {'url': url, 'project_ids': ['p1', 'p3'], 'channel': 'OFFICIAL_EXCHANGE'},
            {'url': url.replace('a.pdf', 'b.pdf'), 'project_ids': ['p2'], 'channel': 'OFFICIAL_GOVERNMENT'},
        ]}
        proof = {'value': '1', 'method': 'ORIGINAL_QUANTITY', 'locator': 'p2', 'pdf_page': '2', 'source_url': url,
                 'observation_ids': '["observation-1"]', 'attribute_ids': '[]'}
        rows = {('p1', 'ore'): {**proof, 'source_sha256': f'{original};{missing}'},
                ('p2', 'ore'): {**proof, 'source_sha256': original},
                ('p3', 'ore'): {**proof, 'source_sha256': original},
                ('p1', 'grade'): {**proof, 'source_sha256': ''},
                ('p1', 'contained'): {**proof, 'value': '', 'source_sha256': other}}
        report = historical_provenance(rows, manifest, catalog)
        assert report['populated_cells'] == 4 and report['distinct_historical_sha256'] == 2
        assert report['historical_methods']['ORIGINAL_QUANTITY']['edition_alignment'] == {
            'ALL_EXACT_EDITIONS': 1, 'PARTIAL_EXACT_EDITIONS': 1, 'NO_ARCHIVED_EDITION_SHA': 1,
            'EXACT_EDITION_NOT_LINKED_TO_PROJECT': 1,
        }
        assert report['historical_methods']['ORIGINAL_QUANTITY']['populated_cells_by_exact_acquisition_method'] == {
            'ANONYMOUS_HTTPS_GET': 2,
        }
        assert report['project_sha_groups_by_edition_state'] == {
            'EXACT_ELIGIBLE': 2, 'EXACT_ELIGIBLE_OTHER_PROJECT': 1, 'NO_EXACT_ELIGIBLE_EDITION': 1,
        }
        assert report['capture_counts_by_acquisition_method'] == {'ANONYMOUS_HTTPS_GET': 1}
        assert report['project_sha_counts_by_catalog_channel'] == {'OFFICIAL_EXCHANGE': 1, 'NOT_IN_PINNED_CATALOG': 1}
        by_project = {(r['project_id'], r['source_sha256']): r for r in report['project_sha_groups']}
        assert by_project['p3', original]['catalog_channels_by_capture_sha256'] == {}
        with self.assertRaisesRegex(ValueError, 'Capture does not bind'):
            historical_provenance(rows, {**manifest, 'events': [{**event, 'sha256': other}]}, catalog)


    @unittest.skipUnless(os.getenv('AU_REE_FILL_WORKSPACE') and os.getenv('AU_REE_FILL_V2_ROOT'),
                         'requires both the exact V2 archive and Stage 6 CAS replicas')
    def test_frozen_originals_and_stage6_archives(self):
        workspace = Path(os.environ['AU_REE_FILL_WORKSPACE'])
        cold = workspace / 'work/census-fill/t0'
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        out = Path(temporary.name) / 'inventory'
        audit = run(workspace, Path(os.environ['AU_REE_FILL_V2_ROOT']), [cold / 'cold-a', cold / 'cold-b'],
                    cold / 'comparison', out)
        assert audit['cold_science'] == {'status': 'PASS', 'runs': 2, 'outputs_each': 8, 'core_grid_unchanged': 4743}
        assert audit['value_states'] == {'PRESENT': 1141, 'UNKNOWN': 3602, 'NOT_APPLICABLE': 0}
        assert audit['source_relationships']['distinct_expected_sha_missing_project_link'] == 57
        assert audit['historical_v2_not_applicable_audit']['current_states'] == {'UNKNOWN': 1715}
        with (out / 'census_gap_inventory.csv').open(newline='', encoding='utf-8') as stream:
            rows = {(row['project_id'], row['field_id']): row for row in DictReader(stream)}
        assert len(rows) == 4743
        assert rows['AU-3651cf8448b9227adcf5', 'ore_mt']['disclosure_reason'] == 'DISCLOSED_NOT_EXTRACTED'
        assert rows['AU-efff91a06cf344122d88', 'magnet_oxide_kt']['disclosure_reason'] == 'SEARCHED_NOT_DISCLOSED'
        assert rows['AU-0098e17a63e7c05c213f', 'hree8_kt']['disclosure_reason'] == 'PARTIAL_DISCLOSURE'


    @unittest.skipUnless(os.getenv('AU_REE_FILL_WORKSPACE') and os.getenv('AU_REE_FILL_V2_ROOT'),
                         'requires both the exact V2 archive and Stage 6 CAS replicas')
    def test_frozen_provenance_report(self):
        workspace = Path(os.environ['AU_REE_FILL_WORKSPACE'])
        cold = workspace / 'work/census-fill/t0'
        report = provenance_report(workspace, Path(os.environ['AU_REE_FILL_V2_ROOT']),
                                   [cold / 'cold-a', cold / 'cold-b'], cold / 'comparison')
        assert report['status'] == 'PASS' and report['populated_cells'] == 2500
        assert report['distinct_historical_sha256'] == 193 and len(report['historical_methods']) == 68
        assert report['project_sha_groups_by_edition_state'] == {
            'EXACT_ELIGIBLE': 161, 'EXACT_ELIGIBLE_OTHER_PROJECT': 15, 'NO_EXACT_ELIGIBLE_EDITION': 119,
        }
        assert report['capture_counts_by_acquisition_method'] == {'ANONYMOUS_HTTPS_GET': 146}
        assert report['capture_counts_by_source_kind'] == {'pdf': 144, 'zip': 2}
        assert sum(method['populated_cells_by_exact_acquisition_method'].get('ANONYMOUS_HTTPS_GET', 0)
                   for method in report['historical_methods'].values()) == 1859
