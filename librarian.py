"""The library desk (/desk): pages librarians use to manage books, members, loans, fines and refunds."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from secrets import token_hex

import pymysql
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from database import STOCK, database, one, rows, run
from library import (LOANS, RESERVATIONS, FormError, active_request, borrowed, due_info, field,
                     contact_fields, get_book, library_now, lock_member, member_fields, new_password, staff)

admin = Blueprint("admin", __name__, url_prefix="/desk")


def finish(endpoint, message):
    """Save the change, show a success message and go back to the page named."""
    database().commit()
    flash(message, "success")
    return redirect(url_for("admin." + endpoint))


def failure(exc):
    """Undo the change and show why it failed."""
    database().rollback()
    flash(str(exc) if isinstance(exc, FormError) else "This email is already in use.", "error")


def sync_copies(book_id, desired):
    """Keep good physical copies aligned with catalogue stock under a book lock."""
    good = rows("SELECT c.id FROM book_copies c WHERE c.book_id=%s AND c.condition_status='good' ORDER BY c.id", (book_id,))
    difference = desired - len(good)
    if difference > 0:
        for _ in range(difference):
            copy_id = run("INSERT INTO book_copies(book_id,barcode) VALUES(%s,%s)", (book_id, 'new-' + token_hex(16)))
            run("UPDATE book_copies SET barcode=%s WHERE id=%s", (f"SF-C{copy_id:06}", copy_id))
    elif difference < 0:
        free = rows("SELECT c.id FROM book_copies c WHERE c.book_id=%s AND c.condition_status='good' AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL) ORDER BY c.id DESC LIMIT %s FOR UPDATE", (book_id, -difference))
        if len(free) != -difference:
            raise FormError("Not enough unissued copies can be removed.")
        for copy in free:
            run("UPDATE book_copies SET condition_status='retired' WHERE id=%s", (copy["id"],))


def unpaid_fines(user_id):
    """Count a member's fines that are not yet paid (or whose payment was refunded)."""
    return one("""SELECT
      (SELECT COUNT(*) FROM manual_fines f LEFT JOIN fine_payments p ON p.fine_kind='manual' AND p.fine_id=f.id
       LEFT JOIN payment_refunds pr ON pr.fine_payment_id=p.id
       WHERE f.user_id=%s AND f.voided_at IS NULL AND (p.id IS NULL OR p.status!='paid' OR pr.status='processed')) +
      (SELECT COUNT(*) FROM loans l LEFT JOIN fine_payments p ON p.fine_kind='loan' AND p.fine_id=l.id
       LEFT JOIN payment_refunds pr ON pr.fine_payment_id=p.id LEFT JOIN loan_fine_waivers w ON w.loan_id=l.id
       WHERE l.user_id=%s AND l.returned IS NOT NULL AND l.fine>0 AND w.id IS NULL AND (p.id IS NULL OR p.status!='paid' OR pr.status='processed')) AS n""", (user_id,user_id))["n"]


def restrictions_for_issue(user_id, today):
    """Reasons a book should not be issued to this member: loan limit, overdue books or unpaid fines."""
    active = one("SELECT COUNT(*) AS n FROM loans WHERE user_id=%s AND returned IS NULL", (user_id,))["n"]
    overdue = one("SELECT COUNT(*) AS n FROM loans WHERE user_id=%s AND returned IS NULL AND due<%s", (user_id,today))["n"]
    limit = one("SELECT borrowing_limit FROM settings WHERE id=1")["borrowing_limit"]
    restrictions = []
    if active >= limit:
        restrictions.append(f"Borrowing limit reached ({active}/{limit})")
    if overdue:
        restrictions.append(f"{overdue} overdue loan(s)")
    if unpaid_fines(user_id):
        restrictions.append("Unpaid fines")
    return restrictions


