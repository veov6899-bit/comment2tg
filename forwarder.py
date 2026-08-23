#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comment2tg - дурын сайтын сэтгэгдлийг Telegram суваг руу автоматаар дамжуулагч.

Хэрэглээ:
    python forwarder.py --add https://site.mn/post/123       # ШИНЭ САЙТ НЭМЭХ (линк өгөхөд л болно)
    python forwarder.py --inspect https://site.mn/post/123   # бүтцийг нь гараар судлах
    python forwarder.py --test-telegram                      # ботын холболт шалгах
    python forwarder.py --seed                               # одоо байгаа бүх сэтгэгдлийг "уншсан" гэж тэмдэглэх
    python forwarder.py --once --dry-run                     # илгээхгүйгээр туршиж үзэх
    python forwarder.py --once --limit 1                     # зөвхөн 1 сэтгэгдэл илгээх (тест)
    python forwarder.py --loop                               # тасралтгүй ажиллуулах

Сайтуудаа --add-ээр эсвэл sites.json дотор гараар нэмнэ. Энэ файлыг өөрчлөх шаардлагагүй.

Дэмждэг: энгийн HTML сэтгэгдэл, WordPress REST API, JS массиваар ирдэг сэтгэгдэл,
тусдаа хаягаас (AJAX) ачаалагддаг сэтгэгдэл.
"""

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

# Windows консол дээр кирилл үсэг зөв гарахын тулд
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "sites.json")
STATE_DIR = os.path.join(BASE_DIR, "state")
MAX_STATE_KEYS = 20000          # state файл хэт томрохоос сэргийлнэ
TG_LIMIT = 4000                 # Telegram-ийн 4096 тэмдэгтийн хязгаараас доогуур

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Зарим сүлжээнд (ялангуяа Монголын зарим ISP) api.telegram.org руу залгах нь
# тогтворгүй/хаалттай байдаг тул нэрээр болон шууд IP-гээр ээлжлэн олон удаа оролдоно.
TG_HOST = "api.telegram.org"
TG_FALLBACK_IPS = ["149.154.167.220", "149.154.167.221", "149.154.167.222"]
_TG_ENDPOINT = None                # ажилласан хаягийг санаж, дараагийн удаа шууд ашиглана


# ---------------------------------------------------------------- туслах хэрэгсэл

def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def load_config(path=None):
    global CONFIG_PATH
    path = path or CONFIG_PATH
    CONFIG_PATH = os.path.abspath(path)          # --add ижил файл руу бичихийн тулд
    if not os.path.exists(path):
        log("Тохиргооны файл олдсонгүй: %s" % path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def state_file(site_name):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", site_name)
    return os.path.join(STATE_DIR, "%s.json" % safe)


def load_state(site_name):
    p = state_file(site_name)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(site_name, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    if len(state) > MAX_STATE_KEYS:                       # хамгийн хуучныг нь хасна
        items = sorted(state.items(), key=lambda kv: kv[1])
        state = dict(items[-MAX_STATE_KEYS:])
    tmp = state_file(site_name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)
    os.replace(tmp, state_file(site_name))
    return state


def sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def clean(text):
    if not text:
        return ""
    return re.sub(r"[ \t ]+", " ", text).strip()


def fetch(url, settings, session, quiet=False):
    """Хуудсыг татаж авах (3 удаа оролдоно). quiet=True бол алдааг чимээгүй өнгөрөөнө."""
    headers = {"User-Agent": settings.get("user_agent", DEFAULT_UA),
               "Accept-Language": "mn,en;q=0.8"}
    # Таамаглалын хүсэлт (quiet) ихэвчлэн 404 байдаг тул дахин оролдох нь дэмий цаг.
    tries = 1 if quiet else 3
    timeout = settings.get("probe_timeout", 8) if quiet else settings.get("timeout", 25)
    last = None
    for attempt in range(tries):
        try:
            r = session.get(url, headers=headers, timeout=timeout,
                            verify=settings.get("verify_ssl", True))
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or r.encoding
                return r.text
            last = "HTTP %s" % r.status_code
        except Exception as e:
            last = str(e)
        if attempt < tries - 1:
            time.sleep(1.5 * (attempt + 1))
    if not quiet:
        log("  ! татаж чадсангүй %s (%s)" % (url, last))
    return None


# ---------------------------------------------------------------- задлан шинжлэх

def pick(node, selector):
    """
    selector-т тохирох ЭХНИЙ ХООСОН БИШ элементийн текстийг буцаана.
    (Зарим сайт <p><p>текст</p></p> гэж давхарлан бичдэг тул эхний тохирол хоосон гардаг.)
    """
    if not selector:
        return ""
    try:
        els = node.select(selector)
    except Exception:
        return ""
    for el in els[:6]:
        t = clean(el.get_text(" ", strip=True))
        if t:
            return t
    return ""


def pick_attr(node, selector, attr):
    if not selector or not attr:
        return ""
    try:
        el = node.select_one(selector)
    except Exception:
        return ""
    if el is None:
        return ""
    return clean(str(el.get(attr, "")))


def extract_comments(html_text, site, page_url, title_override=None):
    """Нэг хуудаснаас (эсвэл сэтгэгдлийн фрагментээс) сэтгэгдлүүдийг ялгаж авна."""
    # Хариу нь JSON бол шууд түүнээс уншина
    if (html_text or "").lstrip()[:1] in ("{", "["):
        title = title_override or page_url
        rows = extract_json_comments(html_text)
        if rows:
            log("   JSON хариунаас %d сэтгэгдэл уншлаа" % len(rows))
        out = []
        for r in rows:
            r = dict(r)
            r["page"], r["title"] = page_url, title
            out.append(r)
        return title, out

    soup = BeautifulSoup(html_text, "lxml")
    title = title_override or pick(soup, site.get("title_selector") or "h1") or page_url
    c = site.get("comment", {})
    item_sel = c.get("item")

    # Selector бичээгүй бол автоматаар таниад цээжилнэ (mode: "auto")
    if not item_sel:
        det = detect_selectors(html_text, min_items=int(site.get("min_items", 2)))
        if det:
            c = {"item": det["item"], "text": det["text"], "author": det["author"],
                 "date": det["date"], "id_selector": det["id_selector"], "id_attr": det["id_attr"]}
            site["comment"] = c
            item_sel = c["item"]
            log("   авто-танилт: %s / %s (%d сэтгэгдэл)"
                % (det["item"], det["text"] or "(блок)", det["count"]))

    out = []
    items = []
    if item_sel:
        try:
            items = soup.select(item_sel)
        except Exception as e:
            log("  ! selector алдаатай: %s (%s)" % (item_sel, e))
            items = []

    for it in items:
        # Сэтгэгдэл бичих форм / дүрмийн анхааруулга бол сэтгэгдэл биш
        if _tokens(it) & FORM_TOKENS or _has_form(it):
            continue
        if c.get("text"):
            text = pick(it, c["text"])
        else:
            text = clean(it.get_text(" ", strip=True))
        if not text:
            continue
        if BOILERPLATE_RE.search(text[:300]) or COUNT_START_RE.match(text):
            continue
        # Зарим сайт сэтгэгдлийн эхэнд IP хаяг бичдэг - түүнийг нийтлэхгүй
        text = clean(re.sub(r"^\s*(?:%s)\s*" % IP_RE.pattern, "", text))
        if not text:
            continue

        cid = ""
        if c.get("id_selector") and c.get("id_attr"):
            cid = pick_attr(it, c["id_selector"], c["id_attr"])
        if not cid and c.get("id_attr"):
            cid = clean(str(it.get(c["id_attr"], "")))
        if not cid:
            cid = clean(str(it.get("id", "")))
        if not cid:
            cid = "h" + sha(text)                       # ID байхгүй бол текстийн хэшээр

        date = pick(it, c.get("date"))
        if not date and c.get("date_regex", True):
            # Огнооны selector олдоогүй бол блокийн текстээс регексээр олно.
            # IP хаягийг эхлээд хасна, эс бөгөөс "92.82.92" гэх мэт хог утга гарна.
            whole = IP_RE.sub(" ", _txt(it))
            m = DATE_TEXT.search(whole[:200])
            if m:
                date = clean(m.group(0))

        out.append({
            "id": cid,
            "text": text,
            "author": clean(IP_RE.sub("", pick(it, c.get("author")))),
            "date": date,
            "page": page_url,
            "title": title,
        })

    # HTML-ээс олдоогүй бол JS массиваас уншиж үзнэ (ikon.mn гэх мэт сайтууд)
    if not out and site.get("read_js", True):
        rows = extract_js_comments(html_text)
        if rows:
            log("   JS массиваас %d сэтгэгдэл уншлаа" % len(rows))
            for r in rows:
                r = dict(r)
                r["page"] = page_url
                r["title"] = title
                out.append(r)

    return title, out


def extract_wordpress(site, settings, session):
    """WordPress сайтуудад зориулсан REST API горим (хамгийн найдвартай)."""
    base = site["base_url"].rstrip("/")
    url = "%s/wp-json/wp/v2/comments?per_page=%d&orderby=date&order=desc" % (
        base, int(site.get("per_page", 20)))
    raw = fetch(url, settings, session)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        log("  ! wp-json нь JSON биш байна - mode-оо 'html' болгоно уу")
        return []
    out = []
    for c in data:
        body = c.get("content", {}).get("rendered", "")
        text = clean(BeautifulSoup(body, "lxml").get_text(" ", strip=True))
        if not text:
            continue
        out.append({
            "id": str(c.get("id")),
            "text": text,
            "author": clean(c.get("author_name", "")),
            "date": clean(c.get("date", "")),
            "page": c.get("link") or base,
            "title": "",
        })
    return out


def discover_links(site, settings, session):
    """Жагсаалтын хуудаснаас мэдээний линкүүдийг цуглуулна."""
    from urllib.parse import urljoin
    d = site.get("discover") or {}
    urls = []
    for list_url in d.get("from", []):
        raw = fetch(list_url, settings, session)
        if not raw:
            continue
        soup = BeautifulSoup(raw, "lxml")
        sel = d.get("link_selector") or "a[href]"
        pat = re.compile(d["link_pattern"]) if d.get("link_pattern") else None
        for a in soup.select(sel):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            # "p/199969" гэх мэт харьцангуй хаягийг ч зөв задлана
            href = urljoin(list_url, href)
            if not href.startswith("http"):
                continue
            if pat and not pat.match(href):
                continue
            if href not in urls:
                urls.append(href)
        time.sleep(settings.get("request_delay_seconds", 1.0))
    return urls[: int(d.get("max_pages", 15))]


# ---------------------------------------------------------------- АВТОМАТ ТАНИЛТ
# Зорилго: зөвхөн линк өгөхөд сэтгэгдлийн блокуудыг өөрөө олж, selector-ийг гаргана.

HINT_RE = re.compile(r"comment|setgegdel|coment|koment|reply|discus|otziv|review|feedback"
                     r"|сэтгэгдэл|коммент|хариулт", re.I)
AUTHOR_HINT = re.compile(r"author|user|nick|name|member|profile|owner|нэр|хэрэглэгч", re.I)
DATE_HINT = re.compile(r"date|time|created|posted|ago|огноо|цаг", re.I)
DATE_TEXT = re.compile(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"          # 2024/05/25
                       r"|\d{1,2}[./-]\d{1,2}[./-]\d{4}"          # 25.05.2024
                       r"|\d{1,2}\s+(?:сарын|-р сарын)\s+\d{1,2}"
                       r"|\d+\s*(?:цагийн|минутын|өдрийн|хоногийн)\s*өмнө"
                       r"|өчигдөр|өнөөдөр|уржигдар"
                       r"|\d+\s*(?:hours?|minutes?|days?)\s*ago", re.I)
# IP хаягийг огноо гэж андуурахаас сэргийлнэ (зарим сайт нэрийн оронд IP харуулдаг)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
                   r"|\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")
ID_ATTRS = ["data-id", "data-comment-id", "data-comment", "data-cid", "data-key", "id"]
ID_VALUE = re.compile(r"^[A-Za-z_-]{0,15}?\d{2,}$")
FORM_TAGS = ["textarea", "input", "form", "select"]
# Сэтгэгдэл БИШ мөртлөө сэтгэгдлийн хэсэгт байрладаг блокууд
FORM_TOKENS = {"respond", "form", "mediaform", "editor", "composer", "submit", "csubmit"}
NOISE_TOKENS = {"avatar", "vote", "votes", "like", "unlike", "share", "icon", "count",
                "climiter", "guideline", "guidelines", "warning", "warn", "warring",
                "pagination", "tab", "tabbar", "reply", "cheader", "ccount"}
# Сэтгэгдлийн хэсгийн анхааруулга/дүрэм — эдгээрийг агуулсан блок бол сэтгэгдэл биш,
# харин сэтгэгдэл бичих талбарын тайлбар юм.
BOILERPLATE_RE = re.compile(
    r"\u0430\u0434\u043c\u0438\u043d\s+\u0443\u0441\u0442\u0433\u0430\u0445"          # админ устгах
    r"|\u0451\u0441\s+\u0441\u0443\u0440\u0442\u0430\u0445\u0443\u0443\u043d"          # ёс суртахуун
    r"|\u0425\u0425\u0417\u0425"                                                              # ХХЗХ
    r"|\u0441\u044d\u0442\u0433\u044d\u0433\u0434\u044d\u043b \u0431\u0438\u0447\u0438\u0445\u0434\u044d\u044d"  # сэтгэгдэл бичихдээ
    r"|\u0437\u04af\u0439 \u0437\u043e\u0445\u0438\u0441\u0433\u04af\u0439"              # зүй зохисгүй
    r"|\u0431\u0430\u0440\u0438\u043c\u0442\u0430\u043b\u043d\u0430 \u0443\u0443",      # баримтална уу
    re.I)
# "Сэтгэгдэл (5)" гэж ЭХЭЛСЭН блок = тоологч бүхий хайрцаг
COUNT_START_RE = re.compile(r"^\s*\u0441\u044d\u0442\u0433\u044d\u0433\u0434\u044d\u043b\w*\s*\(", re.I)
# "9 сэтгэгдэл" / "Сэтгэгдэл (3)" гэх мэт тоологчийн гарчиг
COUNT_HDR_RE = re.compile(r"^\(?\s*\d+\s*\)?\s*(?:\u0441\u044d\u0442\u0433\u044d\u0433\u0434\u044d\u043b\w*|comments?)\s*:?$"
                          r"|^(?:\u0441\u044d\u0442\u0433\u044d\u0433\u0434\u044d\u043b\w*|comments?)\s*:?\s*\(?\s*\d+\s*\)?$", re.I)


def css_escape(cls):
    return re.sub(r"([^A-Za-z0-9_-])", r"\\\1", cls)


def _sel_from_classes(classes, prefer_hint=True):
    """Классуудаас CSS selector үүсгэнэ. Боломжтой бол зөвхөн 'comment'-тэй холбоотойг нь авна."""
    classes = [c for c in classes if c and c.strip()]
    if not classes:
        return None
    if prefer_hint:
        hinted = [c for c in classes if HINT_RE.search(c)]
        if hinted:
            return "." + ".".join(css_escape(c) for c in hinted[:2])
    return "." + ".".join(css_escape(c) for c in classes[:3])


def _txt(el):
    return clean(el.get_text(" ", strip=True))


def _ancestor_hinted(el, levels=4):
    p = el.parent
    for _ in range(levels):
        if p is None or getattr(p, "get", None) is None:
            return False
        blob = " ".join(p.get("class") or []) + " " + (p.get("id") or "")
        if HINT_RE.search(blob):
            return True
        p = p.parent
    return False


def _contains(outer, inner):
    p = inner.parent
    while p is not None:
        if p is outer:
            return True
        p = p.parent
    return False


def _depth(el, root):
    d, p = 0, el.parent
    while p is not None and p is not root:
        d += 1
        p = p.parent
    return d


def _detect_id(items):
    """Сэтгэгдэл бүрийн давтагдашгүй ID хаанаас авахыг олно -> (selector, attr)."""
    # 1) item дээрөө байна уу
    for attr in ID_ATTRS:
        vals = [clean(str(i.get(attr) or "")) for i in items]
        if all(ID_VALUE.match(v or "") for v in vals) and len(set(vals)) == len(vals):
            return "", attr
    # 2) дотоод элемент дээр байна уу
    for el in items[0].find_all(True):
        for attr in ID_ATTRS:
            val = clean(str(el.get(attr) or ""))
            if not ID_VALUE.match(val or ""):
                continue
            sel = _sel_from_classes(el.get("class") or [], prefer_hint=False)
            if not sel:
                continue
            got = []
            for it in items:
                e2 = it.select_one(sel)
                got.append(clean(str(e2.get(attr))) if e2 is not None and e2.get(attr) else "")
            if all(ID_VALUE.match(g or "") for g in got) and len(set(got)) >= max(2, int(len(items) * 0.9)):
                return sel, attr
    return "", ""


def _child_groups(items):
    """Сэтгэгдэл бүрийн дотоод элементүүдийг класс-аар нь бүлэглэнэ."""
    groups = {}
    for idx, it in enumerate(items):
        for el in it.find_all(True):
            classes = tuple(c for c in (el.get("class") or []) if c.strip())
            if not classes:
                continue
            g = groups.setdefault(classes, {"items": set(), "texts": [], "depths": [], "el": el})
            if idx not in g["items"]:
                g["items"].add(idx)
                g["texts"].append(_txt(el))
                g["depths"].append(_depth(el, it))
    return groups


def _median(nums):
    nums = sorted(nums)
    if not nums:
        return 0
    n = len(nums)
    return nums[n // 2] if n % 2 else (nums[n // 2 - 1] + nums[n // 2]) / 2.0


def _tokens(el):
    """Класс + id-г үг болгон задална (дэд мөрөөр шалгавал 'thread-even' дотроос 'ad-' олдоно)."""
    blob = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
    return {t for t in re.split(r"[^0-9A-Za-z\u0400-\u04ff_]+", blob.lower()) if t}


def _has_form(el):
    if el.find(["form", "textarea", "select"]) is not None:
        return True
    for inp in el.find_all("input"):
        if (inp.get("type") or "text").lower() != "hidden":
            return True
    return False


def _find_root(soup):
    """Сэтгэгдлийн хэсгийн үндсэн хайрцгийг олно (хуудсын өөр хэсгийн класс орохоос сэргийлнэ)."""
    best, best_score, best_depth = None, 0, -1
    for el in soup.find_all(True):
        blob = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
        if not HINT_RE.search(blob):
            continue
        if _tokens(el) & FORM_TOKENS:
            continue
        score = sum(1 for d in el.find_all(True)
                    if HINT_RE.search(" ".join(d.get("class") or []) + " " + (d.get("id") or "")))
        depth = len(list(el.parents))
        if score > best_score or (score == best_score and depth > best_depth):
            best, best_score, best_depth = el, score, depth
    return best if (best is not None and best_score >= 2) else soup


def _cohesion(els):
    """
    Жинхэнэ сэтгэгдлүүд нэг эцэг доор эгнэдэг (эсвэл хариулт болж дотор нь ордог).
    Санамсаргүй давхцсан класс (жишээ нь нэг сэтгэгдэл дотор 2 удаа орсон .comment-author)
    энэ шалгуурыг давахгүй.
    """
    if not els:
        return 0.0
    counts = {}
    for e in els:
        par = e.parent
        # Эцгийн ТӨРӨЛ + КЛАСС-аар нь бүлэглэнэ. Зарим сайт сэтгэгдэл бүрийг
        # тусдаа ижил хайрцагт боодог тул эцэг нь өөр ч бүтэц нь ижил байдаг.
        if par is None:
            k = ("", ())
        else:
            k = (getattr(par, "name", ""), tuple(par.get("class") or []))
        counts[k] = counts.get(k, 0) + 1
    top = max(counts.values())
    nested = sum(1 for e in els if any(o is not e and _contains(o, e) for o in els))
    return (top + nested) / float(len(els))


def detect_selectors(html_text, min_items=2):
    """
    HTML-ээс сэтгэгдлийн блокуудыг автоматаар олно.
    Буцаах: {"item","text","author","date","id_selector","id_attr","count","samples"} эсвэл None
    """
    soup = BeautifulSoup(html_text, "lxml")
    root = _find_root(soup)

    # --- 1. Нэр дэвшигчдийг КЛАСС ТУС БҮРЭЭР үүсгэнэ.
    # (Бүтэн класс-багцаар бүлэглэвэл WordPress-ийн even/odd/depth-N ээлжлэлээс болж хуваагдана.)
    tokens = set()
    for el in root.find_all(True):
        for c in (el.get("class") or []):
            c = c.strip()
            if c:
                tokens.add(c)

    cands = []
    for tok in tokens:
        # "comment-warning", "comment_guideline" гэх мэтийг үгээр нь задалж шүүнэ
        parts = {x for x in re.split(r"[^0-9A-Za-z\u0400-\u04ff]+", tok.lower()) if x}
        if parts & (NOISE_TOKENS | FORM_TOKENS):
            continue
        sel = "." + css_escape(tok)
        try:
            els = root.select(sel)
        except Exception:
            continue
        if len(els) < min_items:
            continue

        # Сэтгэгдлийн блок нь ӨӨРӨӨ 'comment/сэтгэгдэл' гэсэн утгатай нэртэй байх ёстой.
        # (Эс бөгөөс .clearfix, .row, .media гэх мэт ерөнхий класс сонгогдож хог гарна.)
        own_hint = bool(HINT_RE.search(tok))
        if not own_hint:
            continue
        # Сэтгэгдэл бичих формыг ЖАГСААЛТААС хасна (нэр дэвшигчийг бүхэлд нь хаяхгүй)
        els = [e for e in els if not (_tokens(e) & FORM_TOKENS) and not _has_form(e)]
        if len(els) < min_items:
            continue

        texts = [_txt(e) for e in els]
        med = _median([len(t) for t in texts])
        # Ганцхан блок олдсон үед (сэтгэгдэл цөөтэй мэдээ) урт текст шаардана
        if med < (40 if len(els) < 2 else 20):
            continue
        if any(COUNT_HDR_RE.match(t) for t in texts):             # "9 сэтгэгдэл" гэсэн гарчиг
            continue
        # Анхааруулга/дүрмийн текст агуулсан бол энэ нь сэтгэгдэл бичих ХЭСЭГ, сэтгэгдэл биш
        if sum(1 for t in texts if BOILERPLATE_RE.search(t)) >= max(1, len(texts) * 0.5):
            continue
        if sum(1 for t in texts if COUNT_START_RE.match(t)) >= max(1, len(texts) * 0.5):
            continue
        if _cohesion(els) < 0.6:                                  # эгнээ үүсгэдэггүй = сэтгэгдэл биш
            continue

        score = (200 if own_hint else 0) + min(len(els), 60)
        if 20 <= med <= 1500:
            score += 40
        score += 5 * tok.count("-") + 5 * tok.count("_")
        cands.append({"sel": sel, "els": els, "med": med, "score": score,
                      "count": len(els), "own_hint": own_hint})

    if not cands:
        return None

    # --- 1b. ЖАГСААЛТЫН ХАЙРЦГИЙГ хасна.
    # Нэг элемент нь өөр нэр дэвшигчийн 2+ элементийг агуулж байгаад, өөрийн тоо нь
    # эрс цөөн бол тэр нь бүх сэтгэгдлийг багтаасан хайрцаг мөн (сэтгэгдэл биш).
    # (Хариултууд эцэг сэтгэгдэл дотор ордог тохиолдлыг тоогоор нь ялгана.)
    drop = set()
    for i, x in enumerate(cands):
        for y in cands:
            if y is x or y["count"] < 2:
                continue
            if x["count"] * 2 > y["count"]:
                continue
            if any(sum(1 for e in y["els"] if _contains(xe, e)) >= 2 for xe in x["els"][:5]):
                drop.add(i)
                break
    if len(drop) < len(cands):
        cands = [c for i, c in enumerate(cands) if i not in drop]

    # --- 2. Хамгийн гадна талын блокыг сонгоно (сэтгэгдэл бүрийг бүхэлд нь агуулсан)
    cands.sort(key=lambda c: (-c["score"], -c["count"]))
    best = cands[0]
    for _ in range(4):
        changed = False
        for c in cands[:12]:
            if c is best or c["count"] < min_items:
                continue
            if not _contains(c["els"][0], best["els"][0]):
                continue
            # 'comment' гэсэн нэртэй блокоос ерөнхий зохиомжийн класс руу (.clearfix гэх мэт)
            # хэзээ ч шилжихгүй
            if best["own_hint"] and not c["own_hint"]:
                continue
            # Гадна талынх нь хэт цөөн бол тэр нь ЖАГСААЛТЫН хайрцаг, сэтгэгдэл биш.
            # (Хариултууд эцэг сэтгэгдэл дотор ордог тул тоо нь ойролцоо байх ёстой.)
            if c["count"] * 2 < best["count"]:
                continue
            best, changed = c, True
        if not changed:
            break

    items = best["els"]

    # --- 3. Сэтгэгдлийн текст хаана байгааг олох (нэр, огноо, товчийг оруулахгүйгээр)
    text_sel = ""
    kids = _child_groups(items)
    usable = [(classes, g) for classes, g in kids.items()
              if len(g["items"]) >= max(2, int(len(items) * 0.7))]
    stats = []
    for classes, g in usable:
        # Классын нэрийг ҮГЭЭР нь задлан шүүнэ: "comment-reply-btn" -> {comment, reply, btn}
        toks = set()
        for c0 in classes:
            toks |= {x for x in re.split(r"[^0-9A-Za-z\u0400-\u04ff]+", c0.lower()) if x}
        if toks & (NOISE_TOKENS | FORM_TOKENS):
            continue
        # Бүх сэтгэгдэл дээр ЯГ ИЖИЛ текст гарч байвал энэ нь товчны шошго (сэтгэгдэл биш)
        nonempty = [t for t in g["texts"] if t]
        if len(nonempty) >= 3 and len(set(nonempty)) <= 1:
            continue
        avg_len = sum(len(t) for t in g["texts"]) / float(len(g["texts"]))
        if avg_len < 12:
            continue
        dated = sum(1 for t in g["texts"] if DATE_TEXT.search(t))
        if dated >= len(g["texts"]) * 0.5 and avg_len <= 40:      # энэ бол огноо
            continue
        avg_depth = sum(g["depths"]) / float(len(g["depths"]))
        stats.append((classes, g, avg_len, avg_depth))

    # Зарим сайт сэтгэгдлээ классгүй <p> дотор бичдэг
    for tag_sel in ("p",):
        vals = [pick(it, tag_sel) for it in items]
        got = [v for v in vals if v]
        if len(got) >= max(2, int(len(items) * 0.7)) and len(set(got)) > 1:
            avg = sum(len(v) for v in got) / float(len(got))
            if avg >= 12:
                stats.append((("__tag__" + tag_sel,), None, avg, 2.0))

    if stats:
        max_len = max(x[2] for x in stats)
        keep = [x for x in stats if x[2] >= 0.55 * max_len]
        keep.sort(key=lambda x: (-x[3], -x[2]))                   # хамгийн гүнд байгаа цэвэр текст
        if keep:
            key = keep[0][0]
            if len(key) == 1 and key[0].startswith("__tag__"):
                cand_sel = key[0][len("__tag__"):]
            else:
                cand_sel = _sel_from_classes(list(key))
            if cand_sel:
                ok = sum(1 for it in items if pick(it, cand_sel))
                if ok >= max(2, int(len(items) * 0.7)):
                    text_sel = cand_sel

    # --- 3b. Текстийн эхэнд нэр/огноо орсон бол доторх <p>-г авч цэвэрлэнэ
    if text_sel:
        for refine in (text_sel + " > p", text_sel + " p"):
            checked = cleaner = 0
            for it in items[:8]:
                base, ref = pick(it, text_sel), pick(it, refine)
                if not base or not ref:
                    continue
                checked += 1
                if ref in base and 0 < len(base) - len(ref) <= 60:
                    cleaner += 1
            if checked >= 2 and cleaner >= checked * 0.7:
                text_sel = refine
                break

    # --- 4. Нэр, огноо
    author_sel, date_sel = "", ""

    # <time> элемент байвал огноо нь бараг үргэлж тэнд байдаг
    if sum(1 for it in items if it.find("time") is not None) >= max(2, int(len(items) * 0.7)):
        date_sel = "time"

    for classes, g in kids.items():
        if len(g["items"]) < max(2, int(len(items) * 0.6)):
            continue
        blob = " ".join(classes)
        sel = _sel_from_classes(list(classes), prefer_hint=False)
        if not sel or sel == text_sel:
            continue
        texts = [t for t in g["texts"] if t]
        if not texts:
            continue
        longest = max(len(t) for t in texts)
        dated = sum(1 for t in texts if DATE_TEXT.search(t))
        ipish = sum(1 for t in texts if IP_RE.search(t))
        # Огноог хасахад бараг юу ч үлдэхгүй байж гэмээнэ ЦЭВЭР огноо гэж үзнэ.
        # (Ингэснээр "Нэр 2026/08/19" гэсэн блокыг огноо гэж андуурахгүй.)
        pure = sum(1 for t in texts if len(DATE_TEXT.sub("", t).strip(" .,|-")) <= 3)
        if (not date_sel and longest <= 40 and ipish < len(texts) * 0.5
                and pure >= len(texts) * 0.7
                and (DATE_HINT.search(blob) or dated >= len(texts) * 0.7)):
            date_sel = sel
            continue
        if (not author_sel and AUTHOR_HINT.search(blob) and longest < 80
                and ipish < len(texts) * 0.5 and dated < len(texts) * 0.5):
            author_sel = sel

    id_sel, id_attr = _detect_id(items)

    samples = []
    for it in items[:3]:
        t = pick(it, text_sel) if text_sel else _txt(it)
        if t:
            samples.append(t[:160])

    return {
        "item": best["sel"],
        "text": text_sel,
        "author": author_sel,
        "date": date_sel,
        "id_selector": id_sel,
        "id_attr": id_attr,
        "count": len(items),
        "samples": samples,
    }


# --- JavaScript массиваар ирдэг сэтгэгдлүүд (ikon.mn гэх мэт) -----------------

JS_ARRAY_RE = re.compile(
    r"(?:var|let|const)\s+[A-Za-z_$][\w$]*\s*=\s*(\[\s*\{[\s\S]{40,200000}?\}\s*,?\s*\])\s*;",
    re.I)
# Дотроо нэг түвшин {} агуулсан объектыг ч барина (жишээ нь configs: {})
JS_OBJ_RE = re.compile(r"\{(?:[^{}]|\{[^{}]*\})*\}")
TEXT_KEYS = ["comment", "text", "body", "message", "content", "setgegdel"]
NAME_KEYS = ["name", "author", "username", "user", "nick"]
DATE_KEYS = ["date", "created", "created_at", "time", "datetime", "posted"]
ID_KEYS = ["id", "comment_id", "commentid", "cid"]


def _js_field(obj, keys, numeric=False):
    for k in keys:
        if numeric:
            m = re.search(r"[\"']?\b%s[\"']?\s*:\s*[\"']?(\d+)[\"']?" % re.escape(k), obj)
        else:
            m = re.search(r"[\"']?\b%s[\"']?\s*:\s*\"((?:[^\"\\]|\\.)*)\"" % re.escape(k), obj)
        if m:
            v = m.group(1)
            return re.sub(r"\\(.)", lambda x: {"n": " ", "t": " ", "r": " "}.get(x.group(1), x.group(1)), v)
    return ""


def extract_js_comments(html_text):
    """
    Зарим сайт сэтгэгдлээ HTML биш, JS массиваар өгдөг:
        var comments = [ { id: 1, name: "...", comment: "...", date: "..." }, ... ];
    Түүнийг ялгаж авна.
    """
    best = []
    for m in JS_ARRAY_RE.finditer(html_text):
        arr = m.group(1)
        objs = JS_OBJ_RE.findall(arr)
        rows = []
        for o in objs:
            if len(o) < 20:
                continue
            text = clean(_js_field(o, TEXT_KEYS))
            if len(text) < 2:
                continue
            text = clean(html.unescape(text))          # &quot; гэх мэтийг буцааж тайлна
            rows.append({
                "id": _js_field(o, ID_KEYS, numeric=True) or "h" + sha(text),
                "text": text,
                "author": clean(html.unescape(_js_field(o, NAME_KEYS))),
                "date": clean(_js_field(o, DATE_KEYS)),
            })
        if len(rows) > len(best):
            best = rows
    return best


# --- JSON-оор ирдэг сэтгэгдлүүд (isee.mn, WordPress REST гэх мэт) -------------

def _json_rows(node, depth=0):
    """JSON бүтэц дотроос сэтгэгдлийн жагсаалт (текст талбартай dict-үүд) хайна."""
    if depth > 6:
        return []
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if len(dicts) >= 1:
            ok = 0
            for d in dicts[:5]:
                keys = {k.lower() for k in d.keys()}
                if any(k in keys for k in TEXT_KEYS):
                    ok += 1
            if ok >= max(1, min(3, len(dicts))):
                return dicts
        best = []
        for x in node:
            r = _json_rows(x, depth + 1)
            if len(r) > len(best):
                best = r
        return best
    if isinstance(node, dict):
        best = []
        for v in node.values():
            r = _json_rows(v, depth + 1)
            if len(r) > len(best):
                best = r
        return best
    return []


def _dict_field(d, keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k)
        if isinstance(v, dict):                      # WP REST: {"rendered": "..."}
            v = v.get("rendered") or v.get("raw") or ""
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    return ""


def extract_json_comments(body):
    """JSON хариунаас сэтгэгдлүүдийг ялгаж авна (мөн хүүхэд хариултуудыг нь)."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    rows = _json_rows(data)
    out = []

    def add(d, depth=0):
        raw_text = _dict_field(d, TEXT_KEYS)
        text = clean(BeautifulSoup(html.unescape(raw_text), "lxml").get_text(" ", strip=True))
        if len(text) >= 2:
            out.append({
                "id": _dict_field(d, ID_KEYS) or "h" + sha(text),
                "text": text,
                "author": clean(html.unescape(_dict_field(d, NAME_KEYS))),
                "date": clean(_dict_field(d, DATE_KEYS)),
            })
        if depth < 3:                                # хариултууд (children/replies)
            for k in ("children", "replies", "child", "answers"):
                kids = d.get(k)
                if isinstance(kids, list):
                    for kd in kids:
                        if isinstance(kd, dict):
                            add(kd, depth + 1)

    for d in rows:
        add(d)
    return out


