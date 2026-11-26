"""Vẽ MỚI bản vẽ chi tiết tròn xoay đơn giản (trụ đặc / ống) từ bộ tham số.

Dùng cho mẫu "kind": "generator" — khi bản gốc chỉ là ảnh scan (không dựng lại hình từ ảnh được),
tool vẽ lại sạch: hình chiếu đứng + cạnh, kích thước là đối tượng DIMENSION thật (sửa được trong CAD),
dung sai, khung GD&T, ký hiệu nhám, ghi chú, khung tên. Hình học vẽ đúng kích thước thật (model 1:1);
khung bản vẽ chuẩn SPC (sheet.py, mặc định A4H-SPC; template.json "drawing.sheet" để đổi) được phóng
theo tỷ lệ in 1:k chọn tự động để hình lọt vùng trống. Chữ dùng font chuẩn (cadstd.py).
"""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf

from . import cadstd, sheet
from .dxf_writer import DXF_VERSION
from .layers import LAYER_DEFS
from .template import Template, _fmt

TEXT_STYLE = cadstd.STYLE
SCALES = [0.5, 1, 2, 2.5, 4, 5, 10, 20]          # 1:k  (0.5 = 2:1)


def _ev(expr, vals):
    if isinstance(expr, (int, float)):
        return float(expr)
    return eval(str(expr), {"__builtins__": {}}, {k: v for k, v in vals.items()})


def _pick_tol(spec, vals):
    """spec: [up, low] hoặc [[cond, up, low], ...] -> (up, low) theo nghĩa thường (low âm = trừ)."""
    if spec is None:
        return None
    if spec and isinstance(spec[0], (list, tuple)):
        for cond, up, low in spec:
            if _ev(cond, vals):
                return float(up), float(low)
        return None
    return float(spec[0]), float(spec[1])


def _scale_txt(k: float) -> str:
    return "2:1" if k == 0.5 else f"1:{_fmt(k)}"


def new_doc(k: float = 1.0, extra_layers: dict | None = None):
    """Bản vẽ trắng theo chuẩn: đơn vị mm, layer, linetype, text style font chuẩn, LTSCALE = k."""
    doc = ezdxf.new(DXF_VERSION, setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$MEASUREMENT"] = 1
    for name, (aci, lt) in {**LAYER_DEFS, "DIM": (3, "Continuous"), "TEXT": (7, "Continuous"),
                            **(extra_layers or {})}.items():
        if name not in doc.layers:
            doc.layers.add(name, color=aci, linetype=lt if lt in doc.linetypes else "Continuous")
    if "HIDDEN" not in doc.linetypes:
        doc.linetypes.add("HIDDEN", pattern=[9.525, 6.35, -3.175], description="Hidden __ __ __")
        doc.layers.get("HIDDEN").dxf.linetype = "HIDDEN"
    cadstd.setup_styles(doc)
    doc.header["$LTSCALE"] = k
    return doc


def _date(s) -> str:
    """Ngày theo khung chuẩn: YYYY/MM/DD."""
    return str(s or "").replace("-", "/")


def title_values(dr: dict, k: float, **kw) -> dict:
    """Giá trị điền khung tên chuẩn từ mục "drawing" của template.json."""
    v = {"title": [t for t in (dr.get("title1"), dr.get("title2"), dr.get("title3")) if t],
         "material": dr.get("material"), "scale": _scale_txt(k), "date": _date(dr.get("date")),
         "drawing_no": dr.get("drawing_no"), "notes": dr.get("notes"), "roughness": dr.get("roughness")}
    for key in ("heat_treatment", "hardness", "surface_treatment", "remarks", "design", "drawn", "check",
                "approved", "quantity", "parts_name"):
        if dr.get(key):
            v[key] = dr[key]
    v.update({a: b for a, b in kw.items() if b is not None})
    return v


class _Sheet:
    def __init__(self, doc, k: float):
        self.doc, self.msp, self.k = doc, doc.modelspace(), k

    def text(self, s, x, y, h=2.5, layer="TEXT", align="LEFT", rot=0.0, width=1.0):
        from ezdxf.enums import TextEntityAlignment as A
        s, style = cadstd.style_for(s)
        t = self.msp.add_text(s, height=h * self.k, rotation=rot,
                              dxfattribs={"layer": layer, "style": style, "width": width})
        t.set_placement((x, y), align={"LEFT": A.LEFT, "CENTER": A.MIDDLE_CENTER,
                                       "RIGHT": A.RIGHT, "BL": A.BOTTOM_LEFT,
                                       "MC": A.MIDDLE_CENTER, "ML": A.MIDDLE_LEFT}[align])
        return t

    def line(self, a, b, layer="THIN"):
        self.msp.add_line(a, b, dxfattribs={"layer": layer})

    def rect(self, x0, y0, x1, y1, layer="THIN"):
        self.msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                                dxfattribs={"layer": layer})


