"""Preselected profile pictures for library members and librarians."""
from flask import Blueprint, abort, flash, g, redirect, request, url_for

from database import database, run
from library import signed_in

avatars = Blueprint("avatars", __name__)
AVATARS = tuple(f"{group}-{number}" for group in ("long", "short") for number in range(1, 6))


@avatars.post("/profile/avatar")
@signed_in
def update_avatar():
    """Save the profile picture chosen from the preset list."""
    avatar = request.form.get("avatar", "")
    if avatar and avatar not in AVATARS:
        abort(400, "Choose an available profile picture.")
    run("UPDATE users SET avatar=%s WHERE id=%s", (avatar or None, g.user["id"]))
    database().commit()
    flash("Your profile picture has been updated.", "success")
    return redirect(url_for("profile", section="picture"))
