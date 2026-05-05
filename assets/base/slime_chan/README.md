# slime_chan Asset Package

Reference image:

- `assets/base/slime_chan/slime_chan.png`

Core package files:

- `pet_manifest.json`
- `animation_index.json`
- `animation_index.seed.json`
- `base_prompt_requests.json`

Completed base motions:

- `IDLE_NEUTRAL`
- `WALK`
- `FORCE_SLEEP`
- `CARRIED`
- `STROKE`
- `PLAY`
- `EAT`
- `BATH`
- `DOUBLE_CLICK`
- `DROWSY`
- `FORCE_HUNGRY`
- `FORCE_DIRTY`
- `FORCE_SAD`

Regenerate one motion:

```powershell
python scripts/generate_character_motion.py `
  --asset-id slime_chan `
  --slot EAT `
  --overwrite
```

All current motion videos are 512x768 and follow the reference image aspect ratio.
