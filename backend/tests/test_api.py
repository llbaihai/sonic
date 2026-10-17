import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory()
os.environ['SONIC_DATA_DIR'] = temporary.name

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from app import app
from test_mvp import tone_wav


class ApiTests(unittest.TestCase):
    def test_audio_analysis_is_pinned_to_official_gemini(self):
        session=self.upload()
        with patch('providers.active_config',return_value={'gemini_mode':'openai_compatible','model_profile_key':'other-key','gemini_model':'other-model'}), \
                patch('app.describe_audio',return_value={'gemini_used':True,'style_prompt':'piano'}) as describe:
            response=self.client.post('/api/sessions/'+session['id']+'/analyze',json={'use_gemini':True,'pitch_provider':'none'})
            self.assertEqual(response.status_code,200,response.text)
            config=describe.call_args.kwargs['config']
            self.assertEqual(config['gemini_mode'],'official')
            self.assertNotIn('model_profile_key',config)
            self.assertNotEqual(config['gemini_model'],'other-model')

    def test_public_app_accepts_only_its_configured_browser_origin(self):
        with patch.dict(os.environ,{'SONIC_PUBLIC_MODE':'1','SONIC_APP_TOKEN':'x'*40,'SONIC_PUBLIC_ORIGIN':'https://app.example'}):
            headers={'Authorization':'Bearer '+'x'*40,'Origin':'https://app.example'}
            accepted=self.client.post('/api/prompts/translate',json={'text':'piano music','target':'en'},headers=headers)
            self.assertEqual(accepted.status_code,200,accepted.text)
            rejected=self.client.post('/api/prompts/translate',json={'text':'piano music','target':'en'},headers={**headers,'Origin':'https://other.example'})
            self.assertEqual(rejected.status_code,403)

    def test_chinese_prompt_translation_seed_and_default_duration(self):
        from app import SESSIONS_DIR,save_json
        session=self.upload();session['analysis']={'notes':[],'abc':'','style_prompt':'old'}
        save_json(SESSIONS_DIR/session['id']/'session.json',session)
        with patch('app.translate_prompt',return_value='A gentle sea-inspired piano piece') as translate,patch('app.executor.submit') as submit:
            r=self.client.post('/api/compositions',json={'session_ids':[session['id']],'final_prompt':'海边的轻柔钢琴','seed':12345})
            self.assertEqual(r.status_code,200,r.text)
            request=submit.call_args.args[2]
            self.assertEqual(request.duration,120);self.assertEqual(request.seed,12345)
            self.assertEqual(request.prompt_zh,'海边的轻柔钢琴')
            self.assertEqual(request.style,'A gentle sea-inspired piano piece')
            translate.assert_called_once_with('海边的轻柔钢琴','en')

    def test_regenerate_retains_all_sources_and_leaves_parent_untouched(self):
        from app import JOBS_DIR,SESSIONS_DIR,save_json,read_json
        import uuid
        sessions=[self.upload(),self.upload()]
        for session in sessions:
            session['analysis']={'notes':[],'abc':'','style_prompt':'old'}
            save_json(SESSIONS_DIR/session['id']/'session.json',session)
        identity=uuid.uuid4().hex;path=JOBS_DIR/identity;path.mkdir()
        parent={'id':identity,'session_id':sessions[0]['id'],'sources':[{'id':s['id']} for s in sessions],
            'seed':1,'prompt_zh':'旧提示词','creation_mode':'text_conditioned'}
        save_json(path/'project.json',parent)
        with patch('app.translate_prompt',return_value='new music'),patch('app.executor.submit') as submit:
            r=self.client.post(f'/api/jobs/{identity}/regenerate',json={'prompt_zh':'新提示词','seed':777})
            self.assertEqual(r.status_code,200,r.text);self.assertNotEqual(r.json()['id'],identity)
            request=submit.call_args.args[2]
            self.assertEqual(request.selected_session_ids,[s['id'] for s in sessions])
            self.assertEqual(request.seed,777);self.assertEqual(request.parent_job_id,identity)
        self.assertEqual(read_json(path/'project.json'),parent)

    def test_regenerate_uses_saved_melody_when_source_has_been_reanalyzed(self):
        from app import JOBS_DIR,SESSIONS_DIR,save_json
        import uuid
        session=self.upload()
        session['analysis']={'notes':[],'abc':'','style_prompt':'new source analysis'}
        save_json(SESSIONS_DIR/session['id']/'session.json',session)
        identity=uuid.uuid4().hex;path=JOBS_DIR/identity;path.mkdir()
        notes=[{'start':0.,'end':1.,'midi':60}]
        save_json(path/'project.json',{'session_id':session['id'],'sources':[{'id':session['id']}],
            'melody_id':session['id'],'creation_mode':'melody_conditioned','input_abc':'SAVED ABC',
            'analysis':{'notes':notes,'abc':'MODEL SCORE','pitch_provider':'pyin'}})
        with patch('app.translate_prompt',return_value='new style'),patch('app.executor.submit') as submit:
            response=self.client.post(f'/api/jobs/{identity}/regenerate',json={'prompt_zh':'新的风格','seed':88})
            self.assertEqual(response.status_code,200,response.text)
            request=submit.call_args.args[2];record=submit.call_args.args[3]
            self.assertTrue(request.keep_melody)
            self.assertEqual(record['analysis']['notes'],notes)
            self.assertEqual(record['analysis']['abc'],'SAVED ABC')

    def test_final_edited_prompt_is_authoritative_with_genre_and_abc(self):
        from app import SESSIONS_DIR,save_json
        session=self.upload()
        session['analysis']={'notes':[{'start':0.,'end':1.,'midi':60}], 'abc':'original ABC', 'style_prompt':'STALE recommendation'}
        save_json(SESSIONS_DIR/session['id']/'session.json',session)
        with patch('app.executor.submit') as submit:
            response=self.client.post('/api/compositions',json={'session_ids':[session['id']],
                'melody_id':session['id'],'genre':'rock','final_prompt':'MY EDITED PROMPT','abc':'MY EDITED ABC'})
            self.assertEqual(response.status_code,200,response.text)
            request=submit.call_args.args[2];record=submit.call_args.args[3]
            self.assertIn('MY EDITED PROMPT',request.style)
            self.assertIn('electric guitar',request.style.lower())
            self.assertNotIn('STALE',request.style)
            self.assertEqual(record['analysis']['abc'],'MY EDITED ABC')
            self.assertTrue(request.keep_melody)

    def test_composition_combines_sources_and_has_optional_melody(self):
        from app import SESSIONS_DIR,save_json
        sessions=[self.upload(),self.upload()]
        for i,session in enumerate(sessions):
            session['analysis']={'notes':[],'abc':'','style_prompt':['ocean waves','footsteps'][i],'description':session['name']}
            save_json(SESSIONS_DIR/session['id']/'session.json',session)
        with patch('app.executor.submit') as submit:
            response=self.client.post('/api/compositions',json={'session_ids':[s['id'] for s in sessions],'style':'gentle piano'})
            self.assertEqual(response.status_code,200,response.text)
            request=submit.call_args.args[2]
            self.assertFalse(request.keep_melody)
            self.assertIn('ocean waves',request.style)
            self.assertIn('footsteps',request.style)
            self.assertEqual(request.selected_session_ids,[s['id'] for s in sessions])
        self.assertEqual(self.client.post('/api/compositions',json={'session_ids':[]}).status_code,422)

    def test_life_sound_analysis_can_skip_pitch_and_use_gemini(self):
        session=self.upload()
        with patch('app.describe_audio',return_value={'description':'海浪与脚步','style_prompt':'ocean ambience with footsteps, instrumental', 'gemini_used':True}), patch('app.analyze_audio') as pitch:
            response=self.client.post(f"/api/sessions/{session['id']}/analyze",json={'pitch_provider':'none','use_gemini':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['notes'],[])
            self.assertTrue(response.json()['gemini_used'])
            pitch.assert_not_called()

    def test_yue_without_notes_uses_text_even_when_old_client_requests_melody(self):
        from app import GenerateRequest,JOBS_DIR,save_json,process_job,read_json
        import uuid
        record=self.upload();record['analysis']={'notes':[],'abc':'','style_prompt':'ocean soundscape, piano','warnings':[]}
        identity=uuid.uuid4().hex;destination=JOBS_DIR/identity;destination.mkdir()
        save_json(destination/'job.json',{'id':identity})
        def fake_render(*args):
            target=destination/'generated.flac'
            sf.write(target,np.sin(np.arange(16000*16)*.1)*.1,16000)
            return target
        with patch('app.render_yue2',side_effect=fake_render) as generate:
            process_job(identity,GenerateRequest(session_id=record['id'],provider='yue2',duration=16,keep_melody=True),record)
            self.assertFalse(generate.call_args.args[4])
        self.assertEqual(read_json(destination/'job.json')['status'],'completed')
        self.assertEqual(read_json(destination/'project.json')['creation_mode'],'text_conditioned')

    def test_source_defaults_off_but_recorded_stem_remains_playable(self):
        from app import GenerateRequest, JOBS_DIR, SESSIONS_DIR, save_json, process_job, read_json
        import uuid
        record=self.upload();path=SESSIONS_DIR/record['id']
        record['analysis']={'notes':[{'midi':69,'start':0.,'end':1.}], 'abc':'X:1', 'style_prompt':'piano'}
        identity=uuid.uuid4().hex;destination=JOBS_DIR/identity;destination.mkdir()
        save_json(destination/'job.json',{'id':identity})
        request=GenerateRequest(session_id=record['id'],duration=16)
        self.assertEqual(request.source_gain,0)
        process_job(identity,request,record)
        result=read_json(destination/'job.json')
        self.assertEqual(result['status'],'completed')
        original=next(t for t in result['tracks'] if t['kind']=='recorded')
        self.assertEqual(original['gain'],0)
        samples,_=sf.read(destination/'source.wav')
        self.assertGreater(float(np.max(np.abs(samples))),.01)

    def test_live_separation_rejects_non_yue_and_missing_jobs(self):
        from app import JOBS_DIR, save_json
        import uuid
        identity=uuid.uuid4().hex
        path=JOBS_DIR/identity;path.mkdir()
        save_json(path/'job.json',{'status':'completed','provider':'mma'})
        with patch('app.executor.submit') as submit:
            self.assertEqual(self.client.post(f'/api/jobs/{identity}/separate',json={}).status_code,400)
            self.assertEqual(self.client.post('/api/jobs/'+'f'*32+'/separate',json={}).status_code,404)
            submit.assert_not_called()

    def test_completed_live_project_is_reused_without_duplicate_gpu_job(self):
        from app import JOBS_DIR, save_json
        import uuid
        identity=uuid.uuid4().hex
        path=JOBS_DIR/identity;path.mkdir()
        save_json(path/'job.json',{'status':'completed','provider':'yue2'})
        save_json(path/'separation.json',{'status':'completed','project_id':'mvp2_existing','editor_url':'/live/?project=mvp2_existing'})
        with patch('app.executor.submit') as submit:
            result=self.client.post(f'/api/jobs/{identity}/separate',json={})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['project_id'],'mvp2_existing')
            submit.assert_not_called()

    def setUp(self):
        self.client = TestClient(app)

    def upload(self):
        response = self.client.post('/api/sessions', files={'file': ('fixture.wav', tone_wav(), 'audio/wav')},
                                    data={'source': 'sample'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_foreign_host_cannot_read_private_sessions(self):
        response = self.client.get('/api/sessions', headers={'host': 'evil.example'})
        self.assertEqual(response.status_code, 400)

    def test_foreign_origin_cannot_upload_audio(self):
        response = self.client.post('/api/sessions', headers={'origin': 'https://evil.example'},
                                    files={'file': ('fixture.wav', tone_wav(), 'audio/wav')})
        self.assertEqual(response.status_code, 403)

    def test_invalid_file_has_no_session_side_effect(self):
        before = len(self.client.get('/api/sessions').json()['sessions'])
        response = self.client.post('/api/sessions', files={'file': ('bad.wav', b'not wav', 'audio/wav')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.client.get('/api/sessions').json()['sessions']), before)

    def test_audio_formats_preserve_original_and_produce_playable_wav(self):
        samples, rate = sf.read(io.BytesIO(tone_wav()), dtype='float32')
        for container, subtype, suffix in (
            ('AIFF', 'PCM_24', 'aiff'), ('AIFF', 'FLOAT', 'aifc'),
            ('FLAC', 'PCM_24', 'flac'), ('OGG', 'VORBIS', 'ogg'),
            ('OGG', 'OPUS', 'opus'), ('MP3', 'MPEG_LAYER_III', 'mp3'),
        ):
            with self.subTest(container=container, subtype=subtype):
                encoded = io.BytesIO()
                sf.write(encoded, samples, rate, format=container, subtype=subtype)
                blob = encoded.getvalue()
                response = self.client.post('/api/sessions',
                    files={'file': ('fixture.' + suffix, blob, 'application/octet-stream')})
                self.assertEqual(response.status_code, 200, response.text)
                record = response.json()
                self.assertTrue(record['original_url'].endswith('.' +
                    {'AIFF': 'aiff', 'FLAC': 'flac', 'OGG': 'ogg', 'MP3': 'mp3'}[container]))
                self.assertEqual(self.client.get(record['original_url']).content, blob)
                decoded, decoded_rate = sf.read(io.BytesIO(self.client.get(record['audio_url']).content))
                self.assertEqual(decoded_rate, rate)
                self.assertAlmostEqual(len(decoded) / decoded_rate, 2.4, delta=0.1)
                self.assertGreater(float(np.max(np.abs(decoded))), 0.1)

    def test_new_formats_keep_validation_and_do_not_trust_filename(self):
        for duration, channels in ((0.4, 1), (30.1, 1), (2.4, 3)):
            with self.subTest(duration=duration, channels=channels):
                encoded = io.BytesIO()
                sf.write(encoded, np.zeros((round(duration * 16000), channels)),
                         16000, format='AIFF', subtype='PCM_16')
                response = self.client.post('/api/sessions',
                    files={'file': ('misleading.wav', encoded.getvalue(), 'audio/wav')})
                self.assertEqual(response.status_code, 400, response.text)
        fake = self.client.post('/api/sessions',
            files={'file': ('fake.aiff', b'not audio', 'audio/aiff')})
        self.assertEqual(fake.status_code, 400)

    def test_marker_changes_invalidate_cached_analysis(self):
        session = self.upload()
        analyzed = self.client.post('/api/sessions/' + session['id'] + '/analyze', json={'use_gemini': False})
        self.assertEqual(analyzed.status_code, 200)
        response = self.client.patch('/api/sessions/' + session['id'], json={'markers': [0.4]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['analysis'])
        invalid = self.client.patch('/api/sessions/' + session['id'], json={'markers': [4]})
        self.assertEqual(invalid.status_code, 400)

    def test_local_job_produces_playable_audio_and_preserved_source_manifest(self):
        session = self.upload()
        created = self.client.post('/api/jobs', json={'session_id': session['id'], 'provider': 'local', 'duration': 16})
        self.assertEqual(created.status_code, 200)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = self.client.get('/api/jobs/' + created.json()['id']).json()
            if job['status'] in {'completed', 'failed'}:
                break
            time.sleep(0.1)
        self.assertEqual(job['status'], 'completed', job)
        audio = self.client.get(job['audio_url'])
        samples, sample_rate = sf.read(io.BytesIO(audio.content))
        self.assertEqual(sample_rate, 24000)
        self.assertGreater(np.max(np.abs(samples)), 0.01)
        project = self.client.get(job['project_url']).json()
        self.assertEqual(project['session_id'], session['id'])
        self.assertEqual(project['provider'], 'local')
        self.assertEqual(project['source'], 'sample')
        self.assertIn('recorded', {track['kind'] for track in project['tracks']})

    def test_unknown_provider_does_not_silently_fallback(self):
        session = self.upload()
        response = self.client.post('/api/jobs', json={'session_id': session['id'], 'provider': 'not-real'})
        self.assertEqual(response.status_code, 400)

    def test_failed_gemini_attempt_remains_explicit_in_partial_analysis(self):
        session = self.upload()
        with patch('app.describe_audio', side_effect=RuntimeError('Gemini 调用未完成：测试超时')):
            response = self.client.post('/api/sessions/' + session['id'] + '/analyze', json={'use_gemini': True})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result.get('gemini_attempted'))
        self.assertFalse(result['gemini_used'])
        self.assertTrue(result['notes'])
        self.assertIn('测试超时', result.get('gemini_error', ''))

    def test_basic_pitch_selection_uses_remote_transcription_without_local_substitution(self):
        session = self.upload()
        plan = {'notes': [{'midi': 69, 'name': 'A4', 'start': 0.0, 'end': 1.0}],
                'abc': 'remote native score', 'pitch_provider': 'basic_pitch',
                'description': 'Remote transcription', 'warnings': [], 'gemini_used': False}
        with patch('app.transcribe_basic_pitch', return_value=plan, create=True) as remote:
            response = self.client.post('/api/sessions/' + session['id'] + '/analyze',
                                       json={'pitch_provider': 'basic_pitch'})
        self.assertEqual(response.status_code, 200, response.text)
        remote.assert_called_once()
        self.assertEqual(response.json()['pitch_provider'], 'basic_pitch')

    def test_basic_pitch_failure_is_explicit_and_unknown_pitch_provider_is_rejected(self):
        session = self.upload()
        with patch('app.transcribe_basic_pitch', side_effect=RuntimeError('remote unavailable'), create=True):
            result = self.client.post('/api/sessions/' + session['id'] + '/analyze',
                                      json={'pitch_provider': 'basic_pitch'})
        self.assertEqual(result.status_code, 502)
        self.assertIsNone(self.client.get('/api/sessions/' + session['id']).json()['analysis'])
        invalid = self.client.post('/api/sessions/' + session['id'] + '/analyze',
                                   json={'pitch_provider': 'unknown'})
        self.assertEqual(invalid.status_code, 422)

    def test_transcription_downloads_only_expose_the_current_validated_artifacts(self):
        session = self.upload()

        def transcribe(samples, rate, markers, directory):
            artifact_id = 'basic-pitch-' + 'b' * 32
            folder = directory / artifact_id
            folder.mkdir()
            (folder / 'notes.csv').write_text('start,end,midi\n0,1,69\n', encoding='utf-8')
            return {'pitch_provider': 'basic_pitch', 'pitch_artifact_id': artifact_id,
                    'notes': [], 'warnings': [], 'gemini_used': False}

        with patch('app.transcribe_basic_pitch', side_effect=transcribe):
            result = self.client.post('/api/sessions/' + session['id'] + '/analyze',
                json={'pitch_provider': 'basic_pitch'}).json()
        self.assertEqual(self.client.get(result['transcription_urls']['csv']).status_code, 200)
        self.assertEqual(self.client.get('/api/sessions/' + session['id'] + '/transcription/runtime.py').status_code, 404)
        self.client.patch('/api/sessions/' + session['id'], json={'markers': [0.4]})
        self.assertEqual(self.client.get(result['transcription_urls']['csv']).status_code, 404)

    def test_yue2_job_exposes_real_mix_and_original_without_fabricated_stems(self):
        session = self.upload()

        def render(identity, analysis, style, duration, keep, directory, progress):
            # A mocked provider boundary; the real audio packaging still runs.
            audio, rate = sf.read(io.BytesIO(tone_wav()))
            generated = directory / 'generated.flac'
            sf.write(generated, np.column_stack([audio, audio]), rate)
            return generated

        with patch('app.render_yue2', side_effect=render), patch('app.settings', return_value={'ssh_host': 'test.invalid'}):
            created = self.client.post('/api/jobs', json={'session_id': session['id'],
                'provider': 'yue2', 'duration': 20})
            self.assertEqual(created.status_code, 200, created.text)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                job = self.client.get('/api/jobs/' + created.json()['id']).json()
                if job['status'] in {'completed', 'failed'}:
                    break
                time.sleep(0.05)
        self.assertEqual(job['status'], 'completed', job)
        project = self.client.get(job['project_url']).json()
        self.assertEqual({track['kind'] for track in project['tracks']}, {'recorded', 'yue2_mix'})
        self.assertEqual(len(project['tracks']), 2)
        self.assertTrue(project['duration_is_hint'])
        self.assertAlmostEqual(project['generated_duration'], 2.4, delta=0.01)


if __name__ == '__main__':
    unittest.main()
