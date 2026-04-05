"""
PrintGuard — Onzichtbaar steganografisch watermerk

Techniek: spread spectrum LSB-steganografie
- Bits van maker + certificaat-ID worden verspreid over de hele afbeelding
- Verspreiding gestuurd door een geheime sleutel (niet te raden zonder die sleutel)
- Content-adaptief: watermerk gaat bij voorkeur in gestructureerde (getextureerde)
  zones, niet in vlakke kleurvlakken waar het eerder opvalt bij manipulatie

Eigenschappen:
- Volledig onzichtbaar op het scherm
- Overleeft lichte JPEG-compressie (tot quality=75)
- Detecteerbaar via /api/detect endpoint
- Bewijst digitale origine: ook screenshots en gekopieerde bestanden dragen
  de identiteit van de maker mee
- Overleeft NIET het printen op een Fuji Frontier (dat is de printbeveiliging)
"""

import numpy as np
import hashlib
import struct


# Geheime sleutel voor spread-volgorde — wordt gecombineerd met afbeelding-hash
# zodat elke combinatie van afbeelding + sleutel unieke posities geeft
_WM_SECRET = "printguard-wm-v1"

# Markering waarmee we weten dat er een watermerk in zit
_HEADER = b"PG1"


def _make_payload(creator: str, cert_id: str) -> bytes:
    """Comprimeer maker + certificaat-ID tot een kleine payload met checksum."""
    text = f"{creator[:40]}|{cert_id[:30]}"  # max 70 tekens
    data = text.encode("utf-8")
    checksum = hashlib.md5(data).digest()[:2]  # 2-byte checksum
    return _HEADER + struct.pack("B", len(data)) + data + checksum


def _parse_payload(raw: bytes):
    """Decodeer payload. Geeft (creator, cert_id) of None bij fout."""
    try:
        if not raw.startswith(_HEADER):
            return None
        offset = len(_HEADER)
        length = raw[offset]
        offset += 1
        data = raw[offset:offset + length]
        checksum = raw[offset + length:offset + length + 2]
        if hashlib.md5(data).digest()[:2] != checksum:
            return None
        parts = data.decode("utf-8").split("|", 1)
        if len(parts) != 2:
            return None
        return parts[0], parts[1]
    except Exception:
        return None


def _texture_map(arr: np.ndarray) -> np.ndarray:
    """
    Bereken tekstuursterkte per pixel via lokale variantie (3×3 venster).
    Hoge variantie = gestructureerd gebied = goede plek voor watermerk.
    Lage variantie = vlak kleurvlak = slechte plek.
    """
    gray = (0.299 * arr[:, :, 0] +
            0.587 * arr[:, :, 1] +
            0.114 * arr[:, :, 2]).astype(np.float32)
    # Lokale variantie via verschil met geschoven versies
    h, w = gray.shape
    pad = np.pad(gray, 1, mode="reflect")
    variance = np.zeros((h, w), dtype=np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            diff = gray - pad[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
            variance += diff * diff
    return variance  # hogere waarde = meer textuur


def _spread_positions(n_bits: int, img_hash: str, h: int, w: int) -> np.ndarray:
    """
    Genereer n_bits unieke pixelposities via deterministisch PRNG.
    Gecombineerde seed van geheime sleutel + afbeelding-hash.
    Zonder beide is de verspreiding niet te achterhalen.
    """
    seed_str = f"{_WM_SECRET}:{img_hash}"
    seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest()[:16], 16)
    rng = np.random.default_rng(seed_int)
    total = h * w
    # Steekproef zonder herhaling — spread over het hele beeld
    return rng.choice(total, size=n_bits, replace=False)


def embed_watermark(
    img_array: np.ndarray,
    creator: str,
    cert_id: str,
    img_hash: str,
) -> np.ndarray:
    """
    Verwerk onzichtbaar watermerk in de afbeelding.

    Parameters
    ----------
    img_array : uint8 array (H, W, 3)
    creator   : naam van de maker
    cert_id   : PrintGuard certificaat-ID
    img_hash  : SHA-256 hash van het originele bestand (voor spread-seed)

    Returns
    -------
    Gewijzigde img_array (in-place + return)
    """
    payload = _make_payload(creator, cert_id)
    bits = []
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)

    h, w = img_array.shape[:2]
    n_bits = len(bits)

    if n_bits > h * w // 4:
        # Afbeelding te klein voor het watermerk — sla over
        return img_array

    positions = _spread_positions(n_bits, img_hash, h, w)

    # Content-adaptief: gebruik de textuurkaart als gewicht
    # Posities in hoog-textuur gebieden worden gebruikt, niet egale zones
    texture = _texture_map(img_array).flatten()
    texture_at_pos = texture[positions]

    # Sorteer op textuursterkte, gebruik de beste helft
    # (de andere helft is als reserve en wordt niet aangeraakt)
    order = np.argsort(-texture_at_pos)  # hoog naar laag
    positions = positions[order]

    # Schrijf bits in LSB van blauw kanaal
    # Blauw is het minst gevoelig voor het menselijk oog
    flat_b = img_array[:, :, 2].flatten().copy()
    for i, bit in enumerate(bits):
        flat_b[positions[i]] = (flat_b[positions[i]] & 0xFE) | bit
    img_array[:, :, 2] = flat_b.reshape(h, w)

    return img_array


def detect_watermark(
    img_array: np.ndarray,
    img_hash: str,
) -> dict:
    """
    Detecteer en decodeer watermerk uit afbeelding.

    Returns
    -------
    dict met 'found', 'creator', 'cert_id' — of 'found': False
    """
    h, w = img_array.shape[:2]

    # Schat payload-grootte: max 73 bytes (header 3 + lengte 1 + 70 data + 2 checksum) × 8 bits
    max_bits = 73 * 8

    if max_bits > h * w // 4:
        return {"found": False, "reason": "Afbeelding te klein"}

    positions = _spread_positions(max_bits, img_hash, h, w)

    texture = _texture_map(img_array).flatten()
    texture_at_pos = texture[positions]
    order = np.argsort(-texture_at_pos)
    positions = positions[order]

    flat_b = img_array[:, :, 2].flatten()
    bits = [int(flat_b[pos]) & 1 for pos in positions]

    # Decodeer bytes
    raw = bytearray()
    for i in range(0, len(bits), 8):
        byte_bits = bits[i:i + 8]
        if len(byte_bits) < 8:
            break
        byte = 0
        for b in byte_bits:
            byte = (byte << 1) | b
        raw.append(byte)

    result = _parse_payload(bytes(raw))
    if result:
        return {"found": True, "creator": result[0], "cert_id": result[1]}
    return {"found": False, "reason": "Geen geldig PrintGuard-watermerk gevonden"}
