import io
import math
import unittest
import wave
from array import array

from audio_engine import analyze_audio, mix_tracks, notes_to_abc, read_audio, render_local, validate_markers


def tone_wav(frequencies=(440,), seconds=2.4, sample_rate=16000):
    pcm = array('h')
    for frequency in frequencies:
        pcm.extend(int(0.22 * 32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
                   for i in range(round(seconds * sample_rate)))
    output = io.BytesIO()
    with wave.open(output, 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(sample_rate)
        file.writeframes(pcm.tobytes())
    return output.getvalue()


class AudioTests(unittest.TestCase):
    def test_quiet_mix_is_boosted_with_common_gain_and_silence_stays_silent(self):
        import numpy as np
        tracks = {'source': np.array([0., .04, -.04]), 'melody': np.array([0., .06, -.06])}
        mixed, components = mix_tracks(tracks, {})
        self.assertAlmostEqual(float(np.max(np.abs(mixed))), .4, places=5)
        np.testing.assert_allclose(sum(components.values()), mixed, atol=1e-6)
        silent, _ = mix_tracks({'source': np.zeros(10)}, {})
        self.assertFalse(np.any(silent))

    def test_native_abc_uses_blank_title_and_limited_measure_groups(self):
        score = notes_to_abc([{'midi': 61, 'start': 0, 'end': 0.8},
                              {'midi': 60, 'start': 0.8, 'end': 12}])
        self.assertEqual(score.splitlines()[1], 'T:')
        self.assertEqual(score.count('V: Ins\n'), 2)
        self.assertIn('=C', score)

    def test_invalid_payload_cannot_be_treated_as_audio(self):
        with self.assertRaises(ValueError):
            read_audio(b'<svg onload="alert(1)"></svg>')

    def test_short_audio_is_rejected_before_processing(self):
        with self.assertRaises(ValueError):
            read_audio(tone_wav(seconds=0.4))

    def test_markers_must_be_finite_and_inside_original_audio(self):
        for markers in ([-0.1], [3], [float('nan')], [float('inf')]):
            with self.subTest(markers=markers), self.assertRaises(ValueError):
                validate_markers(markers, 2.4)
        self.assertEqual(validate_markers([1.8, 0.6, 0.6], 2.4), [0.6, 1.8])

    def test_known_a4_signal_yields_a4_notes(self):
        audio, sample_rate = read_audio(tone_wav())
        analysis = analyze_audio(audio, sample_rate, [])
        self.assertTrue(analysis['notes'])
        pitches = [note['midi'] for note in analysis['notes']]
        self.assertTrue(all(abs(pitch - 69) <= 1 for pitch in pitches), pitches)
        self.assertGreater(analysis['voiced_ratio'], 0.6)
        self.assertIn('V: Ins', analysis['abc'])

    def test_marker_selects_later_melody_instead_of_earlier_tone(self):
        audio, sample_rate = read_audio(tone_wav((220, 440), seconds=2.1))
        analysis = analyze_audio(audio, sample_rate, [2.1])
        self.assertTrue(analysis['notes'])
        self.assertTrue(all(abs(note['midi'] - 69) <= 1 for note in analysis['notes']))
        self.assertGreaterEqual(analysis['source_range']['start'], 2.1)

    def test_silence_never_produces_fabricated_notes(self):
        import numpy as np
        analysis = analyze_audio(np.zeros(40000), 16000, [])
        self.assertEqual(analysis['notes'], [])
        self.assertTrue(analysis['warnings'])

    def test_render_contains_original_and_components_reconstruct_mix(self):
        import numpy as np
        audio, sample_rate = read_audio(tone_wav())
        notes = [{'midi': 69, 'name': 'A4', 'start': 0.0, 'end': 2.4}]
        analysis = {'notes': notes, 'source_range': {'start': 0, 'end': 2.4}, 'tempo': 96}
        tracks, output_rate = render_local(audio, sample_rate, analysis, 16)
        self.assertEqual(output_rate, 24000)
        self.assertEqual(set(tracks), {'source', 'melody', 'accompaniment'})
        self.assertEqual(len(tracks['source']), 16 * output_rate)
        self.assertGreater(float(np.max(np.abs(tracks['source']))), 0.1)
        mixed, components = mix_tracks(tracks, {'source': 1.0, 'melody': 0.3, 'accompaniment': 0.5})
        self.assertLessEqual(float(np.max(np.abs(mixed))), 10 ** (-1 / 20) + 1e-6)
        np.testing.assert_allclose(sum(components.values()), mixed, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
