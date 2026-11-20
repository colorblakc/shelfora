"""Shelfora web app: page routes for readers (catalogue, accounts, My shelf) and the shared setup."""
import hmac
import os
import secrets
from pathlib import Path
from reading_room import MY_SEATS, room
from assistant import assistant
from payments import payments
from avatars import avatars, AVATARS
from urllib.parse import urlsplit

import pymysql
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import STOCK, configure, database, one, rows, run
from library import (LOANS, RESERVATIONS, FormError, active_request, borrowed, due_info,
                     contact_fields, get_book, library_now, lock_member, member_fields, new_password, signed_in, refresh_holds)

app = Flask(__name__, instance_relative_config=True)
app.config.from_mapping(
    SECRET_KEY=os.environ.get("SHELFORA_SECRET_KEY"),
    MYSQL_HOST="localhost", MYSQL_PORT=3306, MYSQL_USER="shelfora",
    MYSQL_DATABASE="shelfora", MYSQL_PASSWORD="", SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True, MAX_CONTENT_LENGTH=64 * 1024,
)
app.config.from_pyfile("config.py", silent=True)
# A hosted MySQL service hands out plain MYSQL_* variables; use them when present.
for setting in ("HOST", "PORT", "USER", "PASSWORD", "DATABASE"):
    value = os.environ.get("MYSQL_" + setting)
    if value:
        app.config["MYSQL_" + setting] = int(value) if setting == "PORT" else value
app.config.from_prefixed_env(prefix="SHELFORA")
if os.environ.get("MYSQL_HOST") and "MYSQL_SSL" not in app.config:
    app.config["MYSQL_SSL"] = True  # hosted databases require an encrypted connection
if os.environ.get("SHELFORA_BEHIND_PROXY"):
    # Behind a hosting proxy, trust its forwarded address and HTTPS flag.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if not app.secret_key:
    raise RuntimeError("Set a unique SECRET_KEY in instance/config.py. See README.md.")
configure(app)
from librarian import admin
app.register_blueprint(admin)
app.register_blueprint(room)
app.register_blueprint(assistant)
app.register_blueprint(payments)
app.register_blueprint(avatars)


def csrf():
    """Return this visitor's form token, creating one the first time it is needed."""
    if "_token" not in session:
        session["_token"] = secrets.token_urlsafe(32)
    return session["_token"]


@app.before_request
def prepare():
    """Runs before every request: checks the form token on POSTs, expires old holds and loads the signed-in member."""
    if request.endpoint == "static":
        return
    if request.method == "POST":
        token = request.form.get("_token", "")
        if not token.isascii() or not session.get("_token") or not hmac.compare_digest(session["_token"], token):
            abort(400, "This form has expired. Reload the page and try again.")
    refresh_holds()
    g.user = None
    if session.get("uid"):
        g.user = one("SELECT id,name,email,role,phone,address,address_line2,region,city,postal_code,avatar FROM users WHERE id=%s AND active=1", (session["uid"],))
        if not g.user:
            session.clear()


@app.context_processor
def shared():
    """Values every template can use: today's date, the form token, avatar choices and which books have a cover photo."""
    return dict(profile_avatars=AVATARS, csrf=csrf, today=library_now().date(), cover_keys={p.stem for p in Path(app.static_folder, "images").glob("*.jpg")})


