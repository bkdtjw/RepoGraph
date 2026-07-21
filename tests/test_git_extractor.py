"""git_extractor 自测：真实 git 仓库，断言 MODIFIES/overlap/悬空计数/fixes/TOUCHES 降级。

运行：cd <repo> && python tests/test_git_extractor.py
不依赖 pytest；用 assert。自行把 src 加入 sys.path。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from repograph.extract import git_extractor  # noqa: E402
from repograph.models import (  # noqa: E402
    FunctionFacts,
    ModuleFacts,
    PipelineStats,
    issue_id,
    module_id,
    symbol_id,
)


# ---------------------------------------------------------------------------
# git 仓库构造工具
# ---------------------------------------------------------------------------

def _git(cwd: str, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def _init_repo() -> str:
    d = tempfile.mkdtemp(prefix="rg_git_")
    _git(d, "init", "-q")
    _git(d, "config", "core.autocrlf", "false")
    return d


def _write(root: str, relpath: str, content: str) -> None:
    full = os.path.join(root, relpath.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _commit(root: str, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _fn(qualname: str, s: int = 1, e: int = 1) -> FunctionFacts:
    return FunctionFacts(
        qualname=qualname,
        span_start=s,
        span_end=e,
        signature="()",
        is_async=False,
        is_method=False,
        docstring=None,
    )


def _module(repo: str, relpath: str, fn_names: list[str]) -> ModuleFacts:
    return ModuleFacts(
        repo=repo,
        relpath=relpath,
        name=os.path.splitext(os.path.basename(relpath))[0],
        package="",
        dotted=relpath[:-3].replace("/", "."),
        loc=1,
        docstring=None,
        functions=[_fn(n) for n in fn_names],
    )


# ---------------------------------------------------------------------------
# 主场景：3 提交（新建/修改/删除+改装饰器行）
# ---------------------------------------------------------------------------

def test_main_scenario() -> None:
    root = _init_repo()
    repo = "demo"
    rp = "pkg/mod.py"

    # commit 1（根）
    _write(root, rp, "def alpha():\n    return 1\ndef to_delete():\n    return 99\n")
    _commit(root, "seed")

    # commit 2：改 alpha + 新增 beta，显式 fixes #5
    _write(
        root,
        rp,
        "def alpha():\n    return 100\ndef to_delete():\n    return 99\n"
        "def beta():\n    return 20\n",
    )
    _commit(root, "improve alpha and add beta, fixes #5")

    # commit 3：删除 to_delete + 给 alpha 加装饰器行，标题弱引用 (#12)
    _write(
        root,
        rp,
        "def deco(f):\n    return f\n\n@deco\ndef alpha():\n    return 100\n\n"
        "def beta():\n    return 20\n",
    )
    _commit(root, "drop to_delete; decorate alpha (#12)")

    # HEAD 模块（commit3 内容）：deco / alpha / beta
    modules = [_module(repo, rp, ["deco", "alpha", "beta"])]
    stats = PipelineStats()
    result = git_extractor.extract_git(root, repo, modules, stats)

    # 提交按 message 索引
    msg2cid = {c["message"]: c["id"] for c in result["commits"]}
    assert len(result["commits"]) == 3, result["commits"]
    cid1 = msg2cid["seed"]
    cid2 = msg2cid["improve alpha and add beta, fixes #5"]
    cid3 = msg2cid["drop to_delete; decorate alpha (#12)"]

    # Commit 节点属性齐备（§3.2）
    c2 = next(c for c in result["commits"] if c["id"] == cid2)
    for key in ("id", "hash", "author", "authored_at", "message",
                "files_changed", "insertions", "deletions"):
        assert key in c2, f"commit 缺属性 {key}: {c2}"
    assert c2["author"] == "t"
    assert "T" in c2["authored_at"]  # ISO8601

    mod_map = {(s, d): p for (s, d, p) in result["modifies"]}

    # commit2：alpha 改 1 行（+1/-1，overlap=2），beta 新增 2 行
    alpha_id = symbol_id(repo, rp, "alpha")
    beta_id = symbol_id(repo, rp, "beta")
    assert (cid2, alpha_id) in mod_map, mod_map
    assert mod_map[(cid2, alpha_id)] == {
        "lines_added": 1,
        "lines_deleted": 1,
        "overlap_lines": 2,
    }, mod_map[(cid2, alpha_id)]
    assert (cid2, beta_id) in mod_map
    assert mod_map[(cid2, beta_id)]["lines_added"] == 2
    assert mod_map[(cid2, beta_id)]["lines_deleted"] == 0
    assert mod_map[(cid2, beta_id)]["overlap_lines"] == 2

    # commit1（根）：alpha 新增 2 行（在 HEAD → 建边）
    assert (cid1, alpha_id) in mod_map, mod_map
    assert mod_map[(cid1, alpha_id)]["lines_added"] == 2

    # commit3：装饰器行落在 alpha 跨度内 → alpha 被记为改动；deco 新增
    deco_id = symbol_id(repo, rp, "deco")
    assert (cid3, alpha_id) in mod_map, "改装饰器行应映射到 alpha"
    assert mod_map[(cid3, alpha_id)]["lines_added"] >= 1
    assert (cid3, deco_id) in mod_map, "新增 deco 应建边"

    # to_delete 不在 HEAD：commit1 新增 + commit3 删除 → 至少两次悬空
    assert stats.dangling_modifies >= 2, stats.dangling_modifies
    # to_delete 从不应出现在 MODIFIES 目标里（无幽灵节点）
    td_id = symbol_id(repo, rp, "to_delete")
    assert all(d != td_id for (_, d) in mod_map), "被删函数不得建 MODIFIES 边"

    # 覆盖计数
    assert stats.code_commits == 3
    assert stats.modifies_commits == 3

    # blob 缓存命中：commit2 的版本既是 commit3 diff 的旧侧、又是 commit2 diff 的新侧
    assert stats.blob_cache_misses > 0
    assert stats.blob_cache_hits >= 1, (
        stats.blob_cache_hits,
        stats.blob_cache_misses,
    )

    # FIXES：显式 fixes #5 + squash 弱引用 (#12)
    fx_map = {(s, d): p for (s, d, p) in result["fixes"]}
    assert (cid2, issue_id(repo, 5)) in fx_map, fx_map
    assert fx_map[(cid2, issue_id(repo, 5))]["pattern"].startswith("fix")
    assert (cid3, issue_id(repo, 12)) in fx_map, fx_map
    assert fx_map[(cid3, issue_id(repo, 12))]["pattern"] == "pr_ref"

    # Issue 存根节点
    inums = {i["number"] for i in result["issues"]}
    assert inums == {5, 12}, inums
    for i in result["issues"]:
        assert i["stub"] is True
        assert i["id"] == issue_id(repo, i["number"])

    shutil.rmtree(root, ignore_errors=True)
    print("test_main_scenario OK")


# ---------------------------------------------------------------------------
# 降级场景：历史 blob 语法错误 → TOUCHES(Commit→Module)
# ---------------------------------------------------------------------------

def test_touches_fallback_on_bad_blob() -> None:
    root = _init_repo()
    repo = "brk"
    rp = "bad.py"

    _write(root, rp, "def ok():\n    return 1\n")
    _commit(root, "add bad")

    # 引入语法错误
    _write(root, rp, "def ok(:\n    return 1\n")
    _commit(root, "break bad")

    # 修复
    _write(root, rp, "def ok():\n    return 2\n")
    _commit(root, "fix bad")

    modules = [_module(repo, rp, ["ok"])]
    stats = PipelineStats()
    result = git_extractor.extract_git(root, repo, modules, stats)

    msg2cid = {c["message"]: c["id"] for c in result["commits"]}
    cid_break = msg2cid["break bad"]

    touch_pairs = {(s, d) for (s, d, _) in result["touches"]}
    assert (cid_break, module_id(repo, rp)) in touch_pairs, result["touches"]
    assert stats.parse_skips_blob >= 1, stats.parse_skips_blob

    # 语法错误的那次提交不应产生指向 ok 的 MODIFIES（解析失败降级）
    bad_modifies = [
        (s, d) for (s, d, _) in result["modifies"] if s == cid_break
    ]
    assert not bad_modifies, bad_modifies

    shutil.rmtree(root, ignore_errors=True)
    print("test_touches_fallback_on_bad_blob OK")


# ---------------------------------------------------------------------------
# 边界：空仓库不崩
# ---------------------------------------------------------------------------

def test_empty_repo() -> None:
    root = _init_repo()
    stats = PipelineStats()
    result = git_extractor.extract_git(root, "empty", [], stats)
    assert result["commits"] == []
    assert result["modifies"] == []
    assert result["issues"] == []
    shutil.rmtree(root, ignore_errors=True)
    print("test_empty_repo OK")


if __name__ == "__main__":
    test_main_scenario()
    test_touches_fallback_on_bad_blob()
    test_empty_repo()
    print("ALL PASSED")
