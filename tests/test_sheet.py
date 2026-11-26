"""Khung bản vẽ chuẩn SPC (sheet.py) + font chuẩn (cadstd.py) + bộ đọc mẫu TopSolid (tools/dft_import.py)."""
import importlib.util
import json
import zipfile
from pathlib import Path

import ezdxf
import pytest

from pdf2cad import cadstd, sheet

ROOT = Path(__file__).resolve().parents[1]


def _texts(doc, layer=None):
    return [e for e in doc.modelspace().query("TEXT") if layer is None or e.dxf.layer == layer]


@pytest.mark.parametrize("name", sheet.names())
def test_sheets_complete(name):
    sh = sheet.load(name)
    bx0, by0, bx1, by1 = sh["border"]
    assert 0 < bx0 < bx1 <= sh["size"][0] and 0 < by0 < by1 <= sh["size"][1]
    assert {"title", "drawing_no", "date", "scale", "material", "notes"} <= set(sh["slots"])
    for key, s in sh["slots"].items():
        if "cell" in s:
            x0, y0, x1, y1 = s["cell"]
            assert bx0 <= x0 < x1 <= bx1 and by0 <= y0 < y1 <= by1, key
    labels = {t["s"].strip().lower() for t in sh["texts"]}
    assert "drawing number" in labels and "scale" in labels
    # mẫu không còn chữ nháp / đường dẫn file TopSolid
    assert not any(":\\" in t["s"] for t in sh["texts"])


def test_draw_fills_title_block(tmp_path):
    doc = ezdxf.new("R2007")
    sheet.draw(doc, 2.0, {"title": ["SUPPORT PILLAR", "SPLB-B"], "drawing_no": "DR-TE-1 (D30 L120)",
                          "material": "S45C", "scale": "1:2", "date": "2026/11/26", "drawn": "Lê Hiệp",
                          "notes": ["MUST BE FREE OF VISIBLE SCRATCHES"]})
    p = tmp_path / "s.dxf"
    doc.saveas(p)
    doc = ezdxf.readfile(p)
    vals = {e.dxf.text: e for e in _texts(doc, "TITLE")}
    assert {"SUPPORT PILLAR", "SPLB-B", "DR-TE-1 (D30 L120)", "S45C", "1:2", "2026/11/26", "Lê Hiệp",
            "･MUST BE FREE OF VISIBLE SCRATCHES"} <= set(vals)
    # chữ mẫu của TopSolid đã được thay
    assert not {"DR-TE-", "Vat Lieu", "C.MINH", "2016/01/19", "A4-SPC"} & {e.dxf.text for e in _texts(doc)}
    # giá trị canh giữa ô, ô Drawing Number của A4H: x 217.6 .. 275 (giấy) -> nhân k = 2
    dn = vals["DR-TE-1 (D30 L120)"]
    assert dn.dxf.halign == 1 and abs(dn.dxf.align_point.x - (217.5993 + 275) / 2 * 2) < 1e-3
    assert vals["Lê Hiệp"].dxf.style == cadstd.STYLE              # tiếng Việt: FG-SPC
    assert doc.styles.get(cadstd.STYLE).dxf.font == cadstd.FONT_FILE
    # nét khung có độ dày in
    assert {e.dxf.lineweight for e in doc.modelspace().query('LINE[layer=="FRAME"]')} >= {18, 50}


def test_fit_avoids_title_block():
    sh = sheet.load("A4H-SPC")
    k, box = sheet.fit(sh, 120 + 30, 30, [0.5, 1, 2, 5], extra=(48, 37))
    assert k == 1
    x0, y0, x1, y1 = box
    assert y0 > 42.5 and y1 < 176 and x0 >= 10 and x1 <= 275      # trên khung tên, dưới bảng sửa đổi
    for ox0, oy0, ox1, oy1 in sh["obstacles"]:
        assert not (ox0 < x1 and ox1 > x0 and oy0 < y1 and oy1 > y0)
    k2, _ = sheet.fit(sh, 600, 120, [0.5, 1, 2, 2.5, 4, 5, 10])
    assert k2 > 2


def test_style_for():
    assert cadstd.style_for("Lê Hiệp") == ("Lê Hiệp", cadstd.STYLE)
    assert cadstd.style_for("2000～4000") == ("2000～4000", cadstd.STYLE_J)       # ký tự toàn khổ: NT-M
    assert cadstd.style_for("Đã kiểm ～ tra")[1] == cadstd.STYLE                    # có tiếng Việt: FG-SPC
    assert cadstd.std_text("C0.1～0.2 ･ ｍ") == "C0.1~0.2 · m"
    assert cadstd.style_for("SAIGON PRECISION", proportional=True)[1] == cadstd.STYLE_P
    # ký hiệu đường kính: font chuẩn không có Ø/∅ -> Φ (FG-SPC)
    assert cadstd.style_for("%%c12 %%p0.1") == ("Φ12 %%p0.1", cadstd.STYLE)
    assert cadstd.style_for("Ø8~%%C25") == ("Φ8~Φ25", cadstd.STYLE)
    assert cadstd.undia("Φd1") == "%%cd1"
    assert cadstd.text_width("HHHH", 1.0) == pytest.approx(4 * 0.5 / cadstd.CAP, rel=0.08)


def test_generator_uses_standard_sheet(tmp_path):
    from pdf2cad.template import TEMPLATES_DIR, Template, generate, resolve_params
    if not (TEMPLATES_DIR / "splb-b" / "template.json").exists():
        pytest.skip("chưa có mẫu")
    t = Template.load(TEMPLATES_DIR / "splb-b")
    v, _, err = resolve_params(t, {"D": 30, "L": 120})
    g = generate(t, v, tmp_path / "a.dxf")
    doc = ezdxf.readfile(g["output"])
    texts = {e.dxf.text for e in _texts(doc)}
    assert {"Drawing Number", "Parts Name", "GENERAL TOLERANCE", "SAIGON PRECISION Co.,Ltd."} <= texts
    assert g["scale"] in texts
    assert {e.dxf.style for e in _texts(doc)} <= {cadstd.STYLE, cadstd.STYLE_P, cadstd.STYLE_J}
    assert doc.header["$TEXTSTYLE"] == cadstd.STYLE


@pytest.mark.skipif(not (ROOT / "docs" / "template.zip").exists(), reason="không có docs/template.zip")
def test_dft_import_matches_bundled(tmp_path):
    spec = importlib.util.spec_from_file_location("dft_import", ROOT / "tools" / "dft_import.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with zipfile.ZipFile(ROOT / "docs" / "template.zip") as z:
        data = z.read(next(n for n in z.namelist() if n.endswith("A4H-SPC.dft")))
    res = mod.clean(mod.convert("A4H-SPC", data))
    assert json.loads(json.dumps(res, ensure_ascii=False)) == sheet.load("A4H-SPC")