# ----------------------------------------------------------------- ký hiệu
def _gdt_symbol(sh: _Sheet, kind: str, cx: float, cy: float):
    k, m = sh.k, sh.msp
    if kind == "concentricity":
        m.add_circle((cx, cy), 1.6 * k, dxfattribs={"layer": "THIN"})
        m.add_circle((cx, cy), 0.8 * k, dxfattribs={"layer": "THIN"})
    elif kind == "roundness":
        m.add_circle((cx, cy), 1.5 * k, dxfattribs={"layer": "THIN"})
    elif kind == "cylindricity":
        m.add_circle((cx, cy), 1.2 * k, dxfattribs={"layer": "THIN"})
        for dx in (-0.9, 0.9):
            sh.line((cx + (dx - 0.9) * k, cy - 1.8 * k), (cx + (dx + 0.9) * k, cy + 1.8 * k))
    elif kind == "perpendicularity":
        sh.line((cx - 1.6 * k, cy - 1.4 * k), (cx + 1.6 * k, cy - 1.4 * k))
        sh.line((cx, cy - 1.4 * k), (cx, cy + 1.8 * k))


def _frame(sh: _Sheet, x, y, kind, value, datum=None):
    """Khung dung sai hình học, góc dưới-trái tại (x, y). Trả về (x_phải, y_giữa)."""
    k = sh.k
    h, w0 = 5.0 * k, 6.0 * k
    w1 = max(8.0, 1.9 * len(value) + 2.5) * k
    cells = [w0, w1] + ([5.0 * k] if datum else [])
    x1 = x + sum(cells)
    sh.rect(x, y, x1, y + h)
    cx = x
    for w in cells[:-1]:
        cx += w
        sh.line((cx, y), (cx, y + h))
    _gdt_symbol(sh, kind, x + w0 / 2, y + h / 2)
    sh.text(value, x + w0 + w1 / 2, y + h / 2, 2.8, align="MC")
    if datum:
        sh.text(datum, x + w0 + w1 + 2.5 * k, y + h / 2, 2.8, align="MC")
    return x1, y + h / 2


def _roughness(sh: _Sheet, x, y, label):
    """Ký hiệu nhám (tam giác + cạnh dài) với giá trị, đỉnh tại (x, y)."""
    k = sh.k
    a = (x - 3 * k, y + 5 * k)
    b = (x + 3 * k, y + 5 * k)
    sh.line(a, (x, y))
    sh.line((x, y), (x + 6 * k, y + 10 * k))
    sh.line(a, b)
    sh.line((x - 8 * k, y + 5 * k), a)
    sh.text(label, x - 9 * k, y + 6.5 * k, 3.0)


def _datum(sh: _Sheet, x, y, name="A"):
    """Ký hiệu chuẩn: tam giác tô đặc đứng trên bề mặt tại (x, y), ô chữ phía trên."""
    k = sh.k
    tri = [(x - 1.5 * k, y), (x + 1.5 * k, y), (x, y + 2.6 * k)]
    h = sh.msp.add_hatch(color=7, dxfattribs={"layer": "THIN"})
    h.paths.add_polyline_path(tri, is_closed=True)
    sh.msp.add_lwpolyline(tri, close=True, dxfattribs={"layer": "THIN"})
    sh.line((x, y + 2.6 * k), (x, y + 6 * k))
    sh.rect(x - 2.5 * k, y + 6 * k, x + 2.5 * k, y + 11 * k)
    sh.text(name, x, y + 8.5 * k, 3.0, align="MC")


