import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.products import Products
from plaid.model.country_code import CountryCode

load_dotenv()

PLAID_ENVS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

app = Flask(__name__)
# Fail closed: this key signs the Plaid-link token that binds a link session to a
# user_id. A predictable default would let an attacker forge it and attach a Plaid
# account to another user.
_flask_secret = os.environ.get("FLASK_SECRET_KEY")
if not _flask_secret:
    raise RuntimeError(
        "FLASK_SECRET_KEY environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )
app.secret_key = _flask_secret

_LINK_TOKEN_MAX_AGE = 3600  # 1 hour


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.secret_key)


def sign_user_id(user_id: int) -> str:
    """Create a signed, time-limited token encoding user_id for the link URL."""
    return _signer().dumps(user_id, salt="plaid-link")


def _verify_token(token: str) -> int | None:
    """Returns user_id if token is valid and unexpired, else None."""
    try:
        return _signer().loads(token, salt="plaid-link", max_age=_LINK_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _build_client():
    from core.db import get_credential
    client_id = get_credential("plaid_client_id") or os.getenv("PLAID_CLIENT_ID")
    env        = get_credential("plaid_env")       or os.getenv("PLAID_ENV", "sandbox")
    secret     = get_credential("plaid_secret")    or os.getenv(f"PLAID_SECRET_{env.upper()}")
    configuration = plaid.Configuration(
        host=PLAID_ENVS[env.lower()],
        api_key={"clientId": client_id, "secret": secret}
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


@app.route("/")
def index():
    token = request.args.get("t", "")
    user_id = _verify_token(token)
    if not user_id:
        return "<h3 style='font-family:monospace;padding:40px'>Invalid or expired link.<br>Please regenerate from Settings.</h3>", 403
    # Pass the signed token through to JS — never expose raw user_id to the browser
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
    <title>spend. — connect account</title>
    <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0a0a0a; color: #e0e0e0; font-family: 'DM Mono', monospace; display: flex; align-items: center; justify-content: center; height: 100vh; }
        .card { background: #111; border: 1px solid #1f1f1f; border-radius: 12px; padding: 40px; width: 400px; }
        .logo { font-size: 22px; font-weight: 500; margin-bottom: 32px; }
        .logo span { color: #c8ff00; }
        label { font-size: 10px; letter-spacing: 0.15em; text-transform: uppercase; color: #555; display: block; margin-bottom: 8px; }
        input, select { width: 100%; background: #0a0a0a; border: 1px solid #1f1f1f; border-radius: 8px; padding: 10px 14px; color: #e0e0e0; font-family: 'DM Mono', monospace; font-size: 13px; margin-bottom: 20px; outline: none; appearance: none; }
        input:focus, select:focus { border-color: #333; }
        button { width: 100%; background: #c8ff00; color: #0a0a0a; border: none; border-radius: 8px; padding: 12px; font-family: 'DM Mono', monospace; font-size: 13px; font-weight: 500; cursor: pointer; }
        button:hover { background: #d4ff33; }
        button:disabled { background: #1f1f1f; color: #444; cursor: not-allowed; }
        .status { margin-top: 20px; font-size: 12px; color: #555; line-height: 1.6; white-space: pre-wrap; }
        .status.success { color: #4caf82; }
        .status.error { color: #ff5c5c; }
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">spend<span>.</span></div>
        <label>Institution name</label>
        <input type="text" id="institution" placeholder="e.g. Capital One, Discover" />
        <label>Account type</label>
        <select id="account_type">
            <option value="bank">Bank</option>
            <option value="credit_card">Credit card</option>
            <option value="p2p">P2P (Venmo, PayPal)</option>
        </select>
        <button id="btn" onclick="startLink()">Connect with Plaid</button>
        <div class="status" id="status"></div>
    </div>
    <script>
        const SIGNED_TOKEN = "{{ signed_token }}";

        async function startLink() {
            const institution = document.getElementById('institution').value.trim();
            const account_type = document.getElementById('account_type').value;
            if (!institution) {
                setStatus('Please enter an institution name.', 'error');
                return;
            }

            document.getElementById('btn').disabled = true;
            setStatus('Initializing...');

            try {
                const res = await fetch('/create_link_token');
                const data = await res.json();
                if (data.error) { setStatus(data.error, 'error'); document.getElementById('btn').disabled = false; return; }

                Plaid.create({
                    token: data.link_token,
                    onSuccess: async (public_token, metadata) => {
                        setStatus('Saving connection...');
                        const r = await fetch('/exchange_token', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ public_token, institution, account_type, signed_token: SIGNED_TOKEN })
                        });
                        const d = await r.json();
                        if (d.error) {
                            setStatus(d.error, 'error');
                            document.getElementById('btn').disabled = false;
                        } else {
                            setStatus(`✓ ${d.institution} connected successfully.\\n\\nYou can close this window and refresh Settings.`, 'success');
                        }
                    },
                    onExit: (err) => {
                        if (err) setStatus(err.display_message || 'Cancelled.', 'error');
                        document.getElementById('btn').disabled = false;
                    }
                }).open();
            } catch (e) {
                setStatus('Failed to initialize: ' + e.message, 'error');
                document.getElementById('btn').disabled = false;
            }
        }

        function setStatus(msg, type = '') {
            const el = document.getElementById('status');
            el.textContent = msg;
            el.className = 'status ' + type;
        }
    </script>
</body>
</html>
    """, signed_token=token)


@app.route("/create_link_token")
def create_link_token():
    try:
        plaid_client = _build_client()
        response = plaid_client.link_token_create(LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id="local-dev-user"),
            client_name="Spend",
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en"
        ))
        return jsonify({"link_token": response["link_token"]})
    except plaid.ApiException as e:
        return jsonify({"error": str(e.body)}), 400


@app.route("/exchange_token", methods=["POST"])
def exchange_token():
    try:
        from core.db import add_connected_account, get_connected_account_by_name
        public_token  = request.json["public_token"]
        institution   = request.json["institution"]
        account_type  = request.json.get("account_type", "bank")
        signed_token  = request.json.get("signed_token", "")

        user_id = _verify_token(signed_token)
        if not user_id:
            return jsonify({"error": "Session expired — please reopen the link page from Settings."}), 403

        existing = get_connected_account_by_name(institution, user_id)
        if existing:
            return jsonify({"error": f"'{institution}' is already connected. Remove it in Settings first."}), 409

        plaid_client = _build_client()
        response = plaid_client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=public_token)
        )
        access_token = response["access_token"]
        connected_id = add_connected_account(institution, account_type, access_token, user_id)

        accounts_resp = plaid_client.accounts_get(AccountsGetRequest(access_token=access_token))
        from core.db import upsert_plaid_accounts
        upsert_plaid_accounts(connected_id, [
            {
                "account_id":    a["account_id"],
                "name":          a["name"],
                "official_name": a.get("official_name"),
                "mask":          a["mask"],
                "type":          str(a["type"]),
                "subtype":       str(a["subtype"]),
            }
            for a in accounts_resp["accounts"]
        ])

        return jsonify({"institution": institution})

    except plaid.ApiException as e:
        return jsonify({"error": str(e.body)}), 400


if __name__ == "__main__":
    from core.db import get_credential
    env = get_credential("plaid_env") or os.getenv("PLAID_ENV", "sandbox")
    print(f"\n  spend. — connect account")
    print(f"  environment: {env}")
    print(f"  open http://localhost:5000\n")
    app.run(port=5000, debug=False)
