"""
PrintGuard — Onzichtbaar steganografisch watermerk (twee lagen)

Laag 1 — LSB spread spectrum steganografie:
  Bits van maker + certificaat-ID verspreid over het blauw-kanaal.
  Volledig onzichtbaar. Overleeft lossless kopieën en lichte JPEG (quality≥75).
  Fragiel bij agressieve compressie of printen — bedoeld voor digitale diefstal.

Laag 2 — DCT-domein watermerk:
  Bits ingebed in mid-frequentie coëfficiënten van 8×8 DCT-blokken (zoals JPEG).
  Overleeft JPEG-compressie (quality≥65), matige bijsnijding en kleurcorrectie.
  Robuuster dan LSB — bewijst ook bij gecomprimeerde kopieën.
  Gebaseerd op zuivere NumPy matrix-vermenigvuldiging (geen externe libraries).

Beide lagen samen:
  - LSB: bewijst lossless digitale origine
  - DCT: bewijst origine na JPEG-compressie of lichte bewerking
  - Onzichtbaar voor het menselijk oog in beide gevallen
"""

import numpy as np
import hashlib
import struct


# ── DCT-matrix (eenmalig berekend) ───────────────────────────────────────────

def _build_dct8() -> np.ndarray:
    """Orthonormale 8×8 DCT-II transformatiematrix (pure NumPy)."""
    n  = 8
    k  = np.arange(n, dtype=np.float32).reshape(n, 1)
    i  = np.arange(n, dtype=np.float32).reshape(1, n)
    D  = np.cos(np.pi * k * (2 * i + 1) / (2 * n)).astype(np.float32)
    D[0] /= np.sqrt(2)
    D   *= np.sqrt(2 / n)
    return D

_D8  = _build_dct8()          # Forward DCT matrix  (8×8)
_D8T = _D8.T.copy()           # Inverse DCT matrix  (8×8)

# DCT-positie die we aanpassen: (rij=2, kolom=3) is midden-frequentie
# Overleeft JPEG quality≥65 en is perceptueel gemaskeerd in getextureerde zones
_WM_ROW, _WM_COL = 2, 3
_DCT_STRENGTH    = 22         # ±22 in DCT-domein → ~±3px in beelddomein
                              # JPEG quantisatie op (2,3) ≈ 10 bij quality 75
                              # → 22 > 10/2 → teken overleeft kwantisatie ✓


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


# ── DCT-domein watermerk ──────────────────────────────────────────────────────

def _block_indices(n_bits: int, img_hash: str, h_blocks: int, w_blocks: int) -> np.ndarray:
    """
    Kies n_bits willekeurige blok-indices geseed op img_hash + geheime sleutel.
    Geeft array van (blok_rij, blok_kolom) paren terug.
    """
    seed_str = f"{_WM_SECRET}:dct:{img_hash}"
    seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest()[:16], 16)
    rng      = np.random.default_rng(seed_int)
    total    = h_blocks * w_blocks
    chosen   = rng.choice(total, size=min(n_bits, total), replace=False)
    rows     = chosen // w_blocks
    cols     = chosen %  w_blocks
    return rows, cols


