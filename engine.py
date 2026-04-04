"""
PrintGuard Protection Engine v2
Tiled processing — werkt met bestanden van elke grootte, ook 15.000px+.

In plaats van het hele beeld in één keer als NumPy-array in het geheugen
te laden, verwerkt deze versie horizontale strips van ~2000px hoog.
Geheugengebruik blijft constant ongeacht de bestandsgrootte.
"""

import numpy as np
from PIL import Image

TILE_HEIGHT = 2000


def _process_strip(strip, y_offset, pattern, strength, channel_split, freq_variation, f):
    strip_h, w = strip.shape[:2]
    fv = max(1, freq_variation)

    xs = np.arange(w, dtype=np.int32)
    ys = np.arange(y_offset, y_offset + strip_h, dtype=np.int32)
    X, Y = np.meshgrid(xs, ys)

    noise_r = np.zeros((strip_h, w), dtype=np.int16)
    noise_g = np.zeros((strip_h, w), dtype=np.int16)
    noise_b = np.zeros((strip_h, w), dtype=np.int16)

    if pattern in ("hf", "combined"):
        hf = np.where((X + Y) % (f * 2) < f, strength, -strength).astype(np.int16)
        noise_r += hf; noise_g += hf; noise_b += hf
        del hf

    if pattern in ("checker", "combined"):
        bx = (X // (f + 1)).astype(np.int32)
        by = (Y // (f + 1)).astype(np.int32)
        checker = np.where((bx + by) % 2 == 0, 1, -1).astype(np.int16)
        bs = int(strength * 0.6)
        noise_r += checker * bs; noise_g += checker * bs; noise_b += checker * bs
        del bx, by, checker

    if pattern == "stripes":
        stripes = np.where((X + Y) // (f + 1) % 2 == 0, strength, -strength).astype(np.int16)
        noise_r += stripes; noise_g += stripes; noise_b += stripes
        del stripes

    if pattern in ("channel", "combined"):
        cycle = ((X * fv + Y) % (fv * 4)).astype(np.int32)
        cs = int(channel_split)
        z0 = cycle < fv
        z1 = (cycle >= fv)     & (cycle < fv * 2)
        z2 = (cycle >= fv * 2) & (cycle < fv * 3)
        z3 = cycle >= fv * 3
        noise_r += np.where(z0,  cs, np.where(z2, -cs, np.where(z3, -(cs//2), 0))).astype(np.int16)
        noise_g += np.where(z1,  cs, np.where(z0, -cs, np.where(z3,  cs//2,  0))).astype(np.int16)
        noise_b += np.where(z2,  cs, np.where(z1, -cs, 0)).astype(np.int16)
        del cycle, z0, z1, z2, z3

    if fv > 1:
        mask = (X * 7 + Y * 13) % (fv * 3) == 0
        wobble = int(strength * 0.3)
        noise_r += np.where(mask,  wobble, 0).astype(np.int16)
        noise_g += np.where(mask, -wobble, 0).astype(np.int16)
        del mask

    del X, Y
    strip[:, :, 0] = np.clip(strip[:, :, 0] + noise_r, 0, 255)
    strip[:, :, 1] = np.clip(strip[:, :, 1] + noise_g, 0, 255)
    strip[:, :, 2] = np.clip(strip[:, :, 2] + noise_b, 0, 255)
    del noise_r, noise_g, noise_b
    return strip


def apply_protection(
    img,
    pattern="combined",
    strength=18,
    channel_split=12,
    freq_variation=3,
    printer_target="offset",
    tile_height=TILE_HEIGHT,
    progress_callback=None,
):
    mode = img.mode
    rgb  = img.convert("RGB")
    w, h = rgb.size
    f = {"offset": 1, "laser": 2, "inkjet": 3, "all": 1}.get(printer_target, 1)

    result_arr   = np.empty((h, w, 3), dtype=np.uint8)
    strips_total = (h + tile_height - 1) // tile_height

    for strip_idx, y0 in enumerate(range(0, h, tile_height)):
        y1       = min(y0 + tile_height, h)
        strip_np = np.array(rgb.crop((0, y0, w, y1)), dtype=np.int16)
        strip_np = _process_strip(strip_np, y0, pattern, strength, channel_split, freq_variation, f)
        result_arr[y0:y1] = strip_np.astype(np.uint8)
        del strip_np
        if progress_callback:
            progress_callback(int((strip_idx + 1) / strips_total * 100))

    result = Image.fromarray(result_arr, "RGB")
    del result_arr
    if mode == "RGBA":
        result.putalpha(img.split()[3])
    return result


def protect_file(input_path, output_path, **kwargs):
    import time
    img  = Image.open(input_path)
    w, h = img.size
    t0   = time.time()
    protected = apply_protection(img, **kwargs)
    elapsed   = round(time.time() - t0, 2)
    protected.save(output_path, "PNG", compress_level=6)
    return {"width": w, "height": h, "megapixels": round(w*h/1e6,1), "elapsed_sec": elapsed, "output": output_path}
