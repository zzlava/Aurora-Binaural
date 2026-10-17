import sys,numpy as np,soundfile as sf
sys.path.insert(0,'.')
from aurora_core import Renderer
class C: input_channels=2; output_channels=2
class P:
    def __init__(s,pre): s.strength=1.0; s.preset=pre; s.seed=20261016
    def effective_strength(s): return 1.0
SOFA='data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa'
x,_=sf.read('data/assets/real_voice.wav',always_2d=True); x=x[:48000*3]
def run(seq,route):
    pre='V2' if route=='V' else 'room'
    r=Renderer('data/hrtf/sadie_d1_frontal.ahrp',SOFA,'presets/route_v.toml' if route=='V' else 'presets/p0_draft.toml',strength=1.0,preset=pre,route=route,hrtf_name='sadie2_d1',limiter=False)
    r.prepare(48000,4096,C()); r.set_params(P(pre)); out=[];i=0;k=0
    while i<len(x):
        b=seq[k%len(seq)]; k+=1; out.append(np.asarray(r.process(x[i:i+b].astype(np.float32)),dtype=np.float64)); i+=b
    return np.concatenate(out)
worst={}
for route in ('V','B'):
    ref=run([256],route); w=-999
    for s in range(int(sys.argv[1])):
        seq=[int(v) for v in np.random.default_rng(5000+s).integers(1,4097,size=400)]
        y=run(seq,route); n=min(len(y),len(ref)); e=20*np.log10(np.max(np.abs(y[:n]-ref[:n]))+1e-30); w=max(w,e)
    worst[route]=w
print('worst dBFS over seeds',worst)
