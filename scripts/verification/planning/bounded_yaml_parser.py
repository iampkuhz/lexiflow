"""为受信任配置提供有界、按内容键控的 YAML 纯解析复用。"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

import yaml

_MAX_CACHE_BYTES = 128 * 1024


@lru_cache(maxsize=8, typed=True)
def _parse_cached(content: bytes) -> Any:
    return yaml.safe_load(content)


def parse_yaml_input(content: bytes) -> Any:
    """解析精确 YAML 内容；每次返回独立深副本，不复用调用方的校验结论。"""
    if not isinstance(content, bytes):
        raise TypeError("YAML input must be raw UTF-8 bytes")
    content.decode("utf-8")
    cacheable = len(content) <= _MAX_CACHE_BYTES
    parsed = _parse_cached(content) if cacheable else yaml.safe_load(content)
    return deepcopy(parsed)
