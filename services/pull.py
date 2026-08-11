import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
import datetime
from collections import defaultdict
from dotenv import load_dotenv
from rapidfuzz import fuzz
from core.db import (upsert_transactions, fetch_transactions, load_category_map, seed_category_map,
                     get_pending_transactions_in_window, delete_transactions_by_ids)
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("pull.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

load_dotenv()

PLAID_ENVS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

def _build_client():
    from core.db import get_credential
    client_id = get_credential("plaid_client_id") or os.getenv("PLAID_CLIENT_ID")
    env        = get_credential("plaid_env")       or os.getenv("PLAID_ENV", "sandbox")
    secret     = get_credential("plaid_secret")    or os.getenv(f"PLAID_SECRET_{env.upper()}")
    configuration = plaid.Configuration(
        host=PLAID_ENVS[env.lower()],
        api_key={"clientId": client_id, "secret": secret}
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration)), env

CSV_FILE = "data/transactions.csv"
FIELDNAMES = ["id", "date", "name", "amount", "category", "pending", "institution"]

def load_existing_ids(user_id: int) -> set:
    from core.db import fetch_transactions
    return {t["id"] for t in fetch_transactions(user_id)}


def build_content_index(existing_txns: list) -> dict:
    """
    Index existing transactions by (plaid_account_id, rounded_amount) so we can
    do a fast O(1) bucket lookup, then a cheap date+name check per candidate.
    Returns: {(plaid_account_id, amount): [{date, name, pending}, ...]}
    """
    idx = defaultdict(list)
    for t in existing_txns:
        acct = t.get("plaid_account_id")
        if not acct:
            continue
        key = (acct, round(float(t["amount"]), 2))
        date_val = t["date"]
        if isinstance(date_val, str):
            date_val = datetime.date.fromisoformat(date_val[:10])
        idx[key].append({
            "date":    date_val,
            "name":    t["name"].lower().strip(),
            "pending": bool(t.get("pending", False)),
        })
    return idx


def is_content_duplicate(t: dict, content_index: dict) -> tuple[bool, str]:
    """
    Check whether an incoming Plaid transaction is a duplicate of an existing
    PENDING transaction in the DB.

    The only safe content-match rule: if we already have a PENDING transaction
    for the same account, same amount, close date, and similar name — and Plaid
    is now returning a new ID — that is a pending→posted settlement where Plaid
    dropped the old ID and issued a new one.

    We deliberately do NOT match against existing POSTED transactions, because
    two separate posted purchases of the same amount at the same merchant are
    both legitimate and should both be stored.
    """
    key = (t["account_id"], round(float(t["amount"]), 2))
    candidates = content_index.get(key, [])
    if not candidates:
        return False, ""

    tx_date = t["date"]
    if isinstance(tx_date, str):
        tx_date = datetime.date.fromisoformat(str(tx_date)[:10])
    tx_name = t["name"].lower().strip()

    for c in candidates:
        # Only match against pending records — posted-vs-posted is two real purchases
        if not c["pending"]:
            continue

        days_diff = abs((tx_date - c["date"]).days)
        if days_diff > 5:
            continue

        sim = fuzz.token_sort_ratio(tx_name, c["name"])
        if sim >= 80:
            return True, f"pending→posted: existing pending matched (±{days_diff}d, name sim {sim}%)"

    return False, ""


def _field(obj, key):
    """Read a field from a Plaid model (dict-style) or plain dict, defensively."""
    try:
        return obj[key]
    except Exception:
        return getattr(obj, key, None)


def extract_enrichment(t) -> dict:
    """Pull Plaid enrichment fields off a transaction into plain JSON-safe values.

    Plaid model enums (e.g. TRANSFER_OUT, VERY_HIGH) stringify cleanly. Every
    field is optional — older items or unentitled plans may omit PFC or
    counterparties, so each read is guarded.
    """
    pfc = _field(t, "personal_finance_category")
    primary = detailed = confidence = None
    if pfc is not None:
        primary = _field(pfc, "primary")
        detailed = _field(pfc, "detailed")
        confidence = _field(pfc, "confidence_level")

    counterparties = []
    for c in (_field(t, "counterparties") or []):
        name = _field(c, "name")
        ctype = _field(c, "type")
        if name or ctype:
            counterparties.append({
                "name": name,
                "type": str(ctype) if ctype is not None else None,
            })

    def _s(v):
        return str(v) if v is not None else None

    return {
        "pfc_primary":         _s(primary),
        "pfc_detailed":        _s(detailed),
        "pfc_confidence":      _s(confidence),
        "payment_channel":     _s(_field(t, "payment_channel")),
        "plaid_merchant_name": _field(t, "merchant_name"),
        "counterparties":      json.dumps(counterparties) if counterparties else None,
    }


