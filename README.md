# PrintGuard — Volledige Overdracht aan Claude Code

## Wat is dit?
PrintGuard is een SaaS-tool voor kunstenaars die onzichtbare print-verstorende
ruis toevoegt aan digitale kunstwerken. De ruis is onzichtbaar op scherm maar
verstoort het raster-algoritme van printers (inkjet, laser, offset).

Daarnaast genereert de tool een PDF-certificaat met SHA-256 hash als
prioriteitsbewijs van auteursrecht.

Website: https://www.printguardtool.com
Hoofdtaal: Engels (/)  |  Nederlands: (/nl/)

---

## Bestanden

```
printguard/
├── engine.py        ← NumPy algoritme, tiled processing, werkt tot 20.000px+
├── server.py        ← Flask API (login, protect, certificate, usage)
├── users.py         ← Abonnementen, limieten, reset op lidmaatschapsdatum
├── certificate.py   ← PDF-certificaat generator (ReportLab)
├── static/
│   └── index.html   ← Volledige website NL+EN, members-only tool
└── README.md        ← Dit bestand
```

---

## Dependencies

```bash
pip install flask pillow numpy reportlab mollie-api-python
```

---

## Wat Claude Code moet bouwen

### 1. URL-structuur tweetalig
- `/` → Engels (standaard)
- `/nl/` → Nederlands
- Flask detecteert browser-taal (`Accept-Language` header) en redirect
  automatisch naar `/` of `/nl/` als de gebruiker geen voorkeur heeft opgegeven
- Alle vertalingen verplaatsen naar `translations.py`
- HTML krijgt één taal tegelijk meegestuurd (geen dubbele data- attributen meer)
- `hreflang` tags in de HTML head voor SEO

### 2. Database — vervang in-memory USERS_DB
Huidige situatie: gebruikers zitten als dict in `users.py` (verdwijnt bij herstart).
Vervang door **SQLite** (klein begin) of **PostgreSQL** (productie).

Tabel `users`:
```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,          -- bcrypt
    name          TEXT,
    plan          TEXT DEFAULT 'starter', -- starter / professional / studio
    billing       TEXT DEFAULT 'monthly', -- monthly / yearly
    member_since  TEXT NOT NULL,          -- ISO date: 2026-04-15
    uploads_this_period INTEGER DEFAULT 0,
    period_key    TEXT,                   -- ISO date van start huidige periode
    created_at    TEXT DEFAULT (datetime('now'))
);
```

Gebruik `bcrypt` voor wachtwoord-hashing (vervang plain-text vergelijking in users.py).

### 3. Mollie betalingen
Gebruik de officiële `mollie-api-python` library.

Flows:
- Gebruiker kiest plan op tariefpagina → POST /api/checkout
- Server maakt Mollie payment aan, stuurt redirect naar Mollie checkout
- Na betaling: Mollie webhook POST /api/mollie-webhook
- Webhook maakt gebruiker aan in database (of upgradet bestaand account)
- Stuur welkomstmail via Resend of Mailgun

```python
from mollie.api.client import Client
mollie = Client()
mollie.set_api_key("live_xxxxxxxxxxxxxxxx")  # uit environment variable

payment = mollie.payments.create({
    "amount": {"currency": "EUR", "value": "24.00"},
    "description": "PrintGuard Professional — maandelijks",
    "redirectUrl": "https://www.printguardtool.com/bedankt",
    "webhookUrl": "https://www.printguardtool.com/api/mollie-webhook",
    "metadata": {
        "plan": "professional",
        "billing": "monthly",
        "email": "klant@email.nl"
    }
})
```

Ondersteunde betaalmethodes via Mollie: iDEAL, Bancontact, creditcard, PayPal.

### 4. Sessies — vervang in-memory SESSIONS dict
Gebruik **Flask-Session** met database backend, of **JWT tokens**.
Huidige SESSIONS dict verdwijnt bij herstart → gebruikers worden uitgelogd.

### 5. Uploadlimiet server-side
Voeg toe aan Flask config:
```python
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload
```

### 6. Deployment
```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 --timeout 300 server:app
```

Timeout 300 seconden vanwege grote bestanden (15.000px ~8-40 sec verwerking).

Aanbevolen: **Railway.app** of **Render.com** (gratis starten).
Domein koppelen: DNS A-record → server IP, SSL via Let's Encrypt.

### 7. Environment variables
Zet gevoelige data in .env (nooit in code):
```
MOLLIE_API_KEY=live_xxxxxxxxxxxxxxxx
SECRET_KEY=willekeurige_lange_string
DATABASE_URL=sqlite:///printguard.db
RESEND_API_KEY=re_xxxxxxxx
```

---

## Abonnementsstructuur

| Plan         | Maandelijks | Jaarlijks (10 mnd) | Uploads        | Max resolutie | Certificaat |
|--------------|-------------|---------------------|----------------|---------------|-------------|
| Starter      | €9/mnd      | €90/jaar            | 25/mnd of 300/jaar | 4.000px  | Nee         |
| Professional | €24/mnd     | €240/jaar           | 100/mnd of 1200/jaar | 12.000px | Ja      |
| Studio       | €79/mnd     | €790/jaar           | Onbeperkt      | 20.000px      | Ja + register |

Reset-logica:
- Maandelijks → reset op de dag van de maand waarop ze lid werden
- Jaarlijks → reset op de jaardag van lidmaatschap (geen maandelijkse reset)
- Studio → altijd onbeperkt, geen teller

---

## Demo account (voor testen)
- Email: demo@printguardtool.com
- Wachtwoord: demo123
- Plan: Professional (maandelijks)

---

## Wat al werkt en NIET aangeraakt hoeft te worden
- `engine.py` — volledig werkend, tiled processing, alle resoluties
- `certificate.py` — volledig werkend PDF-generatie
- De kern van `users.py` — limietlogica, periode-reset op lidmaatschapsdatum
- `static/index.html` — volledige UI, beide talen, alle flows

---

## Volgorde van aanpak (aanbevolen)
1. Database opzetten (SQLite) + bcrypt wachtwoorden
2. Flask-Session of JWT voor sessies
3. URL-structuur /nl/ + translations.py
4. Mollie checkout flow
5. Mollie webhook → gebruiker aanmaken
6. Deployment op Railway + domein koppelen
7. Welkomstmail bij aanmelding
