"""Config + env loading. Stdlib only."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path=None):
    """Minimal .env reader. Does not override variables already in the environment."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def load_config(path=None):
    path = path or os.path.join(ROOT, "config", "pipeline.json")
    with open(path) as fh:
        return json.load(fh)


def api_key(required=True):
    """Editor key. mabl permissions API keys per use case, and DataTable
    read/write is an editor-role action."""
    load_dotenv()
    key = os.environ.get("MABL_API_KEY", "").strip()
    if not key and required:
        sys.exit(
            "MABL_API_KEY is not set (needs an Editor key).\n"
            "  local: copy .env.example to .env and paste it\n"
            "  CI:    gh secret set MABL_API_KEY --repo <owner>/<repo>"
        )
    return key


def deploy_key(required=True):
    """Deployment Trigger / CI-CD key, for POST /events/deployment and
    GET /execution/result/event/{id}. An Editor key is not permissioned for
    those, so this is deliberately separate. Falls back to MABL_API_KEY for
    single-key setups."""
    load_dotenv()
    key = os.environ.get("MABL_DEPLOY_KEY", "").strip()
    if key:
        return key
    fallback = os.environ.get("MABL_API_KEY", "").strip()
    if fallback:
        print("  ! MABL_DEPLOY_KEY not set - falling back to MABL_API_KEY.")
        print("    If the trigger 401s, that is why: it needs a Deployment")
        print("    Trigger or CI/CD Integration key, not an Editor key.")
        return fallback
    if required:
        sys.exit("MABL_DEPLOY_KEY is not set (needs a Deployment Trigger or CI/CD key).")
    return ""


def out_dir():
    d = os.path.join(ROOT, "out")
    os.makedirs(d, exist_ok=True)
    return d