def _leader(sh: _Sheet, tip, elbow, text, h=3.0):
    sh.line(tip, elbow)
    k = sh.k
    tw = (len(text) * 0.62 * h + 1) * k
    sh.line(elbow, (elbow[0] + tw, elbow[1]))
    sh.text(text, elbow[0] + 0.5 * k, elbow[1] + 0.8 * k, h)
    # mũi tên
    ang = math.atan2(elbow[1] - tip[1], elbow[0] - tip[0])
    for da in (0.25, -0.25):
        sh.line(tip, (tip[0] + 2.2 * k * math.cos(ang + da), tip[1] + 2.2 * k * math.sin(ang + da)))


def _dim(sh: _Sheet, p1, p2, base, angle, text="<>", tol=None):
    k = sh.k
    ov = {"dimscale": k, "dimlfac": 1.0, "dimtxt": 3.0, "dimasz": 2.5, "dimexe": 1.5, "dimexo": 1.0,
          "dimgap": 0.8, "dimtxsty": TEXT_STYLE, "dimdec": 3, "dimzin": 8, "dimtad": 1,
          "dimclrd": 0, "dimclre": 0}
    d = sh.msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, text=cadstd.dia(text),
                              dimstyle="EZDXF", override=ov, dxfattribs={"layer": "DIM"})
    if tol is not None:
        up, low = tol
        d.set_tolerance(up, -low, hfactor=0.65, dec=3, trailing_zeros=False)
    d.render()
    return d


