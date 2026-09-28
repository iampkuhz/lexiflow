"""读取并校验 LexiFlow 私有本机运行配置。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from scripts.environment.java_runtime import JavaRuntimeError

_CONFIG_PATH = Path(".local/lexiflow/runtime.json")
_ALLOWED_KEYS = frozenset({"JDBC_URL", "STARDICT_CSV"})


class LocalConfigError(JavaRuntimeError):
    """本机运行配置不可安全读取或不符合约束。"""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate key")
        values[key] = value
    return values


def _read_config(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=_unique_object)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError) as exc:
        raise LocalConfigError("本机运行配置无法读取或不是合法 JSON。") from exc

    if not isinstance(value, dict):
        raise LocalConfigError("本机运行配置必须是 JSON 对象。")
    if any(key not in _ALLOWED_KEYS for key in value):
        raise LocalConfigError("本机运行配置包含未知字段。")
    config: dict[str, str] = {}
    for key, item in value.items():
        if (
            not isinstance(item, str)
            or not item.strip()
            or any(ord(char) < 32 for char in item)
        ):
            raise LocalConfigError("本机运行配置字段必须是非空字符串。")
        if key == "STARDICT_CSV" and not Path(item).is_absolute():
            raise LocalConfigError("STARDICT_CSV 必须是绝对路径。")
        config[key] = item
    return config


def command_local_config(root: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """读取私有配置并合并环境变量；显式环境变量（包括空值）优先。"""

    config = _read_config(root / _CONFIG_PATH)
    for key in _ALLOWED_KEYS:
        if key in environ:
            config[key] = environ[key]
    return config
