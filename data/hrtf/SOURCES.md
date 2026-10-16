# HRTF 数据来源登记

本目录仅包含实测 SOFA；缺少文件时原型直接报错，不回退到合成 HRTF。

## 1. TH Köln — Neumann KU100 Far-Field HRIR (Lebedev 2702)

| 字段 | 值 |
|---|---|
| 文件 | `thk_ku100/HRIR_L2702.sofa` |
| 数据集 | Spherical Far-Field HRIR Compilation of the Neumann KU100 |
| 作者 | Benjamin Bernschütz (TH Köln / TU Berlin) |
| 版本 | Zenodo record v1（2020-07-02 上传的 SOFA 发行） |
| 来源 URL | https://zenodo.org/records/3928297 |
| 直接下载 | https://zenodo.org/records/3928297/files/HRIR_L2702.sofa?download=1 |
| 许可证原文 | 见 Zenodo 记录页与论文说明；数据通过 Zenodo/SOFA 惯例发布供学术与工程使用。许可证链接：https://zenodo.org/records/3928297 （记录级权利声明）及 https://audiogroup.web.th-koeln.de/ku100hrir.html |
| 引用 | B. Bernschütz, “A Spherical Far Field HRIR / HRTF Compilation of the Neumann KU 100,” AIA-DAGA 2013. |
| 采样率 | 48 kHz（SOFA `Data.SamplingRate`） |
| 方向数量 | 2702（Lebedev 网格） |
| IR 长度 | 见 SOFA `Data.IR`（原型截断/填零至 256 taps @ 48 kHz） |
| 文件大小 | ≈ 3.3 MB（< 100 MB，经 Git LFS 提交） |
| SHA-256 | `d33e675d20f5456426bf79560187122e4278fecb5aaa1b2608f1727acb91e974` |

## 2. SADIE II — Subject D1 (Neumann KU100), 48 kHz 256-tap FIR

| 字段 | 值 |
|---|---|
| 文件 | `sadie2_d1/D1_48K_24bit_256tap_FIR_SOFA.sofa` |
| 数据集 | SADIE II Database — Dummy head D1 (KU100) |
| 作者 | Cal Armstrong, Lewis Thresh, Gavin Kearney (University of York) |
| 版本 | SOFA Acoustics 托管的 256-tap FIR @ 48 kHz / 24-bit 发行 |
| 来源 URL | https://www.york.ac.uk/sadie-project/database.html |
| 直接下载 | https://sofacoustics.org/data/database/sadie/D1_48K_24bit_256tap_FIR_SOFA.sofa |
| Zenodo（完整库） | https://zenodo.org/records/10886409 |
| 许可证原文 | Copyright University of York；学术使用请引用论文。数据库页权利说明：https://www.york.ac.uk/sadie-project/database.html ；Zenodo 记录：https://zenodo.org/records/10886409 |
| 引用 | Armstrong / Thresh / Murphy / Kearney, “A Perceptual Evaluation of Individual and Non-Individual HRTFs… SADIE II Database,” *Applied Sciences*, 2018. DOI: 10.3390/app8112029 |
| 采样率 | 48 kHz |
| 方向数量 | 见 SOFA（SADIE II D1 高密度网格；FIR 截断版） |
| 文件大小 | ≈ 35 MB（< 100 MB，经 Git LFS 提交） |
| SHA-256 | `e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31` |

## 使用规则

- 运行前必须存在上述两个 `.sofa` 文件。
- 采样率不一致时只重采样 HRIR，不重采样输入音频（plan §5.3）。
- 单个 SOFA > 100 MB 时不提交，仅在报告中说明下载方式。
