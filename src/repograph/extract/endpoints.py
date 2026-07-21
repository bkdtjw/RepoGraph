"""端点识别（技术设计文档 §4.3）。

对每个函数的装饰器点分名，逐个与 `settings.endpoint_patterns` 正则匹配。
命中即标记 `is_endpoint=True`，`http_method` 取装饰器点分名最后一段大写，
`route_path` 取该装饰器对应的 `decorator_first_arg`（首个字符串字面量位置参数）。

端点标记是影响面分析（§7.2）把“波及的函数”进一步汇聚为“波及的 API”的依据。
识别不到不硬编码——新的入口型装饰器（Feishu Bot 回调、CLI 入口等）通过在
配置中追加模式接入。
"""
from __future__ import annotations

import re

from repograph.models import ModuleFacts


def mark_endpoints(modules: list[ModuleFacts], settings) -> int:
    """就地标记端点函数，返回被标记的函数数量。

    幂等：模式列表相同则重复调用结果一致。
    """
    patterns = [re.compile(p) for p in settings.endpoint_patterns]
    marked = 0

    for module in modules:
        for fn in module.functions:
            hit = _match_decorator(fn.decorators, fn.decorator_first_arg, patterns)
            if hit is None:
                continue
            dec_name, dec_arg = hit
            fn.is_endpoint = True
            # 点分名最后一段大写：app.get → GET；router.websocket → WEBSOCKET
            fn.http_method = dec_name.rsplit(".", 1)[-1].upper()
            fn.route_path = dec_arg
            marked += 1

    return marked


def _match_decorator(
    decorators: list[str],
    first_args: list[str | None],
    patterns: list[re.Pattern],
) -> tuple[str, str | None] | None:
    """返回首个命中的 (装饰器点分名, 对应首参) ，无命中返回 None。"""
    for i, dec_name in enumerate(decorators):
        for pat in patterns:
            if pat.search(dec_name):
                arg = first_args[i] if i < len(first_args) else None
                return dec_name, arg
    return None
