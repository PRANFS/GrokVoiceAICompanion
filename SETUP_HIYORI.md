# Hiyori Character Generation Setup

## Overview

This project uses **Hiyori Momose (hiyori_pro_t11)** — Live2D's official PRO sample model — as the base for character generation. The Textoon-inspired pipeline recolors Hiyori's texture atlases using HSV region masks and luminance-preserving color transfer, all driven by Grok APIs.

The PRO version includes arm pose switching (pose3.json) which is integrated with the emotion system — expressive emotions like happy, excited, and surprised automatically raise the arms, while calm emotions keep them relaxed.

The Hiyori model ships with this repo in `static/models/hiyori/`. If for some reason the files are missing, follow the download instructions below.

---

## Prerequisites

1. **Python 3.10+** with the project's virtual environment activated
2. **xAI API key** in your `.env` file:
   ```
   XAI_API_KEY=xai-your-key-here
   ```
3. **Python packages** (already in `requirements.txt`):
   ```bash
   pip install -r requirements.txt
   ```
   Key dependencies: `Pillow>=10.0.0`, `numpy>=1.26.0`, `httpx`

---

## Downloading Hiyori (Manual Setup)

The model files should already be in `static/models/hiyori/` if you cloned this repo. If they're missing:

### Download from Live2D Website

