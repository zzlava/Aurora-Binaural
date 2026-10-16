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
| 许可证 | **CC BY-SA 3.0**（Creative Commons Attribution-ShareAlike 3.0 Unported） |
| 许可证依据 | 1) SOFA 全局属性 `GLOBAL_License = "CC 3.0 BY-SA"`（本仓库文件内嵌）。2) 作者论文 AIA-DAGA 2013 原文：“The compilation is freely available under a Creative Commons CC BY-SA 3.0 license”（https://audiogroup.web.th-koeln.de/FILES/AIA-DAGA2013_HRIRs.pdf ，https://zenodo.org/records/3928297/files/AIA-DAGA2013_HRIRs.pdf ）。3) 下载页 https://audiogroup.web.th-koeln.de/ku100hrir.html 。 |
| 与 Zenodo 记录字段的差异 | Zenodo API `metadata.license.id` 为 `cc-by-3.0`（CC BY 3.0，无 ShareAlike），与文件内嵌属性和论文原文不一致。**再分发与衍生滤波器按文件和论文所载的 CC BY-SA 3.0 履行**；Zenodo 记录级字段未与文件对齐，不把它单独当成已确认的更宽松许可。 |
| 再分发与商用 | 允许复制、修改、再分发和商用。必须署名（作者 Benjamin Bernschütz / TH Köln，数据集名称，来源链接）。若分发修改后的 HRIR/滤波器，衍生数据须以 CC BY-SA 3.0 或兼容条款提供。许可证文本：https://creativecommons.org/licenses/by-sa/3.0/ |
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
| 许可证 | **Apache-2.0** |
| 许可证依据 | 1) 官方数据库页 https://www.york.ac.uk/sadie-project/database.html 写明 “Licensed under the Apache License, Version 2.0”，并链到 https://www.apache.org/licenses/LICENSE-2.0 。2) 本仓库 SOFA `GLOBAL_License` 为 University of York 2018 的 Apache-2.0 全文声明。3) Zenodo 记录 https://zenodo.org/records/10886409 的许可证字段为 Apache License 2.0；记录内 `LICENSE.txt` 为 Apache-2.0 文本。 |
| 再分发与商用 | 允许复制、修改、再分发和商用（含专利授权条款）。再分发时须附上 Apache-2.0 许可证、保留版权与归属声明、标明修改过的文件。官方页另要求：以原始或修改形式使用时注明原始数据集；学术使用请引用 DOI 10.3390/app8112029。无非商业限制。 |
| 引用 | Armstrong / Thresh / Murphy / Kearney, “A Perceptual Evaluation of Individual and Non-Individual HRTFs… SADIE II Database,” *Applied Sciences*, 2018. DOI: 10.3390/app8112029 |
| 采样率 | 48 kHz |
| 方向数量 | 见 SOFA（SADIE II D1 高密度网格；FIR 截断版） |
| 文件大小 | ≈ 35 MB（< 100 MB，经 Git LFS 提交） |
| SHA-256 | `e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31` |

## 使用规则

- 运行前必须存在上述两个 `.sofa` 文件。
- 采样率不一致时只重采样 HRIR，不重采样输入音频（plan §5.3）。
- 单个 SOFA > 100 MB 时不提交，仅在报告中说明下载方式。
