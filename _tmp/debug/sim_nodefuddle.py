"""Linux 仿真 + 无 defuddle + 环境 init.defaultBranch=master（最贴近 CI runner 的组合）"""
import importlib.util, os, stat as stmod, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())
(T / "gitconfig").write_text("[init]\n\tdefaultBranch = master\n", encoding="utf-8")
os.environ["GIT_CONFIG_GLOBAL"] = str(T / "gitconfig"); os.environ["GIT_CONFIG_SYSTEM"] = "/dev/null"
spec = importlib.util.spec_from_file_location("t", REPO / "tests" / "test_deploy_mac_mini.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
tmp = Path(tempfile.mkdtemp())
work, fake_bin, _ = mod._prepare_deploy_repo(tmp, REPO)
home, vault = mod._prepare_home(tmp, env_mode=0o600)
def ex(p, body):
    p.write_text(body, encoding="utf-8"); p.chmod(p.stat().st_mode | stmod.S_IXUSR)
ex(fake_bin / "uname", '#!/bin/bash\necho Linux\n')
ex(fake_bin / "stat", '#!/bin/bash\nif [[ "$1" == "-f" ]]; then echo "?p"; exit 0; fi\nif [[ "$1" == "-c" && "$2" == "%a" ]]; then exec /usr/bin/stat -f "%Lp" "$3"; fi\nexec /usr/bin/stat "$@"\n')
r = mod._run_deploy_dry_run(work, fake_bin, home, vault, include_defuddle=False)
print("returncode =", r.returncode)
print((r.stdout + r.stderr).strip())
