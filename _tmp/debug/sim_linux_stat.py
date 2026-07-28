"""手工模拟 Linux/CI 环境跑 deploy dry-run:
   - uname -s → Linux（走 GNU 分支）
   - stat 换成 GNU 语义仿真：`stat -f '%Lp' F` → 打 '?p' 且 exit 0（coreutils 对未知
     格式符的行为，正是把 `stat -f … || stat -c …` 短路链坑掉的原因）；
     `stat -c '%a' F` → 真权限位。
用法: python3 _tmp/debug/sim_linux_stat.py [<要塞进 fixture 的 deploy 脚本路径>]
"""
import importlib.util, os, stat as stmod, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("t", REPO / "tests" / "test_deploy_mac_mini.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

override = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
tmp = Path(tempfile.mkdtemp())
work, fake_bin, _ = mod._prepare_deploy_repo(tmp, REPO)
if override:
    (work / "scripts" / "deploy-mac-mini.sh").write_text(override.read_text(encoding="utf-8"), encoding="utf-8")
home, vault = mod._prepare_home(tmp, env_mode=0o600)

def ex(p, body):
    p.write_text(body, encoding="utf-8"); p.chmod(p.stat().st_mode | stmod.S_IXUSR)

ex(fake_bin / "uname", '#!/bin/bash\necho Linux\n')
ex(fake_bin / "stat", '''#!/bin/bash
# GNU coreutils 仿真:-f 是 filesystem 模式，未知格式符打 '?'，仍 exit 0
if [[ "$1" == "-f" ]]; then echo "?p"; exit 0; fi
if [[ "$1" == "-c" && "$2" == "%a" ]]; then exec /usr/bin/stat -f "%Lp" "$3"; fi
exec /usr/bin/stat "$@"
''')

r = mod._run_deploy_dry_run(work, fake_bin, home, vault, include_defuddle=True)
print("returncode =", r.returncode)
print((r.stdout + r.stderr).strip())