# --- Сэтгэгдлээ тусдаа хаягаас (AJAX) ачаалдаг сайтууд ------------------------

# Мэдээний дугаараас таамаглах түгээмэл хаягууд (сайт бүр өөрийн гэсэн хэлбэртэй)
ENDPOINT_TMPLS = [
    "{origin}/comments/{id}",             # shuurhai.mn
    "{origin}/comments?id={id}",          # isee.mn
    "{origin}/comment/{id}",
    "{origin}/c/{id}",
    "{origin}/a/{id}/comments/list?size=200",   # unuudur.mn
    "{origin}/comments/list?id={id}",
]


def guess_comment_endpoints(page_url, only_tmpl=None):
    """Мэдээний хаягт дугаар байвал сэтгэгдлийн боломжит хаягуудыг үүсгэнэ."""
    m = re.match(r"(https?://[^/]+)(/.*)?$", page_url)
    if not m:
        return []
    origin, path = m.group(1), (m.group(2) or "")
    nums = re.findall(r"\d{3,}", path)
    if not nums:
        return []
    nid = nums[-1]
    tmpls = [only_tmpl] if only_tmpl else ENDPOINT_TMPLS
    return [(t, t.format(origin=origin, id=nid)) for t in tmpls]

FRAG_ATTR_RE = re.compile(
    r'(?:src|href|data-src|data-[a-z-]*url[a-z-]*|data-[a-z-]*value)\s*=\s*"([^"]{3,200})"', re.I)


