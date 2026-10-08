import copy
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
import requests

GITHUB_BANNED_TLDS_URL = "https://raw.githubusercontent.com/nhattanseo3105-max/domain-checker-forSEO-Ares/main/banned_tlds.json"

DEFAULT_BANNED_TLDS = {
    "common": {".ch", ".li", ".cn", ".au", ".fr", ".ca", ".eu", ".eco"},
    "godaddy": {".in", ".co.in", ".net.in", ".org.in", ".cz", ".nl", ".eu"},
    "dynadot": {".it", ".org"},
    "spaceship": {".de"},
}
DEFAULT_UK_PURE_BANNED = True

def load_banned_tlds() -> Tuple[Dict[str, Set[str]], bool]:
    try:
        response = requests.get(GITHUB_BANNED_TLDS_URL, timeout=5)
        if response.status_code == 200:
            data = response.json()
            tlds: Dict[str, Set[str]] = {}
            for cat, items in data.get("banned_tlds", {}).items():
                tlds[cat] = set(items) if isinstance(items, list) else set()
            for cat in DEFAULT_BANNED_TLDS:
                if cat not in tlds:
                    tlds[cat] = set()
            uk = bool(data.get("uk_pure_banned", DEFAULT_UK_PURE_BANNED))
            return tlds, uk
    except Exception as e:
        print(f"Error loading banned TLDs from GitHub: {e}")
    return copy.deepcopy(DEFAULT_BANNED_TLDS), DEFAULT_UK_PURE_BANNED

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

def is_banned(domain: str, provider: str, banned_tlds: Dict[str, Set[str]], uk_pure_banned: bool) -> bool:
    tld = get_tld(domain)
    provider = provider.upper()
    if tld in banned_tlds.get("common", set()):
        return True
    if tld == ".uk" and uk_pure_banned:
        return True
    if provider == "GODADDY" and (
        tld.endswith(".in") or tld in banned_tlds.get("godaddy", set())
    ):
        return True
    if provider == "DYNADOT" and tld in banned_tlds.get("dynadot", set()):
        return True
    if provider == "SPACESHIP" and tld in banned_tlds.get("spaceship", set()):
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

USD_TO_VND = 26_000.0

def _norm_curr(curr: str) -> str:
    c = (curr or "USD").upper()
    if "VND" in c or c in ("₫", "Đ"):
        return "VND"
    return "USD"

def price_to_vnd(price: float, currency: str) -> float:
    if _norm_curr(currency) == "VND":
        return float(price)
    return float(price) * USD_TO_VND

def prices_for_compare(
    price_a: float, curr_a: str, price_b: float, curr_b: str
) -> Tuple[float, float, str, float]:
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
    banned_tlds, uk_pure_banned = load_banned_tlds()
    
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
    banned = [d for d in all_original if is_banned(d, all_original[d]["provider"], banned_tlds, uk_pure_banned)]

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
            va, vb, _unit, threshold = prices_for_compare(
                orig_price, orig_curr, cart_price, curr
            )
            if vb - va > threshold:
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
    cart = result["cart"]
    currency = cart.currency or "USD"
    is_vnd = currency == "VND"
    threshold = PRICE_DIFF_REPORT_VND if is_vnd else PRICE_DIFF_REPORT_USD

    matched_valid = [
        d.domain
        for g in result["groups"]
        for d in g.domains
        if d.domain in result["matched"] and d.domain not in result["banned"]
    ]
    allocated = allocate_tax_fees(cart, matched_valid)
    final_prices = result["final_prices"]

    orig_map: Dict[str, DomainItem] = {}
    group_of: Dict[str, str] = {}
    for g in result["groups"]:
        gname = clean_group_name(g.group_name) or g.group_name or "SEO"
        for d in g.domains:
            orig_map[d.domain] = d
            group_of[d.domain] = gname

    by_group: "OrderedDict[str, List[Tuple[str, float, float]]]" = OrderedDict()
    for domain in matched_valid:
        if domain not in orig_map:
            continue
        orig_item = orig_map[domain]
        orig_price = orig_item.price
        orig_curr = orig_item.currency or "USD"
        cart_price = allocated.get(domain, final_prices.get(domain, 0.0))

        va, vb, unit, thr = prices_for_report(
            orig_price, orig_curr, cart_price, currency
        )
        if vb - va > thr:
            gname = group_of.get(domain, "SEO")
            if gname not in by_group:
                by_group[gname] = []
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