@app.after_request
def headers(response):
    """Adds basic security headers, and stops browsers from caching pages (static files may be cached)."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.template_filter("nice_date")
def nice_date(value):
    """Show a date as "5 Mar 2026", or a dash when there is none."""
    return value.strftime("%d %b %Y").lstrip("0") if value else "—"


@app.get("/")
def home():
    """Home page: the 12 newest catalogue entries."""
    books = rows(STOCK + " WHERE b.archived=0 ORDER BY b.id DESC LIMIT 12")
    # Keep the collection's original shelf order within the latest additions.
    return render_template("home.html", books=list(reversed(books)))


@app.get("/about")
def about():
    """About page."""
    return render_template("about.html")


@app.get("/terms")
def terms():
    """Terms of use page."""
    return render_template("policies.html", section="terms")


@app.get("/borrowing")
def borrowing():
    """Borrowing and fines page, showing the current fine per overdue day."""
    rate = one("SELECT daily_rate FROM settings WHERE id=1")["daily_rate"]
    return render_template("policies.html", section="borrowing", rate=rate)


@app.get("/privacy")
def privacy():
    """Privacy page."""
    return render_template("policies.html", section="privacy")


@app.get("/catalogue")
def catalogue():
    """Catalogue page: search, category and availability filters, sorting and 12 books per page."""
    q = request.args.get("q", "").strip()[:200]
    category = request.args.get("category", "").strip()[:60]
    available = request.args.get("available") == "1"
    order = request.args.get("sort", "shelf")
    sort_sql = {"shelf": "b.id", "title": "b.title,b.id", "author": "b.author,b.title,b.id"}
    criteria, args = ["b.archived=0"], []
    if q:
        pattern = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        criteria.append("(" + " OR ".join(f"b.{col} LIKE %s ESCAPE '!'" for col in ("title","author","category","description","isbn","publisher")) + ")")
        args.extend([pattern] * 6)
    if category:
        criteria.append("b.category=%s")
        args.append(category)
    query = STOCK + " WHERE " + " AND ".join(criteria)
    if available:
        query += " HAVING available>0"
    count = one("SELECT COUNT(*) AS n FROM (" + query + ") AS hits", args)["n"]
    pages = max(1, (count + 11) // 12)
    page = max(1, min(request.args.get("page", 1, type=int), pages))
    books = rows(query + " ORDER BY " + sort_sql.get(order, "b.id") + " LIMIT 12 OFFSET %s", [*args, (page - 1) * 12])
    categories = rows("SELECT DISTINCT category FROM books WHERE archived=0 ORDER BY category")
    return render_template("catalogue.html", **locals())


@app.get("/books/<int:book_id>")
def book(book_id):
    """One book's page, with this member's own request or loan for it if they have one."""
    item = get_book(book_id)
    uid = g.user["id"] if g.user else None
    return render_template("book.html", item=item, reservation=active_request(uid, book_id) if uid else None,
                           loan=borrowed(uid, book_id) if uid else None)


def next_page():
    """Work out where to send someone after signing in, allowing only pages inside this site."""
    target = request.values.get("next", "")
    if target.startswith("/") and not target.startswith("//") and all(ord(c) >= 32 for c in target) and "\\" not in target:
        parts = urlsplit(target)
        if parts.path in {"/shelf", "/profile", "/catalogue", "/reading-room", "/desk"} or (
            parts.path.startswith("/books/") and parts.path[7:].isdigit()
        ):
            return target
    return url_for("admin.dashboard" if g.user and g.user["role"] == "admin" else "shelf")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Sign-in page."""
    if g.user:
        return redirect(next_page())
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = one("SELECT * FROM users WHERE email=%s AND active=1", (email,))
        password = request.form.get("password", "")
        if user and len(password) <= 128 and check_password_hash(user["password_hash"], password):
            session.clear()
            session["uid"] = user["id"]
            g.user = user
            return redirect(next_page())
        flash("The email or password didn’t match an active account.", "error")
    return render_template("account.html", mode="login")


@app.route("/join", methods=["GET", "POST"])
def join():
    """Registration page for new members."""
    if g.user:
        return redirect(next_page())
    if request.method == "POST":
        try:
            name, email = member_fields()
            uid = run("INSERT INTO users(name,email,password_hash) VALUES(%s,%s,%s)",
                      (name, email, generate_password_hash(new_password())))
            database().commit()
            session.clear()
            session["uid"] = uid
            flash("Your library membership is ready.", "success")
            return redirect(next_page())
        except FormError as exc:
            flash(str(exc), "error")
        except pymysql.IntegrityError:
            database().rollback()
            flash("This email already has an account. Try signing in.", "error")
    return render_template("account.html", mode="join")


@app.post("/logout")
def logout():
    """Sign the member out."""
    session.clear()
    return redirect(url_for("home"))


@app.route("/profile", methods=["GET","POST"])
@signed_in
def profile():
    """Profile page: change name, email and contact details."""
    if request.method == "POST":
        try:
            name, email = member_fields()
            contact = contact_fields(g.user)
            run("UPDATE users SET name=%s,email=%s,phone=%s,address=%s,address_line2=%s,region=%s,city=%s,postal_code=%s WHERE id=%s", (name, email, *contact, g.user["id"]))
            database().commit()
            flash("Your profile has been updated.", "success")
            return redirect(url_for("profile"))
        except FormError as exc:
            flash(str(exc), "error")
        except pymysql.IntegrityError:
            database().rollback()
            flash("That email is already in use.", "error")
    return render_template("profile.html", section=request.args.get("section", "details") if request.args.get("section", "details") in {"details", "picture", "security"} else "details")


