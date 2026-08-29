from pathlib import Path

from reportlab.lib import colors

COMPANY_NAME = "Wickham Roofing"
COMPANY_PHONE = "1-800-ROOFING"
COMPANY_EMAIL = "wickhamroofing@gmail.com"
COMPANY_ADDRESS = "Ochlocknee, GA"
COMPANY_TAGLINE = "Residential & Commercial Storm Restoration Specialists"
FIELD_DOCS_DIR = Path("data/field_docs")

# ============================================================================
# BRAND COLORS & SUB-BRAND PALETTES
# ============================================================================
# Core brand color definitions
BRAND_NAVY = colors.HexColor("#0f172a")       # Slate 900
BRAND_BLUE = colors.HexColor("#1e3a8a")       # Blue 900 / Primary header accent
BRAND_SLATE = colors.HexColor("#334155")      # Slate 700 / Secondary dark
BRAND_LIGHT_BG = colors.HexColor("#f8fafc")   # Slate 50 / Card background
BRAND_MUTED_BG = colors.HexColor("#f1f5f9")   # Slate 100 / Row alternating bg
BRAND_BORDER = colors.HexColor("#cbd5e1")     # Slate 300 / Subtle borders
BRAND_DARK_BORDER = colors.HexColor("#94a3b8")# Slate 400
BRAND_ACCENT = colors.HexColor("#ea580c")     # Orange 600 / Bold CTA accent
BRAND_GOLD = colors.HexColor("#f59e0b")       # Amber 500
BRAND_GREEN = colors.HexColor("#16a34a")      # Green 600 / Success / Approvals
BRAND_RED = colors.HexColor("#dc2626")        # Red 600 / Warnings & Denials

# Homeowner-facing sub-brand: Primary navy/slate + soft neutrals & whitesmoke
HOMEOWNER_PALETTE = {
    "primary": BRAND_BLUE,
    "secondary": BRAND_SLATE,
    "bg_card": BRAND_LIGHT_BG,
    "bg_alt": colors.whitesmoke,
    "border": BRAND_BORDER,
    "text": BRAND_NAVY,
    "muted_text": colors.HexColor("#64748b"),
    "accent": BRAND_GOLD,
    "warning": BRAND_RED,
}

# Carrier-facing sub-brand: Dense, grayscale/black-and-white, subtle navy header accent
CARRIER_PALETTE = {
    "primary": colors.HexColor("#000000"),
    "secondary": colors.HexColor("#333333"),
    "header_accent": BRAND_BLUE,
    "bg_card": colors.HexColor("#ffffff"),
    "bg_alt": colors.HexColor("#f8fafc"),
    "gridline": colors.HexColor("#cccccc"),
    "border": colors.HexColor("#333333"),
    "text": colors.HexColor("#111111"),
    "muted_text": colors.HexColor("#555555"),
    "accent": BRAND_BLUE,
}

# Neighbor-facing sub-brand: Vibrant marketing palette with bold accent
NEIGHBOR_PALETTE = {
    "primary": BRAND_NAVY,
    "secondary": BRAND_BLUE,
    "accent": BRAND_ACCENT,
    "bg_card": BRAND_LIGHT_BG,
    "bg_alt": BRAND_MUTED_BG,
    "border": BRAND_BORDER,
    "text": BRAND_NAVY,
    "muted_text": colors.HexColor("#475569"),
}

# Internal financial / commission sub-brand: Data-dense neutral grayscale
INTERNAL_PALETTE = {
    "primary": BRAND_SLATE,
    "secondary": BRAND_NAVY,
    "bg_card": BRAND_LIGHT_BG,
    "bg_alt": BRAND_MUTED_BG,
    "border": BRAND_BORDER,
    "text": BRAND_NAVY,
    "muted_text": colors.HexColor("#64748b"),
    "success": BRAND_GREEN,
}

# ============================================================================
# STANDARD SPACING & MARGINS (Points: 72 points = 1 inch)
# ============================================================================
MARGIN_DEFAULT = 36    # 0.5 in
MARGIN_NARROW = 28     # 0.39 in
MARGIN_WIDE = 54       # 0.75 in

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

# ============================================================================
# FONT ASSETS & PATHS
# Note: Custom TrueType font files (.ttf) can be placed in FONT_DIR.
# If font files are absent or unreadable, the PDFEngine automatically
# falls back gracefully to standard PostScript fonts (Helvetica / Helvetica-Bold).
# ============================================================================
FONT_DIR = Path("app/assets/fonts")
FONT_INTER_REGULAR = FONT_DIR / "Inter-Regular.ttf"
FONT_INTER_BOLD = FONT_DIR / "Inter-Bold.ttf"
FONT_INTER_ITALIC = FONT_DIR / "Inter-Italic.ttf"
FONT_INTER_BOLD_ITALIC = FONT_DIR / "Inter-BoldItalic.ttf"

