"""Apache AGE 后端的对外唯一入口（文档 §6.3）——部署形态预留。

本仓库 v0.1 的默认后端是 models.GraphStore（内存 + JSON 持久化）。本文件是
"PostgreSQL 16 + Apache AGE" 部署形态的切换点：当规模或多用户并发需要真正的
图数据库时，把检索/写入路由到这里，无需改动上层调用契约。

设计要点（§6.3）：
  * AGE 的 `cypher()` 第三参数为 agtype 参数映射，必须经 prepared statement 传入
    （PREPARE q(agtype) AS SELECT * FROM cypher('repograph', $$ ... $$, $1) AS (...)），
    这是与 Neo4j 驱动体验差异最大处，被 run_cypher 封装为唯一入口。
  * 写查询与读查询走不同连接池角色（§8.4）：读用 repograph_ro，无 INSERT 权限，
    在数据库层硬拦截 text2cypher 的写操作。
  * 图 DDL 与初始化见同目录 ddl.sql（§6.2 关系表 + §6.3 AGE create_graph/GIN 索引）。

psycopg（psycopg 3）为可选依赖：本地默认后端不需要它，故在 import 处 try/except，
未安装时本模块仍可被导入（函数体在调用时才 raise）。
"""
from __future__ import annotations

from typing import Any, Optional

try:  # 可选依赖：仅 AGE 部署形态需要
    import psycopg  # type: ignore
    _HAS_PSYCOPG = True
    _IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # 本地后端下缺失 psycopg 属正常
    psycopg = None  # type: ignore
    _HAS_PSYCOPG = False
    _IMPORT_ERROR = exc


def has_psycopg() -> bool:
    """psycopg 是否可用（AGE 后端是否具备最低运行条件）。"""
    return _HAS_PSYCOPG


def run_cypher(conn: Any, query: str, params: dict, columns: list[str]) -> list[dict]:
    """在 AGE 图 'repograph' 上执行一条 Cypher，返回行列表（每行 dict，键为 columns）。

    query 中以 `$name` 引用参数；本函数负责 agtype 参数映射的 PREPARE/EXECUTE 与
    返回值反序列化（agtype → Python）。变长路径上界（`*1..d`）不可参数化，由调用方
    以白名单整数拼入查询串，其余值一律走 params（§7.2）。

    形态预留：AGE 后端尚未启用——这是文档 §6.3 的切换点。本地默认后端为
    models.GraphStore；检索请走 retrieve/*（BFS 实现），组装请走 build.build_graph。
    """
    raise NotImplementedError(
        "AGE 后端未启用：这是文档 §6.3 的部署形态切换点，v0.1 默认后端为 "
        "models.GraphStore。启用步骤：部署 ddl.sql → 装 psycopg 3 → 在此实现 "
        "PREPARE/EXECUTE 与 agtype 反序列化。"
        + ("" if _HAS_PSYCOPG else f"（当前 psycopg 亦未安装：{_IMPORT_ERROR}）")
    )