@app.post("/profile/password")
@signed_in
def change_password():
    """Change the password after checking the current one, then keep the member signed in."""
    try:
        current_password = request.form.get("current_password", "")
        with database().cursor() as cursor:
            cursor.execute("SELECT password_hash FROM users WHERE id=%s FOR UPDATE", (g.user["id"],))
            account = cursor.fetchone()
        if not account or len(current_password) > 128 or not check_password_hash(account["password_hash"], current_password):
            raise FormError("Your current password is incorrect.")
        password = new_password()
        if password != request.form.get("confirm_password", ""):
            raise FormError("The new passwords do not match.")
        run("UPDATE users SET password_hash=%s WHERE id=%s", (generate_password_hash(password), g.user["id"]))
        database().commit()
        uid = g.user["id"]
        session.clear()
        session["uid"] = uid
        flash("Your password has been changed.", "success")
    except FormError as exc:
        database().rollback()
        flash(str(exc), "error")
    return redirect(url_for("profile", section="security"))


@app.post("/books/<int:book_id>/reserve")
@signed_in
def reserve(book_id):
    """Request a book. If every copy is out, the member joins the waitlist instead."""
    try:
        uid = g.user["id"]
        lock_member(uid)
        item = get_book(book_id, lock=True)
        if active_request(uid, book_id) or borrowed(uid, book_id):
            raise FormError("You already have a request or loan for this title.")
        state = "pending" if item["available"] > 0 else "waitlisted"
        run("INSERT INTO reservations(user_id,book_id,status) VALUES(%s,%s,%s)", (uid, book_id, state))
        database().commit()
        flash("Reservation requested. Follow its status in My shelf." if state == "pending" else "You joined the waitlist. Watch My shelf for your pickup notice.", "success")
        return redirect(url_for("shelf"))
    except FormError as exc:
        database().rollback()
        flash(str(exc), "error")
        return redirect(url_for("book", book_id=book_id))


@app.post("/reservations/<int:rid>/cancel")
@signed_in
def cancel(rid):
    """Cancel one of the member's own pending, approved or waitlisted requests."""
    uid = g.user["id"]
    lock_member(uid)
    record = one("SELECT book_id FROM reservations WHERE id=%s AND user_id=%s", (rid, uid))
    if not record:
        abort(404)
    get_book(record["book_id"], lock=True)
    run("UPDATE reservations SET status='cancelled' WHERE id=%s AND status IN ('pending','approved','waitlisted')", (rid,))
    database().commit()
    return redirect(url_for("shelf"))


@app.get("/shelf")
@signed_in
def shelf():
    """My shelf: requests, loans with fines, seat bookings, renewals and payment status."""
    uid = g.user["id"]
    reservations = rows(RESERVATIONS + " WHERE r.user_id=%s ORDER BY r.id DESC", (uid,))
    loans = [due_info(r) for r in rows(LOANS + " WHERE l.user_id=%s ORDER BY l.id DESC", (uid,))]
    payment_records = rows("SELECT p.fine_kind,p.fine_id,p.status,r.status AS refund_status FROM fine_payments p LEFT JOIN payment_refunds r ON r.fine_payment_id=p.id WHERE p.user_id=%s", (uid,))
    payment_status = {(p["fine_kind"], p["fine_id"]): ("refunded" if p["refund_status"] == "processed" else "refund_pending" if p["refund_status"] in ("pending","submitting","uncertain") else p["status"]) for p in payment_records}
    return render_template("shelf.html", payment_status=payment_status, reservations=reservations, loans=loans, seats=rows(MY_SEATS,(uid, library_now().date())), fines=rows("SELECT * FROM manual_fines WHERE user_id=%s ORDER BY id DESC", (uid,)), renewals=rows("SELECT * FROM renewal_requests WHERE user_id=%s ORDER BY id DESC", (uid,)),
                           waived_loans={w["loan_id"] for w in rows("SELECT w.loan_id FROM loan_fine_waivers w JOIN loans l ON l.id=w.loan_id WHERE l.user_id=%s", (uid,))})


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def problem(error):
    """Friendly error page for bad requests, missing pages and similar errors."""
    return render_template("error.html", error=error), error.code


@app.post("/loans/<int:loan_id>/renew")
@signed_in
def request_renewal(loan_id):
    """Ask a librarian to extend a loan. Only one pending request per loan is allowed."""
    loan = one("SELECT * FROM loans WHERE id=%s AND user_id=%s FOR UPDATE", (loan_id, g.user["id"]))
    if not loan or loan["returned"]:
        abort(404)
    if one("SELECT id FROM renewal_requests WHERE loan_id=%s AND status='pending'", (loan_id,)):
        flash("A renewal request is already waiting for review.", "info")
    else:
        run("INSERT INTO renewal_requests(loan_id,user_id) VALUES(%s,%s)", (loan_id, g.user["id"]))
        database().commit()
        flash("Renewal requested. A librarian will review it.", "success")
    return redirect(url_for("shelf"))
