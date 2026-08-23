#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comment2tg — УДИРДАХ ЦЭС

Энэ файлыг manage.bat дээр давхар дарж ажиллуулна.
Сайтаа сольж, GitHub руу нэг дарахад илгээнэ.
"""

import json
import io
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
FWD = os.path.join(BASE, "forwarder.py")
CFG = os.path.join(BASE, "sites.json")


# ------------------------------------------------------------------ туслах

def run(args, quiet=False):
    """forwarder.py-г дуудна."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([PY, FWD] + args, cwd=BASE, env=env,
                       capture_output=quiet, text=True, encoding="utf-8")
    return r


def git(args, quiet=False):
    r = subprocess.run(["git"] + args, cwd=BASE,
                       capture_output=True, text=True, encoding="utf-8")
    if not quiet and r.stdout.strip():
        print(r.stdout.strip())
    return r


def sites():
    with io.open(CFG, encoding="utf-8") as f:
        return json.load(f).get("sites", [])


def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def pick_site(title):
    """Сайтуудыг дугаарлаж харуулаад нэгийг сонгуулна."""
    ss = sites()
    if not ss:
        print("\n  Сайт алга. Эхлээд '3' сонголтоор шинэ сайт нэмнэ үү.\n")
        return None
    print("\n  %s\n" % title)
    for i, st in enumerate(ss, 1):
        mark = "ON " if st.get("enabled", True) else "off"
        print("    %d) %-16s [%s]" % (i, st.get("name", "?"), mark))
    print("    0) Буцах\n")
    a = ask("  Дугаараа бич: ")
    if not a.isdigit() or int(a) < 1 or int(a) > len(ss):
        return None
    return ss[int(a) - 1].get("name")


# ------------------------------------------------------------------ үйлдлүүд

def act_list():
    run(["--list"])


def act_only():
    name = pick_site("АЛЬ САЙТЫГ ҮЛДЭЭХ ВЭ? (бусад нь унтарна)")
    if not name:
        return
    run(["--only", name])
    offer_push("Зөвхөн %s үлдээв" % name)


def act_toggle():
    name = pick_site("АЛЬ САЙТЫГ АСААХ/УНТРААХ ВЭ?")
    if not name:
        return
    cur = next((s for s in sites() if s.get("name") == name), None)
    if cur is None:
        return
    if cur.get("enabled", True):
        run(["--disable", name])
        offer_push("%s унтраав" % name)
    else:
        run(["--enable", name])
        offer_push("%s асаав" % name)


def act_add():
    print("\n  ШИНЭ САЙТ НЭМЭХ")
    print("  Сайтын хаягийг буулга. Жишээ:  https://ikon.mn/")
    print("  (сэтгэгдэлтэй тодорхой мэдээний линк өгвөл бүр хурдан)\n")
    url = ask("  Хаяг: ")
    if not url.startswith("http"):
        print("\n  Хаяг нь http-ээр эхлэх ёстой. Болилоо.\n")
        return
    print("\n  Шалгаж байна... (хэдэн минут болж магадгүй)\n")
    run(["--add", url])

    names = [s.get("name") for s in sites()]
    host = url.split("//", 1)[-1].split("/")[0].replace("www.", "")
    if host not in names:
        print("\n  Нэмэгдсэнгүй. Сэтгэгдэлтэй өөр мэдээгээр дахин оролдоно уу.\n")
        return

    print("\n  Хуучин сэтгэгдлүүдийг 'уншсан' болгож байна (сувагт цутгахаас сэргийлнэ)...\n")
    run(["--seed", "--site", host])
    offer_push("%s нэмэв" % host)


def act_test():
    name = pick_site("АЛЬ САЙТЫГ ТУРШИХ ВЭ? (илгээхгүй, зөвхөн харуулна)")
    if not name:
        return
    print("")
    run(["--site", name, "--once", "--dry-run", "--limit", "3"])
    print("")


def push(msg):
    print("\n  GitHub руу илгээж байна...\n")
    # Үүлэн ажиллагаа өөрийн тэмдэглэгээг хадгалдаг тул эхлээд татаж авна
    r = git(["pull", "--rebase", "--autostash"], quiet=True)
    if r.returncode != 0:
        print("  ! Татахад алдаа гарлаа:")
        print("   ", (r.stderr or r.stdout).strip()[:400])
        print("\n  Дараах командыг гараар ажиллуулж үзнэ үү:  git pull --rebase\n")
        return
    git(["add", "-A"], quiet=True)
    st = git(["diff", "--cached", "--quiet"], quiet=True)
    if st.returncode == 0:
        print("  Өөрчлөлт алга — илгээх зүйл байхгүй.\n")
        return
    git(["commit", "-m", msg], quiet=True)
    r = git(["push"], quiet=True)
    if r.returncode == 0:
        print("  OK. GitHub руу илгээгдлээ: %s" % msg)
        print("  Дараагийн ажиллагаанаас эхлэн шинэ тохиргоо мөрдөгдөнө.\n")
    else:
        print("  ! Илгээхэд алдаа гарлаа:")
        print("   ", (r.stderr or r.stdout).strip()[:400])
        print("\n  Нэвтрэх цонх гарвал veov6899-bit-ээрээ нэвтэрнэ үү.\n")


def offer_push(msg):
    a = ask("\n  GitHub руу одоо илгээх үү? (Enter = тийм, n = үгүй): ")
    if a.lower() in ("", "y", "yes", "т", "тийм"):
        push("тохиргоо: %s" % msg)
    else:
        print("\n  Илгээгээгүй. Дараа '6' сонголтоор илгээж болно.\n")


# ------------------------------------------------------------------ цэс

MENU = """
  ============================================
    comment2tg  —  УДИРДАХ ЦЭС
  ============================================

    1) Сайтуудыг харах
    2) ЗӨВХӨН нэг сайт үлдээх
    3) Шинэ сайт нэмэх
    4) Сайт асаах / унтраах
    5) Туршиж үзэх (илгээхгүй)
    6) GitHub руу илгээх
    0) Гарах
"""


def main():
    if not os.path.exists(CFG):
        print("sites.json олдсонгүй: %s" % CFG)
        return
    while True:
        print(MENU)
        a = ask("  Сонголт: ")
        if a == "1":
            act_list()
        elif a == "2":
            act_only()
        elif a == "3":
            act_add()
        elif a == "4":
            act_toggle()
        elif a == "5":
            act_test()
        elif a == "6":
            push("тохиргоо шинэчлэв")
        elif a in ("0", "q", ""):
            print("\n  Баяртай.\n")
            return
        else:
            print("\n  0-6 хооронд дугаар бичнэ үү.\n")


if __name__ == "__main__":
    main()