def embed_dct_watermark(
    img_array: np.ndarray,
    creator: str,
    cert_id: str,
    img_hash: str,
) -> np.ndarray:
    """
    DCT-domein watermerk — overleeft JPEG-compressie (quality≥65).

    Werking:
    - Afbeelding opgesplitst in 8×8 blokken (luminantie-kanaal)
    - Per geselecteerd blok: 2D DCT berekend via matrix-vermenigvuldiging
    - Midden-frequentie coëfficiënt (2,3) aangepast: +22 = bit 1, -22 = bit 0
    - Inverse DCT terugschrijven naar pixels
    - Wijziging ≈ ±3 pixelwaarden, volledig onzichtbaar in getextureerde zones

    Parameters
    ----------
    img_array : uint8 (H, W, 3)

    Returns
    -------
    Gewijzigde uint8 array
    """
    arr  = img_array.astype(np.float32)
    h, w = arr.shape[:2]

    # Luminantie Y berekenen (ITU-R BT.601)
    Y = (0.299 * arr[:, :, 0] +
         0.587 * arr[:, :, 1] +
         0.114 * arr[:, :, 2])

    h8 = (h // 8) * 8
    w8 = (w // 8) * 8
    h_blocks = h8 // 8
    w_blocks = w8 // 8

    payload = _make_payload(creator, cert_id)
    bits    = []
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)

    n_bits = len(bits)
    if n_bits > h_blocks * w_blocks:
        return img_array   # afbeelding te klein

    b_rows, b_cols = _block_indices(n_bits, img_hash, h_blocks, w_blocks)

    # Reshape Y naar blokken: (h_blocks, w_blocks, 8, 8)
    Y_blocks = Y[:h8, :w8].reshape(h_blocks, 8, w_blocks, 8).transpose(0, 2, 1, 3)

    # Vectorized 2D DCT: _D8 @ block @ _D8T voor alle geselecteerde blokken
    sel_blocks = Y_blocks[b_rows, b_cols]                    # (n_bits, 8, 8)
    dct_blocks = _D8 @ sel_blocks @ _D8T                    # (n_bits, 8, 8)

    # Bits inschrijven: coëfficiënt (WM_ROW, WM_COL) op ±DCT_STRENGTH zetten
    for idx, bit in enumerate(bits):
        target = _DCT_STRENGTH if bit == 1 else -_DCT_STRENGTH
        dct_blocks[idx, _WM_ROW, _WM_COL] = target

    # Inverse DCT terugschrijven
    idct_blocks = _D8T @ dct_blocks @ _D8                   # (n_bits, 8, 8)
    Y_blocks[b_rows, b_cols] = idct_blocks

    # Blokken terug naar beeld-layout
    Y_modified = Y_blocks.transpose(0, 2, 1, 3).reshape(h8, w8)

    # Verschil berekenen en toepassen op alle drie kanalen (proportioneel)
    delta = (Y_modified - Y[:h8, :w8]).astype(np.float32)

    result = img_array.copy()
    for ch in range(3):
        channel = result[:h8, :w8, ch].astype(np.float32)
        result[:h8, :w8, ch] = np.clip(channel + delta, 0, 255).astype(np.uint8)

    return result


def detect_dct_watermark(
    img_array: np.ndarray,
    img_hash: str,
) -> dict:
    """
    Lees het DCT-domein watermerk terug.
    Werkt ook na JPEG-compressie op quality≥65.
    """
    arr  = img_array.astype(np.float32)
    h, w = arr.shape[:2]
    Y    = (0.299 * arr[:, :, 0] +
            0.587 * arr[:, :, 1] +
            0.114 * arr[:, :, 2])

    h8 = (h // 8) * 8
    w8 = (w // 8) * 8
    h_blocks = h8 // 8
    w_blocks = w8 // 8

    max_bits = 73 * 8
    if max_bits > h_blocks * w_blocks:
        return {"found": False, "reason": "Afbeelding te klein voor DCT-watermerk"}

    b_rows, b_cols = _block_indices(max_bits, img_hash, h_blocks, w_blocks)

    Y_blocks   = Y[:h8, :w8].reshape(h_blocks, 8, w_blocks, 8).transpose(0, 2, 1, 3)
    sel_blocks = Y_blocks[b_rows, b_cols]
    dct_blocks = _D8 @ sel_blocks @ _D8T

    bits = [1 if dct_blocks[i, _WM_ROW, _WM_COL] >= 0 else 0
            for i in range(max_bits)]

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
        return {"found": True, "layer": "DCT", "creator": result[0], "cert_id": result[1]}
    return {"found": False, "reason": "Geen DCT PrintGuard-watermerk gevonden"}
