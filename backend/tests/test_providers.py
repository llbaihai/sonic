import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import numpy as np
import soundfile as sf

import providers


ANALYSIS = {'sound_role': 'melody', 'description': '连续的乐音。', 'mood': '轻快',
            'style_prompt': 'Gentle instrumental piano, preserve the melody, no vocals.'}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.health_patch = patch('providers._gemini_last_call', None, create=True)
        self.health_patch.start()
        self.addCleanup(self.health_patch.stop)
        self.config = {'gemini_mode': 'openai_compatible', 'gemini_model': 'gemini-3.8-flash',
                       'gemini_base_url': 'https://gateway.example/v1/',
                       'gemini_compatible_api_key': 'proxy-test-secret',
                       'gemini_official_api_key': 'official-test-secret'}
        self.audio = np.sin(2 * np.pi * 440 * np.arange(3200) / 16000).astype('float32') * 0.1

    def test_compatible_mode_uses_its_own_key_even_with_official_environment_key(self):
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'official-env-secret'}, clear=True):
            self.assertEqual(providers.gemini_key(self.config), 'proxy-test-secret')

    def test_official_mode_does_not_reuse_proxy_key(self):
        self.config['gemini_mode'] = 'official'
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(providers.gemini_key(self.config), 'official-test-secret')

    def test_google_compatible_mode_uses_proxy_key(self):
        self.config['gemini_mode'] = 'google_compatible'
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'official-env-secret'}, clear=True):
            self.assertEqual(providers.gemini_key(self.config), 'proxy-test-secret')

    def test_timeout_is_reported_as_failed_connection_not_success(self):
        def timeout(request):
            raise httpx.ReadTimeout('timed out', request=request)

        client = httpx.Client(transport=httpx.MockTransport(timeout))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client):
            with self.assertRaises(RuntimeError):
                providers.describe_audio(self.audio, 16000, '')
        status = providers.gemini_call_status(self.config)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('60', status['message'])
        self.assertNotIn('secret', json.dumps(status))
        changed = {**self.config, 'gemini_compatible_api_key': 'different-key'}
        self.assertEqual(providers.gemini_call_status(changed)['state'], 'unverified')

    def test_success_requires_a_valid_live_response_in_this_process(self):
        self.assertEqual(providers.gemini_call_status(self.config)['state'], 'unverified')
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': json.dumps(ANALYSIS)}}]})))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client):
            providers.describe_audio(self.audio, 16000, '')
        self.assertEqual(providers.gemini_call_status(self.config)['state'], 'success')

    def test_no_audio_reply_never_counts_as_successful_understanding(self):
        plan = {**ANALYSIS, 'sound_role': 'unknown', 'description': '未检测到可供分析的原始录音，无法可靠描述实际声音。'}
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': json.dumps(plan)}}]})))
        with patch('providers.httpx.Client', return_value=client):
            with self.assertRaisesRegex(RuntimeError, '音频理解未确认'):
                providers.describe_audio(self.audio, 16000, '', config=self.config)
        self.assertEqual(providers.gemini_call_status(self.config)['state'], 'failed')

    def test_google_compatible_audio_matches_native_gemini_protocol(self):
        self.config.update(gemini_mode='google_compatible', gemini_native_base_url='https://gateway.example')

        def respond(request):
            self.assertEqual(str(request.url), 'https://gateway.example/v1beta/models/gemini-3.8-flash:generateContent')
            self.assertEqual(request.headers['x-goog-api-key'], 'proxy-test-secret')
            self.assertNotIn('authorization', request.headers)
            payload = json.loads(request.content)
            part = next(item for item in payload['contents'][0]['parts'] if 'inline_data' in item)
            self.assertEqual(part['inline_data']['mime_type'], 'audio/wav')
            samples, sample_rate = sf.read(io.BytesIO(base64.b64decode(part['inline_data']['data'])))
            self.assertEqual(sample_rate, 16000)
            self.assertEqual(len(samples), 3200)
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(ANALYSIS)}]}}]})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client), \
                patch('providers.genai.Client', side_effect=AssertionError('Wrong provider')):
            result = providers.describe_audio(self.audio, 16000, 'warm piano')
        self.assertEqual(result['gemini_mode'], 'google_compatible')
        self.assertTrue(result['gemini_used'])

    def test_compatible_request_contains_decodable_wav_and_valid_auth(self):
        observed = []

        def respond(request):
            observed.append(request)
            self.assertEqual(str(request.url), 'https://gateway.example/v1/chat/completions')
            self.assertEqual(request.headers['authorization'], 'Bearer proxy-test-secret')
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'gemini-3.8-flash')
            audio_part = next(item for item in payload['messages'][0]['content'] if item['type'] == 'input_audio')
            self.assertEqual(audio_part['input_audio']['format'], 'wav')
            samples, sample_rate = sf.read(io.BytesIO(base64.b64decode(audio_part['input_audio']['data'])))
            self.assertEqual(sample_rate, 16000)
            self.assertEqual(len(samples), 3200)
            self.assertLessEqual(payload['max_tokens'], 2048)
            self.assertNotIn('official-test-secret', request.content.decode())
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(ANALYSIS)}}]})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client), \
                patch('providers.genai.Client', side_effect=AssertionError('Wrong provider')):
            result = providers.describe_audio(self.audio, 16000, 'warm piano')
        self.assertEqual(len(observed), 1)
        self.assertTrue(result['gemini_used'])
        self.assertEqual(result['gemini_mode'], 'openai_compatible')
        self.assertEqual(result['description'], ANALYSIS['description'])

    def test_gateway_failure_redacts_keys_and_never_falls_back_to_official(self):
        def respond(request):
            return httpx.Response(401, json={'error': {'message': 'bad proxy-test-secret official-test-secret'}})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client), \
                patch('providers.genai.Client', side_effect=AssertionError('No silent fallback')):
            with self.assertRaises(RuntimeError) as caught:
                providers.describe_audio(self.audio, 16000, '')
        self.assertNotIn('proxy-test-secret', str(caught.exception))
        self.assertNotIn('official-test-secret', str(caught.exception))

    def test_gateway_non_json_output_is_not_reported_as_ai_success(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': 'not a structured plan'}}]})))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client):
            with self.assertRaises(RuntimeError):
                providers.describe_audio(self.audio, 16000, '')

    def test_empty_plan_is_not_reported_as_audio_understanding(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': '{}'}}]})))
        with patch('providers.settings', return_value=self.config), \
                patch('providers.httpx.Client', return_value=client):
            with self.assertRaises(RuntimeError):
                providers.describe_audio(self.audio, 16000, '')

    def test_status_reports_endpoint_without_exposing_any_key(self):
        from app import status
        official={**self.config,'gemini_mode':'official'}
        with patch('app.official_audio_settings', return_value=official), patch.dict('os.environ', {}, clear=True):
            public = status()['gemini']
        self.assertEqual(public['mode'], 'official')
        self.assertEqual(public['endpoint_host'], 'generativelanguage.googleapis.com')
        self.assertTrue(public['configured'])
        self.assertNotIn('secret', json.dumps(public))

    def test_official_interactions_mode_remains_available(self):
        self.config['gemini_mode'] = 'official'
        client = SimpleNamespace(interactions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            outputs=[SimpleNamespace(text=json.dumps(ANALYSIS))])), close=lambda: None)
        with patch('providers.settings', return_value=self.config), \
                patch.dict('os.environ', {}, clear=True), \
                patch('providers.genai.Client', return_value=client) as factory:
            result = providers.describe_audio(self.audio, 16000, '')
        self.assertEqual(factory.call_args.kwargs['api_key'], 'official-test-secret')
        self.assertEqual(result['gemini_mode'], 'official')

    def test_successful_audio_response_redacts_echoed_credentials(self):
        plan = {**ANALYSIS, 'description': 'Echo proxy-test-secret',
                'mood': 'official-test-secret', 'style_prompt': 'Echo proxy-test-secret'}
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'choices': [{'message': {'content': json.dumps(plan)}}]})))
        with patch('providers.httpx.Client', return_value=client):
            result = providers.describe_audio(self.audio, 16000, '', config=self.config)
        self.assertNotIn('proxy-test-secret', json.dumps(result))
        self.assertNotIn('official-test-secret', json.dumps(result))

    def test_model_list_discards_echoed_credentials(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={'data': [{'id': 'audio-model'}, {'id': 'proxy-test-secret'}]})))
        with patch('providers.httpx.Client', return_value=client):
            self.assertEqual(providers.list_models(self.config), ['audio-model'])

    def test_official_probes_never_send_key_to_legacy_gateway(self):
        self.config.update(gemini_mode='official', gemini_native_base_url='https://legacy-gateway.example')

        def respond(request):
            self.assertEqual(request.url.host, 'generativelanguage.googleapis.com')
            self.assertEqual(request.headers['x-goog-api-key'], 'official-test-secret')
            if request.method == 'GET':
                return httpx.Response(200, json={'models': [{'name': 'models/gemini-test'}]})
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': 'OK'}]}}]})

        factory = httpx.Client
        with patch.dict('os.environ', {}, clear=True), patch('providers.httpx.Client',
                side_effect=lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs)):
            self.assertEqual(providers.list_models(self.config), ['gemini-test'])
            self.assertEqual(providers.probe_model_text(self.config), 'OK')

    def test_gemini_25_audio_does_not_receive_gemini_3_only_controls(self):
        self.config.update(gemini_mode='google_compatible', gemini_model='gemini-2.5-flash',
                           gemini_native_base_url='https://gateway.example')

        def respond(request):
            payload = json.loads(request.content)
            self.assertNotIn('thinkingConfig', payload['generationConfig'])
            self.assertIn('gemini-2.5-flash:generateContent', str(request.url))
            return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(ANALYSIS)}]}}]})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('providers.httpx.Client', return_value=client):
            result = providers.describe_audio(self.audio, 16000, '', config=self.config)
        self.assertTrue(result['gemini_used'])


if __name__ == '__main__':
    unittest.main()
