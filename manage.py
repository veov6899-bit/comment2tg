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


def load_cfg():
    with io.open(CFG, encoding="utf-8") as f:
        return json.load(f)


def save_cfg(c):
    with io.open(CFG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


def sites():
    return load_cfg().get("sites", [])


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
        return

    run(["--enable", name])
    # Удаан унтраалттай байсан сайтад хуримтлагдсан сэтгэгдэл олон байдаг.
    # Тэднийг "уншсан" болгохгүй бол асаамагц сувагт бөөнөөрөө цутгана.
    print("\n  Энэ сайт унтраалттай байх хугацаанд шинэ сэтгэгдэл хуримтлагдсан байж болно.")
    a = ask("  Тэдгээрийг 'уншсан' болгож, ЗӨВХӨН цаашдын шинэ сэтгэгдлийг авах уу?"
            "\n  (Enter = тийм, n = үгүй, бүгдийг нь авна): ")
    if a.lower() in ("", "y", "yes", "т", "тийм"):
        print("\n  Тэмдэглэж байна...\n")
        run(["--seed", "--site", name])
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


def local_loops():
    """Энэ компьютер дээр forwarder.py --loop ажиллаж байгаа эсэхийг шалгана."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
          "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        data = json.loads(r.stdout or "[]")
    except Exception:
        return None                       # шалгаж чадсангүй
    if isinstance(data, dict):
        data = [data]
    out = []
    for p in data:
        cmd = (p.get("CommandLine") or "")
        if "forwarder.py" in cmd and "--loop" in cmd:
            out.append((p.get("ProcessId"), cmd.strip()))
    return out


def act_check_local():
    print("\n  Компьютер дээрх ажиллагааг шалгаж байна...\n")
    loops = local_loops()
    if loops is None:
        print("  Шалгаж чадсангүй. Task Manager -> Details -> python.exe гэж гараар харна уу.\n")
        return
    if not loops:
        print("  ЗӨВ: энэ компьютер дээр юу ч ажиллахгүй байна.")
        print("  Сэтгэгдлийг зөвхөн GitHub илгээж байна - давхардахгүй.\n")
        return
    print("  АНХААР: энд %d ажиллагаа явж байна:\n" % len(loops))
    for pid, cmd in loops:
        print("    PID %-7s %s" % (pid, cmd[:90]))
    print("\n  Энэ нь GitHub-тай зэрэг ажиллаж байгаа тул сэтгэгдэл ХОЁР УДАА ирнэ.")
    a = ask("\n  Зогсоох уу? (Enter = тийм, n = үгүй): ")
    if a.lower() in ("", "y", "yes", "т", "тийм"):
        for pid, _ in loops:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True)
        print("\n  Зогсоолоо.\n")
    else:
        print("")


MODE_TEXT = {
    "strip": "strip  (зөвхөн эможиг арилгаад текстийг илгээнэ)",
    "skip": "skip   (тэр эможитой сэтгэгдлийг ОГТ илгээхгүй)",
}


def parse_emoji(raw):
    """
    Оруулсан мөрийг эможи болгон задална.
    Шууд буулгаж болно (🐸 💋), эсвэл кодоор бичиж болно (U+1F438).
    """
    out = []
    for tok in raw.replace(",", " ").split():
        t = tok.strip()
        if not t:
            continue
        if t.upper().startswith("U+"):
            try:
                t = chr(int(t[2:], 16))
            except ValueError:
                print("    '%s' -> код буруу байна, алгаслаа" % tok)
                continue
        out.append(t)
    return out


def act_emoji():
    changed = False
    while True:
        c = load_cfg()
        ef = c.setdefault("settings", {}).setdefault(
            "emoji_filter", {"mode": "strip", "list": []})
        lst = ef.setdefault("list", [])
        mode = ef.get("mode", "strip")

        print("\n  ЭМОЖИЙН ШҮҮЛТҮҮР")
        print("  Горим: %s\n" % MODE_TEXT.get(mode, mode))
        if lst:
            for i, e in enumerate(lst, 1):
                print("    %d) %s   (U+%04X)" % (i, e, ord(e[0])))
        else:
            print("    (жагсаалт хоосон - юу ч шүүхгүй)")
        print("""
    a) Эможи НЭМЭХ
    d) Эможи ХАСАХ
    m) Горим солих (strip <-> skip)
    0) Буцах""")

        a = ask("\n  Сонголт: ").lower()

        if a in ("0", ""):
            if changed:
                offer_push("эможийн шүүлтүүр шинэчлэв")
            return

        elif a == "a":
            print("\n  Эможигоо буулгана уу (олныг зайгаар тусгаарлана).")
            print("  Буулгаж чадахгүй бол кодоор бичиж болно, жишээ нь: U+1F438\n")
            raw = ask("  Эможи: ")
            add = [e for e in parse_emoji(raw) if e not in lst]
            if not add:
                print("\n  Шинэ эможи алга (эсвэл аль хэдийн жагсаалтад байна).\n")
                continue
            lst.extend(add)
            save_cfg(c)
            changed = True
            print("\n  Нэмэгдлээ: %s\n" % " ".join(add))

        elif a == "d":
            if not lst:
                print("\n  Жагсаалт хоосон байна.\n")
                continue
            n = ask("\n  Хасах эможийн дугаар: ")
            if not n.isdigit() or not (1 <= int(n) <= len(lst)):
                print("\n  Дугаар буруу байна.\n")
                continue
            gone = lst.pop(int(n) - 1)
            save_cfg(c)
            changed = True
            print("\n  Хасагдлаа: %s\n" % gone)

        elif a == "m":
            ef["mode"] = "skip" if mode == "strip" else "strip"
            save_cfg(c)
            changed = True
            print("\n  Шинэ горим: %s\n" % MODE_TEXT[ef["mode"]])

        else:
            print("\n  a, d, m эсвэл 0 гэж бичнэ үү.\n")


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
    # 1) Хадгалаагүй өөрчлөлт байвал commit хийнэ
    if git(["diff", "--cached", "--quiet"], quiet=True).returncode != 0:
        git(["commit", "-m", msg], quiet=True)

    # 2) Илгээгээгүй commit байгаа эсэхийг шалгана
    #    (өмнө нь энэ шалгалт байхгүй байсан тул хадгалсан хэрнээ илгээгээгүй
    #     өөрчлөлтийг "алга" гэж буруу хэлдэг байв)
    ahead = git(["rev-list", "--count", "@{u}..HEAD"], quiet=True).stdout.strip()
    if not ahead.isdigit():
        ahead = git(["rev-list", "--count", "origin/main..HEAD"], quiet=True).stdout.strip()
    if not ahead.isdigit() or int(ahead) == 0:
        print("  Өөрчлөлт алга — бүх зүйл GitHub дээр байна.\n")
        return
    print("  Илгээх өөрчлөлт: %s ширхэг\n" % ahead)
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
    7) Энэ компьютер дээр ажиллаж байгаа эсэхийг шалгах
    8) Эможийн шүүлтүүр (нэмэх / хасах)
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
        elif a == "7":
            act_check_local()
        elif a == "8":
            act_emoji()
        elif a in ("0", "q", ""):
            print("\n  Баяртай.\n")
            return
        else:
            print("\n  0-6 хооронд дугаар бичнэ үү.\n")


if __name__ == "__main__":
    main()
