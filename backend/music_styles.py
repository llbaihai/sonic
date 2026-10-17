"""Shared, explicit creative directions; no model-dependent hidden genres."""
from typing import Literal
Genre=Literal['classical','rock','pop','electronic']
STYLES={
 'classical':{'label':'古典','prompt':'Classical chamber music, acoustic grand piano and orchestral strings, expressive dynamics, no electronic dance beat or distorted rock guitar'},
 'rock':{'label':'摇滚','prompt':'Rock music, electric guitar riffs, electric bass, energetic acoustic drum kit, strong rhythmic accents'},
 'pop':{'label':'流行','prompt':'Pop music, memorable melodic hooks, polished piano or guitar arrangement, steady bass and balanced modern drums'},
 'electronic':{'label':'电子','prompt':'Electronic music, synthesizer leads, sequenced arpeggios, electronic drum machines and deep synth bass'}
}
def with_style(prompt,genre):
    return ((STYLES[genre]['prompt']+'. ') if genre else '')+prompt.strip()
