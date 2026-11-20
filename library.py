"""Shared rules and helpers: library time, form checks, sign-in decorators and loan, reservation and hold logic."""
from datetime import datetime, timedelta, timezone
from functools import wraps
import re

from flask import abort, flash, g, redirect, request, url_for

from database import STOCK, database, one, rows, run


def library_now():
    """The current time in India (IST), which the library uses for every date and session."""
    return datetime.now(timezone(timedelta(hours=5, minutes=30), "IST"))


class FormError(ValueError):
    """A validation message that may be shown to the person submitting a form."""


def field(name, label, limit, required=True):
    """Read one text field from the form, and reject it if it is missing or too long."""
    value = request.form.get(name, "").strip()
    if (required and not value) or len(value) > limit:
        raise FormError(f"Enter {label.lower()} of up to {limit} characters.")
    return value


def valid_email(value):
    """A simple check that an email address looks right and is not too long."""
    return len(value) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None


def member_fields():
    """Read and check the name and email from a sign-up or profile form."""
    name = field("name", "a name", 100)
    email = request.form.get("email", "").strip().lower()
    if not valid_email(email):
        raise FormError("Enter a valid email address.")
    return name, email


def contact_fields(existing=None):
    """Read and check the optional phone and address fields, keeping old values for fields not on the form."""
    existing = existing or {}
    def optional(name, label, limit):
        if name not in request.form:
            return existing.get(name) or None
        return field(name, label, limit, required=False) or None
    phone = optional("phone", "a phone number", 30)
    if phone and (not re.fullmatch(r"\+?[0-9() .-]+", phone) or not 7 <= sum(c in "0123456789" for c in phone) <= 15):
        raise FormError("Enter a valid phone number with 7–15 digits. A leading + is allowed.")
    address = optional("address", "an address", 500)
    line2 = optional("address_line2", "an address line", 200)
    region = optional("region", "a state or region", 100)
    city = optional("city", "a city", 100)
    postal = optional("postal_code", "a postal code", 12)
    if postal and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]{1,11}", postal):
        raise FormError("Enter a valid postal code using letters, numbers, spaces or hyphens.")
    return phone, address, line2, region, city, postal


def new_password():
    """Read the new password from the form and check its length."""
    password = request.form.get("password", "")
    if not 10 <= len(password) <= 128:
        raise FormError("Use a password between 10 and 128 characters.")
    return password


def signed_in(fn):
    """Decorator: send visitors who are not signed in to the sign-in page."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.user:
            flash("Sign in to use your library membership.", "info")
            return redirect(url_for("login", next=request.path if request.method == "GET" else "/shelf"))
        return fn(*args, **kwargs)
    return wrapper


def staff(fn):
    """Decorator: only librarians (administrators) may open the page."""
    @signed_in
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def get_book(book_id, lock=False):
    """Load one catalogue book with its availability, or show "not found". Pass lock=True to hold it while changing stock."""
    if lock:
        one("SELECT id FROM books WHERE id=%s FOR UPDATE", (book_id,))
    book = one(STOCK + " WHERE b.id=%s AND b.archived=0", (book_id,))
    if not book:
        abort(404)
    return book


def lock_member(user_id):
    """Hold the member's row while changing their records, and make sure the account is still active."""
    user = one("SELECT id FROM users WHERE id=%s AND active=1 FOR UPDATE", (user_id,))
    if not user:
        raise FormError("This account is no longer active.")


def active_request(user_id, book_id):
    """The member's open request for this book (pending, approved or waitlisted), if any."""
    return one("""SELECT * FROM reservations WHERE user_id=%s AND book_id=%s
        AND status IN ('pending','approved','waitlisted')""", (user_id, book_id))


def borrowed(user_id, book_id):
    """The member's current loan of this book, if they still have it."""
    return one("SELECT id FROM loans WHERE user_id=%s AND book_id=%s AND returned IS NULL", (user_id, book_id))


RESERVATIONS = """SELECT r.*, b.title, b.author, b.seed_key, u.name, u.email FROM reservations r
    JOIN books b ON b.id=r.book_id JOIN users u ON u.id=r.user_id"""
LOANS = """SELECT l.*, b.title, b.author, b.seed_key, u.name, u.email, c.barcode AS copy_barcode FROM loans l
    JOIN books b ON b.id=l.book_id JOIN users u ON u.id=l.user_id LEFT JOIN book_copies c ON c.id=l.copy_id"""


def due_info(loan):
    """Add overdue days and the fine so far to a loan record."""
    end = loan["returned"] or library_now().date()
    days = max((end - loan["due"]).days, 0)
    return {**loan, "overdue_days": days, "amount": loan["fine"] if loan["returned"] else loan["daily_rate"] * days}


def refresh_holds():
    """Expire holds and offer available copies in queue order, under book locks."""
    now = library_now().replace(tzinfo=None)
    books = rows("SELECT DISTINCT book_id FROM reservations WHERE (status='approved' AND hold_until IS NOT NULL AND hold_until<=%s) OR status='waitlisted' ORDER BY book_id", (now,))
    for entry in books:
        book_id = entry["book_id"]
        one("SELECT id FROM books WHERE id=%s FOR UPDATE", (book_id,))
        run("UPDATE reservations SET status='expired' WHERE book_id=%s AND status='approved' AND hold_until IS NOT NULL AND hold_until<=%s", (book_id, now))
        item = one(STOCK + " WHERE b.id=%s AND b.archived=0", (book_id,))
        if not item:
            continue
        waiting = rows("SELECT id FROM reservations WHERE book_id=%s AND status='waitlisted' ORDER BY requested_at,id LIMIT %s FOR UPDATE", (book_id, max(0, item["available"])))
        for hold in waiting:
            run("UPDATE reservations SET status='approved',hold_until=%s WHERE id=%s", (now + timedelta(days=3), hold["id"]))
    database().commit()
