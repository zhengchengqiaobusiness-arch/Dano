"""OTel 占位:重写阶段先 no-op span(后续可接真实 tracing)。"""
from __future__ import annotations
from contextlib import contextmanager


@contextmanager
def span(name: str, **attrs):  # noqa: ANN001, ANN201
    yield None
