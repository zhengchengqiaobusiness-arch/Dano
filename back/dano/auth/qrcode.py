"""最小 QR 编码器(byte 模式, 纠错级 M, 版本 1-10)→ SVG data URI。

只为 TOTP 绑定二维码服务,不追求完整 QR 规范覆盖(不做 numeric/alphanumeric
模式优化,不支持版本 11 以上)。正确性由与 segno 的逐模块对拍测试保证,
见 tests/test_auth_qrcode.py。
"""

from __future__ import annotations

import base64

# 版本 → (每块纠错码字数, 组1块数, 组1数据码字, 组2块数, 组2数据码字),纠错级 M
_EC_M: dict[int, tuple[int, int, int, int, int]] = {
    1: (10, 1, 16, 0, 0),
    2: (16, 1, 28, 0, 0),
    3: (26, 1, 44, 0, 0),
    4: (18, 2, 32, 0, 0),
    5: (24, 2, 43, 0, 0),
    6: (16, 4, 27, 0, 0),
    7: (18, 4, 31, 0, 0),
    8: (22, 2, 38, 2, 39),
    9: (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44),
}

# 版本 → 校正图形中心坐标
_ALIGNMENT: dict[int, list[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

_MAX_VERSION = 10


# ── GF(256) ──────────────────────────────────────────────────────────────
def _build_gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value <<= 1
        if value & 0x100:              # 本原多项式 x^8+x^4+x^3+x^2+1
            value ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


_EXP, _LOG = _build_gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    """生成多项式 (x-2^0)(x-2^1)...(x-2^(degree-1)) 的系数。"""
    poly = [1]
    for i in range(degree):
        poly.append(0)
        for j in range(len(poly) - 1, 0, -1):
            poly[j] ^= _gf_mul(poly[j - 1], _EXP[i])
    return poly


def _rs_encode(data: bytes, ec_len: int) -> bytes:
    """按生成多项式做多项式除法,余数即纠错码字。"""
    generator = _rs_generator(ec_len)
    remainder = [0] * ec_len
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i in range(ec_len):
            remainder[i] ^= _gf_mul(generator[i + 1], factor)
    return bytes(remainder)


# ── 数据编码 ─────────────────────────────────────────────────────────────
def _data_capacity(version: int) -> int:
    ec_len, g1_blocks, g1_data, g2_blocks, g2_data = _EC_M[version]
    return g1_blocks * g1_data + g2_blocks * g2_data


def _count_bits(version: int) -> int:
    """byte 模式的字符计数指示符位数:版本 1-9 是 8 位,10 起是 16 位。"""
    return 8 if version <= 9 else 16


def _pick_version(payload_len: int) -> int:
    for version in range(1, _MAX_VERSION + 1):
        needed = 4 + _count_bits(version) + payload_len * 8
        if needed <= _data_capacity(version) * 8:
            return version
    raise ValueError(f"内容过长,超出版本 {_MAX_VERSION} 的容量")


def _encode_data(payload: bytes, version: int) -> bytes:
    """模式指示符 + 长度 + 字节流 + 终止符 + 填充,凑满数据码字。"""
    capacity = _data_capacity(version) * 8
    bits = "0100" + format(len(payload), f"0{_count_bits(version)}b")
    bits += "".join(format(byte, "08b") for byte in payload)
    bits += "0" * min(4, capacity - len(bits))        # 终止符最多 4 位
    bits += "0" * (-len(bits) % 8)                    # 补齐到整字节
    codewords = bytearray(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
    for pad in _cycle_pad(_data_capacity(version) - len(codewords)):
        codewords.append(pad)
    return bytes(codewords)


def _cycle_pad(count: int) -> list[int]:
    """规范指定的填充字节:0xEC 与 0x11 交替。"""
    return [0xEC if i % 2 == 0 else 0x11 for i in range(max(count, 0))]


def _interleave(data: bytes, version: int) -> bytes:
    """按分组切块、逐块算纠错码,再交织成最终码字序列。"""
    ec_len, g1_blocks, g1_data, g2_blocks, g2_data = _EC_M[version]
    blocks: list[bytes] = []
    cursor = 0
    for _ in range(g1_blocks):
        blocks.append(data[cursor:cursor + g1_data])
        cursor += g1_data
    for _ in range(g2_blocks):
        blocks.append(data[cursor:cursor + g2_data])
        cursor += g2_data
    ec_blocks = [_rs_encode(block, ec_len) for block in blocks]

    result = bytearray()
    for i in range(max(len(b) for b in blocks)):
        for block in blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(ec_len):
        for block in ec_blocks:
            result.append(block[i])
    return bytes(result)


# ── 矩阵铺设 ─────────────────────────────────────────────────────────────
def _draw_finder(matrix, reserved, row: int, col: int, size: int) -> None:
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = row + r, col + c
            if not (0 <= rr < size and 0 <= cc < size):
                continue
            ring = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6))
            core = 2 <= r <= 4 and 2 <= c <= 4
            matrix[rr][cc] = ring or core
            reserved[rr][cc] = True


def _draw_alignment(matrix, reserved, centers: list[int], size: int) -> None:
    for row in centers:
        for col in centers:
            # 跳过与三个定位图形重叠的位置
            if (row, col) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    matrix[row + r][col + c] = max(abs(r), abs(c)) != 1
                    reserved[row + r][col + c] = True


def _reserve_format_areas(reserved, size: int, version: int) -> None:
    for i in range(9):
        reserved[8][i] = True
        reserved[i][8] = True
    for i in range(8):
        reserved[size - 1 - i][8] = True
        reserved[8][size - 1 - i] = True
    if version >= 7:
        for i in range(18):
            reserved[i // 3][size - 11 + i % 3] = True
            reserved[size - 11 + i % 3][i // 3] = True


def _draw_function_patterns(matrix, reserved, version: int, size: int) -> None:
    _draw_finder(matrix, reserved, 0, 0, size)
    _draw_finder(matrix, reserved, 0, size - 7, size)
    _draw_finder(matrix, reserved, size - 7, 0, size)
    for i in range(size):                          # 时序图形
        if not reserved[6][i]:
            matrix[6][i] = i % 2 == 0
            reserved[6][i] = True
        if not reserved[i][6]:
            matrix[i][6] = i % 2 == 0
            reserved[i][6] = True
    _draw_alignment(matrix, reserved, _ALIGNMENT[version], size)
    matrix[4 * version + 9][8] = True              # 固定暗模块
    reserved[4 * version + 9][8] = True
    _reserve_format_areas(reserved, size, version)


def _place_data(matrix, reserved, codewords: bytes, size: int) -> None:
    bits = [bool((byte >> shift) & 1) for byte in codewords for shift in range(7, -1, -1)]
    index = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:                               # 跳过时序列
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for cc in (col, col - 1):
                if reserved[row][cc]:
                    continue
                matrix[row][cc] = bits[index] if index < len(bits) else False
                index += 1
        upward = not upward
        col -= 2


# ── 掩码与格式信息 ───────────────────────────────────────────────────────
_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _bch(value: int, generator: int, gen_bits: int) -> int:
    result = value
    while result.bit_length() >= gen_bits:
        result ^= generator << (result.bit_length() - gen_bits)
    return result


def _format_bits(mask_id: int) -> int:
    """纠错级 M(指示位 00)+ 掩码号的 15 位格式信息。"""
    data = mask_id                       # (0b00 << 3) | mask_id
    bits = (data << 10) | _bch(data << 10, 0b10100110111, 11)
    return bits ^ 0b101010000010010


def _version_bits(version: int) -> int:
    return (version << 12) | _bch(version << 12, 0b1111100100101, 13)


def _draw_format(matrix, size: int, version: int, mask_id: int) -> None:
    bits = _format_bits(mask_id)
    for i in range(15):
        bit = bool((bits >> i) & 1)
        # 第一份副本:左上角,沿列 8 向下再折向行 8
        if i < 6:
            matrix[i][8] = bit
        elif i == 6:
            matrix[7][8] = bit
        elif i == 7:
            matrix[8][8] = bit
        elif i == 8:
            matrix[8][7] = bit
        else:
            matrix[8][14 - i] = bit
        # 第二份副本:右下角
        if i < 8:
            matrix[8][size - 1 - i] = bit
        else:
            matrix[size - 15 + i][8] = bit
    if version >= 7:
        vbits = _version_bits(version)
        for i in range(18):
            bit = bool((vbits >> i) & 1)
            matrix[i // 3][size - 11 + i % 3] = bit
            matrix[size - 11 + i % 3][i // 3] = bit


def _penalty(matrix: list[list[bool]]) -> int:
    size = len(matrix)
    score = 0
    # 规则 1:行/列中连续同色 5 个以上
    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    # 规则 2:2x2 同色块
    for r in range(size - 1):
        for c in range(size - 1):
            block = (matrix[r][c], matrix[r][c + 1], matrix[r + 1][c], matrix[r + 1][c + 1])
            if all(block) or not any(block):
                score += 3
    # 规则 3:类定位图形的 1:1:3:1:1 序列
    pattern_a = [True, False, True, True, True, False, True, False, False, False, False]
    pattern_b = list(reversed(pattern_a))
    for line in list(matrix) + [list(col) for col in zip(*matrix)]:
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == pattern_a or window == pattern_b:
                score += 40
    # 规则 4:暗模块占比偏离 50%
    dark = sum(cell for row in matrix for cell in row)
    percent = dark * 100 / (size * size)
    score += 10 * int(abs(percent - 50) // 5)
    return score


def encode_matrix(text: str) -> list[list[bool]]:
    """编码为模块矩阵(不含静区),True = 黑。"""
    payload = text.encode("utf-8")
    version = _pick_version(len(payload))
    codewords = _interleave(_encode_data(payload, version), version)
    size = version * 4 + 17

    base: list[list[bool]] = [[False] * size for _ in range(size)]
    reserved: list[list[bool]] = [[False] * size for _ in range(size)]
    _draw_function_patterns(base, reserved, version, size)
    _place_data(base, reserved, codewords, size)

    best_score = None
    best_matrix: list[list[bool]] = []
    for mask_id in range(8):
        candidate = [row[:] for row in base]
        rule = _MASKS[mask_id]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and rule(r, c):
                    candidate[r][c] = not candidate[r][c]
        _draw_format(candidate, size, version, mask_id)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best_score, best_matrix = score, candidate
    return best_matrix


def svg_data_uri(text: str, *, module_px: int = 4, quiet_zone: int = 4) -> str:
    """渲染为 SVG 并包成 data URI,前端用 <img src> 直接显示。"""
    matrix = encode_matrix(text)
    size = len(matrix) + quiet_zone * 2
    rects = "".join(
        f'<rect x="{x + quiet_zone}" y="{y + quiet_zone}" width="1" height="1"/>'
        for y, row in enumerate(matrix) for x, cell in enumerate(row) if cell
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size * module_px}" height="{size * module_px}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<g fill="#000">{rects}</g></svg>'
    )
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"
