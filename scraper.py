# Created by 青云 on 2026/10/21.
"""Holiday Planner 数据抓取：内地放假 / 调休安排、香港公众假期。只用标准库。

内地数据来自 holiday-cn（解析国务院通知，通知发布后通常当天更新）；官方安排未公布的年份，
按《全国年节及纪念日放假办法》（2025 年起施行）推一份「预估版」，只含法定假日 + 落在周末的补休，不含调休。
"""
import json, urllib.request
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data.json"
CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
CN_MIRRORS = [
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{y}.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{y}.json",
]
HK_URL = "https://www.1823.gov.hk/common/ical/sc.json"

# 农历节日对应的公历日期（预估版用）：春节取除夕
LUNAR = {
    2027: {"除夕": "2027-02-05", "清明节": "2027-04-05", "端午节": "2027-06-09", "中秋节": "2027-09-15"},
    2028: {"除夕": "2028-01-25", "清明节": "2028-04-04", "端午节": "2028-05-28", "中秋节": "2028-10-03"},
}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "ignore")


def now_cst():
    return datetime.now(CST)


# ── 内地：holiday-cn ───────────────────────────────────
def cn_year(y):
    last_err = None
    for tpl in CN_MIRRORS:
        try:
            d = json.loads(fetch(tpl.format(y=y)))
            days = [{"date": x["date"], "name": x["name"], "off": bool(x["isOffDay"])} for x in d.get("days", [])]
            return {"papers": d.get("papers", []), "days": days}
        except Exception as e:
            last_err = e
    raise last_err


def cn_estimate(y):
    """法定假日 + 落在周末时顺延到下一个工作日补休。不含调休上班日（官方会另行安排）。"""
    lunar = LUNAR.get(y)
    if not lunar:
        return None
    eve = date.fromisoformat(lunar["除夕"])
    legal = [("元旦", date(y, 1, 1))]
    legal += [("春节", eve + timedelta(days=i)) for i in range(4)]
    legal += [("清明节", date.fromisoformat(lunar["清明节"]))]
    legal += [("劳动节", date(y, 5, 1)), ("劳动节", date(y, 5, 2))]
    legal += [("端午节", date.fromisoformat(lunar["端午节"]))]
    legal += [("中秋节", date.fromisoformat(lunar["中秋节"]))]
    legal += [("国庆节", date(y, 10, d)) for d in (1, 2, 3)]
    taken = {d for _, d in legal}
    days = []
    for name, d in legal:
        days.append({"date": d.isoformat(), "name": name, "off": True})
        if d.weekday() >= 5:                        # 周末撞上法定假日：往后找一个工作日补休
            x = d + timedelta(days=1)
            while x.weekday() >= 5 or x in taken:
                x += timedelta(days=1)
            taken.add(x)
            days.append({"date": x.isoformat(), "name": name, "off": True, "comp": True})
    days.sort(key=lambda r: r["date"])
    return {"papers": [], "days": days}


# ── 香港：1823 公众假期 ────────────────────────────────
def hk_all():
    d = json.loads(fetch(HK_URL))
    out = {}
    for ev in d["vcalendar"][0]["vevent"]:
        s = ev["dtstart"][0]
        iso = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        out.setdefault(s[:4], []).append({"date": iso, "name": ev["summary"]})
    if not out:
        raise ValueError("empty")
    return {y: sorted(v, key=lambda r: r["date"]) for y, v in out.items()}


