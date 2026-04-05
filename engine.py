"""
PrintGuard Protection Engine v3
Tiled processing — werkt met bestanden van elke grootte, ook 15.000px+.

Printer-specifieke frequentieafstemming:
  offset  → CMYK-scheiding, halftoon 150-300 LPI, hoekpatronen 15°/45°/75°
  laser   → 600-1200 DPI clustered dot, bredere frequentiebanden
  inkjet  → dithering (Bayer 4×4 en 8×8 matrix disruption)
  all     → breedband aanval, treft alle printertypen + AI-upscalers

Multi-frequentie aanval: in plaats van één enkele frequentie worden meerdere
tegelijk toegepast bij gereduceerde sterkte per band (zelfde totale RMS-energie).
Dit maakt het onmogelijk om met een eenvoudig notch-filter te filteren.
"""

import numpy as np
from PIL import Image

TILE_HEIGHT = 2000

# ── Printer-profielen ─────────────────────────────────────────────────────────
# freq_bands      : lijst van frequentie-veelvouden t.o.v. basis (1 = kleinste cel)
# channel_weight  : vermenigvuldiger voor channel-split sterkte
# angle_patterns  : voeg 15° en 75° diagonalen toe (CMYK-hoekafstemming)
# bayer_patterns  : voeg 4×4 en 8×8 Bayer-matrixverstoring toe (inkjet)

PRINTER_PROFILES = {
    "offset": {
        # Offsetdruk: CMYK-scheiding, halftoonraster 150–300 LPI
        # Bij 300 DPI = elke 2px een halftooncel → target f=2, ook f=3 en f=4
        # Hoeken: C=15°, M=75°, Y=0°, K=45° — expliciet aangevallen
        "freq_bands":     [2, 3, 4],
        "channel_weight": 1.5,
        "angle_patterns": True,
        "bayer_patterns": False,
    },
    "laser": {
        # Laserprinter: 600–1200 DPI, clustered-dot halftoon
        # Minder gevoelig voor CMYK-hoeken, wel voor middelfrequente patronen
        "freq_bands":     [3, 5, 7],
        "channel_weight": 1.0,
        "angle_patterns": False,
        "bayer_patterns": False,
    },
    "inkjet": {
        # Inkjet: Bayer-dithering 4×4 en 8×8, ook Floyd-Steinberg
        # Aanval op dither-matrix harmonischen
        "freq_bands":     [2, 4, 6],
        "channel_weight": 0.9,
        "angle_patterns": False,
        "bayer_patterns": True,
    },
    "all": {
        # Breedband: treft alle printertypen + AI-upscalers zoals Topaz
        "freq_bands":     [2, 3, 4, 6, 8],
        "channel_weight": 1.3,
        "angle_patterns": True,
        "bayer_patterns": True,
    },
}


