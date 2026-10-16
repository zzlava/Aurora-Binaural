# P0 听感试听

1. 在 Finder 中打开 `qa/p0/listening/index.html`（用 Safari 或 Chrome），戴上耳机。
2. 人声、音乐、环境音三组，每组都有原声参照和 8–10 个版本；可以勾选"盲听"。
3. 按 空间感／清晰度／音色自然／不疲劳 四项打 1–5 分。打完点"生成结果"，把文字复制给 Claude。

需要决定的事项（见 docs/DECISIONS-PENDING.md U2／U3）：

- 默认算法路线：B（上混 + HRTF）还是 C（上混 + Ambisonics）
- 默认 HRTF：KU100（CC BY-SA 3.0）还是 SADIE II（Apache-2.0）
- 默认强度是否合适：音乐另有 0.2 和 1.0 两档可听
