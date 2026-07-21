-- RepoGraph 存储层 DDL（文档 §6.2 关系表 + §6.3 AGE 初始化）——部署形态预留。
-- 单 PostgreSQL 16 实例三引擎：AGE（图）、pgvector（向量）、普通关系表。
-- 本地 v0.1 默认后端为 models.GraphStore；本文件在切换到 AGE 部署时执行。
-- 幂等：全部对象使用 IF NOT EXISTS / CREATE OR REPLACE 语义，可重复执行。

-- ===========================================================================
-- §6.2 扩展与关系表
-- ===========================================================================
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

-- 向量块：source_ref 构成"块 ↔ 图节点"双向锚（§7.4 混合检索关键）
CREATE TABLE IF NOT EXISTS chunk (
  id           TEXT PRIMARY KEY,          -- {repo}::chunk::{source_type}::{hash}
  repo         TEXT NOT NULL,
  source_type  TEXT NOT NULL CHECK (source_type IN ('docstring','readme','commit_msg','issue','code')),
  source_ref   TEXT NOT NULL,             -- 关联的图节点 ID
  content      TEXT NOT NULL,
  token_count  INT,
  embedding    vector(1024)               -- 维度配置化（§6.4，默认 bge-m3=1024）
);
CREATE INDEX IF NOT EXISTS chunk_embedding_idx  ON chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunk_source_ref_idx ON chunk (source_ref);

-- 实体链接：短名/别名 → 节点 ID（§7.1 词面候选）
CREATE TABLE IF NOT EXISTS symbol_alias (
  alias      TEXT NOT NULL,
  entity_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,               -- exact | suffix | concept_alias
  PRIMARY KEY (alias, entity_id)
);
CREATE INDEX IF NOT EXISTS symbol_alias_alias_idx ON symbol_alias (alias);

-- 索引水位：增量更新起点（§6.5）
CREATE TABLE IF NOT EXISTS index_meta (
  repo         TEXT PRIMARY KEY,
  last_commit  TEXT NOT NULL,
  indexed_at   TIMESTAMPTZ NOT NULL
);

-- 语义抽取暂存（§5.2/§5.3）：校验通过、对齐前暂存，不直接落图。
-- 字段与抽取输出契约一致；各环节拒绝原因进 repograph stats(extraction_reject_by_reason)。
CREATE TABLE IF NOT EXISTS concept_staging (
  id            BIGSERIAL PRIMARY KEY,
  repo          TEXT NOT NULL,
  batch_id      TEXT NOT NULL,            -- 抽取批次（每 20 条 commit 一批，§5.1）
  source_ref    TEXT NOT NULL,            -- 来源图节点 ID（commit/issue/module）
  edge_type     TEXT NOT NULL CHECK (edge_type IN ('DESCRIBES','PROPOSES','IMPLEMENTS')),
  name          TEXT NOT NULL,
  ctype         TEXT NOT NULL CHECK (ctype IN ('design_decision','domain_concept','constraint')),
  description   TEXT NOT NULL,
  quote         TEXT,                      -- evidence 原文子串
  quote_valid   BOOLEAN NOT NULL DEFAULT FALSE,   -- 子串校验结果（不成立则 confidence 减半）
  target_ref    TEXT,                      -- IMPLEMENTS 目标符号 ID（存在性校验后）
  confidence    REAL NOT NULL,             -- < semantic_confidence_min(0.6) 过滤
  reject_reason TEXT,                       -- 被拒原因（schema/quote/target/confidence）
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS concept_staging_batch_idx ON concept_staging (repo, batch_id);

-- 概念对齐裁决审计（§5.4）：blocking + LLM matching 的可抽检记录
CREATE TABLE IF NOT EXISTS align_audit (
  id            BIGSERIAL PRIMARY KEY,
  left_ref      TEXT NOT NULL,            -- staging 概念标识
  right_ref     TEXT NOT NULL,            -- 已有规范概念 ID
  similarity    REAL,                      -- blocking 余弦相似度
  verdict       TEXT NOT NULL CHECK (verdict IN ('same','different','unsure')),
  rationale     TEXT,                      -- LLM 裁决理由
  canonical_id  TEXT,                      -- verdict=same 时合并后的规范 Concept ID
  decided_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- §6.3 AGE 图初始化
-- 注意：以下三句依赖会话级 search_path，通常由部署脚本按会话执行；此处保留为
-- 文档级 DDL。create_graph 非幂等，重复执行前应先判存在（部署脚本负责）。
-- ===========================================================================
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT create_graph('repograph');

-- 属性 GIN 索引（AGE 将每个 label 落为 graph schema 下的表）
CREATE INDEX IF NOT EXISTS function_props_idx ON repograph."Function" USING gin (properties);
CREATE INDEX IF NOT EXISTS commit_props_idx   ON repograph."Commit"   USING gin (properties);

-- ===========================================================================
-- §7.2 影响面分析参数化模板（PREPARE 形态示例；depth 上界由服务端白名单拼入）
-- run_cypher（store/age.py）在启用 AGE 后端时按此形态封装 PREPARE/EXECUTE。
-- ===========================================================================
-- PREPARE q_callers(agtype) AS
-- SELECT * FROM cypher('repograph', $$
--   MATCH (t:Function {id: $fid})
--   OPTIONAL MATCH (caller:Function)-[:CALLS*1..3]->(t)
--   WITH t, collect(DISTINCT caller) AS callers
--   UNWIND (callers + [t]) AS c
--   OPTIONAL MATCH (m:Module)-[:CONTAINS]->(c)
--   RETURN c.id, c.qualname, c.is_endpoint, c.http_method, c.route_path, m.path
-- $$, $1) AS (id agtype, qualname agtype, is_endpoint agtype,
--            http_method agtype, route_path agtype, path agtype);
