# Process: Shot render — v1

Status: draft

How a shot render is made in the {label}. Edit the stages to match how your team really works, then confirm the card
(`Status: confirmed`) once a few tasks have run it.

| # | Stage | Input | Output | Gate | Passed by | Pattern card |
|---|---|---|---|---|---|---|
| 1 | Setup | `approved frame` | `scene file` | the scene matches the frame | lane | patterns/setup.md |
| 2 | Render | `scene file` | `frames` | no dropped or noisy frames | lane | patterns/render.md |
| 3 | Check | `frames` | `verdict` | timing and colour match the storyboard | review lane | patterns/check.md |
