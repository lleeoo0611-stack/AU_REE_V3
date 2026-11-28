from collections import defaultdict
from csv import DictReader
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.census_disclosure_inventory import distributions, editions_for_cells, old_applicability, primary_disclosure, run


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
