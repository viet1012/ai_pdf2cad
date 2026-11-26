"""Mẫu tham số (template) dựng từ bản vẽ PDF -> vẽ lại theo kích thước mong muốn.

Ý tưởng: bản vẽ họ sản phẩm (ví dụ DSJC-B) vẽ "Scale FREE", kích thước ghi bằng ký hiệu
(L, S, ØH...) + bảng giá trị. Mẫu lưu:
  - base.dxf      : bản vẽ đã chuyển từ PDF (có TEXT nhờ OCR)
  - template.json : vùng hình chiếu, trục, mỗi ký hiệu kích thước đo từ đâu đến đâu,
                    bảng tham số + quy tắc (NOTE), cách thay chữ ký hiệu bằng số.
Khi vẽ lại: giải hệ để tìm vị trí mới của các "điểm mốc" dọc trục (x) và bán kính (r) sao cho
mọi kích thước đúng giá trị, rồi co giãn tuyến tính từng đoạn toàn bộ hình chiếu => DXF 1:1.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import os

from .i18n import Bi, en_of, ja_of

# Thư mục mẫu: biến môi trường PDF2CAD_TEMPLATES, mặc định <repo>/templates
TEMPLATES_DIR = Path(os.environ.get("PDF2CAD_TEMPLATES",
                                    Path(__file__).resolve().parents[2] / "templates"))


# ----------------------------------------------------------------- đọc mẫu
@dataclass
class Template:
    name: str
    dir: Path
    spec: dict
    title: str = ""
    params: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Template":
        path = Path(path)
        d = path if path.is_dir() else path.parent
        spec = json.loads((d / "template.json").read_text(encoding="utf-8"))
        return cls(spec["name"], d, spec, spec.get("title", spec["name"]), spec["params"])

    @property
    def base_dxf(self) -> Path:
        return self.dir / self.spec["base_dxf"]


# Mẫu tự học từ bản vẽ mới (pdf2cad learn / nút "Dạy bot")
LEARNED_DIR = Path(os.environ.get("PDF2CAD_LEARNED", TEMPLATES_DIR / "learned"))


def list_templates(root: Path | None = None, learned: Path | None = None) -> list[Template]:
    out: list[Template] = []
    for r in (Path(root or TEMPLATES_DIR), Path(learned or LEARNED_DIR)):
        if r.exists():
            for p in sorted(r.iterdir()):
                if (p / "template.json").exists():
                    try:
                        out.append(Template.load(p))
                    except Exception:            # mẫu hỏng không làm hỏng cả danh sách
                        pass
    return out


# ----------------------------------------------------------------- tham số & quy tắc
def _fmt(v: float) -> str:
    """8.0 -> '8', 1.50 -> '1.5'"""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _rng(spec) -> tuple[float, float] | None:
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        return float(spec), float(spec)
    return float(spec[0]), float(spec[1])


# ----------------------------------------------------------------- bảng tham số (tổng quát)
def keys_of(t: "Template") -> list[str]:
    k = t.spec["table_key"]
    return list(k) if isinstance(k, list) else [k]


def expand_values(spec: str, step: float = 10.0) -> list[float]:
    """'20~50/55/60~150' -> [20, 30, 40, 50, 55, 60, 70, ..., 150] (bước 10 mm như ghi chú bảng)."""
    out: list[float] = []
    for part in str(spec).replace(" ", "").split("/"):
        if not part:
            continue
        if "~" in part:
            # "15~60" (bước 10): 15, 20, 30, ..., 60 — bước bám theo bội số của 10
            # (khớp cách bảng ghi "20~50/55/60~150": 55 phải ghi riêng)
            a, b = (float(x) for x in part.split("~", 1))
            out.append(a)
            v = (a // step + 1) * step
            while v <= b + 1e-9:
                out.append(round(v, 4))
                v += step
            if abs(out[-1] - b) > 1e-9:
                out.append(b)
        else:
            out.append(float(part))
    return sorted(set(out))


def cell_info(cell, step: float = 10.0) -> dict:
    """Chuẩn hoá ô bảng -> {range, also, values, text, default}."""
    if cell is None:
        return {}
    if isinstance(cell, (int, float)):
        return {"range": (float(cell), float(cell)), "also": [], "values": None,
                "text": _fmt(float(cell))}
    if isinstance(cell, str):
        return {"range": None, "also": [], "values": expand_values(cell, step), "text": cell}
    if isinstance(cell, list):
        r = _rng(cell)
        return {"range": r, "also": [], "values": None,
                "text": _fmt(r[0]) + (f"~{_fmt(r[1])}" if r[1] != r[0] else "")}
    vals = cell.get("values")
    if isinstance(vals, str):
        vals_l = expand_values(vals, cell.get("step", step))
    else:
        vals_l = [float(x) for x in vals] if vals else None
    r = _rng(cell.get("range"))
    also = [float(x) for x in cell.get("also", [])]
    text = (vals if isinstance(vals, str) else "/".join(_fmt(x) for x in vals_l)) if vals_l else (
        (_fmt(r[0]) + (f"~{_fmt(r[1])}" if r[1] != r[0] else "")) if r else "-")
    if also:
        a = ", ".join(_fmt(x) for x in also)
        text = Bi(f"{text} (hoặc {a})", f"{text} (or {a})", f"{text} (または {a})")
    return {"range": r, "also": also, "values": vals_l, "text": text, "default": cell.get("default")}


def cell_allows(ci: dict, v: float) -> bool:
    if not ci:
        return True
    if ci.get("values"):
        return any(abs(v - x) < 1e-6 for x in ci["values"]) or any(abs(v - x) < 1e-6 for x in ci["also"])
    r = ci.get("range")
    return r is None or r[0] - 1e-9 <= v <= r[1] + 1e-9 or any(abs(v - x) < 1e-6 for x in ci["also"])


def cell_first(ci: dict) -> float | None:
    if not ci:
        return None
    if ci.get("default") is not None:
        return float(ci["default"])
    if ci.get("values"):
        return ci["values"][0]
    return ci["range"][0] if ci.get("range") else None


def nearest_allowed(ci: dict, v: float, n: int = 3) -> list[float]:
    pool = list(ci.get("values") or []) + list(ci.get("also") or [])
    if ci.get("range"):
        pool += [ci["range"][0], ci["range"][1]]
    return sorted(sorted(set(pool), key=lambda x: abs(x - v))[:n])


def matching_rows(t: "Template", req: dict, ignore: str | None = None) -> list[dict]:
    keys = keys_of(t)
    return [r for r in t.spec["table"]
            if all(abs(float(r[k]) - float(req[k])) < 1e-9 for k in keys if k in req and k != ignore)]


def candidates(t: "Template", req: dict, key: str) -> list[float]:
    return sorted({float(r[key]) for r in matching_rows(t, req, ignore=key)})


def resolve_params(t: Template, req: dict) -> tuple[dict, list[str], list[str]]:
    """Từ yêu cầu (vd {'No': 16, 'L': 80} hoặc {'D': 30, 'd': 9, 'L': 120}) -> bộ tham số đầy đủ.

    Trả về (values, notes, errors). notes: các giá trị tự chọn/tính; errors: vi phạm quy tắc.
    """
    # thông báo là Bi (str tiếng Việt + .en): bot dò e.startswith("Thiếu ...") trên phần tiếng Việt
    notes, errors = [], []
    keys = keys_of(t)
    for k in keys:
        cands = candidates(t, req, k)
        cs = ", ".join(_fmt(c) for c in cands)
        if k not in req:
            errors.append(Bi(f"Thiếu {k} (chọn 1 trong: {cs})", f"Missing {k} (choose one of: {cs})",
                             f"{k} が未指定です（選択肢: {cs}）"))
            return {}, notes, errors
        if not any(abs(float(req[k]) - c) < 1e-9 for c in cands):
            other = ", ".join(f"{x}={_fmt(float(req[x]))}" for x in keys if x in req and x != k)
            kv = f"{k}={_fmt(float(req[k]))}"
            errors.append(Bi(f"{kv} không có trong bảng" + (f" (với {other})" if len(keys) > 1 else "")
                             + f" — chọn: {cs}",
                             f"{kv} is not in the table" + (f" (with {other})" if len(keys) > 1 else "")
                             + f" — choose: {cs}",
                             f"{kv} は表にありません" + (f"（{other} の場合）" if len(keys) > 1 else "")
                             + f" — 選択肢: {cs}"))
            return {}, notes, errors
    row = matching_rows(t, req)[0]
    step = float(t.spec.get("values_step", 10))
    vals: dict = {k: float(req[k]) for k in keys}
    label = ", ".join(f"{k}={_fmt(vals[k])}" for k in keys)
    for p, pdef in t.params.items():
        if p in keys or pdef.get("derived"):
            continue
        if pdef.get("info"):                          # trường thông tin (vd mã vật liệu)
            if p in row:
                vals[p] = row[p]
            continue
        ci = cell_info(row.get(p), step)
        tv, te, tj = (str(ci["text"]), en_of(ci["text"]), ja_of(ci["text"])) if ci else ("", "", "")
        if p in req:
            v = float(req[p])
            if ci and not cell_allows(ci, v):
                near = ", ".join(_fmt(x) for x in nearest_allowed(ci, v))
                hv = f"; gần nhất: {near}" if ci.get("values") else ""
                he = f"; nearest: {near}" if ci.get("values") else ""
                hj = f"; 最も近い値: {near}" if ci.get("values") else ""
                msg = Bi(f"⚠ {p}={_fmt(v)} ngoài bảng tiêu chuẩn cho {label} (bảng: {tv}{hv})"
                         " — vẫn vẽ theo yêu cầu (hàng đặc biệt)",
                         f"⚠ {p}={_fmt(v)} is outside the standard table for {label} (table: {te}{he})"
                         " — drawn as requested (special row)",
                         f"⚠ {p}={_fmt(v)} は {label} の標準表の範囲外です（表: {tj}{hj}）"
                         " — 指定どおり作図しました（特殊品）")
                (errors if pdef.get("strict") else notes).append(msg)
            vals[p] = v
        elif pdef.get("required"):
            errors.append(Bi(f"Thiếu {p}" + (f" (bảng: {tv})" if ci else ""),
                             f"Missing {p}" + (f" (table: {te})" if ci else ""),
                             f"{p} が未指定です" + (f"（表: {tj}）" if ci else "")))
        elif not ci and pdef.get("column"):          # ô bảng trống (OCR không đọc được) -> hỏi
            errors.append(Bi(f"Thiếu {p} — bảng chưa có {p} cho {label} (OCR không đọc được), nhập {p}=...",
                             f"Missing {p} — the table has no {p} for {label} (OCR could not read it), "
                             f"enter {p}=...",
                             f"{p} が未指定です — {label} の {p} が表にありません（OCR で読み取れず）。"
                             f"{p}=... を入力してください"))
        elif ci:
            v = cell_first(ci)
            vals[p] = v
            if ci.get("values") and len(ci["values"]) > 1 or (
                    ci.get("range") and ci["range"][0] != ci["range"][1]) or ci.get("default") is not None:
                notes.append(Bi(f"{p} chưa chỉ định -> dùng {_fmt(v)} (bảng: {tv})",
                                f"{p} not specified -> using {_fmt(v)} (table: {te})",
                                f"{p} 未指定 -> {_fmt(v)} を使用（表: {tj}）"))
        elif "default" in pdef:
            vals[p] = float(pdef["default"])

    num = {k: v for k, v in vals.items() if isinstance(v, (int, float))}
    # quy tắc NOTE: if <cond> then <param> = value (ghi đè giá trị bảng nếu người dùng không chỉ định)
    for rule in t.spec.get("rules", []):
        try:
            cond = eval(rule["if"], {"__builtins__": {}}, dict(num))
        except Exception:
            continue
        if cond:
            for p, v in rule["then"].items():
                if p in req and abs(float(req[p]) - v) > 1e-9:
                    cur = _fmt(float(req[p]))
                    notes.append(Bi(f"Lưu ý: NOTE '{rule['text']}' gợi ý {p}={_fmt(v)}, "
                                    f"đang dùng {p}={cur} theo yêu cầu",
                                    f"Note: NOTE '{rule['text']}' suggests {p}={_fmt(v)}, "
                                    f"using {p}={cur} as requested",
                                    f"注意: NOTE「{rule['text']}」では {p}={_fmt(v)} ですが、"
                                    f"指定どおり {p}={cur} を使用"))
                elif p not in req:
                    vals[p] = num[p] = float(v)
                    notes.append(Bi(f"{p}={_fmt(v)} theo NOTE: {rule['text']}",
                                    f"{p}={_fmt(v)} per NOTE: {rule['text']}",
                                    f"NOTE により {p}={_fmt(v)}: {rule['text']}"))

    for p, pdef in t.params.items():                  # tham số dẫn xuất (vd S1 = S + 1)
        if pdef.get("derived"):
            vals[p] = num[p] = float(eval(pdef["derived"], {"__builtins__": {}}, dict(num)))

    for c in t.spec.get("checks", []):                # ràng buộc hình học
        try:
            if not eval(c["expr"], {"__builtins__": {}}, dict(num)):
                errors.append(Bi(c["msg"], c.get("msg_en", c["msg"]),
                                 c.get("msg_ja", c.get("msg_en", c["msg"]))))
        except Exception:
            pass
    return vals, notes, errors


# ----------------------------------------------------------------- co giãn từng đoạn
def solve_breakpoints(dims: list[dict], vals: dict, fixed_zero: float | None = None):
    """dims: [{a, b, expr}] (toạ độ trên bản vẽ gốc). Trả về (điểm mốc gốc, vị trí mới)."""
    pts = sorted({round(float(x), 4) for d in dims for x in (d["a"], d["b"])}
                 | ({round(fixed_zero, 4)} if fixed_zero is not None else set()))
    idx = {p: i for i, p in enumerate(pts)}
    n = len(pts)
    rows, rhs, w = [], [], []
    ratios = []
    for d in dims:
        v = float(eval(d["expr"], {"__builtins__": {}}, dict(vals))) if isinstance(d["expr"], str) \
            else float(d["expr"])
        if d.get("half"):
            v /= 2
        a, b = round(float(d["a"]), 4), round(float(d["b"]), 4)
        r = np.zeros(n)
        r[idx[b]], r[idx[a]] = 1, -1
        rows.append(r), rhs.append(v), w.append(1000.0)
        if abs(b - a) > 1e-6:
            ratios.append(v / (b - a))
    s = float(np.median(ratios)) if ratios else 1.0
    for i in range(n - 1):              # đoạn không có kích thước: giữ tỷ lệ chung (ràng buộc yếu)
        r = np.zeros(n)
        r[i + 1], r[i] = 1, -1
        rows.append(r), rhs.append(s * (pts[i + 1] - pts[i])), w.append(1.0)
    anchor = idx[round(fixed_zero, 4)] if fixed_zero is not None else 0
    r = np.zeros(n)
    r[anchor] = 1
    rows.append(r), rhs.append(0.0), w.append(1e6)
    A = np.array(rows) * np.array(w)[:, None]
    X, *_ = np.linalg.lstsq(A, np.array(rhs) * np.array(w), rcond=None)
    return np.array(pts), X


def _warp1d(c: float, P: np.ndarray, X: np.ndarray) -> float:
    """Ánh xạ tuyến tính từng đoạn; ngoài khoảng: giữ khoảng cách tới mốc ngoài cùng."""
    if c <= P[0]:
        return X[0] + (c - P[0])
    if c >= P[-1]:
        return X[-1] + (c - P[-1])
    return float(np.interp(c, P, X))


@dataclass
class Warp:
    box: tuple[float, float, float, float]       # vùng hình chiếu (x0, y0, x1, y1)
    x0: float                                    # gốc trục dọc (mặt đầu)
    axis_y: float                                # tâm trục (chi tiết tròn xoay)
    Px: np.ndarray
    Xx: np.ndarray
    Pr: np.ndarray
    Xr: np.ndarray

    def inside(self, x, y) -> bool:
        b = self.box
        return b[0] <= x <= b[2] and b[1] <= y <= b[3]

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        if not self.inside(x, y):
            return x, y
        nx = self.x0 + _warp1d(x, self.Px, self.Xx)
        dy = y - self.axis_y
        nr = _warp1d(abs(dy), self.Pr, self.Xr)
        return nx, self.axis_y + math.copysign(nr, dy)

    def scale_at(self, x: float, y: float) -> float:
        e = 0.05
        a, b = self(x - e, y), self(x + e, y)
        c, d = self(x, y - e), self(x, y + e)
        return (math.dist(a, b) + math.dist(c, d)) / (4 * e)

    def scale_xy(self, x: float, y: float) -> tuple[float, float]:
        """Tỷ lệ co giãn cục bộ theo x và theo y."""
        e = 0.05
        return ((self(x + e, y)[0] - self(x - e, y)[0]) / (2 * e),
                abs(self(x, y + e)[1] - self(x, y - e)[1]) / (2 * e))

    def inverse(self, nx: float, ny: float) -> tuple[float, float]:
        """Điểm trên bản mới -> điểm tương ứng trên bản gốc (chỉ trong vùng hình chiếu)."""
        def inv(v, P, X):
            if v <= X[0]:
                return P[0] + (v - X[0])
            if v >= X[-1]:
                return P[-1] + (v - X[-1])
            return float(np.interp(v, X, P))
        dy = ny - self.axis_y
        return inv(nx - self.x0, self.Px, self.Xx), self.axis_y + math.copysign(inv(abs(dy), self.Pr, self.Xr), dy)


_FEAT_CACHE: dict = {}


def _outline_features(t: Template) -> list[tuple[float, float, float, float]]:
    """Cạnh thấy (layer OUTLINE) trong vùng hình chiếu: (x1, r1, x2, r2), r = khoảng cách tới tâm trục.
    Dùng để giữ hình dạng chi tiết không ghi kích thước (vát mép, góc lượn, mũi khoan)."""
    import ezdxf
    p = Path(t.base_dxf)
    key = (str(p), p.stat().st_mtime if p.exists() else 0)
    if key in _FEAT_CACHE:
        return _FEAT_CACHE[key]
    v = t.spec["view"]
    b, ay = v["box"], v["axis_y"]
    inside = lambda x, y: b[0] <= x <= b[2] and b[1] <= y <= b[3]
    segs = []
    try:
        msp = ezdxf.readfile(p).modelspace()
    except (OSError, ezdxf.DXFError):
        msp = []
    for e in msp:
        if e.dxf.layer != "OUTLINE":
            continue
        if e.dxftype() == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif e.dxftype() == "LWPOLYLINE":
            pts = [(q[0], q[1]) for q in e.get_points("xy")]
        else:
            continue
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if not (inside(x1, y1) and inside(x2, y2)) or (y1 - ay) * (y2 - ay) < 0:
                continue                                # cạnh cắt ngang tâm trục: không dùng
            segs.append((x1, abs(y1 - ay), x2, abs(y2 - ay)))
    _FEAT_CACHE[key] = segs
    return segs


def solve_warp(x_dims: list[dict], r_dims: list[dict], vals: dict, x0: float,
               feats: list[tuple[float, float, float, float]] = ()):
    """Giải chung trục dọc (x, gốc x0) và bán kính (r, gốc 0).

    - Kích thước có ghi (x_dims / r_dims, toạ độ gốc; r_dims đã tính từ tâm trục): ràng buộc cứng.
    - Cạnh xiên của hình (vát 45°, mũi khoan 118°, cung lượn chia đoạn): giữ nguyên góc, nên x và r
      ở vùng đó phải co giãn cùng tỷ lệ -> không bị méo khi L và Φ đổi khác tỷ lệ nhau.
    - Mỗi khoảng giữa 2 mốc (kể cả đỉnh hình): ưu tiên giữ tỷ lệ chung s; sai lệch tương đối nên
      khoảng dài (thân) gánh phần co giãn, chi tiết nhỏ (vát, lượn) gần như giữ nguyên dạng.
    Trả về (Px, X, Pr, R): mốc gốc và vị trí mới (X tính từ x0)."""
    rd = lambda z: round(float(z), 4)

    def val(d):
        v = float(eval(d["expr"], {"__builtins__": {}}, dict(vals))) if isinstance(d["expr"], str) \
            else float(d["expr"])
        return v / 2 if d.get("half") else v

    Px = sorted({rd(z) for d in x_dims for z in (d["a"], d["b"])} | {rd(x0)}
                | {rd(z) for f in feats for z in (f[0], f[2])})
    Pr = sorted({rd(z) for d in r_dims for z in (d["a"], d["b"])} | {0.0}
                | {rd(z) for f in feats for z in (f[1], f[3])})
    ix, ir = {p: i for i, p in enumerate(Px)}, {p: len(Px) + i for i, p in enumerate(Pr)}
    n = len(Px) + len(Pr)
    rows, rhs = [], []

    def add(coef: dict, b: float, w: float):
        r = np.zeros(n)
        for k, c in coef.items():
            r[k] += c * w
        rows.append(r), rhs.append(b * w)

    ratios = []
    for dims, idx in ((x_dims, ix), (r_dims, ir)):
        for d in dims:
            v, a, b = val(d), rd(d["a"]), rd(d["b"])
            add({idx[b]: 1, idx[a]: -1}, v, 1e4)
            if abs(b - a) > 1e-6:
                ratios.append(v / (b - a))
    s = float(np.median(ratios)) if ratios else 1.0
    add({ix[rd(x0)]: 1}, 0.0, 1e6)
    add({ir[0.0]: 1}, 0.0, 1e6)
    for P, idx in ((Px, ix), (Pr, ir)):                  # giữ tỷ lệ chung (yếu, theo tỷ lệ tương đối)
        for p, q in zip(P, P[1:]):
            L = q - p
            add({idx[q]: 1 / L, idx[p]: -1 / L}, s, 1.0)
    for x1, r1, x2, r2 in feats:                         # giữ góc cạnh xiên: ΔX·Δr - ΔR·Δx = 0
        dx, dr = x2 - x1, r2 - r1
        L2 = dx * dx + dr * dr
        if abs(dx) < 1e-6 or abs(dr) < 1e-6 or L2 < 1e-12:
            continue
        a, b, c, e = ix[rd(x1)], ix[rd(x2)], ir[rd(r1)], ir[rd(r2)]
        add({b: dr / L2, a: -dr / L2, e: -dx / L2, c: dx / L2}, 0.0, 30.0)
    sol, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    X, R = sol[:len(Px)], sol[len(Px):]
    if (np.diff(X) <= 0).any() or (np.diff(R) <= 0).any():
        raise ValueError("fold")
    return np.array(Px), X, np.array(Pr), R


def build_warp(t: Template, vals: dict) -> Warp:
    v = t.spec["view"]
    ay = v["axis_y"]
    r_dims = [{**d, "a": d["a"] - ay, "b": d["b"] - ay} for d in v["r_dims"]]
    try:
        Px, Xx, Pr, Xr = solve_warp(v["x_dims"], r_dims, vals, v["x0"], _outline_features(t))
    except (ValueError, np.linalg.LinAlgError, KeyError):
        # kích thước yêu cầu quá khác bản gốc (hình gấp ngược) -> cách cũ: co giãn x, r độc lập
        Px, Xx = solve_breakpoints(v["x_dims"], vals, fixed_zero=v["x0"])
        Xx = Xx - Xx[list(Px).index(round(v["x0"], 4))]
        Pr, Xr = solve_breakpoints(r_dims, vals, fixed_zero=0.0)
    return Warp(tuple(v["box"]), v["x0"], ay, Px, Xx, Pr, Xr)


# ----------------------------------------------------------------- vẽ lại
def _sub_labels(t: Template, text: str, vals: dict) -> str | None:
    for lab in t.spec.get("labels", []):
        if text == lab["text"]:
            return re.sub(r"\{([^}]+)\}",
                          lambda m: _fmt(float(eval(m.group(1), {"__builtins__": {}}, dict(vals)))),
                          lab["to"])
    return None


def _shift_tolerances(msp, replaced):
    """Chữ ký hiệu ("L") thay bằng số ("80") dài hơn => dời dung sai đứng ngay sau nó
    ("+0.26", "+0.16") theo phần dài thêm, tránh chữ đè nhau."""
    from .ocr import _text_width
    texts = list(msp.query("TEXT"))
    done = {id(e) for e, *_ in replaced}
    for e, old, old_wf in replaced:
        h = e.dxf.height
        a = math.radians(e.dxf.rotation)
        d, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
        ow = (_text_width(old, h) or h * len(old) * 0.7) * old_wf
        nw = (_text_width(e.dxf.text, h) or h * len(e.dxf.text) * 0.7) * e.dxf.get("width", 1.0)
        delta = nw - ow
        if abs(delta) < 1e-6:
            continue
        p0 = e.dxf.insert
        for t2 in texts:
            if id(t2) in done or abs((t2.dxf.rotation - e.dxf.rotation + 180) % 360 - 180) > 1:
                continue
            rx, ry = t2.dxf.insert.x - p0.x, t2.dxf.insert.y - p0.y
            along, across = rx * d[0] + ry * d[1], rx * n[0] + ry * n[1]
            if ow - 0.6 * h <= along <= ow + 3 * h and -1.3 * h <= across <= 1.6 * h:
                i = t2.dxf.insert
                t2.dxf.insert = (i.x + delta * d[0], i.y + delta * d[1], 0)


def _fill(fmt: str, vals: dict) -> str:
    return fmt.format(**{k: (_fmt(v) if isinstance(v, float) else v) for k, v in vals.items()})


# ----------------------------------------------------------------- giữ dạng nét phụ khi co giãn
# Bản gốc thường vẽ không theo tỷ lệ; vẽ lại 1:1 thì x và r co giãn rất khác nhau. Các nét phụ
# không được méo theo: gạch mặt cắt vẽ lại 45° đều, vòng tròn vẫn tròn, ký hiệu (nhám, mũi tên)
# giữ nguyên cỡ, chữ thông số không đè nhau.
def _seg(e):
    return (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)


def _part_extent(feats) -> tuple[float, float, float] | None:
    """(xmin, xmax, rmax) của chi tiết trên bản gốc, từ cạnh OUTLINE."""
    if not feats:
        return None
    xs = [z for f in feats for z in (f[0], f[2])]
    return min(xs), max(xs), max(z for f in feats for z in (f[1], f[3]))


def _take_hatch(msp, W: "Warp", ext) -> list[dict]:
    """Lấy (và xoá) các đường gạch mặt cắt THIN 45°/135° nằm trong chi tiết. Trả về họ đường theo hướng."""
    if ext is None:
        return []
    xmin, xmax, rmax = ext
    fams: dict[int, dict] = {}
    for e in list(msp.query('LINE[layer=="THIN"]')):
        p, q = _seg(e)
        if not (W.inside(*p) and W.inside(*q)):
            continue
        ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 180
        fam = 45 if abs(ang - 45) < 1.5 else 135 if abs(ang - 135) < 1.5 else None
        if fam is None or math.dist(p, q) < 0.2:
            continue
        if not all(xmin - 0.05 <= z[0] <= xmax + 0.05 and abs(z[1] - W.axis_y) <= rmax + 0.05 for z in (p, q)):
            continue
        th = math.radians(fam)
        d, n = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
        c = p[0] * n[0] + p[1] * n[1]
        t0, t1 = sorted((p[0] * d[0] + p[1] * d[1], q[0] * d[0] + q[1] * d[1]))
        f = fams.setdefault(fam, {"angle": fam, "d": d, "n": n, "lines": [], "pts": [],
                                  "attr": {k: e.dxf.get(k) for k in ("layer", "color", "linetype",
                                                                      "lineweight", "ltscale")
                                           if e.dxf.hasattr(k)}})
        f["lines"].append((c, t0, t1))
        f["pts"] += [p, q]
        msp.delete_entity(e)
    out = []
    for f in fams.values():
        if len(f["lines"]) < 3:
            continue
        f["lines"].sort()
        cs = np.array([c for c, _, _ in f["lines"]])
        gaps = np.diff(cs)
        gaps = gaps[gaps > 0.05]
        f["sp"] = float(np.median(gaps)) if len(gaps) else 1.0
        out.append(f)
    return out


def _in_hatch(f: dict, p) -> bool:
    """Điểm p (bản gốc) nằm giữa 2 đường gạch kề nhau => thuộc vùng vật liệu bị cắt."""
    c = p[0] * f["n"][0] + p[1] * f["n"][1]
    t = p[0] * f["d"][0] + p[1] * f["d"][1]
    lo = hi = False
    for cl, t0, t1 in f["lines"]:
        if abs(cl - c) > 1.6 * f["sp"] or not (t0 - 0.02 <= t <= t1 + 0.02):
            continue
        lo |= cl <= c
        hi |= cl >= c
    return lo and hi


def _redraw_hatch(msp, W: "Warp", fams: list[dict]):
    """Vẽ lại gạch mặt cắt trên bản mới: cùng góc, cách đều, cắt đúng theo cạnh OUTLINE đã co giãn."""
    if not fams:
        return
    bounds = []
    for e in msp.query('LINE[layer=="OUTLINE"]'):
        p, q = _seg(e)
        if W.inside(*p) or W.inside(*q):
            bounds.append((p, q))
    for e in msp.query('LWPOLYLINE[layer=="OUTLINE"]'):
        pts = [(z[0], z[1]) for z in e.get_points("xy")]
        bounds += [(a, b) for a, b in zip(pts, pts[1:]) if W.inside(*a) or W.inside(*b)]
    b = W.box
    bounds.append(((b[0], W.axis_y), (b[2], W.axis_y)))          # tâm trục (chỗ không có lỗ)
    for f in fams:
        d, n = f["d"], f["n"]
        newpts = [W(*p) for p in f["pts"]]
        k = float(np.median([W.scale_at(*p) for p in f["pts"]]))
        sp = f["sp"] * k
        cs = [p[0] * n[0] + p[1] * n[1] for p in newpts]
        ts = [p[0] * d[0] + p[1] * d[1] for p in newpts]
        cmin, cmax = min(cs), max(cs)
        tlo, thi = min(ts) - 1, max(ts) + 1
        for i in range(int((cmax - cmin) / sp) + 2):
            c = cmin + (i + 0.5) * sp
            if c > cmax:
                break
            o = (c * n[0], c * n[1])
            hits = []
            for p, q in bounds:                               # giao đường c với các cạnh
                ex, ey = q[0] - p[0], q[1] - p[1]
                den = d[0] * ey - d[1] * ex
                if abs(den) < 1e-12:
                    continue
                wx, wy = p[0] - o[0], p[1] - o[1]
                tt = (wx * ey - wy * ex) / den
                u = (wx * d[1] - wy * d[0]) / den
                if -1e-9 <= u <= 1 + 1e-9 and tlo <= tt <= thi:
                    hits.append(tt)
            hits.sort()
            for t0, t1 in zip(hits, hits[1:]):
                if t1 - t0 < 1e-3:
                    continue
                tm = (t0 + t1) / 2
                m = (o[0] + tm * d[0], o[1] + tm * d[1])
                if W.inside(*m) and _in_hatch(f, W.inverse(*m)):
                    msp.add_line((o[0] + t0 * d[0], o[1] + t0 * d[1]), (o[0] + t1 * d[0], o[1] + t1 * d[1]),
                                 dxfattribs=f["attr"])


def _fit_circle(P: np.ndarray):
    A = np.c_[2 * P, np.ones(len(P))]
    s, *_ = np.linalg.lstsq(A, (P ** 2).sum(1), rcond=None)
    cx, cy = s[0], s[1]
    r = math.sqrt(max(s[2] + cx * cx + cy * cy, 0.0))
    return cx, cy, r, float(np.abs(np.hypot(P[:, 0] - cx, P[:, 1] - cy) - r).max())


def _components(segs: list, tol: float) -> list[list[int]]:
    """Gom đoạn thẳng thành nhóm nối nhau (đầu mút cách nhau < tol)."""
    n = len(segs)
    par = list(range(n))

    def root(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    ends = np.array([[*p, *q] for p, q in segs]).reshape(n, 2, 2) if n else np.zeros((0, 2, 2))
    for i in range(n):
        if i + 1 >= n:
            break
        dd = np.min(np.linalg.norm(ends[i + 1:, :, None, :] - ends[i][None, None, :, :], axis=-1), axis=(1, 2))
        for j in np.nonzero(dd < tol)[0]:
            par[root(i)] = root(i + 1 + int(j))
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def _rigid_moves(msp, W: "Warp", ext, feats) -> dict:
    """Nét phụ không được méo: id(entity) -> hàm biến điểm.
    - Vòng tròn chia đoạn (kể cả nét đứt): dời tâm theo W, phóng đều (trong chi tiết) hoặc giữ cỡ (ký hiệu).
    - Ký hiệu nhỏ có nét xiên (nhám bề mặt...): giữ nguyên cỡ, dời theo điểm bám gần chi tiết nhất.
    - Mũi tên (HATCH nhỏ): giữ nguyên cỡ, dời theo đỉnh nhọn."""
    moves: dict = {}
    ents = [e for e in msp.query('LINE[layer=="THIN"]') if all(W.inside(*z) for z in _seg(e))]
    segs = [_seg(e) for e in ents]
    ay = W.axis_y
    in_part = (lambda x, y, m=0.0: ext is not None and ext[0] - m <= x <= ext[1] + m
               and abs(y - ay) <= ext[2] + m)

    def similar(c, k):
        nc = W(*c)
        return lambda x, y: (nc[0] + k * (x - c[0]), nc[1] + k * (y - c[1]))

    # 1) vòng tròn
    short = [i for i, s in enumerate(segs) if math.dist(*s) < 1.5]
    circles = []
    for g in _components([segs[i] for i in short], 0.8):
        if len(g) < 8:
            continue
        P = np.array([z for i in g for z in segs[short[i]]])
        cx, cy, r, res = _fit_circle(P)
        if r > 0.3 and res < max(0.02, 0.01 * r):
            if not any(math.hypot(cx - c[0], cy - c[1]) < 0.05 and abs(r - c[2]) < 0.05 for c in circles):
                circles.append((cx, cy, r))
    for cx, cy, r in circles:
        if in_part(cx, cy, r):
            sx, sy = W.scale_xy(cx, cy)
            k = math.sqrt(max(sx * sy, 1e-12))
        else:
            k = 1.0
        f = similar((cx, cy), k)
        for i in short:
            if id(ents[i]) not in moves and all(abs(math.hypot(z[0] - cx, z[1] - cy) - r) < 0.03 for z in segs[i]):
                moves[id(ents[i])] = f
    # 2) ký hiệu nhỏ có nét xiên
    rest = [i for i in range(len(ents)) if id(ents[i]) not in moves]

    def dist_part(z):
        r = abs(z[1] - ay)
        best = math.inf
        for x1, r1, x2, r2 in feats:
            ex, er = x2 - x1, r2 - r1
            L2 = ex * ex + er * er
            u = 0.0 if L2 < 1e-12 else min(1.0, max(0.0, ((z[0] - x1) * ex + (r - r1) * er) / L2))
            best = min(best, math.hypot(z[0] - x1 - u * ex, r - r1 - u * er))
        return best
    for g in _components([segs[i] for i in rest], 0.05):
        P = np.array([z for i in g for z in segs[rest[i]]])
        diag = float(np.linalg.norm(P.max(0) - P.min(0)))
        is_sl = [abs(segs[rest[i]][1][0] - segs[rest[i]][0][0]) > 1e-3
                 and abs(segs[rest[i]][1][1] - segs[rest[i]][0][1]) > 1e-3 for i in g]
        # ký hiệu: có nét xiên, nét ngang/dọc đều ngắn (khác cụm đường kích thước: nét dài thẳng trục)
        long_axis = any(not sl and math.dist(*segs[rest[i]]) > 4 for i, sl in zip(g, is_sl))
        if len(g) < 2 or diag > 10 or not any(is_sl) or long_axis or all(in_part(*z) for z in P):
            continue
        anchor = min((tuple(z) for z in P), key=dist_part) if feats else tuple(P.mean(0))
        f = similar(anchor, 1.0)
        for i in g:
            moves[id(ents[rest[i]])] = f
    # 3) mũi tên
    for e in msp.query("HATCH"):
        for path in e.paths:
            vs = [(v[0], v[1]) for v in getattr(path, "vertices", [])]
            if not (3 <= len(vs) <= 5) or not any(W.inside(*v) for v in vs):
                continue
            P = np.array(vs)
            if float(np.linalg.norm(P.max(0) - P.min(0))) > 6:
                continue

            def ang(i):
                a, b, c = P[i - 1], P[i], P[(i + 1) % len(P)]
                u, w = a - b, c - b
                return math.acos(max(-1, min(1, float(u @ w) / (np.linalg.norm(u) * np.linalg.norm(w) + 1e-12))))
            tip = tuple(P[min(range(len(P)), key=ang)])
            moves[id(e)] = similar(tip, 1.0)
    return moves


def _text_box(e) -> tuple[float, float, float, float]:
    """Khung bao (xmin, ymin, xmax, ymax) của TEXT (chân trái, xoay bất kỳ)."""
    from .ocr import _text_width
    h = e.dxf.height
    w = (_text_width(e.dxf.text, h) or h * len(e.dxf.text) * 0.7) * e.dxf.get("width", 1.0)
    a = math.radians(e.dxf.rotation)
    i = e.dxf.insert
    pts = [(i.x + u * math.cos(a) - v * math.sin(a), i.y + u * math.sin(a) + v * math.cos(a))
           for u, v in ((0, 0), (w, 0), (w, h), (0, h))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b, m=0.0) -> bool:
    return a[0] < b[2] + m and b[0] < a[2] + m and a[1] < b[3] + m and b[1] < a[3] + m


def _unclutter_texts(msp, W: "Warp", part_box):
    """Chữ thông số (TEXT_PARAM) đè lên chữ khác: trượt dọc theo hướng chữ (vẫn nằm trên đường kích thước)
    tới chỗ trống gần nhất, không chui vào hình chi tiết."""
    texts = [e for e in msp.query("TEXT") if W.inside(e.dxf.insert.x, e.dxf.insert.y)]
    for e in [t for t in texts if t.dxf.layer == "TEXT_PARAM"]:
        box = _text_box(e)
        others = [o for o in texts if o is not e]
        if not any(_overlap(box, _text_box(o)) for o in others):
            continue
        a = math.radians(e.dxf.rotation)
        d = (math.cos(a), math.sin(a))
        h = e.dxf.height
        i0 = e.dxf.insert
        start_in_part = part_box is not None and _overlap(box, part_box)
        best = None
        for k in range(1, 41):                                   # thử dời 0.25h, 0.5h, ... cả 2 phía
            for sgn in (1, -1):
                s = sgn * k * 0.25 * h
                e.dxf.insert = (i0.x + s * d[0], i0.y + s * d[1], 0)
                nb = _text_box(e)
                if any(_overlap(nb, _text_box(o), 0.15 * h) for o in others):
                    continue
                if part_box is not None and not start_in_part and _overlap(nb, part_box):
                    continue
                best = s
                break
            if best is not None:
                break
        s = best or 0.0
        e.dxf.insert = (i0.x + s * d[0], i0.y + s * d[1], 0)


def generate(t: Template, vals: dict, out: str | Path) -> dict:
    """Vẽ lại bản vẽ theo bộ tham số vals -> DXF. Trả về thông tin kiểm tra."""
    if t.spec.get("kind") == "generator":            # vẽ mới hoàn toàn (bản gốc là ảnh scan)
        import datetime

        from .gen import generate_family
        nm = t.spec.get("naming", {})
        v2 = dict(vals)
        v2["_parts_name"] = _fill(nm.get("parts_name", t.title), vals)
        v2["_drawing_no"] = _fill(nm.get("drawing_no", t.spec.get("drawing", {}).get("drawing_no", "")),
                                  vals)
        v2["_info"] = _fill(nm["info"], vals) if nm.get("info") else ""
        t.spec.setdefault("drawing", {}).setdefault("date", datetime.date.today().isoformat())
        return generate_family(t, v2, out)
    import ezdxf

    warp = build_warp(t, vals)
    doc = ezdxf.readfile(t.base_dxf)
    msp = doc.modelspace()
    W = warp
    feats = _outline_features(t)
    ext = _part_extent(feats)
    hatch = _take_hatch(msp, W, ext)                 # gạch mặt cắt: vẽ lại sau khi co giãn
    moves = _rigid_moves(msp, W, ext, feats)         # vòng tròn, ký hiệu, mũi tên: không méo
    replaced: list = []
    for e in msp:
        typ = e.dxftype()
        f = moves.get(id(e))
        if f is not None and typ == "LINE":
            s, en = e.dxf.start, e.dxf.end
            e.dxf.start = (*f(s.x, s.y), 0)
            e.dxf.end = (*f(en.x, en.y), 0)
        elif f is not None and typ == "HATCH":
            for path in e.paths:
                if hasattr(path, "vertices"):
                    path.vertices = [(*f(v[0], v[1]), *v[2:]) for v in path.vertices]
        elif typ == "LINE":
            s, en = e.dxf.start, e.dxf.end
            if W.inside(s.x, s.y) or W.inside(en.x, en.y):
                e.dxf.start = (*W(s.x, s.y), 0)
                e.dxf.end = (*W(en.x, en.y), 0)
        elif typ in ("CIRCLE", "ARC"):
            c = e.dxf.center
            if W.inside(c.x, c.y):
                k = W.scale_at(c.x, c.y)
                e.dxf.center = (*W(c.x, c.y), 0)
                e.dxf.radius = e.dxf.radius * k
        elif typ == "LWPOLYLINE":
            pts = list(e.get_points("xyseb"))
            if any(W.inside(p[0], p[1]) for p in pts):
                e.set_points([(*W(p[0], p[1]), *p[2:]) for p in pts], format="xyseb")
        elif typ == "HATCH":
            for path in e.paths:
                if hasattr(path, "vertices") and any(W.inside(v[0], v[1]) for v in path.vertices):
                    path.vertices = [(*W(v[0], v[1]), *v[2:]) for v in path.vertices]
        elif typ == "TEXT":
            i = e.dxf.insert
            if W.inside(i.x, i.y):
                e.dxf.insert = (*W(i.x, i.y), 0)
                new = _sub_labels(t, e.dxf.text, vals)
                if new is not None:
                    replaced.append((e, e.dxf.text, e.dxf.get("width", 1.0)))
                    e.dxf.text = new
                    e.dxf.width = 1.0
                    e.dxf.layer = "TEXT_PARAM"
    _shift_tolerances(msp, replaced)
    _redraw_hatch(msp, W, hatch)
    if ext is not None:
        lo, hi = W(ext[0], W.axis_y + ext[2]), W(ext[1], W.axis_y - ext[2])
        part_box = (min(lo[0], hi[0]), min(lo[1], hi[1]), max(lo[0], hi[0]), max(lo[1], hi[1]))
    else:
        part_box = None
    _unclutter_texts(msp, W, part_box)
    # thay chữ ngoài hình chiếu (khung tên...)
    for lab in t.spec.get("title_labels", []):
        for e in msp.query("TEXT"):
            if e.dxf.text == lab["text"]:
                e.dxf.text = re.sub(r"\{([^}]+)\}", lambda m: _fmt(float(
                    eval(m.group(1), {"__builtins__": {}}, dict(vals)))), lab["to"])
                e.dxf.width = min(e.dxf.get("width", 1.0), 0.9)
    if "TEXT_PARAM" not in doc.layers:
        doc.layers.add("TEXT_PARAM", color=6)
    # ghi chú tham số
    note = "  ".join(f"{k}={_fmt(v)}" for k, v in vals.items()
                     if not t.params.get(k, {}).get("derived"))
    nx, ny = t.spec.get("param_note_at", [warp.box[0], warp.box[1] - 4])
    msp.add_text(f"PARAMETERS: {note}", height=2.5,
                 dxfattribs={"insert": (nx, ny), "layer": "TEXT_PARAM", "style": "PDF2CAD"})

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    from .dxf_writer import DXF_VERSION
    from .layers import LAYERS_OFF
    # nét chữ gốc (TEXT_GLYPH, tắt sẵn) trong base.dxf chiếm ~95% dung lượng: bỏ khi xuất bản vẽ mới
    for name in LAYERS_OFF:
        for e in list(msp.query(f'*[layer=="{name}"]')):
            msp.delete_entity(e)
    doc.dxfversion = DXF_VERSION   # base.dxf có thể là bản CAD mới hơn -> hạ về AutoCAD 2007 khi lưu
    doc.saveas(out)
    # kiểm tra: đo lại từng kích thước sau khi co giãn
    check = []
    for d in t.spec["view"]["x_dims"]:
        want = float(eval(d["expr"], {"__builtins__": {}}, dict(vals))) if isinstance(d["expr"], str) \
            else float(d["expr"])
        got = W(d["b"], t.spec["view"]["axis_y"])[0] - W(d["a"], t.spec["view"]["axis_y"])[0]
        check.append((d.get("name", d["expr"]), want, round(got, 4)))
    for d in t.spec["view"]["r_dims"]:
        want = float(eval(d["expr"], {"__builtins__": {}}, dict(vals)))
        ya = W(t.spec["view"]["x0"] + 1e-3, d["b"])[1] - t.spec["view"]["axis_y"]
        check.append((d.get("name", d["expr"]), want, round(2 * ya, 4)))
    return {"output": str(out), "check": check}


# ----------------------------------------------------------------- hiểu câu chat
_NUM = r"(\d+(?:[.,]\d+)?)"


def parse_request(t: Template, msg: str) -> dict:
    """Hiểu yêu cầu kiểu: 'vẽ DSJC-B No.16 L=80 P 15', 'số 13, dài 70, d1=1.8', 'H 20'."""
    req: dict[str, float] = {}
    m = msg.replace("，", ",")
    key = keys_of(t)[0]
    # khoá phân biệt hoa/thường (D ≠ d); riêng "No" và từ tiếng Việt thì không
    kp = "(?i:No)" if key.lower() == "no" else re.escape(key)
    km = re.search(rf"(?:(?<![A-Za-z0-9]){kp}\.?|(?i:\bsố|\bsize|\bcỡ|\bloại)|Φ|Ø)\s*[:=]?\s*{_NUM}", m)
    if km:
        req[key] = float(km.group(1).replace(",", "."))
    lm = re.search(rf"\b(?:dài|chiều dài|length)\s*[:=]?\s*{_NUM}", m, re.I)
    if lm:
        req["L"] = float(lm.group(1).replace(",", "."))
    names = sorted((p for p in t.params if p != key and not t.params[p].get("derived")
                    and not t.params[p].get("info")),
                   key=len, reverse=True)
    alias = {a: p for p, d in t.params.items() for a in d.get("aliases", [])}
    pat = "|".join(re.escape(x) for x in names + list(alias))
    for mm in re.finditer(rf"(?<![A-Za-z0-9])({pat})\s*(?:=|:|\s)?\s*{_NUM}", m):
        name = alias.get(mm.group(1), mm.group(1))
        req[name] = float(mm.group(2).replace(",", "."))
    return req


def file_stem(t: Template, vals: dict) -> str:
    ks = "_".join(f"{k}{_fmt(float(vals[k]))}" for k in keys_of(t))
    return f"{t.name}_{ks}_L{_fmt(float(vals.get('L', 0)))}"


def chat(t: Template, msg: str, out_dir: str | Path) -> dict:
    req = parse_request(t, msg)
    vals, notes, errors = resolve_params(t, req)
    res = {"request": req, "params": vals, "notes": notes, "errors": errors, "output": None}
    if errors:
        return res
    stem = file_stem(t, vals)
    g = generate(t, vals, Path(out_dir) / f"{stem}.dxf")
    res.update(g)
    return res