def fetch_all_transactions(client, access_token: str, start_date, end_date) -> list:
    all_transactions = []
    offset = 0

    while True:
        try:
            request = TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=end_date,
                options=TransactionsGetRequestOptions(
                    count=500,
                    offset=offset,
                    # Personal Finance Category is the primary signal for
                    # transfer/income/spend classification downstream.
                    include_personal_finance_category=True,
                )
            )
            response = client.transactions_get(request)
            transactions = response["transactions"]
            total = response["total_transactions"]
            all_transactions.extend(transactions)
            log.info(f"Fetched {len(all_transactions)}/{total} transactions")

            if len(all_transactions) >= total:
                break

            offset += len(transactions)

        except plaid.ApiException as e:
            log.error(f"Plaid API error: {e.body}")
            raise

    return all_transactions


def save_transactions(transactions: list, institution: str,
                      existing_ids: set, account_map: dict, user_id: int,
                      content_index: dict | None = None) -> int:
    """
    Filter and insert new transactions.

    Two-stage dedup:
      1. ID-based:      skip any transaction_id already in the DB (fast set lookup).
      2. Content-based: skip transactions whose account+amount+date+name match an
                        existing record — catches Plaid re-issuing a new ID for the
                        same logical transaction (e.g. pending→posted ID swap, or a
                        full-sync returning a record we already stored under a prior ID).
    """
    registered_account_ids = set(account_map.keys())
    category_map = load_category_map(user_id)
    new_transactions = []
    skipped_content = 0

    for t in transactions:
        if t["transaction_id"] in existing_ids:
            continue
        if t["account_id"] not in registered_account_ids:
            continue
        if content_index:
            is_dup, reason = is_content_duplicate(t, content_index)
            if is_dup:
                log.debug(f"Content-dup skipped: '{t['name']}' ${t['amount']} {t['date']} — {reason}")
                skipped_content += 1
                continue
        new_transactions.append(t)

    if skipped_content:
        log.info(f"{institution}: skipped {skipped_content} content-duplicate(s)")

    if not new_transactions:
        return 0

    records = [{
        "id":               t["transaction_id"],
        "date":             str(t["date"]),
        "name":             t["name"],
        "amount":           t["amount"],
        "category":         category_map.get(
                                t["category"][0] if t["category"] else "", "Uncategorized"
                            ),
        "pending":          bool(t["pending"]),
        "institution":      account_map.get(t["account_id"], {}).get("institution_name", institution),
        "plaid_account_id": t["account_id"],
        "user_id":          user_id,
        **extract_enrichment(t),
    } for t in new_transactions]

    upsert_transactions(records)

    # Keep content_index current so later institutions in the same sync don't
    # insert a record that an earlier institution already inserted this run.
    if content_index is not None:
        for r in records:
            key = (r["plaid_account_id"], round(float(r["amount"]), 2))
            date_val = datetime.date.fromisoformat(r["date"][:10])
            content_index[key].append({
                "date":    date_val,
                "name":    r["name"].lower().strip(),
                "pending": r["pending"],
            })

    return len(new_transactions)


