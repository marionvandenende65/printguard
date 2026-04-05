"""
PrintGuard — Certificate Generator
Genereert een professioneel PDF-certificaat van auteursrechtregistratie.
Bevat: SHA-256 hash, timestamp, maker, bestandsinfo, QR-achtige vingerafdruk.
"""

import hashlib, datetime, io, math
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import mm

# Kleurpalet — zelfde als de website
INK        = HexColor("#0f0e0d")
PAPER      = HexColor("#f7f4ef")
PAPER_WARM = HexColor("#efe9df")
PAPER_MID  = HexColor("#e4ddd2")
ACCENT     = HexColor("#c8531a")
MUTED      = HexColor("#7a776f")
BORDER     = HexColor("#d0cbc3")


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _draw_hash_visual(c, hash_str, x, y, size=48, cols=8):
    """
    Tekent een visuele vingerafdruk op basis van de hash —
    een 8×8 raster van gekleurde blokjes, uniek per kunstwerk.
    Vergelijkbaar met een visuele checksum / mini-QR.
    """
    rows = cols
    cell = size / cols
    for i in range(cols * rows):
        if i * 2 + 1 >= len(hash_str):
            break
        val = int(hash_str[i*2:i*2+2], 16)
        col_idx = i % cols
        row_idx = i // cols
        # Kleur op basis van hash-byte
        if val < 64:
            color = PAPER_MID
        elif val < 128:
            color = MUTED
        elif val < 192:
            color = ACCENT
        else:
            color = INK
        cx = x + col_idx * cell
        cy = y - row_idx * cell
        c.setFillColor(color)
        c.rect(cx, cy - cell, cell - 0.5, cell - 0.5, fill=1, stroke=0)


