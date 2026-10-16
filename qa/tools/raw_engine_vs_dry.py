import sys,numpy as np,soundfile as sf
sys.path.insert(0,'qa/p1/cursor'); sys.path.insert(0,'qa/tools'); sys.path.insert(0,'.')
import compare_route_b as c, p0_metrics as m
from pathlib import Path
for cat in ['music','voice','ambience']:
    x=c.read_wav(Path(f'data/assets/real_{cat}.wav')); p=np.asarray(c.render_prototype(x, Path('data/hrtf/thk_ku100/HRIR_L2702.sofa'),'thk_ku100',0.4))
    if p.shape[0]==2 and p.shape[1]!=2: p=p.T
    dry=np.asarray(x)
    if dry.ndim==2 and dry.shape[0]==2 and dry.shape[1]!=2: dry=dry.T
    if dry.ndim==1: dry=np.stack([dry,dry],1)
    a=m.metrics(p,48000); b=m.metrics(dry,48000)
    d={k:a['_levels'][k]-b['_levels'][k] for k in a['_levels'] if 40<=k<=16000}; med=np.median(list(d.values()))
    print(cat,'raw-engine IACC',round(a['iacc'],3),'dry',round(b['iacc'],3),'| bands >±3dB:',{int(k):round(v-med,1) for k,v in d.items() if abs(v-med)>3})
