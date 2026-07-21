"""Git 历史抽取与 diff→函数映射（技术设计文档 §4.4 与 §4.5）。

产出（返回 dict，供 build.build_graph 消费）：
    commits : list[dict]  Commit 节点属性（含 id，§3.2）
    issues  : list[dict]  Issue 存根节点（id / number / stub=True）
    modifies: list[tuple[commit_id, function_id, {lines_added,lines_deleted,overlap_lines}]]
    touches : list[tuple[commit_id, module_id,   {lines_added,lines_deleted}]]
    fixes   : list[tuple[commit_id, issue_id,    {pattern}]]

设计要点：
  * 逐提交取首父 diff（合并提交只取第一父；根提交与 git 空树比较）。
  * unified diff hunk 头 @@ -a,b +c,d @@：'+' 行按新侧行号归属该提交版本的函数，
    '-' 行按旧侧行号归属父版本的函数（含纯删除 hunk d==0），二者合并为一条 MODIFIES。
  * 函数跨度表以“该提交时点”的 blob 源码 ast.parse 得到（span 含装饰器行），
    按 blob hexsha 缓存，命中/未命中计入 PipelineStats。
  * 映射目标锚定 HEAD：仅当同 relpath 同 qualname 的函数在传入的 modules 中存在才建边，
    否则计 dangling_modifies（不建幽灵节点）。
  * blob 解析失败（历史语法错误）→ 降级 TOUCHES(Commit→Module)（模块须在 HEAD 存在）。

本模块只依赖标准库、GitPython、以及共享契约 models.py（ID 生成），不 import 其他抽取模块。
"""
from __future__ import annotations

import ast
import re

import git

from ..models import (
    ModuleFacts,
    PipelineStats,
    commit_id,
    issue_id,
    module_id,
    symbol_id,
)

# git 的固定空树对象（用于根提交 diff）
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# hunk 头：@@ -a[,b] +c[,d] @@（b/d 省略时默认 1）
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# §4.5 显式修复关键词（捕获关键词本身作为 pattern）
_FIXES_RE = re.compile(
    r"\b(fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE
)
# squash-merge 标题中的弱引用 (#N)
_PR_REF_RE = re.compile(r"\(#(\d+)\)")

# blob 解析失败在缓存中的哨兵
_PARSE_ERROR = "__PARSE_ERROR__"


# ---------------------------------------------------------------------------
# 轻量跨度表：qualname → (span_start, span_end)，span_start 含装饰器行
# （与 ast_extractor 同规则，独立实现，避免跨 agent 依赖）
# ---------------------------------------------------------------------------

def _span_start(node: ast.AST) -> int:
    lines = [node.lineno]
    for d in getattr(node, "decorator_list", []):
        lines.append(d.lineno)
    return min(lines)


