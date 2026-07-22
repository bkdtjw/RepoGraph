#!/usr/bin/env bash
# glm-5.2 主审对真实审查 prompt 恒挂起（>300s 零输出、无完成事件，配额已恢复仍挂）。
# 改由 mandated 授权集内另一模型 qwen3.8-max-preview 送审全 15 段（干净临时目录消除启动开销）。
set -u
cd "/c/Users/nirvana/AppData/Local/Temp/rgreview" || exit 2
LOG="_max_progress.log"; : > "$LOG"
SEGS="A_router_route B_router_banding C_router_premise D_metrics_graph E_metrics_cyclo F_llm_client G_repocard_det H_repocard_summary I_enrich J_context_route K1_server_rewrite_premise K2_server_event_focus L_server_apichat M_appjs_a N_appjs_b"

run_seg() {
  local seg="$1" out="rev_${seg}.txt" err="rev_${seg}.err"
  local t0=$(date +%s)
  timeout 360 opencode run --pure -m qwen/qwen3.8-max-preview "$(cat ${seg}.txt)" > "$out" 2>"$err"
  local rc=$?
  if [ $rc -ne 0 ] || [ ! -s "$out" ]; then
    echo "RETRY $seg rc=$rc wait60" >> "$LOG"; sleep 60; t0=$(date +%s)
    timeout 360 opencode run --pure -m qwen/qwen3.8-max-preview "$(cat ${seg}.txt)" > "$out" 2>"$err"; rc=$?
  fi
  local t1=$(date +%s)
  if [ $rc -ne 0 ] || [ ! -s "$out" ]; then echo "FAIL $seg rc=$rc $((t1-t0))s" >> "$LOG"
  else echo "DONE $seg $((t1-t0))s bytes=$(wc -c < "$out")" >> "$LOG"; fi
}

echo "=== max-preview 全段主审 $(date +%H:%M:%S) ===" >> "$LOG"
for seg in $SEGS; do run_seg "$seg"; done
echo "ALL_DONE $(date +%H:%M:%S)" >> "$LOG"
