"""Chuẩn CAD của công ty (SPC): font chữ cho mọi TEXT / DIMENSION trong DXF xuất ra.

Font chuẩn (docs/Font.zip):
  - FG-SPC.ttf        : bản FG教科書体NT-M có thêm tiếng Việt (cùng nét chữ Latin, mỗi ký tự rộng 0.5 em)
                        -> style "SPC", dùng cho toàn bộ chữ (kích thước, ghi chú, khung tên).
  - FGCNTKYM.TTC      : FG教科書体NT-M + FGP教科書体NT-M (chữ tỷ lệ), có chữ Nhật / ký tự toàn khổ
                        -> style "SPC_P" (FGP: chữ khung tên mẫu ghi bằng FGP, vd tên công ty),
                           style "SPC_J" (NT-M: chữ có ký tự toàn khổ như ～ ･ mà FG-SPC không có).
Máy mở DXF cần cài 2 font này (Windows: chuột phải -> Install for all users).

Chiều cao TEXT trong CAD là chiều cao chữ hoa; với FG教科書体 chữ hoa = 0.73 em (CAP), nên chữ
TopSolid cỡ 3.5 (em) tương ứng TEXT cao 2.555.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"
FONT_FILE = "FG-SPC.ttf"
FONT_FAMILY = "FG-SPC"
FONT_P_FILE = "FGCNTKYM.TTC"
FONT_P_FAMILY = "FGP教科書体NT-M"
FONT_J_FAMILY = "FG教科書体NT-M"
STYLE = "SPC"
STYLE_P = "SPC_P"
STYLE_J = "SPC_J"
CAP = 0.73                 # chiều cao chữ hoa / em
EM_W = 0.5                 # bề rộng 1 ký tự Latin / em (FG-SPC đơn cách)

# ký tự FG-SPC không có -> ký tự tương đương có trong font
_MAP = {"～": "~", "〜": "~", "∼": "~", "･": "·", "・": "·", "•": "·", "Ø": "Φ", "ø": "φ", "⌀": "Φ",
        "∅": "Φ", "ｍ": "m"}
_ALWAYS = {"Ø": "Φ", "ø": "φ", "⌀": "Φ", "∅": "Φ"}
# Ký hiệu đường kính: cả FG-SPC lẫn NT-M đều không có Ø / ∅ (CAD vẽ %%c bằng ∅) -> ghi chữ Φ
# (FG-SPC có sẵn Φ hẹp 0.68 em dành cho việc này). Trong chương trình vẫn dùng mã %%c, chỉ đổi khi ghi DXF.
DIA = "Φ"


def dia(s: str) -> str:
    """%%c / %%C -> Φ (chữ ghi ra DXF, kể cả chữ đè của DIMENSION "%%c<>")."""
    return s.replace("%%c", DIA).replace("%%C", DIA)


def undia(s: str) -> str:
    """Ngược lại: Φ -> %%c (đọc DXF đã xuất để so khớp với mẫu / bảng dùng mã %%c)."""
    return s.replace(DIA, "%%c")


def _wide(ch: str) -> bool:
    return ord(ch) >= 0x3000 or unicodedata.east_asian_width(ch) in ("W", "F", "H")


def _vietnamese(ch: str) -> bool:
    return "Ạ" <= ch <= "ỹ" or ch in "ĂăĐđĨĩŨũƠơƯư"


@lru_cache(maxsize=1)
def _spc_chars() -> frozenset | None:
    """Bảng ký tự có trong FG-SPC.ttf (None nếu không đọc được font)."""
    try:
        from fontTools.ttLib import TTFont
        return frozenset(chr(c) for c in TTFont(str(FONT_DIR / FONT_FILE)).getBestCmap())
    except Exception:
        return None


def _missing(ch: str) -> bool:
    cs = _spc_chars()
    return _wide(ch) if cs is None else (ch not in cs and ch not in "\t\r\n")


def style_for(s: str, proportional: bool = False) -> tuple[str, str]:
    """Chọn (chữ, style) theo font chuẩn: chữ có ký tự FG-SPC không có (～ ･ ⇒ ...) mà không có tiếng Việt
    -> giữ nguyên, dùng FG教科書体NT-M (style SPC_J); còn lại -> FG-SPC (SPC) hoặc FGP (SPC_P)."""
    s = "".join(_ALWAYS.get(ch, ch) for ch in dia(s))     # ký hiệu đường kính: cả 2 font đều không có Ø
    if any(_missing(ch) for ch in s) and not any(_vietnamese(ch) for ch in s):
        return s, STYLE_P if proportional else STYLE_J
    s = std_text(s)
    if proportional and s.isascii():
        return s, STYLE_P
    return s, STYLE


def std_text(s: str) -> str:
    """Đổi các ký tự font chuẩn không có (dấu ～ / ･ toàn khổ, chữ full-width...) sang ký tự tương đương.
    Mã %%p, %%d giữ nguyên (font có ± °); %%c do style_for / dia() đổi sang Φ."""
    out = []
    for ch in s:
        ch = _MAP.get(ch, ch)
        if "！" <= ch <= "～":          # chữ/số full-width -> thường
            ch = unicodedata.normalize("NFKC", ch)
        out.append(ch)
    return "".join(out)


def setup_styles(doc, default: bool = True) -> str:
    """Thêm text style chuẩn vào bản vẽ; default=True: đặt "SPC" làm style mặc định ($TEXTSTYLE, dimstyle
    STANDARD/EZDXF). Trả về tên style chính."""
    for name, font, family in ((STYLE, FONT_FILE, FONT_FAMILY), (STYLE_P, FONT_P_FILE, FONT_P_FAMILY),
                               (STYLE_J, FONT_P_FILE, FONT_J_FAMILY)):
        if name in doc.styles:
            continue
        st = doc.styles.add(name, font=font)
        try:
            st.set_extended_font_data(family=family, italic=False, bold=False)
        except Exception:      # ezdxf cũ: không có XDATA tên font -> CAD vẫn tìm theo tên file
            pass
    if default:
        doc.header["$TEXTSTYLE"] = STYLE
        for ds in ("Standard", "EZDXF"):
            if ds in doc.dimstyles:
                doc.dimstyles.get(ds).dxf.dimtxsty = STYLE
    return STYLE


def apply_standard(doc) -> int:
    """Đổi mọi TEXT trong modelspace (vd base.dxf tạo bằng font cũ) sang font chuẩn. Trả số chữ đã đổi."""
    old_fonts = {st.dxf.name.lower(): st.dxf.get("font", "") for st in doc.styles}
    setup_styles(doc)
    n = 0
    for e in doc.modelspace().query("TEXT"):
        old = old_fonts.get(e.dxf.get("style", "Standard").lower(), "")
        s, style = style_for(e.dxf.text)
        if style == STYLE and old.lower().endswith((".ttf", ".otf")) and old.lower() != FONT_FILE.lower():
            # giữ nguyên chiều dài chữ đã khớp với bản gốc (hệ số rộng tính theo font cũ)
            try:
                from ezdxf.fonts import fonts
                h, wf = e.dxf.height, e.dxf.get("width", 1.0)
                t = e.dxf.text.replace("%%c", "Ø").replace("%%p", "±").replace("%%d", "°")
                w_old = fonts.make_font(old, h, wf).text_width(t)
                w_new = text_width(s, h, wf)
                if w_old > 0 and w_new > 0:
                    e.dxf.width = round(wf * w_old / w_new, 3)
            except Exception:
                pass
        e.dxf.text = s
        e.dxf.style = style
        n += 1
    return n


@lru_cache(maxsize=1)
def register_fonts() -> bool:
    """Cho ezdxf biết font kèm theo (đo bề rộng chữ, xem trước) — chỉ quét 1 lần."""
    try:
        from ezdxf.fonts import fonts
        fonts.font_manager.scan_folder(FONT_DIR)
        return fonts.font_manager.has_font(FONT_FILE)
    except Exception:
        return False


def _estimate(text: str) -> float:
    """Bề rộng tính theo em (FG-SPC: Latin 0.5 em, ký tự toàn khổ 1 em)."""
    t = text.replace("%%c", "Φ").replace("%%p", "±").replace("%%d", "°")
    w = 0.0
    for ch in t:
        if ch in "Φφ":
            w += 0.7
        elif unicodedata.east_asian_width(ch) in ("W", "F") or ch in "±×÷":
            w += 1.0
        else:
            w += EM_W
    return w


def estimate_width(text: str, cap_height: float, width_factor: float = 1.0) -> float:
    """Như text_width nhưng chỉ tính theo bảng bề rộng (nhanh, không nạp font)."""
    return _estimate(text) * cap_height / CAP * width_factor


def text_width(text: str, cap_height: float, width_factor: float = 1.0) -> float:
    """Chiều dài chữ khi hiện bằng font chuẩn (cùng đơn vị với cap_height)."""
    if register_fonts() and not any(_missing(ch) for ch in text):
        try:
            from ezdxf.fonts import fonts
            f = fonts.make_font(FONT_FILE, cap_height, width_factor)
            t = text.replace("%%c", "Φ").replace("%%p", "±").replace("%%d", "°")
            return float(f.text_width(t))
        except Exception:
            pass
    return _estimate(text) * cap_height / CAP * width_factor