1. Go to the [Live2D Sample Data](https://www.live2d.com/en/learn/sample/) page
2. Download the **Hiyori PRO** sample (hiyori_pro_t11)
3. Extract and place the files so the structure looks like:

```
static/models/hiyori/
├── hiyori_pro_t11.model3.json      ← required
├── hiyori_pro_t11.moc3             ← required
├── hiyori_pro_t11.physics3.json
├── hiyori_pro_t11.pose3.json       ← arm pose switching
├── hiyori_pro_t11.cdi3.json
├── hiyori_pro_t11.cmo3
├── hiyori_pro_t04.can3
├── hiyori_pro_t11.2048/
│   ├── texture_00.png              ← required
│   └── texture_01.png              ← required
├── motion/
│   ├── hiyori_m01.motion3.json
│   ├── hiyori_m02.motion3.json
│   ├── ... (m03 through m10)
│   └── hiyori_m10.motion3.json
└── ReadMe.txt
```

### Verify

Run the server — if the model is present you'll see:
```
✅ Hiyori model found - character generation ready
```

If not, you'll see which files are missing:
```
⚠️ Hiyori model not found (missing: ['hiyori_pro_t11.model3.json', ...])
```

---

## How It Works

### Pipeline Flow

```
User Prompt  →  Grok Chat API  →  Color Attributes (JSON)
                                       ↓
              Grok Imagine API  →  Reference Artwork (thumbnail)
                                       ↓
             Original Hiyori Textures + HSV Region Masks
                                       ↓
            Per-Region Color Transfer (luminance-preserving)
                                       ↓
              Recolored Model  →  static/models/generated/
```

### What Gets Recolored

The pipeline identifies 7 color regions on Hiyori's texture atlases using HSV filtering:

| Region | Original Color | What It Covers |
|--------|---------------|----------------|
| `hair` | Dark brown | Bangs, side hair, back hair, ahoge |
| `eye_iris` | Green | Both eye irises |
| `skin` | Light peach | Face, neck, arms, hands, legs |
| `clothing_white` | White | School uniform top |
| `clothing_dark` | Navy blue | Sailor collar, skirt, accents |
| `ribbon` | Red | Hair ribbon, chest bow |
| `shoes` | Brown | Loafers/shoes |

### Region Masking (Textoon-Style Part Mapping)

Instead of Textoon's coordinate-based part-swapping (which requires hundreds of pre-drawn template assets), this pipeline uses **HSV color analysis** to build region masks:

1. Convert each texture atlas to HSV color space
2. For each region, apply HSV range filters to identify matching pixels
3. Process regions in priority order — higher priority regions "claim" pixels first
4. Apply morphological cleanup to remove noise
5. Cache masks for instant reuse on subsequent generations

This achieves the same result as Textoon's part-based system but without needing model-specific template assets. The trade-off is that we recolor existing geometry rather than swapping parts, so the silhouette stays the same.

---

## File Structure

```
app/
  character_generator.py   ← Main pipeline (parse, mask, recolor, package)
  textoon_config.json      ← HSV region definitions
  main.py                  ← API endpoints (/generate-character, /model-status, etc.)

static/models/
  hiyori/                  ← Base model (ships with repo)
  generated/               ← Generated models (generated_YYYYMMDD-HHMMSS/)
    generated_*/
      hiyori_pro_t11.model3.json    ← Working Live2D model (load this in browser)
      hiyori_pro_t11.2048/           ← Recolored texture atlases
      thumbnail.png                  ← AI-generated reference artwork
      metadata.json                  ← Prompt, attributes, paths

static/js/
  app.js                   ← Frontend generation UI
  live2d-avatar.js         ← Live2D rendering + emotion system + arm pose switching
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/generate-character` | Generate new character from prompt |
| `GET` | `/generated-models` | List all generated models |
| `DELETE` | `/generated-models/{id}` | Delete a generated model |
| `GET` | `/model-status` | Check base model availability |

### Generate Character Request

```json
POST /generate-character
{
  "prompt": "cute elf girl with silver hair and purple eyes, wearing a dark gothic dress"
}
```

### Response

```json
{
  "success": true,
  "model": {
    "id": "generated_20250101-120000",
    "prompt": "cute elf girl with silver hair...",
    "model_path": "/static/models/generated/generated_20250101-120000/hiyori_pro_t11.model3.json",
    "thumbnail_url": "/static/models/generated/generated_20250101-120000/thumbnail.png",
    "attributes": {
      "hair_color": "#C0C0C0",
      "eye_color": "#8B00FF",
      "skin_tone": "#FFF0E6",
      "top_color": "#1A1A2E",
      "bottom_color": "#2D1B46",
      "accent_color": "#6B3FA0",
      "shoe_color": "#1A1A1A",
      "style_note": "..."
    }
  }
}
```

---

## Tuning HSV Regions

If the recoloring isn't accurate (e.g., hair color bleeds into skin), you can tune the HSV ranges in `app/textoon_config.json`:

1. Open `static/models/hiyori/hiyori_pro_t11.2048/texture_00.png` in an image editor (GIMP, Photoshop)
2. Use the color picker in HSV mode to sample pixels in the region you want to adjust
3. Note: PIL uses H: 0-255 (not 0-360), S: 0-255, V: 0-255
4. Update the `hsv_filter` values in the config
5. Delete the mask cache to force rebuilding:
   ```bash
   del static\models\hiyori\_region_masks.npz
   ```
   Or call the `/model-status` endpoint which will show the current state.

### Priority System

Regions are processed in `priority` order (lowest = first). This means:
- `eye_iris` (priority 0) claims green pixels before `hair` can
- `ribbon` (priority 1) claims red pixels before anything else
- `clothing_white` (priority 6) gets remaining near-white pixels last

---

## Hiyori Model Info

- **Creator**: Kani Biimu (illustration) / Live2D Inc. (modeling)
- **Format**: Live2D Cubism 3/4 (.moc3)
- **Resolution**: 2048×2048 texture atlases (2 textures)
- **Features**: Full body, arm pose switching (pose3.json), 10 motions, physics, eye blink, lip sync, glue on shoulders
- **Parameters**: Standard Cubism 4 set (ParamAngleX/Y/Z, ParamEyeLOpen, ParamMouthOpenY, etc.) plus body/arm/hair parameters
- **Pose Groups**: PartArmA (relaxed) / PartArmB (raised) — integrated with emotion system
- **License**: [Live2D Free Material License Agreement](https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html) — free for development and personal use
- **Source**: https://www.live2d.com/en/learn/sample/

---

## Troubleshooting

**"Hiyori model not found"**
- Follow the download instructions above
- Ensure files are in `static/models/hiyori/` (not a nested subfolder)
- The 4 essential files: `hiyori_pro_t11.model3.json`, `hiyori_pro_t11.moc3`, `hiyori_pro_t11.2048/texture_00.png`, `hiyori_pro_t11.2048/texture_01.png`

**Colors look wrong / bleeding between regions**
- Delete `static/models/hiyori/_region_masks.npz` (mask cache)
- Adjust HSV ranges in `app/textoon_config.json`
- Check the pipeline logs — it prints pixel counts per region

**Generation takes too long**
- First run builds region masks (~2-3 seconds, cached after)
- Grok Imagine API can take 10-30 seconds for image generation
- The model packaging (file copy) is nearly instant
