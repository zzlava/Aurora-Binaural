#!/bin/bash
# Claude 在虚拟机里提交前必须运行：拒绝把 LFS 规则匹配的文件以非指针形式写进暂存区。
exec python3 - "$@" <<'PY'
import subprocess, sys
G = ["git", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false", "-c", "filter.lfs.clean=cat", "-c", "filter.lfs.smudge=cat"]
names = subprocess.run(G + ["diff", "--cached", "--name-only", "-z", "--diff-filter=AM"], capture_output=True, check=True).stdout.split(b"\0")
names = [n for n in names if n]
if not names:
    sys.exit(0)
attrs = subprocess.run(G + ["check-attr", "-z", "filter", "--stdin"], input=b"\0".join(names) + b"\0", capture_output=True, check=True).stdout.split(b"\0")
lfs = [attrs[i] for i in range(0, len(attrs) - 2, 3) if attrs[i + 2] == b"lfs"]
bad = 0
if not lfs:
    print("guard: checked 0 LFS paths, ok"); sys.exit(0)
idx = dict()
for line in subprocess.run(G + ["ls-files", "-s", "-z", "--"] + [n.decode() for n in lfs], capture_output=True, check=True).stdout.split(b"\0"):
    if line:
        meta, path = line.split(b"\t", 1)
        idx[path] = meta.split()[1]
out = subprocess.run(G + ["cat-file", "--batch-check=%(objectname) %(objectsize)"], input=b"\n".join(idx.values()) + b"\n", capture_output=True, check=True).stdout.split(b"\n")
sizes = {l.split()[0]: int(l.split()[1]) for l in out if l}
for path, sha in idx.items():
    # LFS 指针都很小（< 200 字节）；超过 1 KB 视为原始大文件
    if sizes.get(sha, 10**9) > 1024:
        print("拒绝：", path.decode(), "匹配 LFS 规则，但暂存区里不是 LFS 指针（", sizes.get(sha), "字节）"); bad = 1
print(f"guard: checked {len(lfs)} LFS paths, {'FAIL' if bad else 'ok'}")
sys.exit(bad)
PY
