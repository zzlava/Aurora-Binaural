#!/bin/bash
# Aurora Binaural 白名单派单器（macOS，兼容系统自带 bash 3.2）
#
# 由用户在 macOS 终端启动：  bash tools/dispatch.sh
# 停止：Ctrl-C（会同时结束本派单器启动的代理进程）
#
# 工作方式：轮询 .aurora/queue/*.job（key=value 文本），只执行下列固定动作，
# 任何字段都经过白名单校验，绝不 eval 或执行队列文件里的任意命令：
#   doctor        检查本机工具、版本、gh 登录状态、代理 CLI 帮助
#   setup-remote  关联私有仓库 zzlava/Aurora-Binaural 并推送（远程已有提交时只抓取、不覆盖）
#   push          推送 main、标签、agent/* 与 qa/* 分支（禁止 force）
#   run-agent     在 .agents/<agent>/ 克隆中，用 docs/tasks/ 下的任务书调用代理 CLI
#   cancel        结束指定任务
# 结果写入 .aurora/results/<id>.log 与 <id>.status。
# 本脚本修改后不会自动生效，需要用户重新启动（修改会出现在 git 提交里，便于审查）。

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
A="$ROOT/.aurora"; Q="$A/queue"; RUN="$A/running"; RES="$A/results"; DONE="$A/done"
mkdir -p "$Q" "$RUN" "$RES" "$DONE"
REPO_SLUG="zzlava/Aurora-Binaural"
POLL=5
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cursor/bin:$PATH"

log(){ printf '%s %s\n' "$(date '+%F %T')" "$*"; }
getf(){ sed -n "s/^$2=//p" "$1" | head -n1; }
setstatus(){ printf '%s=%s\n' "$2" "$3" >> "$RES/$1.status"; }

gitcred(){ # 有 gh 且已登录时，只对本条命令使用 gh 凭据；否则用系统已有的 git 凭据
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    git -C "$ROOT" -c credential.helper= -c 'credential.helper=!gh auth git-credential' "$@"
  else git -C "$ROOT" "$@"; fi
}
gitpush(){ gitcred push "$@"; }

do_doctor(){
  echo "== date"; date
  echo "== sw_vers"; sw_vers 2>&1
  echo "== tools"
  for t in git git-lfs gh agent agy cargo rustc python3 uv ffmpeg brew; do
    printf '%-8s ' "$t"; command -v "$t" || echo MISSING
  done
  echo "== versions"
  git --version 2>&1; git lfs version 2>&1; gh --version 2>&1 | head -n1
  agent --version 2>&1 | head -n3; agy --version 2>&1 | head -n3
  rustc --version 2>&1; python3 --version 2>&1; ffmpeg -version 2>&1 | head -n1
  echo "== gh auth"; gh auth status 2>&1
  echo "== git"; git -C "$ROOT" remote -v; git -C "$ROOT" log --oneline -n3
  echo "== agent --help"; agent --help 2>&1 | head -n 100
  echo "== agy --help"; agy --help 2>&1 | head -n 100
}

