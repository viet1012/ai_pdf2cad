"""Đọc khung bản vẽ TopSolid'Draft (.dft, header "TOPCOMPRESS_V1.0") -> JSON khung chuẩn.

Chỉ lấy phần "biểu diễn" (record R_*: hình đã tính sẵn ở toạ độ tờ giấy, đơn vị mét):
  R_LINE    : 2 điểm 3D
  R_ELLIPSE : tâm, trục u, trục v, pháp tuyến, 2 bán kính, góc đầu/cuối (độ, tính từ u về v)
  R_POLYGON : n điểm 3D (ký hiệu nhám...)
  R_AREA    : vùng tô (logo)
  R_TEXT    : chuỗi UTF-16 + tên font + cỡ chữ (em) + hệ số rộng + điểm gốc (trái-chân chữ) + hướng
Phần tử "#constructive curve" (đường dựng, không in) bị bỏ.

Kết quả đã làm sạch (bỏ phần tử ẩn, đường dựng, bản sao ngoài tờ giấy, chữ trùng) + các "ô điền"
(slots: ô giá trị dưới nhãn khung tên, ghi chú chung, nhám chung) + vùng trống để đặt hình.

Dùng:  python tools/dft_import.py docs/template.zip  src/pdf2cad/sheets/      [--raw: bản thô để dò]
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path

MAGIC = b"TOPCOMPRESS_V1.0"
_TAG = re.compile(rb"([\x02-\x40])\x00\x00\x00((?:[A-Z_#a-z0-9 ][\x00]){2,64})")


def decompress(data: bytes) -> bytes:
    if not data.startswith(MAGIC):
        raise ValueError("không phải file TopSolid nén (TOPCOMPRESS_V1.0)")
    pos, out = len(MAGIC), []
    while pos + 8 <= len(data):
        _, n = struct.unpack_from("<II", data, pos)
        out.append(zlib.decompress(data[pos + 8:pos + 8 + n]))
        pos += 8 + n
    return b"".join(out)


def _tags(raw: bytes):
    res = []
    for m in _TAG.finditer(raw):
        n = m.group(1)[0]
        s = m.group(2)
        if len(s) < 2 * n:
            continue
        name = s[:2 * n].decode("utf-16-le")
        if name.startswith(("R_", "D_ELT", "#", "DText#")) or name in ("ELEMENTS",):
            res.append((m.start(), name, m.start() + 4 + 2 * n))
    return res


def _d(raw, p, n=1):
    v = struct.unpack_from("<%dd" % n, raw, p)
    return v if n > 1 else v[0]


def _mm(v):
    return round(v * 1000.0, 4)


def _str_at(raw, p, end, maxlen=400):
    if p + 4 > end:
        return None, p
    n = struct.unpack_from("<I", raw, p)[0]
    if not 0 < n <= maxlen or p + 4 + 2 * n > end:
        return None, p
    try:
        s = raw[p + 4:p + 4 + 2 * n].decode("utf-16-le")
    except UnicodeDecodeError:
        return None, p
    return s, p + 4 + 2 * n


def _texts(raw, data, end):
    """R_TEXT: tìm các cặp (chuỗi, font) rồi đọc tham số theo vị trí cố định sau tên font."""
    out = []
    p = data
    while p < end - 8:
        s, q = _str_at(raw, p, end)
        if s is None:
            p += 1
            continue
        font, r = _str_at(raw, q, end, 64)
        if font is None or not font.startswith("FG"):
            p += 1
            continue
        size = _d(raw, r + 0x24)
        wf = _d(raw, r + 0x2C)
        x, y = _d(raw, r + 0x54, 2)
        ux, uy = _d(raw, r + 0x6C, 2)
        ha = raw[r + 4]                    # canh ngang của điểm gốc: 0 trái, 1 giữa, 2 phải
        out.append({"text": s, "font": font, "size": _mm(size), "wf": round(wf, 4),
                    "x": _mm(x), "y": _mm(y), "rot": round(math.degrees(math.atan2(uy, ux)), 3),
                    "ha": ha})
        p = r + 0xA0
    return out


def parse(raw: bytes) -> dict:
    tags = _tags(raw)
    ents = []
    elt, elt_flags, elt_name, level, elt_attr = None, 0, "", 0, []
    for i, (off, name, data) in enumerate(tags):
        nxt = tags[i + 1][0] if i + 1 < len(tags) else len(raw)
        if name == "ELEMENTS":
            break
        if name == "D_ELT":
            level = struct.unpack_from("<I", raw, data + 12)[0] if data + 16 <= nxt else 0
            continue
        if name.startswith("#") or name.startswith("DText#"):
            elt_name = name
            elt_flags = struct.unpack_from("<I", raw, data)[0] if data + 4 <= nxt else 0
            elt_attr = list(raw[data + 8:data + 13])
            elt = off
            continue
        if not name.startswith("R_"):
            continue
        k = raw.find(b"\xff\xff", data, nxt)
        if k < 0:
            continue
        p = k + 10
        base = {"elt": elt_name.strip(), "flags": elt_flags, "eid": elt, "lv": level,
                "attr": elt_attr}
        if name == "R_LINE":
            x1, y1, _, x2, y2, _ = _d(raw, p, 6)
            ents.append({**base, "t": "line", "p": [_mm(x1), _mm(y1), _mm(x2), _mm(y2)],
                         "tail": raw[p + 48:min(nxt, p + 58)].hex()})
        elif name == "R_ELLIPSE":
            v = _d(raw, p, 16)
            cx, cy = v[0], v[1]
            ux, uy = v[3], v[4]
            nz = v[11]
            ra, rb, t0, t1 = v[12], v[13], v[14], v[15]
            a_u = math.degrees(math.atan2(uy, ux))
            if nz >= 0:
                a0, a1 = a_u + t0, a_u + t1
            else:
                a0, a1 = a_u - t1, a_u - t0
            full = abs(t1 - t0) >= 359.999
            ents.append({**base, "t": "circle" if full else "arc", "c": [_mm(cx), _mm(cy)],
                         "r": _mm(ra), "rb": _mm(rb), "a": [round(a0 % 360, 4), round(a1 % 360, 4)]})
        elif name == "R_POLYGON":
            n = struct.unpack_from("<I", raw, p + 1)[0]
            pts = [_d(raw, p + 5 + 24 * j, 2) for j in range(n)]
            ents.append({**base, "t": "poly", "pts": [[_mm(a), _mm(b)] for a, b in pts]})
        elif name == "R_AREA":
            nloop = struct.unpack_from("<I", raw, p)[0]
            q = p + 4
            loops = []
            for _ in range(nloop):
                q += 1
                n = struct.unpack_from("<I", raw, q)[0]
                q += 4
                loops.append([[_mm(a), _mm(b)] for a, b in (_d(raw, q + 24 * j, 2) for j in range(n))])
                q += 24 * n
            ents.append({**base, "t": "area", "loops": loops})
        elif name == "R_TEXT":
            for t in _texts(raw, p, nxt):
                ents.append({**base, "t": "text", **t})
    return {"entities": ents}


def fields(raw: bytes) -> dict:
    """Thuộc tính tài liệu ($TITLE1, $DOC_NUMBER ...) ở đầu file."""
    out = {}
    p = 0x50
    while p < 0x600:
        k, q = _str_at(raw, p, 0x600, 64)
        if k and k.startswith("$"):
            q += 4                      # kiểu (1 = chuỗi)
            v, q2 = _str_at(raw, q, 0x600)
            out.setdefault(k, v or "")
            p = q2 if v is not None else q + 4
        else:
            p += 1
    return out


# ----------------------------------------------------------------------------- làm sạch -> khung chuẩn
CAP = 0.73            # chiều cao chữ hoa / cỡ em của FG教科書体 (CAD: chiều cao TEXT = chiều cao chữ hoa)
HIDDEN_LEVELS = {29}  # "※General Tolerance f" (tắt trong mẫu; bảng đang bật là JIS B 0405-m)
FONT_STYLE = {"FG教科書体NT-M": "SPC", "FGP教科書体NT-M": "SPC_P"}
# nhãn ô khung tên -> khoá giá trị (ô giá trị nằm ngay dưới ô nhãn)
LABELS = {"parts name": "parts_name", "drawing title": "title", "material": "material",
          "heat treatment": "heat_treatment", "hardness": "hardness",
          "surface treatment": "surface_treatment", "remarks": "remarks", "design": "design",
          "drawn": "drawn", "check": "check", "approved": "approved", "approve": "approved",
          "quantity": "quantity", "scale": "scale", "projection": "projection", "date": "date",
          "drawing number": "drawing_no"}
_W = {1: 0.13, 2: 0.18, 3: 0.25, 4: 0.35}


def _visible(e) -> bool:
    a = e.get("attr") or [0, 0]
    if e["elt"].startswith(("#constructive", "#point", "#key point", "#end point", "#relative point")):
        return False
    if a[0] == 1 or a[1] in HIDDEN_LEVELS:          # byte 0 = 1: phần tử bị ẩn
        return False
    return not (e["t"] == "text" and (":\\" in e["text"] or not e["text"].strip()))


def _pts(e):
    if e["t"] == "line":
        return [e["p"][:2], e["p"][2:]]
    if e["t"] in ("arc", "circle"):
        (x, y), r = e["c"], e["r"]
        return [[x - r, y - r], [x + r, y + r]]
    if e["t"] == "poly":
        return e["pts"]
    if e["t"] == "area":
        return [p for lp in e["loops"] for p in lp]
    w = len(e["text"]) * 0.5 * e["size"] * e["wf"]
    x0 = e["x"] - w * {1: 0.5, 2: 1.0}.get(e.get("ha", 0), 0.0)
    return [[x0, e["y"]], [x0 + w, e["y"] + e["size"] * CAP]]


def _lw(e) -> float:
    a = e.get("attr") or [0, 0, 0, 2]
    if e["elt"] == "#draft drawing":                 # khung giấy: viền trong đậm, đường cắt + vạch chia mảnh
        x1, y1, x2, y2 = e["p"]
        return 0.18 if e["tail"].startswith("010c") else 0.5 if max(abs(x2 - x1), abs(y2 - y1)) > 50 else 0.25
    if e["elt"].startswith("#draft border"):         # bảng: viền ngoài đậm, đường chia mảnh
        return 0.25 if e["tail"][12:14] == "01" else 0.5
    return _W.get(a[3], 0.35 if a[3] > 4 else 0.18)


def _grid(ents):
    """Đường kẻ khung tên / bảng (để tìm ô)."""
    hs, vs = [], []
    for e in ents:
        if e["t"] != "line" or not e["elt"].startswith(("#draft border", "#draft drawing")):
            continue
        x1, y1, x2, y2 = e["p"]
        if abs(y1 - y2) < 1e-3:
            hs.append((y1, min(x1, x2), max(x1, x2)))
        elif abs(x1 - x2) < 1e-3:
            vs.append((x1, min(y1, y2), max(y1, y2)))
    return hs, vs


def _cell(hs, vs, x, y):
    e = 1e-3
    up = min((h for h, a, b in hs if h > y + e and a - e <= x <= b + e), default=None)
    dn = max((h for h, a, b in hs if h < y - e and a - e <= x <= b + e), default=None)
    lf = max((v for v, a, b in vs if v < x - e and a - e <= y <= b + e), default=None)
    rt = min((v for v, a, b in vs if v > x + e and a - e <= y <= b + e), default=None)
    if None in (up, dn, lf, rt):
        return None
    return [round(lf, 4), round(dn, 4), round(rt, 4), round(up, 4)]


def _inside(c, x, y):
    return c[0] <= x <= c[2] and c[1] <= y <= c[3]


def _tcenter(t):
    (x0, y0), (x1, y1) = _pts(t)
    return (x0 + x1) / 2, (y0 + y1) / 2


def clean(res: dict) -> dict:
    """Bỏ phần ẩn / đường dựng / bản sao ngoài tờ giấy; tìm ô điền giá trị trong khung tên."""
    ents = res["entities"]
    frame = [e for e in ents if e["elt"] == "#draft drawing" and e["t"] == "line"]
    W = max(max(e["p"][0], e["p"][2]) for e in frame)
    H = max(max(e["p"][1], e["p"][3]) for e in frame)
    inner = [e["p"] for e in frame if _lw(e) == 0.5]
    bx0 = min(min(p[0], p[2]) for p in inner)
    by0 = min(min(p[1], p[3]) for p in inner)
    bx1 = max(max(p[0], p[2]) for p in inner)
    by1 = max(max(p[1], p[3]) for p in inner)

    keep = []
    for e in ents:
        if not _visible(e):
            continue
        pts = _pts(e)
        lim = (0, 0, W, H) if e["elt"] == "#draft drawing" else (bx0, by0, bx1, by1)
        if not all(lim[0] - 0.5 <= x <= lim[2] + 0.5 and lim[1] - 0.5 <= y <= lim[3] + 0.5 for x, y in pts):
            continue
        keep.append(e)
    # chữ trùng (cùng nội dung, chồng lên nhau) chỉ giữ 1
    texts, seen = [], []
    for e in keep:
        if e["t"] != "text":
            continue
        c = _tcenter(e)
        if any(s == e["text"] and abs(c[0] - d[0]) < 3 and abs(c[1] - d[1]) < 3 for s, d in seen):
            e["dup"] = True
            continue
        seen.append((e["text"], c))
        texts.append(e)
    keep = [e for e in keep if not e.get("dup")]

    hs, vs = _grid(keep)
    slots, used = {}, set()
    labels = [t for t in texts if t["text"].strip().lower() in LABELS]
    for lab in labels:
        key = LABELS[lab["text"].strip().lower()]
        cx, cy = _tcenter(lab)
        lc = _cell(hs, vs, cx, cy)
        if not lc or key in slots:
            continue
        vc = _cell(hs, vs, cx, lc[1] - 0.3)
        if not vc or vc[3] > lc[1] + 1e-3:
            continue
        vals = [t for t in texts if id(t) not in used and t is not lab
                and t["text"].strip().lower() not in LABELS and _inside(vc, *_tcenter(t))]
        vals.sort(key=lambda t: -t["y"])
        for t in vals:
            used.add(id(t))
        lines = [{"y": t["y"], "h": round(t["size"] * CAP, 3), "wf": t["wf"],
                  "font": FONT_STYLE.get(t["font"], "SPC"), "sample": t["text"]} for t in vals]
        slots[key] = {"cell": vc, "lines": lines}
    # ghi chú chung (dòng bắt đầu bằng "･") và ký hiệu nhám chung (chữ nhám đầu tiên)
    notes = sorted((t for t in texts if t["text"].startswith("･") and id(t) not in used), key=lambda t: -t["y"])
    if notes:
        for t in notes:
            used.add(id(t))
        gap = (notes[0]["y"] - notes[-1]["y"]) / (len(notes) - 1) if len(notes) > 1 else notes[0]["size"] * 1.5
        slots["notes"] = {"x": _pts(notes[0])[0][0], "y": notes[-1]["y"], "gap": round(gap, 3),
                          "h": round(notes[0]["size"] * CAP, 3), "wf": notes[0]["wf"],
                          "font": FONT_STYLE.get(notes[0]["font"], "SPC"),
                          "sample": [t["text"] for t in notes]}
    rug = sorted((t for t in texts if t["elt"] == "#aligned rugosity"), key=lambda t: t["x"])
    if rug:
        used.add(id(rug[0]))
        t = rug[0]
        slots["roughness"] = {"x": _pts(t)[0][0], "y": t["y"], "h": round(t["size"] * CAP, 3), "wf": t["wf"],
                              "font": FONT_STYLE.get(t["font"], "SPC"), "sample": t["text"]}

    out = {"name": res["name"], "size": [W, H], "border": [bx0, by0, bx1, by1],
           "lines": [], "arcs": [], "circles": [], "polys": [], "fills": [], "texts": [], "slots": slots}
    boxes = []
    for e in keep:
        if e["t"] == "text":
            if id(e) in used:
                if e["elt"] == "#aligned rugosity":       # ký hiệu nhám vẫn chiếm chỗ
                    p = _pts(e)
                    boxes.append([p[0][0], p[0][1], p[1][0], p[1][1]])
                continue
            out["texts"].append({"s": e["text"], "x": e["x"], "y": e["y"], "h": round(e["size"] * CAP, 3),
                                 "wf": e["wf"], "rot": e["rot"], "ha": e.get("ha", 0),
                                 "font": FONT_STYLE.get(e["font"], "SPC")})
        elif e["t"] == "line":
            out["lines"].append(e["p"] + [_lw(e)])
        elif e["t"] == "circle":
            out["circles"].append(e["c"] + [e["r"], _lw(e)])
        elif e["t"] == "arc":
            out["arcs"].append(e["c"] + [e["r"]] + e["a"] + [_lw(e)])
        elif e["t"] == "poly":
            out["polys"].append([e["pts"], _lw(e)])
        elif e["t"] == "area":
            out["fills"].append(e["loops"])
        if e["elt"] != "#draft drawing":
            p = _pts(e)
            boxes.append([min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p)])
    out["obstacles"] = _merge_boxes(boxes)
    return out


def _merge_boxes(boxes, gap=-0.01):
    """Gộp các hộp bao chồng/sát nhau (bớt số hộp cần tránh khi đặt hình)."""
    bs = [list(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                a, b = bs[i], bs[j]
                if a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                    bs[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del bs[j]
                    changed = True
                    break
            if changed:
                break
    return [[round(v, 3) for v in b] for b in bs]


def convert(name: str, data: bytes) -> dict:
    raw = decompress(data)
    res = parse(raw)
    res["name"] = name
    res["fields"] = fields(raw)
    return res


def main(argv):
    src, dst = Path(argv[1]), Path(argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            items = [(Path(n).stem, z.read(n)) for n in z.namelist() if n.lower().endswith(".dft")]
    else:
        items = [(src.stem, src.read_bytes())]
    raw_dump = "--raw" in argv
    for name, data in items:
        res = convert(name, data)
        out = res if raw_dump else clean(res)
        (dst / f"{name}.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                                          encoding="utf-8")
        if raw_dump:
            print(name, len(res["entities"]))
        else:
            print(f"{name}: {out['size'][0]}x{out['size'][1]}  lines={len(out['lines'])} texts={len(out['texts'])}"
                  f"  slots={sorted(out['slots'])}  obstacles={len(out['obstacles'])}")


if __name__ == "__main__":
    main(sys.argv)
