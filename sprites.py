"""
ANSI 256-colour half-block sprite renderer.

Each terminal character represents two pixel rows using Unicode block
elements (▀ upper half, ▄ lower half) so the rendered height is
image_height / 2 rows.  Full pixel width is preserved.

Requires Pillow (PIL).
"""
import os
from PIL import Image

_SPRITES_DIR = os.path.join(os.path.dirname(__file__), 'sprites')

# Reset escape
_RST = '\033[0m'


def _ansi256(r, g, b):
    """Map an RGB triple to the nearest ANSI 256-colour index."""
    # 6×6×6 colour cube (indices 16–231)
    r6 = round(r / 255 * 5)
    g6 = round(g / 255 * 5)
    b6 = round(b / 255 * 5)
    def _cube_val(i): return 0 if i == 0 else 95 + (i - 1) * 40
    cr, cg, cb = _cube_val(r6), _cube_val(g6), _cube_val(b6)
    cube_dist = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    cube_idx  = 16 + 36 * r6 + 6 * g6 + b6

    # Greyscale ramp (indices 232–255)
    lum     = round(0.299 * r + 0.587 * g + 0.114 * b)
    grey_n  = max(0, min(23, round((lum - 8) / 10)))
    grey_v  = 8 + grey_n * 10
    grey_dist = (r - grey_v) ** 2 + (g - grey_v) ** 2 + (b - grey_v) ** 2

    return (232 + grey_n) if grey_dist < cube_dist else cube_idx


def _fg(n):   return f'\033[38;5;{n}m'
def _bg(n):   return f'\033[48;5;{n}m'


def render_sprite(path):
    """
    Return an ANSI-coloured string rendering of the sprite at *path*.
    Each row of output characters represents two pixel rows.
    Transparent pixels use the terminal's default background.
    """
    img = Image.open(path).convert('RGBA')
    w, h = img.size
    pixels = img.load()

    lines = []
    # Step two pixel rows at a time
    for row in range(0, h, 2):
        line_parts = []
        for col in range(w):
            top = pixels[col, row]
            bot = pixels[col, row + 1] if row + 1 < h else (0, 0, 0, 0)

            tr, tg, tb, ta = top
            br, bg_, bb, ba = bot

            top_vis = ta >= 128
            bot_vis  = ba >= 128

            if not top_vis and not bot_vis:
                line_parts.append(_RST + ' ')
            elif top_vis and not bot_vis:
                # Upper half filled, lower transparent → ▀ in fg colour
                line_parts.append(_fg(_ansi256(tr, tg, tb)) + '▀' + _RST)
            elif not top_vis and bot_vis:
                # Lower half filled, upper transparent → ▄ in fg colour
                line_parts.append(_fg(_ansi256(br, bg_, bb)) + '▄' + _RST)
            else:
                # Both visible → ▀ with fg=top, bg=bottom
                line_parts.append(
                    _fg(_ansi256(tr, tg, tb)) +
                    _bg(_ansi256(br, bg_, bb)) +
                    '▀' + _RST
                )
        lines.append(''.join(line_parts))

    return '\n'.join(lines)


def _sprite_filename(npc_name):
    """Return the expected sprite filename for *npc_name*, or None if absent."""
    filename = npc_name.lower().strip() + '.png'
    path = os.path.join(_SPRITES_DIR, filename)
    return path if os.path.exists(path) else None


def get_sprite_path(npc_name):
    """Return the full path to the sprite file for *npc_name*, or None."""
    return _sprite_filename(npc_name)


def render_npc_sprite(npc_name):
    """
    Return rendered ANSI art for *npc_name*, or None if no sprite exists.
    """
    path = _sprite_filename(npc_name)
    if not path:
        return None
    try:
        return render_sprite(path)
    except Exception:
        return None
