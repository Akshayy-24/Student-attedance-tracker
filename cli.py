"""Command-line mode: same core logic as the web app.
Usage: python cli.py report | defaulters | mark SESSION_ID ROLL_NO [Present|Late|Absent] | sessions | alert"""
import sys
from tabulate import tabulate
import core

def main(a):
    core.init()
    cmd = a[0] if a else "report"
    if cmd in ("report", "defaulters"):
        rows = [r for r in core.stats() if cmd == "report" or r["defaulter"]]
        print(tabulate([[r["roll_no"], r["name"], r["p"], r["l"], r["a"], r["total"], f"{r['pct']}%",
                         "DEFAULTER" if r["defaulter"] else "OK"] for r in rows],
                       ["Roll", "Name", "P", "L", "A", "Total", "Pct", "Status"], tablefmt="grid"))
        print(f"\nEffective = Present + 0.5*Late; threshold = {core.THRESHOLD}%")
    elif cmd == "sessions":
        c = core.db()
        print(tabulate([tuple(r) for r in c.execute("SELECT id,course,topic,date FROM sessions ORDER BY date DESC")],
                       ["ID", "Course", "Topic", "Date"]))
    elif cmd == "mark" and len(a) >= 3:
        st = a[3] if len(a) > 3 else "Present"
        if st == "Present": print(core.scan(int(a[1]), a[2])["msg"])
        else:
            c = core.db(); s = c.execute("SELECT id FROM students WHERE roll_no=?", (a[2],)).fetchone()
            if not s: return print("Unknown roll no")
            core.mark(int(a[1]), s["id"], st); print("Saved")
    elif cmd == "alert":
        print(f"{core.send_alerts('cli')} alert(s) logged")
    else:
        print(__doc__)

if __name__ == "__main__":
    main(sys.argv[1:])