def _process_strip(strip, y_offset, pattern, strength, channel_split, freq_variation, profile):
    strip_h, w = strip.shape[:2]
    fv = max(1, freq_variation)

    xs = np.arange(w, dtype=np.int32)
    ys = np.arange(y_offset, y_offset + strip_h, dtype=np.int32)
    X, Y = np.meshgrid(xs, ys)

    noise_r = np.zeros((strip_h, w), dtype=np.int16)
    noise_g = np.zeros((strip_h, w), dtype=np.int16)
    noise_b = np.zeros((strip_h, w), dtype=np.int16)

    freq_bands     = profile["freq_bands"]
    channel_weight = profile["channel_weight"]
    angle_patterns = profile["angle_patterns"]
    bayer_patterns = profile["bayer_patterns"]

    # Sterkte per frequentieband schaalt met 1/sqrt(n) zodat RMS-energie gelijk blijft
    n_bands      = len(freq_bands)
    band_strength = max(1, int(strength / (n_bands ** 0.5)))

    # ── Multi-frequentie hf-patroon ───────────────────────────────────────────
    if pattern in ("hf", "combined"):
        for f in freq_bands:
            period = max(2, f * 2)
            hf = np.where((X + Y) % period < f, band_strength, -band_strength).astype(np.int16)
            noise_r += hf; noise_g += hf; noise_b += hf
            del hf

    # ── Checker-patroon (multi-frequentie) ───────────────────────────────────
    if pattern in ("checker", "combined"):
        for f in freq_bands[:2]:   # eerste twee bands, anders te druk
            cell = max(1, f + 1)
            bx = (X // cell).astype(np.int32)
            by = (Y // cell).astype(np.int32)
            checker = np.where((bx + by) % 2 == 0, 1, -1).astype(np.int16)
            bs = max(1, int(band_strength * 0.6))
            noise_r += checker * bs; noise_g += checker * bs; noise_b += checker * bs
            del bx, by, checker

    # ── Strepen-patroon ───────────────────────────────────────────────────────
    if pattern == "stripes":
        for f in freq_bands[:2]:
            cell = max(1, f + 1)
            stripes = np.where((X + Y) // cell % 2 == 0, band_strength, -band_strength).astype(np.int16)
            noise_r += stripes; noise_g += stripes; noise_b += stripes
            del stripes

    # ── Channel-split (CMYK-scheiding aanvallen) ──────────────────────────────
    if pattern in ("channel", "combined"):
        cs = max(1, int(channel_split * channel_weight))
        for f in freq_bands[:2]:
            cycle = ((X * fv + Y) % (fv * 4 * f)).astype(np.int32)
            period = fv * f
            z0 = cycle < period
            z1 = (cycle >= period)     & (cycle < period * 2)
            z2 = (cycle >= period * 2) & (cycle < period * 3)
            z3 = cycle >= period * 3
            noise_r += np.where(z0,  cs, np.where(z2, -cs, np.where(z3, -(cs//2), 0))).astype(np.int16)
            noise_g += np.where(z1,  cs, np.where(z0, -cs, np.where(z3,  cs//2,  0))).astype(np.int16)
            noise_b += np.where(z2,  cs, np.where(z1, -cs, 0)).astype(np.int16)
            del cycle, z0, z1, z2, z3

    # ── Frequentie-variatie wobble ────────────────────────────────────────────
    if fv > 1:
        mask   = (X * 7 + Y * 13) % (fv * 3) == 0
        wobble = max(1, int(strength * 0.3))
        noise_r += np.where(mask,  wobble, 0).astype(np.int16)
        noise_g += np.where(mask, -wobble, 0).astype(np.int16)
        del mask

    # ── Offset-specifiek: CMYK-hoekpatronen (15°, 45°, 75°) ─────────────────
    if angle_patterns:
        f0 = freq_bands[0]
        as_ = max(1, int(strength * 0.35))

        # 45° diagonaal (K-kanaal, sterkste hoek)
        diag45 = np.where((X + Y) % (f0 * 2) < f0, as_, -as_).astype(np.int16)
        noise_r += diag45; noise_b += diag45
        del diag45

        # 15° benadering: tan(15°) ≈ 1/4 → (X + Y*4) % periode
        period15 = max(4, f0 * 5)
        diag15 = np.where((X + Y * 4) % (period15 * 2) < period15, as_, -as_).astype(np.int16)
        noise_r += diag15; noise_g -= diag15   # C vs M tegenstelling
        del diag15

        # 75° benadering: tan(75°) ≈ 4 → (X*4 + Y) % periode
        diag75 = np.where((X * 4 + Y) % (period15 * 2) < period15, as_, -as_).astype(np.int16)
        noise_g += diag75; noise_b -= diag75   # M vs Y tegenstelling
        del diag75

    # ── Inkjet-specifiek: Bayer-matrixverstoring (4×4 en 8×8) ────────────────
    if bayer_patterns:
        bs = max(1, int(strength * 0.4))

        # 4×4 Bayer beat
        bayer4 = np.where(((X // 4) + (Y // 4)) % 2 == 0, bs, -bs).astype(np.int16)
        noise_r += bayer4; noise_g -= bayer4
        del bayer4

        # 8×8 Bayer beat (hogere harmonische)
        bs8 = max(1, int(bs * 0.6))
        bayer8 = np.where(((X // 8) + (Y // 8)) % 2 == 0, bs8, -bs8).astype(np.int16)
        noise_g += bayer8; noise_b -= bayer8
        del bayer8

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
    printer_target="all",
    tile_height=TILE_HEIGHT,
    progress_callback=None,
):
    profile = PRINTER_PROFILES.get(printer_target, PRINTER_PROFILES["offset"])

    mode = img.mode
    rgb  = img.convert("RGB")
    w, h = rgb.size

    result_arr   = np.empty((h, w, 3), dtype=np.uint8)
    strips_total = (h + tile_height - 1) // tile_height

    for strip_idx, y0 in enumerate(range(0, h, tile_height)):
        y1       = min(y0 + tile_height, h)
        strip_np = np.array(rgb.crop((0, y0, w, y1)), dtype=np.int16)
        strip_np = _process_strip(strip_np, y0, pattern, strength, channel_split, freq_variation, profile)
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
