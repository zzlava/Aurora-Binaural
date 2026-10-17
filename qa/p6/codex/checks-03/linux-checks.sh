#!/bin/bash
set -uo pipefail
export PATH=/root/.local/bin:/root/.cargo/bin:$PATH
mkdir -p /workspace
find /workspace -name '._*' -type f -delete
tar xf /source.tar -C /workspace
cd /workspace
cat /etc/os-release > /evidence/environment.txt
for tool in uv cargo rustup node npm ffmpeg ffprobe; do
  "$tool" --version >> /evidence/environment.txt 2>&1 || "$tool" -version >> /evidence/environment.txt 2>&1
done
bash scripts/setup.sh > /evidence/setup-1.log 2>&1
setup_result=$?
printf 'setup_first=%s\n' "$setup_result" | tee /evidence/results.txt
if [ "$setup_result" != 0 ]; then tail -50 /evidence/setup-1.log; exit "$setup_result"; fi
bash scripts/setup.sh > /evidence/setup-2.log 2>&1
printf 'setup_second=%s\n' "$?" | tee -a /evidence/results.txt
.venv/bin/pytest -ra tests/app > /evidence/pytest.log 2>&1
printf 'pytest=%s\n' "$?" | tee -a /evidence/results.txt
.venv/bin/ruff check src tests/app > /evidence/ruff.log 2>&1
printf 'ruff=%s\n' "$?" | tee -a /evidence/results.txt
.venv/bin/mypy src tests/app > /evidence/mypy.log 2>&1
printf 'mypy=%s\n' "$?" | tee -a /evidence/results.txt
npm --prefix web run lint > /evidence/npm-lint.log 2>&1
printf 'npm_lint=%s\n' "$?" | tee -a /evidence/results.txt
npm --prefix web run typecheck > /evidence/npm-typecheck.log 2>&1
printf 'npm_typecheck=%s\n' "$?" | tee -a /evidence/results.txt
cat /evidence/results.txt
tail -60 /evidence/pytest.log
