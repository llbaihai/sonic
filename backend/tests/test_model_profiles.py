import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import providers
from app import app


class ModelProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'profiles.json'
        self.path_patch = patch.dict(os.environ, {'SONIC_MODEL_PROFILES_PATH': str(self.path)})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.client = TestClient(app)
        self.draft = {'name': 'GPT test', 'protocol': 'openai_compatible',
                      'base_url': 'https://gateway.example/v1/chat/completions/',
                      'model': 'audio-model', 'api_key': 'private-test-key'}

    def save(self, **changes):
        response = self.client.post('/api/model-profiles', json={**self.draft, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('private-test-key', response.text)
        return response.json()['profile']

    def test_save_activate_and_keep_key_when_editing(self):
        profile = self.save()
        self.assertEqual(profile['base_url'], 'https://gateway.example/v1')
        self.assertTrue(profile['has_key'])
        self.client.post('/api/model-profiles/' + profile['id'] + '/activate')
        with patch.dict(os.environ, {'GEMINI_COMPATIBLE_API_KEY': 'unrelated-environment-key'}):
            config = providers.settings()
            self.assertEqual(config['gemini_model'], 'audio-model')
            self.assertEqual(providers.gemini_key(config), 'private-test-key')
        self.save(id=profile['id'], api_key='', name='Renamed GPT')
        self.assertEqual(providers.gemini_key(providers.settings()), 'private-test-key')
        self.assertNotIn('private-test-key', self.client.get('/api/model-profiles').text)
        self.assertNotIn('private-test-key', self.client.get('/api/status').text)
        self.assertEqual(self.client.get('/api/status').json()['gemini']['name'], 'Google 官方 Gemini')

    def test_changing_endpoint_requires_a_new_key(self):
        profile = self.save()
        response = self.client.post('/api/model-profiles', json={**self.draft, 'id': profile['id'],
            'base_url': 'https://another.example/v1', 'api_key': ''})
        self.assertEqual(response.status_code, 400)

    def test_url_and_key_alone_can_fetch_models_without_a_name(self):
        factory = httpx.Client
        observed = []
        def respond(request):
            observed.append(str(request.url))
            return httpx.Response(200, json={'data': [{'id': 'audio-model'}]})
        with patch('providers.httpx.Client', side_effect=lambda **kwargs:
                factory(transport=httpx.MockTransport(respond), **kwargs)):
            response = self.client.post('/api/model-profiles/models', json={
                'name': '', 'model': '', 'protocol': 'openai_compatible',
                'base_url': 'https://gateway.example', 'api_key': 'private-test-key'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['models'], ['audio-model'])
        self.assertEqual(observed, ['https://gateway.example/v1/models'])

    def test_save_can_derive_name_from_selected_model(self):
        profile = self.save(name='')
        self.assertEqual(profile['name'], 'audio-model')

    def test_explicit_root_endpoint_is_preserved_after_editing(self):
        profile = self.save(base_url='https://gateway.example/chat/completions')
        self.assertEqual(profile['base_url'], 'https://gateway.example/')
        updated = self.save(id=profile['id'], base_url=profile['base_url'], api_key='')
        self.assertEqual(updated['base_url'], 'https://gateway.example/')

    def test_site_root_with_trailing_slash_uses_v1(self):
        profile = self.save(base_url='https://gateway.example/')
        self.assertEqual(profile['base_url'], 'https://gateway.example/v1')

    def test_model_list_and_audio_test_use_the_selected_draft(self):
        profile = self.save()
        observed = []

        def respond(request):
            observed.append(request)
            self.assertEqual(request.headers['authorization'], 'Bearer private-test-key')
            if request.method == 'GET':
                self.assertEqual(str(request.url), 'https://gateway.example/v1/models')
                return httpx.Response(200, json={'data': [{'id': 'audio-model'}, {'id': 'text-model'}]})
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'audio-model')
            self.assertIn('input_audio', request.content.decode())
            plan = {'description': '测试旋律', 'style_prompt': 'Instrumental piano', 'sound_role': 'melody'}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(plan)}}]})

        transport = httpx.MockTransport(respond)
        factory = httpx.Client
        with patch('providers.httpx.Client', side_effect=lambda **kwargs: factory(transport=transport, **kwargs)):
            listed = self.client.post('/api/model-profiles/models', json={'id': profile['id']})
            self.assertEqual(listed.json()['models'], ['audio-model', 'text-model'])
            tested = self.client.post('/api/model-profiles/test', json={'id': profile['id'], 'kind': 'audio'})
        self.assertEqual(tested.status_code, 200, tested.text)
        self.assertEqual(tested.json()['kind'], 'audio')
        self.assertTrue(tested.json()['ok'])
        self.assertEqual(len(observed), 2)

    def test_failed_text_test_redacts_secret_and_does_not_activate(self):
        profile = self.save()
        factory = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(401,
            json={'error': {'message': 'Invalid private-test-key'}}))
        with patch('providers.httpx.Client', side_effect=lambda **kwargs: factory(transport=transport, **kwargs)):
            result = self.client.post('/api/model-profiles/test', json={'id': profile['id'], 'kind': 'text'})
        self.assertEqual(result.status_code, 400)
        self.assertNotIn('private-test-key', result.text)
        self.assertEqual(self.client.get('/api/model-profiles').json()['active_id'], 'legacy')

    def test_audio_test_does_not_confirm_a_model_that_cannot_identify_the_sample(self):
        profile = self.save()
        plan = {'sound_role': 'unknown', 'description': 'No audible recording received.',
                'style_prompt': 'Instrumental piano', 'gemini_used': True}
        with patch('model_routes.describe_audio', return_value=plan):
            result = self.client.post('/api/model-profiles/test',
                                      json={'id': profile['id'], 'kind': 'audio'})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['ok'])
        self.assertEqual(result.json()['sound_role'], 'unknown')
        self.assertTrue(result.json()['warning'])

    def test_active_profile_cannot_be_deleted_and_legacy_can_be_restored(self):
        profile = self.save()
        self.client.post('/api/model-profiles/' + profile['id'] + '/activate')
        self.assertEqual(self.client.delete('/api/model-profiles/' + profile['id']).status_code, 400)
        self.assertEqual(self.client.post('/api/model-profiles/legacy/activate').status_code, 200)
        self.assertEqual(self.client.delete('/api/model-profiles/' + profile['id']).status_code, 200)

    def test_invalid_urls_and_foreign_origin_cannot_save_credentials(self):
        for url in ('http://gateway.example/v1', 'https://user:pass@gateway.example/v1',
                    'https://gateway.example/v1?key=private-test-key'):
            response = self.client.post('/api/model-profiles', json={**self.draft, 'base_url': url})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('private-test-key', response.text)
        response = self.client.post('/api/model-profiles', json=self.draft,
                                    headers={'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
