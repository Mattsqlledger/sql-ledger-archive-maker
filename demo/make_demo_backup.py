#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generates demo/demo-company.sql: a fictitious SQL-Ledger backup
("Example Trading Ltd", 2023-2025) used for the README screenshots and
for trying the Archive Maker without real data. All names and figures
are made up.

Usage:  python3 demo/make_demo_backup.py
"""
import os, random, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "demo-company.sql")
rnd = random.Random(42)

CHART = [  # id, accno, description, category
    (1, "1100", "Bank", "A"), (2, "1200", "Accounts receivable", "A"),
    (3, "1500", "Equipment", "A"), (4, "1520", "Input VAT", "A"),
    (5, "2100", "Accounts payable", "L"), (6, "2200", "VAT payable", "L"),
    (7, "3000", "Share capital", "Q"), (8, "3100", "Retained earnings", "Q"),
    (9, "4000", "Sales - consulting", "I"), (10, "4100", "Sales - training", "I"),
    (11, "6000", "Rent", "E"), (12, "6100", "Office supplies", "E"),
    (13, "6200", "Software subscriptions", "E"), (14, "6300", "Travel", "E"),
    (15, "6400", "Bank charges", "E"), (16, "7000", "Wages", "E"),
]
ACC = {accno: cid for cid, accno, _, _ in CHART}
CUSTOMERS = ["Riverbank Traders", "Blue Harbour Logistics", "Meadow Lane Retail",
             "Greenfield Farms", "Atlas Engineering", "Riverside Clinic"]
VENDORS = [("Paper & Pen Supplies", "6100"), ("Example Cloud Services", "6200"),
           ("City Rail", "6300"), ("Demo Property Management", "6000")]
PARTS = [(1, "CONS-HR", "Consulting hour", "hr", 95.0, "4000"),
         (2, "TRN-DAY", "Training day", "day", 1150.0, "4100"),
         (3, "WS-HALF", "Half-day workshop", "ea", 650.0, "4100")]
VAT = 0.21

rows = []  # (table, {col: value})
def add(table, **kw): rows.append((table, kw))
def d(dt): return dt.strftime("%d-%m-%Y")
def r2(x): return round(x + 1e-9, 2)

tid = [1000]
def next_id():
    tid[0] += 1; return tid[0]
def post(trans_id, dt, accno, amount, source="", memo=""):
    add("acc_trans", trans_id=trans_id, chart_id=ACC[accno], amount=r2(amount),
        transdate=d(dt), source=source, memo=memo)

def gl(dt, ref, descr, lines):
    t = next_id()
    add("gl", id=t, reference=ref, description=descr, transdate=d(dt))
    for accno, amt in lines: post(t, dt, accno, amt)
    return t

add("defaults", fldname="company", fldvalue="Example Trading Ltd")
add("defaults", fldname="businessnumber", fldvalue="DEMO-12345")
for cid, accno, descr, cat in CHART:
    add("chart", id=cid, accno=accno, description=descr, category=cat)
for i, n in enumerate(CUSTOMERS, 1): add("customer", id=i, name=n)
for i, (n, _) in enumerate(VENDORS, 1): add("vendor", id=i, name=n)
for pid, pn, descr, unit, price, _ in PARTS:
    add("parts", id=pid, partnumber=pn, description=descr, unit=unit)

gl(datetime.date(2023, 1, 2), "OPEN-1", "Share capital paid in", [("1100", -25000), ("3000", 25000)])
gl(datetime.date(2023, 1, 15), "EQ-1", "Laptops and furniture", [("1500", -6400), ("1100", 6400)])

CUTOFF = datetime.date(2025, 12, 31)
inv_no = 0
for year, growth in ((2023, 1.0), (2024, 1.35), (2025, 1.7)):
    for month in range(1, 13):
        # sales invoices
        for _ in range(rnd.randint(2, 4)):
            inv_no += 1
            dt = datetime.date(year, month, rnd.randint(1, 26))
            cust = rnd.randint(1, len(CUSTOMERS))
            lines = []
            for pid, pn, descr, unit, price, inc in rnd.sample(PARTS, rnd.randint(1, 2)):
                qty = rnd.randint(4, 30) if unit == "hr" else rnd.randint(1, 2)
                qty = max(1, round(qty * growth)) if unit == "hr" else qty
                lines.append((pid, descr, qty, price, inc))
            net = {}
            for pid, descr, qty, price, inc in lines:
                net[inc] = net.get(inc, 0) + qty * price
            netamount = r2(sum(net.values())); tax = r2(netamount * VAT); gross = r2(netamount + tax)
            t = next_id(); due = dt + datetime.timedelta(days=30)
            paid_dt = dt + datetime.timedelta(days=rnd.randint(10, 50))
            is_paid = paid_dt <= CUTOFF - datetime.timedelta(days=20)
            add("ar", id=t, invnumber=f"INV-{inv_no:04d}", customer_id=cust, transdate=d(dt),
                duedate=d(due), amount=gross, netamount=netamount, paid=gross if is_paid else 0,
                datepaid=d(paid_dt) if is_paid else None, curr="EUR", ordnumber="")
            for pid, descr, qty, price, inc in lines:
                add("invoice", trans_id=t, parts_id=pid, description=descr, qty=qty, sellprice=price, discount=0)
            post(t, dt, "1200", -gross)
            for inc, amt in net.items(): post(t, dt, inc, amt)
            post(t, dt, "2200", tax)
            if is_paid:
                post(t, paid_dt, "1200", gross, source=f"BANK-{paid_dt:%Y%m%d}")
                post(t, paid_dt, "1100", -gross, source=f"BANK-{paid_dt:%Y%m%d}")
        # purchase invoices
        for vid, (vname, exp) in enumerate(VENDORS, 1):
            if exp == "6000": netamount = 1450.0
            elif exp == "6200": netamount = r2(180 * growth + rnd.uniform(0, 40))
            elif rnd.random() < 0.5: continue
            else: netamount = r2(rnd.uniform(60, 420) * growth)
            dt = datetime.date(year, month, rnd.randint(1, 10))
            tax = r2(netamount * VAT); gross = r2(netamount + tax)
            t = next_id(); paid_dt = dt + datetime.timedelta(days=rnd.randint(5, 25))
            is_paid = paid_dt <= CUTOFF - datetime.timedelta(days=10)
            add("ap", id=t, invnumber=f"P{year}-{month:02d}-{vid}", vendor_id=vid, transdate=d(dt),
                duedate=d(dt + datetime.timedelta(days=14)), amount=gross, netamount=netamount,
                paid=gross if is_paid else 0, datepaid=d(paid_dt) if is_paid else None,
                curr="EUR", ordnumber="")
            post(t, dt, "2100", gross); post(t, dt, exp, -netamount); post(t, dt, "1520", -tax)
            if is_paid:
                post(t, paid_dt, "2100", -gross, source=f"BANK-{paid_dt:%Y%m%d}")
                post(t, paid_dt, "1100", gross, source=f"BANK-{paid_dt:%Y%m%d}")
        last = datetime.date(year, month, 28)
        gl(last, f"PAY-{year}{month:02d}", "Salaries", [("7000", -r2(3800 * growth)), ("1100", r2(3800 * growth))])
        gl(last, f"BC-{year}{month:02d}", "Bank charges", [("6400", -12.5), ("1100", 12.5)])
        if month % 3 == 0:  # quarterly VAT settlement with the tax office
            bal = {}
            for table, kw in rows:
                if table == "acc_trans" and kw["chart_id"] in (ACC["2200"], ACC["1520"]):
                    bal[kw["chart_id"]] = bal.get(kw["chart_id"], 0) + kw["amount"]
            out, inp = r2(bal.get(ACC["2200"], 0)), r2(bal.get(ACC["1520"], 0))
            gl(last, f"VAT-{year}Q{month // 3}", f"VAT return Q{month // 3} {year}",
               [("2200", -out), ("1520", -inp), ("1100", r2(out + inp))])

def balances_until(year):
    tot = {}
    for table, kw in rows:
        if table == "acc_trans" and kw["transdate"].endswith(str(year)):
            tot[kw["chart_id"]] = tot.get(kw["chart_id"], 0) + kw["amount"]
    return tot

# year-end closing for 2023 and 2024 (2025 left open)
for year in (2023, 2024):
    tot = balances_until(year)
    lines, result = [], 0.0
    for cid, accno, _, cat in CHART:
        if cat in "IE" and abs(tot.get(cid, 0)) > 0.004:
            lines.append((accno, -tot[cid])); result += tot[cid]
    lines.append(("3100", r2(result)))
    t = gl(datetime.date(year, 12, 31), f"YE-{year}", f"Year-end closing {year}", lines)
    add("yearend", trans_id=t, transdate=d(datetime.date(year, 12, 31)))

def lit(v):
    if v is None: return "NULL"
    if isinstance(v, (int, float)): return repr(v)
    return "'" + str(v).replace("'", "''") + "'"

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("-- SQL-Ledger demo backup (fictitious data)\n-- Dataset: demo\n--\n\n")
    for table, kw in rows:
        fh.write(f"INSERT INTO {table} ({', '.join(kw)}) VALUES ({', '.join(lit(v) for v in kw.values())});\n")
print(f"Written: {OUT} ({len(rows)} rows)")