do_push(){
  git -C "$ROOT" remote get-url origin >/dev/null 2>&1 || { echo "没有 origin，请先执行 setup-remote"; return 6; }
  gitpush origin main || return 7
  gitpush origin --tags || return 7
  for b in $(git -C "$ROOT" for-each-ref --format='%(refname:short)' refs/heads/agent refs/heads/qa); do
    case "$b" in
      agent/*|qa/*) case "$b" in *[!A-Za-z0-9._/-]*) echo "跳过非法分支名 $b";; *) gitpush origin "$b:$b" || return 7;; esac ;;
    esac
  done
  echo "push ok"
}

do_setup_remote(){
  if command -v git-lfs >/dev/null; then git -C "$ROOT" lfs install --local; else echo "WARN: 未安装 git-lfs（brew install git-lfs），音频与 HRTF 提交前必须安装"; fi
  if ! git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
    git -C "$ROOT" remote add origin "https://github.com/$REPO_SLUG.git" || return 5
  fi
  echo "origin: $(git -C "$ROOT" remote get-url origin)"
  gitcred fetch origin || { echo "无法访问远程（检查 GitHub 凭据：gh auth login 或钥匙串）"; return 8; }
  if git -C "$ROOT" rev-parse --verify -q refs/remotes/origin/main >/dev/null; then
    if ! git -C "$ROOT" merge-base --is-ancestor refs/remotes/origin/main main; then
      echo "远程 main 已有本地没有的提交，已抓取到 origin/main，等待 Claude 合并后再推送（不强推）"
      git -C "$ROOT" log --oneline -n5 refs/remotes/origin/main
      return 9
    fi
  fi
  do_push
}

do_agent(){ # agent task branch
  ag="$1"; task="$2"; branch="$3"
  dir="$ROOT/.agents/$ag"
  [ -d "$dir/.git" ] || { echo "缺少代理克隆：$dir"; return 10; }
  cur="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
  [ "$cur" = "$branch" ] || { echo "分支不一致：克隆当前为 $cur，任务要求 $branch"; return 11; }
  prompt="$(cat "$ROOT/$task")"
  cd "$dir" || return 12
  case "$ag" in
    cursor)
      command -v agent >/dev/null || { echo "找不到 Cursor CLI: agent"; return 13; }
      agent -p --force --output-format text "$prompt" ;;
    antigravity)
      command -v agy >/dev/null || { echo "找不到 Antigravity CLI: agy"; return 13; }
      echo "Antigravity 的非交互参数尚未确认（见 doctor 输出），暂不执行"; return 14 ;;
  esac
}

agent_busy(){ # agent self_id
  for j in "$RUN"/*.job; do
    [ -e "$j" ] || continue
    [ "$(basename "$j" .job)" = "$2" ] && continue
    [ "$(getf "$j" agent)" = "$1" ] && return 0
  done
  return 1
}

handle(){
  id="$1"; job="$RUN/$id.job"; out="$RES/$id.log"
  : > "$RES/$id.status"
  setstatus "$id" state running; setstatus "$id" started "$(date '+%FT%T%z')"
  action="$(getf "$job" action)"
  code=0
  case "$action" in
    doctor)       do_doctor > "$out" 2>&1; code=$? ;;
    push)         do_push > "$out" 2>&1; code=$? ;;
    setup-remote) do_setup_remote > "$out" 2>&1; code=$? ;;
    run-agent)
      ag="$(getf "$job" agent)"; task="$(getf "$job" task)"; branch="$(getf "$job" branch)"
      case "$ag" in cursor|antigravity) ;; *) echo "非法 agent: $ag" > "$out"; code=20 ;; esac
      if [ $code -eq 0 ]; then
        if ! [[ "$task" =~ ^docs/tasks/[A-Za-z0-9._-]+\.md$ ]] || [ ! -f "$ROOT/$task" ]; then echo "非法或不存在的任务书：$task" > "$out"; code=21; fi
      fi
      if [ $code -eq 0 ]; then
        if ! [[ "$branch" =~ ^agent/$ag/[A-Za-z0-9._-]+$ ]]; then echo "非法分支：$branch" > "$out"; code=22; fi
      fi
      if [ $code -eq 0 ] && agent_busy "$ag" "$id"; then echo "$ag 已有任务在运行" > "$out"; code=23; fi
      if [ $code -eq 0 ]; then log "[$id] 派单 $ag ← $task ($branch)"; do_agent "$ag" "$task" "$branch" > "$out" 2>&1; code=$?; fi ;;
    cancel)
      target="$(getf "$job" target)"
      if [[ "$target" =~ ^[A-Za-z0-9._-]+$ ]] && [ -f "$RES/$target.pid" ]; then
        p="$(cat "$RES/$target.pid")"; pkill -TERM -P "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null
        echo "已结束 $target (pid $p)" > "$out"; setstatus "$target" state canceled
      else echo "找不到任务 $target" > "$out"; code=24; fi ;;
    *) echo "非白名单动作：$action" > "$out"; code=29 ;;
  esac
  setstatus "$id" exit "$code"; setstatus "$id" finished "$(date '+%FT%T%z')"
  if [ "$code" -eq 0 ]; then setstatus "$id" state succeeded; else setstatus "$id" state failed; fi
  mv "$job" "$DONE/$id.job" 2>/dev/null; rm -f "$RES/$id.pid"
  log "[$id] $action 结束，exit=$code"
}

cleanup(){
  log "停止派单器，结束运行中的任务…"
  for pf in "$RES"/*.pid; do
    [ -e "$pf" ] || continue
    p="$(cat "$pf")"; pkill -TERM -P "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null
  done
  exit 0
}
trap cleanup INT TERM

log "Aurora 派单器已启动：$ROOT（pid $$）。Ctrl-C 停止。"
command -v caffeinate >/dev/null && { caffeinate -i -w $$ & }
while :; do
  for f in "$Q"/*.job; do
    [ -e "$f" ] || continue
    id="$(basename "$f" .job)"
    case "$id" in ''|*[!A-Za-z0-9._-]*) rm -f "$f"; continue ;; esac
    mv "$f" "$RUN/$id.job" 2>/dev/null || continue
    log "[$id] 收到 $(getf "$RUN/$id.job" action)"
    handle "$id" &
    echo $! > "$RES/$id.pid"
  done
  sleep "$POLL"
done