def summarize(cn, today, max_leave=3):
    """给 pages-dashboard 用的摘要：下一个内地假期 + 计划年份请 ≤ max_leave 天最划算的方案。
    规则与前端「全年最划算」一致：从休息日起、到休息日止，只算中间桥接的请假。"""
    info = {r["date"]: {**r, "est": not v["official"]} for v in cn.values() for r in v["days"]}

    def is_off(d):
        r = info.get(d.isoformat())
        return r["off"] if r else d.weekday() >= 5

    hol = sorted(k for k, r in info.items() if r["off"] and k >= today.isoformat())
    nxt = None
    if hol:
        d0 = date.fromisoformat(hol[0])
        work = sum(1 for i in range((d0 - today).days) if not is_off(today + timedelta(days=i)))
        nxt = {"date": hol[0], "name": info[hol[0]]["name"], "days": (d0 - today).days, "workdays": work, "est": info[hol[0]]["est"]}

    # 计划年份：当年还有假期就看当年，否则看明年
    y = today.year if any(k.startswith(str(today.year)) for k in hol) else today.year + 1
    start, end = max(date(y, 1, 1) - timedelta(days=20), today), date(y, 12, 31) + timedelta(days=20)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    off = [is_off(d) for d in days]
    plans = []
    for s in range(len(days)):
        if not off[s] or (s > 0 and off[s - 1]):
            continue
        e, used = s, 0
        while e + 1 < len(days) and used + (not off[e + 1]) <= max_leave:
            e += 1
            used += not off[e]
        while e > s and not off[e]:
            e -= 1
        span = days[s:e + 1]
        names = sorted({info[d.isoformat()]["name"] for d in span if d.isoformat() in info and info[d.isoformat()]["off"]},
                       key=lambda n: min(k for k, r in info.items() if r["name"] == n))
        if len(span) < 3 or not names or (days[s].year != y and days[e].year != y):
            continue
        leave = [d.isoformat() for d in span if not is_off(d)]
        plans.append({"from": days[s].isoformat(), "to": days[e].isoformat(), "len": len(span),
                      "used": len(leave), "leave": leave, "names": names})
    plans.sort(key=lambda p: (-p["len"], p["used"], p["from"]))
    picked = []
    for p in plans:
        if all(p["to"] < q["from"] or p["from"] > q["to"] for q in picked):
            picked.append(p)
        if len(picked) >= 5:
            break
    return {"next": nxt, "plan_year": y, "plan_official": cn.get(str(y), {}).get("official", False),
            "max_leave": max_leave, "top": picked}


def main():
    now = now_cst()
    stamp = now.isoformat(timespec="seconds")
    old = json.loads(DATA_FILE.read_text("utf-8")) if DATA_FILE.exists() else {}
    sources = []

    def run(name, fn, count=len):
        try:
            v = fn()
            sources.append({"name": name, "ok": True, "count": count(v), "updated": stamp})
            return v
        except Exception as e:
            prev = next((s for s in old.get("sources", []) if s["name"] == name), {})
            sources.append({"name": name, "ok": False, "error": str(e)[:120], "updated": prev.get("updated")})
            print("FAIL", name, e)
            return None

    # 内地：去年 ~ 明年
    cn, announce = {}, dict(old.get("announce", {}))
    for y in range(now.year - 1, now.year + 2):
        key = str(y)
        got = run(f"holiday-cn {y}", lambda y=y: cn_year(y), count=lambda v: len(v["days"]))
        if got is None:
            got = old.get("cn", {}).get(key)
            if got and got.get("official"):
                cn[key] = {**got, "stale": True}
                continue
            got = {"papers": [], "days": []}
        if got["days"]:
            cn[key] = {**got, "official": True, "stale": False}
            if key not in announce and y > now.year - 1:
                # 第一次抓到官方安排的时间，用于前端「刚公布」高亮
                was_estimate = not old.get("cn", {}).get(key, {}).get("official", True)
                announce[key] = {"first_seen": stamp, "fresh": was_estimate}
        else:
            est = cn_estimate(y)
            if est:
                cn[key] = {**est, "official": False, "stale": False}

    hk = run("香港 1823 公众假期", hk_all, count=lambda v: sum(map(len, v.values())))
    if hk is None:
        hk = old.get("hk", {})

    data = {
        "updated": stamp,               # 每次都变 = 心跳，保证每次运行都有提交
        "cn": cn,
        "hk": hk,
        "announce": announce,
        "summary": summarize(cn, now.date()),
        "sources": sources,
    }
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print("cn", {y: (v["official"], len(v["days"])) for y, v in cn.items()})
    print("hk", {y: len(v) for y, v in hk.items()})
    print("failed", [s["name"] for s in sources if not s["ok"]])


if __name__ == "__main__":
    main()
