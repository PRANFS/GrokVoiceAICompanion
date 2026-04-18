"""Character Generator - Color Transfer Pipeline (Hiyori Edition)

Generates customized Live2D characters by recoloring the Hiyori Momose
base model textures using luminance-preserving color transfer with
region masks. Optionally generates AI reference artwork for thumbnails.

Pipeline:
1. Parse user prompt → color attributes via Grok chat API
2. (High quality) Generate reference artwork via Grok Imagine API
3. Build HSV-based region masks from original textures (cached)
4. Apply per-region luminance-preserving color transfer with edge feathering
5. Package edited model into static/models/generated/

Requires: Hiyori model files in static/models/hiyori/ (see SETUP_HIYORI.md)

No ComfyUI, no GPU, no SDXL. Pure Python (PIL + numpy) + Grok API.
"""

import os
import json
import time
import base64
import shutil
import logging
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, Tuple

import httpx
import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
HIYORI_DIR = BASE_DIR / "static" / "models" / "hiyori"
GENERATED_DIR = BASE_DIR / "static" / "models" / "generated"
CONFIG_PATH = Path(__file__).parent / "textoon_config.json"
MASK_CACHE_PATH = HIYORI_DIR / "_region_masks.npz"

# Grok API endpoints
GROK_CHAT_URL = "https://api.x.ai/v1/chat/completions"
GROK_IMAGINE_URL = "https://api.x.ai/v1/images/generations"


# ─── Config ───────────────────────────────────────────────────────────────────

