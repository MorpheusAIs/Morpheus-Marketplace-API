"""Check the CA configuration of the API image.

Used at image build time (Dockerfile) and by the image check workflow.
Exits non-zero if any check fails.
"""

import os
import ssl
import subprocess
import sys

BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
BLOCKED = ("emsign", "aaa certificate services")
MIN_CERTIFI = (2026, 10, 19)
ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE")

errors = []


def subject_text(cert):
    return ", ".join(f"{k}={v}" for rdn in cert.get("subject", ()) for k, v in rdn)


def check_store(name, certs, expected_count=None):
    subjects = [subject_text(c) for c in certs]
    blocked = [s for s in subjects if any(b in s.lower() for b in BLOCKED)]
    print(f"{name}: {len(subjects)} certificates, {len(blocked)} blocked")
    for s in blocked:
        print(f"  blocked: {s}")
    if blocked:
        errors.append(f"{name} contains blocked roots")
    if len(subjects) < 100:
        errors.append(f"{name} has too few certificates ({len(subjects)})")
    if expected_count is not None and len(subjects) != expected_count:
        errors.append(f"{name}: decoded {len(subjects)} of {expected_count} certificates")


# System bundle, decoded by subject.
with open(BUNDLE, encoding="ascii") as f:
    pem_count = f.read().count("-----BEGIN CERTIFICATE-----")
ctx = ssl.create_default_context(cafile=BUNDLE)
check_store("system bundle", ctx.get_ca_certs(), pem_count)

# Environment points the Python clients at the system bundle.
for var in ENV_VARS:
    value = os.environ.get(var)
    print(f"{var}={value}")
    if value is None or os.path.realpath(value) != os.path.realpath(BUNDLE):
        errors.append(f"{var} does not point at {BUNDLE}")

# certifi version.
import certifi  # noqa: E402

print(f"certifi {certifi.__version__}")
if tuple(int(p) for p in certifi.__version__.split(".")[:3]) < MIN_CERTIFI:
    errors.append(f"certifi {certifi.__version__} is older than 2026.10.19")

# What the clients actually resolve.
import botocore.session  # noqa: E402
import httpx  # noqa: E402
import requests  # noqa: E402

req_verify = requests.Session().merge_environment_settings(
    "https://example.com", {}, None, True, None
)["verify"]
print(f"requests verify={req_verify}")
if not isinstance(req_verify, str) or os.path.realpath(req_verify) != os.path.realpath(BUNDLE):
    errors.append("requests does not use the system bundle")

boto_bundle = botocore.session.get_session().get_config_variable("ca_bundle")
print(f"botocore ca_bundle={boto_bundle}")
if not boto_bundle or os.path.realpath(boto_bundle) != os.path.realpath(BUNDLE):
    errors.append("botocore does not use the system bundle")

httpx_ctx = httpx.create_ssl_context(trust_env=True)
check_store("httpx context", httpx_ctx.get_ca_certs(), pem_count)

# Stripe uses its own CA file unless configured; the app's Stripe module sets it.
# Import only (no network, no keys). A placeholder DATABASE_URL satisfies settings
# validation; nothing connects to it.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://check:check@127.0.0.1:1/check")
sys.path.insert(0, os.environ.get("APP_DIR", "/app"))
import stripe  # noqa: E402
import src.services.stripe_webhook_service  # noqa: E402,F401

print(f"stripe ca_bundle_path={stripe.ca_bundle_path}")
if os.path.realpath(stripe.ca_bundle_path) != os.path.realpath(BUNDLE):
    errors.append("stripe does not use the system bundle")

# Subprocess cases for the Stripe CA resolution (import only; no network, no keys).
_PROBE = (
    "import os, sys\n"
    "sys.path.insert(0, os.environ.get('APP_DIR', '/app'))\n"
    "if os.environ.get('CHECK_NO_CA_FILES') == '1':\n"
    "    os.path.isfile = lambda p: False\n"
    "import stripe\n"
    "try:\n"
    "    import src.services.stripe_webhook_service\n"
    "except RuntimeError as e:\n"
    "    print('RAISED:' + str(e)); sys.exit(0)\n"
    "print('PATH:' + stripe.ca_bundle_path)\n"
)


def _probe(extra_env):
    env = dict(os.environ, **extra_env)
    out = subprocess.run([sys.executable, "-c", _PROBE], env=env,
                         capture_output=True, text=True, timeout=120)
    lines = [l for l in out.stdout.splitlines() if l.startswith(("PATH:", "RAISED:"))]
    return out.returncode, (lines[-1] if lines else "")


rc, res = _probe({"SSL_CERT_FILE": ""})
print(f"stripe with blank SSL_CERT_FILE: {res!r}")
if rc != 0 or not res.startswith("PATH:") or \
        os.path.realpath(res[5:] or ".") != os.path.realpath(BUNDLE):
    errors.append("stripe does not resolve the system bundle when SSL_CERT_FILE is blank")

rc, res = _probe({"SSL_CERT_FILE": "   "})
print(f"stripe with whitespace SSL_CERT_FILE: {res!r}")
if rc != 0 or not res.startswith("PATH:") or \
        os.path.realpath(res[5:] or ".") != os.path.realpath(BUNDLE):
    errors.append("stripe does not resolve the system bundle when SSL_CERT_FILE is whitespace")

rc, res = _probe({"CHECK_NO_CA_FILES": "1"})
print(f"stripe with no CA files: {res!r}")
if rc != 0 or not res.startswith("RAISED:No system CA bundle found"):
    errors.append("stripe module import does not fail when no CA bundle exists")

if errors:
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
print("CA checks passed")