def build_span_table(source: str) -> dict[str, tuple[int, int]]:
    """解析源码得到函数跨度表（可能抛 SyntaxError，由调用方处理）。"""
    tree = ast.parse(source)
    spans: dict[str, tuple[int, int]] = {}
    stack: list[str] = []

    class _V(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def _func(self, node) -> None:
            qn = ".".join(stack + [node.name])
            end = getattr(node, "end_lineno", None) or node.lineno
            # 同名重复定义（如条件定义）取首个
            spans.setdefault(qn, (_span_start(node), end))
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = _func       # type: ignore[assignment]
        visit_AsyncFunctionDef = _func  # type: ignore[assignment]

    _V().visit(tree)
    return spans


# ---------------------------------------------------------------------------
# unified diff 解析：返回新增行的新侧行号、删除行的旧侧行号
# ---------------------------------------------------------------------------

def _parse_patch(patch_text: str) -> tuple[list[int], list[int]]:
    added: list[int] = []
    deleted: list[int] = []
    new_ln: int | None = None
    old_ln: int | None = None
    for line in patch_text.split("\n"):
        m = _HUNK_RE.match(line)
        if m:
            old_ln = int(m.group(1))
            new_ln = int(m.group(3))
            continue
        if new_ln is None or old_ln is None:
            # hunk 之前的头部（diff --git / --- / +++ 等），忽略
            continue
        if line == "":
            # 分片间的真正空行（罕见）；不推进行号
            continue
        tag = line[0]
        if tag == "+":
            added.append(new_ln)
            new_ln += 1
        elif tag == "-":
            deleted.append(old_ln)
            old_ln += 1
        elif tag == "\\":
            # "\ No newline at end of file"
            continue
        else:
            # 上下文行（以空格开头）
            new_ln += 1
            old_ln += 1
    return added, deleted


def _blob_bytes(blob) -> bytes:
    return blob.data_stream.read()


def _span_table_cached(
    blob, cache: dict[str, object], stats: PipelineStats
) -> dict[str, tuple[int, int]] | None:
    """按 blob hexsha 缓存跨度表。返回 None 表示解析失败（历史语法错误）。"""
    if blob is None:
        return None
    key = blob.hexsha
    if key in cache:
        stats.blob_cache_hits += 1
        val = cache[key]
        return None if val == _PARSE_ERROR else val  # type: ignore[return-value]
    stats.blob_cache_misses += 1
    try:
        source = _blob_bytes(blob).decode("utf-8", "replace")
        table = build_span_table(source)
    except SyntaxError:
        cache[key] = _PARSE_ERROR
        return None
    cache[key] = table
    return table


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def extract_git(
    repo_root: str,
    repo_name: str,
    modules: list[ModuleFacts],
    stats: PipelineStats,
) -> dict:
    repo = git.Repo(repo_root)

    # HEAD 锚定索引
    head_relpaths: set[str] = {m.relpath for m in modules}
    head_funcs: set[tuple[str, str]] = {
        (m.relpath, fn.qualname) for m in modules for fn in m.functions
    }

    commits_out: list[dict] = []
    modifies_out: list[tuple[str, str, dict]] = []
    touches_out: list[tuple[str, str, dict]] = []
    fixes_out: list[tuple[str, str, dict]] = []

    issue_numbers: set[int] = set()
    blob_cache: dict[str, object] = {}

    try:
        commit_iter = list(repo.iter_commits("HEAD"))
    except git.exc.GitCommandError:
        # 空仓库（无任何提交）
        commit_iter = []

    for commit in commit_iter:
        sha = commit.hexsha
        cid = commit_id(repo_name, sha)

        # --- Commit 节点属性（§3.2） ---
        total = commit.stats.total
        commits_out.append(
            {
                "id": cid,
                "repo": repo_name,
                "hash": sha,
                "author": commit.author.name if commit.author else "",
                "authored_at": commit.authored_datetime.isoformat(),
                "message": (commit.message or "").strip(),
                "files_changed": int(total.get("files", 0)),
                "insertions": int(total.get("insertions", 0)),
                "deletions": int(total.get("deletions", 0)),
            }
        )

        # --- FIXES / pr_ref（§4.5） ---
        _extract_fixes(commit, repo_name, cid, fixes_out, issue_numbers)

        # --- diff → 函数映射（§4.4） ---
        diffs = _diff_items(repo, commit)
        has_py_change = False
        produced_modifies = False

        for d in diffs:
            b_path = d.b_path
            a_path = d.a_path
            is_py = (b_path and b_path.endswith(".py")) or (
                a_path and a_path.endswith(".py")
            )
            if not is_py:
                continue
            has_py_change = True

            patch = d.diff
            if not patch:
                # 纯 rename / mode change，无内容变更
                continue
            patch_text = patch.decode("utf-8", "replace") if isinstance(patch, bytes) else patch
            added, deleted = _parse_patch(patch_text)
            if not added and not deleted:
                continue

            # 当前（HEAD 侧）文件路径：优先新侧
            cur_relpath = (b_path or a_path).replace("\\", "/")

            # 需要的跨度表
            new_table = None
            old_table = None
            new_failed = False
            old_failed = False
            if added:
                new_table = _span_table_cached(d.b_blob, blob_cache, stats)
                new_failed = new_table is None and d.b_blob is not None
            if deleted:
                old_table = _span_table_cached(d.a_blob, blob_cache, stats)
                old_failed = old_table is None and d.a_blob is not None

            # blob 解析失败 → 降级 TOUCHES（模块须在 HEAD 存在）
            if new_failed or old_failed:
                stats.parse_skips_blob += 1
                if cur_relpath in head_relpaths:
                    touches_out.append(
                        (
                            cid,
                            module_id(repo_name, cur_relpath),
                            {"lines_added": len(added), "lines_deleted": len(deleted)},
                        )
                    )
                continue

            # 逐函数累计新增/删除行
            file_mods: dict[str, list[int]] = {}
            if new_table:
                for qn, (s, e) in new_table.items():
                    n_add = sum(1 for ln in added if s <= ln <= e)
                    if n_add:
                        file_mods.setdefault(qn, [0, 0])[0] += n_add
            if old_table:
                for qn, (s, e) in old_table.items():
                    n_del = sum(1 for ln in deleted if s <= ln <= e)
                    if n_del:
                        file_mods.setdefault(qn, [0, 0])[1] += n_del

            for qn, (n_add, n_del) in file_mods.items():
                if n_add == 0 and n_del == 0:
                    continue
                if (cur_relpath, qn) in head_funcs:
                    modifies_out.append(
                        (
                            cid,
                            symbol_id(repo_name, cur_relpath, qn),
                            {
                                "lines_added": n_add,
                                "lines_deleted": n_del,
                                "overlap_lines": n_add + n_del,
                            },
                        )
                    )
                    produced_modifies = True
                else:
                    # 函数在 HEAD 已不存在（被删/改名未被 rename 检测覆盖）
                    stats.dangling_modifies += 1

        if has_py_change:
            stats.code_commits += 1
        if produced_modifies:
            stats.modifies_commits += 1

    # Issue 存根节点
    issues_out = [
        {"id": issue_id(repo_name, n), "number": n, "stub": True}
        for n in sorted(issue_numbers)
    ]

    return {
        "commits": commits_out,
        "issues": issues_out,
        "modifies": modifies_out,
        "touches": touches_out,
        "fixes": fixes_out,
    }


def _diff_items(repo: git.Repo, commit):
    """返回该提交相对首父（根提交相对空树）的 diff item 列表，
    始终以 commit 为新侧（b_blob）。"""
    kwargs = dict(create_patch=True, M=True)
    if commit.parents:
        parent = commit.parents[0]
        return parent.diff(commit, **kwargs)
    try:
        empty = repo.tree(EMPTY_TREE_SHA)
        return empty.diff(commit, **kwargs)
    except Exception:
        # 兜底：反向 diff（commit 相对空树，-R 使其成为新侧）
        return commit.diff(EMPTY_TREE_SHA, R=True, **kwargs)


def _extract_fixes(
    commit,
    repo_name: str,
    cid: str,
    fixes_out: list[tuple[str, str, dict]],
    issue_numbers: set[int],
) -> None:
    message = commit.message or ""
    title = message.splitlines()[0] if message else ""

    # (commit, issue_number) → pattern；显式 fixes 优先于 pr_ref
    pending: dict[int, str] = {}

    for m in _FIXES_RE.finditer(message):
        keyword = m.group(1).lower()
        number = int(m.group(2))
        pending[number] = keyword  # 显式命中总是覆盖

    explicit = set(pending)
    for m in _PR_REF_RE.finditer(title):
        number = int(m.group(1))
        if number not in explicit:
            pending.setdefault(number, "pr_ref")

    for number, pattern in pending.items():
        issue_numbers.add(number)
        fixes_out.append((cid, issue_id(repo_name, number), {"pattern": pattern}))
