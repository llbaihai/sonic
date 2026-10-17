import io
import math

import librosa
import numpy as np
import pretty_midi
import soundfile as sf
from scipy.ndimage import median_filter


MAX_BYTES = 12 * 1024 * 1024
OUTPUT_RATE = 24000
AUDIO_FORMATS = {
    'WAV': ('wav', 'audio/wav'), 'WAVEX': ('wav', 'audio/wav'),
    'RF64': ('wav', 'audio/wav'), 'FLAC': ('flac', 'audio/flac'),
    'AIFF': ('aiff', 'audio/aiff'), 'OGG': ('ogg', 'audio/ogg'),
    'MP3': ('mp3', 'audio/mpeg'),
}


def read_audio(blob):
    if not blob or len(blob) > MAX_BYTES:
        raise ValueError('音频文件为空或超过 12 MB。')
    try:
        info = sf.info(io.BytesIO(blob))
        if info.format not in AUDIO_FORMATS:
            raise ValueError('请选择 WAV、FLAC、AIFF/AIF、MP3 或 OGG/Opus 文件。')
        if not 2 <= info.duration <= 30:
            raise ValueError('请选择 2–30 秒的声音片段。')
        if not 8000 <= info.samplerate <= 96000 or not 1 <= info.channels <= 2:
            raise ValueError('音频需要是 8–96 kHz 的单声道或双声道文件。')
        samples, sample_rate = sf.read(io.BytesIO(blob), dtype='float32')
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('文件无法解码，请使用 WAV、FLAC、AIFF/AIF、MP3 或 OGG/Opus，或重新导出 WAV。') from error
    if not np.isfinite(samples).all() or float(np.max(np.abs(samples))) > 4:
        raise ValueError('音频包含无效或异常幅度的数据。')
    return samples, int(sample_rate)


def validate_markers(markers, duration):
    if not isinstance(markers, list) or len(markers) > 32:
        raise ValueError('标记需要是最多 32 个相对秒数。')
    result = []
    for value in markers:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('标记需要使用相对录音起点的秒数。')
        if not math.isfinite(value) or value < 0 or value >= duration:
            raise ValueError('标记必须位于录音范围内。')
        result.append(round(float(value), 5))
    return sorted(set(result))


def encode_wav(samples, sample_rate):
    output = io.BytesIO()
    sf.write(output, samples, sample_rate, format='WAV', subtype='PCM_16')
    return output.getvalue()


def note_abc(midi):
    names = ['C', '^C', 'D', '^D', 'E', 'F', '^F', 'G', '^G', 'A', '^A', 'B']
    octave = int(midi) // 12 - 1
    name = names[int(midi) % 12]
    if not name.startswith('^'):
        name = '=' + name
    if octave >= 5:
        return name.lower() + "'" * (octave - 5)
    return name + ',' * max(0, 4 - octave)


def notes_to_abc(notes, start=0.0, tempo=96):
    unit = 60.0 / tempo / 4
    cursor = 0
    events = []
    for note in notes:
        onset = max(cursor, round((note['start'] - start) / unit))
        if onset > cursor:
            events.append(('z', onset - cursor))
        length = max(1, round((note['end'] - note['start']) / unit))
        events.append((note_abc(note['midi']), length))
        cursor = onset + length
    if not events:
        events = [('z', 16)]
    padding = (-sum(count for _, count in events)) % 16
    if padding:
        events.append(('z', padding))
    bars = []
    current = []
    remaining = 16
    for symbol, count in events:
        while count:
            length = max(value for value in (1, 2, 3, 4, 6, 8, 12, 16) if value <= min(remaining, count))
            count -= length
            token = symbol + (str(length) if length != 1 else '')
            if count and symbol != 'z':
                token += '-'
            current.append(token)
            remaining -= length
            if remaining == 0:
                bars.append(''.join(current) + '|')
                current = []
                remaining = 16
    groups = []
    for index in range(0, len(bars), 4):
        group = bars[index:index + 4]
        groups.extend(['V: Vocal', 'Z' + (str(len(group)) if len(group) > 1 else '') + '|',
                       'V: Ins', ''.join(group)])
    return ('X:1\nT:\nM:4/4\nL:1/16\nQ:1/4=' + str(tempo)
            + '\nV: Vocal clef=treble name="Vocal Melody" snm="Vocal"'
            + '\nV: Ins clef=treble name="Ins Melody" snm="Inst."\nK:C\n'
            + '% verse\n' + '\n'.join(groups) + '\n')


