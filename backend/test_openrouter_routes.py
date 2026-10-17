import copy
import sqlite3
from pathlib import Path
import tempfile
import unittest

from dervo_connections import Rejected
from openrouter_connection import ConnectionError
from openrouter_routes import Routes, endpoints, model_id, price, pricing
from release_profiles import stage
from test_openrouter_budget import OWNER, OTHER

KEY = 'sk-or-' + 'v1-' + 'fixture' * 8
MODEL = 'provider/model'


def response(**changes):
    return {'id': MODEL, 'endpoints': [{'model_id': MODEL, 'tag': 'provider', 'context_length': 32000,
        'max_completion_tokens': 4096, 'status': 0, 'supported_parameters': ['max_tokens','tools'],
        'pricing': {'prompt': '0.000001', 'completion': '0.000002', 'request': '0'}, **changes}]}


class EndpointTests(unittest.TestCase):
    def test_family_aggregates_prices_limits_and_parameters(self):
        data = response()
        variant = response(tag='provider/turbo', context_length=16000, max_completion_tokens=1024,
            supported_parameters=['max_tokens'], pricing={'prompt':'0.000003','completion':'0.000001','request':'0.01'})['endpoints'][0]
        data['endpoints'].append(variant)
        family, exact = endpoints(data, MODEL, KEY)['endpoints']
        self.assertEqual(family['coveredProviders'], ['provider', 'provider/turbo'])
        self.assertEqual(family['contextTokens'], 16000)
        self.assertEqual(family['budgetContextTokens'], 32000)
        self.assertEqual(family['maxOutputTokens'], 1024)
        self.assertEqual(family['parameters'], ['max_tokens'])
        self.assertEqual(family['pricing'], {'prompt':'0.000003','completion':'0.000002','request':'0.01'})
        self.assertEqual(exact['pricing'], variant['pricing'])
        self.assertEqual(exact['coveredProviders'], ['provider/turbo'])

    def test_unknown_or_unavailable_variant_blocks_family(self):
        for change in ({'pricing':None}, {'status':-1}, {'supported_parameters':['tools']},
                       {'pricing':{'prompt':'0','completion':'0','request':'0','image':'1'}}):
            data = response()
            data['endpoints'].append(response(tag='provider/turbo', **change)['endpoints'][0])
            family = endpoints(data, MODEL, KEY)['endpoints'][0]
            self.assertFalse(family['selectable'])
            self.assertTrue(family['reason'])

    def test_exact_model_endpoint_and_prices(self):
        result = endpoints(response(), MODEL, KEY)
        self.assertTrue(result['endpoints'][0]['selectable'])
        self.assertEqual(result['endpoints'][0]['pricing']['prompt'], '0.000001')

    def test_unknown_prices_are_unselectable_not_free(self):
        for rates in ({}, {'prompt':'0'}, {'prompt':'NaN','completion':'1','request':'0'}):
            row = endpoints(response(pricing=rates), MODEL, KEY)['endpoints'][0]
            self.assertFalse(row['selectable'])
            self.assertIsNone(row['pricing'])

    def test_public_pricing_metadata_and_context_tiers_use_highest_rates(self):
        rates = {'prompt':'0.000003','completion':'0.000015','input_cache_read':'0.0000003',
                 'input_cache_write':'0.00000375','discount':0,
                 'overrides':[{'min_prompt_tokens':200000,'prompt':'0.000006',
                               'completion':'0.0000225','input_cache_write':'0.0000075'}]}
        row = endpoints(response(pricing=rates), MODEL, KEY)['endpoints'][0]
        self.assertTrue(row['selectable'])
        self.assertEqual(row['pricing'], {'prompt':'0.000006','completion':'0.0000225',
            'input_cache_read':'0.0000003','input_cache_write':'0.0000075'})
        self.assertNotIn('request', row['pricing'])
        rates['overrides'].append({'min_prompt_tokens':500000,'prompt':'0.000004'})
        self.assertEqual(pricing(rates)['prompt'], '0.000006')

    def test_invalid_discount_and_unknown_tier_conditions_stay_unselectable(self):
        base = {'prompt':'0.1','completion':'0.2'}
        for extra in ({'discount':True}, {'discount':float('nan')}, {'discount':2}, {'discount':'0'},
                      {'overrides':None}, {'overrides':[{'min_prompt_tokens':True,'prompt':'0.3'}]},
                      {'overrides':[{'min_prompt_tokens':100,'unknown_condition':'0.3'}]},
                      {'overrides':[{'min_prompt_tokens':100,'prompt':'NaN'}]}):
            with self.subTest(extra=extra):
                self.assertFalse(endpoints(response(pricing={**base, **extra}), MODEL, KEY)['endpoints'][0]['selectable'])

    def test_family_tiers_take_maximum_across_all_variants(self):
        data = response(pricing={'prompt':'0.1','completion':'0.2','discount':0})
        data['endpoints'].append(response(tag='provider/turbo', context_length=64000,
            pricing={'prompt':'0.05','completion':'0.1','discount':0.5,
                     'overrides':[{'min_prompt_tokens':100,'prompt':'0.3'}]})['endpoints'][0])
        family = endpoints(data, MODEL, KEY)['endpoints'][0]
        self.assertTrue(family['selectable'])
        self.assertEqual(family['pricing']['prompt'], '0.3')
        self.assertEqual(family['pricing']['completion'], '0.2')
        self.assertEqual(family['budgetContextTokens'], 64000)

    def test_unavailable_or_missing_parameter_stays_visible_but_unselectable(self):
        for change in ({'status': -1}, {'supported_parameters': ['temperature']}):
            row = endpoints(response(**change), MODEL, KEY)['endpoints'][0]
            self.assertFalse(row['selectable'])
            self.assertTrue(row['reason'])

    def test_mismatched_duplicate_or_malformed_endpoints_fail_closed(self):
        for change in ({'model_id':'other/model'}, {'context_length':True}, {'max_completion_tokens':33000},
                       {'tag':KEY}, {'supported_parameters':[KEY]}):
            with self.assertRaises(ConnectionError): endpoints(response(**change), MODEL, KEY)
        data = response()
        data['endpoints'] *= 2
        with self.assertRaises(ConnectionError): endpoints(data, MODEL, KEY)

    def test_price_precision_does_not_turn_unknown_prices_into_zero(self):
        self.assertEqual(price('1e-9'), '0.000000001')
        for value in ('1e-999','1e999','-1','NaN',True,1,'0.0000000000000000001'):
            with self.assertRaises(ValueError): price(value)

    def test_model_path_traversal_query_and_url_are_rejected(self):
        for value in ('../key','a/../key','a//b','a/b?key=x','https://foreign.invalid/model','a/b#fragment'):
            with self.assertRaises(Rejected): model_id(value)


class RouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        stage(self.root, 'personal-experimental')
        self.db = sqlite3.connect(self.root/'routes.sqlite3')
        self.addCleanup(self.db.close)
        self.routes = Routes(self.db, self.root)
        self.checked = endpoints(response(), MODEL, KEY)

    def save(self, **changes):
        fields = dict(checked=self.checked, model=MODEL, provider='provider', max_output=1024, revision=0)
        fields.update(changes)
        return self.routes.save(OWNER, **fields)

    def test_saved_route_is_durable_and_has_no_fallbacks(self):
        self.assertIsNone(self.routes.status(OWNER)['selection'])
        self.save()
        recreated = Routes(self.db, self.root)
        params = recreated.request_parameters(OWNER)
        self.assertEqual(params, {'model':MODEL, 'max_tokens':1024,
            'provider':{'only':['provider'], 'allow_fallbacks':False, 'require_parameters':True}})

    def test_unknown_model_provider_unsupported_tokens_or_stale_revision_cannot_save(self):
        for fields in ({'model':'other/model'}, {'provider':'other'}, {'checked':None}, {'max_output':4097},
                       {'max_output':True}, {'max_output':0}, {'revision':1}):
            with self.assertRaises(Rejected): self.save(**fields)
        self.save()
        with self.assertRaises(Rejected): self.save()

    def test_unselectable_endpoint_is_refused(self):
        self.checked['endpoints'][0]['selectable'] = False
        with self.assertRaises(Rejected): self.save()

    def test_owner_and_profile_are_rechecked(self):
        self.save()
        self.assertIsNone(self.routes.status(OTHER)['selection'])
        with self.assertRaises(Rejected): self.routes.request_parameters(OTHER)
        stage(self.root, 'private-beta')
        with self.assertRaises(ValueError): self.routes.status(OWNER)
        with self.assertRaises(ValueError): self.save(revision=1)

    def test_construction_does_not_commit_existing_transaction(self):
        self.db.execute('CREATE TABLE pending(value TEXT)')
        self.db.execute("INSERT INTO pending VALUES ('not committed')")
        with self.assertRaises(Rejected): Routes(self.db, self.root)
        with self.assertRaises(Rejected): self.save()
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM pending').fetchone()[0], 0)
