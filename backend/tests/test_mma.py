import pytest
from mma_runtime import build_plan


def test_user_chords_and_groove_are_validated_and_used():
    request = {'notes': [{'start': 0., 'end': 1., 'midi': 60}],
        'placements': [2.], 'duration': 20, 'bpm': 100,
        'bpm_source': 'user', 'groove': 'Ballad1', 'chords': 'C F G7 C'}
    plan = build_plan(request)
    assert plan['chords'] == ['C','F','G7','C'] * 2 + ['C']
    assert plan['groove'] == 'Ballad1'
    assert plan['bpm_source'] == 'user'
    assert plan['chord_source'] == 'user'
    for field, value in [('groove', 'Ballad\nSystem echo bad'), ('chords', 'C\nSystem bad')]:
        with pytest.raises(ValueError):
            build_plan({**request, field: value})


def test_notes_keep_timing_at_each_original_placement():
    plan = build_plan({'notes': [{'start': 2.25, 'end': 3.0, 'midi': 64}],
                       'placements': [2., 12.], 'duration': 20, 'bpm': 96})
    assert plan['notes'] == [{'start': 4.25, 'end': 5., 'midi': 64},
                             {'start': 14.25, 'end': 15., 'midi': 64}]
    assert len(plan['chords']) == 8
    assert plan['bpm_source'] == 'default'


@pytest.mark.parametrize('notes', [[], [{'start': 0, 'end': float('inf'), 'midi': 60}],
    [{'start': 0, 'end': 1, 'midi': 200}]])
def test_missing_or_invalid_melody_is_rejected(notes):
    with pytest.raises(ValueError):
        build_plan({'notes': notes, 'placements': [2.], 'duration': 20, 'bpm': 96})


def test_c_major_phrase_uses_one_diatonic_chord_family():
    plan = build_plan({'notes': [{'start': i*.5, 'end': i*.5+.4, 'midi': p}
        for i,p in enumerate([60,64,67,65,69,67,62,60])],
        'placements': [0.], 'duration': 20, 'bpm': 96})
    assert plan['tonic_pc'] == 0
    assert set(plan['chords']) <= {'C','Dm','Em','F','G','Am'}