def main(user_id: int, full_sync: bool = False):
    from core.db import get_connected_account_tokens, get_latest_transaction_date, set_last_synced_at
    seed_category_map(user_id)

    client, env = _build_client()
    log.info(f"Starting transaction pull ({env}) for user {user_id}")

    end_date = datetime.date.today()
    if full_sync:
        start_date = end_date - datetime.timedelta(days=730)  # Plaid max is ~24 months
        log.info(f"Full sync requested — pulling from {start_date} (24-month Plaid limit)")
    else:
        last = get_latest_transaction_date(user_id)
        if last:
            # Roll back 30 days from the latest stored transaction so that any
            # transactions that were pending during the last sync (and later posted
            # with a new Plaid transaction_id) are not missed.
            latest = datetime.date.fromisoformat(last)
            start_date = latest - datetime.timedelta(days=30)
        else:
            # No transactions yet — pull as far back as Plaid allows (24 months)
            start_date = end_date - datetime.timedelta(days=730)
    log.info(f"Pulling from {start_date} to {end_date}")

    all_existing = fetch_transactions(user_id)
    existing_ids = {t["id"] for t in all_existing}
    content_index = build_content_index(all_existing)
    log.info(f"Found {len(existing_ids)} existing transactions")

    accounts = get_connected_account_tokens(user_id)
    if not accounts:
        log.warning("No connected accounts found — add accounts in Settings")
        return

    from core.db import get_plaid_account_map, list_plaid_accounts, upsert_plaid_accounts
    from plaid.model.accounts_get_request import AccountsGetRequest

    # Sync plaid_accounts for any institution that has none yet
    for account in accounts:
        existing = list_plaid_accounts(account["id"])
        if not existing:
            try:
                resp = client.accounts_get(AccountsGetRequest(access_token=account["access_token"]))
                upsert_plaid_accounts(account["id"], [
                    {
                        "account_id":    a["account_id"],
                        "name":          a["name"],
                        "official_name": a.get("official_name"),
                        "mask":          a["mask"],
                        "type":          str(a["type"]),
                        "subtype":       str(a["subtype"]),
                    }
                    for a in resp["accounts"]
                ])
                log.info(f"{account['name']}: synced {len(resp['accounts'])} plaid accounts")
            except Exception as e:
                log.warning(f"{account['name']}: could not sync accounts — {e}")

    account_map = get_plaid_account_map(user_id)
    log.info(f"Account map loaded: {len(account_map)} plaid accounts")

    # ── Fetch /item/get for each connected account and cache health status ───────
    from core.db import upsert_item_health
    from plaid.model.item_get_request import ItemGetRequest
    for account in accounts:
        try:
            item_resp = client.item_get(ItemGetRequest(access_token=account["access_token"]))
            item = item_resp["item"]
            status = item_resp.get("status") or {}
            tx_status = (status.get("transactions") or {}) if isinstance(status, dict) else {}

            error = item.get("error")
            upsert_item_health(account["id"], {
                "error_code":               error.get("error_code") if error else None,
                "error_message":            error.get("error_message") if error else None,
                "consent_expiration_time":  item.get("consent_expiration_time"),
                "last_successful_update":   tx_status.get("last_successful_update"),
                "last_failed_update":       tx_status.get("last_failed_update"),
            })
            log.info(f"{account['name']}: item health stored"
                     + (f" — error: {error.get('error_code')}" if error else ""))
        except Exception as e:
            log.warning(f"{account['name']}: /item/get failed (non-fatal): {e}")

    total_new = 0
    for account in accounts:
        log.info(f"Pulling {account['name']}...")
        try:
            transactions = fetch_all_transactions(client, account["access_token"], start_date, end_date)

            # ── Clean up stale pending transactions ──────────────────────────
            # Plaid removes a pending transaction and replaces it with a new posted
            # transaction_id when it settles. Since we only INSERT, the old pending
            # row lingers. Delete any pending transactions for this account whose ID
            # Plaid no longer returns — they've been superseded.
            plaid_ids_in_window = {t["transaction_id"] for t in transactions}
            # Use the account_ids that appeared in this fetch to scope the cleanup
            account_plaid_ids = list({t["account_id"] for t in transactions}
                                     & set(account_map.keys()))
            pending_in_db = get_pending_transactions_in_window(
                user_id, account_plaid_ids,
                str(start_date), str(end_date)
            )
            stale_ids = [p["id"] for p in pending_in_db if p["id"] not in plaid_ids_in_window]
            if stale_ids:
                deleted = delete_transactions_by_ids(stale_ids)
                log.info(f"{account['name']}: removed {deleted} stale pending transaction(s)")
                # Remove from existing_ids so their successors can be inserted
                existing_ids -= set(stale_ids)

            saved = save_transactions(transactions, account["name"], existing_ids, account_map, user_id, content_index)
            total_new += saved
            log.info(f"{account['name']}: {saved} new transactions saved")
        except Exception as e:
            log.error(f"{account['name']} pull failed: {e}")
            continue

    log.info(f"Done — {total_new} total new transactions saved")
    set_last_synced_at(user_id, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

if __name__ == "__main__":
    main()