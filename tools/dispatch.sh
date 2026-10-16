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
#   init-lfs      在主仓库与代理克隆中安装 LFS 钩子，克隆共享主仓库的 LFS 存储
#   prepare-agent 在 .agents/<agent>/ 克隆中基于最新 main 切出 agent/<agent>/* 分支（克隆必须干净）
#   merge         把 agent/* 或 qa/* 分支以 --no-ff 合并到 main（主仓库必须干净；冲突则中止）
#   tag           打阶段标签 pN-accepted 或 vX.Y.Z（不覆盖已有标签）
#   commit-agent  把被中断的代理工作区改动保存为 WIP 提交（只在该代理自己的分支上，遵守 .gitignore 与 LFS）
#   cancel        结束指定任务
# 结果写入 .aurora/results/<id>.log 与 <id>.status。
# 本脚本修改后不会自动生效，需要用户重新启动（修改会出现在 git 提交里，便于审查）。

set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
A="$ROOT/.aurora"; Q="$A/queue"; RUN="$A/running"; RES="$A/results"; DONE="$A/done"
mkdir -p "$Q" "$RUN" "$RES" "$DONE"
REPO_SLUG="zzlava/Aurora-Binaural"
POLL=5
CURSOR_FALLBACK_MODEL="grok-4.7-high"   # Cursor 本周额度内仍可用的模型
export PATH="$HOME/.cargo/bin:/opt/homebrew/opt/rustup/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cursor/bin:$PATH"

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
  for t in git git-lfs gh agent agy codex cargo rustc python3 uv ffmpeg brew; do
    printf '%-8s ' "$t"; command -v "$t" || echo MISSING
  done
  echo "== versions"
  git --version 2>&1; git lfs version 2>&1; gh --version 2>&1 | head -n1
  agent --version 2>&1 | head -n3; agy --version 2>&1 | head -n3
  rustc --version 2>&1; python3 --version 2>&1; ffmpeg -version 2>&1 | head -n1
  echo "== gh auth"; gh auth status 2>&1
  echo "== git"; git -C "$ROOT" remote -v; git -C "$ROOT" log --oneline -n3
  echo "== agent status"; agent status 2>&1 | head -n 20
  echo "== agent models"; agent models 2>&1 | head -n 80
  echo "== agent --help"; agent --help 2>&1 | head -n 100
  echo "== agy --help"; agy --help 2>&1 | head -n 100
  echo "== codex --version"; codex --version 2>&1 | head -n 3
  echo "== codex login status"; codex login status 2>&1 | head -n 5
  echo "== codex exec --help"; codex exec --help 2>&1 | head -n 80
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

run_codex_owner(){ # prompt；Codex 作为应用层负责人（用户 2026-10-17 授权，接替额度用尽的 Antigravity）
  command -v codex >/dev/null || { echo "找不到 Codex CLI: codex"; return 15; }
  codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C "$PWD" \
    "你是 Codex，负责 Aurora Binaural 的应用层（接替 Antigravity）。任务书中的所有规则（目录范围、只在当前分支提交、不推送）都适用于你。提交信息末尾使用 'Agent: Codex'。

$1"
}

run_codex(){ # prompt；在当前目录（代理克隆）执行
  command -v codex >/dev/null || { echo "找不到 Codex CLI: codex"; return 15; }
  codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C "$PWD" \
    "你是 Codex，本次代替 Cursor 执行下面的任务书。所有规则（目录范围、只在当前分支提交、不推送）都适用于你。提交信息末尾使用 'Agent: Codex (for Cursor)'。

$1"
}

