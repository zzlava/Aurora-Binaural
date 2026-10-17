import sys,numpy as np,soundfile as sf
sys.path.insert(0,'.')
from aurora_core import Renderer
from scipy.signal import resample_poly
class C: input_channels=2; output_channels=2
class P:
    def __init__(s,pre,st): s.strength=st; s.preset=pre; s.seed=20261016
    def effective_strength(s): return s.strength
SOFA='data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa'
def run(x,route,st=1.0,lim=True):
    pre='V2' if route=='V' else 'room'
    r=Renderer('data/hrtf/sadie_d1_frontal.ahrp',SOFA,'presets/route_v.toml' if route=='V' else 'presets/p0_draft.toml',strength=st,preset=pre,route=route,hrtf_name='sadie2_d1',limiter=lim)
    r.prepare(48000,512,C()); r.set_params(P(pre,st)); L=r.latency_samples()
    xx=np.concatenate([x,np.zeros((L+48000,2))]); out=[]
    for i in range(0,len(xx),512): out.append(np.asarray(r.process(xx[i:i+512].astype(np.float32)),dtype=np.float64))
    return np.concatenate(out)[L:L+len(x)],L
x=np.zeros((48000*2,2)); x[48000,:]=0.5
for route in ('B','V'):
    y,L=run(x,route,lim=False); e=np.abs(y).sum(1)
    print(route,'latency',L,'peak at',int(np.argmax(e))-48000,'samples rel. to input impulse; first >-40dB at',int(np.argmax(e>e.max()*0.01))-48000)
el,_=sf.read('data/assets/holdout_electronic.wav',always_2d=True)
for route in ('B','V'):
    for st in (0.4,1.0):
        y,_=run(el,route,st); print(route,st,'TP',round(20*np.log10(np.max(np.abs(resample_poly(y,4,1,axis=0)))),2))
