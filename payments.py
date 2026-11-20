"""Server-side fine checkout and payment reconciliation."""
import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pymysql
from flask import Blueprint, current_app, g, jsonify, request
from database import database, one, run
from library import signed_in

payments = Blueprint("payments", __name__, url_prefix="/payments")


def setting(name):
    """Read a setting from the environment, or from the .env file in the project folder."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = Path(current_app.root_path, ".env")
    if path.exists():
        for line in path.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == name:
                return value.strip().strip('"').strip("'")
    return ""


def api(path, data=None, extra_headers=None):
    """Call the payment provider's API with the saved keys and return its answer."""
    key, secret = setting("RZP_KEY_ID"), setting("RZP_KEY_SECRET")
    if not key or not secret:
        raise ValueError("Payment service is not configured.")
    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
    req = Request("https://api.razorpay.com/v1/" + path,
                  data=json.dumps(data).encode() if data is not None else None,
                  headers={"Authorization": "Basic " + credentials, "Content-Type": "application/json",
                           "User-Agent": "Shelfora/1.0"})
    for name, value in (extra_headers or {}).items():
        req.add_header(name, value)
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def owned_fine(kind, fine_id, lock=False):
    """Find the signed-in member's own unpaid fine that can be paid, or None."""
    suffix = " FOR UPDATE" if lock else ""
    if kind == "manual":
        return one("SELECT id,amount FROM manual_fines WHERE id=%s AND user_id=%s AND voided_at IS NULL" + suffix,
                   (fine_id, g.user["id"]))
    if kind == "loan":
        return one("""SELECT id,fine AS amount FROM loans
                      WHERE id=%s AND user_id=%s AND returned IS NOT NULL AND fine>0
                      AND NOT EXISTS(SELECT 1 FROM loan_fine_waivers w WHERE w.loan_id=loans.id)""" + suffix,
                   (fine_id, g.user["id"]))
    return None


def checkout_details(record):
    """The details the payment window needs to open."""
    return {"key": setting("RZP_KEY_ID"), "order_id": record["order_id"],
            "amount": record["amount_paise"], "currency": "INR"}


@payments.post("/start")
@signed_in
def start():
    """Begin paying a fine: create (or reuse) the payment order."""
    if not setting("RZP_KEY_ID") or not setting("RZP_KEY_SECRET"):
        return jsonify(error="Payment is not configured. Please contact the librarian."), 503
    kind = request.form.get("kind")
    fine_id = request.form.get("fine_id", type=int)
    try:
        fine = owned_fine(kind, fine_id, lock=True)
        if not fine or fine["amount"] <= 0:
            database().rollback()
            return jsonify(error="This fine cannot be paid."), 404
        record = one("SELECT * FROM fine_payments WHERE fine_kind=%s AND fine_id=%s FOR UPDATE",
                     (kind, fine_id))
        if record:
            database().commit()
            if record["status"] == "paid":
                return jsonify(error="This fine has already been paid."), 409
            if not record["order_id"]:
                return jsonify(error="This payment needs librarian review before another attempt."), 409
            return jsonify(checkout_details(record))
        amount = int(fine["amount"] * 100)
        payment_id = run("""INSERT INTO fine_payments(user_id,fine_kind,fine_id,amount_paise)
                            VALUES(%s,%s,%s,%s)""", (g.user["id"], kind, fine_id, amount))
        database().commit()
        try:
            order = api("orders", {"amount": amount, "currency": "INR", "receipt": f"fine_{payment_id}"})
            if (not isinstance(order, dict) or not re.fullmatch(r"order_[A-Za-z0-9]+", str(order.get("id", ""))) or order.get("amount") != amount
                    or order.get("currency") != "INR"):
                raise ValueError("Unexpected order response")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError):
            run("UPDATE fine_payments SET status='needs_review' WHERE id=%s", (payment_id,))
            database().commit()
            current_app.logger.warning("Payment order could not be confirmed, record %s", payment_id)
            return jsonify(error="The payment order could not be confirmed. Please contact the librarian before retrying."), 503
        run("UPDATE fine_payments SET order_id=%s,status='pending' WHERE id=%s", (order["id"], payment_id))
        database().commit()
        return jsonify(key=setting("RZP_KEY_ID"), order_id=order["id"], amount=amount, currency="INR")
    except pymysql.IntegrityError:
        database().rollback()
        return jsonify(error="Another checkout is being started. Refresh and try again."), 409


