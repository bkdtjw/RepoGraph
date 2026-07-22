#!/usr/bin/env bash
# 配额恢复后正规送审：qwen/glm-5.2 主审全段 + C/K1 关键段 qwen3.8-max-preview 二审。
# 逐段执行、每段 timeout 300s、429/挂起等 60s 重试一次，仍失败如实记 FAIL 继续。
set -u
cd "C:/Users/nirvana/Desktop/代码库知识图谱/design_work/review_c" || exit 2
LOG="_qwen_progress.log"
: > "$LOG"

SEGS="A_router_route B_router_banding C_router_premise D_metrics_graph E_metrics_cyclo F_llm_client G_repocard_det H_repocard_summary I_enrich J_context_route K1_server_rewrite_premise K2_server_event_focus L_server_apichat M_appjs_a N_appjs_b"
KEY2="C_router_premise K1_server_rewrite_premise"

run_seg() {
  local model="$1" seg="$2" tag="$3"
  local out="out_${seg}_${tag}.txt" err="err_${seg}_${tag}.txt"
  local t0=$(date +%s)
  timeout 300 opencode run --pure -m "$model" "$(cat ${seg}.txt)" > "$out" 2>"$err"
  local rc=$?
  if [ $rc -ne 0 ] || [ ! -s "$out" ]; then
    echo "RETRY $seg $tag rc=$rc empty=$([ -s "$out" ] && echo no || echo yes) wait60" >> "$LOG"
    sleep 60
    t0=$(date +%s)
    timeout 300 opencode run --pure -m "$model" "$(cat ${seg}.txt)" > "$out" 2>"$err"
    rc=$?
  fi
  local t1=$(date +%s)
  if [ $rc -ne 0 ] || [ ! -s "$out" ]; then
    echo "FAIL $seg $tag rc=$rc $((t1-t0))s" >> "$LOG"
  else
    echo "DONE $seg $tag $((t1-t0))s bytes=$(wc -c < "$out")" >> "$LOG"
  fi
}

echo "=== glm-5.2 主审 15 段 $(date +%H:%M:%S) ===" >> "$LOG"
for seg in $SEGS; do run_seg "qwen/glm-5.2" "$seg" "glm"; done

echo "=== qwen3.8-max-preview 二审 C/K1 $(date +%H:%M:%S) ===" >> "$LOG"
for seg in $KEY2; do run_seg "qwen/qwen3.8-max-preview" "$seg" "max"; done

echo "ALL_DONE $(date +%H:%M:%S)" >> "$LOG"
