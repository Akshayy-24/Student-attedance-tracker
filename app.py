"""Smart Attendance Tracker - Flask web app. Run: python app.py"""
import sqlite3
from datetime import date
from functools import wraps
from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash
import core

app = Flask(__name__)
app.secret_key = "change-me-in-production"
core.init()

def login_required(role=None):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if "uid" not in session: return redirect(url_for("login"))
            if role and session["role"] != role: abort(403)
            return f(*a, **k)
        return w
    return deco

@app.route("/")
def index():
    if "uid" not in session: return redirect(url_for("login"))
    return redirect(url_for("dashboard" if session["role"] == "teacher" else "me"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = core.db().execute("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session.update(uid=u["id"], role=u["role"], username=u["username"], student_id=u["student_id"])
            return redirect(url_for("index"))
        flash("Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------- Teacher ----------
@app.route("/dashboard")
@login_required("teacher")
def dashboard():
    rows = core.stats(); tr = core.trend(); ca = core.course_avg()
    n_sessions = rows[0]["total"] if rows else 0
    return render_template("dashboard.html", rows=rows, defaulters=[r for r in rows if r["defaulter"]],
        avg=round(sum(r["pct"] for r in rows) / len(rows), 1) if rows else 0, sessions=n_sessions,
        tiers=core.tiers(rows), trend_labels=[t[0] for t in tr], trend_vals=[t[1] for t in tr],
        course_labels=[c[0] for c in ca], course_vals=[c[1] for c in ca])

@app.route("/students")
@login_required("teacher")
def students():
    return render_template("students.html", rows=core.stats())

@app.route("/students/save", methods=["GET", "POST"])
@app.route("/students/save/<int:sid>", methods=["GET", "POST"])
@login_required("teacher")
def student_form(sid=None):
    c = core.db()
    s = c.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone() if sid else None
    if request.method == "POST":
        roll, name, email = (request.form[k].strip() for k in ("roll_no", "name", "email"))
        if not roll or not name:
            flash("Roll number and name are required")
        else:
            try:
                if s: c.execute("UPDATE students SET roll_no=?,name=?,email=? WHERE id=?", (roll, name, email, sid)); c.commit()
                else: core.add_student(roll, name, email, c); c.commit()
                flash("Student saved (new students log in with roll no as password)")
                return redirect(url_for("students"))
            except sqlite3.IntegrityError:
                flash("Roll number already exists")
    return render_template("form.html", s=s)

@app.route("/students/delete/<int:sid>", methods=["POST"])
@login_required("teacher")
def student_delete(sid):
    c = core.db(); c.execute("DELETE FROM users WHERE student_id=?", (sid,))
    c.execute("DELETE FROM students WHERE id=?", (sid,)); c.commit()
    flash("Student deleted"); return redirect(url_for("students"))

@app.route("/sessions", methods=["GET", "POST"])
@login_required("teacher")
def sessions():
    c = core.db()
    if request.method == "POST":
        c.execute("INSERT INTO sessions(course,topic,date,created_by) VALUES(?,?,?,?)",
                  (request.form["course"].strip(), request.form["topic"].strip(),
                   request.form["date"], session["username"]))
        c.commit(); flash("Session created")
    rows = c.execute("SELECT * FROM sessions ORDER BY date DESC, id DESC").fetchall()
    return render_template("sessions.html", rows=rows, today=date.today().isoformat())

@app.route("/sessions/<int:sid>/roll", methods=["GET", "POST"])
@login_required("teacher")
def roll(sid):
    c = core.db(); se = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone() or abort(404)
    studs = c.execute("SELECT * FROM students ORDER BY roll_no").fetchall()
    if request.method == "POST":
        for s in studs:
            core.mark(sid, s["id"], request.form.get(f"s{s['id']}", "Absent"), c)
        c.commit(); flash("Roll call saved"); return redirect(url_for("sessions"))
    cur = {r["student_id"]: r["status"] for r in c.execute("SELECT * FROM attendance WHERE session_id=?", (sid,))}
    return render_template("roll.html", se=se, studs=studs, cur=cur)

@app.route("/scanner/<int:sid>")
@login_required("teacher")
def scanner(sid):
    se = core.db().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone() or abort(404)
    return render_template("scanner.html", se=se)

@app.route("/api/scan", methods=["POST"])
@login_required("teacher")
def api_scan():
    d = request.get_json(force=True)
    return jsonify(core.scan(int(d["session_id"]), str(d["code"]).strip()))

@app.route("/reports")
@login_required("teacher")
def reports():
    only = request.args.get("defaulters") == "1"
    rows = [r for r in core.stats() if r["defaulter"] or not only]
    return render_template("reports.html", rows=rows, only=only)

@app.route("/reports/export")
@login_required("teacher")
def export():
    return send_file(core.export_xlsx(), as_attachment=True, download_name="attendance_report.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/alerts/send", methods=["POST"])
@login_required("teacher")
def alerts_send():
    sid = request.form.get("student_id")
    n = core.send_alerts(session["username"], {int(sid)} if sid else None)
    flash(f"{n} warning(s) recorded in the alert log"); return redirect(url_for("alerts"))

@app.route("/alerts")
@login_required("teacher")
def alerts():
    rows = core.db().execute("""SELECT a.*, s.name, s.roll_no FROM alerts a JOIN students s ON s.id=a.student_id
                                ORDER BY a.id DESC""").fetchall()
    return render_template("alerts.html", rows=rows)

# ---------- Shared / Student ----------
@app.route("/badge/<int:sid>.png")
@login_required()
def badge(sid):
    if session["role"] == "student" and session["student_id"] != sid: abort(403)
    s = core.db().execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone() or abort(404)
    return send_file(core.badge_png(s), mimetype="image/png", download_name=f"badge_{s['roll_no']}.png")

@app.route("/me")
@login_required("student")
def me():
    sid = session["student_id"]; st = core.stats(sid)[0]
    subs = core.subject_stats(sid)
    al = core.db().execute("SELECT * FROM alerts WHERE student_id=? ORDER BY id DESC", (sid,)).fetchall()
    return render_template("me.html", s=st, subs=subs, alerts=al,
                           labels=[x["course"] for x in subs], vals=[x["pct"] for x in subs])

if __name__ == "__main__":
    app.run(debug=True)