def apply_override(user_id, action, restrictions):
    """Let a librarian issue anyway, but only with the override ticked and a reason, which is recorded."""
    if not restrictions:
        return
    reason = request.form.get("override_reason", "").strip()
    if not reason or len(reason) > 500 or request.form.get("override") != "yes":
        raise FormError("; ".join(restrictions) + ". To proceed, select the override and enter its reason.")
    run("INSERT INTO lending_overrides(user_id,staff_id,action,reason,restrictions) VALUES(%s,%s,%s,%s,%s)",
        (user_id,g.user["id"],action,reason,"; ".join(restrictions)))


@admin.get("")
@staff
def dashboard():
    """Library desk home: counts and what needs attention."""
    today = library_now().date()
    stats = one("""SELECT (SELECT COALESCE(SUM(copies),0) FROM books WHERE archived=0) AS copies,
        (SELECT COUNT(*) FROM loans WHERE returned IS NULL) AS loans,
        (SELECT COUNT(*) FROM loans WHERE returned IS NULL AND due<%s) AS overdue,
        (SELECT COUNT(*) FROM reservations WHERE status='pending') AS pending""", (today,))
    requests = rows(RESERVATIONS + " WHERE r.status='pending' ORDER BY r.id LIMIT 8")
    overdue = [due_info(l) for l in rows(LOANS + " WHERE l.returned IS NULL AND l.due<%s ORDER BY l.due LIMIT 8", (today,))]
    start = today - timedelta(days=6)
    activity = rows("""SELECT d, SUM(outs) AS outs, SUM(ins) AS ins FROM
        (SELECT issued AS d,COUNT(*) AS outs,0 AS ins FROM loans WHERE issued BETWEEN %s AND %s GROUP BY issued
         UNION ALL
         SELECT returned AS d,0 AS outs,COUNT(*) AS ins FROM loans WHERE returned BETWEEN %s AND %s GROUP BY returned)
         AS daily GROUP BY d""", (start, today, start, today))
    by_date = {entry["d"]: entry for entry in activity}
    week = [by_date.get(start + timedelta(days=i), {"d":start + timedelta(days=i),"outs":0,"ins":0}) for i in range(7)]
    peak = max(1, max(max(d["outs"], d["ins"]) for d in week))
    return render_template("desk.html", tab="Overview", stats=stats, requests=requests, overdue=overdue,
                           week=week, peak=peak)


@admin.get("/books")
@staff
def books():
    """Catalogue list for librarians."""
    return render_template("desk.html", tab="Books", books=rows(STOCK + " WHERE b.archived=0 ORDER BY b.title"))


@admin.route("/books/new", methods=["GET","POST"])
@admin.route("/books/<int:book_id>/edit", methods=["GET","POST"])
@staff
def edit_book(book_id=None):
    """Add a new book or edit an existing one, including how many copies it has."""
    item = get_book(book_id, lock=request.method=="POST") if book_id else {}
    if request.method == "POST":
        try:
            title, author, category = (field(k, "a "+k, size) for k,size in (("title",200),("author",140),("category",60)))
            desc = field("description","a description",5000,required=False)
            isbn = request.form.get("isbn", "").strip().replace("-", "").replace(" ", "")
            publisher = field("publisher", "a publisher", 150, required=False)
            if isbn and not (len(isbn) == 13 and isbn.isascii() and isbn.isdigit() and
                sum(int(n) * (1 if i % 2 == 0 else 3) for i, n in enumerate(isbn)) % 10 == 0):
                raise FormError("Enter a valid 13-digit ISBN, or leave it blank.")
            year = request.form.get("published",type=int)
            copies = request.form.get("copies",type=int)
            if copies is None or not 0 <= copies <= 65535 or (request.form.get("published") and (not year or not 1 <= year <= 9999)):
                raise FormError("Enter a valid year and a copy count from 0 to 65535.")
            if item and copies < item["copies"] - item["available"]:
                raise FormError("Copies cannot be lower than the number held or on loan.")
            data = (title,author,category,desc,year,copies,isbn or None,publisher or None)
            if book_id:
                run("UPDATE books SET title=%s,author=%s,category=%s,description=%s,published=%s,copies=%s,isbn=%s,publisher=%s WHERE id=%s", (*data,book_id))
            else:
                book_id = run("INSERT INTO books(title,author,category,description,published,copies,isbn,publisher) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", data)
            sync_copies(book_id, copies)
            return finish("books","Catalogue record saved.")
        except FormError as exc:
            failure(exc)
    return render_template("edit.html", kind="book", record=item, copies=rows("SELECT c.*,EXISTS(SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL) AS on_loan FROM book_copies c WHERE c.book_id=%s ORDER BY c.id", (book_id,)) if book_id else [])