def find_fragment_urls(html_text, page_url, limit=3):
    """Сэтгэгдэл ачаалж буй туслах хаягуудыг (AJAX endpoint) таамаглана."""
    from urllib.parse import urljoin
    out = []
    for v in FRAG_ATTR_RE.findall(html_text):
        v = v.strip()
        if not v or "#" in v or v.lower().startswith(("javascript:", "mailto:", "data:")):
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|css|js|woff2?)($|\?)", v, re.I):
            continue
        hit = re.search(r"comment|setgegdel|сэтгэгдэл", v, re.I) or re.match(r"^/?c/\d+", v)
        if not hit:
            continue
        if re.search(r"guideline|rule|policy|journal|faq", v, re.I):   # тусламжийн линк
            continue
        full = urljoin(page_url, v)
        if full not in out:
            out.append(full)
    out.sort(key=lambda u: (0 if re.search(r"\d", u) else 1, len(u)))
    return out[:limit]


def guess_discover(url):
    """Жишээ мэдээний хаягаас нүүр хуудас + линкийн regex-ийг таамаглана."""
    m = re.match(r"(https?://[^/]+)(/.*)?$", url)
    origin = m.group(1) if m else url
    path = (m.group(2) or "/") if m else "/"
    segs = [s for s in path.split("?")[0].split("#")[0].split("/") if s]
    if not segs:
        p = "^" + re.escape(origin) + "/[^/?#]+/?$"
        return origin, p, p
    parts = []
    for i, s in enumerate(segs):
        last = (i == len(segs) - 1)
        if re.fullmatch(r"\d+", s):
            parts.append(r"\d+")
        elif last:
            if re.fullmatch(r"[a-z0-9]+", s):
                parts.append(r"[a-z0-9]{%d,%d}" % (max(1, len(s) - 2), len(s) + 4))
            else:
                parts.append(r"[A-Za-z0-9\-_%.Ѐ-ӿ]+")
        else:
            parts.append(re.escape(s))
    strict = "^" + re.escape(origin) + "/" + "/".join(parts) + "/?$"
    loose_parts = parts[:-1] + [r"[^/?#]+"]
    loose = "^" + re.escape(origin) + "/" + "/".join(loose_parts) + "/?$"
    return origin, strict, loose


