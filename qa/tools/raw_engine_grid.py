import sys,numpy as np,soundfile as sf
sys.path.insert(0,'.'); sys.path.insert(0,'qa/tools')
import p0_metrics as m
from pathlib import Path
from prototypes.aurora_p0.engine import Processor, RenderConfig
H={'ku100':('data/hrtf/thk_ku100/HRIR_L2702.sofa','thk_ku100'),'sadie':('data/hrtf/sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa','sadie2_d1')}
cats=sys.argv[1].split(','); sts=[float(s) for s in sys.argv[2].split(',')]
for cat in cats:
    x,fs=sf.read(f'data/assets/real_{cat}.wav',always_2d=True)
    if x.shape[1]==1: x=np.repeat(x,2,1)
    b=m.metrics(x,fs)
    for hk,(sofa,name) in H.items():
        for st in sts:
            cfg=RenderConfig(route='B',strength=st,preset='room',hrtf_path=Path(sofa),hrtf_name=name,eq_mode='frontal',directional_power=True,timbre_guard=False,apply_fixed_eq=True,seed=20261016,block_size=512,sample_rate=48000)
            p=Processor(); p.prepare(48000,512,cfg); y=np.asarray(p.process_file(np.ascontiguousarray(x.T)))
            if y.shape[0]==2 and y.shape[1]!=2: y=y.T
            a=m.metrics(y,fs)
            d={k:a['_levels'][k]-b['_levels'][k] for k in a['_levels'] if 40<=k<=16000}; med=np.median(list(d.values())); dev=max(abs(v-med) for v in d.values())
            print(f"{cat:8s} {hk:5s} s={st:.1f} IACC={a['iacc']:.3f} dry={b['iacc']:.3f} drop={b['iacc']-a['iacc']:+.3f} maxdev={dev:.2f}dB peak={a['peak_dbfs']:.1f} finite={a['finite']}",flush=True)