@admin.post("/books/<int:book_id>/remove")
@staff
def remove_book(book_id):
    """Remove a book from the catalogue; its past loans are kept."""
    item = get_book(book_id, lock=True)
    if item["available"] < item["copies"] or one("SELECT id FROM reservations WHERE book_id=%s AND status='pending' LIMIT 1", (book_id,)):
        flash("Resolve open loans and reservations before removing this title.", "error")
    else:
        run("UPDATE books SET archived=1 WHERE id=%s", (book_id,))
        return finish("books","Book removed from the catalogue. Its circulation history is preserved.")
    return redirect(url_for("admin.books"))


@admin.get("/members")
@staff
def members():
    """Member list for librarians."""
    q = request.args.get("q","").strip()[:254]
    people = rows("""SELECT id,name,email,created_at FROM users WHERE active=1 AND role='member'
        AND (LOCATE(%s,name)>0 OR LOCATE(%s,email)>0 OR CAST(id AS CHAR)=%s) ORDER BY name""",(q,q,q)) if q else rows(
        "SELECT id,name,email,created_at FROM users WHERE active=1 AND role='member' ORDER BY name")
    return render_template("desk.html", tab="Members", people=people, q=q)


@admin.route("/members/new", methods=["GET","POST"])
@admin.route("/members/<int:uid>/edit", methods=["GET","POST"])
@staff
def edit_member(uid=None):
    """Add a member or edit their details."""
    person = one("SELECT id,name,email,phone,address,address_line2,region,city,postal_code FROM users WHERE id=%s AND role='member' AND active=1", (uid,)) if uid else {}
    if uid and not person:
        abort(404)
    if request.method == "POST":
        try:
            name,email = member_fields()
            contact = contact_fields(person)
            if uid:
                lock_member(uid)
                run("UPDATE users SET name=%s,email=%s,phone=%s,address=%s,address_line2=%s,region=%s,city=%s,postal_code=%s WHERE id=%s",(name,email,*contact,uid))
            else:
                run("INSERT INTO users(name,email,password_hash,phone,address,address_line2,region,city,postal_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(name,email,generate_password_hash(new_password()),*contact))
            return finish("members","Member details saved.")
        except (FormError, pymysql.IntegrityError) as exc:
            failure(exc)
    history = rows(LOANS+" WHERE l.user_id=%s ORDER BY l.id DESC",(uid,)) if uid else []
    return render_template("edit.html", kind="member", record=person, history=history)


@admin.post("/members/<int:uid>/remove")
@staff
def remove_member(uid):
    """Close a member's account; unpaid fines need a recorded override."""
    person = one("SELECT id FROM users WHERE id=%s AND role='member' AND active=1 FOR UPDATE", (uid,))
    if not person:
        abort(404)
    if one("SELECT id FROM loans WHERE user_id=%s AND returned IS NULL LIMIT 1",(uid,)) or one(
        "SELECT id FROM reservations WHERE user_id=%s AND status IN ('pending','approved','waitlisted') LIMIT 1",(uid,)):
        flash("Resolve this member’s loans and reservations before removing their account.", "error")
        return redirect(url_for("admin.members"))
    try:
        apply_override(uid, "close_account", ["Unpaid fines"] if unpaid_fines(uid) else [])
    except FormError as exc:
        failure(exc)
        return redirect(url_for("admin.edit_member", uid=uid))
    run("DELETE FROM seat_bookings WHERE user_id=%s AND visit_date>=%s",(uid,library_now().date()))
    run("UPDATE users SET active=0 WHERE id=%s",(uid,))
    return finish("members","Membership closed and upcoming seats released. Borrowing history is preserved.")


@admin.get("/reservations")
@staff
def reservations():
    """List of requests waiting for a librarian."""
    return render_template("desk.html", tab="Reservations", requests=rows(RESERVATIONS+" ORDER BY FIELD(r.status,'pending','approved','issued','rejected','cancelled'),r.id DESC"))


@admin.post("/reservations/<int:rid>")
@staff
def review(rid):
    """Approve or reject a book request."""
    record = one("SELECT book_id,user_id FROM reservations WHERE id=%s",(rid,))
    if not record:
        abort(404)
    try:
        lock_member(record["user_id"])
        item = get_book(record["book_id"], lock=True)
        record = one("SELECT status FROM reservations WHERE id=%s FOR UPDATE",(rid,))
        target = request.form.get("action")
        if (record["status"],target) not in {("pending","approved"),("pending","rejected"),("approved","cancelled")}:
            raise FormError("This request has already changed. Check its current status.")
        if target == "approved" and item["available"] <= 0:
            raise FormError("No copy is available to hold for this member.")
        run("UPDATE reservations SET status=%s,hold_until=%s WHERE id=%s", (target, library_now().replace(tzinfo=None)+timedelta(days=3) if target == "approved" else None, rid))
        return finish("reservations",f"Reservation {target}.")
    except FormError as exc:
        failure(exc)
        return redirect(url_for("admin.reservations"))


@admin.get("/loans")
@staff
def loans():
    """Current loans, returns, fines and the borrowing limit setting."""
    return render_template("desk.html", tab="Loans", loans=[due_info(l) for l in rows(LOANS + " ORDER BY l.returned IS NULL DESC,l.due,l.id DESC")],
                           rate=one("SELECT daily_rate FROM settings WHERE id=1")["daily_rate"], borrowing_limit=one("SELECT borrowing_limit FROM settings WHERE id=1")["borrowing_limit"],
                           renewals=rows("SELECT rr.id,b.title,u.name FROM renewal_requests rr JOIN loans l ON l.id=rr.loan_id JOIN books b ON b.id=l.book_id JOIN users u ON u.id=rr.user_id WHERE rr.status='pending' AND l.returned IS NULL ORDER BY rr.created_at"),
                           waivers={w["loan_id"]: w for w in rows("SELECT * FROM loan_fine_waivers")},
                           payment_loans={p["fine_id"] for p in rows("SELECT fine_id FROM fine_payments WHERE fine_kind='loan'")},
                           overrides=rows("SELECT o.*,u.name AS member_name,s.name AS staff_name FROM lending_overrides o JOIN users u ON u.id=o.user_id JOIN users s ON s.id=o.staff_id ORDER BY o.id DESC LIMIT 20"))


@admin.route("/issue", methods=["GET","POST"])
@staff
def issue():
    """Lend a book to a member: checks restrictions, picks a physical copy and sets the due date."""
    today = library_now().date()
    book_id, uid = (request.values.get(k,type=int) for k in ("book_id","user_id"))
    if request.method == "POST":
        try:
            if not book_id or not uid:
                raise FormError("Choose a title and a member.")
            lock_member(uid)
            if not one("SELECT id FROM users WHERE id=%s AND active=1 AND role='member'", (uid,)):
                raise FormError("Choose an active member.")
            item = get_book(book_id,lock=True)
            reservation = active_request(uid,book_id)
            if borrowed(uid,book_id):
                raise FormError("This member already has a copy on loan.")
            if reservation and reservation["status"] in ("pending", "waitlisted"):
                raise FormError("Approve the pending reservation before issuing this copy.")
            if not reservation and item["available"] <= 0:
                raise FormError("No unreserved copy is available.")
            try:
                due = date.fromisoformat(request.form.get("due",""))
            except ValueError:
                raise FormError("Choose a valid due date.")
            if due < today:
                raise FormError("The due date must be today or later.")
            apply_override(uid, "issue", restrictions_for_issue(uid, today))
            barcode = request.form.get("barcode", "").strip()
            if len(barcode) > 64:
                raise FormError("Enter a valid copy ID or barcode.")
            sql = "SELECT c.* FROM book_copies c WHERE c.book_id=%s AND c.condition_status='good' AND NOT EXISTS(SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL)"
            copy = one(sql + (" AND c.barcode=%s" if barcode else "") + " ORDER BY c.id LIMIT 1 FOR UPDATE", (book_id,barcode) if barcode else (book_id,))
            if not copy:
                raise FormError("This copy is unavailable or does not match the selected title.")
            rate = one("SELECT daily_rate FROM settings WHERE id=1")["daily_rate"]
            rid = reservation["id"] if reservation else None
            run("INSERT INTO loans(book_id,user_id,reservation_id,issued,due,daily_rate,copy_id) VALUES(%s,%s,%s,%s,%s,%s,%s)",(book_id,uid,rid,today,due,rate,copy["id"]))
            if rid:
                run("UPDATE reservations SET status='issued' WHERE id=%s",(rid,))
            return finish("loans","Book issued and borrowing record updated.")
        except FormError as exc:
            failure(exc)
    return render_template("issue.html", books=rows("SELECT id,title FROM books WHERE archived=0 ORDER BY title"),
                           people=rows("SELECT id,name,email FROM users WHERE active=1 AND role='member' ORDER BY name"), book_id=book_id, uid=uid, due=today+timedelta(days=14),
                           copies=rows("SELECT c.barcode,c.book_id,b.title FROM book_copies c JOIN books b ON b.id=c.book_id WHERE b.archived=0 AND c.condition_status='good' AND NOT EXISTS(SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL) ORDER BY b.title,c.id"))


@admin.post("/loans/<int:lid>/return")
@staff
def return_book(lid):
    """Record a returned book as good, damaged or lost, and work out its fine."""
    record = one("SELECT book_id FROM loans WHERE id=%s",(lid,))
    if not record:
        abort(404)
    get_book(record["book_id"],lock=True)
    loan = one("SELECT * FROM loans WHERE id=%s FOR UPDATE",(lid,))
    if loan["returned"]:
        flash("This loan has already been returned.", "info")
        return redirect(url_for("admin.loans"))
    today = library_now().date()
    barcode = request.form.get("return_barcode", "").strip()
    if len(barcode) > 64:
        flash("Enter a valid copy ID or barcode.", "error")
        return redirect(url_for("admin.loans"))
    if barcode:
        copy = one("SELECT c.id FROM book_copies c WHERE c.book_id=%s AND c.barcode=%s AND c.condition_status='good' AND NOT EXISTS(SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL AND l.id!=%s) FOR UPDATE", (loan["book_id"], barcode, lid))
        if not copy:
            flash("That copy is not available for this loan. Check the copy ID and title.", "error")
            return redirect(url_for("admin.loans"))
        run("UPDATE loans SET copy_id=%s,copy_id_provisional=FALSE WHERE id=%s", (copy["id"],lid))
        loan["copy_id"] = copy["id"]
    condition = request.form.get("condition", "good")
    notes = request.form.get("notes", "").strip()
    if condition not in ("good", "damaged", "lost") or len(notes) > 500:
        flash("Choose a valid condition and keep notes under 500 characters.", "error")
        return redirect(url_for("admin.loans"))
    fine = due_info(loan)["amount"]
    if condition in ("lost", "damaged"):
        run("UPDATE books SET copies=copies-1 WHERE id=%s AND copies>0", (loan["book_id"],))
        if loan["copy_id"]:
            run("UPDATE book_copies SET condition_status=%s WHERE id=%s", (condition, loan["copy_id"]))
    run("UPDATE loans SET returned=%s,fine=%s,return_condition=%s,return_notes=%s WHERE id=%s",(today,fine,condition,notes,lid))
    run("UPDATE renewal_requests SET status='rejected',reviewed_at=%s WHERE loan_id=%s AND status='pending'", (library_now().replace(tzinfo=None), lid))
    return finish("loans",f"Loan closed ({condition}). Fine for this loan is ₹{fine:.2f}." + (" This copy has been removed from available stock." if condition in ("lost", "damaged") else ""))


@admin.post("/rate")
@staff
def rate():
    """Change the fine charged per overdue day for new loans."""
    try:
        value = Decimal(request.form.get("rate",""))
        if not value.is_finite() or not 0 <= value <= 99999 or value != value.quantize(Decimal(".01")):
            raise InvalidOperation
    except InvalidOperation:
        flash("Enter a daily rate from ₹0 to ₹99,999 with up to two decimal places.", "error")
        return redirect(url_for("admin.loans"))
    run("UPDATE settings SET daily_rate=%s WHERE id=1",(value,))
    return finish("loans","Daily fine rate updated for new loans.")

@admin.route("/fines", methods=["GET", "POST"])
@staff
def fines():
    """List fines and add a manual fine for a member."""
    if request.method == "POST":
        try:
            user_id = request.form.get("user_id", type=int)
            member = one("SELECT id FROM users WHERE id=%s AND active=1 AND role='member' FOR UPDATE", (user_id,))
            if not member:
                raise FormError("Choose an active member.")
            reason = field("reason", "a reason", 200)
            try:
                amount = Decimal(request.form.get("amount", ""))
                if not amount.is_finite() or not Decimal("0.01") <= amount <= Decimal("9999999.99") or amount != amount.quantize(Decimal(".01")):
                    raise InvalidOperation
            except InvalidOperation:
                raise FormError("Enter an amount between ₹0.01 and ₹9,999,999.99 with up to two decimal places.")
            run("INSERT INTO manual_fines(user_id,created_by,reason,amount) VALUES(%s,%s,%s,%s)",
                (user_id, g.user["id"], reason, amount))
            return finish("fines", "Fine recorded.")
        except FormError as exc:
            failure(exc)
    people = rows("SELECT id,name,email FROM users WHERE active=1 AND role='member' ORDER BY name")
    charges = rows("""SELECT f.*,u.name,u.email,p.status AS payment_status FROM manual_fines f
                      JOIN users u ON u.id=f.user_id LEFT JOIN fine_payments p ON p.fine_kind='manual' AND p.fine_id=f.id ORDER BY f.id DESC""")
    paid = rows("""SELECT p.id,p.amount_paise,p.paid_at,u.name,COALESCE(f.reason,b.title,'Library fine') AS description,
                         r.status AS refund_status,r.reason AS refund_reason
                  FROM fine_payments p JOIN users u ON u.id=p.user_id
                  LEFT JOIN manual_fines f ON p.fine_kind='manual' AND f.id=p.fine_id
                  LEFT JOIN loans l ON p.fine_kind='loan' AND l.id=p.fine_id
                  LEFT JOIN books b ON b.id=l.book_id
                  LEFT JOIN payment_refunds r ON r.fine_payment_id=p.id
                  WHERE p.status='paid' ORDER BY p.id DESC""")
    return render_template("desk.html", tab="Fines", people=people, charges=charges, paid=paid)


@admin.post("/fines/<int:fine_id>/change")
@staff
def change_fine(fine_id):
    """Edit or cancel a manual fine, before any payment has been tried."""
    try:
        fine = one("SELECT * FROM manual_fines WHERE id=%s FOR UPDATE", (fine_id,))
        if not fine or fine["voided_at"]:
            raise FormError("This fine is unavailable or already waived.")
        if one("SELECT id FROM fine_payments WHERE fine_kind='manual' AND fine_id=%s FOR UPDATE", (fine_id,)):
            raise FormError("This fine has a payment attempt and cannot be changed here. Review it with the payment provider.")
        action = request.form.get("action")
        if action == "waive":
            reason = field("void_reason", "a reason for waiving", 200)
            run("INSERT INTO fine_history(fine_id,changed_by,action,previous_amount,previous_reason,explanation) VALUES(%s,%s,'waived',%s,%s,%s)", (fine_id,g.user["id"],fine["amount"],fine["reason"],reason))
            run("UPDATE manual_fines SET voided_at=%s,void_reason=%s WHERE id=%s", (library_now().replace(tzinfo=None), reason, fine_id))
            return finish("fines", "Fine waived; its history has been retained.")
        if action == "edit":
            reason = field("reason", "a reason", 200)
            try:
                amount = Decimal(request.form.get("amount", ""))
                if not amount.is_finite() or not Decimal("0.01") <= amount <= Decimal("9999999.99") or amount != amount.quantize(Decimal(".01")):
                    raise InvalidOperation
            except InvalidOperation:
                raise FormError("Enter a valid amount with up to two decimal places.")
            run("INSERT INTO fine_history(fine_id,changed_by,action,previous_amount,previous_reason) VALUES(%s,%s,'edited',%s,%s)", (fine_id,g.user["id"],fine["amount"],fine["reason"]))
            run("UPDATE manual_fines SET reason=%s,amount=%s WHERE id=%s", (reason, amount, fine_id))
            return finish("fines", "Fine updated.")
        raise FormError("Choose an action.")
    except FormError as exc:
        failure(exc)
        return redirect(url_for("admin.fines"))


@admin.post("/renewals/<int:rid>")
@staff
def review_renewal(rid):
    """Approve or reject a member's request to extend a loan."""
    try:
        renewal = one("SELECT * FROM renewal_requests WHERE id=%s", (rid,))
        if not renewal or renewal["status"] != "pending":
            raise FormError("This renewal has already been reviewed.")
        loan_ref = one("SELECT book_id FROM loans WHERE id=%s", (renewal["loan_id"],))
        if not loan_ref:
            raise FormError("This loan no longer exists.")
        one("SELECT id FROM books WHERE id=%s FOR UPDATE", (loan_ref["book_id"],))
        loan = one("SELECT * FROM loans WHERE id=%s FOR UPDATE", (renewal["loan_id"],))
        renewal = one("SELECT * FROM renewal_requests WHERE id=%s FOR UPDATE", (rid,))
        if not renewal or renewal["status"] != "pending":
            raise FormError("This renewal has already been reviewed.")
        action = request.form.get("action")
        if action not in ("approved", "rejected") or not loan or loan["returned"]:
            raise FormError("This loan cannot be renewed.")
        if action == "approved":
            if loan["due"] < library_now().date():
                raise FormError("Overdue books must be returned before they can be borrowed again.")
            if one("SELECT id FROM reservations WHERE book_id=%s AND status IN ('pending','approved','waitlisted') LIMIT 1", (loan["book_id"],)):
                raise FormError("Another reader is waiting for this title; decline the renewal.")
            due = loan["due"] + timedelta(days=14)
            run("UPDATE loans SET due=%s WHERE id=%s", (due, loan["id"]))
        run("UPDATE renewal_requests SET status=%s,reviewed_at=%s WHERE id=%s", (action, library_now().replace(tzinfo=None), rid))
        return finish("loans", "Renewal request " + action + ".")
    except FormError as exc:
        failure(exc)
        return redirect(url_for("admin.loans"))


@admin.post("/payments/<int:payment_id>/refund")
@staff
def refund_payment(payment_id):
    # The request is committed before contacting the provider; retries use the same key.
    """Refund a paid fine through the payment provider."""
    from payments import api
    from urllib.error import HTTPError, URLError
    try:
        payment = one("SELECT * FROM fine_payments WHERE id=%s FOR UPDATE", (payment_id,))
        if not payment or payment["status"] != "paid" or not payment["payment_id"]:
            raise FormError("Only a confirmed payment can be refunded.")
        refund = one("SELECT * FROM payment_refunds WHERE fine_payment_id=%s FOR UPDATE", (payment_id,))
        if refund and refund["status"] in ('processed','pending','failed'):
            raise FormError("A refund has already been submitted for this payment.")
        if refund:
            refund_id = refund["id"]
        else:
            reason = field("reason", "a refund reason", 200)
            refund_id = run("INSERT INTO payment_refunds(fine_payment_id,requested_by,reason) VALUES(%s,%s,%s)", (payment_id,g.user["id"],reason))
        database().commit()
        try:
            response = api("payments/" + payment["payment_id"] + "/refund",
                           {"amount":payment["amount_paise"],"speed":"normal"},
                           {"X-Refund-Idempotency":"shelfora_refund_" + str(refund_id)})
            if (not isinstance(response, dict) or response.get('payment_id') != payment['payment_id']
                or response.get('amount') != payment['amount_paise'] or response.get('currency') != 'INR'
                or not isinstance(response.get('id'),str) or not response['id'].startswith('rfnd_')
                or response.get('status') not in ('pending','processed','failed')):
                raise ValueError("Unexpected refund response")
            run("UPDATE payment_refunds SET provider_id=%s,status=%s WHERE id=%s AND status != 'processed'", (response['id'],response['status'],refund_id))
            database().commit()
            flash("Refund " + response['status'] + ".", "success" if response['status']=='processed' else "info")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError):
            run("UPDATE payment_refunds SET status='uncertain' WHERE id=%s AND status IN ('submitting','uncertain')", (refund_id,))
            database().commit()
            flash("The refund response was not confirmed. Retry this same request; do not create a separate refund.", "error")
        return redirect(url_for('admin.fines'))
    except FormError as exc:
        failure(exc)
        return redirect(url_for('admin.fines'))


