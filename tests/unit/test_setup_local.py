import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("setup_local", os.path.join(ROOT, "scripts", "setup_local.py"))
setup_local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_local)


def test_set_key_replaces_commented_and_missing_keys():
    assert setup_local.set_key("A=1\nB=2\n", "B", "9") == "A=1\nB=9\n"
    assert setup_local.set_key("# TOKEN=old\n", "TOKEN", "new") == "TOKEN=new\n"
    assert setup_local.set_key("A=1", "NEW", "x") == "A=1\nNEW=x\n"
    assert setup_local.set_key("A=1\nA=2\n", "A", "9") == "A=9\nA=2\n"        # first match only


def test_setup_creates_matching_tokens_and_is_idempotent(tmp_path):
    project = tmp_path / "crypto-bot"
    (project / "laravel" / "database").mkdir(parents=True)
    (project / "scripts").mkdir()
    (project / ".env.example").write_text("TRADING_MODE=PAPER\nCONTROL_PLANE_TOKEN=CHANGE_THIS_LONG_RANDOM_SECRET\n"
                                          "CONTROL_PLANE_URL=http://127.0.0.1:8000\n", encoding="utf-8")
    (project / "laravel" / ".env.example").write_text("APP_ENV=production\nAPP_DEBUG=false\nDB_DATABASE=/x\n"
                                                      "TRADING_BOT_INTERNAL_TOKEN=CHANGE_THIS\nSESSION_SECURE_COOKIE=true\n",
                                                      encoding="utf-8")
    script = project / "scripts" / "setup_local.py"
    script.write_bytes(open(os.path.join(ROOT, "scripts", "setup_local.py"), "rb").read())

    assert subprocess.run([sys.executable, str(script)], capture_output=True, text=True).returncode == 0
    engine = (project / ".env").read_text(encoding="utf-8")
    laravel = (project / "laravel" / ".env").read_text(encoding="utf-8")
    token = [l for l in engine.splitlines() if l.startswith("CONTROL_PLANE_TOKEN=")][0].split("=", 1)[1]
    assert len(token) >= 32 and "CHANGE_THIS" not in token
    assert f"TRADING_BOT_INTERNAL_TOKEN={token}" in laravel
    assert "APP_DEBUG=true" in laravel and "SESSION_SECURE_COOKIE=false" in laravel
    assert (project / "laravel" / "database" / "database.sqlite").exists()

    subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert (project / ".env").read_text(encoding="utf-8") == engine                 # rerun changes nothing
    r = subprocess.run([sys.executable, str(script), "--force"], capture_output=True, text=True)
    assert r.returncode == 0 and (project / ".env").read_text(encoding="utf-8") != engine   # --force rotates
