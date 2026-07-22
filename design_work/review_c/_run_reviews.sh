#!/usr/bin/env bash
# Phase C 独立审查并行编排器（opencode headless）。
# 主审 big-pickle 全段；关键段加 deepseek-flash 二审（qwen 因 429 配额耗尽不可用，见 verify-c.md）。
set -u
cd "C:/Users/nirvana/Desktop/代码库知识图谱/design_work/review_c" || exit 2
LOG="_progress.log"
: > "$LOG"

# 关键段（契约/密钥/并发最重）二审集合
KEY="B_router_banding C_router_premise F_llm_client J_context_route K1_server_rewrite_premise K2_server_event_focus L_server_apichat"

run_one() {
  local model="$1" seg="$2" tag="$3"
  local out="out_${seg}_${tag}.txt"
  if [ -s "$out" ]; then echo "SKIP $seg $tag (exists)" >> "$LOG"; return; fi
  local t0=$(date +%s)
  timeout 280 opencode run --pure -m "$model" "$(cat ${seg}.txt)" > "$out" 2>"err_${seg}_${tag}.txt"
  local rc=$? t1=$(date +%s)
  echo "DONE $seg $tag rc=$rc $((t1-t0))s bytes=$(wc -c < "$out" 2>/dev/null)" >> "$LOG"
}
export -f run_one
export LOG

# 构造任务清单：主审 big-pickle 全段 + 关键段 deepseek 二审
JOBS=()
for f in *.txt; do
  seg="${f%.txt}"
  case "$seg" in _*|out_*|err_*) continue;; esac
  JOBS+=("bp|$seg|bp")
done
for seg in $KEY; do JOBS+=("ds|$seg|ds"); done

printf '%s\n' "${JOBS[@]}" | xargs -P 4 -I{} bash -c '
  IFS="|" read -r m s t <<< "{}"
  case "$m" in
    bp) model="opencode/big-pickle";;
    ds) model="opencode/deepseek-v4-flash-free";;
  esac
  run_one "$model" "$s" "$t"
'
echo "ALL_DONE $(date +%H:%M:%S)" >> "$LOG"