@admin.post("/borrowing-limit")
@staff
def borrowing_limit():
    """Set how many books one member may borrow at once."""
    value = request.form.get("borrowing_limit", type=int)
    if value is None or not 1 <= value <= 100:
        flash("Enter a borrowing limit from 1 to 100 books.", "error")
        return redirect(url_for("admin.loans"))
    run("UPDATE settings SET borrowing_limit=%s WHERE id=1", (value,))
    return finish("loans", "Borrowing limit updated.")


@admin.post("/copies/<int:copy_id>/restore")
@staff
def restore_copy(copy_id):
    """Put a damaged or lost copy back into available stock."""
    ref = one("SELECT book_id FROM book_copies WHERE id=%s", (copy_id,))
    if not ref:
        abort(404)
    book = get_book(ref["book_id"], lock=True)
    copy = one("SELECT * FROM book_copies WHERE id=%s FOR UPDATE", (copy_id,))
    if not copy or copy["condition_status"] not in ("damaged", "lost") or one("SELECT id FROM loans WHERE copy_id=%s AND returned IS NULL", (copy_id,)):
        flash("This copy cannot be restored.", "error")
        return redirect(url_for("admin.edit_book", book_id=book["id"]))
    run("UPDATE book_copies SET condition_status='good' WHERE id=%s", (copy_id,))
    run("UPDATE books SET copies=copies+1 WHERE id=%s", (book["id"],))
    database().commit()
    flash("Copy restored to available stock.", "success")
    return redirect(url_for("admin.edit_book", book_id=book["id"]))


@admin.post("/loans/<int:loan_id>/waive-fine")
@staff
def waive_loan_fine(loan_id):
    """Cancel an overdue fine, with a recorded reason, if no payment has been tried."""
    try:
        loan = one("SELECT * FROM loans WHERE id=%s FOR UPDATE", (loan_id,))
        if not loan or not loan["returned"] or not loan["fine"] or loan["fine"] <= 0:
            raise FormError("Only a finalised overdue fine can be waived.")
        if one("SELECT id FROM fine_payments WHERE fine_kind='loan' AND fine_id=%s FOR UPDATE", (loan_id,)):
            raise FormError("A payment attempt exists for this fine. It cannot be waived here.")
        if one("SELECT id FROM loan_fine_waivers WHERE loan_id=%s", (loan_id,)):
            raise FormError("This fine has already been waived.")
        reason = field("reason", "a waiver reason", 200)
        run("INSERT INTO loan_fine_waivers(loan_id,waived_by,amount,reason) VALUES(%s,%s,%s,%s)", (loan_id,g.user["id"],loan["fine"],reason))
        return finish("loans", "Overdue fine waived.")
    except FormError as exc:
        failure(exc)
        return redirect(url_for("admin.loans"))
