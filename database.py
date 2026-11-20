"""Database access for Shelfora: the MySQL connection, small query helpers and the setup commands."""
from pathlib import Path
import json
import os
import ssl

import click
import pymysql
from flask import current_app, g
from werkzeug.security import generate_password_hash

from collection import BOOKS, LEGACY_BOOK_KEYS
from metadata import EDITIONS, CLASSIC_ISBNS


def tls_context():
    """Encrypt the connection without checking the server's certificate name.

    Managed MySQL hosts require TLS and use a certificate made for their own address.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def database():
    """The database connection for this request, opened the first time it is needed."""
    if "db" not in g:
        cfg = current_app.config
        g.db = pymysql.connect(host=cfg["MYSQL_HOST"], port=cfg["MYSQL_PORT"],
                               unix_socket=cfg.get("MYSQL_SOCKET"), user=cfg["MYSQL_USER"],
                               password=cfg["MYSQL_PASSWORD"], database=cfg["MYSQL_DATABASE"],
                               charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                               init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
                               ssl=tls_context() if cfg.get("MYSQL_SSL") else None,
                               connect_timeout=8)
    return g.db


def run(sql, args=()):
    """Run one change (INSERT, UPDATE or DELETE) and return the new row id."""
    with database().cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.lastrowid


def rows(sql, args=()):
    """Run a query and return every matching row."""
    with database().cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.fetchall()


def one(sql, args=()):
    """Run a query and return the first row, or None."""
    result = rows(sql, args)
    return result[0] if result else None


# An approved reservation holds one copy until it is issued or cancelled.
STOCK = """SELECT b.*, CAST(b.copies AS SIGNED)
    - (SELECT COUNT(*) FROM loans l WHERE l.book_id=b.id AND l.returned IS NULL)
    - (SELECT COUNT(*) FROM reservations r WHERE r.book_id=b.id AND r.status='approved')
    AS available FROM books b"""


def configure(app):
    """Hook the database into the app: close it after each request and add the setup commands."""
    @app.teardown_appcontext
    def close_db(error):
        """Close the connection when the request ends; unfinished changes are rolled back."""
        db = g.pop("db", None)
        if db:
            db.close()  # MySQL rolls back any incomplete transaction.

    @app.cli.command("init-db")
    def init_db():
        """Create the schema and add missing starter titles; keep edited records."""
        for statement in Path(app.root_path, "schema.sql").read_text().split(";"):
            if statement.strip():
                run(statement)
        for column, definition in (("phone", "VARCHAR(30)"), ("address", "VARCHAR(500)"), ("address_line2", "VARCHAR(200)"), ("region", "VARCHAR(100)"), ("city", "VARCHAR(100)"), ("postal_code", "VARCHAR(12)"), ("avatar", "VARCHAR(20)")):
            if not one("SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='users' AND column_name=%s", (column,)):
                run(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        for column, definition in (("isbn", "VARCHAR(17)"), ("publisher", "VARCHAR(150)")):
            if not one("SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='books' AND column_name=%s", (column,)):
                run(f"ALTER TABLE books ADD COLUMN {column} {definition}")
        migrations = {
            "reservations": [("hold_until", "DATETIME NULL")],
            "loans": [("return_condition", "VARCHAR(20) NULL"), ("return_notes", "VARCHAR(500) NULL"), ("copy_id", "INT UNSIGNED NULL"), ("copy_id_provisional", "BOOLEAN NOT NULL DEFAULT FALSE")],
            "settings": [("borrowing_limit", "SMALLINT UNSIGNED NOT NULL DEFAULT 3")],
            "manual_fines": [("voided_at", "DATETIME NULL"), ("void_reason", "VARCHAR(200) NULL")],
        }
        for table, columns in migrations.items():
            for column, definition in columns:
                if not one("SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s", (table, column)):
                    run(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        reservation_status = one("SELECT COLUMN_TYPE AS t FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='reservations' AND column_name='status'")["t"]
        if "waitlisted" not in reservation_status:
            run("ALTER TABLE reservations MODIFY status ENUM('pending','approved','rejected','cancelled','issued','waitlisted','expired') NOT NULL DEFAULT 'pending'")
        # Existing holds get a collection window on upgrade.
        run("UPDATE reservations SET hold_until=DATE_ADD(NOW(), INTERVAL 3 DAY) WHERE status='approved' AND hold_until IS NULL")
        with database().cursor() as cursor:
            cursor.executemany("""INSERT INTO books
              (seed_key,title,author,category,published,copies,description)
              VALUES(%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=id""", BOOKS)
        catalogue = json.loads(Path(app.root_path, "catalogue_seed.json").read_text())
        with database().cursor() as cursor:
            cursor.executemany("""INSERT INTO books(seed_key,title,author,category,published,isbn,description,copies)
                VALUES(%s,%s,%s,%s,%s,%s,'',2) ON DUPLICATE KEY UPDATE id=id""",
                [(b["key"], b["title"], b["author"], "Books", b["year"], b["isbn"]) for b in catalogue])
        for b in catalogue:
            if b.get("subjects"):
                run("UPDATE books SET description=%s WHERE seed_key=%s AND description=''",
                    ("Subjects: " + ", ".join(b["subjects"]) + ".", b["key"]))
        for seed_key, isbn in CLASSIC_ISBNS.items():
            run("UPDATE books SET isbn=COALESCE(NULLIF(isbn,''),%s) WHERE seed_key=%s", (isbn, seed_key))
        for seed_key in LEGACY_BOOK_KEYS:
            run("""UPDATE books b SET archived=1 WHERE b.seed_key=%s AND b.archived=0
                AND NOT EXISTS (SELECT 1 FROM loans l WHERE l.book_id=b.id AND l.returned IS NULL)
                AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.book_id=b.id AND r.status IN ('pending','approved'))""", (seed_key,))
        # Keep the original seat IDs and labels so existing bookings remain valid.
        for row in "ABCDEFGHIJ":
            for number in range(1, 11):
                label = f"{row}{number}"
                if not one("SELECT id FROM seats WHERE label=%s", (label,)):
                    next_id = one("SELECT COALESCE(MAX(id),0)+1 AS id FROM seats")["id"]
                    run("INSERT INTO seats(id,label) VALUES(%s,%s)", (next_id, label))
        for seed_key, (isbn, publisher, description) in EDITIONS.items():
            run("""UPDATE books SET isbn=COALESCE(NULLIF(isbn,''),%s),
                publisher=COALESCE(NULLIF(publisher,''),%s),
                description=CASE WHEN description='' THEN %s ELSE description END
                WHERE seed_key=%s""", (isbn,publisher,description,seed_key))
        # Give existing aggregate inventory stable copy IDs. Existing active loans
        # receive distinct provisional IDs; historical returned copies remain unknown.
        for book in rows("SELECT id,copies FROM books ORDER BY id"):
            existing = one("SELECT COUNT(*) AS n FROM book_copies WHERE book_id=%s", (book["id"],))["n"]
            if not existing:
                for number in range(1, book["copies"] + 1):
                    run("INSERT INTO book_copies(book_id,barcode) VALUES(%s,%s)", (book["id"], f"SF-{book['id']}-{number}"))
            unassigned = rows("SELECT id FROM loans WHERE book_id=%s AND returned IS NULL AND copy_id IS NULL ORDER BY id", (book["id"],))
            for loan in unassigned:
                copy = one("SELECT c.id FROM book_copies c WHERE c.book_id=%s AND c.condition_status='good' AND NOT EXISTS(SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL) ORDER BY c.id LIMIT 1", (book["id"],))
                if not copy:
                    raise click.ClickException(f"Book {book['id']} has more active loans than good copies; resolve inventory before migrating.")
                run("UPDATE loans SET copy_id=%s,copy_id_provisional=TRUE WHERE id=%s", (copy["id"], loan["id"]))
        database().commit()
        click.echo("Library schema and starter catalogue are ready.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Add clearly labelled demonstration members and activity once."""
        from datetime import timedelta
        from secrets import token_urlsafe
        from library import library_now

        today = library_now().date()
        rate = one("SELECT daily_rate FROM settings WHERE id=1")["daily_rate"]
        examples = [
            ("Demo Reader Ananya", "demo.ananya@example.test", "gatsby", "active"),
            ("Demo Reader Rohan", "demo.rohan@example.test", "pride", "overdue"),
            ("Demo Reader Meera", "demo.meera@example.test", "time", "returned"),
            ("Demo Reader Isha", "demo.isha@example.test", "garden", "pending"),
        ]
        for index, (name, email, book_key, activity) in enumerate(examples):
            if one("SELECT id FROM users WHERE email=%s", (email,)):
                continue
            book = one(STOCK + " WHERE b.seed_key=%s AND b.archived=0", (book_key,))
            if not book or (activity in ("active", "overdue") and book["available"] < 1):
                click.echo(f"Skipped {name}: demo title is unavailable.")
                continue
            uid = run("INSERT INTO users(name,email,password_hash) VALUES(%s,%s,%s)",
                      (name, email, generate_password_hash(token_urlsafe(32))))
            if activity == "pending":
                run("INSERT INTO reservations(user_id,book_id,status) VALUES(%s,%s,'pending')", (uid, book["id"]))
            else:
                issued = today - timedelta(days=21 if activity == "overdue" else 7)
                due = today - timedelta(days=3) if activity == "overdue" else today + timedelta(days=7)
                returned = today if activity == "returned" else None
                available_copy = one("SELECT c.id FROM book_copies c WHERE c.book_id=%s AND c.condition_status='good' AND NOT EXISTS(SELECT 1 FROM loans l WHERE l.copy_id=c.id AND l.returned IS NULL) ORDER BY c.id LIMIT 1", (book["id"],))
                run("""INSERT INTO loans(book_id,user_id,issued,due,returned,daily_rate,fine,copy_id)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (book["id"], uid, issued, due, returned, rate, 0 if returned else None, available_copy["id"] if available_copy else None))
            seat = one("""SELECT s.id FROM seats s WHERE s.label=%s AND NOT EXISTS
                         (SELECT 1 FROM seat_bookings sb WHERE sb.seat_id=s.id
                          AND sb.visit_date=%s AND sb.period='morning')""",
                       (f"A{index + 1}", today + timedelta(days=1)))
            if seat:
                run("INSERT INTO seat_bookings(seat_id,user_id,visit_date,period) VALUES(%s,%s,%s,'morning')",
                    (seat["id"], uid, today + timedelta(days=1)))
        database().commit()
        click.echo("Demonstration activity added. Existing demo members were left unchanged.")

    @app.cli.command("create-admin")
    @click.option("--name", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    def create_admin(name, email, password):
        """Create a librarian account. Public registration creates members only."""
        from library import valid_email
        if not (1 <= len(name.strip()) <= 100 and valid_email(email.strip()) and 10 <= len(password) <= 128):
            raise click.ClickException("Enter a valid name, email and a password of 10–128 characters.")
        try:
            run("INSERT INTO users(name,email,password_hash,role) VALUES(%s,%s,%s,'admin')",
                (name.strip(), email.strip().lower(), generate_password_hash(password)))
            database().commit()
        except pymysql.IntegrityError:
            raise click.ClickException("This email already has an account.")
        click.echo("Librarian account created.")

    @app.cli.command("ensure-admin")
    def ensure_admin():
        """Create the first librarian from environment variables when it does not exist yet.

        Reads SHELFORA_ADMIN_NAME, SHELFORA_ADMIN_EMAIL and SHELFORA_ADMIN_PASSWORD. Safe to run on every start.
        """
        from library import valid_email
        name = os.environ.get("SHELFORA_ADMIN_NAME", "").strip()
        email = os.environ.get("SHELFORA_ADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("SHELFORA_ADMIN_PASSWORD", "")
        if not (name and email and password):
            click.echo("Admin variables are not all set; no librarian account created.")
            return
        if not (1 <= len(name) <= 100 and valid_email(email) and 10 <= len(password) <= 128):
            raise click.ClickException("Admin name, email or password (10-128 characters) is not valid.")
        if one("SELECT 1 FROM users WHERE email=%s", (email,)):
            click.echo("The librarian account already exists; left unchanged.")
            return
        run("INSERT INTO users(name,email,password_hash,role) VALUES(%s,%s,%s,'admin')",
            (name, email, generate_password_hash(password)))
        database().commit()
        click.echo("Librarian account created.")
