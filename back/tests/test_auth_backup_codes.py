from dano.auth.backup_codes import consume, generate_codes, hash_code, normalize


def test_生成十个不重复的码():
    codes = generate_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for code in codes:
        assert len(code) == 11 and code[5] == "-"


def test_归一化忽略分隔符与大小写():
    assert normalize("a7k2m-9pqr3") == "A7K2M9PQR3"
    assert normalize(" A7K2M 9PQR3 ") == "A7K2M9PQR3"


def test_核销后该码失效():
    codes = generate_codes()
    hashes = [hash_code(c) for c in codes]
    remaining = consume(codes[3], hashes)
    assert remaining is not None
    assert len(remaining) == 9
    assert consume(codes[3], remaining) is None


def test_未命中返回None():
    hashes = [hash_code(c) for c in generate_codes()]
    assert consume("ZZZZZ-ZZZZZ", hashes) is None


def test_不含易混字符():
    joined = "".join(normalize(c) for c in generate_codes(count=50))
    assert not set(joined) & set("OIL01")