do_agent(){ # agent task branch [model]
  ag="$1"; task="$2"; branch="$3"; model="${4:-auto}"
  dir="$ROOT/.agents/$ag"
  [ -d "$dir/.git" ] || { echo "缺少代理克隆：$dir"; return 10; }
  cur="$(git -C "$dir" rev-parse --abbrev-ref HEAD)"
  [ "$cur" = "$branch" ] || { echo "分支不一致：克隆当前为 ${cur}，任务要求 $branch"; return 11; }
  prompt="$(cat "$ROOT/$task")"
  cd "$dir" || return 12
  case "$ag" in
    cursor)
      command -v agent >/dev/null || { echo "找不到 Cursor CLI: agent"; return 13; }
      tmp="$RES/.cursor-$$.out"
      for m in "$model" "$CURSOR_FALLBACK_MODEL"; do
        echo "[dispatch] cursor model=$m"
        agent -p --force --trust --model "$m" --output-format text "$prompt" 2>&1 | tee "$tmp"
        rc=${PIPESTATUS[0]}
        [ "$rc" -eq 0 ] && { rm -f "$tmp"; return 0; }
        # 只是该模型不在本周额度内：换 Cursor 仍可用的模型，不算 Cursor 用尽
        if grep -Eqi 'not included in your weekly usage' "$tmp" && [ "$m" != "$CURSOR_FALLBACK_MODEL" ]; then
          echo "[dispatch] 模型 $m 不在本周额度内，改用 Cursor 的 $CURSOR_FALLBACK_MODEL"; continue
        fi
        break
      done
      # 只有 Cursor 整体用尽时才交给 Codex（用户规则）
      if grep -Eqi "hit your usage limit|usage limit reached|out of usage|not included in your weekly usage" "$tmp"; then
        rm -f "$tmp"
        echo "[dispatch] Cursor 额度已用尽，按用户规则改由 Codex CLI 在同一克隆与分支上执行"
        run_codex "$prompt"; return $?
      fi
      rm -f "$tmp"; return "$rc" ;;
    codex)
      echo "[dispatch] codex (application layer owner)"; run_codex_owner "$prompt"; return $? ;;
    antigravity)
      command -v agy >/dev/null || { echo "找不到 Antigravity CLI: agy"; return 13; }
      agy --dangerously-skip-permissions --print-timeout 3h -p "$prompt" ;;
  esac
}

do_init_lfs(){
  command -v git-lfs >/dev/null || { echo "未安装 git-lfs"; return 30; }
  git -C "$ROOT" lfs install --local || return 31
  for ag in cursor antigravity codex; do
    d="$ROOT/.agents/$ag"; [ -d "$d/.git" ] || continue
    git -C "$d" config lfs.storage ../../../.git/lfs
    git -C "$d" lfs install --local || return 31
  done
  echo "lfs ok"
}

do_prepare_agent(){ # agent branch
  ag="$1"; branch="$2"; d="$ROOT/.agents/$ag"
  [ -d "$d/.git" ] || git clone -q "$ROOT" "$d" || return 40
  git -C "$d" remote set-url origin ../..
  [ -z "$(git -C "$d" status --porcelain)" ] || { echo "克隆有未提交的修改，拒绝切换"; git -C "$d" status --short; return 41; }
  git -C "$d" fetch -q origin || return 42
  if git -C "$d" rev-parse --verify -q "refs/heads/$branch" >/dev/null; then
    git -C "$d" checkout -q "$branch" || return 43
    echo "已切换到已有分支 $branch（未重置）"
  else
    git -C "$d" checkout -q -b "$branch" origin/main || return 43
    echo "已基于 origin/main 创建 $branch"
  fi
  git -C "$d" log --oneline -n1
}