def load_textoon_config() -> dict:
    """Load the Textoon model configuration."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Asset Check ─────────────────────────────────────────────────────────────

def check_hiyori_model() -> dict:
    """
    Check if Hiyori model files are present in static/models/hiyori/.
    See SETUP_HIYORI.md for download instructions.
    """
    required = {
        "hiyori_pro_t11.model3.json": HIYORI_DIR / "hiyori_pro_t11.model3.json",
        "hiyori_pro_t11.moc3": HIYORI_DIR / "hiyori_pro_t11.moc3",
        "texture_00.png": HIYORI_DIR / "hiyori_pro_t11.2048" / "texture_00.png",
        "texture_01.png": HIYORI_DIR / "hiyori_pro_t11.2048" / "texture_01.png",
    }

    missing = [name for name, path in required.items() if not path.exists()]

    return {
        "available": len(missing) == 0,
        "missing": missing,
        "model_path": "/static/models/hiyori/hiyori_pro_t11.model3.json",
    }


# ─── Region Mask Building ────────────────────────────────────────────────────

def build_region_masks(config: dict) -> Dict[str, Dict[str, np.ndarray]]:
    """
    Build boolean masks for each color region from the original Hiyori textures.

    Uses HSV color analysis with priority-based claiming:
    - Regions are processed in priority order (lowest number = highest priority)
    - Once a pixel is claimed by a region, it cannot be claimed by a later one
    - Alpha channel filters out transparent (non-drawable) pixels
    - Morphological cleanup removes small isolated pixel clusters

    Returns: {"texture_00": {"hair": mask, ...}, "texture_01": {...}}
    """
    # Check for cached masks
    if MASK_CACHE_PATH.exists():
        logger.info("📦 Loading cached region masks")
        try:
            cache = np.load(MASK_CACHE_PATH, allow_pickle=False)
            masks = {}
            for key in cache.files:
                parts = key.split("__", 1)
                if len(parts) == 2:
                    tex_name, region_name = parts
                    if tex_name not in masks:
                        masks[tex_name] = {}
                    masks[tex_name][region_name] = cache[key]
            return masks
        except Exception as e:
            logger.warning(f"⚠️ Cache load failed, rebuilding: {e}")

    logger.info("🔍 Building region masks from original Hiyori textures...")
    regions = config["regions"]
    sorted_regions = sorted(regions.items(), key=lambda x: x[1].get("priority", 99))

    texture_dir = HIYORI_DIR / "hiyori_pro_t11.2048"
    all_masks: Dict[str, Dict[str, np.ndarray]] = {}
    masks_to_cache: Dict[str, np.ndarray] = {}

    for tex_idx in range(config.get("texture_count", 2)):
        tex_name = f"texture_{tex_idx:02d}"
        tex_path = texture_dir / f"{tex_name}.png"

        if not tex_path.exists():
            logger.warning(f"  ⚠️ Texture not found: {tex_path}")
            continue

        texture = Image.open(tex_path).convert("RGBA")
        height, width = texture.size[1], texture.size[0]

        # Get alpha channel — only process visible (non-transparent) pixels
        rgba = np.array(texture)
        alpha = rgba[:, :, 3]
        visible = alpha > 10

        # Convert to PIL HSV → numpy (H: 0-255, S: 0-255, V: 0-255)
        hsv_image = texture.convert("HSV")
        hsv = np.array(hsv_image)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

        # Track which pixels are already claimed
        claimed = np.zeros((height, width), dtype=bool)
        tex_masks: Dict[str, np.ndarray] = {}

        for region_name, region_config in sorted_regions:
            # Skip region if restricted to specific texture indices
            texture_only = region_config.get("texture_only")
            if texture_only is not None and tex_idx not in texture_only:
                tex_masks[region_name] = np.zeros((height, width), dtype=bool)
                continue

            filt = dict(region_config["hsv_filter"])  # Copy so we can override
            min_area = region_config.get("min_area", 50)

            # Apply per-texture filter overrides (e.g. different v_max per texture)
            per_texture_overrides = region_config.get("per_texture", {})
            tex_override = per_texture_overrides.get(str(tex_idx), {})
            filt.update(tex_override)

            # HSV filter
            if filt.get("wrap_hue"):
                # Red hue wraps around 0/255
                h_match = (h >= filt.get("h_min_alt", filt["h_min"])) | (
                    h <= filt["h_max"]
                )
            else:
                h_match = (h >= filt["h_min"]) & (h <= filt["h_max"])

            s_match = (s >= filt["s_min"]) & (s <= filt["s_max"])
            v_match = (v >= filt["v_min"]) & (v <= filt["v_max"])

            mask = visible & h_match & s_match & v_match & ~claimed

            # Morphological cleanup — remove small isolated pixels
            pixel_count = int(np.sum(mask))
            if pixel_count > min_area:
                mask_img = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
                # Erode → dilate (opening) to remove noise
                mask_img = mask_img.filter(ImageFilter.MinFilter(3))
                mask_img = mask_img.filter(ImageFilter.MaxFilter(3))
                mask = np.array(mask_img) > 127
            elif pixel_count > 0 and pixel_count <= min_area:
                # Too small — discard this region on this texture
                mask = np.zeros((height, width), dtype=bool)

            tex_masks[region_name] = mask
            claimed |= mask

            pixel_count = int(np.sum(mask))
            if pixel_count > 0:
                logger.info(f"  {tex_name}/{region_name}: {pixel_count:,} pixels")

        all_masks[tex_name] = tex_masks

        for region_name, mask in tex_masks.items():
            cache_key = f"{tex_name}__{region_name}"
            masks_to_cache[cache_key] = mask

    # Cache masks for subsequent runs
    try:
        np.savez_compressed(str(MASK_CACHE_PATH), **masks_to_cache)
        logger.info(f"💾 Cached region masks to {MASK_CACHE_PATH.name}")
    except Exception as e:
        logger.warning(f"⚠️ Could not cache masks: {e}")

    return all_masks


def invalidate_mask_cache():
    """Delete the cached region masks (e.g. after config changes)."""
    if MASK_CACHE_PATH.exists():
        MASK_CACHE_PATH.unlink()
        logger.info("🗑️ Region mask cache invalidated")


# ─── Prompt Parsing via Grok API ─────────────────────────────────────────────

ATTRIBUTE_PARSING_PROMPT = """You are a character color attribute extraction AI. Given a text description of an anime/cartoon character, extract structured color attributes as JSON.

Output ONLY valid JSON with these exact keys:
{
  "hair_color": hex color string like "#FF69B4" for hair,
  "eye_color": hex color string like "#4169E1" for eye iris color,
  "skin_tone": hex color string like "#FFE0C4" for skin,
  "top_color": hex color string for main outfit/top,
  "bottom_color": hex color string for skirt/pants/lower garment,
  "accent_color": hex color string for ribbon/accessories/trim,
  "shoe_color": hex color string for shoes,
  "style_note": short free-form description for artwork generation
}

