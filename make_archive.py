#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL-Ledger Archive Maker — no database installation required.

Usage:  place this script next to your SQL-Ledger backup files (.sql)
        and run it:   python3 make_archive.py
        (or double-click make-archive.cmd / make-archive.command)

The script reads the backups directly (no Docker or PostgreSQL needed),
detects the date format (dd-mm-yyyy or mm/dd/yy) automatically, and creates
in the folder 'archive' a browsable HTML archive per company: balance sheet,
profit & loss in two views (operational and as posted), trial balance,
general ledger, journal, invoices and open items, plus a dashboard with
yearly charts. Works with SQL-Ledger 2.6 through 3.x backups.

Copyright (C) 2026 Matthijs Havinga

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option)
any later version. This program is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file LICENSE
or <https://www.gnu.org/licenses/> for details.
"""
import sqlite3, os, sys, re, html, math, glob, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "archive")
MONTHS = ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")
TODAY = f"{datetime.date.today().day} {MONTHS[datetime.date.today().month-1]} {datetime.date.today().year}"
CAT_NAME = {"A": "Assets", "L": "Liabilities", "Q": "Equity", "I": "Income", "E": "Expenses"}

# ----------------------------------------------------------------------
#  Helpers
# ----------------------------------------------------------------------
def money(x):
    x = float(x or 0)
    s = f"{abs(x):,.2f}"
    return ("-" + s) if x < -0.004 else s

def money0(x):
    x = float(x or 0)
    s = f"{abs(x):,.0f}"
    return ("− € " + s) if x < 0 else ("€ " + s)

def pctf(x): return f"{x:.1f}%"
def esc(s): return html.escape(s or "")

def fmt_date(d):
    if not d: return ""
    try:
        j, m, day = d[:10].split("-")
        return f"{int(day)} {MONTHS[int(m)-1]} {j}"
    except Exception:
        return d

# ----------------------------------------------------------------------
#  Backup parser: reads a SQL-Ledger dump into a SQLite database
# ----------------------------------------------------------------------
DATE_RE = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})$")

def read_statements(path):
    """Yields complete INSERT statements (they may span multiple lines)."""
    enc = "utf-8"
    try:
        open(path, encoding="utf-8").read(1 << 20)
    except UnicodeDecodeError:
        enc = "latin-1"
    buf, in_insert = [], False
    with open(path, encoding=enc, errors="replace") as f:
        for line_txt in f:
            if not in_insert:
                if line_txt.startswith("INSERT INTO"):
                    buf = [line_txt]; in_insert = True
                else:
                    continue
            else:
                buf.append(line_txt)
            # statement is complete when the quote count is even and it ends with );
            s = "".join(buf)
            if s.rstrip().endswith(");") and s.count("'") % 2 == 0:
                yield s
                buf, in_insert = [], False

INS_RE = re.compile(r"^INSERT INTO (\w+)\s*\(([^)]*)\)\s*VALUES\s*\(", re.S)

def split_values(s):
    """Splits the VALUES(...) contents on top-level commas, honouring '' escapes."""
    out, cur, in_str, depth, i, n = [], [], False, 0, 0, len(s)
    while i < n:
        c = s[i]
        if in_str:
            if c == "'":
                if i + 1 < n and s[i+1] == "'":
                    cur.append("'"); i += 2; continue
                in_str = False
            else:
                cur.append(c)
        else:
            if c == "'":
                in_str = True
            elif c == "(":
                depth += 1; cur.append(c)
            elif c == ")":
                depth -= 1; cur.append(c)
            elif c == "," and depth == 0:
                out.append("".join(cur)); cur = []
            else:
                cur.append(c)
        i += 1
    out.append("".join(cur))
    return out

def parse_insert(stmt):
    m = INS_RE.match(stmt)
    if not m: return None
    table_name = m.group(1).lower()
    columns = [k.strip().lower() for k in m.group(2).split(",")]
    rest = stmt[m.end():].rstrip()
    if rest.endswith(");"): rest = rest[:-2]
    raw = split_values(rest)
    values_list = []
    voor_quote = stmt[m.end():]
    # determine per value whether it was quoted (string) or bare (number/NULL)
    bare = [w.strip() for w in raw]
    # reconstruction: split_values stripped the quotes; recognise bare NULL:
    for k in bare:
        values_list.append(None if k == "NULL" else k)
    if len(values_list) != len(columns): return None
    return table_name, columns, values_list

def detect_date_format(path):
    """Counts violations for DMY and MDY across all date-like values."""
    err_dmy = err_mdy = total = 0
    for stmt in read_statements(path):
        r = parse_insert(stmt)
        if not r: continue
        for w in r[2]:
            if w is None: continue
            m = DATE_RE.match(w)
            if not m: continue
            a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if len(m.group(1)) == 4:  # already ISO
                continue
            total += 1
            if a > 12: err_mdy += 1   # first field > 12 cannot be a month
            if b > 12: err_dmy += 1   # second field > 12 cannot be a month
    if total == 0: return "DMY", 0, 0
    return ("DMY" if err_dmy <= err_mdy else "MDY"), err_dmy, err_mdy

def to_iso(w, fmt):
    m = DATE_RE.match(w or "")
    if not m: return w
    a, b, c = m.group(1), m.group(2), m.group(3)
    if len(a) == 4:                       # yyyy-mm-dd
        return f"{a}-{int(b):02d}-{int(c):02d}"
    year = int(c)
    if year < 100: year += 2000 if year < 70 else 1900
    if fmt == "DMY": day, month = int(a), int(b)
    else:            month, day = int(a), int(b)
    if month > 12: month, day = day, month        # last resort
    return f"{year:04d}-{month:02d}-{day:02d}"

DATE_COLUMNS = {"transdate", "duedate", "datepaid", "startdate", "enddate", "shippingdate",
              "reqdate", "quodate", "orddate", "validto", "employeeuntil", "employeesince",
              "closedto", "audittime", "birthdate", "checkdate", "exchangeratedate"}

# columns the reports rely on; missing ones are created empty
EXPECTED = {
    "acc_trans": ["trans_id", "chart_id", "amount", "transdate", "source", "memo"],
    "chart":     ["id", "accno", "description", "category"],
    "ar":        ["id", "invnumber", "customer_id", "transdate", "duedate", "amount",
                  "netamount", "paid", "datepaid", "curr", "ordnumber"],
    "ap":        ["id", "invnumber", "vendor_id", "transdate", "duedate", "amount",
                  "netamount", "paid", "datepaid", "curr", "ordnumber"],
    "gl":        ["id", "reference", "description", "transdate"],
    "customer":  ["id", "name"],
    "vendor":    ["id", "name"],
    "invoice":   ["trans_id", "parts_id", "description", "qty", "sellprice", "discount"],
    "parts":     ["id", "partnumber", "description", "unit"],
    "yearend":   ["trans_id"],
    "defaults":  ["fldname", "fldvalue"],
}

def load_backup(path, fmt):
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA journal_mode=OFF"); con.execute("PRAGMA synchronous=OFF")
    cur = con.cursor()
    tables = {}
    for stmt in read_statements(path):
        r = parse_insert(stmt)
        if not r: continue
        table_name, columns, values_list = r
        if table_name not in tables:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(columns)})")
            tables[table_name] = set(columns)
        else:
            for k in columns:
                if k not in tables[table_name]:
                    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {k}")
                    tables[table_name].add(k)
        row_vals = []
        for k, w in zip(columns, values_list):
            if w is not None and (k in DATE_COLUMNS or (DATE_RE.match(w) and k.endswith("date"))):
                w = to_iso(w, fmt)
            row_vals.append(w)
        cur.execute(f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({','.join('?'*len(row_vals))})", row_vals)
    # add missing tables/columns so the reports never crash
    for table_name, cols in EXPECTED.items():
        if table_name not in tables:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(cols)})")
            tables[table_name] = set(cols)
        else:
            for k in cols:
                if k not in tables[table_name]:
                    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {k}")
                    tables[table_name].add(k)
    con.commit()
    return con

# ----------------------------------------------------------------------
#  HTML basics
# ----------------------------------------------------------------------
CSS = """
/* Same look as PyBalans: calm grey page, white panels, navy accent; follows dark mode. */
:root{color-scheme:light dark;--bg:#f6f7f9;--panel:#fff;--text:#1f2933;--muted:#6b7684;--line:#d9dee5;
 --accent:#1f4e79;--accent-soft:#e6eef6;--topbar:#1f4e79;--stripe:#fafbfc;--ok:#1d6b34;--err:#a32020;
 font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text)}
