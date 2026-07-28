"""模拟 Linux/CI 环境跑 deploy dry-run（uname=Linux + GNU 语义 stat 仿真）。
用法: python3 sim_linux_stat.py [--mode 600|644] [--script <脚本路径>]"""
import argparse, importlib.util, stat as stmod, subprocess, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser()
ap.add_argument("--mode", default="600")
ap.add_argument("--script", default=None)
a = ap.parse_args()

spec = importlib.util.spec_from_file_location("t", REPO / "tests" / "test_deploy_mac_mini.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

tmp = Path(tempfile.mkdtemp())
work, fake_bin, _ = mod._prepare_deploy_repo(tmp, REPO)
if a.script:  # 换脚本后重新提交，避免"工作区脏"污染 B3 判定
    (work / "scripts" / "deploy-mac-mini.sh").write_text(
        Path(a.script).resolve().read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "override"], cwd=work, check=True, capture_output=True, text=True)
home, vault = mod._prepare_home(tmp, env_mode=int(a.mode, 8))

def ex(p, body):
    p.write_text(body, encoding="utf-8"); p.chmod(p.stat().st_mode | stmod.S_IXUSR)

ex(fake_bin / "uname", '#!/bin/bash\necho Linux\n')
ex(fake_bin / "stat", '''#!/bin/bash
if [[ "$1" == "-f" ]]; then echo "?p"; exit 0; fi          # GNU: -f 是 filesystem 模式，未知格式符打 '?'
if [[ "$1" == "-c" && "$2" == "%a" ]]; then exec /usr/bin/stat -f "%Lp" "$3"; fi
exec /usr/bin/stat "$@"
''')

r = mod._run_deploy_dry_run(work, fake_bin, home, vault, include_defuddle=True)
print("returncode =", r.returncode)
print((r.stdout + r.stderr).strip())