def generate_certificate(
    image_bytes: bytes,
    creator_name: str,
    artwork_title: str,
    image_width: int,
    image_height: int,
    file_size_bytes: int,
    certificate_id: str = None,
    lang: str = "nl",
) -> bytes:
    """
    Genereer een PDF-certificaat van auteursrechtregistratie.

    Parameters
    ----------
    image_bytes      : Ruwe bytes van het originele bestand (voor hash)
    creator_name     : Naam van de maker
    artwork_title    : Titel van het kunstwerk
    image_width/height: Afmetingen in pixels
    file_size_bytes  : Bestandsgrootte origineel
    certificate_id   : Optioneel uniek ID (anders automatisch gegenereerd)
    lang             : "nl" of "en"

    Returns
    -------
    PDF als bytes
    """
    NL = lang == "nl"

    # Hash berekenen
    file_hash = sha256_of_bytes(image_bytes)
    timestamp = datetime.datetime.utcnow()
    ts_display = timestamp.strftime("%d %B %Y  %H:%M:%S UTC")
    ts_iso     = timestamp.strftime("%Y%m%d-%H%M%S")

    if not certificate_id:
        certificate_id = f"PG-{ts_iso}-{file_hash[:8].upper()}"

    # Canvas
    buf = io.BytesIO()
    W, H = A4   # 595 × 842 pt
    c = canvas.Canvas(buf, pagesize=A4)

    # ── Achtergrond ───────────────────────────────────────────────────────────
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # ── Accent balk links ─────────────────────────────────────────────────────
    c.setFillColor(ACCENT)
    c.rect(0, 0, 6*mm, H, fill=1, stroke=0)

    # ── Sierrand ──────────────────────────────────────────────────────────────
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    margin = 18*mm
    c.rect(margin, margin, W - 2*margin, H - 2*margin, fill=0, stroke=1)

    # ── Header ────────────────────────────────────────────────────────────────
    top = H - 28*mm

    # Logo
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin + 6*mm, top, "Print")
    c.setFillColor(ACCENT)
    c.drawString(margin + 6*mm + 58, top, "Guard")

    # Tagline
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    tagline = "ART PROTECTION TECHNOLOGY" if not NL else "KUNSTBESCHERMING TECHNOLOGIE"
    c.drawString(margin + 6*mm, top - 10, tagline)

    # Scheidingslijn header
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(margin + 4*mm, top - 16, W - margin - 4*mm, top - 16)

    # ── Titel van het certificaat ─────────────────────────────────────────────
    cert_title = "CERTIFICAAT VAN REGISTRATIE" if NL else "CERTIFICATE OF REGISTRATION"
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W/2, top - 32, cert_title)

    sub = "Auteursrecht & Prioriteitsbewijs" if NL else "Copyright & Priority Evidence"
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawCentredString(W/2, top - 44, sub)

    # ── Certificaatnummer ─────────────────────────────────────────────────────
    c.setFillColor(PAPER_WARM)
    c.roundRect(margin + 4*mm, top - 68, W - 2*margin - 8*mm, 18, 3, fill=1, stroke=0)
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 8)
    cert_label = "CERTIFICAATNUMMER" if NL else "CERTIFICATE NUMBER"
    c.drawString(margin + 8*mm, top - 60, cert_label)
    c.setFillColor(INK)
    c.setFont("Helvetica", 8)
    c.drawRightString(W - margin - 8*mm, top - 60, certificate_id)

    # ── Kunstwerk info sectie ─────────────────────────────────────────────────
    y = top - 90

    def section_header(label, ypos):
        c.setFillColor(PAPER_MID)
        c.rect(margin + 4*mm, ypos - 2, W - 2*margin - 8*mm, 14, fill=1, stroke=0)
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(margin + 8*mm, ypos + 3, label)
        return ypos - 18

    def field_row(label, value, ypos, accent_value=False):
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(margin + 8*mm, ypos, label)
        c.setFillColor(ACCENT if accent_value else INK)
        c.setFont("Helvetica-Bold" if accent_value else "Helvetica", 8)
        c.drawRightString(W - margin - 8*mm, ypos, value)
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.3)
        c.line(margin + 8*mm, ypos - 4, W - margin - 8*mm, ypos - 4)
        return ypos - 16

    # Kunstwerk details
    kunst_label = "KUNSTWERK DETAILS" if NL else "ARTWORK DETAILS"
    y = section_header(kunst_label, y)

    title_label  = "Titel" if NL else "Title"
    creator_label = "Maker" if NL else "Creator"
    dim_label    = "Afmetingen" if NL else "Dimensions"
    size_label   = "Bestandsgrootte" if NL else "File size"

    y = field_row(title_label,   artwork_title,                         y)
    y = field_row(creator_label, creator_name,                          y)
    y = field_row(dim_label,     f"{image_width} × {image_height} px",  y)
    y = field_row(size_label,    f"{file_size_bytes / 1024 / 1024:.2f} MB", y)

    y -= 8

    # Registratie details
    reg_label = "REGISTRATIE" if NL else "REGISTRATION"
    y = section_header(reg_label, y)

    date_label   = "Tijdstempel (UTC)" if NL else "Timestamp (UTC)"
    method_label = "Methode" if NL else "Method"
    method_val   = "SHA-256 cryptografische hash" if NL else "SHA-256 cryptographic hash"
    proof_label  = "Bewijskracht" if NL else "Legal weight"
    proof_val    = "Prioriteitsbewijs — datum en inhoud vastgelegd" if NL else "Priority evidence — date and content recorded"

    y = field_row(date_label,   ts_display, y)
    y = field_row(method_label, method_val, y)
    y = field_row(proof_label,  proof_val,  y)

    y -= 8

    # Hash sectie
    hash_label_section = "DIGITALE VINGERAFDRUK (SHA-256)" if NL else "DIGITAL FINGERPRINT (SHA-256)"
    y = section_header(hash_label_section, y)

    # Hash in twee regels van 32 tekens
    c.setFillColor(INK)
    c.setFont("Courier", 7.5)
    hash1 = file_hash[:32]
    hash2 = file_hash[32:]
    c.drawCentredString(W/2, y,      hash1)
    c.drawCentredString(W/2, y - 12, hash2)

    y -= 28

    # ── Visuele vingerafdruk ──────────────────────────────────────────────────
    vis_size = 40*mm
    vis_x = W/2 - vis_size/2
    vis_y = y - 4

    vis_label = "Visuele vingerafdruk" if NL else "Visual fingerprint"
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    c.drawCentredString(W/2, vis_y + 4, vis_label)

    # Achtergrond voor vingerafdruk
    c.setFillColor(PAPER_WARM)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.roundRect(vis_x - 4, vis_y - vis_size - 8, vis_size + 8, vis_size + 4, 3, fill=1, stroke=1)

    _draw_hash_visual(c, file_hash, vis_x, vis_y - 4, size=float(vis_size), cols=8)

    y = vis_y - vis_size - 20

    # ── Uitleg ────────────────────────────────────────────────────────────────
    c.setFillColor(PAPER_WARM)
    c.roundRect(margin + 4*mm, y - 32, W - 2*margin - 8*mm, 34, 3, fill=1, stroke=0)

    if NL:
        uitleg1 = "Dit certificaat legt vast dat het bovenstaande digitale kunstwerk op het vermelde tijdstip bestond"
        uitleg2 = "en toebehoorde aan de genoemde maker. De SHA-256 hash is uniek voor de exacte bestandsinhoud."
        uitleg3 = "Bij een juridisch geschil dient dit document als prioriteitsbewijs van creatie."
    else:
        uitleg1 = "This certificate records that the above digital artwork existed at the stated time"
        uitleg2 = "and belonged to the named creator. The SHA-256 hash is unique to the exact file contents."
        uitleg3 = "In case of a legal dispute, this document serves as priority evidence of creation."

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    c.drawCentredString(W/2, y - 10, uitleg1)
    c.drawCentredString(W/2, y - 20, uitleg2)
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W/2, y - 30, uitleg3)

    # ── Footer ────────────────────────────────────────────────────────────────
    fy = margin + 10*mm
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.3)
    c.line(margin + 4*mm, fy + 8, W - margin - 4*mm, fy + 8)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    footer_l = "printguardtool.com" if NL else "printguardtool.com"
    footer_r = f"Gegenereerd op {timestamp.strftime('%d-%m-%Y')} · PrintGuard v2" if NL else f"Generated on {timestamp.strftime('%Y-%m-%d')} · PrintGuard v2"
    c.drawString(margin + 8*mm, fy, footer_l)
    c.drawRightString(W - margin - 8*mm, fy, footer_r)

    # ── Subtiele achtergrondtekst ─────────────────────────────────────────────
    c.saveState()
    c.setFillColor(HexColor("#ede8e0"))
    c.setFont("Helvetica", 9)
    c.drawRightString(W - margin - 8*mm, margin + 4*mm, "printguardtool.com")
    c.restoreState()

    c.save()
    buf.seek(0)
    return buf.read()
