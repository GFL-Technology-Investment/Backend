import io
import math
import base64
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

def smart_resize(
    t: int,
    h: int,
    w: int,
    t_factor: int = 1,
    h_factor: int = 28,
    w_factor: int = 28,
    min_pixels: int = 112 * 112,
    max_pixels: int = 14 * 14 * 4 * 15000,
):
    """
    Smart resize for images.

    Ensures:
    1. Height and width are divisible by the given factors
    2. Total pixels are within [min_pixels, max_pixels]
    3. Keeps aspect ratio as much as possible

    Args:
        t: Temporal dimension.
        h: Height.
        w: Width.
        t_factor: Temporal factor.
        h_factor: Height factor.
        w_factor: Width factor.
        min_pixels: Minimum pixels.
        max_pixels: Maximum pixels.

    Returns:
        (new_h, new_w)
    """
    assert t >= t_factor, "Temporal dimension must be greater than the factor."

    h_bar = round(h / h_factor) * h_factor
    w_bar = round(w / w_factor) * w_factor
    t_bar = round(t / t_factor) * t_factor

    if t_bar * h_bar * w_bar > max_pixels:
        beta = math.sqrt((t * h * w) / max_pixels)
        h_bar = math.floor(h / beta / h_factor) * h_factor
        w_bar = math.floor(w / beta / w_factor) * w_factor
    elif t_bar * h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (t * h * w))
        h_bar = math.ceil(h * beta / h_factor) * h_factor
        w_bar = math.ceil(w * beta / w_factor) * w_factor

    return h_bar, w_bar

def load_image_to_pil(image_source) -> Image.Image:
    """Load an image and convert it to base64.

    Supported inputs:
    - PIL.Image.Image
    - np.ndarray
    - Local file path (str)
    - data:image/... URL (str)
    - <|base64|>... blob (str)
    - <|tarpath|>... blob (str)
    - Raw bytes (bytes)

    Args:
        image_source: Image source.
        t_patch_size: Temporal patch size.
        max_pixels: Max pixels.
        image_format: Image format.
        patch_expand_factor: Patch expand factor.
        min_pixels: Min pixels.

    Returns:
        Base64-encoded image content.
    """
    import os

    def _try_decode_base64_to_image_bytes(s: str) -> bytes | None:
        # Remove whitespace/newlines and pad for base64.
        candidate = "".join(str(s).split())
        if len(candidate) < 32:
            return None

        # Strip optional "<|base64|>" prefix.
        if candidate.startswith("<|base64|>"):
            candidate = candidate[len("<|base64|>") :]

        # If it looks like a filename (has a short extension), skip.
        if "." in candidate and len(candidate.rsplit(".", 1)[-1]) <= 5:
            return None

        pad = (-len(candidate)) % 4
        if pad:
            candidate = candidate + ("=" * pad)

        try:
            return base64.b64decode(candidate, validate=True)
        except Exception:
            return None

    # Handle different input types
    if isinstance(image_source, Image.Image):
        # Already a PIL Image
        image = image_source
    elif isinstance(image_source, np.ndarray):
        image = Image.fromarray(image_source)
    elif isinstance(image_source, bytes):
        # Raw bytes
        image = Image.open(io.BytesIO(image_source))
    elif isinstance(image_source, str):
        if image_source.startswith("file://"):
            image_source = image_source[7:]

        if os.path.isfile(image_source):
            # Local file path (PDFs are handled via PageLoader)
            with open(image_source, "rb") as f:
                image_data = f.read()
            image = Image.open(io.BytesIO(image_data))
        elif image_source.startswith("data:image/"):
            # data:image/... URL
            image_data = base64.b64decode(image_source.split(",")[1])
            image = Image.open(io.BytesIO(image_data))
        else:
            # Raw base64 payload or <|base64|> blob
            decoded = _try_decode_base64_to_image_bytes(image_source)
            if decoded is None:
                raise ValueError(f"Invalid image source: {image_source}")
            image = Image.open(io.BytesIO(decoded))
    else:
        raise TypeError(f"Unsupported image source type: {type(image_source)}")

    # Convert to RGB
    if image.mode != "RGB":
        image = image.convert("RGB")

    return image

def load_image_to_base64(
    image_source,
    t_patch_size: int,
    max_pixels: int,
    image_format: str,
    patch_expand_factor: int = 1,
    min_pixels: int = 112 * 112,
):
    pil_image = load_image_to_pil(image_source)

    # Original size
    w, h = pil_image.size

    # Compute new size
    h_bar, w_bar = smart_resize(
        t=t_patch_size,
        h=h,
        w=w,
        t_factor=t_patch_size,
        h_factor=14 * 2 * patch_expand_factor,
        w_factor=14 * 2 * patch_expand_factor,
        min_pixels=min_pixels,
        max_pixels=max_pixels,
    )

    # Resize
    image = pil_image.resize((w_bar, h_bar), Image.Resampling.BICUBIC)

    # Encode as bytes
    buffered = io.BytesIO()
    image.save(buffered, format=image_format)
    buffered.seek(0)
    image_data = buffered.getvalue()

    # Convert bytes to base64
    base64_encoded_data = base64.b64encode(image_data)
    image_base64 = base64_encoded_data.decode("utf-8")

    return image_base64

def crop_image_region(image: Image.Image, boxes, padding: int = 0):
    
    if not isinstance(image, Image.Image):
        image = load_image_to_pil(image)

    image_width, image_height = image.size

    regions = []

    for item in boxes:
        bbox = item["coordinate"]

        if len(bbox) != 4:
            continue

        xmin, ymin, xmax, ymax = bbox

        xmin = max(0, int(xmin - padding))
        ymin = max(0, int(ymin - padding))
        xmax = min(image_width, int(xmax + padding))
        ymax = min(image_height, int(ymax + padding))

        if xmax <= xmin or ymax <= ymin:
            continue

        crop_img = image.crop((xmin, ymin, xmax, ymax))

        regions.append(
            {
                "label": item["label"],
                "score": item["score"],
                "bbox": [xmin, ymin, xmax, ymax],
                "image": crop_img,
            }
        )

    return regions
