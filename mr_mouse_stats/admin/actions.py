"""POST actions: every fix appends; nothing is deleted or overwritten."""

from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, request, url_for

from ..db import Store, now_utc

bp = Blueprint("actions", __name__)


def _store() -> Store:
    return Store(current_app.get_conn())


@bp.post("/actions/replace-handle")
def replace_handle():
    store = _store()
    player_id = int(request.form["player_id"])
    new_handle = request.form["new_handle"].strip().lstrip("@#")
    old_account_id = request.form.get("old_account_id")
    if not new_handle:
        flash("empty handle ignored")
        return redirect(url_for("handles"))
    now = now_utc()
    if old_account_id:
        store.retire_social_account(int(old_account_id), now)
    store.record_social_account(
        player_id, "twitch", new_handle,
        f"https://www.twitch.tv/{new_handle}", now, source="manual",
    )
    store.commit()
    flash(
        f"handle '{new_handle}' recorded — restart the collector to pick it up: "
        "systemctl --user restart mr-mouse-stats-collect"
    )
    return redirect(url_for("handles"))


@bp.post("/actions/retire-handle")
def retire_handle():
    store = _store()
    store.retire_social_account(int(request.form["account_id"]), now_utc())
    store.commit()
    flash("handle retired (kept as history)")
    return redirect(url_for("handles"))


@bp.post("/actions/candidates/<int:message_id>/manual")
def manual_observation(message_id: int):
    store = _store()
    row = store.conn.execute(
        "SELECT * FROM twitch_messages WHERE id = %s", (message_id,)
    ).fetchone()
    if row is None:
        flash("unknown message")
        return redirect(url_for("candidates"))
    player_id = store.player_ids_by_twitch_channel().get(row["channel"])
    if player_id is None:
        flash(f"no player known for channel '{row['channel']}' — fix handles first")
        return redirect(url_for("candidates"))

    def number(name, cast):
        raw = request.form.get(name, "").strip()
        try:
            return cast(raw) if raw else None
        except ValueError:
            return None

    fields = {
        "dpi": number("dpi", int),
        "sensitivity": number("sensitivity", float),
        "windows_sens": number("windows_sens", int),
        "polling_rate": number("polling_rate", int),
        "mouse_brand": request.form.get("mouse_brand", "").strip() or None,
        "mouse_model": request.form.get("mouse_model", "").strip() or None,
    }
    if all(v is None for v in fields.values()):
        flash("no values entered — nothing recorded")
        return redirect(url_for("candidates"))
    store.add_settings_observation(
        player_id, row["observed_at"], "manual",
        channel=row["channel"], raw_text=row["text"],
        source_message_id=message_id, **fields,
    )
    store.commit()
    flash("manual observation recorded")
    return redirect(url_for("candidates"))


@bp.post("/actions/candidates/<int:message_id>/dismiss")
def dismiss(message_id: int):
    store = _store()
    store.dismiss_twitch_message(message_id, now_utc())
    store.commit()
    flash("candidate dismissed")
    return redirect(url_for("candidates"))
