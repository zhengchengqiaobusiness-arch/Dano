from dano.auth.throttle import CLEARED, ThrottleState, is_locked, register_failure

NOW = 1_700_000_000.0


def test_未达阈值不锁定():
    state = CLEARED
    for _ in range(4):
        state = register_failure(state, now=NOW)
    assert state.fail_count == 4
    assert not is_locked(state, now=NOW)


def test_第五次失败锁一分钟():
    state = CLEARED
    for _ in range(5):
        state = register_failure(state, now=NOW)
    assert is_locked(state, now=NOW)
    assert state.locked_until == NOW + 60
    assert not is_locked(state, now=NOW + 61)


def test_退避逐次翻倍():
    state = CLEARED
    for _ in range(5):
        state = register_failure(state, now=NOW)
    assert state.locked_until - NOW == 60
    state = register_failure(state, now=NOW)
    assert state.locked_until - NOW == 120
    state = register_failure(state, now=NOW)
    assert state.locked_until - NOW == 240


def test_锁定时长封顶():
    state = ThrottleState(fail_count=40, locked_until=0.0)
    state = register_failure(state, now=NOW, lock_max_minutes=30)
    assert state.locked_until - NOW == 30 * 60


def test_成功后清零():
    assert CLEARED.fail_count == 0
    assert not is_locked(CLEARED, now=NOW)


def test_阈值可配():
    state = CLEARED
    state = register_failure(state, now=NOW, max_failures=2)
    assert not is_locked(state, now=NOW)
    state = register_failure(state, now=NOW, max_failures=2)
    assert is_locked(state, now=NOW)
