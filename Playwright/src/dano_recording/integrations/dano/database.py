"""Shared PostgreSQL pool adapter."""


def get_shared_pool():
    from dano.infra.db import get_pool

    return get_pool()