@media (prefers-color-scheme: dark){:root{--bg:#15191e;--panel:#1d2228;--text:#e4e8ed;--muted:#95a0ad;--line:#333b45;
 --accent:#7fb0e0;--accent-soft:#23303d;--topbar:#12304d;--stripe:#20262d;--ok:#8fd3a2;--err:#f0a0a0}}
*{box-sizing:border-box}
body{margin:0;padding:0 0 40px 0;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
.top{background:var(--topbar);color:#fff;padding:.6rem 1.2rem;font-size:15px;font-weight:700}
.top a{color:#fff}
.crumb{padding:.5rem 1.2rem;font-size:12px;color:var(--muted);background:var(--panel);border-bottom:1px solid var(--line)}
h1{font-size:1.35rem;font-weight:600;margin:1.1rem 1.2rem .3rem}
h2{font-size:1.02rem;font-weight:600;margin:1.3rem 1.2rem .4rem;color:var(--accent)}
.sub{margin:.3rem 1.2rem .9rem;color:var(--muted);font-size:12px}
table{border-collapse:collapse;margin:.5rem 1.2rem;background:var(--panel);border:1px solid var(--line);border-radius:4px}
th{background:var(--accent-soft);color:var(--text);padding:.4rem .75rem;text-align:left;font-size:12px;font-weight:600;border-bottom:1px solid var(--line)}
td{padding:.32rem .75rem;border-bottom:1px solid var(--line);vertical-align:top}
tr:nth-child(even) td{background:var(--stripe)}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.totals td{border-top:2px solid var(--accent);font-weight:600;background:var(--accent-soft)}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:1rem 1.2rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:.8rem 1.1rem;min-width:170px}
.card b{display:block;margin-bottom:.4rem;color:var(--accent);font-size:15px}
.card a{display:block;font-size:13px;margin:2px 0}
.large{font-size:16px;padding:1.2rem 1.6rem}
small{color:var(--muted)}
"""

def page_html(company, title, body, path=""):
    breadcrumb = "" if title == "Overview" else f'<div class="crumb"><a href="{path}../index.html">Archive</a> &rsaquo; <a href="{path}index.html">{esc(company)}</a> &rsaquo; {title}</div>'
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(company)} — {title}</title><link rel="stylesheet" href="{path}../archive.css"></head><body>
<div class="top">SQL-Ledger archive — {esc(company)}</div>{breadcrumb}{body}
<p class="sub" style="margin-top:30px">Static archive, generated on {TODAY} from the SQL-Ledger backup. Read-only.</p></body></html>"""

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# ----------------------------------------------------------------------
#  Archive per company (SQLite variant)
# ----------------------------------------------------------------------
def build_company(con, slug):
    cur = con.cursor()
    def q(sql, *args):
        cur.execute(sql, args); return cur.fetchall()
    def qtry(sql, *args):
        try:
            cur.execute(sql, args); return cur.fetchall()
        except sqlite3.Error:
            return []
    def f(x): return float(x or 0)

    d = dict(qtry("SELECT fldname, fldvalue FROM defaults WHERE fldname IN ('company','businessnumber')"))
    COMPANY, REG_NO = d.get("company") or slug, d.get("businessnumber") or ""
    base = os.path.join(OUT_DIR, slug); os.makedirs(base, exist_ok=True)
    years = [int(r[0]) for r in q("SELECT DISTINCT substr(transdate,1,4) FROM acc_trans WHERE transdate IS NOT NULL ORDER BY 1")]
    if not years:
        print(f"  !! {slug}: no transactions found, skipped"); return None

    refs, ar_info, ap_info = {}, {}, {}
    for tid, inv, name, td, dd, amt, net, paid, dp, curr, ordn in qtry(
        """SELECT a.id, a.invnumber, c.name, a.transdate, a.duedate, a.amount,
                  a.netamount, a.paid, a.datepaid, a.curr, a.ordnumber
           FROM ar a LEFT JOIN customer c ON c.id=a.customer_id"""):
        refs[int(tid)] = ("Sales", inv or "", name or "")
        ar_info[int(tid)] = (inv, name, td, dd, f(amt), f(net), f(paid), dp, curr, ordn)
    for tid, inv, name, td, dd, amt, net, paid, dp, curr, ordn in qtry(
        """SELECT a.id, a.invnumber, v.name, a.transdate, a.duedate, a.amount,
                  a.netamount, a.paid, a.datepaid, a.curr, a.ordnumber
           FROM ap a LEFT JOIN vendor v ON v.id=a.vendor_id"""):
        refs[int(tid)] = ("Purchase", inv or "", name or "")
        ap_info[int(tid)] = (inv, name, td, dd, f(amt), f(net), f(paid), dp, curr, ordn)
    for tid, ref, desc in qtry("SELECT id, reference, description FROM gl"):
        refs[int(tid)] = ("GL entry", ref or "", desc or "")
    year_end_ids = {int(r[0]) for r in qtry("SELECT trans_id FROM yearend") if r[0] is not None}
    for tid in year_end_ids:
        if tid in refs: refs[tid] = ("Year-end closing", refs[tid][1], refs[tid][2])
    has_ye_table = bool(qtry("SELECT name FROM sqlite_master WHERE name='yearend'"))
    ye_filter = "AND t.trans_id NOT IN (SELECT trans_id FROM yearend)" if has_ye_table else ""

    inv_lines = defaultdict(list)
    for tid, pnum, desc, qty, sell, disc, unit in qtry(
        """SELECT i.trans_id, p.partnumber, coalesce(nullif(i.description,''), p.description),
                  i.qty, i.sellprice, coalesce(i.discount,0), p.unit
           FROM invoice i LEFT JOIN parts p ON p.id=i.parts_id ORDER BY i.rowid"""):
        inv_lines[int(tid)].append((pnum, desc, f(qty), f(sell), f(disc), unit))
    heeft_pagina = set(ar_info) | set(ap_info)

    year_data = []
    for year in years:
        js = f"{year:04d}"
        jd = os.path.join(base, str(year)); os.makedirs(jd, exist_ok=True)
        os.makedirs(os.path.join(jd, "invoices"), exist_ok=True)

        # ---------- P&L (two views) ----------
        def wv_pagina(filename, title, exclude_closing, note):
            extra = ye_filter if exclude_closing else ""
            rows = q(f"""SELECT c.accno, c.description, c.category, sum(t.amount)
                        FROM acc_trans t JOIN chart c ON c.id=t.chart_id
                        WHERE substr(t.transdate,1,4)=? AND c.category IN ('I','E') {extra}
                        GROUP BY 1,2,3 HAVING abs(sum(t.amount)) >= 0.005 ORDER BY 1""", js)
            sections, totals = {"I": [], "E": []}, {"I": 0.0, "E": 0.0}
            for accno, descr, cat, b in rows:
                shown = f(b) if cat == "I" else -f(b)
                sections[cat].append((accno, descr, shown)); totals[cat] += shown
            body = ""
            for cat in ("I", "E"):
                body += f"<h2>{CAT_NAME[cat]}</h2><table><tr><th>Account</th><th>Description</th><th class=r>Amount</th></tr>"
                for accno, descr, b in sections[cat]:
                    body += f"<tr><td>{accno}</td><td><a href='ledger.html#a{accno}'>{esc(descr)}</a></td><td class=r>{money(b)}</td></tr>"
                body += f"<tr class=totals><td colspan=2>Total {CAT_NAME[cat].lower()}</td><td class=r>{money(totals[cat])}</td></tr></table>"
            res = totals["I"] - totals["E"]
            body += f"<table><tr class=totals><td>Result {year} ({'profit' if res>=0 else 'loss'})</td><td class=r>{money(res)}</td></tr></table>"
            body += f"<p class=sub>{note}</p>"
            write_file(f"{jd}/{filename}", page_html(COMPANY, f"{title} {year}", f"<h1>{title} {year}</h1>"+body, "../"))
            return totals["I"], totals["E"], res

        if year_end_ids:
            o, k, res = wv_pagina("profit-loss.html", "Profit & loss (operational)", True,
                "View without the year-end closing entry (result booked to equity); "
                "see also the <a href='profit-loss-as-posted.html'>P&amp;L as posted</a>.")
            wv_pagina("profit-loss-as-posted.html", "Profit & loss (as posted)", False,
                "View including the year-end closing entry: for a closed financial year all "
                "income and expense accounts add up to (virtually) zero here. "
                "See the <a href='profit-loss.html'>operational P&amp;L</a> for the figures before closing.")
        else:
            o, k, res = wv_pagina("profit-loss.html", "Profit & loss", True,
                "No year-end closing entries were found in this company.")
        year_data.append((year, o, k, res))

        # ---------- Balance sheet ----------
        rows = q("""SELECT c.accno, c.description, c.category, sum(t.amount)
                    FROM acc_trans t JOIN chart c ON c.id=t.chart_id WHERE t.transdate <= ?
                    GROUP BY 1,2,3 HAVING abs(sum(t.amount)) >= 0.005 ORDER BY 1""", f"{js}-12-31")
        bal_sections, bal_totals, result_sum = {"A": [], "L": [], "Q": []}, {"A": 0.0, "L": 0.0, "Q": 0.0}, 0.0
        for accno, descr, cat, b in rows:
            if cat in ("I", "E"): result_sum += f(b); continue
            doel = cat if cat in bal_sections else "Q"
            shown = -f(b) if cat == "A" else f(b)
            bal_sections[doel].append((accno, descr, shown)); bal_totals[doel] += shown
        bal_sections["Q"].append(("", f"Retained result through {year}", result_sum)); bal_totals["Q"] += result_sum
        body = ""
        for cat in ("A", "L", "Q"):
            body += f"<h2>{CAT_NAME[cat]}</h2><table><tr><th>Account</th><th>Description</th><th class=r>Balance</th></tr>"
            for accno, descr, b in bal_sections[cat]:
                link = f"<a href='ledger.html#a{accno}'>{esc(descr)}</a>" if accno else esc(descr)
                body += f"<tr><td>{accno}</td><td>{link}</td><td class=r>{money(b)}</td></tr>"
            body += f"<tr class=totals><td colspan=2>Total {CAT_NAME[cat].lower()}</td><td class=r>{money(bal_totals[cat])}</td></tr></table>"
        v = bal_totals["A"] - bal_totals["L"] - bal_totals["Q"]
        body += f"<p class=sub>Check: assets {money(bal_totals['A'])} = liabilities + equity {money(bal_totals['L']+bal_totals['Q'])}" + (" ✓" if abs(v) < 0.011 else f" — DIFFERENCE {money(v)}") + "</p>"
        if abs(v) >= 0.011:
            print(f"  !! {slug} {year}: balance sheet does NOT balance (difference {money(v)})")
        write_file(f"{jd}/balance.html", page_html(COMPANY, f"Balance sheet {year}", f"<h1>Balance sheet as at 31 Dec {year}</h1>"+body, "../"))

        # ---------- Trial balance ----------
        rows = q("""SELECT c.accno, c.description,
                      sum(CASE WHEN t.transdate < ? THEN t.amount ELSE 0 END),
                      sum(CASE WHEN t.transdate >= ? AND CAST(t.amount AS REAL) < 0 THEN -t.amount ELSE 0 END),
                      sum(CASE WHEN t.transdate >= ? AND CAST(t.amount AS REAL) > 0 THEN t.amount ELSE 0 END)
                    FROM acc_trans t JOIN chart c ON c.id=t.chart_id WHERE t.transdate <= ?
                    GROUP BY 1,2
                    HAVING abs(sum(t.amount)) >= 0.005
                        OR abs(sum(CASE WHEN t.transdate >= ? AND CAST(t.amount AS REAL) < 0 THEN -t.amount ELSE 0 END)) >= 0.005
                        OR abs(sum(CASE WHEN t.transdate >= ? AND CAST(t.amount AS REAL) > 0 THEN t.amount ELSE 0 END)) >= 0.005
                    ORDER BY 1""",
                 f"{js}-01-01", f"{js}-01-01", f"{js}-01-01", f"{js}-12-31", f"{js}-01-01", f"{js}-01-01")
        def dc(x):
            if abs(x) < 0.005: return ""
            return f"{money(-x)} D" if x < 0 else f"{money(x)} C"
        body = ("<table><tr><th>Account</th><th>Description</th><th class=r>Opening balance</th>"
                "<th class=r>Debit</th><th class=r>Credit</th><th class=r>Closing balance</th></tr>")
        tot_debit = tot_credit = 0.0
        openings = {}
        for accno, descr, opening, debit_sum, credit_sum in rows:
            opening, debit_sum, credit_sum = f(opening), f(debit_sum), f(credit_sum)
            openings[accno] = opening
            eind = opening - debit_sum + credit_sum
            tot_debit += debit_sum; tot_credit += credit_sum
            body += (f"<tr><td>{accno}</td><td><a href='ledger.html#a{accno}'>{esc(descr)}</a></td>"
                     f"<td class=r>{dc(opening)}</td><td class=r>{money(debit_sum) if debit_sum else ''}</td>"
                     f"<td class=r>{money(credit_sum) if credit_sum else ''}</td><td class=r>{dc(eind)}</td></tr>")
        ok = " ✓" if abs(tot_debit - tot_credit) < 0.011 else " — DIFFERENCE!"
        body += f"<tr class=totals><td colspan=3>Total movements {year}</td><td class=r>{money(tot_debit)}</td><td class=r>{money(tot_credit)}</td><td class=r>{ok}</td></tr></table>"
        body += "<p class=sub>Balances: D = debit, C = credit. Opening balance at 1 January, closing balance at 31 December (including any year-end closing).</p>"
        write_file(f"{jd}/trial-balance.html", page_html(COMPANY, f"Trial balance {year}", f"<h1>Trial balance {year}</h1>"+body, "../"))

        # ---------- General ledger ----------
        rows = q("""SELECT c.accno, c.description, t.transdate, t.trans_id, t.amount, t.source, t.memo
                    FROM acc_trans t JOIN chart c ON c.id=t.chart_id
                    WHERE substr(t.transdate,1,4)=? ORDER BY 1, 3, 4""", js)
        per = defaultdict(list)
        for accno, descr, td, tid, b, src, memo in rows: per[(accno, descr)].append((td, int(tid), f(b), src, memo))
        body = ""
        for (accno, descr), entry_rows in sorted(per.items(), key=lambda x: (x[0][0] or "", x[0][1] or "")):
            body += (f"<h2 id='a{accno}'>{accno} — {esc(descr)}</h2><table><tr><th>Date</th><th>Entry</th>"
                     "<th>Description</th><th class=r>Debit</th><th class=r>Credit</th><th class=r>Balance</th></tr>")
            running_bal = openings.get(accno, 0.0)
            body += f"<tr><td></td><td></td><td><i>Opening balance 1 Jan {year}</i></td><td></td><td></td><td class=r>{dc(running_bal)}</td></tr>"
            for td, tid, b, src, memo in entry_rows:
                running_bal += b
                kind, ref, wie = refs.get(tid, ("", "", ""))
                descr2 = " — ".join(x for x in (ref, wie, memo or src) if x)
                link = f"<a href='invoices/f{tid}.html'>{kind}</a>" if tid in heeft_pagina else kind
                body += (f"<tr><td>{fmt_date(td)}</td><td>{link}</td><td>{esc(descr2)}</td>"
                         f"<td class=r>{money(-b) if b<0 else ''}</td><td class=r>{money(b) if b>0 else ''}</td><td class=r>{dc(running_bal)}</td></tr>")
            body += f"<tr class=totals><td colspan=5>Closing balance 31 Dec {year}</td><td class=r>{dc(running_bal)}</td></tr></table>"
        body += "<p class=sub>Balances: D = debit, C = credit. The closing balance per account equals the trial balance.</p>"
        write_file(f"{jd}/ledger.html", page_html(COMPANY, f"General ledger {year}", f"<h1>General ledger {year}</h1>"+body, "../"))

        # ---------- Journal ----------
        rows = q("""SELECT t.trans_id, t.transdate, c.accno, c.description, t.amount
                    FROM acc_trans t JOIN chart c ON c.id=t.chart_id
                    WHERE substr(t.transdate,1,4)=? ORDER BY t.transdate, t.trans_id, CAST(t.amount AS REAL) DESC""", js)
        per_txn, txn_order = defaultdict(list), []
        for tid, td, accno, descr, b in rows:
            tid = int(tid)
            if tid not in per_txn: txn_order.append((tid, td))
            per_txn[tid].append((accno, descr, f(b)))
        body = "<table><tr><th>Date</th><th>Type</th><th>Reference</th><th>Party / description</th><th class=r>Account</th><th class=r>Debit</th><th class=r>Credit</th></tr>"
        for tid, td in txn_order:
            kind, ref, wie = refs.get(tid, ("", "", ""))
            slink = f"<a href='invoices/f{tid}.html'>{kind}</a>" if tid in heeft_pagina else kind
            first = True
            for accno, descr, b in per_txn[tid]:
                c1 = f"<td id='t{tid}'>{fmt_date(td)}</td><td>{slink}</td><td>{esc(ref)}</td><td>{esc(wie)}</td>" if first else "<td></td><td></td><td></td><td></td>"
                body += f"<tr>{c1}<td>{accno} {esc(descr)}</td><td class=r>{money(-b) if b<0 else ''}</td><td class=r>{money(b) if b>0 else ''}</td></tr>"
                first = False
        body += "</table>"
        write_file(f"{jd}/journal.html", page_html(COMPANY, f"Journal {year}", f"<h1>Journal {year}</h1>"+body, "../"))

        # ---------- Invoices ----------
        for kind, info, title in (("Sales", ar_info, "Sales invoices"), ("Purchase", ap_info, "Purchase invoices")):
            items = [(tid, *vals) for tid, vals in info.items() if vals[2] and vals[2][:4] == js]
            items.sort(key=lambda x: (x[3] or "", x[1] or ""))
            body = "<table><tr><th>Invoice</th><th>Party</th><th>Date</th><th>Due date</th><th class=r>Net</th><th class=r>Total</th><th class=r>Paid</th><th class=r>Open</th></tr>"
            tot_net = tot_gross = 0.0
            for tid, inv, name, td, dd, amt, net, paid, dp, curr, ordn in items:
                open_ = abs(amt) - abs(paid)
                tot_net += abs(net); tot_gross += abs(amt)
                body += (f"<tr><td><a href='invoices/f{tid}.html'>{esc(inv) or tid}</a></td><td>{esc(name)}</td>"
                         f"<td>{fmt_date(td)}</td><td>{fmt_date(dd)}</td><td class=r>{money(abs(net))}</td><td class=r>{money(abs(amt))}</td>"
                         f"<td class=r>{money(abs(paid))}</td><td class=r>{money(open_) if abs(open_)>=0.005 else ''}</td></tr>")
            body += f"<tr class=totals><td colspan=4>Total {len(items)} invoices</td><td class=r>{money(tot_net)}</td><td class=r>{money(tot_gross)}</td><td></td><td></td></tr></table>"
            write_file(f"{jd}/{kind.lower()}-invoices.html", page_html(COMPANY, f"{title} {year}", f"<h1>{title} {year}</h1>"+body, "../"))
            for tid, inv, name, td, dd, amt, net, paid, dp, curr, ordn in items:
                header_html = f"""<h1>{kind} invoice {esc(inv) or tid}</h1>
<table><tr><td><b>Party</b></td><td>{esc(name)}</td></tr>
<tr><td><b>Invoice date</b></td><td>{fmt_date(td)}</td></tr>
<tr><td><b>Due date</b></td><td>{fmt_date(dd)}</td></tr>
{'<tr><td><b>Order number</b></td><td>'+esc(ordn)+'</td></tr>' if ordn else ''}
<tr><td><b>Net</b></td><td>{money(abs(net))} {esc(curr)}</td></tr>
<tr><td><b>VAT</b></td><td>{money(abs(amt)-abs(net))}</td></tr>
<tr><td><b>Total</b></td><td><b>{money(abs(amt))}</b></td></tr>
<tr><td><b>Paid</b></td><td>{money(abs(paid))}{' on '+fmt_date(dp) if dp else ''}</td></tr></table>"""
                rg = ""
                if inv_lines.get(tid):
                    rg = "<h2>Invoice lines</h2><table><tr><th>Item</th><th>Description</th><th class=r>Qty</th><th class=r>Price</th><th class=r>Discount</th><th class=r>Amount</th></tr>"
                    for pnum, desc, qty, sell, disc, unit in inv_lines[tid]:
                        bedrag = qty * sell * (1 - disc)
                        rg += f"<tr><td>{esc(pnum)}</td><td>{esc(desc)}</td><td class=r>{qty:g} {esc(unit)}</td><td class=r>{money(sell)}</td><td class=r>{f'{disc*100:.0f}%' if disc else ''}</td><td class=r>{money(bedrag)}</td></tr>"
                    rg += "</table>"
                bk = q("""SELECT t.transdate, c.accno, c.description, t.amount
                          FROM acc_trans t JOIN chart c ON c.id=t.chart_id WHERE CAST(t.trans_id AS INTEGER)=? ORDER BY CAST(t.amount AS REAL) DESC""", tid)
                jr = "<h2>Ledger entries</h2><table><tr><th>Date</th><th>Account</th><th class=r>Debit</th><th class=r>Credit</th></tr>"
                for btd, accno, descr, b in bk:
                    b = f(b)
                    jr += f"<tr><td>{fmt_date(btd)}</td><td>{accno} {esc(descr)}</td><td class=r>{money(-b) if b<0 else ''}</td><td class=r>{money(b) if b>0 else ''}</td></tr>"
                jr += "</table>"
                write_file(f"{jd}/invoices/f{tid}.html", page_html(COMPANY, f"{kind} invoice {esc(inv) or tid}", header_html + rg + jr, "../../"))

    # ---------- open items ----------
    body = ""
    for title, info in (("receivables", ar_info), ("payables", ap_info)):
        rows = [(tid, *v) for tid, v in info.items() if abs(abs(v[4]) - abs(v[6])) >= 0.005]
        rows.sort(key=lambda x: (x[3] or ""))
        body += f"<h2>Open {title}</h2>"
        if not rows:
            body += "<p class=sub>No open items.</p>"; continue
        body += "<table><tr><th>Invoice</th><th>Party</th><th>Date</th><th>Due date</th><th class=r>Amount</th><th class=r>Paid</th><th class=r>Open</th></tr>"
        for tid, inv, name, td, dd, amt, net, paid, dp, curr, ordn in rows:
            jr = (td or "")[:4] or str(years[-1])
            body += (f"<tr><td><a href='{jr}/invoices/f{tid}.html'>{esc(inv) or tid}</a></td><td>{esc(name)}</td><td>{fmt_date(td)}</td>"
                     f"<td>{fmt_date(dd)}</td><td class=r>{money(abs(amt))}</td><td class=r>{money(abs(paid))}</td><td class=r>{money(abs(amt)-abs(paid))}</td></tr>")
        body += "</table>"
    write_file(f"{base}/open-items.html", page_html(COMPANY, "Open items", "<h1>Open items</h1>"+body))

    # ---------- index ----------
    n = q("SELECT count(DISTINCT trans_id) FROM acc_trans")[0][0]
    closing_note = " · year-end closings found: P&L in two views" if year_end_ids else ""
    body = (f"<h1>{esc(COMPANY)}</h1><p class=sub>{('Reg. no. '+REG_NO+' — ') if REG_NO else ''}{len(years)} financial years "
            f"({years[0]}–{years[-1]}), {n} entries{closing_note} — <a href='open-items.html'>open items</a></p><div class=cards>")
    for year, *_ in year_data:
        pnl_links = (f"<a href='{year}/profit-loss.html'>P&amp;L operational</a><a href='{year}/profit-loss-as-posted.html'>P&amp;L as posted</a>"
                   if year_end_ids else f"<a href='{year}/profit-loss.html'>Profit &amp; loss</a>")
        body += (f"<div class=card><b>{year}</b><a href='{year}/balance.html'>Balance sheet</a>{pnl_links}"
                 f"<a href='{year}/trial-balance.html'>Trial balance</a><a href='{year}/ledger.html'>Ledger</a>"
                 f"<a href='{year}/journal.html'>Journal</a><a href='{year}/sales-invoices.html'>Sales invoices</a>"
                 f"<a href='{year}/purchase-invoices.html'>Purchase invoices</a></div>")
    body += "</div>"
    idx = page_html(COMPANY, "Overview", body).replace('SQL-Ledger archive —', "<a href='../index.html'>SQL-Ledger archive</a> —", 1)
    write_file(f"{base}/index.html", idx)
    print(f"  ok {slug}: {COMPANY} — {len(years)} years, {n} entries" + (" (with year-end closings)" if year_end_ids else ""))
    return (slug, COMPANY, years, n, year_data)

# ----------------------------------------------------------------------
#  Dashboard
# ----------------------------------------------------------------------
def dashboard(admins):
    W, H, PAD_L, PAD_T, PAD_B = 880, 300, 64, 26, 34
    DCSS = """
:root { color-scheme: light dark; --page:#f6f7f9; --surface:#ffffff; --ink:#1f2933; --ink2:#52606d;
 --muted:#6b7684; --grid:#e4e8ed; --baseline:#c3cad3; --ring:#d9dee5; --accent:#1f4e79; --topbar:#1f4e79;
 --pos:#2a78d6; --neg:#e34948; --omzet:#2a78d6; --kosten:#eb6834; --line:#1baf7a; --marge:#2a78d6; }
@media (prefers-color-scheme: dark) { :root { --page:#15191e; --surface:#1d2228; --ink:#e4e8ed; --ink2:#b8c1cc;
 --muted:#95a0ad; --grid:#2a3139; --baseline:#3d4652; --ring:#333b45; --accent:#7fb0e0; --topbar:#12304d;
 --pos:#3987e5; --neg:#e66767; --omzet:#3987e5; --kosten:#d95926; --line:#199e70; --marge:#3987e5; } }
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin:0; padding:0 24px 24px; background:var(--page); color:var(--ink); }
.top { background:var(--topbar); color:#fff; padding:.6rem 1.2rem; margin:0 -24px 18px; font-size:15px; font-weight:700; }
.top a { color:#fff; text-decoration:none; }
.viz-root { background:var(--surface); border:1px solid var(--ring); border-radius:4px; padding:18px 22px; max-width:960px; margin:0 auto 22px; }
h1 { font-size:21px; max-width:960px; margin:0 auto 4px; } h2 { font-size:15px; margin:0 0 2px; }
h3.block { font-size:16px; max-width:960px; margin:26px auto 10px; }
p.intro,.sub { color:var(--ink2); font-size:12px; margin:0 0 10px; } p.intro { max-width:960px; margin:0 auto 14px; }
.as { font-size:11px; fill:var(--muted); } .dl { font-size:11px; font-weight:600; fill:var(--ink); }
.leg { display:flex; gap:14px; font-size:12px; color:var(--ink2); margin-bottom:4px; }
.leg i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; }
.leg i.line { height:3px; width:16px; vertical-align:middle; }
svg { width:100%; height:auto; display:block; } .hit:hover { fill:rgba(128,128,128,.08); }
#tip { position:fixed; pointer-events:none; background:var(--surface); color:var(--ink); border:1px solid var(--ring);
 border-radius:6px; padding:7px 10px; font-size:12px; box-shadow:0 2px 8px rgba(0,0,0,.18); display:none; z-index:9; }
#tip b { display:block; } #tip span { color:var(--ink2); }
details { margin-top:8px; font-size:12px; } summary { cursor:pointer; color:var(--ink2); }
table { border-collapse:collapse; margin-top:8px; font-variant-numeric:tabular-nums; }
th,td { padding:3px 12px 3px 0; text-align:left; border-bottom:1px solid var(--grid); font-size:12px; }
th.r,td.r { text-align:right; } tr.totals td { font-weight:600; border-top:2px solid var(--baseline); }
.footnote { max-width:960px; margin:4px auto 20px; color:var(--muted); font-size:11px; }
a.back { display:block; max-width:960px; margin:0 auto 10px; font-size:12px; color:var(--accent); text-decoration:none; }
"""
    def nice_step(rng, stappen):
        for s in stappen:
            if rng / s <= 6.5: return s
        return stappen[-1] * 4

    def smooth(pts):
        if len(pts) < 2: return ""
        if len(pts) == 2: return f"M{pts[0][0]:.1f},{pts[0][1]:.1f} L{pts[1][0]:.1f},{pts[1][1]:.1f}"
        d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
        for i in range(len(pts) - 1):
            p0 = pts[i-1] if i > 0 else pts[i]; p1, p2 = pts[i], pts[i+1]
            p3 = pts[i+2] if i+2 < len(pts) else p2
            c1 = (p1[0]+(p2[0]-p0[0])/6, p1[1]+(p2[1]-p0[1])/6)
            c2 = (p2[0]-(p3[0]-p1[0])/6, p2[1]-(p3[1]-p1[1])/6)
            d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
        return d

    def grid_lines(svg, Y, ymin, ymax, step, fmt):
        t = ymin
        while t <= ymax + 1e-9:
            y = Y(t)
            k = "var(--baseline)" if abs(t) < 1e-9 else "var(--grid)"
            svg.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W-8}" y2="{y:.1f}" stroke="{k}" stroke-width="{1.5 if abs(t)<1e-9 else 1}"/>')
            svg.append(f'<text x="{PAD_L-8}" y="{y+4:.1f}" text-anchor="end" class="as">{fmt(t)}</text>')
            t += step

    def axis_fmt(x):
        if x == 0: return "0"
        v = float(x)
        return f"{v/1000000:g}M" if abs(v) >= 1000000 else f"{v/1000:g}k"

    def bar_path(x, bw, y0, y1):
        top, bot = min(y0, y1), max(y0, y1)
        hgt = max(bot - top, 1.5); rad = min(4, hgt)
        if y1 <= y0:
            return (f"M{x:.1f},{bot:.1f} V{top+rad:.1f} Q{x:.1f},{top:.1f} {x+rad:.1f},{top:.1f} "
                    f"H{x+bw-rad:.1f} Q{x+bw:.1f},{top:.1f} {x+bw:.1f},{top+rad:.1f} V{bot:.1f} Z")
        return (f"M{x:.1f},{top:.1f} V{bot-rad:.1f} Q{x:.1f},{bot:.1f} {x+rad:.1f},{bot:.1f} "
                f"H{x+bw-rad:.1f} Q{x+bw:.1f},{bot:.1f} {x+bw:.1f},{bot-rad:.1f} V{top:.1f} Z")

    def data_table(rows):
        t = ["<details><summary>Table</summary><table><tr><th>Year</th><th class=r>Revenue</th><th class=r>Costs</th><th class=r>Result</th></tr>"]
        for year, o, k, r in rows:
            t.append(f"<tr><td>{year}</td><td class=r>{money0(o)}</td><td class=r>{money0(k)}</td><td class=r>{money0(r)}</td></tr>")
        t.append("</table></details>")
        return "".join(t)

    def panel_result(name, rows):
        res = [r[3] for r in rows]
        lo, hi = min(min(res), 0.0), max(max(res), 0.0)
        step = nice_step(hi - lo or 1, (5000, 10000, 20000, 25000, 50000, 100000, 200000, 500000))
        ymin, ymax = math.floor(lo/step)*step, math.ceil(hi/step)*step or step
        if ymin == 0 and lo < 0: ymin = -step
        plot_h = H - PAD_T - PAD_B
        Y = lambda v: PAD_T + (ymax - v) / (ymax - ymin) * plot_h
        n = len(rows); slot = (W - PAD_L - 12) / n; bw = min(30, max(6, slot - 8))
        beste = max(range(n), key=lambda i: res[i]); slechtste = min(range(n), key=lambda i: res[i])
        svg = [f'<svg viewBox="0 0 {W} {H}" role="img">']; grid_lines(svg, Y, ymin, ymax, step, axis_fmt)
        for i, (year, o, k, r) in enumerate(rows):
            x = PAD_L + 12 + i*slot + (slot-bw)/2
            svg.append(f'<path d="{bar_path(x, bw, Y(0), Y(r))}" fill="var({"--pos" if r>=0 else "--neg"})"/>')
            if i in (beste, slechtste):
                ly = min(Y(0), Y(r)) - 6 if r >= 0 else max(Y(0), Y(r)) + 14
                svg.append(f'<text x="{x+bw/2:.1f}" y="{ly:.1f}" text-anchor="middle" class="dl">{money0(r)}</text>')
            svg.append(f'<text x="{x+bw/2:.1f}" y="{H-10}" text-anchor="middle" class="as">{year}</text>')
            svg.append(f'<rect class="hit" x="{PAD_L+12+i*slot:.1f}" y="{PAD_T}" width="{slot:.1f}" height="{plot_h}" fill="transparent" data-year="{year}" data-descr="Result {money0(r)} · Revenue {money0(o)} · Costs {money0(k)}"/>')
        svg.append("</svg>")
        total = sum(r[3] for r in rows)
        header_html = f"<h2>{esc(name)}</h2><p class=sub>Result per financial year · total over the period: <b>{money0(total)}</b></p>"
        leg = '<div class=leg><span><i style="background:var(--pos)"></i>profit</span><span><i style="background:var(--neg)"></i>loss</span></div>'
        return f'<section class="viz-root">{header_html}{leg}{"".join(svg)}{data_table(rows)}</section>'

    def panel_revenue_costs(name, rows):
        vals = [v for r in rows for v in (r[1], r[2], r[3])]
        lo, hi = min(min(vals), 0.0), max(max(vals), 0.0)
        step = nice_step(hi - lo or 1, (10000, 20000, 25000, 50000, 100000, 200000, 250000, 500000, 1000000))
        ymin, ymax = math.floor(lo/step)*step, math.ceil(hi/step)*step or step
        plot_h = H - PAD_T - PAD_B
        Y = lambda v: PAD_T + (ymax - v) / (ymax - ymin) * plot_h
        n = len(rows); slot = (W - PAD_L - 14) / n; bw = min(16, max(4, (slot-10)/2))
        svg = [f'<svg viewBox="0 0 {W} {H}" role="img">']; grid_lines(svg, Y, ymin, ymax, step, axis_fmt)
        line_pts = []
        for i, (year, o, k, r) in enumerate(rows):
            cx = PAD_L + 14 + i*slot + slot/2
            svg.append(f'<path d="{bar_path(cx-bw-1, bw, Y(0), Y(o))}" fill="var(--omzet)"/>')
            svg.append(f'<path d="{bar_path(cx+1, bw, Y(0), Y(k))}" fill="var(--kosten)"/>')
            line_pts.append((cx, Y(r)))
            svg.append(f'<text x="{cx:.1f}" y="{H-10}" text-anchor="middle" class="as">{year}</text>')
        svg.append(f'<path d="{smooth(line_pts)}" fill="none" stroke="var(--line)" stroke-width="2.5" stroke-linecap="round"/>')
        for cx, cy in line_pts:
            svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="var(--line)" stroke="var(--surface)" stroke-width="2"/>')
        for i, (year, o, k, r) in enumerate(rows):
            svg.append(f'<rect class="hit" x="{PAD_L+14+i*slot:.1f}" y="{PAD_T}" width="{slot:.1f}" height="{plot_h}" fill="transparent" data-year="{year}" data-descr="Revenue {money0(o)} · Costs {money0(k)} · Result {money0(r)}"/>')
        svg.append("</svg>")
        header_html = f"<h2>{esc(name)}</h2><p class=sub>Revenue and costs per financial year, result as a line</p>"
        leg = ('<div class=leg><span><i style="background:var(--omzet)"></i>revenue</span>'
               '<span><i style="background:var(--kosten)"></i>costs</span>'
               '<span><i class=line style="background:var(--line)"></i>result (line)</span></div>')
        return f'<section class="viz-root">{header_html}{leg}{"".join(svg)}{data_table(rows)}</section>'

    def panel_margin(name, rows):
        margins = [(year, (r/o*100 if o > 0 else None), o, r) for year, o, k, r in rows]
        valid = [m for _, m, _, _ in margins if m is not None]
        if not valid: return ""
        lo, hi = min(min(valid), 0.0), max(max(valid), 0.0)
        if lo < -100: lo = -100
        step = nice_step(hi - lo or 1, (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50))
        ymin = max(math.floor(lo/step)*step, -100); ymax = math.ceil(hi/step)*step or step
        plot_h = H - PAD_T - PAD_B
        Y = lambda v: PAD_T + (ymax - v) / (ymax - ymin) * plot_h
        n = len(rows); slot = (W - PAD_L - 14) / n
        svg = [f'<svg viewBox="0 0 {W} {H}" role="img">']
        grid_lines(svg, Y, ymin, ymax, step, lambda t: f"{t:g}".replace(".", ",") + "%")
        seg, segs = [], []
        for i, (year, m, o, r) in enumerate(margins):
            cx = PAD_L + 14 + i*slot + slot/2
            svg.append(f'<text x="{cx:.1f}" y="{H-10}" text-anchor="middle" class="as">{year}</text>')
            if m is None:
                if seg: segs.append(seg); seg = []
                svg.append(f'<text x="{cx:.1f}" y="{Y(0)-8:.1f}" text-anchor="middle" class="as">×</text>'); continue
            if m < ymin:
                svg.append(f'<circle cx="{cx:.1f}" cy="{Y(ymin):.1f}" r="4.5" fill="var(--surface)" stroke="var(--marge)" stroke-width="2"/>')
                svg.append(f'<text x="{cx:.1f}" y="{Y(ymin)-8:.1f}" text-anchor="middle" class="dl">{pctf(m)} ↓</text>')
                if seg: segs.append(seg); seg = []
                continue
            seg.append((cx, Y(m), year, m))
        if seg: segs.append(seg)
        all_pts = [p for s in segs for p in s]
        for s in segs:
            svg.append(f'<path d="{smooth([(x, y) for x, y, *_ in s])}" fill="none" stroke="var(--marge)" stroke-width="2.5" stroke-linecap="round"/>')
            for cx, cy, year, m in s:
                svg.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="var(--marge)" stroke="var(--surface)" stroke-width="2"/>')
        if all_pts:
            for cx, cy, year, m in (max(all_pts, key=lambda p: p[3]), min(all_pts, key=lambda p: p[3])):
                svg.append(f'<text x="{cx:.1f}" y="{cy + (-10 if m>=0 else 16):.1f}" text-anchor="middle" class="dl">{pctf(m)}</text>')
        for i, (year, m, o, r) in enumerate(margins):
            info = f"Margin {pctf(m)} · Revenue {money0(o)} · Result {money0(r)}" if m is not None else "No (positive) revenue — margin not meaningful"
            svg.append(f'<rect class="hit" x="{PAD_L+14+i*slot:.1f}" y="{PAD_T}" width="{slot:.1f}" height="{plot_h}" fill="transparent" data-year="{year}" data-descr="{info}"/>')
        svg.append("</svg>")
        header_html = f"<h2>{esc(name)}</h2><p class=sub>Result as a percentage of revenue · × = no (positive) revenue · ↓ = off the scale</p>"
        return f'<section class="viz-root">{header_html}{"".join(svg)}</section>'

    blok1 = "".join(panel_result(name, jd) for _, name, _, _, jd in admins)
    blok2 = "".join(panel_revenue_costs(name, jd) for _, name, _, _, jd in admins)
    blok3 = "".join(panel_margin(name, jd) for _, name, _, _, jd in admins)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard — SQL-Ledger archive</title><style>{DCSS}</style></head><body>
<div class="top"><a href="index.html">SQL-Ledger archive</a> — Dashboard</div>
<a class="back" href="index.html">&lsaquo; back to the archive</a>
<h1>Financial dashboard</h1>
<p class="intro">Results per financial year, excluding year-end closing entries · generated on {TODAY} · hover over a year for exact figures.</p>
<h3 class="block">1 · Result per year</h3>{blok1}
<h3 class="block">2 · Revenue and costs, result as a line</h3>{blok2}
<h3 class="block">3 · Margin (result as % of revenue)</h3>{blok3}
<p class="footnote">Incomplete or not-yet-closed financial years may distort the picture. Blocks 1 and 2 have an expandable table with exact amounts below each chart.</p>
<div id="tip"></div>
<script>
const tip = document.getElementById('tip');
document.querySelectorAll('.hit').forEach(h => {{
  h.addEventListener('mousemove', e => {{
    tip.innerHTML = '<b>' + h.dataset.year + '</b><span>' + h.dataset.descr + '</span>';
    tip.style.display = 'block';
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + 'px'; tip.style.top = (e.clientY + 14) + 'px';
  }});
  h.addEventListener('mouseleave', () => tip.style.display = 'none');
}});
</script></body></html>"""

# ----------------------------------------------------------------------
#  Main program
# ----------------------------------------------------------------------
def dataset_name(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(8):
                r = fh.readline()
                m = re.match(r"--\s*Dataset:\s*(\S+)", r)
                if m: return re.sub(r"[^a-z0-9_]", "", m.group(1).lower())
    except Exception:
        pass
    basis = os.path.splitext(os.path.basename(path))[0].lower()
    return re.sub(r"[^a-z0-9_]", "", basis) or "company"

def main():
    files = sorted(set(glob.glob(os.path.join(HERE, "*.sql")) + glob.glob(os.path.join(HERE, "backup", "*.sql"))))
    if not files:
        print("No .sql files found. Place your SQL-Ledger backups next to this script (or in the 'backup' folder).")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    write_file(os.path.join(OUT_DIR, "archive.css"), CSS)
    admins, used_names = [], set()
    for path in files:
        name = dataset_name(path)
        while name in used_names: name += "x"
        used_names.add(name)
        print(f"Reading: {os.path.basename(path)} (dataset '{name}')")
        fmt, fd, fm = detect_date_format(path)
        if fd and fm and min(fd, fm) >= max(5, (fd + fm) // 1000):
            print(f"  !! Warning: date format is ambiguous (dd-mm violations: {fd}, mm-dd violations: {fm}); chosen: {fmt}. Verify dates in the archive!")
        elif fmt == "MDY":
            print("  Date format detected: mm-dd (US style)")
        con = load_backup(path, fmt)
        a = build_company(con, name)
        con.close()
        if a: admins.append(a)
    if not admins:
        print("No usable companies found."); return 1

    write_file(os.path.join(OUT_DIR, "dashboard.html"), dashboard(admins))
    body = "<h1>Choose a company</h1><div class=cards>"
    for slug, name, years, n, _ in admins:
        body += f"<div class='card large'><b>{esc(name)}</b><a href='{slug}/index.html'>Open &rsaquo;</a><small>{years[0]}–{years[-1]}, {n} entries</small></div>"
    body += "<div class='card large'><b>Dashboard</b><a href='dashboard.html'>Charts per year &rsaquo;</a><small>result, revenue/costs, margin</small></div></div>"
    write_file(os.path.join(OUT_DIR, "index.html"), f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SQL-Ledger archive</title><link rel="stylesheet" href="archive.css"></head><body>
<div class="top">SQL-Ledger archive</div>{body}
<p class="sub" style="margin-top:30px">Static archive, generated on {TODAY} from SQL-Ledger backups. Read-only.</p></body></html>""")
    print(f"\nDone! Open: {os.path.join(OUT_DIR, 'index.html')}")
    return 0

if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        print(f"\nERROR: {e}")
        code = 1
    if os.name == "nt" and sys.stdout.isatty():
        input("\nPress Enter to close...")
    sys.exit(code)