do_merge(){ # branch
  b="$1"
  [ "$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "主仓库不在 main"; return 50; }
  [ -z "$(git -C "$ROOT" status --porcelain)" ] || { echo "主仓库有未提交修改，拒绝合并"; git -C "$ROOT" status --short; return 51; }
  git -C "$ROOT" rev-parse --verify -q "refs/heads/$b" >/dev/null || { echo "分支不存在：$b"; return 52; }
  if ! git -C "$ROOT" merge --no-ff --no-edit -m "merge: $b (accepted by Claude)" "$b"; then
    git -C "$ROOT" merge --abort; echo "合并冲突，已中止"; return 53
  fi
  git -C "$ROOT" log --oneline -n3
}

do_tag(){ # name
  t="$1"
  git -C "$ROOT" rev-parse --verify -q "refs/tags/$t" >/dev/null && { echo "标签已存在：$t"; return 60; }
  git -C "$ROOT" tag -a "$t" -m "$t" main && echo "已打标签 $t"
}

do_commit_agent(){ # agent branch
  ag="$1"; branch="$2"; d="$ROOT/.agents/$ag"
  [ -d "$d/.git" ] || { echo "缺少代理克隆：$d"; return 10; }
  [ "$(git -C "$d" rev-parse --abbrev-ref HEAD)" = "$branch" ] || { echo "分支不一致"; return 11; }
  git -C "$d" add -A || return 70
  if git -C "$d" diff --cached --quiet; then echo "没有需要保存的改动"; return 0; fi
  case "$ag" in cursor) who="Cursor";; antigravity) who="Antigravity";; esac
  git -C "$d" commit -q -m "wip($ag): snapshot of interrupted run (saved by dispatcher on Claude's request)" -m "Agent: $who" || return 71
  git -C "$d" log --oneline -n1; git -C "$d" lfs ls-files 2>/dev/null | head -n 50
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
      ag="$(getf "$job" agent)"; task="$(getf "$job" task)"; branch="$(getf "$job" branch)"; model="$(getf "$job" model)"
      case "$ag" in cursor|antigravity|codex) ;; *) echo "非法 agent: $ag" > "$out"; code=20 ;; esac
      if [ $code -eq 0 ] && [ -n "$model" ] && ! [[ "$model" =~ ^[A-Za-z0-9._-]+$ ]]; then echo "非法模型名：$model" > "$out"; code=25; fi
      if [ $code -eq 0 ]; then
        if ! [[ "$task" =~ ^docs/tasks/[A-Za-z0-9._-]+\.md$ ]] || [ ! -f "$ROOT/$task" ]; then echo "非法或不存在的任务书：$task" > "$out"; code=21; fi
      fi
      if [ $code -eq 0 ]; then
        if ! [[ "$branch" =~ ^agent/$ag/[A-Za-z0-9._-]+$ ]]; then echo "非法分支：$branch" > "$out"; code=22; fi
      fi
      if [ $code -eq 0 ] && agent_busy "$ag" "$id"; then echo "$ag 已有任务在运行" > "$out"; code=23; fi
      if [ $code -eq 0 ]; then log "[$id] 派单 $ag ← $task ($branch)"; do_agent "$ag" "$task" "$branch" "$model" > "$out" 2>&1; code=$?; fi ;;
    init-lfs)     do_init_lfs > "$out" 2>&1; code=$? ;;
    prepare-agent)
      ag="$(getf "$job" agent)"; branch="$(getf "$job" branch)"
      if [[ "$ag" =~ ^(cursor|antigravity|codex)$ ]] && [[ "$branch" =~ ^agent/$ag/[A-Za-z0-9._-]+$ ]]; then
        if agent_busy "$ag" "$id"; then echo "$ag 有任务在运行" > "$out"; code=23; else do_prepare_agent "$ag" "$branch" > "$out" 2>&1; code=$?; fi
      else echo "非法参数：$ag $branch" > "$out"; code=22; fi ;;
    merge)
      b="$(getf "$job" branch)"
      if [[ "$b" =~ ^(agent/(cursor|antigravity|codex)|qa)/[A-Za-z0-9._-]+$ ]]; then do_merge "$b" > "$out" 2>&1; code=$?; else echo "非法分支：$b" > "$out"; code=22; fi ;;
    tag)
      t="$(getf "$job" name)"
      if [[ "$t" =~ ^(p[0-9]+-accepted|v[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then do_tag "$t" > "$out" 2>&1; code=$?; else echo "非法标签：$t" > "$out"; code=22; fi ;;
    commit-agent)
      ag="$(getf "$job" agent)"; branch="$(getf "$job" branch)"
      if [[ "$ag" =~ ^(cursor|antigravity|codex)$ ]] && [[ "$branch" =~ ^agent/$ag/[A-Za-z0-9._-]+$ ]]; then
        if agent_busy "$ag" "$id"; then echo "$ag 有任务在运行" > "$out"; code=23; else do_commit_agent "$ag" "$branch" > "$out" 2>&1; code=$?; fi
      else echo "非法参数：$ag $branch" > "$out"; code=22; fi ;;
    cancel)
      target="$(getf "$job" target)"
      if [[ "$target" =~ ^[A-Za-z0-9._-]+$ ]] && [ -f "$RES/$target.pid" ]; then
        p="$(cat "$RES/$target.pid")"; pkill -TERM -P "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null
        echo "已结束 $target (pid $p)" > "$out"; setstatus "$target" state canceled; mv "$RUN/$target.job" "$DONE/$target.job" 2>/dev/null; rm -f "$RES/$target.pid"
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

log "Aurora 派单器已启动：${ROOT}（pid $$）。Ctrl-C 停止。"
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
