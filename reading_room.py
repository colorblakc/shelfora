"""Reading room: the seat map and seat bookings for the morning and afternoon sessions."""
from datetime import date, timedelta

import pymysql
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from database import database, one, rows, run
from library import FormError, library_now, lock_member, signed_in, staff

room = Blueprint("room", __name__, url_prefix="/reading-room")
PERIODS = {"morning": "Morning · 9 am–1 pm", "afternoon": "Afternoon · 1–5 pm"}
MY_SEATS = """SELECT sb.*, s.label FROM seat_bookings sb JOIN seats s ON s.id=sb.seat_id
              WHERE sb.user_id=%s AND sb.visit_date >= %s ORDER BY sb.visit_date,sb.period"""


def selection(values):
    """Read and check the chosen day and session, defaulting to the next one still open."""
    today = library_now().date()
    try:
        default_day = today + timedelta(days=1) if library_now().hour >= 17 else today
        day = date.fromisoformat(values.get("day", default_day.isoformat()))
    except ValueError:
        abort(400, "Choose a valid booking date.")
    period = values.get("period", "afternoon" if day == today and 13 <= library_now().hour < 17 else "morning")
    if not today <= day <= today + timedelta(days=14) or period not in PERIODS:
        abort(400, "Choose an available session within the next 14 days.")
    return day, period


def ended(day, period):
    """True when today's chosen session has already finished."""
    now = library_now()
    return day == now.date() and now.hour >= (13 if period == "morning" else 17)


@room.get("")
def overview():
    """Reading room page: the seat map for one day and session, with booked seats marked."""
    day, period = selection(request.args)
    seats = rows("""SELECT s.id,s.label,sb.id AS booking,sb.user_id,u.name
        FROM seats s LEFT JOIN seat_bookings sb ON sb.seat_id=s.id AND sb.visit_date=%s AND sb.period=%s
        LEFT JOIN users u ON sb.user_id=u.id ORDER BY LEFT(s.label,1), CAST(SUBSTRING(s.label,2) AS UNSIGNED)""", (day, period))
    uid = g.user["id"] if g.user else None
    own = next((s for s in seats if s["user_id"] == uid and uid), None)
    tables = [[seat for seat in seats if seat["label"].startswith(letter)] for letter in "ABCDEFGHIJ"]
    members = rows("SELECT id,name,email FROM users WHERE active=1 AND role='member' ORDER BY name") if g.user and g.user["role"] == "admin" else []
    return render_template("room.html", seats=seats, tables=tables, members=members, day=day, period=period, periods=PERIODS,
                           last=library_now().date()+timedelta(days=14), closed=ended(day, period), own=own)


@room.post("/book")
@signed_in
def book_seat():
    """Book a seat for the signed-in member; one seat per person per session."""
    day, period = selection(request.form)
    try:
        lock_member(g.user["id"])
        if ended(day, period):
            raise FormError("This session has ended. Choose another session.")
        seat = one("SELECT * FROM seats WHERE id=%s", (request.form.get("seat", type=int),))
        if not seat:
            raise FormError("Select an available seat on the map.")
        run("INSERT INTO seat_bookings(seat_id,user_id,visit_date,period) VALUES(%s,%s,%s,%s)",
            (seat["id"], g.user["id"], day, period))
        database().commit()
        flash(f"Seat {seat['label']} is booked for {day:%d %b}, {period}.", "success")
    except (FormError, pymysql.IntegrityError) as exc:
        database().rollback()
        flash(str(exc) if isinstance(exc, FormError) else "That seat or your session is already booked. Check the updated map.", "error")
    return redirect(url_for("room.overview", day=day, period=period))


@room.post("/allocate")
@staff
def allocate():
    """Librarian books a seat on behalf of an active member."""
    day, period = selection(request.form)
    try:
        member_id = request.form.get("member", type=int)
        if not member_id:
            raise FormError("Choose an active member.")
        member = one("SELECT id,name FROM users WHERE id=%s AND role='member' AND active=1 FOR UPDATE", (member_id,))
        if not member:
            raise FormError("Choose an active member.")
        if ended(day, period):
            raise FormError("This session has ended. Choose another session.")
        seat = one("SELECT id,label FROM seats WHERE id=%s", (request.form.get("seat", type=int),))
        if not seat:
            raise FormError("Choose an available seat.")
        run("INSERT INTO seat_bookings(seat_id,user_id,visit_date,period) VALUES(%s,%s,%s,%s)",
            (seat["id"], member_id, day, period))
        database().commit()
        flash(f"Seat {seat['label']} allocated to {member['name']}.", "success")
    except (FormError, pymysql.IntegrityError) as exc:
        database().rollback()
        flash(str(exc) if isinstance(exc, FormError) else "That seat or member already has a booking for this session.", "error")
    return redirect(url_for("room.overview", day=day, period=period))


@room.post("/release/<int:booking_id>")
@signed_in
def release(booking_id):
    """Give a booked seat back. Members can release their own; librarians can release any."""
    booking = one("SELECT * FROM seat_bookings WHERE id=%s FOR UPDATE", (booking_id,))
    if not booking or (booking["user_id"] != g.user["id"] and g.user["role"] != "admin"):
        abort(404)
    run("DELETE FROM seat_bookings WHERE id=%s", (booking_id,))
    database().commit()
    flash("Seat booking released.", "success")
    return redirect(url_for("room.overview", day=booking["visit_date"], period=booking["period"]))