Rules:
- Extract colors that faithfully match the description
- If something is not specified, use sensible anime-style defaults
- Always output ALL keys
- hair_color, eye_color, skin_tone, top_color, bottom_color, accent_color, shoe_color are ALWAYS hex strings
- style_note is a short text summarizing the character's look for image generation
- Output ONLY the JSON object, no markdown formatting, no extra text

Example input: "cute cyberpunk fox girl with glowing tattoos, pink hair, neon outfit"
Example output:
{"hair_color":"#FF69B4","eye_color":"#00FFFF","skin_tone":"#FFE0C4","top_color":"#0D0D2B","bottom_color":"#1A0A2E","accent_color":"#FF00FF","shoe_color":"#2D2D2D","style_note":"cyberpunk fox girl with glowing neon tattoos and futuristic outfit"}

Example input: "elegant school girl with blue eyes and blonde hair"
Example output:
{"hair_color":"#F5DEB3","eye_color":"#4169E1","skin_tone":"#FFE0C4","top_color":"#FFFFFF","bottom_color":"#1B3A5C","accent_color":"#E04050","shoe_color":"#2D1810","style_note":"elegant blonde school girl with blue eyes in uniform"}"""


async def parse_prompt_to_attributes(prompt: str, api_key: str) -> dict:
    """
    Use Grok chat API to parse a text prompt into structured color attributes.
    Replaces Textoon's Qwen2.5-based PromptAttributeExtractor.
    """
    logger.info(f"🔍 Parsing prompt: {prompt[:80]}...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GROK_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "grok-4-1-fast-non-reasoning",
                "messages": [
                    {"role": "system", "content": ATTRIBUTE_PARSING_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Grok API error {response.status_code}: {response.text}"
            )

        result = response.json()
        content = (
            result.get("choices", [{}])[0].get("message", {}).get("content", "")
        )

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines)

        attributes = json.loads(content)
        logger.info(f"✅ Parsed attributes: {json.dumps(attributes, indent=2)}")
        return attributes


# ─── Reference Image Generation via Grok Imagine ────────────────────────────

async def generate_reference_image(
    attributes: dict, prompt: str, api_key: str
) -> Optional[Image.Image]:
    """
    Generate a reference character image using Grok Imagine API.
    This image is now critical — used both as a visual reference for AI texture
    editing AND as the gallery thumbnail. Returns PIL Image or None.
    """
    style_note = attributes.get("style_note", "anime girl character")
    hair_color = attributes.get("hair_color", "#2B1D0E")
    eye_color = attributes.get("eye_color", "#4169E1")
    top_color = attributes.get("top_color", "#FFFFFF")
    bottom_color = attributes.get("bottom_color", "#1B3A5C")
    accent_color = attributes.get("accent_color", "#E04050")
    shoe_color = attributes.get("shoe_color", "#2D1810")

    img_prompt = (
        f"Highly detailed 2D anime girl character, front-facing T-pose, full body from head to toe. "
        f"{style_note}. "
        f"Hair color {hair_color}, eye color {eye_color}. "
        f"Top/outfit color {top_color}, bottom/skirt color {bottom_color}, "
        f"accent/accessory color {accent_color}, shoe color {shoe_color}. "
        f"Character description: {prompt}. "
        f"Clean solid white background, flat even studio lighting, no shadows on background, "
        f"anime art style, high quality, vivid colors, sharp details."
    )

    logger.info(f"🎨 Generating reference image...")

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                GROK_IMAGINE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "grok-imagine-image",
                    "prompt": img_prompt,
                    "n": 1,
                    "response_format": "b64_json",
                },
            )

            if response.status_code != 200:
                logger.error(
                    f"❌ Grok Imagine error {response.status_code}: {response.text[:200]}"
                )
                return None

            result = response.json()
            b64_data = result.get("data", [{}])[0].get("b64_json")

            if not b64_data:
                # Fallback: try URL format
                url = result.get("data", [{}])[0].get("url")
                if url:
                    img_resp = await client.get(url)
                    if img_resp.status_code == 200:
                        image = Image.open(BytesIO(img_resp.content)).convert("RGBA")
                        logger.info(f"✅ Reference image generated (URL fallback): {image.size}")
                        return image
                logger.error("❌ No image data in Grok Imagine response")
                return None

            image_bytes = base64.b64decode(b64_data)
            image = Image.open(BytesIO(image_bytes)).convert("RGBA")
            logger.info(f"✅ Reference image generated: {image.size}")
            return image

    except Exception as e:
        logger.error(f"❌ Error generating reference image: {e}")
        return None





# ─── Color Transfer Engine (Fallback) ────────────────────────────────────────

def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (128, 128, 128)
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _feather_mask(mask: np.ndarray, radius: int = 3) -> np.ndarray:
    """
    Apply Gaussian feathering to a boolean mask to create soft edges.
    Returns a float mask (0.0-1.0) with smooth transitions at region boundaries.
    """
    if radius <= 0:
        return mask.astype(np.float32)
    mask_img = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.array(mask_img).astype(np.float32) / 255.0


def apply_color_transfer_masked(
    image: Image.Image,
    mask: np.ndarray,
    original_rgb: Tuple[int, int, int],
    target_rgb: Tuple[int, int, int],
    preserve_detail: float = 0.85,
    feather_radius: int = 2,
) -> Image.Image:
    """
    Apply luminance-preserving color transfer to masked pixels with edge feathering.

    Algorithm:
    1. Feather mask edges for smooth transitions (no hard color boundaries)
    2. Compute per-pixel luminance ratio relative to region's original color
    3. Apply target color modulated by luminance ratio
    4. Blend with original using the feathered mask

    This preserves all shading, highlights, and texture detail while changing
    the base color of the region. Edge feathering prevents visible seams.

    Args:
        image: RGBA PIL Image
        mask: Boolean numpy array (True = pixels to modify)
        original_rgb: Average RGB of the region in the original texture
        target_rgb: Desired new RGB for the region
        preserve_detail: 0-1, how much shading detail to preserve (0.85 = good balance)
        feather_radius: Gaussian blur radius for mask edge softening (0 = hard edges)
    """
    if not np.any(mask):
        return image

    data = np.array(image, dtype=np.float32)
    original_data = data.copy()

    # Feather the mask for smooth edges
    soft_mask = _feather_mask(mask, feather_radius)

    # Compute original region luminance (the "base brightness" of the original color)
    orig_lum = (
        0.299 * original_rgb[0] + 0.587 * original_rgb[1] + 0.114 * original_rgb[2]
    )
    if orig_lum < 1.0:
        orig_lum = 1.0

    # Per-pixel luminance
    pixel_lum = (
        0.299 * data[:, :, 0] + 0.587 * data[:, :, 1] + 0.114 * data[:, :, 2]
    )
    lum_ratio = pixel_lum / orig_lum
    # Clamp extreme ratios to prevent blowout
    lum_ratio = np.clip(lum_ratio, 0.05, 3.0)

    # Target color as floats
    tr, tg, tb = float(target_rgb[0]), float(target_rgb[1]), float(target_rgb[2])

    # Luminance-modulated color transfer:
    # new_pixel = target * lum_ratio * preserve + target * (1-preserve)
    detail = preserve_detail
    flat = 1.0 - preserve_detail

    new_r = np.clip(tr * lum_ratio * detail + tr * flat, 0, 255)
    new_g = np.clip(tg * lum_ratio * detail + tg * flat, 0, 255)
    new_b = np.clip(tb * lum_ratio * detail + tb * flat, 0, 255)

    # Blend transferred color with original using the soft mask
    # soft_mask=1.0 → fully recolored, soft_mask=0.0 → original, soft_mask=0.5 → blended
    blend = soft_mask[:, :, np.newaxis]  # (H, W, 1) for broadcasting
    recolored = np.stack([new_r, new_g, new_b], axis=-1)
    blended_rgb = original_data[:, :, :3] * (1.0 - blend) + recolored * blend

    data[:, :, 0] = blended_rgb[:, :, 0]
    data[:, :, 1] = blended_rgb[:, :, 1]
    data[:, :, 2] = blended_rgb[:, :, 2]
    # Alpha channel untouched

    return Image.fromarray(data.astype(np.uint8), image.mode)


# ─── Texture Processing Pipeline ─────────────────────────────────────────────

def process_textures(
    config: dict,
    attributes: dict,
    masks: Dict[str, Dict[str, np.ndarray]],
) -> Dict[str, Image.Image]:
    """
    Apply per-region color transfer to all Hiyori texture atlases.

    For each texture:
    1. Load the original PNG
    2. For each color region that has a user-specified target color:
       a. Look up the region mask for this texture
       b. Apply luminance-preserving color transfer
    3. Return the modified textures

    Returns: dict mapping texture name (e.g. "texture_00") to modified PIL Image
    """
    regions = config["regions"]
    attr_map = config["attribute_to_region_map"]

    # Build color map: region_name → (original_rgb, target_rgb)
    color_map: Dict[str, Tuple[Tuple[int, ...], Tuple[int, ...]]] = {}
    for attr_name, region_name in attr_map.items():
        target_hex = attributes.get(attr_name)
        if target_hex and region_name in regions:
            original_rgb = tuple(regions[region_name]["default_rgb"])
            target_rgb = hex_to_rgb(target_hex)
            # Skip if target is very similar to original (no change needed)
            diff = sum(abs(a - b) for a, b in zip(original_rgb, target_rgb))
            if diff > 30:
                color_map[region_name] = (original_rgb, target_rgb)

    logger.info(f"🎨 Color map: {len(color_map)} regions to recolor")
    for name, (orig, tgt) in color_map.items():
        logger.info(f"  {name}: RGB{orig} → RGB{tgt}")

    texture_dir = HIYORI_DIR / "hiyori_pro_t11.2048"
    textures: Dict[str, Image.Image] = {}

    for tex_idx in range(config.get("texture_count", 2)):
        tex_name = f"texture_{tex_idx:02d}"
        tex_path = texture_dir / f"{tex_name}.png"

        if not tex_path.exists():
            logger.warning(f"  ⚠️ Missing: {tex_path}")
            continue

        texture = Image.open(tex_path).convert("RGBA")
        tex_masks = masks.get(tex_name, {})
        modified = False

        for region_name, (orig_rgb, tgt_rgb) in color_map.items():
            mask = tex_masks.get(region_name)
            if mask is not None and np.any(mask):
                texture = apply_color_transfer_masked(
                    texture, mask, orig_rgb, tgt_rgb
                )
                modified = True
                logger.info(
                    f"  🖌️ {tex_name}/{region_name}: "
                    f"RGB{orig_rgb} → RGB{tgt_rgb} "
                    f"({int(np.sum(mask)):,} px)"
                )

        if modified:
            textures[tex_name] = texture

    return textures


# ─── Model Packaging ─────────────────────────────────────────────────────────

def package_model(
    textures: Dict[str, Image.Image],
    attributes: dict,
    prompt: str,
    reference_image: Optional[Image.Image],
    save_dir: Path,
    generation_method: str = "ai_edit",
) -> dict:
    """
    Package a generated character model:
    1. Copy entire Hiyori base model to save_dir
    2. Overwrite textures with edited versions
    3. Save reference image (full size + thumbnail)
    4. Write metadata.json

    Returns metadata dict.
    """
    if save_dir.exists():
        shutil.rmtree(save_dir)

    # Copy Hiyori model (skip cache files and __pycache__)
    shutil.copytree(
        HIYORI_DIR,
        save_dir,
        ignore=shutil.ignore_patterns("_region_masks.npz", "__pycache__", "*.pyc"),
    )
    logger.info(f"📁 Copied Hiyori model to {save_dir.name}")

    # Replace textures with edited versions
    tex_dir = save_dir / "hiyori_pro_t11.2048"
    for tex_name, tex_image in textures.items():
        tex_path = tex_dir / f"{tex_name}.png"
        tex_image.save(tex_path, "PNG")
        logger.info(f"  💾 Saved edited: {tex_name}")

    # Save reference image — both full-size and thumbnail
    has_thumbnail = False
    if reference_image:
        # Full-size reference (used for quality comparison)
        ref_path = save_dir / "reference.png"
        reference_image.save(ref_path, "PNG")

        # Thumbnail for gallery
        thumbnail_path = save_dir / "thumbnail.png"
        thumb = reference_image.copy()
        thumb.thumbnail((256, 384), Image.LANCZOS)
        thumb.save(thumbnail_path, "PNG")
        has_thumbnail = True
        logger.info("  🖼️ Saved reference + thumbnail")

    # Build metadata
    timestamp = save_dir.name.replace("generated_", "")
    model_path = f"/static/models/generated/{save_dir.name}/hiyori_pro_t11.model3.json"

    metadata = {
        "id": save_dir.name,
        "prompt": prompt,
        "attributes": attributes,
        "model3_json": "hiyori_pro_t11.model3.json",
        "model_path": model_path,
        "thumbnail_url": f"/static/models/generated/{save_dir.name}/thumbnail.png",
        "reference_url": f"/static/models/generated/{save_dir.name}/reference.png",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": timestamp,
        "has_thumbnail": has_thumbnail,
        "base_model": "Hiyori",
        "generation_method": generation_method,
    }

    with open(save_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    logger.info("  📝 Saved metadata")

    return metadata


# ─── Main Generation Pipeline ────────────────────────────────────────────────

async def generate_character(
    prompt: str, api_key: str, quality: str = "high", progress_callback=None
) -> dict:
    """
    Full character generation pipeline.

    Args:
        prompt: User's text description of the character
        api_key: xAI API key for Grok APIs
        quality: 'high' generates reference artwork + color transfer,
                 'standard' does color transfer only (faster)
        progress_callback: Optional async function(step, message) for progress

    Returns:
        dict with model_path, attributes, metadata, etc.

    Raises:
        FileNotFoundError: If Hiyori model is not installed
        RuntimeError: If Grok API calls fail
    """
    generate_ref = quality != "standard"

    # Pre-check: Hiyori model must be present
    status = check_hiyori_model()
    if not status["available"]:
        raise FileNotFoundError(
            f"Hiyori model not found. Missing: {status['missing']}. "
            f"See SETUP_HIYORI.md for download instructions."
        )

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    save_dir = GENERATED_DIR / f"generated_{timestamp}"
    save_dir.mkdir(parents=True, exist_ok=True)

    config = load_textoon_config()

    try:
        # Step 1: Parse prompt into color attributes
        if progress_callback:
            await progress_callback("parsing", "Analyzing character description...")
        attributes = await parse_prompt_to_attributes(prompt, api_key)

        # Step 2: Generate reference artwork (for gallery thumbnail)
        reference_image = None
        if generate_ref:
            if progress_callback:
                await progress_callback("generating", "Generating reference artwork...")
            reference_image = await generate_reference_image(attributes, prompt, api_key)

        # Step 3: Build region masks (cached after first run)
        if progress_callback:
            await progress_callback("masking", "Analyzing model textures...")
        masks = build_region_masks(config)

        # Step 4: Apply color transfer to textures
        if progress_callback:
            await progress_callback("texturing", "🖌️ Applying colors to model...")
        textures = process_textures(config, attributes, masks)

        # Step 5: Package model
        if progress_callback:
            await progress_callback("packaging", "Assembling Live2D model...")
        metadata = package_model(
            textures, attributes, prompt, reference_image, save_dir,
            generation_method="color_transfer",
        )

        if progress_callback:
            await progress_callback("done", "Character generated successfully!")

        logger.info(f"🎉 Character generated: {metadata['model_path']}")
        return metadata

    except Exception as e:
        # Clean up on failure
        if save_dir.exists():
            shutil.rmtree(save_dir, ignore_errors=True)
        raise


# ─── Model Management ────────────────────────────────────────────────────────

def list_generated_models() -> list:
    """List all generated character models, sorted newest first."""
    models = []

    if not GENERATED_DIR.exists():
        return models

    for model_dir in GENERATED_DIR.iterdir():
        if not model_dir.is_dir() or not model_dir.name.startswith("generated_"):
            continue

        metadata_path = model_dir / "metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    models.append(json.load(f))
            except Exception as e:
                logger.error(f"Error reading metadata from {model_dir}: {e}")

    models.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return models


def delete_generated_model(model_id: str) -> bool:
    """Delete a generated model directory."""
    model_dir = GENERATED_DIR / model_id
    if (
        model_dir.exists()
        and model_dir.is_dir()
        and model_id.startswith("generated_")
    ):
        shutil.rmtree(model_dir)
        logger.info(f"🗑️ Deleted model: {model_id}")
        return True
    return False
