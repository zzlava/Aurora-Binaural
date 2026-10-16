#!/bin/bash
# Claude 侧使用：向派单队列写入一个任务（原子写入）。
# 用法：tools/enqueue.sh <action> [key=value ...]
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
Q="$ROOT/.aurora/queue"; mkdir -p "$Q"
action="$1"; shift
id="$(date +%Y%m%d-%H%M%S)-$action"
tmp="$Q/.$id.tmp"
{ echo "action=$action"; for kv in "$@"; do echo "$kv"; done; } > "$tmp"
mv "$tmp" "$Q/$id.job"
echo "$id"
