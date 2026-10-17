import sys,numpy as np,soundfile as sf
sys.path.insert(0,'.')
from aurora_core import Renderer
class C: input_channels=2; output_channels=2
class P:
    strength=1.0; preset='V2'; seed=20261016
    def effective_strength(s): return 1.0
SOFA='data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa'
x,_=sf.read('data/assets/real_voice.wav',always_2d=True); x=x[:48000*2]
def run(seq,maxb=4096):
    r=Renderer('data/hrtf/sadie_d1_frontal.ahrp',SOFA,'presets/route_v.toml',strength=1.0,preset='V2',route='V',hrtf_name='sadie2_d1',limiter=False)
    r.prepare(48000,maxb,C()); r.set_params(P()); out=[];i=0
    for b in seq:
        if i>=len(x): break
        out.append(np.asarray(r.process(x[i:i+b].astype(np.float32)),dtype=np.float64)); i+=b
    return np.concatenate(out)
def first(a,b):
    n=min(len(a),len(b)); d=np.abs(a[:n]-b[:n]).max(1); return int(np.argmax(d>1e-6)) if (d>1e-6).any() else -1
ref=run([256]*1000)
for bs in []:# [2048,2049,3000,4000,4096]:
    print('fixed',bs,first(run([bs]*100),ref))
for seq in ():#([100,4000]*50,[4000,100]*50,[1,4095]*50,[3000,3000]*50):
    print(seq[:2],first(run(seq),ref))
print('search')
for s in range(12):
    g=np.random.default_rng(s); seq=[int(v) for v in g.integers(1,4097,size=200)]
    f=first(run(seq),ref)
    if f>=0:
        cum=np.cumsum([0]+seq); k=int(np.searchsorted(cum,f,side='right'))-1
        print('seed',s,'first',f,'block idx',k,'sizes around',seq[max(0,k-2):k+2],'starts',cum[max(0,k-2):k+2].tolist())