# ----------------------------------------------------------------- vẽ
def generate_family(t: Template, vals: dict, out: str | Path) -> dict:
    g = t.spec["generator"]
    dr = t.spec.get("drawing", {})
    L = float(vals["L"])
    D = float(vals[g.get("D", "D")])
    d = float(vals[g["bore"]]) if g.get("bore") and g["bore"] in vals else 0.0
    C = float(vals[g["chamfer"]]) if g.get("chamfer") and g["chamfer"] in vals else 0.0
    bc = float(g.get("bore_chamfer", 0.0)) if d else 0.0
    R, r = D / 2, d / 2

    Dmax = max(D, float(vals[g["head"]["D"]])) if g.get("head") and g["head"].get("D") in vals else D
    Dside = Dmax

    # ---- khung chuẩn + tỷ lệ in: hình chiếu đứng + cạnh + chỗ ghi kích thước (mm giấy) lọt vùng trống
    shj = sheet.load(dr.get("sheet", sheet.DEFAULT))
    pk = [p for p in t.spec["params_show"] if p in vals]
    ptxt = "  ".join(f"{p}={_fmt(vals[p]) if isinstance(vals[p], float) else vals[p]}" for p in pk)
    extra_lines = ["PARAMETERS: " + ptxt] + ([vals["_info"]] if vals.get("_info") else [])
    notes = sheet.notes_list(shj, dr.get("notes"))
    # chỗ ghi kích thước / GD&T quanh hình (mm giấy, tính từ mép hình chiếu) — theo vị trí vẽ bên dưới
    gdt = dr.get("gdt", [])
    left = 52.0 if any(f.get("at") in ("bore", "left_face") for f in gdt) else 18.0
    below = 22.0                                                    # kích thước L: R + 14 + chữ
    for f in gdt:
        if f.get("at") == "left_face":
            below = max(below, 32.0 if f.get("note") else 28.0)
        elif f.get("at") != "bore":
            below = max(below, 32.0 + 5.0 * int(f.get("stack", 0)))
    below = max([below] + [28.0 + 5.0 * i for i, _ in enumerate(dr.get("view_notes", []))])
    above = 15.0
    k, box = sheet.fit(shj, L + Dside, Dmax, SCALES, extra=(left + 30 + 8, below + above),
                       note_lines=notes + extra_lines)

    doc = new_doc(k, {"TEXT_PARAM": (6, "Continuous")})
    sh = _Sheet(doc, k)
    m = sh.msp
    sheet.draw(doc, k, title_values(dr, k, parts_name=vals.get("_parts_name"),
                                    drawing_no=vals.get("_drawing_no")), name=shj["name"])
    ns = shj["slots"].get("notes")
    if ns:                                       # tham số bản vẽ này: ngay trên ghi chú chung
        for i, line in enumerate(reversed(extra_lines)):
            sh.text(line, ns["x"] * k, (ns["y"] + (len(notes) + i) * ns["gap"]) * k, ns["h"],
                    layer="TEXT_PARAM")
    else:
        for i, line in enumerate(reversed(extra_lines)):
            sh.text(line, box[0] * k, (box[1] + i * 5) * k, 3.0, layer="TEXT_PARAM")
    if dr.get("roughness") and "roughness" not in shj["slots"]:
        _roughness(sh, (box[0] + 20) * k, (box[3] - 12) * k, dr["roughness"])

    # ---- hình chiếu đứng (trục nằm ngang) + hình chiếu cạnh
    fx = box[0] * k + left * k
    cy = box[1] * k + below * k + Dmax / 2
    sx = fx + L + 30 * k + Dside / 2

    head = g.get("head")
    if head and head.get("D") in vals and head.get("L") in vals:
        HD, HT = float(vals[head["D"]]), float(vals[head["L"]])     # đường kính mũ, chiều dày mũ
        RH = HD / 2
        pts = [(fx, cy - RH), (fx + HT, cy - RH), (fx + HT, cy - R), (fx + L, cy - R),
               (fx + L, cy + R), (fx + HT, cy + R), (fx + HT, cy + RH), (fx, cy + RH)]
        m.add_lwpolyline(pts, close=True, dxfattribs={"layer": "OUTLINE"})
        sh.line((fx + HT, cy - R), (fx + HT, cy + R), "OUTLINE")
    elif C > 0:
        pts = [(fx + C, cy - R), (fx + L - C, cy - R), (fx + L, cy - R + C), (fx + L, cy + R - C),
               (fx + L - C, cy + R), (fx + C, cy + R), (fx, cy + R - C), (fx, cy - R + C)]
        m.add_lwpolyline(pts, close=True, dxfattribs={"layer": "OUTLINE"})
        for x in (fx + C, fx + L - C):
            sh.line((x, cy - R), (x, cy + R), "OUTLINE")
    else:
        sh.rect(fx, cy - R, fx + L, cy + R, "OUTLINE")
    if d:
        for sgn in (1, -1):
            sh.line((fx + bc, cy + sgn * r), (fx + L - bc, cy + sgn * r), "HIDDEN")
            if bc:
                sh.line((fx, cy + sgn * (r + bc)), (fx + bc, cy + sgn * r), "HIDDEN")
                sh.line((fx + L - bc, cy + sgn * r), (fx + L, cy + sgn * (r + bc)), "HIDDEN")
        if bc:
            for x in (fx + bc, fx + L - bc):
                sh.line((x, cy - r), (x, cy + r), "HIDDEN")
    sh.line((fx - 3 * k, cy), (fx + L + 3 * k, cy), "CENTER")
    # cạnh
    m.add_circle((sx, cy), R, dxfattribs={"layer": "OUTLINE"})
    if head and head.get("D") in vals:
        m.add_circle((sx, cy), float(vals[head["D"]]) / 2, dxfattribs={"layer": "OUTLINE"})
    if C > 0:
        m.add_circle((sx, cy), R - C, dxfattribs={"layer": "OUTLINE"})
    if d:
        m.add_circle((sx, cy), r, dxfattribs={"layer": "OUTLINE"})
        if bc:
            m.add_circle((sx, cy), r + bc, dxfattribs={"layer": "OUTLINE"})
    e = R + 3 * k
    sh.line((sx - e, cy), (sx + e, cy), "CENTER")
    sh.line((sx, cy - e), (sx, cy + e), "CENTER")

    # ---- kích thước
    dims = dr.get("dims", {})
    lspec = dims.get("L", {})
    _dim(sh, (fx, cy - R), (fx + L, cy - R), (fx, cy - R - 14 * k), 0,
         lspec.get("text", "<>"), _pick_tol(lspec.get("tol"), vals))
    dspec = dims.get("D", {})
    _dim(sh, (sx - R, cy), (sx + R, cy), (sx, cy - R - 14 * k), 0,
         dspec.get("text", "%%c<>"), _pick_tol(dspec.get("tol"), vals))
    if head and head.get("D") in vals and head.get("L") in vals:
        HD, HT = float(vals[head["D"]]), float(vals[head["L"]])
        _dim(sh, (fx, cy - HD / 2), (fx, cy + HD / 2), (fx - 12 * k, cy), 90,
             dims.get(head["D"], {}).get("text", "%%c<>"), _pick_tol(dims.get(head["D"], {}).get("tol"), vals))
        _dim(sh, (fx, cy + HD / 2), (fx + HT, cy + HD / 2), (fx, cy + HD / 2 + 10 * k), 0,
             "<>", _pick_tol(dims.get(head["L"], {}).get("tol"), vals))
    if d:
        bspec = dims.get("d", {})
        _dim(sh, (fx, cy - r), (fx, cy + r), (fx - 12 * k, cy), 90,
             bspec.get("text", "%%c<>"), _pick_tol(bspec.get("tol"), vals))
    if C > 0:
        _leader(sh, (fx + L - C / 2, cy - R + C / 2), (fx + L + 6 * k, cy - R - 6 * k),
                f"2-C{_fmt(C)}")
    if bc:
        _leader(sh, (fx + L - bc / 2, cy + r + bc / 2), (fx + L + 6 * k, cy + r + 4 * k + bc),
                f"2-C{_fmt(bc)}")

    # ---- GD&T + chuẩn
    for f in dr.get("gdt", []):
        val = str(_fmt(_ev(f["value"], vals))) if not isinstance(f["value"], str) or \
            f["value"] in vals else f["value"]
        at = f.get("at")
        if at == "bore":            # gắn vào kích thước Φd (bên trái hình chiếu đứng)
            x, y = fx - 50 * k, cy + R + 8 * k
            xr, ym = _frame(sh, x, y, f["kind"], val, f.get("datum"))
            sh.line((xr, ym), (fx - 12 * k, ym))
            sh.line((fx - 12 * k, ym), (fx - 12 * k, cy + r + 3 * k))
        elif at == "left_face":     # mặt đầu trái
            x, y = fx - 50 * k, cy - R - 26 * k
            xr, ym = _frame(sh, x, y, f["kind"], val, f.get("datum"))
            sh.line((xr, ym), (fx, ym))
            sh.line((fx, ym), (fx, cy - R))
            if f.get("note"):
                sh.text(f["note"].format(**{k2: (_fmt(v) if isinstance(v, float) else v)
                                            for k2, v in vals.items()}), x, y - 5 * k, 2.6)
        else:                        # gắn vào ΦD ở hình chiếu cạnh, xếp chồng
            i = int(f.get("stack", 0))
            x, y = sx - R - 40 * k, cy - R - 30 * k - i * 5 * k
            xr, ym = _frame(sh, x, y, f["kind"], val, f.get("datum"))
            if i == 0:
                sh.line((xr, ym + 2.5 * k), (sx - R * 0.7, cy - R * 0.7))
    if dr.get("datum_on_top"):
        _datum(sh, fx + L * 0.65, cy + R, dr["datum_on_top"])
    for i, line in enumerate(dr.get("view_notes", [])):
        sh.text(line.format(**{k2: (_fmt(v) if isinstance(v, float) else v) for k2, v in vals.items()}),
                fx + L * 0.25, cy - R - 26 * k - i * 5 * k, 2.8)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out)

    # ---- kiểm tra: đo lại trên hình vừa vẽ
    chk = ezdxf.readfile(out).modelspace()
    pts = [(x, y) for e2 in chk.query('LWPOLYLINE[layer=="OUTLINE"]') for x, y in e2.vertices()
           if fx - 1e-6 <= x <= fx + L + 1e-6 and abs(y - cy) <= Dmax / 2 + 1e-6]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    circles = sorted(c.dxf.radius * 2 for c in chk.query('CIRCLE[layer=="OUTLINE"]')
                     if abs(c.dxf.center.x - sx) < 1e-6)
    if head and head.get("D") in vals:
        HD = float(vals[head["D"]])
        check = [["L", L, round(max(xs) - min(xs), 4)], [f"Φ{head['D']}", HD, round(max(ys) - min(ys), 4)],
                 [f"Φ{head['D']} (cạnh)", HD, round(circles[-1], 4)],
                 ["ΦD (cạnh)", D, round(min(c for c in circles if c >= D - 1e-6), 4)]]
        return {"output": str(out), "check": check, "scale": _scale_txt(k)}
    check = [["L", L, round(max(xs) - min(xs), 4)], ["ΦD", D, round(max(ys) - min(ys), 4)],
             ["ΦD (cạnh)", D, round(circles[-1], 4)]]
    if d:
        check.append(["Φd (cạnh)", d, round(min(circles), 4)])
    return {"output": str(out), "check": check, "scale": _scale_txt(k)}