def _url_shape(u):
    """Линкийн 'хэлбэр' - мэдээ болон цэсний линкийг ялгахад ашиглана."""
    from urllib.parse import urlsplit
    segs = [x for x in urlsplit(u).path.strip("/").split("/") if x]
    last = segs[-1] if segs else ""
    return (len(segs), bool(re.search(r"\d", last)), min(len(last) // 8, 3))


def discover_article_urls(origin, settings, session, limit=40):
    """
    Сайтын нүүр хуудаснаас мэдээний линкүүдийг олно.
    Хамгийн олон давтагдсан 'хэлбэр'-тэй линкүүдийг мэдээ гэж үзэн түрүүлж жагсаана
    (цэс, ангиллын линкүүд цөөн байдаг тул хойшоо орно).
    """
    from urllib.parse import urljoin, urlsplit
    raw = fetch(origin, settings, session)
    if not raw:
        return []
    soup = BeautifulSoup(raw, "lxml")
    host = urlsplit(origin).netloc.replace("www.", "")
    seen, cands = set(), []
    for a in soup.select("a[href]"):
        h = (a.get("href") or "").strip()
        if not h or h.startswith("#") or h.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        sp = urlsplit(urljoin(origin, h))
        if sp.scheme not in ("http", "https"):
            continue
        if sp.netloc.replace("www.", "") != host:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|css|js|pdf|xml|rss|zip)$", sp.path, re.I):
            continue
        if len(sp.path.strip("/")) < 2:
            continue
        full = "%s://%s%s" % (sp.scheme, sp.netloc, sp.path)
        if full in seen:
            continue
        seen.add(full)
        cands.append(full)

    shapes = {}
    for u in cands:
        shapes[_url_shape(u)] = shapes.get(_url_shape(u), 0) + 1
    order = {u: i for i, u in enumerate(cands)}
    cands.sort(key=lambda u: (-shapes[_url_shape(u)], order[u]))
    return cands[:limit]


def pattern_from_links(origin, sample_url, links, min_group=2, max_prefix=3):
    """
    Сурсан мэдээ маань цөөхөн хэсэгт (жишээ нь зөвхөн /opinion/) байвал
    нүүр хуудсан дээрх хамгийн олон давтагдсан хэсгүүдийг нэгтгэж загвар үүсгэнэ.
    Жишээ: ^https://ikon\.mn/(n|opinion)/[a-z0-9]{2,8}/?$
    """
    from urllib.parse import urlsplit
    segs = [x for x in urlsplit(sample_url).path.strip("/").split("/") if x]
    if len(segs) < 2:
        return None
    last = segs[-1]
    if re.fullmatch(r"\d+", last):
        lastpat = r"\d+"
    elif re.fullmatch(r"[a-z0-9]+", last):
        lastpat = r"[a-z0-9]{%d,%d}" % (max(1, len(last) - 2), len(last) + 4)
    else:
        lastpat = r"[A-Za-z0-9\-_%.\u0400-\u04ff]+"

    groups = {}
    for l in links:
        p = [x for x in urlsplit(l).path.strip("/").split("/") if x]
        if len(p) != len(segs):
            continue
        if not re.fullmatch(lastpat, p[-1]):
            continue
        groups.setdefault("/".join(p[:-1]), []).append(l)

    good = [(k, v) for k, v in groups.items() if len(v) >= min_group]
    if not good:
        return None
    good.sort(key=lambda kv: -len(kv[1]))
    prefixes = [k for k, _ in good[:max_prefix]]
    alt = "|".join(re.escape(p) for p in prefixes)
    if len(prefixes) > 1:
        alt = "(?:%s)" % alt
    return "^" + re.escape(origin) + "/" + alt + "/" + lastpat + "/?$"


def cmd_add(url, cfg, settings, name=None):
    """--add: линк өгөхөд сайтыг автоматаар таниад sites.json руу нэмнэ."""
    session = requests.Session()
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    name = name or host
    origin = re.match(r"(https?://[^/]+)", url).group(1)

    if any(s.get("name") == name for s in cfg.get("sites", [])):
        log("'%s' аль хэдийн sites.json дотор байна. Нэрийг --name-ээр өөрчилнө үү." % name)
        return

    # 1) WordPress эсэхийг эхлээд шалгана (хамгийн хялбар зам)
    wp = fetch(origin + "/wp-json/wp/v2/comments?per_page=2", settings, session, quiet=True)
    if wp and wp.strip().startswith("["):
        try:
            data = json.loads(wp)
        except Exception:
            data = []
        if data:
            block = {"name": name, "enabled": True, "mode": "wordpress",
                     "base_url": origin, "per_page": 20, "newest_first": True,
                     "filter": {"min_length": 2, "include_keywords": [], "exclude_keywords": []}}
            cfg.setdefault("sites", []).append(block)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            log("OK: '%s' WordPress сайт гэж танигдлаа. Selector хэрэггүй." % name)
            log("    sites.json-д нэмэгдлээ. Одоо: python forwarder.py --site %s --once --dry-run" % name)
            return

    # 2) Энгийн сайт - selector / JS массив / тусдаа хаягийг автоматаар шалгана.
    #    Өгсөн хуудсанд сэтгэгдэл байхгүй бол нүүрнээс нь сэтгэгдэлтэй мэдээ ХАЙЖ олно.
    probe = {"name": name, "mode": "auto", "title_selector": "h1"}
    tries, cs, found_url, src, title = [], [], None, "", ""
    if len(url.rstrip("/")) > len(origin):
        tries.append(url)

    arts = []

    def _try(cand, min_items):
        """Нэг мэдээг шалгана. Бүтэлгүй бол таамгуудыг цэвэрлэнэ."""
        probe["min_items"] = min_items
        t, rows, source = harvest(cand, probe, settings, session)
        # 1-р үе шатанд 2+ сэтгэгдэлтэй мэдээ л хэрэгтэй (JS/JSON замд ч мөн адил)
        if min_items >= 2 and len(rows) < 2:
            rows = []
        # Бүх "сэтгэгдэл" ижил текст = товчны шошгыг барьсан байна
        if len(rows) >= 3 and len({r["text"] for r in rows}) <= 1:
            log("   (буруу таналт: бүх текст ижил байна, үргэлжлүүлэн хайж байна)")
            rows = []
        if not rows:
            probe.pop("comment", None)
            probe.pop("_probe_fail", None)
        return t, rows, source

    # 1-р үе шат: 2+ сэтгэгдэлтэй мэдээ хайна (тэндээс л зөв бүтэц сурч болно)
    for cand in tries:
        title, cs, src = _try(cand, 2)
        if cs:
            found_url = cand
            break

    if not cs:
        log("Тэр хуудсанд сэтгэгдэл алга. Сайтаас сэтгэгдэлтэй мэдээ хайж байна...")
        arts = discover_article_urls(origin, settings, session)
        log("   %d мэдээний линк олдлоо, ээлжлэн шалгаж байна" % len(arts))
        max_tries = int(settings.get("add_max_tries", 20))
        for pass_no, min_items in ((1, 2), (2, 1)):
            checked = 0
            for cand in arts:
                if cand in tries:
                    continue
                checked += 1
                if checked > max_tries:
                    break
                title, cs, src = _try(cand, min_items)
                if cs:
                    found_url = cand
                    log("   сэтгэгдэлтэй мэдээ олдлоо: %s%s"
                        % (cand, "" if min_items > 1 else "  (зөвхөн 1 сэтгэгдэлтэй)"))
                    break
                time.sleep(settings.get("request_delay_seconds", 1.0))
            if cs:
                break
            if pass_no == 1:
                log("   2+ сэтгэгдэлтэй мэдээ олдсонгүй, ганц сэтгэгдэлтэйг ч зөвшөөрч дахин хайж байна")

    if not cs:
        log("Энэ сайтаас сэтгэгдэл олж чадсангүй.")
        log("Шалтгаан: (а) шалгасан мэдээнүүдэд сэтгэгдэл байхгүй, эсвэл")
        log("          (б) сэтгэгдэл нь Facebook/Disqus эсвэл POST-оор ирдэг (уншиж болохгүй).")
        log("-> Сэтгэгдэлтэй ТОДОРХОЙ мэдээний линкийг шууд өгч үзнэ үү.")
        return

    url = found_url or url
    _, pattern, loose = guess_discover(url)

    det = probe.get("comment") or {}
    log("Сэтгэгдэл олдлоо: %d ширхэг" % len(cs))
    if det.get("item"):
        log("   блок  : %s" % det["item"])
        log("   текст : %s" % (det.get("text") or "(блок бүхэлдээ)"))
        log("   ID    : %s [%s]" % (det.get("id_selector") or "(блок дээр)",
                                    det.get("id_attr") or "текстийн хэш"))
    else:
        log("   бүтэц : сэтгэгдлийг JS/тусдаа хаягаас уншина (selector хэрэггүй)")
    if src.startswith("http"):
        log("   эх сурвалж: %s" % src)
    for cm in cs[:3]:
        log("   жишээ: %s" % cm["text"][:130])

    # Мэдээний линкийн загвар нүүр хуудсан дээр хэр олон линктэй таарч байгааг шалгана
    all_links = arts or discover_article_urls(origin, settings, session, limit=200)
    if len(all_links) < 40:
        all_links = discover_article_urls(origin, settings, session, limit=200)
    hits = sum(1 for l in all_links if re.match(pattern, l))
    if hits < 3:
        loose_hits = sum(1 for l in all_links if re.match(loose, l))
        if loose_hits > hits:
            pattern, hits = loose, loose_hits
    if hits < 10 and all_links:
        # Сурсан мэдээ маань цөөхөн хэсэгт байжээ. Нүүр хуудсан дээрх бусад
        # ижил хэлбэрийн хэсгүүдийг нэгтгэж илүү өргөн загвар үүсгэе.
        p2 = pattern_from_links(origin, url, all_links)
        if p2:
            h2 = sum(1 for l in all_links if re.match(p2, l))
            if h2 > hits:
                pattern, hits = p2, h2
                log("   (загварыг нүүр хуудасны зонхилох хэсгүүдээс өргөтгөлөө)")
    log("   мэдээний линкийн загвар: %s  (нүүрэн дээр %d таарлаа)" % (pattern, hits))

    block = {
        "name": name,
        "enabled": True,
        "mode": "auto",
        "newest_first": True,
        "title_selector": "h1",
        "discover": {"from": [origin + "/"], "link_selector": "a[href]",
                     "link_pattern": pattern, "max_pages": 20},
        "pages": [url],
        "filter": {"min_length": 2, "include_keywords": [], "exclude_keywords": []},
    }
    if det.get("item"):
        block["comment"] = det
    cfg.setdefault("sites", []).append(block)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log("OK: '%s' sites.json-д нэмэгдлээ." % name)
    log("    Шалгах:  python forwarder.py --site %s --once --dry-run" % name)


def harvest(url, site, settings, session):
    """
    Нэг хуудаснаас сэтгэгдэл цуглуулна:
      1) HTML доторх selector-оор,  2) JS массиваас,
      3) олдохгүй бол сэтгэгдлээ тусдаа хаягаас (AJAX) ачаалдаг эсэхийг шалгана.
    Буцаах: (title, comments, source)
    """
    raw = fetch(url, settings, session)
    if not raw:
        return "", [], "error"
    title, cs = extract_comments(raw, site, url)
    if cs:
        return title, cs, "page"
    if not site.get("follow_ajax", True):
        return title, [], "empty"

    tmpl = site.get("_tmpl")
    cands = []
    if tmpl:                                   # өмнө нь ажилласан загварыг эхэлж оролдоно
        cands += guess_comment_endpoints(url, only_tmpl=tmpl)
    # Цээжилсэн загвар энэ хаягт тохирохгүй байж болно (жишээ нь дугааргүй хаяг),
    # тиймээс хуудсан доторх холбоосуудаас ҮРГЭЛЖ давхар хайна.
    have = {c[1] for c in cands}
    cands += [(None, u) for u in find_fragment_urls(raw, url) if u not in have]
    if not tmpl and site.get("probe_endpoints", True) and site.get("_probe_fail", 0) < 3:
        have = {c[1] for c in cands}
        cands += [c for c in guess_comment_endpoints(url) if c[1] not in have]

    for t, frag in cands:
        fr = fetch(frag, settings, session, quiet=True)
        if not fr:
            continue
        _, cs2 = extract_comments(fr, site, url, title_override=title)
        if cs2:
            if t:
                site["_tmpl"] = t              # энэ сайтын хаягийн загварыг цээжиллээ
            return title, cs2, frag

    site["_probe_fail"] = site.get("_probe_fail", 0) + 1
    return title, [], "empty"


# ---------------------------------------------------------------- шүүлтүүр

def passes_filter(cm, site):
    f = site.get("filter") or {}
    text = cm["text"]
    if len(text) < int(f.get("min_length", 1)):
        return False
    low = text.lower()
    inc = [k.lower() for k in f.get("include_keywords", []) if k.strip()]
    exc = [k.lower() for k in f.get("exclude_keywords", []) if k.strip()]
    if inc and not any(k in low for k in inc):
        return False
    if exc and any(k in low for k in exc):
        return False
    return True


# ---------------------------------------------------------------- Telegram

def tg_creds(cfg):
    """
    Ботын токен, сувгийн ID-г дараах дарааллаар олно:
      1) орчны хувьсагч TG_BOT_TOKEN / TG_CHAT_ID  (GitHub Actions гэх мэт үүлэн орчинд)
      2) secrets.json файл                          (энэ компьютер дээр, git-д ордоггүй)
      3) sites.json доторх telegram хэсэг            (хуучин хэлбэр)
    """
    tg = cfg.get("telegram", {})
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat:
        sp = os.path.join(BASE_DIR, "secrets.json")
        if os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    sec = json.load(f)
                token = token or str(sec.get("bot_token", "")).strip()
                chat = chat or str(sec.get("chat_id", "")).strip()
            except Exception as e:
                log("! secrets.json уншиж чадсангүй: %s" % e)
    token = token or str(tg.get("bot_token", "")).strip()
    chat = chat or str(tg.get("chat_id", "")).strip()
    return token, chat


class _PinnedAdapter(HTTPAdapter):
    """IP-гээр шууд холбогдоод SNI/сертификатыг api.telegram.org нэрээр шалгана."""

    def init_poolmanager(self, *a, **kw):
        kw["server_hostname"] = TG_HOST
        kw["assert_hostname"] = TG_HOST
        return super().init_poolmanager(*a, **kw)


def tg_call(cfg, method, payload):
    """
    Telegram Bot API дуудлага.
    Зарим сүлжээнд api.telegram.org-ийн DNS буруу/хаагдсан IP буцаадаг тул
    эхлээд ердийн нэрээр, дараа нь мэдэгдэж буй IP-үүдээр ээлжлэн оролдоно.
    """
    global _TG_ENDPOINT
    settings = cfg.get("settings", {})
    token, _chat = tg_creds(cfg)
    proxy = settings.get("proxy") or None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    timeout = int(settings.get("tg_timeout", 8))
    max_attempts = int(settings.get("tg_attempts", 15))

    endpoints = []
    if _TG_ENDPOINT is not None:
        endpoints.append(_TG_ENDPOINT)          # өмнө нь ажилласан хаягийг эхэлж оролдоно
    if None not in endpoints:
        endpoints.append(None)                  # ердийн DNS
    endpoints.extend(ip for ip in TG_FALLBACK_IPS if ip not in endpoints)

    last_err, tried = None, 0
    while tried < max_attempts:
        for ep in endpoints:
            if tried >= max_attempts:
                break
            tried += 1
            s = requests.Session()
            if ep:
                s.mount("https://", _PinnedAdapter())
                url = "https://%s/bot%s/%s" % (ep, token, method)
                headers = {"Host": TG_HOST}
            else:
                url = "https://%s/bot%s/%s" % (TG_HOST, token, method)
                headers = {}
            try:
                r = s.post(url, data=payload, headers=headers,
                           timeout=timeout, proxies=proxies)
                _TG_ENDPOINT = ep               # ажилласан хаягийг цээжилнэ
                return r.json()
            except Exception as e:
                last_err = e
            finally:
                s.close()
        time.sleep(1)
    log("  ! Telegram-д %d удаа оролдсон ч холбогдож чадсангүй: %s" % (tried, last_err))
    return None


def tg_send(cfg, text, session=None):
    tg = cfg.get("telegram", {})
    token, chat_id = tg_creds(cfg)
    if not token or not chat_id:
        log("! Ботын токен/сувгийн ID олдсонгүй.")
        log("  -> secrets.json файлд эсвэл TG_BOT_TOKEN / TG_CHAT_ID орчны хувьсагчид бичнэ үү.")
        return False
    payload = {
        "chat_id": chat_id,
        "text": text[:TG_LIMIT],
        "parse_mode": "HTML",
        "disable_web_page_preview": bool(tg.get("disable_web_page_preview", True)),
    }
    for attempt in range(3):
        j = tg_call(cfg, "sendMessage", payload)
        if j is None:
            time.sleep(3 * (attempt + 1))
            continue
        if j.get("ok"):
            return True
        if j.get("error_code") == 429:                        # хэт олон илгээлт
            wait = j.get("parameters", {}).get("retry_after", 5)
            log("  ... Telegram хүлээхийг хүслээ (%ss)" % wait)
            time.sleep(wait + 1)
            continue
        log("  ! Telegram алдаа: %s" % j.get("description"))
        return False
    return False


def format_message(cm, site, cfg):
    e = html.escape
    st = cfg.get("settings", {})
    parts = []
    if st.get("show_site", True):
        parts.append("\U0001F4AC <b>%s</b>" % e(site.get("name", "")))
    # Мэдээний гарчиг/линкийг үзүүлэх эсэх (үндсэн: ҮГҮЙ — зөвхөн сэтгэгдлийн текст)
    if st.get("show_link", False):
        title = cm.get("title") or site.get("name", "")
        if cm.get("page") and title:
            parts.append('\U0001F4F0 <a href="%s">%s</a>'
                         % (e(cm["page"], quote=True), e(title[:180])))
        elif cm.get("page"):
            parts.append(e(cm["page"]))
    if parts:
        parts.append("")
    body = cm["text"]
    if len(body) > 2500:
        body = body[:2500] + "..."
    parts.append(e(body))
    meta = []
    if st.get("include_author", False) and cm.get("author"):
        meta.append("\U0001F464 %s" % e(cm["author"]))
    # Огноог үзүүлэх эсэх (үндсэн: ҮГҮЙ)
    if st.get("show_date", False) and cm.get("date"):
        meta.append("\U0001F551 %s" % e(cm["date"]))
    if meta:
        parts.append("")
        parts.append(" · ".join(meta))
    return "\n".join(parts)


# ---------------------------------------------------------------- үндсэн урсгал

def run_site(site, cfg, session, args):
    name = site.get("name", "site")
    settings = cfg.get("settings", {})
    state = load_state(name)
    sent = 0

    log("-> %s" % name)

    if site.get("mode") == "wordpress":
        comments = extract_wordpress(site, settings, session)
        pages_scanned = 1
    else:
        pages = list(site.get("pages", []))
        pages += discover_links(site, settings, session)
        seen_pages, uniq = set(), []
        for p in pages:
            if p not in seen_pages:
                seen_pages.add(p)
                uniq.append(p)
        pages = uniq
        comments, pages_scanned = [], 0
        told = False
        for url in pages:
            pages_scanned += 1
            _, cs, src = harvest(url, site, settings, session)
            if cs and src.startswith("http") and not told:
                log("   сэтгэгдлийг тусдаа хаягаас уншиж байна: %s" % src)
                told = True
            comments.extend(cs)
            time.sleep(settings.get("request_delay_seconds", 1.0))

    # шинэ сэтгэгдлүүдийг ялгах
    fresh = []
    for cm in comments:
        key = "%s|%s" % (site.get("key_prefix", name), cm["id"])
        if key in state:
            continue
        fresh.append((key, cm))

    log("   %d хуудас, %d сэтгэгдэл, шинэ: %d" % (pages_scanned, len(comments), len(fresh)))

    # Ихэнх сайт шинээс хуучин руу жагсаадаг -> хуучнаас нь эхлэн постлохын тулд эргүүлнэ
    if site.get("newest_first", True):
        fresh.reverse()

    if args.seed:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for key, _ in fresh:
            state[key] = stamp
        save_state(name, state)
        log("   OK: %d сэтгэгдлийг 'уншсан' гэж тэмдэглэлээ (илгээгээгүй)" % len(fresh))
        return 0

    cap = args.limit if args.limit is not None else int(settings.get("max_send_per_run", 20))

    for key, cm in fresh:
        if sent >= cap:
            log("   ... хязгаарт хүрлээ (%d). Үлдсэнийг дараагийн удаа илгээнэ." % cap)
            break
        if not passes_filter(cm, site):
            if not args.dry_run:
                state[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            continue
        msg = format_message(cm, site, cfg)
        if args.dry_run:
            print("\n--- ИЛГЭЭХ БАЙСАН МЭДЭЭЛЭЛ ---\n%s\n" % msg)
            ok = True
        else:
            ok = tg_send(cfg, msg, session)
        if ok:
            sent += 1
            if not args.dry_run:                # --dry-run үед юу ч тэмдэглэхгүй
                state[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                state = save_state(name, state)  # давхар илгээхээс сэргийлж тухай бүрд хадгална
                time.sleep(settings.get("send_delay_seconds", 3))
        else:
            log("   ! илгээж чадсангүй, дараагийн удаа дахин оролдоно")
            break

    if not args.dry_run:
        save_state(name, state)
    return sent


# ---------------------------------------------------------------- шинэ сайт судлах

def inspect(url, settings):
    """Шинэ сайт нэмэхэд туслах: сэтгэгдэл байж болзошгүй блокуудыг олж, selector санал болгоно."""
    session = requests.Session()
    raw = fetch(url, settings, session)
    if not raw:
        return
    print("\n=== АВТОМАТ ТАНИЛТ: %s ===" % url)
    det = detect_selectors(raw)
    if det:
        print("Сэтгэгдэл олдлоо: %d ширхэг\n" % det["count"])
        print(json.dumps({"comment": {"item": det["item"], "text": det["text"],
                                      "author": det["author"], "date": det["date"],
                                      "id_selector": det["id_selector"],
                                      "id_attr": det["id_attr"]}},
                         ensure_ascii=False, indent=2))
        for s in det["samples"]:
            print("   жишээ: %s" % s)
        print("\n(Үүнийг гараар хуулах шаардлагагүй - 'python forwarder.py --add %s' гэвэл өөрөө бичнэ.)" % url)
    else:
        print("Автоматаар олж чадсангүй. Доорх жагсаалтаас гараар сонгоно уу.")

    soup = BeautifulSoup(raw, "lxml")
    hint = re.compile(r"comment|setgegdel|reply|discus|otziv|review|сэтгэгдэл", re.I)

    groups = {}
    for el in soup.find_all(True):
        classes = el.get("class") or []
        cls = " ".join(classes)
        idv = el.get("id") or ""
        if not hint.search(cls + " " + idv):
            continue
        if not classes:
            continue
        sel = "." + ".".join(classes[:2])
        g = groups.setdefault(sel, {"count": 0, "samples": []})
        g["count"] += 1
        t = clean(el.get_text(" ", strip=True))
        if 15 < len(t) < 400 and len(g["samples"]) < 2:
            g["samples"].append(t[:200])

    print("\n=== %s ===" % url)
    if not groups:
        print("Сэтгэгдэл шууд HTML дотроос олдсонгүй.")
        print("-> JS-ээр ачаалдаг байх магадлалтай (Disqus, Facebook comments гэх мэт).")
        print("-> Хөтчийн F12 -> Network хэсгээс сэтгэгдэл татаж буй хүсэлтийг олж, тэр URL-ийг pages-д бичнэ үү.")
    else:
        print("Магадлалтай selector-ууд (давтагдсан тоо нь сэтгэгдлийн тоотой ойролцоо байх ёстой):\n")
        for sel, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"])[:15]:
            print("  %-45s  x%d" % (sel, g["count"]))
            for s in g["samples"]:
                print("       - %s" % s)
        print("\nЭдгээрээс сэтгэгдэл бүрийг бүхэлд нь агуулсныг comment.item болгож,")
        print("зөвхөн текстийг агуулсан дотоод нэгийг comment.text болгож sites.json-д бичнэ үү.")

    # WordPress эсэхийг шалгах
    m = re.match(r"(https?://[^/]+)", url)
    if m:
        wp = fetch(m.group(1) + "/wp-json/wp/v2/comments?per_page=1", settings, session, quiet=True)
        if wp and wp.strip().startswith("["):
            print("\nOK: Энэ сайт WordPress REST API-тай! Хамгийн хялбар тохиргоо:")
            print('   {"name": "...", "mode": "wordpress", "base_url": "%s"}' % m.group(1))


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def cmd_list(cfg):
    """Тохируулсан сайтуудыг жагсаана."""
    sites = cfg.get("sites", [])
    if not sites:
        log("Сайт алга. Нэмэх:  python forwarder.py --add https://сайт.mn/")
        return
    print("")
    print("  %-3s %-16s %-10s %-9s %s" % ("", "САЙТ", "ГОРИМ", "УНШСАН", "ХАЯГ"))
    print("  " + "-" * 74)
    for st in sites:
        name = st.get("name", "?")
        on = st.get("enabled", True)
        seen = len(load_state(name))
        d = st.get("discover") or {}
        src = (d.get("from") or [st.get("base_url", "")])
        src = src[0] if src else ""
        print("  %-3s %-16s %-10s %-9d %s"
              % ("ON " if on else "off", name, st.get("mode", "auto"), seen, src))
    print("")
    print("  Зөвхөн нэгийг үлдээх:   python forwarder.py --only <нэр>")
    print("  Нэгийг унтраах:         python forwarder.py --disable <нэр>")
    print("  Буцааж асаах:           python forwarder.py --enable <нэр>")
    print("")


def cmd_toggle(cfg, name, mode):
    """
    mode: "only"    -> зөвхөн энэ сайтыг үлдээж бусдыг унтраана
          "enable"  -> энэ сайтыг асаана
          "disable" -> энэ сайтыг унтраана
    """
    sites = cfg.get("sites", [])
    names = [st.get("name", "") for st in sites]
    if name not in names:
        log("'%s' нэртэй сайт олдсонгүй." % name)
        log("Байгаа сайтууд: %s" % ", ".join(names))
        return
    for st in sites:
        if mode == "only":
            st["enabled"] = (st.get("name") == name)
        elif st.get("name") == name:
            st["enabled"] = (mode == "enable")
    save_config(cfg)
    on = [st.get("name") for st in sites if st.get("enabled", True)]
    log("Одоо асаалттай: %s" % (", ".join(on) if on else "(нэг ч байхгүй)"))
    log("Унтраалттай:   %s" % (", ".join(n for n in names if n not in on) or "(байхгүй)"))


def git_sync():
    """
    Үүлэн дээр ажиллаж байхад 'уншсан' тэмдэглэгээг репод буцааж хадгална.
    Ингэснээр ажиллагаа тасарсан ч сэтгэгдэл давхар илгээгдэхгүй.
    """
    def g(*args):
        return subprocess.run(["git"] + list(args), cwd=BASE_DIR,
                              capture_output=True, text=True, encoding="utf-8")
    try:
        g("add", "state")
        if g("diff", "--cached", "--quiet").returncode == 0:
            return                                   # өөрчлөлт алга
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        g("commit", "-m", "state: %s" % stamp)
        g("pull", "--rebase", "--autostash")
        r = g("push")
        if r.returncode != 0:
            log("   ! state-ийг хадгалж чадсангүй: %s" % (r.stderr or "").strip()[:160])
    except Exception as e:
        log("   ! git алдаа: %s" % e)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Сайтын сэтгэгдлийг Telegram руу дамжуулагч")
    ap.add_argument("--once", action="store_true", help="нэг удаа шалгаад дуусах (үндсэн горим)")
    ap.add_argument("--loop", action="store_true", help="тасралтгүй давтаж ажиллах")
    ap.add_argument("--seed", action="store_true", help="одоо байгаа бүгдийг 'уншсан' болгох, юу ч илгээхгүй")
    ap.add_argument("--dry-run", action="store_true", help="илгээхгүй, зөвхөн дэлгэц дээр харуулах")
    ap.add_argument("--limit", type=int, default=None, help="энэ удаа хамгийн ихдээ N сэтгэгдэл илгээх")
    ap.add_argument("--site", default=None, help="зөвхөн энэ нэртэй сайтыг ажиллуулах")
    ap.add_argument("--add", metavar="URL", default=None,
                    help="ШИНЭ САЙТ НЭМЭХ: сэтгэгдэлтэй мэдээний линк өгөхөд автоматаар таниад sites.json-д бичнэ")
    ap.add_argument("--name", default=None, help="--add хийхэд өгөх сайтын нэр (үндсэн: домэйн)")
    ap.add_argument("--inspect", metavar="URL", default=None, help="шинэ сайтын selector-ийг гараар судлах")
    ap.add_argument("--test-telegram", action="store_true", help="Telegram холболт шалгах")
    ap.add_argument("--max-runtime", type=int, default=0, metavar="МИНУТ",
                    help="давталтыг энэ хугацааны дараа цэвэрхэн зогсоох (үүлэн орчинд)")
    ap.add_argument("--git-sync", action="store_true",
                    help="мөчлөг бүрийн дараа state-ээ git репод хадгалах (үүлэн орчинд)")
    ap.add_argument("--list", action="store_true", help="тохируулсан сайтуудыг жагсаах")
    ap.add_argument("--only", metavar="НЭР", default=None,
                    help="ЗӨВХӨН энэ сайтыг үлдээж бусдыг унтраах")
    ap.add_argument("--enable", metavar="НЭР", default=None, help="сайтыг асаах")
    ap.add_argument("--disable", metavar="НЭР", default=None, help="сайтыг унтраах")
    ap.add_argument("--config", default=None, help="өөр тохиргооны файл ашиглах (default: sites.json)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    settings = cfg.get("settings", {})
    session = requests.Session()

    if args.list:
        cmd_list(cfg)
        return

    for mode, val in (("only", args.only), ("enable", args.enable), ("disable", args.disable)):
        if val:
            cmd_toggle(cfg, val, mode)
            return

    if args.add:
        cmd_add(args.add, cfg, settings, args.name)
        return

    if args.inspect:
        inspect(args.inspect, settings)
        return

    if args.test_telegram:
        ok = tg_send(cfg, "<b>comment2tg</b> холбогдлоо.\nСувагт бот зөв ажиллаж байна.", session)
        log("Telegram тест: %s" % ("АМЖИЛТТАЙ" if ok else "БҮТЭЛГҮЙ"))
        return

    if args.site:
        # Нэрээр нь шууд заасан бол унтраалттай байсан ч ажиллуулна (турших боломжтой байх ёстой)
        sites = [s for s in cfg.get("sites", []) if s.get("name") == args.site]
    else:
        sites = [s for s in cfg.get("sites", []) if s.get("enabled", True)]
    if not sites:
        log("Ажиллуулах сайт алга (sites.json дотор enabled=true эсэхийг шалгана уу)")
        return

    interval = int(settings.get("interval_seconds", 300))

    started = time.time()
    deadline = started + args.max_runtime * 60 if args.max_runtime else None

    while True:
        total = 0
        for s in sites:
            try:
                total += run_site(s, cfg, session, args)
            except Exception as e:
                log("! %s дээр алдаа: %s" % (s.get("name"), e))
        log("Нийт илгээсэн: %d" % total)

        if args.git_sync and not args.dry_run:
            git_sync()

        if not args.loop:
            break

        # Дараагийн мөчлөг хугацаанд багтахгүй бол одоо дуусгана
        if deadline and time.time() + interval >= deadline:
            log("Хугацаа дуусав (%d минут). Ажиллагааг цэвэрхэн зогсоолоо." % args.max_runtime)
            break

        log("... %d секунд хүлээж байна\n" % interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