@payments.post("/verify")
@signed_in
def verify():
    """After the member pays, check the payment signature and confirm it with the provider."""
    order_id = request.form.get("order_id", "")
    payment_id = request.form.get("payment_id", "")
    signature = request.form.get("signature", "")
    record = one("SELECT * FROM fine_payments WHERE order_id=%s AND user_id=%s",
                 (order_id, g.user["id"]))
    if not record or not re.fullmatch(r"pay_[A-Za-z0-9]+", payment_id) or not signature.isascii():
        return jsonify(error="Payment details could not be verified."), 400
    expected = hmac.new(setting("RZP_KEY_SECRET").encode(),
                        f"{record['order_id']}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify(error="Payment signature could not be verified."), 400
    return confirm_capture(record, payment_id)


def confirm_capture(record, payment_id):
    """Ask the provider whether the money was really received and matches the fine, then mark it paid."""
    try:
        if not isinstance(payment_id, str) or not re.fullmatch(r"pay_[A-Za-z0-9]+", payment_id):
            return jsonify(error="Payment details could not be verified."), 400
        payment = api("payments/" + payment_id)
        if not isinstance(payment, dict):
            raise ValueError("Unexpected payment response")
        if (payment.get("id") != payment_id or payment.get("order_id") != record["order_id"]
                or payment.get("amount") != record["amount_paise"] or payment.get("currency") != "INR"):
            return jsonify(error="Payment details did not match this fine. Contact the librarian."), 409
        if payment.get("status") != "captured":
            return jsonify(error="Payment is awaiting confirmation. Check its status again shortly."), 409
        run("""UPDATE fine_payments SET status='paid',payment_id=%s,paid_at=CURRENT_TIMESTAMP
               WHERE id=%s AND status!='paid'""", (payment_id, record["id"]))
        database().commit()
        existing = one("SELECT status,payment_id FROM fine_payments WHERE id=%s", (record["id"],))
        if existing["status"] != "paid" or existing["payment_id"] != payment_id:
            return jsonify(error="Payment needs librarian review."), 409
        return jsonify(paid=True)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, pymysql.IntegrityError):
        database().rollback()
        current_app.logger.warning("Unable to confirm payment for record %s", record["id"])
        return jsonify(error="Payment confirmation is unavailable. Check its status again shortly."), 503


@payments.post("/check")
@signed_in
def check():
    """Look up a payment's latest status, for when the member closed the window early."""
    kind, fine_id = request.form.get("kind"), request.form.get("fine_id", type=int)
    record = one("SELECT * FROM fine_payments WHERE fine_kind=%s AND fine_id=%s AND user_id=%s",
                 (kind, fine_id, g.user["id"]))
    if not record or not record["order_id"]:
        return jsonify(error="No payment order was found."), 404
    if record["status"] == "paid":
        return jsonify(paid=True)
    try:
        result = api("orders/" + record["order_id"] + "/payments")
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ValueError("Unexpected payment list")
        captured = [p for p in result["items"] if isinstance(p, dict) and p.get("status") == "captured"]
        if not captured:
            return jsonify(error="No captured payment found yet. Please try again shortly."), 409
        return confirm_capture(record, captured[0]["id"])
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
        return jsonify(error="Payment confirmation is unavailable. Please try again shortly."), 503

@payments.get('/receipt/<kind>/<int:fine_id>')
@signed_in
def receipt(kind, fine_id):
    """Printable receipt for a paid fine."""
    from flask import abort, render_template
    record = one("SELECT * FROM fine_payments WHERE user_id=%s AND fine_kind=%s AND fine_id=%s AND status='paid'", (g.user['id'], kind, fine_id))
    if not record:
        abort(404)
    if kind == 'manual':
        detail = one('SELECT reason AS title FROM manual_fines WHERE id=%s AND user_id=%s', (fine_id, g.user['id']))
    elif kind == 'loan':
        detail = one('SELECT b.title FROM loans l JOIN books b ON b.id=l.book_id WHERE l.id=%s AND l.user_id=%s', (fine_id, g.user['id']))
    else:
        abort(404)
    if not detail:
        abort(404)
    refund = one("SELECT status FROM payment_refunds WHERE fine_payment_id=%s", (record["id"],))
    return render_template('receipt.html', payment=record, detail=detail, refund=refund)
