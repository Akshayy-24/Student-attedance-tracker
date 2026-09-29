"""Shared core logic (used by both the Flask web app and the CLI)."""
import io, os, random, secrets, sqlite3, time
from datetime import date, timedelta
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "attendance.db")
THRESHOLD = 75.0
DEBOUNCE_SECONDS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT, student_id INTEGER);
CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, roll_no TEXT UNIQUE NOT NULL, name TEXT NOT NULL, email TEXT, qr_token TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, course TEXT NOT NULL, topic TEXT, date TEXT NOT NULL, created_by TEXT);
CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('Present','Late','Absent')),
  marked_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(session_id, student_id));
CREATE TABLE IF NOT EXISTS scan_log(id INTEGER PRIMARY KEY, session_id INTEGER, student_id INTEGER, ts REAL, scanned_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES students(id) ON DELETE CASCADE, pct REAL, message TEXT, sent_by TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init(seed=True):
    c = db(); c.executescript(SCHEMA)
    if seed and not c.execute("SELECT 1 FROM users").fetchone():
        c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                  ("teacher", generate_password_hash("teacher123"), "teacher"))
        names = ["Akshay", "Rahul", "Nithin", "Kiran", "Vinay", "Abhilash"]
        for i, n in enumerate(names, 1):
            add_student(f"CS{100+i}", n, f"{n.lower()}@example.com", c)
        random.seed(1)
        for k in range(8):
            d = (date.today() - timedelta(days=k)).isoformat()
            course = ["Python", "DBMS"][k % 2]
            sid = c.execute("INSERT INTO sessions(course,topic,date,created_by) VALUES(?,?,?,?)",
                            (course, f"Lecture {k+1}", d, "teacher")).lastrowid
            for st in c.execute("SELECT id FROM students").fetchall():
                bias = 0.9 if st["id"] % 3 else 0.55  # some students become defaulters
                r = random.random()
                s = "Present" if r < bias else ("Late" if r < bias + .05 else "Absent")
                c.execute("INSERT INTO attendance(session_id,student_id,status) VALUES(?,?,?)", (sid, st["id"], s))
    c.commit(); c.close()

def add_student(roll, name, email, c=None):
    own = c is None; c = c or db()
    sid = c.execute("INSERT INTO students(roll_no,name,email,qr_token) VALUES(?,?,?,?)",
                    (roll, name, email, secrets.token_hex(8))).lastrowid
    c.execute("INSERT INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)",
              (roll, generate_password_hash(roll), "student", sid))  # default password = roll no
    if own: c.commit(); c.close()
    return sid

def pct(p, l, total):
    return round((p + 0.5 * l) / total * 100, 1) if total else 100.0

def stats(student_id=None):
    """Overall attendance per student. Effective = Present + 0.5*Late."""
    c = db(); total = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    q = ("SELECT s.*, COALESCE(SUM(a.status='Present'),0) p, COALESCE(SUM(a.status='Late'),0) l "
         "FROM students s LEFT JOIN attendance a ON a.student_id=s.id")
    args = ()
    if student_id: q += " WHERE s.id=?"; args = (student_id,)
    out = []
    for r in c.execute(q + " GROUP BY s.id ORDER BY s.roll_no", args):
        d = dict(r); d["total"] = total; d["a"] = total - d["p"] - d["l"]
        d["pct"] = pct(d["p"], d["l"], total); d["defaulter"] = d["pct"] < THRESHOLD
        out.append(d)
    c.close(); return out

def subject_stats(student_id):
    c = db()
    rows = c.execute("""SELECT se.course, COUNT(DISTINCT se.id) total,
        COALESCE(SUM(a.status='Present'),0) p, COALESCE(SUM(a.status='Late'),0) l
        FROM sessions se LEFT JOIN attendance a ON a.session_id=se.id AND a.student_id=?
        GROUP BY se.course""", (student_id,)).fetchall()
    c.close()
    return [{"course": r["course"], "pct": pct(r["p"], r["l"], r["total"])} for r in rows]

def course_avg():
    c = db(); n = c.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    rows = c.execute("""SELECT se.course, COUNT(DISTINCT se.id) ns,
        COALESCE(SUM(a.status='Present'),0)+0.5*COALESCE(SUM(a.status='Late'),0) eff
        FROM sessions se LEFT JOIN attendance a ON a.session_id=se.id GROUP BY se.course""").fetchall()
    c.close()
    return [(r["course"], round(r["eff"] / (r["ns"] * n) * 100, 1) if n else 0) for r in rows]

def trend(days=14):
    c = db(); n = c.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = c.execute("""SELECT se.date d, COUNT(DISTINCT se.id) ns,
        COALESCE(SUM(a.status='Present'),0)+0.5*COALESCE(SUM(a.status='Late'),0) eff
        FROM sessions se LEFT JOIN attendance a ON a.session_id=se.id
        WHERE se.date>=? GROUP BY se.date ORDER BY se.date""", (start,)).fetchall()
    c.close()
    return [(r["d"], round(r["eff"] / (r["ns"] * n) * 100, 1) if n else 0) for r in rows]

