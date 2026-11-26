"""Mẫu tham số DSJC-B: quy tắc bảng/NOTE và vẽ lại đúng kích thước."""
from pathlib import Path

import ezdxf
import pytest

from pdf2cad.template import TEMPLATES_DIR, Template, chat, parse_request, resolve_params

TPL = TEMPLATES_DIR / "dsjc-b"
pytestmark = pytest.mark.skipif(not (TPL / "template.json").exists(), reason="chưa có mẫu dsjc-b")


@pytest.fixture(scope="module")
def t():
    return Template.load(TPL)


def test_parse(t):
    assert parse_request(t, "vẽ DSJC-B No.16 L=80 P=15") == {"No": 16, "L": 80, "P": 15}
    assert parse_request(t, "số 13 dài 70, d1=1.8") == {"No": 13, "L": 70, "d1": 1.8}


def test_rules(t):
    v, notes, err = resolve_params(t, {"No": 8, "L": 40})
    assert not err and v["S"] == 15 and v["H"] == 11 and v["P"] == 7
    v, notes, err = resolve_params(t, {"No": 16, "L": 50})
    assert v["S"] == 24
    v, notes, err = resolve_params(t, {"No": 20})
    assert any("Thiếu L" in e for e in err)
    v, notes, err = resolve_params(t, {"No": 25, "L": 80, "H": 30})
    assert not err and any("ngoài bảng" in n for n in notes)
    v, notes, err = resolve_params(t, {"No": 8, "L": 60, "d1": 5})
    assert err                                  # d1 > d2: vi phạm hình học


def test_generate_exact(t, tmp_path):
    r = chat(t, "No.16 L=80 P=15", tmp_path)
    assert not r["errors"] and Path(r["output"]).exists()
    for name, want, got in r["check"]:
        assert abs(want - got) < 1e-3, name
    msp = ezdxf.readfile(r["output"]).modelspace()
    texts = {e.dxf.text for e in msp.query("TEXT")}
    assert {"80", "36%%p0.2", "(37)", "%%c19", "%%c15", "M5"} <= texts
    assert "DSJC-B No.16-L80" in texts
    # chiều dài tổng đo trên đường bao ngoài = 80
    xs = [p.x for e in msp.query('LINE[layer=="OUTLINE"]') for p in (e.dxf.start, e.dxf.end)
          if 60 < p.x < 260 and 120 < p.y < 165]
    assert abs((max(xs) - min(xs)) - 80) < 0.01


@pytest.mark.parametrize("req", [{"No": 8, "L": 40, "S": 15}, {"No": 16, "L": 80, "S": 36, "P": 15},
                                 {"No": 25, "L": 100}])
def test_generate_keeps_shape(t, tmp_path, req):
    """L và Φ co giãn khác tỷ lệ nhau: vát mép, mũi khoan, cung lượn phải giữ nguyên góc (không méo)."""
    import math
    v = t.spec["view"]
    b = v["box"]
    inside = lambda p: b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]

    def outline(path):
        return [((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y))
                for e in ezdxf.readfile(path).modelspace().query('LINE[layer=="OUTLINE"]')]

    ang = lambda p, q: math.degrees(math.atan2(abs(q[1] - p[1]), abs(q[0] - p[0])))
    base = outline(t.base_dxf)
    vals, _, err = resolve_params(t, req)
    assert not err
    from pdf2cad.template import generate
    out = outline(generate(t, vals, tmp_path / "x.dxf")["output"])
    slanted = [i for i, (p, q) in enumerate(base) if inside(p) and inside(q)
               and abs(q[0] - p[0]) > 1e-3 and abs(q[1] - p[1]) > 1e-3]
    assert slanted
    for i in slanted:
        assert abs(ang(*out[i]) - ang(*base[i])) < 1.0, (i, ang(*base[i]), ang(*out[i]))
    xs = [z[0] for i, s in enumerate(out) if inside(base[i][0]) and inside(base[i][1]) for z in s]
    ys = [z[1] for i, s in enumerate(out) if inside(base[i][0]) and inside(base[i][1]) for z in s]
    assert abs(max(xs) - min(xs) - vals["L"]) < 1e-3
    assert abs(max(ys) - min(ys) - vals["H"]) < 1e-3


def test_generate_keeps_annotations(t, tmp_path):
    """Đoạn Φd2 bị nén mạnh (No.25 L=60): gạch mặt cắt vẫn 45°, vòng tròn chi tiết B vẫn tròn,
    chữ thông số không đè nhau."""
    import math

    import numpy as np

    from pdf2cad.template import _fit_circle, _overlap, _text_box, generate
    v = t.spec["view"]
    b, ay = v["box"], v["axis_y"]
    vals, _, err = resolve_params(t, {"No": 25, "L": 60})
    assert not err
    msp = ezdxf.readfile(generate(t, vals, tmp_path / "x.dxf")["output"]).modelspace()
    x0 = v["x0"]
    hatch = []
    for e in msp.query('LINE[layer=="THIN"]'):
        p, q = (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
        inside = all(x0 - 0.01 <= z[0] <= x0 + vals["L"] + 0.01 and 0 <= z[1] - ay <= vals["H"] / 2 + 0.01
                     for z in (p, q))
        if inside and abs(q[0] - p[0]) > 1e-3 and abs(q[1] - p[1]) > 1e-3 and math.dist(p, q) > 1:
            hatch.append(math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 180)
    assert len(hatch) > 20
    # gạch mặt cắt gốc là họ 45°; nét xiên khác hợp lệ (mũi khoan ~120°, nét gần đứng 88-92°) không xét
    bad = [a for a in hatch if 30 < a < 85 and abs(a - 45) >= 1]
    assert not bad, sorted(bad)
    # vòng tròn nét đứt quanh chi tiết B: các đoạn ngắn quanh đầu chi tiết phải nằm trên 1 đường tròn
    from pdf2cad.template import _components
    segs = [((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)) for e in msp.query('LINE[layer=="THIN"]')
            if math.dist((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)) < 1.5
            and x0 - 15 < e.dxf.start.x < x0 + 15 and -5 < e.dxf.start.y - ay < 30]
    fits = [_fit_circle(np.array([z for i in g for z in segs[i]])) for g in _components(segs, 0.8) if len(g) >= 8]
    assert any(r > 5 and res < 0.01 * r for _, _, r, res in fits), fits
    params = [e for e in msp.query('TEXT[layer=="TEXT_PARAM"]') if b[0] <= e.dxf.insert.x <= b[2]]
    others = [e for e in msp.query("TEXT") if b[0] <= e.dxf.insert.x <= b[2] and b[1] <= e.dxf.insert.y <= b[3]]
    for p in params:
        assert not any(_overlap(_text_box(p), _text_box(o)) for o in others if o is not p), p.dxf.text
