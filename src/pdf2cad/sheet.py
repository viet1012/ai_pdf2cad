"""Khung bản vẽ chuẩn SPC (khổ giấy + khung tên + bảng dung sai chung + bảng sửa đổi + ghi chú chung).

Dữ liệu lấy từ mẫu TopSolid'Draft của công ty (docs/template.zip -> tools/dft_import.py ->
src/pdf2cad/sheets/<tên>.json, toạ độ mm trên tờ giấy). Mỗi mẫu có:
  - hình cố định: lines / arcs / circles / polys / fills / texts (nhãn ô, bảng dung sai...)
  - slots: ô điền giá trị — ô dưới nhãn khung tên (parts_name, title, material, scale, date,
    drawing_no, drawn...), ghi chú chung (notes), ký hiệu nhám chung (roughness)
  - obstacles: hộp bao các phần đã chiếm chỗ (để đặt hình chiếu vào vùng trống)

Vẽ ở tỷ lệ in 1:k: mọi toạ độ giấy nhân k (model mm, hình học chi tiết vẽ 1:1).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import cadstd

SHEETS_DIR = Path(__file__).parent / "sheets"
DEFAULT = "A4H-SPC"
LAYERS = {"FRAME": 7, "FRAME_TEXT": 7, "TITLE": 2}
_GRID = 0.5            # mm / ô lưới khi tìm vùng trống


def names() -> list[str]:
    return sorted(p.stem for p in SHEETS_DIR.glob("*.json"))


@lru_cache(maxsize=None)
def load(name: str = DEFAULT) -> dict:
    p = SHEETS_DIR / f"{name}.json"
    if not p.exists():
        raise ValueError(f"không có khung '{name}' (có: {', '.join(names())})")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- ghi chú / vùng trống
def _notes(sh: dict, notes: list[str] | None) -> list[str]:
    slot = sh["slots"].get("notes")
    if notes is None:
        return list(slot["sample"]) if slot else []
    return ["･" + n.lstrip("･・•· ") for n in notes if n.strip()]


def _notes_box(sh: dict, notes: list[str]) -> list[float] | None:
    slot = sh["slots"].get("notes")
    if not slot or not notes:
        return None
    w = max(cadstd.text_width(n, slot["h"], slot["wf"]) for n in notes)
    return [slot["x"], slot["y"] - 0.3 * slot["h"], slot["x"] + w,
            slot["y"] + (len(notes) - 1) * slot["gap"] + slot["h"]]


def _occupancy(sh: dict, notes: list[str], pad: float) -> tuple[np.ndarray, float, float]:
    bx0, by0, bx1, by1 = sh["border"]
    nx, ny = int(np.ceil((bx1 - bx0) / _GRID)), int(np.ceil((by1 - by0) / _GRID))
    occ = np.zeros((ny, nx), dtype=bool)
    boxes = list(sh["obstacles"])
    nb = _notes_box(sh, notes)
    if nb:
        boxes.append(nb)
    for x0, y0, x1, y1 in boxes:
        i0 = max(int((x0 - pad - bx0) / _GRID), 0)
        i1 = min(int(np.ceil((x1 + pad - bx0) / _GRID)), nx)
        j0 = max(int((y0 - pad - by0) / _GRID), 0)
        j1 = min(int(np.ceil((y1 + pad - by0) / _GRID)), ny)
        occ[j0:j1, i0:i1] = True
    m = int(np.ceil(pad / _GRID))               # chừa lề trong khung viền
    occ[:m, :] = occ[-m:, :] = True
    occ[:, :m] = occ[:, -m:] = True
    return occ, bx0, by0


def _largest_empty(occ: np.ndarray) -> tuple[int, int, int, int]:
    """Hình chữ nhật trống lớn nhất (thuật toán histogram). Trả (i0, j0, i1, j1) theo ô lưới."""
    ny, nx = occ.shape
    hgt = np.zeros(nx, dtype=int)
    best, res = 0, (0, 0, 0, 0)
    for j in range(ny):
        hgt = np.where(occ[j], 0, hgt + 1)
        stack = []
        for i in range(nx + 1):
            h = hgt[i] if i < nx else 0
            start = i
            while stack and stack[-1][1] >= h:
                s, sh_ = stack.pop()
                if sh_ * (i - s) > best:
                    best, res = sh_ * (i - s), (s, j - sh_ + 1, i, j + 1)
                start = s
            stack.append((start, h))
    return res


def free_area(sh: dict, notes: list[str] | None = None, pad: float = 2.0) -> list[float]:
    """Vùng trống lớn nhất trên tờ giấy (mm giấy): [x0, y0, x1, y1]."""
    occ, bx0, by0 = _occupancy(sh, _notes(sh, notes), pad)
    i0, j0, i1, j1 = _largest_empty(occ)
    return [bx0 + i0 * _GRID, by0 + j0 * _GRID, bx0 + i1 * _GRID, by0 + j1 * _GRID]


def notes_list(sh: dict, notes: list[str] | None) -> list[str]:
    """Các dòng ghi chú sẽ in (None = ghi chú mặc định của khung)."""
    return _notes(sh, notes)


def fit(sh: dict, need_w: float, need_h: float, scales, notes: list[str] | None = None,
        pad: float = 2.0, extra: tuple[float, float] = (0.0, 0.0),
        note_lines: list[str] | None = None) -> tuple[float, list[float]]:
    """Chọn tỷ lệ 1:k nhỏ nhất để khối hình need_w x need_h (mm model) + extra (mm giấy: chỗ ghi
    kích thước...) lọt vùng trống. note_lines: dòng ghi chú thực sự sẽ in (mặc định từ notes).
    Trả (k, [x0, y0, x1, y1] mm giấy) — ô đặt hình, ưu tiên giữa vùng trống lớn nhất."""
    occ, bx0, by0 = _occupancy(sh, note_lines if note_lines is not None else _notes(sh, notes), pad)
    ny, nx = occ.shape
    S = np.zeros((ny + 1, nx + 1), dtype=int)
    S[1:, 1:] = occ.cumsum(0).cumsum(1)
    li0, lj0, li1, lj1 = _largest_empty(occ)
    cx, cy = (li0 + li1) / 2, (lj0 + lj1) / 2
    for k in scales:
        w = int(np.ceil((need_w / k + extra[0]) / _GRID))
        h = int(np.ceil((need_h / k + extra[1]) / _GRID))
        if w > nx or h > ny:
            continue
        cnt = S[h:, w:] - S[:-h, w:] - S[h:, :-w] + S[:-h, :-w]       # số ô bận trong cửa sổ
        js, is_ = np.nonzero(cnt == 0)
        if not len(js):
            continue
        if li1 - li0 >= w and lj1 - lj0 >= h:                          # lọt vùng lớn nhất: đặt giữa
            i, j = int(round(cx - w / 2)), int(round(cy - h / 2))
        else:
            d = (is_ + w / 2 - cx) ** 2 + (js + h / 2 - cy) ** 2
            n = int(np.argmin(d))
            i, j = int(is_[n]), int(js[n])
        x0, y0 = bx0 + i * _GRID, by0 + j * _GRID
        return k, [x0, y0, x0 + need_w / k + extra[0], y0 + need_h / k + extra[1]]
    k = scales[-1]
    a = free_area(sh, notes, pad)
    return k, [a[0], a[1], a[0] + need_w / k + extra[0], a[1] + need_h / k + extra[1]]


# ---------------------------------------------------------------------------- vẽ
def _layers(doc):
    for name, aci in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=aci)


def _text(msp, s, x, y, h, k, *, wf=1.0, rot=0.0, font="SPC", layer="FRAME_TEXT", ha=0):
    """TEXT chân chữ tại y; ha: 0 trái / 1 giữa / 2 phải (điểm x); font "SPC_P" = chữ tỷ lệ FGP."""
    from ezdxf.enums import TextEntityAlignment as A
    s, style = cadstd.style_for(s, proportional=font == "SPC_P")
    t = msp.add_text(s, height=h * k, rotation=rot,
                     dxfattribs={"layer": layer, "style": style, "width": wf})
    t.set_placement((x * k, y * k), align={1: A.CENTER, 2: A.RIGHT}.get(ha, A.LEFT))
    return t


def _fill_cell(msp, slot: dict, value, k: float):
    """Ghi giá trị vào ô khung tên: canh giữa ô, giữ cỡ chữ mẫu, co hệ số rộng nếu dài quá ô."""
    lines_spec = slot["lines"]
    x0, y0, x1, y1 = slot["cell"]
    if value in (None, "", []):
        vals = ["-" for s in lines_spec if s["sample"].strip() == "-"][:1]
        specs = [s for s in lines_spec if s["sample"].strip() == "-"][:1]
    else:
        vals = [str(v) for v in (value if isinstance(value, (list, tuple)) else [value]) if str(v).strip()]
        if len(vals) <= len(lines_spec):
            specs = lines_spec[:len(vals)]
        else:                                    # nhiều dòng hơn mẫu: chia đều chiều cao ô
            h = min([s["h"] for s in lines_spec] or [2.555])
            gap = (y1 - y0) / len(vals)
            specs = [{"y": y1 - gap * (i + 0.5) - h / 2, "h": min(h, gap * 0.6), "wf": 1.0, "font": "SPC"}
                     for i in range(len(vals))]
    if not specs and vals:                       # ô trống trong mẫu: chữ 2.555 giữa ô
        h = min(2.555, (y1 - y0) * 0.5)
        specs = [{"y": (y0 + y1) / 2 - h / 2, "h": h, "wf": 1.0, "font": "SPC"}]
    for v, s in zip(vals, specs):
        wf = s["wf"]
        room = (x1 - x0) - 1.5
        tw = cadstd.text_width(cadstd.style_for(v)[0], s["h"], wf)
        if tw > room > 0:
            wf = max(round(wf * room / tw, 3), 0.3)
        _text(msp, v, (x0 + x1) / 2, s["y"], s["h"], k, wf=wf, font=s["font"], layer="TITLE", ha=1)


def draw(doc, k: float = 1.0, values: dict | None = None, name: str = DEFAULT) -> dict:
    """Vẽ khung chuẩn vào modelspace ở tỷ lệ 1:k. values: khoá slot -> giá trị
    (title: list dòng; notes: list ghi chú, None = ghi chú mặc định của mẫu; roughness: "3.2a").
    Trả dict thông tin khung (mm model): size, border."""
    sh = load(name)
    values = dict(values or {})
    cadstd.setup_styles(doc, default=False)
    _layers(doc)
    msp = doc.modelspace()

    def lw(v):
        return {"layer": "FRAME", "lineweight": int(round(v * 100))}

    for x1, y1, x2, y2, w in sh["lines"]:
        msp.add_line((x1 * k, y1 * k), (x2 * k, y2 * k), dxfattribs=lw(w))
    for cx, cy, r, w in sh["circles"]:
        msp.add_circle((cx * k, cy * k), r * k, dxfattribs=lw(w))
    for cx, cy, r, a0, a1, w in sh["arcs"]:
        msp.add_arc((cx * k, cy * k), r * k, a0, a1, dxfattribs=lw(w))
    for pts, w in sh["polys"]:
        msp.add_lwpolyline([(x * k, y * k) for x, y in pts], dxfattribs=lw(w))
    for loops in sh["fills"]:
        hatch = msp.add_hatch(color=7, dxfattribs={"layer": "FRAME"})
        for lp in loops:
            hatch.paths.add_polyline_path([(x * k, y * k) for x, y in lp], is_closed=True)
    for t in sh["texts"]:
        _text(msp, t["s"], t["x"], t["y"], t["h"], k, wf=t["wf"], rot=t["rot"], ha=t.get("ha", 0),
              font=t["font"])

    for key, slot in sh["slots"].items():
        if key == "notes":
            notes = _notes(sh, values.get("notes"))
            for i, n in enumerate(reversed(notes)):
                _text(msp, n, slot["x"], slot["y"] + i * slot["gap"], slot["h"], k, wf=slot["wf"],
                      font=slot["font"], layer="TITLE")
        elif key == "roughness":
            _text(msp, values.get("roughness") or slot["sample"], slot["x"], slot["y"], slot["h"], k,
                  wf=slot["wf"], font=slot["font"], layer="TITLE")
        elif key != "projection":
            _fill_cell(msp, slot, values.get(key), k)
    W, H = sh["size"]
    return {"name": name, "size": [W * k, H * k], "border": [v * k for v in sh["border"]], "k": k}
