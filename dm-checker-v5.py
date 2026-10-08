#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Domain Cart Checker — Web Edition v5
Chạy: python3 dm-checker-v5.py
Mở Chrome: http://127.0.0.1:6789

v5 changes:
- Port mặc định 6789
- System tray (mini icon taskbar) + ẩn console Windows
- Chạy ẩn: pythonw dm-checker-v5.py  (cần: pip install pystray Pillow)

v4 changes:
- Giá cart < giá đề xuất (gốc) → không báo đỏ (chỉ cảnh báo khi cart cao hơn)
- Nút "Copy thiếu": copy toàn bộ domain thiếu (mỗi domain 1 dòng), chỉ hiện khi >1 domain thiếu
"""

from __future__ import annotations

import copy
import json
import os
import re
import webbrowser
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ==================== BANNED TLDs ====================
DEFAULT_BANNED_TLDS = {
    "common": {".ch", ".li", ".cn", ".au", ".fr", ".ca", ".eu", ".eco"},
    "godaddy": {".in", ".co.in", ".net.in", ".org.in", ".cz", ".nl", ".eu"},
    "dynadot": {".it", ".org"},
    "spaceship": {".de"},
}
DEFAULT_UK_PURE_BANNED = True

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
BANNED_TLDS_FILE = os.path.join(CONFIG_DIR, "banned_tlds.json")


def load_banned_tlds() -> Tuple[Dict[str, Set[str]], bool]:
    try:
        if os.path.isfile(BANNED_TLDS_FILE):
            with open(BANNED_TLDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            tlds: Dict[str, Set[str]] = {}
            for cat, items in data.get("banned_tlds", {}).items():
                tlds[cat] = set(items) if isinstance(items, list) else set()
            for cat in DEFAULT_BANNED_TLDS:
                if cat not in tlds:
                    tlds[cat] = set()
            uk = bool(data.get("uk_pure_banned", DEFAULT_UK_PURE_BANNED))
            return tlds, uk
    except Exception:
        pass
    return copy.deepcopy(DEFAULT_BANNED_TLDS), DEFAULT_UK_PURE_BANNED


def save_banned_tlds(tlds: Dict[str, Set[str]], uk_banned: bool) -> None:
    try:
        data = {
            "banned_tlds": {cat: sorted(list(s)) for cat, s in tlds.items()},
            "uk_pure_banned": uk_banned,
        }
        with open(BANNED_TLDS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] Không lưu được banned_tlds.json: {e}")


BANNED_TLDS, UK_PURE_BANNED = load_banned_tlds()


# ==================== DATA MODELS ====================
@dataclass
class DomainItem:
    domain: str
    price: float
    currency: str = "USD"
    raw_price: str = ""


@dataclass
class OrderGroup:
    provider: str
    order_id: str
    group_name: str
    full_header: str
    domains: List[DomainItem] = field(default_factory=list)


@dataclass
class CartItem:
    domain: str
    price: float
    currency: str = "USD"
    raw_price: str = ""
    icann: float = 0.0


@dataclass
class CartData:
    provider: str
    items: List[CartItem] = field(default_factory=list)
    total: Optional[float] = None
    tax_fees: Optional[float] = None
    currency: str = "USD"


# ==================== PARSER ====================
def clean_domain(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    match = re.search(r"([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}", text)
    return match.group(0) if match else text


def parse_price(text: str) -> Tuple[float, str]:
    text = text.strip().replace("\u00a0", " ")
    upper = text.upper()
    is_vnd = any(x in upper for x in ["₫", "VND", "VNĐ", "Đ"])
    num = re.sub(r"[^\d.,]", "", text)
    if is_vnd:
        num = num.replace(".", "").replace(",", "")
        try:
            return float(num), "VND"
        except Exception:
            return 0.0, "VND"

    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num and "." not in num:
        num = num.replace(",", ".")
    try:
        return float(num), "USD"
    except Exception:
        return 0.0, "USD"


def normalize_currency(curr: str) -> str:
    if not curr:
        return "USD"
    c = (
        curr.upper()
        .replace("USDT", "USD")
        .replace("VNĐ", "VND")
        .replace("₫", "VND")
        .replace("Đ", "VND")
    )
    if "VND" in c:
        return "VND"
    return "USD"


def parse_original_list(text: str) -> List[OrderGroup]:
    text = text.strip()
    if not text:
        return []

    groups: List[OrderGroup] = []
    current_provider = "UNKNOWN"

    blocks = re.split(r"(?=DM-[A-Z0-9]+)", text, flags=re.I)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        prov = re.match(r"^(NAMECHEAP|DYNADOT|GODADDY|SPACESHIP|SAV)\s*", block, re.I)
        if prov:
            current_provider = prov.group(1).upper()
            block = block[prov.end() :].strip()

        header = re.match(r"(DM-[A-Z0-9]+)\s*-\s*(.+)", block, re.I | re.DOTALL)
        if not header:
            continue

        order_id = header.group(1).upper()
        rest = header.group(2).strip()

        lines = [l.strip() for l in rest.splitlines() if l.strip()]
        group_name = ""
        domains: List[DomainItem] = []

        for line in lines:
            m = re.match(
                r"([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
                r"\s+([\d.,]+)\s*(USD|USDT|VND|VNĐ|₫)?",
                line,
                re.I,
            )
            if m:
                domain = clean_domain(m.group(0).split()[0])
                price_str = m.group(3)
                raw_curr = m.group(4) or ""
                if not raw_curr and any(x in line.upper() for x in ["₫", "VND", "VNĐ"]):
                    raw_curr = "VND"
                curr = normalize_currency(raw_curr or "USD")
                price, detected = parse_price(price_str)
                if curr == "VND" and detected != "VND":
                    try:
                        price = float(re.sub(r"[^\d]", "", price_str.replace(",", "")))
                    except Exception:
                        pass
                domains.append(DomainItem(domain, price, curr, price_str))
            else:
                if not group_name and not re.search(r"\d+\.\d+", line):
                    group_name = line.strip(" -")

        if not group_name:
            first_dom = domains[0].domain if domains else ""
            idx = rest.lower().find(first_dom)
            if idx > 0:
                group_name = rest[:idx].strip(" -\n\t")

        groups.append(
            OrderGroup(
                provider=current_provider,
                order_id=order_id,
                group_name=group_name,
                full_header=f"{current_provider} {order_id} - {group_name}",
                domains=domains,
            )
        )
    return groups


def parse_cart(text: str) -> CartData:
    text = text.strip()
    provider = "UNKNOWN"
    lower = text.lower()
    if "namecheap" in lower:
        provider = "NAMECHEAP"
    elif "dynadot" in lower:
        provider = "DYNADOT"
    elif "godaddy" in lower:
        provider = "GODADDY"
    elif "spaceship" in lower:
        provider = "SPACESHIP"
    elif "sav" in lower:
        provider = "SAV"

    items: List[CartItem] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    PURE_DOMAIN_RE = re.compile(
        r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$",
        re.I,
    )

    def is_pure_domain_line(s: str) -> bool:
        s = s.strip().lower()
        if not s or " " in s or "\t" in s:
            return False
        return bool(PURE_DOMAIN_RE.match(s))

    i = 0
    while i < len(lines):
        line = lines[i]
        if is_pure_domain_line(line):
            domain = clean_domain(line)
            price = 0.0
            icann = 0.0
            raw = ""
            currency = "USD"

            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_lower = next_line.lower()

                if any(
                    x in next_lower
                    for x in ["subtotal", "total:", "tax & fees", "taxes", "tax &"]
                ):
                    break
                if is_pure_domain_line(next_line):
                    break

                if re.search(r"\d", next_line):
                    val, curr = parse_price(next_line)
                    prev_line = lines[j - 1].lower() if j > i else ""
                    if "icann" in next_lower or "icann" in prev_line:
                        icann = val
                    elif raw == "":
                        price = val
                        currency = curr
                        raw = next_line
                j += 1

            items.append(
                CartItem(
                    domain=domain,
                    price=price,
                    currency=currency,
                    raw_price=raw,
                    icann=icann,
                )
            )
            i = j - 1
        i += 1

    total = None
    total_currency = "USD"
    for idx, ln in enumerate(lines):
        if re.match(r"^(total|subtotal)\b", ln, re.I):
            rest = re.sub(r"^(total|subtotal)\b[:\s]*", "", ln, flags=re.I).strip()
            if rest and re.search(r"\d", rest):
                total, total_currency = parse_price(rest)
            elif idx + 1 < len(lines) and re.search(r"\d", lines[idx + 1]):
                total, total_currency = parse_price(lines[idx + 1])
            break

    tax_fees = None
    for idx, ln in enumerate(lines):
        if re.match(r"^(tax\s*&\s*fees|taxes?\s*&\s*fees|tax|fees)\b", ln, re.I):
            rest = re.sub(
                r"^(tax\s*&\s*fees|taxes?\s*&\s*fees|tax|fees)\b[:\s]*",
                "",
                ln,
                flags=re.I,
            ).strip()
            if rest and re.search(r"\d", rest):
                tax_fees, _ = parse_price(rest)
            elif idx + 1 < len(lines) and re.search(r"\d", lines[idx + 1]):
                tax_fees, _ = parse_price(lines[idx + 1])
            break

    cart_currency = total_currency
    if any(it.currency == "VND" for it in items):
        cart_currency = "VND"

    return CartData(
        provider=provider,
        items=items,
        total=total,
        tax_fees=tax_fees,
        currency=cart_currency,
    )


# ==================== LOGIC ====================
def get_tld(domain: str) -> str:
    parts = domain.lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "net", "org", "gov", "ac", "jp"}:
        return "." + ".".join(parts[-2:])
    return "." + parts[-1]


def is_banned(domain: str, provider: str) -> bool:
    tld = get_tld(domain)
    provider = provider.upper()
    if tld in BANNED_TLDS.get("common", set()):
        return True
    if tld == ".uk" and UK_PURE_BANNED:
        return True
    if provider == "GODADDY" and (
        tld.endswith(".in") or tld in BANNED_TLDS.get("godaddy", set())
    ):
        return True
    if provider == "DYNADOT" and tld in BANNED_TLDS.get("dynadot", set()):
        return True
    if provider == "SPACESHIP" and tld in BANNED_TLDS.get("spaceship", set()):
        return True
    return False


def get_final_prices(cart: CartData) -> Dict[str, float]:
    return {item.domain: item.price + item.icann for item in cart.items}


def allocate_tax_fees(cart: CartData, domains: List[str]) -> Dict[str, float]:
    base = get_final_prices(cart)
    currency = cart.currency or "USD"
    is_vnd = currency == "VND"

    ordered = [d for d in domains if d in base]
    if not ordered:
        return {}

    bases = [base[d] for d in ordered]
    sum_base = sum(bases)

    target_total = None
    if cart.total is not None and cart.total > 0:
        target_total = cart.total
    elif cart.tax_fees is not None and cart.tax_fees != 0 and sum_base > 0:
        target_total = sum_base + cart.tax_fees

    if target_total is None or sum_base <= 0:
        return {d: base[d] for d in ordered}

    allocated = []
    for b in bases:
        share = (b / sum_base) * target_total
        if is_vnd:
            allocated.append(round(share))
        else:
            allocated.append(round(share, 2))

    current_sum = sum(allocated)
    diff = target_total - current_sum
    if is_vnd:
        allocated[-1] = round(allocated[-1] + diff)
    else:
        allocated[-1] = round(allocated[-1] + diff, 2)

    return {d: p for d, p in zip(ordered, allocated)}


PRICE_DIFF_USD = 10.0
PRICE_DIFF_VND = 260_000.0
PRICE_DIFF_REPORT_USD = 20.0
PRICE_DIFF_REPORT_VND = 460_000.0
PRICE_MAX_USD = 50.0
PRICE_MAX_VND = 1_300_000.0

# Tỷ giá cố định để so sánh khi gốc USD / cart VND (và ngược lại)
USD_TO_VND = 26_000.0


def _norm_curr(curr: str) -> str:
    c = (curr or "USD").upper()
    if "VND" in c or c in ("₫", "Đ"):
        return "VND"
    return "USD"


def price_to_vnd(price: float, currency: str) -> float:
    """Quy đổi về VND với tỷ giá cố định 1 USD = 26.000 VND."""
    if _norm_curr(currency) == "VND":
        return float(price)
    return float(price) * USD_TO_VND


def prices_for_compare(
    price_a: float, curr_a: str, price_b: float, curr_b: str
) -> Tuple[float, float, str, float]:
    """
    Đưa 2 giá về cùng đơn vị để so sánh.
    - Cùng USD → so USD, ngưỡng USD
    - Cùng VND hoặc khác đơn vị → so VND (quy đổi 26k), ngưỡng VND
    Trả về: (val_a, val_b, unit, threshold)
    """
    ca = _norm_curr(curr_a)
    cb = _norm_curr(curr_b)
    if ca == "USD" and cb == "USD":
        return float(price_a), float(price_b), "USD", PRICE_DIFF_USD
    return (
        price_to_vnd(price_a, ca),
        price_to_vnd(price_b, cb),
        "VND",
        PRICE_DIFF_VND,
    )


def prices_for_report(
    price_a: float, curr_a: str, price_b: float, curr_b: str
) -> Tuple[float, float, str, float]:
    """Giống prices_for_compare nhưng ngưỡng báo cáo (20$ / 460k)."""
    ca = _norm_curr(curr_a)
    cb = _norm_curr(curr_b)
    if ca == "USD" and cb == "USD":
        return float(price_a), float(price_b), "USD", PRICE_DIFF_REPORT_USD
    return (
        price_to_vnd(price_a, ca),
        price_to_vnd(price_b, cb),
        "VND",
        PRICE_DIFF_REPORT_VND,
    )


def compare(groups: List[OrderGroup], cart: CartData) -> Dict:
    provider = cart.provider
    if provider == "UNKNOWN" and groups:
        provider = groups[0].provider

    all_original = {}
    for g in groups:
        for d in g.domains:
            all_original[d.domain] = {"group": g, "item": d, "provider": provider}

    cart_map = {c.domain: c for c in cart.items}
    cart_set = set(cart_map.keys())
    matched = [d for d in all_original if d in cart_set]
    missing = [d for d in all_original if d not in cart_set]
    extra = [d for d in cart_set if d not in all_original]
    banned = [d for d in all_original if is_banned(d, all_original[d]["provider"])]

    final_prices = get_final_prices(cart)
    currency = cart.currency or "USD"

    price_zero: List[str] = []
    price_high: List[str] = []
    price_diff: List[Tuple[str, float, float]] = []

    for domain, citem in cart_map.items():
        cart_price = final_prices.get(domain, citem.price)
        curr = citem.currency or currency

        if cart_price == 0:
            price_zero.append(domain)

        if curr == "VND":
            if cart_price > PRICE_MAX_VND:
                price_high.append(domain)
        else:
            if cart_price > PRICE_MAX_USD:
                price_high.append(domain)

        if domain in all_original:
            orig_item = all_original[domain]["item"]
            orig_price = orig_item.price
            orig_curr = orig_item.currency or "USD"
            # Quy đổi cố định 1 USD = 26.000 VND khi khác đơn vị
            # Chỉ cảnh báo khi giá cart CAO HƠN giá đề xuất (gốc) vượt ngưỡng.
            # Giá cart thấp hơn = tốt → không báo đỏ.
            va, vb, _unit, threshold = prices_for_compare(
                orig_price, orig_curr, cart_price, curr
            )
            if vb - va > threshold:
                # Lưu giá gốc (chưa quy đổi) để hiển thị
                price_diff.append((domain, orig_price, cart_price))

    return {
        "provider": provider,
        "matched": matched,
        "missing": missing,
        "extra": extra,
        "banned": banned,
        "price_zero": price_zero,
        "price_high": price_high,
        "price_diff": price_diff,
        "groups": groups,
        "cart": cart,
        "final_prices": final_prices,
    }


def format_vnd(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def format_usd_unit(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def format_usd_total(value: float) -> str:
    return f"${value:.2f}"


def clean_group_name(name: str) -> str:
    if "-" in name:
        name = name.split("-", 1)[1]
    return name.strip()


def output_step1(groups: List[OrderGroup]) -> str:
    return "\n".join(d.domain for g in groups for d in g.domains)


def output_step2_text(result: Dict) -> str:
    display = {
        "NAMECHEAP": "Namecheap",
        "GODADDY": "GoDaddy",
        "DYNADOT": "Dynadot",
        "SPACESHIP": "Spaceship",
        "SAV": "SAV",
    }.get(result["provider"], result["provider"])

    lines = []
    price_diff = result.get("price_diff", [])
    extra = result.get("extra", [])

    if price_diff or extra:
        lines.append("# **⚠️ CẢNH BÁO LỖI NGHIÊM TRỌNG!!!**")
        if price_diff:
            diff_parts = []
            for d, orig_p, cart_p in price_diff:
                if orig_p >= 1000 or cart_p >= 1000:
                    diff_parts.append(
                        f"{d} (gốc {format_vnd(orig_p)} → cart {format_vnd(cart_p)} VNĐ)"
                    )
                else:
                    diff_parts.append(f"{d} (gốc {orig_p:g} → cart {cart_p:g} USD)")
            lines.append(
                f"**[Giá cart cao hơn gốc >{PRICE_DIFF_USD}$ hoặc >{format_vnd(PRICE_DIFF_VND)} VNĐ: {', '.join(diff_parts)}]**"
            )
        if extra:
            lines.append(f"**[Xuất hiện domain lạ: {', '.join(extra)}]**")
        lines.append("")

    lines.append(f"* Nhà cung cấp: {display} ✔️")

    missing = result.get("missing", [])
    if not missing and not extra:
        lines.append("* Danh sách: Khớp hoàn toàn ✔️")
    else:
        errs = []
        if missing:
            errs.append(f"Thiếu: {', '.join(missing)}")
        if extra:
            errs.append(f"Thừa: {', '.join(extra)}")
        lines.append(f"* Danh sách: {'; '.join(errs)} ❌")

    banned = result.get("banned", [])
    if not banned:
        lines.append("* Đuôi cấm: Không có ✔️")
    else:
        lines.append(f"* Đuôi cấm: Phát hiện đuôi cấm: {', '.join(banned)} ❌")

    return "\n".join(lines)


def output_command_1(result: Dict, mention: str) -> str:
    provider = result["provider"]
    cart = result["cart"]
    currency = cart.currency or "USD"

    valid_domains: List[str] = []
    group_map = OrderedDict()
    for g in result["groups"]:
        clean = clean_group_name(g.group_name)
        if clean not in group_map:
            group_map[clean] = []
        for d in g.domains:
            if d.domain in result["matched"] and d.domain not in result["banned"]:
                group_map[clean].append(d.domain)
                valid_domains.append(d.domain)

    allocated = allocate_tax_fees(cart, valid_domains)

    display = {
        "NAMECHEAP": "Namecheap",
        "GODADDY": "GoDaddy",
        "DYNADOT": "Dynadot",
        "SPACESHIP": "Spaceship",
        "SAV": "SAV",
    }.get(provider, provider)

    lines = [f"{mention} cần thanh toán domain cho SEO:", f"* Nhà cung cấp: {display}", ""]

    for gname, domains in group_map.items():
        if not domains:
            continue
        lines.append(gname)
        lines.append("Domain:")
        for domain in domains:
            price = allocated.get(domain, result["final_prices"].get(domain, 0.0))
            if currency == "VND":
                lines.append(f"* {domain} – {format_vnd(price)} VNĐ")
            else:
                lines.append(f"* {domain} – {format_usd_unit(price)} USD")
        lines.append("")

    if cart.total is not None:
        total = cart.total
    else:
        total = sum(allocated.values()) if allocated else 0.0

    if currency == "VND":
        lines.append(f"Tổng số tiền thanh toán: {format_vnd(total)} VNĐ")
    else:
        lines.append(f"Tổng số tiền thanh toán: {format_usd_total(total)}")

    return "\n".join(lines)


def output_command_2(result: Dict) -> str:
    provider = result["provider"]
    cart = result["cart"]
    final_prices = result["final_prices"]
    currency = cart.currency or "USD"

    display = {
        "NAMECHEAP": "Namecheap",
        "GODADDY": "GoDaddy",
        "DYNADOT": "Dynadot",
        "SPACESHIP": "Spaceship",
        "SAV": "SAV",
    }.get(provider, provider)

    valid_all = [
        d.domain
        for g in result["groups"]
        for d in g.domains
        if d.domain in result["matched"] and d.domain not in result["banned"]
    ]
    allocated = allocate_tax_fees(cart, valid_all)

    lines = [display]
    for g in result["groups"]:
        valid = [
            d
            for d in g.domains
            if d.domain in result["matched"] and d.domain not in result["banned"]
        ]
        missing = [d.domain for d in g.domains if d.domain in result["missing"]]

        if not valid and not missing:
            continue

        lines.append(f"{g.order_id} - {g.group_name}")

        if not valid and missing:
            lines.append(f"THIẾU TẤT CẢ ({', '.join(missing)})")
        elif missing:
            lines.append(f"THIẾU ({', '.join(missing)})")

        for d in valid:
            price = allocated.get(d.domain, final_prices.get(d.domain, d.price))
            if currency == "VND":
                lines.append(f"{d.domain} - {format_vnd(price)}")
            else:
                lines.append(f"{d.domain} - {price:.2f}")
        lines.append("")
    return "\n".join(lines).strip()


def output_lech_gia(result: Dict) -> str:
    """Xuất danh sách domain lệch giá (cart CAO HƠN gốc) > 20$ / 460k VNĐ.
    Cart price dùng giá đã phân bổ tax/fees (GoDaddy: + VAT & Fee).
    Giá cart thấp hơn gốc = tốt, không báo.
    """
    cart = result["cart"]
    currency = cart.currency or "USD"
    is_vnd = currency == "VND"
    threshold = PRICE_DIFF_REPORT_VND if is_vnd else PRICE_DIFF_REPORT_USD

    # Giá cart đã cộng ICANN + phân bổ tax/fees (quan trọng với GoDaddy)
    matched_valid = [
        d.domain
        for g in result["groups"]
        for d in g.domains
        if d.domain in result["matched"] and d.domain not in result["banned"]
    ]
    allocated = allocate_tax_fees(cart, matched_valid)
    final_prices = result["final_prices"]

    # Map domain -> original item
    orig_map: Dict[str, DomainItem] = {}
    group_of: Dict[str, str] = {}
    for g in result["groups"]:
        gname = clean_group_name(g.group_name) or g.group_name or "SEO"
        for d in g.domains:
            orig_map[d.domain] = d
            group_of[d.domain] = gname

    # Collect diffs grouped by SEO name
    by_group: "OrderedDict[str, List[Tuple[str, float, float]]]" = OrderedDict()
    for domain in matched_valid:
        if domain not in orig_map:
            continue
        orig_item = orig_map[domain]
        orig_price = orig_item.price
        orig_curr = orig_item.currency or "USD"
        cart_price = allocated.get(domain, final_prices.get(domain, 0.0))

        # Quy đổi cố định 1 USD = 26.000 VND khi khác đơn vị
        # Chỉ báo lệch khi giá cart CAO HƠN giá đề xuất vượt ngưỡng
        va, vb, unit, thr = prices_for_report(
            orig_price, orig_curr, cart_price, currency
        )
        if vb - va > thr:
            gname = group_of.get(domain, "SEO")
            if gname not in by_group:
                by_group[gname] = []
            # Lưu kèm unit để format đúng khi hiển thị (đã quy đổi nếu mixed)
            by_group[gname].append((domain, va, vb, unit))

    if not by_group:
        unit = "VNĐ" if is_vnd else "USD"
        thr = format_vnd(threshold) if is_vnd else f"{threshold:g}$"
        return f"Không có domain lệch giá > {thr} ({unit})."

    lines: List[str] = []
    for gname, items in by_group.items():
        lines.append(gname)
        for domain, orig_p, cart_p, unit in items:
            if unit == "VND":
                lines.append(
                    f"{domain} - {format_vnd(orig_p)} -> {format_vnd(cart_p)}"
                )
            else:
                def _fmt(v: float) -> str:
                    if abs(v - round(v)) < 1e-9:
                        return f"{v:g}"
                    return f"{v:.2f}".rstrip("0").rstrip(".")

                lines.append(f"{domain} - {_fmt(orig_p)} -> {_fmt(cart_p)}")
        lines.append("")

    lines.append("nhờ TT, TP kiểm tra và duyệt mua giúp em")
    return "\n".join(lines).strip()


def process(
    original_text: str, cart_text: str, command: str, mention: str = "@Pii_S8_003"
) -> str:
    groups = parse_original_list(original_text)
    if command == "step1":
        return output_step1(groups) if groups else "Thiếu dữ liệu để xử lý."

    cart = parse_cart(cart_text)
    if not groups or not cart.items:
        return "Thiếu dữ liệu để xử lý."

    result = compare(groups, cart)

    if command == "step2":
        return output_step2_text(result)
    if command == "1":
        return output_command_1(result, mention)
    if command == "2":
        return output_command_2(result)
    if command == "lechgia":
        return output_lech_gia(result)

    return ""


def result_summary(original_text: str, cart_text: str) -> Optional[Dict]:
    """Structured summary for UI panel (đối soát)."""
    groups = parse_original_list(original_text)
    cart = parse_cart(cart_text)
    if not groups or not cart.items:
        return None
    r = compare(groups, cart)
    display = {
        "NAMECHEAP": "Namecheap",
        "GODADDY": "GoDaddy",
        "DYNADOT": "Dynadot",
        "SPACESHIP": "Spaceship",
        "SAV": "SAV",
    }.get(r["provider"], r["provider"])

    price_diff_fmt = []
    for d, orig_p, cart_p in r.get("price_diff", []):
        if orig_p >= 1000 or cart_p >= 1000:
            price_diff_fmt.append(
                {
                    "domain": d,
                    "orig": format_vnd(orig_p),
                    "cart": format_vnd(cart_p),
                    "unit": "VNĐ",
                }
            )
        else:
            price_diff_fmt.append(
                {
                    "domain": d,
                    "orig": f"{orig_p:g}",
                    "cart": f"{cart_p:g}",
                    "unit": "USD",
                }
            )

    return {
        "provider": display,
        "matched": r["matched"],
        "missing": r["missing"],
        "extra": r["extra"],
        "banned": r["banned"],
        "price_zero": r.get("price_zero", []),
        "price_high": r.get("price_high", []),
        "price_diff": price_diff_fmt,
        "list_ok": not r["missing"] and not r["extra"],
        "banned_ok": not r["banned"],
        "warn_ok": not (
            r.get("price_zero")
            or r.get("price_high")
            or r.get("price_diff")
            or r.get("extra")
        ),
    }


# ==================== HTML UI ====================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Domain Cart Checker • SEO Tool</title>
<link rel="icon" href="https://cms.spidyhost.com/uploads/free_domain_management_6d99a63d6a.svg" type="image/svg+xml" />
<style>
  :root {
    --bg: #f1f5f9;
    --card: #ffffff;
    --border: #e2e8f0;
    --text: #0f172a;
    --muted: #64748b;
    --primary: #3b82f6;
    --primary-h: #2563eb;
    --green: #10b981;
    --green-h: #059669;
    --red: #ef4444;
    --red-h: #dc2626;
    --violet: #7c3aed;
    --violet-h: #6d28d9;
    --sky: #0ea5e9;
    --sky-h: #0284c7;
    --purple: #a855f7;
    --purple-h: #9333ea;
    --slate: #64748b;
    --slate-h: #475569;
    --ok: #16a34a;
    --warn: #ea580c;
    --err: #dc2626;
    --radius: 12px;
    --shadow: 0 1px 3px rgba(15,23,42,.08), 0 4px 12px rgba(15,23,42,.04);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .app {
    display: grid;
    grid-template-columns: 180px 1fr;
    grid-template-rows: auto 1fr auto;
    min-height: 100vh;
    max-width: 1440px;
    margin: 0 auto;
  }
  header {
    grid-column: 1 / -1;
    padding: 16px 20px 8px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header h1 {
    margin: 0;
    font-size: 1.45rem;
    font-weight: 700;
  }
  header .badge {
    font-size: .75rem;
    color: var(--muted);
    background: #e2e8f0;
    padding: 4px 10px;
    border-radius: 999px;
  }
  .sidebar {
    padding: 8px 12px 16px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .side-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 14px 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .side-card h2 {
    margin: 0 0 4px;
    font-size: .95rem;
    font-weight: 700;
  }
  label.field {
    font-size: .8rem;
    color: var(--muted);
    font-weight: 600;
  }
  select, input[type="text"] {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: .9rem;
    background: #fff;
    color: var(--text);
  }
  select:focus, input:focus, textarea:focus {
    outline: 2px solid #93c5fd;
    border-color: var(--primary);
  }
  .btn {
    display: block;
    width: 100%;
    border: 0;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: .9rem;
    font-weight: 700;
    color: #fff;
    cursor: pointer;
    transition: opacity .15s, transform .08s;
  }
  .btn:hover { opacity: .92; }
  .btn:active { transform: scale(.98); }
  .btn:disabled { opacity: .5; cursor: not-allowed; }
  .btn-violet { background: var(--violet); }
  .btn-violet:hover { background: var(--violet-h); }
  .btn-sky { background: var(--sky); }
  .btn-sky:hover { background: var(--sky-h); }
  .btn-green { background: var(--green); }
  .btn-green:hover { background: var(--green-h); }
  .btn-purple { background: var(--purple); }
  .btn-purple:hover { background: var(--purple-h); }
  .btn-red { background: var(--red); }
  .btn-red:hover { background: var(--red-h); }
  .btn-slate { background: var(--slate); }
  .btn-slate:hover { background: var(--slate-h); }
  .btn-orange { background: #ea580c; }
  .btn-orange:hover { background: #c2410c; }
  .btn-sm {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    width: auto;
    padding: 7px 12px;
    font-size: .85rem;
  }
  .main {
    padding: 8px 16px 12px 8px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 0;
  }
  .panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    flex: 1;
    min-height: 260px;
  }
  .panel {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }
  .panel-head {
    padding: 10px 14px 6px;
    font-size: .9rem;
    font-weight: 600;
    color: var(--text);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .panel textarea {
    flex: 1;
    width: 100%;
    border: 0;
    resize: none;
    padding: 8px 14px 14px;
    font-family: Consolas, "Cascadia Code", monospace;
    font-size: .9rem;
    line-height: 1.45;
    color: var(--text);
    background: transparent;
    min-height: 200px;
  }
  .check-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 12px 16px;
  }
  .check-card h3 {
    margin: 0 0 8px;
    font-size: .95rem;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 10px;
    align-items: baseline;
    padding: 4px 0;
    font-size: .92rem;
  }
  .row .label { color: var(--muted); font-weight: 600; min-width: 110px; flex-shrink: 0; }
  .row .value { font-weight: 600; word-break: break-word; flex: 1; min-width: 0; }
  .icon { font-weight: 700; margin-left: 4px; }
  .icon.ok { color: var(--ok); }
  .icon.err { color: var(--err); }
  .icon.warn { color: var(--warn); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    background: #fee2e2;
    color: #b91c1c;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: .82rem;
    font-weight: 700;
    cursor: pointer;
    user-select: none;
  }
  .chip:hover { background: #fecaca; }
  .chip.copied {
    background: #dcfce7;
    color: #16a34a;
  }
  .btn-copy-missing {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: 6px;
    padding: 3px 10px;
    font-size: .78rem;
    font-weight: 700;
    color: #fff;
    background: var(--sky);
    border: 0;
    border-radius: 6px;
    cursor: pointer;
    vertical-align: middle;
    white-space: nowrap;
  }
  .btn-copy-missing:hover { background: var(--sky-h); }
  .btn-copy-missing:active { transform: scale(.97); }
  .warn-line {
    margin-top: 6px;
    color: var(--err);
    font-size: .85rem;
    font-weight: 600;
  }
  .result-wrap {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    min-height: 220px;
    flex: 1;
  }
  .result-head {
    padding: 10px 14px 6px;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .result-head span { font-size: .9rem; font-weight: 600; }
  #result {
    flex: 1;
    width: 100%;
    border: 0;
    resize: none;
    padding: 8px 14px 14px;
    font-family: Consolas, "Cascadia Code", monospace;
    font-size: .9rem;
    line-height: 1.45;
    min-height: 180px;
  }
  footer {
    grid-column: 1 / -1;
    padding: 8px 20px 14px;
    font-size: .82rem;
    color: var(--muted);
  }
  /* Modal */
  .modal-backdrop {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(15,23,42,.45);
    z-index: 100;
    align-items: center;
    justify-content: center;
    padding: 16px;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: #fff;
    border-radius: 14px;
    width: min(520px, 100%);
    max-height: 90vh;
    overflow: auto;
    box-shadow: 0 20px 50px rgba(0,0,0,.2);
    padding: 18px 18px 16px;
  }
  .modal h2 { margin: 0 0 12px; font-size: 1.15rem; }
  .modal .toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 10px;
  }
  .modal textarea {
    width: 100%;
    min-height: 220px;
    font-family: Consolas, monospace;
    font-size: .88rem;
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    resize: vertical;
  }
  .modal .actions {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 12px;
  }
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: #0f172a;
    color: #fff;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: .88rem;
    font-weight: 600;
    opacity: 0;
    pointer-events: none;
    transition: opacity .2s;
    z-index: 200;
  }
  .toast.show { opacity: 1; }
  @media (max-width: 900px) {
    .app { grid-template-columns: 1fr; }
    .sidebar { flex-direction: row; flex-wrap: wrap; padding: 8px 12px; }
    .side-card { flex: 1 1 160px; }
    .panels { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="app">
  <header>
    <h1>Domain Cart Checker</h1>
    <span class="badge">Web Edition v5 · :6789</span>
  </header>

  <aside class="sidebar">
    <div class="side-card">
      <h2>Thao tác</h2>
      <label class="field">Mention</label>
      <select id="mentionSelect">
        <option value="@Pii_S8_003">@Pii_S8_003</option>
        <option value="@bee_s8_01">@bee_s8_01</option>
        <option value="__custom__">Custom...</option>
      </select>
      <input type="text" id="customMention" placeholder="@custom..." disabled />
      <button class="btn btn-violet" data-cmd="step1">🔍 Lọc</button>
      <button class="btn btn-green" data-cmd="1">💡 Đề xuất</button>
      <button class="btn btn-orange" data-cmd="lechgia">⚠️ Lệch giá</button>
      <button class="btn btn-red" id="btnClear">🗑️ Xóa tất cả</button>
      <button class="btn btn-slate" id="btnBanned">⚙ Đuôi cấm</button>
    </div>
  </aside>

  <main class="main">
    <div class="panels">
      <div class="panel">
        <div class="panel-head">Danh sách gốc (Input)</div>
        <textarea id="original" placeholder="Dán danh sách gốc (NAMECHEAP / DYNADOT / … + DM-xxx)…" spellcheck="false"></textarea>
      </div>
      <div class="panel">
        <div class="panel-head">Cart (từ Tampermonkey)</div>
        <textarea id="cart" placeholder="Dán nội dung cart từ nhà cung cấp…" spellcheck="false"></textarea>
      </div>
    </div>

    <div class="check-card" id="checkPanel">
      <h3>Kết quả Đối soát <span style="font-weight:500;color:var(--muted);font-size:.85rem">(click domain để copy)</span></h3>
      <div class="row">
        <div class="label">Nhà cung cấp:</div>
        <div class="value" id="provValue">— <span class="icon" id="provIcon"></span></div>
      </div>
      <div class="row">
        <div class="label">Danh sách:</div>
        <div class="value" id="listValue">— <span class="icon" id="listIcon"></span></div>
      </div>
      <div class="row">
        <div class="label">Đuôi cấm:</div>
        <div class="value" id="bannedValue">— <span class="icon" id="bannedIcon"></span></div>
      </div>
      <div class="row">
        <div class="label">Cảnh báo:</div>
        <div class="value" id="warnValue">— <span class="icon" id="warnIcon"></span></div>
      </div>
      <div class="warn-line" id="warnLine"></div>
    </div>

    <div class="result-wrap">
      <div class="result-head">
        <span>Kết quả (Lọc domain / Đề xuất / DS import)</span>
        <button class="btn btn-sm btn-sky" id="btnCopy">📋 Copy kết quả</button>
        <button class="btn btn-sm btn-violet" id="btnBeast" style="display:none" title="Mở Beast Mode với các domain vừa lọc">🦁 Beast Mode</button>
      </div>
      <textarea id="result" placeholder="Kết quả sẽ hiện ở đây…" spellcheck="false"></textarea>
    </div>
  </main>

  <footer id="status">Sẵn sàng · Hotkey: <b>1</b> = Đề xuất · <b>2</b> = DS import · <b>Esc×3</b> = Xóa tất cả</footer>
</div>

<!-- Banned TLD Modal -->
<div class="modal-backdrop" id="bannedModal">
  <div class="modal">
    <h2>Quản lý đuôi cấm</h2>
    <div class="toolbar">
      <label class="field">Nhóm</label>
      <select id="banCat" style="width:140px">
        <option value="common">common</option>
        <option value="godaddy">godaddy</option>
        <option value="dynadot">dynadot</option>
        <option value="spaceship">spaceship</option>
      </select>
      <label style="display:flex;align-items:center;gap:6px;font-size:.9rem;margin-left:8px">
        <input type="checkbox" id="ukPure" /> Cấm .uk thuần
      </label>
    </div>
    <textarea id="banList" spellcheck="false"></textarea>
    <div class="toolbar" style="margin-top:10px">
      <input type="text" id="banEntry" placeholder=".example" style="width:160px" />
      <button class="btn btn-sm btn-green" id="banAdd">Thêm</button>
      <button class="btn btn-sm btn-red" id="banRemove">Xóa dòng đang chọn</button>
    </div>
    <div class="actions">
      <button class="btn btn-sm btn-slate" id="banReset">Đặt lại mặc định</button>
      <button class="btn btn-sm btn-sky" id="banClose">Đóng</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1800);
}

function getMention() {
  const v = $("#mentionSelect").value;
  if (v === "__custom__") {
    return ($("#customMention").value || "").trim() || "@Pii_S8_003";
  }
  return v;
}

$("#mentionSelect").addEventListener("change", () => {
  const custom = $("#customMention");
  if ($("#mentionSelect").value === "__custom__") {
    custom.disabled = false;
    custom.focus();
  } else {
    custom.disabled = true;
    custom.value = "";
  }
});

function chipHtml(domains) {
  if (!domains || !domains.length) return "";
  return `<div class="chips">${domains.map(d =>
    `<span class="chip" data-d="${escapeAttr(d)}">${escapeHtml(d)}</span>`
  ).join("")}</div>`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function escapeAttr(s) {
  return String(s).replace(/"/g, "&quot;");
}

function bindChips(root) {
  root.querySelectorAll(".chip").forEach(el => {
    el.addEventListener("click", async () => {
      const d = el.dataset.d;
      try {
        await navigator.clipboard.writeText(d);
        el.classList.add("copied");
        el.textContent = "✓ " + d;
        toast("Đã copy: " + d);
        setTimeout(() => {
          el.classList.remove("copied");
          el.textContent = d;
        }, 900);
      } catch (e) {
        toast("Không copy được");
      }
    });
  });
}

function resetCheckPanel() {
  $("#provValue").innerHTML = "— <span class=\"icon\" id=\"provIcon\"></span>";
  $("#listValue").innerHTML = "— <span class=\"icon\" id=\"listIcon\"></span>";
  $("#bannedValue").innerHTML = "— <span class=\"icon\" id=\"bannedIcon\"></span>";
  $("#warnValue").innerHTML = "— <span class=\"icon\" id=\"warnIcon\"></span>";
  $("#warnLine").textContent = "";
}

function setInlineIcon(valueId, iconId, text, iconChar, iconClass) {
  const el = $("#" + valueId);
  el.innerHTML = escapeHtml(text) + ` <span class="icon ${iconClass}" id="${iconId}">${iconChar}</span>`;
}

function renderSummary(s) {
  if (!s) {
    resetCheckPanel();
    return;
  }
  setInlineIcon("provValue", "provIcon", s.provider || "—", "✔", "ok");

  // list
  if (s.list_ok) {
    setInlineIcon("listValue", "listIcon", "Khớp hoàn toàn", "✔", "ok");
  } else {
    let html = "";
    if (s.missing && s.missing.length) {
      html += `<span style="margin-right:4px">Thiếu:</span>${chipHtml(s.missing)}`;
      if (s.missing.length > 1) {
        html += ` <button type="button" class="btn-copy-missing" id="btnCopyMissing" title="Copy tất cả domain thiếu (mỗi domain 1 dòng)">📋 Copy thiếu</button>`;
      }
    }
    if (s.extra && s.extra.length) {
      if (html) html += `<span style="margin:0 6px">|</span>`;
      html += `<span style="margin-right:4px">Thừa:</span>${chipHtml(s.extra)}`;
    }
    $("#listValue").innerHTML = (html || "—") + ` <span class="icon err" id="listIcon">✘</span>`;
    bindChips($("#listValue"));
    const btnMiss = $("#btnCopyMissing");
    if (btnMiss && s.missing && s.missing.length > 1) {
      btnMiss.addEventListener("click", async (e) => {
        e.stopPropagation();
        const text = s.missing.join("\n");
        try {
          await navigator.clipboard.writeText(text);
          btnMiss.textContent = "✓ Đã copy";
          toast("Đã copy " + s.missing.length + " domain thiếu");
          setTimeout(() => { btnMiss.textContent = "📋 Copy thiếu"; }, 1200);
        } catch (err) {
          toast("Không copy được");
        }
      });
    }
  }

  // banned
  if (s.banned_ok) {
    setInlineIcon("bannedValue", "bannedIcon", "Không có", "✔", "ok");
  } else {
    $("#bannedValue").innerHTML = `<span style="margin-right:4px">Phát hiện:</span>${chipHtml(s.banned)} <span class="icon err" id="bannedIcon">✘</span>`;
    bindChips($("#bannedValue"));
  }

  // warnings
  const msgs = [];
  const parts = [];
  if (s.extra && s.extra.length) {
    parts.push(`<span style="margin-right:4px">Domain lạ:</span>${chipHtml(s.extra)}`);
    msgs.push("Domain lạ: " + s.extra.join(", "));
  }
  if (s.price_zero && s.price_zero.length) {
    parts.push(`<span style="margin-right:4px">Giá = 0:</span>${chipHtml(s.price_zero)}`);
    msgs.push("Giá = 0: " + s.price_zero.join(", "));
  }
  if (s.price_high && s.price_high.length) {
    parts.push(`<span style="margin-right:4px">Giá cao:</span>${chipHtml(s.price_high)}`);
    msgs.push("Giá cao: " + s.price_high.join(", "));
  }
  if (s.price_diff && s.price_diff.length) {
    const ds = s.price_diff.map(x => x.domain);
    parts.push(`<span style="margin-right:4px">Chênh giá:</span>${chipHtml(ds)}`);
    msgs.push(
      "Chênh giá: " +
      s.price_diff.map(x => `${x.domain} (gốc ${x.orig} → cart ${x.cart} ${x.unit})`).join(", ")
    );
  }

  if (parts.length) {
    $("#warnValue").innerHTML = parts.join(`<span style="margin:0 6px">|</span>`) + ` <span class="icon warn" id="warnIcon">⚠</span>`;
    bindChips($("#warnValue"));
    $("#warnLine").textContent = "⚠  " + msgs.join("  •  ");
  } else {
    setInlineIcon("warnValue", "warnIcon", "Không có", "✔", "ok");
    $("#warnLine").textContent = "";
  }
}

let lastFilteredDomains = [];
let lastProvider = "";

function updateBeastButton(provider, domains) {
  const btn = $("#btnBeast");
  const p = (provider || "").toUpperCase();
  if ((p === "NAMECHEAP" || p === "SAV") && domains && domains.length) {
    lastFilteredDomains = domains;
    lastProvider = p;
    btn.style.display = "inline-flex";
    btn.title = p === "SAV"
      ? "Mở SAV (mỗi domain 1 tab) với các domain vừa lọc"
      : "Mở Namecheap Beast Mode với các domain vừa lọc";
  } else {
    lastFilteredDomains = [];
    lastProvider = "";
    btn.style.display = "none";
  }
}

async function run(cmd) {
  const original = $("#original").value.trim();
  const cart = $("#cart").value.trim();

  if (cmd === "step1") {
    if (!original) {
      toast("Vui lòng dán Danh sách gốc");
      return;
    }
  } else if (!original || !cart) {
    toast("Vui lòng dán cả Danh sách gốc và Cart");
    return;
  }

  $("#status").textContent = "Đang xử lý…";
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        original,
        cart,
        command: cmd,
        mention: getMention(),
      }),
    });
    const data = await res.json();
    if (data.error) {
      toast(data.error);
      $("#status").textContent = "Lỗi xử lý";
      return;
    }
    $("#result").value = data.result || "";
    if (data.summary) renderSummary(data.summary);
    else if (cmd === "step1") resetCheckPanel();

    if (cmd === "step1") {
      updateBeastButton(data.provider || "", data.domains || []);
    }

    // Lệnh 1 (Đề xuất) hoặc 2 (DS import) → luôn fill nội dung lệnh 2 vào Smart Note IMPORT
    if ((cmd === "1" || cmd === "2") && data.ds_import) {
      try {
        const payload = { text: data.ds_import, ts: Date.now() };
        localStorage.setItem("s8_smart_note_import_fill", JSON.stringify(payload));
        window.dispatchEvent(
          new CustomEvent("s8-fill-import", { detail: payload })
        );
        toast("Đã gửi DS import vào Smart Note (IMPORT)");
      } catch (err) {
        console.warn("Fill Smart Note failed", err);
      }
    }

    const labels = {
      step1: "Đã lọc domain (Bước 1)",
      step2: "Đã đối soát xong",
      "1": "Đã xử lý · Lệnh 1 (Đề xuất)",
      "2": "Đã xử lý · Lệnh 2 (DS import)",
      lechgia: "Đã lọc domain lệch giá (>20$ / 460k)",
    };
    $("#status").textContent = labels[cmd] || "Xong";
  } catch (e) {
    toast("Lỗi kết nối server");
    $("#status").textContent = "Lỗi kết nối";
  }
}

$$("[data-cmd]").forEach(btn => {
  btn.addEventListener("click", () => run(btn.dataset.cmd));
});

function clearAll() {
  $("#original").value = "";
  $("#cart").value = "";
  $("#result").value = "";
  resetCheckPanel();
  updateBeastButton("", []);
  $("#status").textContent = "Đã xóa tất cả · Hotkey: 1 = Đề xuất · 2 = DS import · Esc×3 = Xóa";
  toast("Đã xóa tất cả");
}

$("#btnClear").addEventListener("click", clearAll);

$("#btnCopy").addEventListener("click", async () => {
  const text = $("#result").value.trim();
  if (!text) {
    toast("Không có nội dung để copy");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast("Đã copy kết quả ✓");
    $("#status").textContent = "Đã copy kết quả vào clipboard ✓";
  } catch (e) {
    toast("Không copy được");
  }
});

$("#btnBeast").addEventListener("click", () => {
  if (!lastFilteredDomains.length) {
    toast("Chưa có domain để mở Beast Mode");
    return;
  }
  const p = (lastProvider || "").toUpperCase();
  if (p === "SAV") {
    // Mỗi domain một tab, delay 0.5s giữa các tab để giảm bị Chrome chặn popup
    // Tab đầu mở ngay (trong user gesture); các tab sau cách nhau 500ms
    const domains = lastFilteredDomains.slice();
    const total = domains.length;
    let opened = 0;
    let blocked = 0;

    function openOne(idx) {
      if (idx >= total) {
        if (blocked > 0) {
          toast("SAV: mở " + opened + "/" + total + " tab · " + blocked + " bị chặn — cho phép popup cho localhost");
        } else {
          toast("Đã mở SAV · " + opened + " tab (mỗi domain 1 tab)");
        }
        return;
      }
      const url = `https://v2.sav.com/domain?search=${encodeURIComponent(domains[idx])}`;
      const w = window.open(url, "_blank");
      if (w) opened += 1;
      else blocked += 1;
      if (idx === 0 && total > 1) {
        toast("Đang mở tab SAV 1/" + total + "…");
      }
      setTimeout(() => openOne(idx + 1), 500);
    }
    openOne(0);
  } else {
    // Namecheap Beast Mode: gộp domain trong 1 URL
    const joined = lastFilteredDomains.map(d => encodeURIComponent(d)).join("%09");
    const url = `https://www.namecheap.com/domains/registration/results/?domain=${joined}&type=beast`;
    window.open(url, "_blank");
    toast("Đã mở Namecheap Beast Mode (" + lastFilteredDomains.length + " domain)");
  }
});

// Hotkeys 1 / 2 when not typing in inputs; Esc × 3 = clear all
function isTyping() {
  const el = document.activeElement;
  return el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.isContentEditable);
}
let escCount = 0;
let escTimer = null;
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    escCount += 1;
    if (escTimer) clearTimeout(escTimer);
    escTimer = setTimeout(() => { escCount = 0; }, 800);
    if (escCount >= 3) {
      escCount = 0;
      if (escTimer) clearTimeout(escTimer);
      clearAll();
    }
    return;
  }
  if (isTyping()) return;
  if (e.key === "1") { e.preventDefault(); run("1"); }
  if (e.key === "2") { e.preventDefault(); run("2"); }
});

// ---- Banned TLD modal ----
async function loadBanned() {
  const res = await fetch("/api/banned");
  return res.json();
}

async function saveBanned(payload) {
  await fetch("/api/banned", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

let banState = null;

async function openBannedModal() {
  banState = await loadBanned();
  $("#ukPure").checked = !!banState.uk_pure_banned;
  $("#banCat").value = "common";
  refreshBanList();
  $("#bannedModal").classList.add("open");
}

function refreshBanList() {
  const cat = $("#banCat").value;
  const list = (banState.banned_tlds[cat] || []).slice().sort();
  $("#banList").value = list.length ? list.join("\n") : "";
}

$("#banCat").addEventListener("change", refreshBanList);

$("#ukPure").addEventListener("change", async () => {
  banState.uk_pure_banned = $("#ukPure").checked;
  await saveBanned(banState);
  toast("Đã lưu cài đặt .uk");
});

$("#banAdd").addEventListener("click", async () => {
  let raw = ($("#banEntry").value || "").trim().toLowerCase();
  if (!raw) return;
  if (!raw.startsWith(".")) raw = "." + raw;
  const cat = $("#banCat").value;
  if (!banState.banned_tlds[cat]) banState.banned_tlds[cat] = [];
  if (!banState.banned_tlds[cat].includes(raw)) {
    banState.banned_tlds[cat].push(raw);
  }
  $("#banEntry").value = "";
  await saveBanned(banState);
  refreshBanList();
  toast("Đã thêm " + raw);
});

$("#banRemove").addEventListener("click", async () => {
  const ta = $("#banList");
  const start = ta.selectionStart;
  const val = ta.value;
  const lineStart = val.lastIndexOf("\n", start - 1) + 1;
  let lineEnd = val.indexOf("\n", start);
  if (lineEnd < 0) lineEnd = val.length;
  const line = val.slice(lineStart, lineEnd).trim();
  if (!line) {
    toast("Chọn (bôi) một dòng đuôi để xóa");
    return;
  }
  const cat = $("#banCat").value;
  banState.banned_tlds[cat] = (banState.banned_tlds[cat] || []).filter(x => x !== line);
  await saveBanned(banState);
  refreshBanList();
  toast("Đã xóa " + line);
});

$("#banReset").addEventListener("click", async () => {
  const res = await fetch("/api/banned/reset", { method: "POST" });
  banState = await res.json();
  $("#ukPure").checked = !!banState.uk_pure_banned;
  refreshBanList();
  toast("Đã khôi phục mặc định");
});

$("#banClose").addEventListener("click", () => {
  $("#bannedModal").classList.remove("open");
});
$("#bannedModal").addEventListener("click", (e) => {
  if (e.target === $("#bannedModal")) $("#bannedModal").classList.remove("open");
});
$("#btnBanned").addEventListener("click", openBannedModal);
</script>
</body>
</html>


# ==================== HTTP SERVER ====================
class Handler(BaseHTTPRequestHandler):
    server_version = "DomainCartChecker/1.0"

    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} - {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, html: str):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._html(200, HTML_PAGE)
            return
        if path == "/api/banned":
            global BANNED_TLDS, UK_PURE_BANNED
            BANNED_TLDS, UK_PURE_BANNED = load_banned_tlds()
            self._json(
                200,
                {
                    "banned_tlds": {
                        k: sorted(list(v)) for k, v in BANNED_TLDS.items()
                    },
                    "uk_pure_banned": UK_PURE_BANNED,
                },
            )
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self):
        global BANNED_TLDS, UK_PURE_BANNED
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"error": "JSON không hợp lệ"})
            return

        if path == "/api/run":
            original = str(data.get("original") or "")
            cart = str(data.get("cart") or "")
            command = str(data.get("command") or "")
            mention = str(data.get("mention") or "@Pii_S8_003")
            if command not in ("step1", "step2", "1", "2", "lechgia"):
                self._json(400, {"error": "Lệnh không hợp lệ"})
                return
            try:
                text = process(original, cart, command, mention)
                summary = None
                provider = None
                domains = None
                ds_import = None
                if command == "step1":
                    groups = parse_original_list(original)
                    if groups:
                        provider = groups[0].provider
                        domains = [d.domain for g in groups for d in g.domains]
                else:
                    summary = result_summary(original, cart)
                    # Lệnh 1 hoặc 2: luôn kèm DS import (lệnh 2) để fill Smart Note IMPORT
                    if command in ("1", "2"):
                        groups = parse_original_list(original)
                        cart_obj = parse_cart(cart)
                        if groups and cart_obj.items:
                            result_obj = compare(groups, cart_obj)
                            ds_import = output_command_2(result_obj)
                self._json(
                    200,
                    {
                        "result": text,
                        "summary": summary,
                        "provider": provider,
                        "domains": domains,
                        "ds_import": ds_import,
                    },
                )
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/banned":
            try:
                tlds_in = data.get("banned_tlds") or {}
                new_tlds: Dict[str, Set[str]] = {}
                for cat, items in tlds_in.items():
                    new_tlds[cat] = set(items) if isinstance(items, list) else set()
                for cat in DEFAULT_BANNED_TLDS:
                    if cat not in new_tlds:
                        new_tlds[cat] = set()
                uk = bool(data.get("uk_pure_banned", UK_PURE_BANNED))
                BANNED_TLDS = new_tlds
                UK_PURE_BANNED = uk
                save_banned_tlds(BANNED_TLDS, UK_PURE_BANNED)
                self._json(
                    200,
                    {
                        "ok": True,
                        "banned_tlds": {
                            k: sorted(list(v)) for k, v in BANNED_TLDS.items()
                        },
                        "uk_pure_banned": UK_PURE_BANNED,
                    },
                )
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/banned/reset":
            BANNED_TLDS = copy.deepcopy(DEFAULT_BANNED_TLDS)
            UK_PURE_BANNED = DEFAULT_UK_PURE_BANNED
            save_banned_tlds(BANNED_TLDS, UK_PURE_BANNED)
            self._json(
                200,
                {
                    "banned_tlds": {
                        k: sorted(list(v)) for k, v in BANNED_TLDS.items()
                    },
                    "uk_pure_banned": UK_PURE_BANNED,
                },
            )
            return

        self._json(404, {"error": "Not found"})


def _hide_console_windows():
    """Ẩn cửa sổ console trên Windows (khi chạy bằng python.exe)."""
    try:
        import ctypes
        import sys

        if sys.platform != "win32":
            return
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def _make_tray_icon(size: int = 64):
    """Tạo icon đơn giản (không cần file ảnh ngoài)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin - 1, size - margin - 1],
        fill=(59, 130, 246, 255),
    )
    try:
        draw.text((size // 2 - 10, size // 2 - 14), "D", fill=(255, 255, 255, 255))
    except Exception:
        pass
    return img


def main():
    host = "127.0.0.1"
    port = 6789
    url = f"http://{host}:{port}/"

    httpd = ThreadingHTTPServer((host, port), Handler)

    import threading

    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    print("=" * 50)
    print("  Domain Cart Checker — Web Edition v5")
    print(f"  Mở Chrome: {url}")
    print("  Port: 6789 · Tray icon trên taskbar")
    print("  Right-click icon → Mở / Thoát")
    print("=" * 50)

    try:
        import pystray
        from pystray import MenuItem as item

        def on_open(icon, item):
            try:
                webbrowser.open(url)
            except Exception:
                pass

        def on_quit(icon, item):
            icon.stop()
            try:
                httpd.shutdown()
            except Exception:
                pass

        menu = pystray.Menu(
            item("Mở Domain Cart Checker", on_open, default=True),
            item("Thoát", on_quit),
        )
        icon = pystray.Icon(
            "DomainCartChecker",
            _make_tray_icon(),
            "Domain Cart Checker · :6789",
            menu,
        )

        _hide_console_windows()
        try:
            webbrowser.open(url)
        except Exception:
            pass

        icon.run()

    except ImportError:
        print(
            "\n[!] Chưa cài pystray/Pillow → chạy bình thường (có console).\n"
            "    Cài: pip install pystray Pillow\n"
            "    Sau đó chạy lại để có mini icon trên taskbar.\n"
        )
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            server_thread.join()
        except KeyboardInterrupt:
            print("\nĐã dừng server.")
            httpd.shutdown()
    except Exception as e:
        print(f"[WARN] Tray lỗi: {e} — chạy không tray.")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            server_thread.join()
        except KeyboardInterrupt:
            print("\nĐã dừng server.")
            httpd.shutdown()


if __name__ == "__main__":
    main()
