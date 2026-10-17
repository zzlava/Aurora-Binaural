# HRTF data distributed with Aurora Binaural

The default image includes only **SADIE II, dummy head D1 (Neumann KU100), 48 kHz / 24-bit / 256-tap FIR SOFA**.

Copyright 2018 University of York.
Dataset creators: Cal Armstrong, Lewis Thresh, Gavin Kearney, University of York.
Dataset source: https://www.york.ac.uk/sadie-project/database.html
File source: https://sofacoustics.org/data/database/sadie/D1_48K_24bit_256tap_FIR_SOFA.sofa
SHA-256: `e6c72a84dd947b5ef75438ab96a9c2a32ed10f033472b9c4c11a49aff00a8a31`

The SOFA file is redistributed unmodified. License: **Apache License, Version 2.0**.
The complete license is included in `licenses/SADIE-II-Apache-2.0.txt`, also available at https://www.apache.org/licenses/LICENSE-2.0.txt.
Keep the license and original copyright / attribution notices when redistributing. Mark any future modified data files as modified.
Acknowledge the original SADIE dataset when using original or modified data.
Academic citation: Armstrong, Thresh, Murphy, Kearney, “A Perceptual Evaluation of Individual and Non-Individual HRTFs: A Case Study of the SADIE II Database”, Applied Sciences (2018), DOI https://doi.org/10.3390/app8112029.

The Rust engine verifies the source SOFA hash embedded in the bundled
`sadie_d1_frontal.ahrp` pack before applying it. The pack is derived from the
SADIE II SOFA using minimum-phase / frontal equalization processing; its SHA-256
is `9c3482d6a53351bb9deba559ab4638cc8ba920e77731896b28cbe1e42e5526de`.
Both the unmodified SOFA and the derived pack retain the attribution and Apache-2.0 license.
The explicitly selected passthrough stub does not apply any HRTF.

TH Köln KU100 L2702 (CC BY-SA 3.0) is **仅本地研究 (local research only)**.
Its SOFA and HRIR pack are excluded from the default image, wheel and source
distribution (ADR 0004). The read-only preset TOMLs contain optional
`thk_ku100` calibration tables for local research only; the application has no
KU100 selection control and always selects SADIE II.

This file documents bundled HRTF data. Python and frontend package licenses remain in their installed package distributions; Preact's MIT license is copied to `licenses/Preact-MIT.txt` in the image.