def analyze_audio(samples, sample_rate, markers):
    markers = validate_markers(markers, len(samples) / sample_rate)
    start = markers[0] if markers else 0.0
    end = min(len(samples) / sample_rate, start + 12.0)
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    crop = mono[round(start * sample_rate):round(end * sample_rate)]
    warnings = ['速度 96 BPM 为编配默认值，未测得稳定节拍。', '乐谱经过量化，音符与时间需要回听核对。']
    notes = []
    voiced_ratio = 0.0
    if len(crop) >= sample_rate * 0.4 and np.sqrt(np.mean(crop ** 2)) >= 0.003:
        analysis_rate = 16000
        y = librosa.resample(crop, orig_sr=sample_rate, target_sr=analysis_rate)
        hop = 256
        f0, voiced, probability = librosa.pyin(y, fmin=70, fmax=1200, sr=analysis_rate,
                                             frame_length=1024, hop_length=hop)
        valid = voiced & np.isfinite(f0) & (probability >= 0.45)
        rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
        valid &= rms[:len(valid)] > 0.003
        voiced_ratio = float(np.mean(valid))
        pitches = np.full(len(f0), -1, dtype=int)
        pitches[valid] = np.rint(librosa.hz_to_midi(f0[valid])).astype(int)
        smoothed = median_filter(pitches, size=3)
        smoothed[~valid] = -1
        edges = np.flatnonzero(np.diff(smoothed, prepend=-2, append=-2))
        for left, right in zip(edges[:-1], edges[1:]):
            midi = int(smoothed[left])
            note_start = start + left * hop / analysis_rate
            note_end = min(end, start + right * hop / analysis_rate)
            if midi >= 0 and note_end - note_start >= 0.10:
                notes.append({'midi': midi, 'name': str(librosa.midi_to_note(midi, unicode=False)),
                              'start': round(note_start, 4), 'end': round(note_end, 4)})
    if not notes:
        warnings.append('没有取得可靠的单旋律音符；可回听、重新录制或用原声素材继续。')
    return {'notes': notes, 'abc': notes_to_abc(notes, start), 'pitch_provider': 'pyin',
            'source_range': {'start': round(start, 4), 'end': round(end, 4)},
            'voiced_ratio': round(voiced_ratio, 3), 'tempo': 96, 'tempo_source': 'default',
            'description': '已分析音高轨迹。' if notes else '未取得可靠旋律。',
            'sound_role': 'melody' if notes else 'texture', 'mood': '',
            'style_prompt': 'gentle instrumental music, warm piano, soft arrangement, no vocals',
            'gemini_used': False, 'warnings': warnings}


def source_track(samples, sample_rate, duration, output_rate=OUTPUT_RATE):
    original = librosa.resample(samples, orig_sr=sample_rate, target_sr=output_rate, axis=0)
    length = max(round(duration * output_rate), len(original) + 3 * output_rate)
    shape = (length,) if samples.ndim == 1 else (length, samples.shape[1])
    result = np.zeros(shape, dtype=np.float32)
    placements = [2 * output_rate]
    second = round(duration * 0.58 * output_rate)
    if second >= placements[0] + len(original) + output_rate and second + len(original) < length:
        placements.append(second)
    for position in placements:
        result[position:position + len(original)] += original
    return result, placements, length


def render_local(samples, sample_rate, analysis, duration):
    source, placements, length = source_track(samples, sample_rate, duration)
    melody = pretty_midi.Instrument(program=0)
    for position in placements:
        for note in analysis['notes']:
            onset = position / OUTPUT_RATE + note['start']
            offset = position / OUTPUT_RATE + note['end']
            melody.notes.append(pretty_midi.Note(velocity=85, pitch=note['midi'], start=onset, end=offset))
    melody_audio = melody.synthesize(fs=OUTPUT_RATE)
    melody_audio = np.pad(melody_audio[:length], (0, max(0, length - len(melody_audio))))
    if np.max(np.abs(melody_audio), initial=0) > 0:
        melody_audio = melody_audio / np.max(np.abs(melody_audio)) * 0.22
    accompaniment = pretty_midi.Instrument(program=89)
    root = (analysis['notes'][0]['midi'] if analysis['notes'] else 60) % 12 + 48
    for onset in np.arange(0, length / OUTPUT_RATE, 5.0):
        for pitch in (root, root + 7, root + 12):
            accompaniment.notes.append(pretty_midi.Note(velocity=55, pitch=pitch, start=float(onset),
                                                        end=min(float(onset + 4.8), length / OUTPUT_RATE)))
    bed = accompaniment.synthesize(fs=OUTPUT_RATE)
    bed = np.pad(bed[:length], (0, max(0, length - len(bed))))
    if np.max(np.abs(bed), initial=0) > 0:
        bed = bed / np.max(np.abs(bed)) * 0.14
    if source.ndim == 2:
        melody_audio = np.repeat(melody_audio[:, None], source.shape[1], axis=1)
        bed = np.repeat(bed[:, None], source.shape[1], axis=1)
    return {'source': source, 'melody': melody_audio, 'accompaniment': bed}, OUTPUT_RATE


def mix_tracks(tracks, gains):
    if not tracks:
        raise ValueError('没有可混音的轨道。')
    components = {name: samples.astype(np.float32) * float(gains.get(name, 1.0))
                  for name, samples in tracks.items()}
    if not all(np.isfinite(samples).all() for samples in components.values()):
        raise ValueError('轨道中包含无效数据。')
    mixed = sum(components.values())
    peak = float(np.max(np.abs(mixed), initial=0))
    component_peak = max(float(np.max(np.abs(value), initial=0)) for value in components.values())
    # Common gain preserves balance; cap quiet-recording amplification at 12 dB.
    scale = min(4.0, 10 ** (-1 / 20) / max(peak, component_peak)) if peak > 1e-5 else 1.0
    return mixed * scale, {name: samples * scale for name, samples in components.items()}


def test_sample():
    instrument = pretty_midi.Instrument(program=0)
    for index, pitch in enumerate([60, 64, 67, 69, 67, 64, 62, 60]):
        instrument.notes.append(pretty_midi.Note(velocity=100, pitch=pitch,
                                                start=index * 0.6, end=index * 0.6 + 0.5))
    samples = instrument.synthesize(fs=OUTPUT_RATE)
    samples = samples / max(1.0, float(np.max(np.abs(samples)))) * 0.3
    return encode_wav(samples, OUTPUT_RATE)
