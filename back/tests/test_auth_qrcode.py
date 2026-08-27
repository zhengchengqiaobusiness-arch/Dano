"""QR 编码器对拍测试。

判据不是"与某个库的矩阵全等":掩码编号写在格式信息里,任选一个掩码都能被
扫描器正确解出,各家库的选择本就不同(segno / qrcode / 本实现三者互不相同)。
真正要验的是两件事:
  1. 固定同一个掩码时,码字、纠错、铺设、格式信息与参考实现逐模块一致;
  2. encode_matrix 确实按规范选了罚分最低的那个掩码。
"""

import base64

import pytest

from dano.auth.qrcode import (
    _MASKS,
    _draw_format,
    _draw_function_patterns,
    _encode_data,
    _interleave,
    _penalty,
    _pick_version,
    _place_data,
    encode_matrix,
    svg_data_uri,
)

qrcode = pytest.importorskip("qrcode")
from qrcode.constants import ERROR_CORRECT_M

OTPAUTH = ("otpauth://totp/Dano%3Aacme?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
           "&issuer=Dano&algorithm=SHA1&digits=6&period=30")


def _candidates(text: str) -> tuple[int, list[list[list[bool]]]]:
    """构造该文本在 8 个掩码下的完整矩阵。"""
    payload = text.encode("utf-8")
    version = _pick_version(len(payload))
    size = version * 4 + 17
    base = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]
    _draw_function_patterns(base, reserved, version, size)
    _place_data(base, reserved, _interleave(_encode_data(payload, version), version), size)

    out = []
    for mask_id in range(8):
        cand = [row[:] for row in base]
        rule = _MASKS[mask_id]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and rule(r, c):
                    cand[r][c] = not cand[r][c]
        _draw_format(cand, size, version, mask_id)
        out.append(cand)
    return version, out


def _reference(text: str, version: int, mask_id: int) -> list[list[bool]]:
    qr = qrcode.QRCode(version=version, error_correction=ERROR_CORRECT_M,
                       box_size=1, border=0, mask_pattern=mask_id)
    qr.add_data(text, optimize=0)   # 关掉混合模式优化:我们只做单段 byte 模式
    qr.make(fit=False)
    return [[bool(v) for v in row] for row in qr.get_matrix()]


@pytest.mark.parametrize("text", [OTPAUTH, "hello world", "a" * 100, "x" * 200])
def test_每个掩码下与参考实现逐模块一致(text):
    version, candidates = _candidates(text)
    for mask_id in range(8):
        assert candidates[mask_id] == _reference(text, version, mask_id), \
            f"掩码 {mask_id} 与参考实现不一致"


@pytest.mark.parametrize("text", [OTPAUTH, "hello world", "a" * 100, "x" * 200])
def test_选中罚分最低的掩码(text):
    _, candidates = _candidates(text)
    best = min(range(8), key=lambda m: _penalty(candidates[m]))
    assert encode_matrix(text) == candidates[best]


def test_版本随长度增长():
    assert _pick_version(10) == 1
    assert _pick_version(100) == 6
    assert _pick_version(180) == 9      # v9 的 byte 容量上界
    assert _pick_version(181) == 10     # 越过即进 v10(计数指示符也从 8 位变 16 位)
    assert _pick_version(213) == 10
    with pytest.raises(ValueError, match="超出版本"):
        _pick_version(214)


def test_svg_data_uri可解码且含尺寸():
    uri = svg_data_uri("otpauth://totp/Dano:acme?secret=ABCDEFGH")
    assert uri.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")
    assert svg.startswith("<svg") and "viewBox" in svg and "</svg>" in svg


def test_能被真实解码器读回原文():
    """用 OpenCV 的 QR 解码器把生成的码读回来 —— 比矩阵对拍更贴近"扫描器能不能认"。

    opencv 不在 dev 依赖里(体积大),装了才跑;日常靠上面的逐掩码对拍把关。
    """
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")

    detector = cv2.QRCodeDetector()
    for text in (OTPAUTH, "hello world", "x" * 180):
        matrix = np.array(encode_matrix(text), dtype=np.uint8)
        quiet, scale = 4, 8
        canvas = np.ones((len(matrix) + quiet * 2,) * 2, dtype=np.uint8)
        canvas[quiet:quiet + len(matrix), quiet:quiet + len(matrix)] = 1 - matrix
        image = np.kron(canvas, np.ones((scale, scale), dtype=np.uint8)) * 255
        decoded, _, _ = detector.detectAndDecode(image)
        assert decoded == text
