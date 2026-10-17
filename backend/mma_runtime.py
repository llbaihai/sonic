"""CPU worker uploaded to AutoDL; no dependency installation at request time."""
import json
import math
import re
from pathlib import Path
import subprocess
import sys


def build_plan(request):
    import numpy as np
    notes = request['notes']
    duration, bpm = float(request['duration']), float(request['bpm'])
    placements = request['placements']
    groove = request.get('groove', 'Ballad')
    if groove not in {'Ballad', 'Ballad1', 'BalladSus'}:
        raise ValueError('请选择有效的伴奏风格。')
    chord_text = request.get('chords', '')
    if '\n' in chord_text or '\r' in chord_text:
        raise ValueError('和弦需要是空格分隔的单行。')
    user_chords = chord_text.split()
    if len(user_chords) > 64 or any(not re.fullmatch(r'[A-G](?:b|#)?(?:m|m7|maj7|7|6|sus4|dim)?', c) for c in user_chords):
        raise ValueError('和弦格式无效，例如 C Am F G7。')
    if (not notes or len(notes) > 4000 or not 2 <= duration <= 70 or
            not math.isfinite(duration) or not 60 <= bpm <= 140 or
            not placements or len(placements) > 4 or
            any(not math.isfinite(p) or p < 0 for p in placements)):
        raise ValueError('MMA 需要有效旋律、速度与时间范围。')
    for note in notes:
        if (type(note['midi']) is not int or not 0 <= note['midi'] <= 127 or
                not all(math.isfinite(note[k]) for k in ('start', 'end')) or
                not 0 <= note['start'] < note['end'] <= duration):
            raise ValueError('MMA 音符无效。')
    hist = np.zeros(12)
    for note in notes:
        hist[note['midi'] % 12] += note['end']-note['start']
    profiles = ([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88],
                [6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
    # Heuristic key candidate, not a calibrated probability or confirmed key.
    _, tonic, minor = max((float(np.dot(hist, np.roll(profile, root))), root, mode)
                          for mode, profile in enumerate(profiles) for root in range(12))
    names = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B']
    major_root = (tonic+3)%12 if minor else tonic
    candidates = []
    for offset, is_minor in [(0,False),(2,True),(4,True),(5,False),(7,False),(9,True)]:
        root = (major_root+offset)%12
        candidates.append((names[root]+('m' if is_minor else ''),
                           {root,(root+(3 if is_minor else 4))%12,(root+7)%12}))
    expanded = [dict(start=n['start']+p, end=n['end']+p, midi=n['midi'])
                for p in placements for n in notes if n['end']+p <= duration]
    if not expanded:
        raise ValueError('旋律超出工程范围。')
    bar_s = 4*60/bpm
    chords=[]
    for bar in range(math.ceil(duration/bar_s)):
        def score(item):
            name, pcs = item
            fit = sum(max(0,min(n['end'],(bar+1)*bar_s)-max(n['start'],bar*bar_s)) *
                      (2 if n['midi']%12 in pcs else -1) for n in expanded)
            return fit + (.12 if chords and name == chords[-1] else 0)
        chords.append(max(candidates,key=score)[0])
    if user_chords:
        chords = [user_chords[i % len(user_chords)] for i in range(len(chords))]
    return dict(notes=expanded,chords=chords,bpm=bpm,bpm_source=request.get('bpm_source', 'default'),
        tonic_pc=tonic,mode='minor' if minor else 'major',key_source='heuristic',
        chord_source='user' if user_chords else 'heuristic',
        groove=groove,duration=duration,placements=placements,
        warnings=['原声和旋律不变速；自由节奏与鼓点可能漂移。',
                  '自动和弦为规则候选，需试听；风格文字与 ABC 编辑不控制 MMA。'])


def main(directory):
    import numpy as np
    import pretty_midi as pm
    import soundfile as sf
    plan = build_plan(json.loads((directory/'mma-request.json').read_text()))
    text = f"Tempo {plan['bpm']}\nTime 4\nGroove {plan['groove']}\nVolume mp\n"
    text += '\n'.join(f'{i+1} {chord}' for i,chord in enumerate(plan['chords']))+'\n'
    (directory/'accompaniment.mma').write_text(text)
    def run(args, label):
        result=subprocess.run(args,capture_output=True,text=True,timeout=90)
        (directory/(label+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(label+': '+(result.stdout+result.stderr)[-800:])
    run(['/usr/bin/mma','-f',str(directory/'accompaniment.mid'),str(directory/'accompaniment.mma')],'mma')
    melody=pm.PrettyMIDI(initial_tempo=plan['bpm'])
    lead=pm.Instrument(10,name='Preserved melody')
    lead.notes=[pm.Note(85,n['midi'],n['start'],n['end']) for n in plan['notes']]
    melody.instruments.append(lead)
    melody.write(str(directory/'melody.mid'))
    decoded=pm.PrettyMIDI(str(directory/'melody.mid')).instruments[0].notes
    assert len(decoded)==len(lead.notes)
    assert all(a.pitch==b.pitch and abs(a.start-b.start)<.002 and abs(a.end-b.end)<.002
               for a,b in zip(lead.notes,decoded))
    plan['melody_unchanged']=True
    for name in ('melody','accompaniment'):
        run(['/usr/bin/fluidsynth','-ni','-g','.5','-R','0','-C','0','-T','wav',
             '-F',str(directory/(name+'.wav')),'-r','44100',
             '/usr/share/sounds/sf2/TimGM6mb.sf2',str(directory/(name+'.mid'))],name)
        samples,rate=sf.read(directory/(name+'.wav'),always_2d=True)
        assert rate==44100 and np.isfinite(samples).all() and np.any(samples)
        length=round(plan['duration']*rate)
        samples=np.pad(samples[:length],((0,max(0,length-len(samples))),(0,0)))
        samples[-min(4410,length):] *= np.linspace(1,0,min(4410,length))[:,None]
        sf.write(directory/(name+'.wav'),samples,rate,subtype='PCM_16')
    (directory/'mma-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main(Path(sys.argv[1]))
