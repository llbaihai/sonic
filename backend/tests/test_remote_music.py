import math
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from remote_music import _run, monophonic_notes, render_yue2


class RemoteMusicTests(unittest.TestCase):
    def test_user_seed_reaches_yue_request(self):
        import json
        with patch('remote_music._run') as run:
            render_yue2('a'*32,{'notes':[],'generation_seed':987},'piano',120,False,'.',lambda *args:None)
            payload=json.loads(run.call_args.args[3]['request.json'])
            self.assertEqual(payload['seed'],987);self.assertEqual(payload['requested_duration'],120)
    def test_sftp_channel_has_its_own_timeout_before_transfer(self):
        connection = MagicMock()
        sftp = connection.__enter__.return_value.open_sftp.return_value.__enter__.return_value
        sftp.mkdir.side_effect = TimeoutError('SFTP unavailable')
        with patch('remote_music.connect_yue2', return_value=connection), self.assertRaises(TimeoutError):
            _run('generate', 'a' * 32, Path('.'), {}, {})
        sftp.get_channel.return_value.settimeout.assert_called_once_with(15)

    def test_polyphonic_events_choose_strongest_notes_and_keep_source_timing(self):
        events = [{'start': 0.0, 'end': 1.0, 'midi': 60, 'amplitude': 0.2},
                  {'start': 0.25, 'end': 0.75, 'midi': 69, 'amplitude': 0.8}]
        notes = monophonic_notes(events, 2.0, 1.0)
        self.assertEqual([(n['midi'], n['start'], n['end']) for n in notes],
                         [(60, 2.0, 2.25), (69, 2.25, 2.75), (60, 2.75, 3.0)])

    def test_nonfinite_or_invalid_pitch_events_are_rejected(self):
        for changes in ({'end': math.inf}, {'midi': 200}, {'amplitude': math.nan}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                monophonic_notes([{'start': 0, 'end': 1, 'midi': 60, 'amplitude': 0.2, **changes}], 0, 1)

    def test_keep_melody_requires_actual_notes_and_never_substitutes_a_sample(self):
        with patch('remote_music._run') as remote, self.assertRaises(ValueError):
            render_yue2('a' * 32, {'notes': [], 'abc': ''}, 'piano', 20, True, '.', lambda *args: None)
        remote.assert_not_called()


if __name__ == '__main__':
    unittest.main()