def tiers(rows):
    t = [0, 0, 0, 0]  # >=90, 75-89, 60-74, <60
    for r in rows:
        p = r["pct"]; t[0 if p >= 90 else 1 if p >= 75 else 2 if p >= 60 else 3] += 1
    return t

def mark(session_id, student_id, status, c=None):
    own = c is None; c = c or db()
    c.execute("""INSERT INTO attendance(session_id,student_id,status) VALUES(?,?,?)
                 ON CONFLICT(session_id,student_id) DO UPDATE SET status=excluded.status,
                 marked_at=CURRENT_TIMESTAMP""", (session_id, student_id, status))
    if own: c.commit(); c.close()

def scan(session_id, code):
    """QR scan (or manual roll no). Debounced; logs every accepted scan."""
    c = db()
    if not c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
        return {"ok": False, "msg": "Session not found"}
    s = c.execute("SELECT * FROM students WHERE qr_token=? OR roll_no=?", (code, code)).fetchone()
    if not s: return {"ok": False, "msg": "Unknown QR / roll number"}
    now = time.time()
    last = c.execute("SELECT MAX(ts) FROM scan_log WHERE student_id=? AND session_id=?", (s["id"], session_id)).fetchone()[0]
    if last and now - last < DEBOUNCE_SECONDS:
        return {"ok": False, "dup": True, "msg": f"{s['name']}: duplicate scan ignored"}
    c.execute("INSERT INTO scan_log(session_id,student_id,ts) VALUES(?,?,?)", (session_id, s["id"], now))
    prev = c.execute("SELECT status FROM attendance WHERE session_id=? AND student_id=?", (session_id, s["id"])).fetchone()
    if prev and prev["status"] == "Present":
        c.commit(); return {"ok": True, "msg": f"{s['name']} ({s['roll_no']}) already marked present"}
    mark(session_id, s["id"], "Present", c); c.commit(); c.close()
    return {"ok": True, "msg": f"✔ {s['name']} ({s['roll_no']}) marked present"}

def send_alerts(sender, ids=None):
    rows = [r for r in stats() if r["defaulter"] and (not ids or r["id"] in ids)]
    c = db()
    for r in rows:
        msg = (f"Warning: your attendance is {r['pct']}%, below the mandatory {THRESHOLD:.0f}%. "
               "Please attend upcoming lectures and contact your faculty.")
        c.execute("INSERT INTO alerts(student_id,pct,message,sent_by) VALUES(?,?,?,?)", (r["id"], r["pct"], msg, sender))
    c.commit(); c.close(); return len(rows)

def badge_png(s):
    """360x480 printable ID badge with unique QR."""
    import qrcode
    from PIL import Image, ImageDraw, ImageFont
    def font(sz):
        for f in ("DejaVuSans-Bold.ttf", "arial.ttf"):
            try: return ImageFont.truetype(f, sz)
            except OSError: pass
        return ImageFont.load_default()
    img = Image.new("RGB", (360, 480), "white"); d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 360, 70), fill=(28, 37, 110))
    d.text((180, 35), "STUDENT ID", font=font(26), fill="white", anchor="mm")
    img.paste(qrcode.make(s["qr_token"]).convert("RGB").resize((280, 280)), (40, 90))
    d.text((180, 405), s["name"], font=font(24), fill="black", anchor="mm")
    d.text((180, 440), "Roll No: " + s["roll_no"], font=font(18), fill=(60, 60, 60), anchor="mm")
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0); return buf

def export_xlsx():
    import xlsxwriter
    buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True}); ws = wb.add_worksheet("Attendance")
    hdr = wb.add_format({"bold": True, "bg_color": "#1c256e", "font_color": "white", "border": 1})
    heads = ["Roll No", "Name", "Present", "Late", "Absent", "Total Sessions", "Percentage", "Status"]
    for i, h in enumerate(heads): ws.write(0, i, h, hdr)
    rows = stats()
    for r, s in enumerate(rows, 1):
        for i, v in enumerate([s["roll_no"], s["name"], s["p"], s["l"], s["a"], s["total"], s["pct"],
                               "DEFAULTER" if s["defaulter"] else "OK"]): ws.write(r, i, v)
    red = wb.add_format({"bg_color": "#ffc7ce", "font_color": "#9c0006"})
    ws.conditional_format(1, 6, max(len(rows), 1), 6, {"type": "cell", "criteria": "<", "value": THRESHOLD, "format": red})
    ws.set_column(0, 1, 16); ws.set_column(2, 7, 14); wb.close(); buf.seek(0); return buf
