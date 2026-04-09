import argparse
import asyncio
import glob
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp
import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm


def norm_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none") else s


def try_float(v: Any) -> Optional[float]:
    s = norm_str(v)
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except Exception:
        return None


def detect_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    cols = list(df.columns)
    low = {c: str(c).strip().lower() for c in cols}
    for cand in candidates:
        for c in cols:
            if cand in low[c]:
                return c
    return None


def get_distributor_id(x: Dict[str, Any]) -> Optional[int]:
    for k in ("distributorId", "distributorID", "distributor_id", "distributor"):
        v = x.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
        if isinstance(v, dict):
            for kk in ("id", "distributorId"):
                vv = v.get(kk)
                if isinstance(vv, int):
                    return vv
                if isinstance(vv, str) and vv.isdigit():
                    return int(vv)
    return None


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    params: Dict[str, Any],
    timeout_s: int
) -> Tuple[int, Any]:
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            try:
                return resp.status, await resp.json(content_type=None)
            except Exception:
                return resp.status, await resp.text()
    except Exception as e:
        return 0, {"error": str(e)}


def _extract_list(payload: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ("data", "result", "results", "items", "distributors", "distributorList", "list"):
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return None


def _guess_id_name(item: Dict[str, Any]) -> Tuple[Optional[int], str]:
    _id = None
    for k in ("id", "distributorId", "distributorID", "code"):
        v = item.get(k)
        if isinstance(v, int):
            _id = v
            break
        if isinstance(v, str) and v.isdigit():
            _id = int(v)
            break

    name = norm_str(
        item.get("name")
        or item.get("title")
        or item.get("description")
        or item.get("distributorName")
        or item.get("distributorDescription")
        or item.get("site")
        or item.get("domain")
    )
    return _id, name


async def fetch_distributor_map(
    session: aiohttp.ClientSession,
    base_url: str,
    userlogin: str,
    userpsw_md5: str,
    timeout_s: int
) -> Dict[int, str]:
    """
    Получаем справочник distributorId -> 'autopiter.ru [online]' и т.д.
    В разных инсталляциях ABCP endpoint может отличаться, поэтому пробуем несколько вариантов.
    """
    base = base_url.rstrip("/")
    params = {"userlogin": userlogin, "userpsw": userpsw_md5}

    candidates = [
        ("/cp/distributors", params),
        ("/cp/distributors", {**params, "distributors4mc": 1}),
        ("/cp/distributors/list", params),
        ("/cp/distributors/get", params),
        ("/cp/distributor/list", params),
        ("/cp/routes", params),          # на некоторых стендах список “поставщиков” может быть в routes
        ("/cp/routes/list", params),
    ]

    for ep, p in candidates:
        status, payload = await _fetch_json(session, base + ep, p, timeout_s)
        lst = _extract_list(payload)
        if status == 200 and lst:
            out: Dict[int, str] = {}
            for it in lst:
                did, name = _guess_id_name(it)
                if did is None:
                    continue
                if name:
                    out[did] = name
            if out:
                return out

    return {}


def build_display_map(distributor_map: Dict[int, str]) -> Dict[int, str]:
    """
    Гарантируем уникальные имена: если одно имя у нескольких id — добавим (id)
    """
    counts: Dict[str, int] = {}
    for _, name in distributor_map.items():
        counts[name] = counts.get(name, 0) + 1

    out: Dict[int, str] = {}
    for did, name in distributor_map.items():
        out[did] = f"{name} ({did})" if counts.get(name, 0) > 1 else name
    return out


def store_display(x: Dict[str, Any], disp_by_id: Dict[int, str]) -> str:
    # иногда имя может прилетать прямо в ответе
    direct = norm_str(x.get("distributorDescription") or x.get("distributorName"))
    if direct and not direct.isdigit():
        return direct

    did = get_distributor_id(x)
    if did is not None and did in disp_by_id:
        return disp_by_id[did]

    # фолбэки (если справочник не отдался)
    # это будет хуже (цифры), но лучше чем падать
    name = norm_str(x.get("supplierDescription") or x.get("supplierName"))
    code = norm_str(x.get("supplierCode") or x.get("supplier_code"))
    return name or code or (str(did) if did is not None else "ABCP")


async def fetch_offers(
    session: aiohttp.ClientSession,
    base_url: str,
    userlogin: str,
    userpsw_md5: str,
    number: str,
    brand: str = "",
    timeout_s: int = 30,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "userlogin": userlogin,
        "userpsw": userpsw_md5,
        "number": number,
        "useOnlineStocks": 1,
        "disableOnlineFiltering": 1,
        "withOutAnalogs": 1,
    }
    if brand:
        params["brand"] = brand

    base = base_url.rstrip("/")
    endpoints = ["/cp/search/articles", "/search/articles"]

    last_payload: Any = None
    for ep in endpoints:
        status, payload = await _fetch_json(session, base + ep, params, timeout_s)
        last_payload = payload

        if status == 200 and isinstance(payload, list):
            return payload

        if status == 200 and isinstance(payload, dict):
            for k in ("data", "result", "results", "items"):
                if isinstance(payload.get(k), list):
                    return payload[k]

    if isinstance(last_payload, list):
        return last_payload
    return []


def best_price_per_store(offers: Iterable[Dict[str, Any]], disp_by_id: Dict[int, str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for x in offers:
        store = store_display(x, disp_by_id)
        price = try_float(x.get("price") or x.get("sellPrice") or x.get("cost"))
        if price is None:
            continue
        if store not in out or price < out[store]:
            out[store] = price
    return out


def offer_row(article: str, brand_in: str, x: Dict[str, Any], disp_by_id: Dict[int, str]) -> Dict[str, Any]:
    did = get_distributor_id(x)
    return {
        "Артикул": article,
        "Бренд": norm_str(x.get("brand") or brand_in),
        "ПоставщикId": did,
        "Поставщик": disp_by_id.get(did, "") if did is not None else "",
        "ПоставщикКод": norm_str(x.get("supplierCode") or x.get("supplier_code")),
        "Цена": try_float(x.get("price") or x.get("sellPrice") or x.get("cost")),
        "Валюта": norm_str(x.get("currency") or x.get("curr")),
        "Кол-во": norm_str(x.get("quantity") or x.get("qty") or x.get("qnt") or x.get("availability")),
        "Доставка(дн)": norm_str(x.get("deliveryDays") or x.get("delivery_days") or x.get("days") or x.get("deliveryPeriod")),
        "RAW": str(x)[:32000],
    }


async def process_one_excel(
    in_path: str,
    out_dir: str,
    base_url: str,
    userlogin: str,
    userpsw_md5: str,
    concurrency: int,
    timeout_s: int,
) -> str:
    df = pd.read_excel(in_path, dtype=str)
    if df.empty:
        return ""

    article_col = detect_col(df, ["артик", "article", "part", "номер", "number"])
    brand_col = detect_col(df, ["бренд", "brand", "марка"])

    if article_col is None:
        article_col = df.columns[0]

    jobs: List[Tuple[int, str, str]] = []
    for i, row in df.iterrows():
        art = norm_str(row.get(article_col))
        if not art:
            continue
        br = norm_str(row.get(brand_col)) if brand_col else ""
        jobs.append((i, art, br))

    if not jobs:
        return ""

    sem = asyncio.Semaphore(max(1, concurrency))
    cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    async with aiohttp.ClientSession() as session:
        distributor_map = await fetch_distributor_map(session, base_url, userlogin, userpsw_md5, timeout_s)
        disp_by_id = build_display_map(distributor_map)

        async def _one(i: int, art: str, br: str) -> Tuple[int, str, str, List[Dict[str, Any]]]:
            key = (art, br)
            if key in cache:
                return i, art, br, cache[key]
            async with sem:
                offers = await fetch_offers(
                    session=session,
                    base_url=base_url,
                    userlogin=userlogin,
                    userpsw_md5=userpsw_md5,
                    number=art,
                    brand=br,
                    timeout_s=timeout_s,
                )
            cache[key] = offers
            return i, art, br, offers

        tasks = [asyncio.create_task(_one(i, art, br)) for i, art, br in jobs]

        results: List[Tuple[int, str, str, List[Dict[str, Any]]]] = []
        for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=os.path.basename(in_path)):
            results.append(await coro)

    stores = set()
    matrix: Dict[int, Dict[str, float]] = {}
    offers_flat: List[Dict[str, Any]] = []

    for row_idx, art, br, offers in results:
        if not offers:
            continue

        for x in offers:
            stores.add(store_display(x, disp_by_id))
            offers_flat.append(offer_row(art, br, x, disp_by_id))

        matrix[row_idx] = best_price_per_store(offers, disp_by_id)

    stores_sorted = sorted(stores)

    out_df = df.copy()

    # ВАЖНО (pandas 3.0): колонки под цены должны быть числовыми
    for s in stores_sorted:
        if s not in out_df.columns:
            out_df[s] = pd.Series([pd.NA] * len(out_df), dtype="Float64")

    for row_idx, sp in matrix.items():
        for store, price in sp.items():
            out_df.at[row_idx, store] = float(price)

    offers_df = pd.DataFrame(offers_flat)
    suppliers_df = pd.DataFrame(
        [{"ПоставщикId": k, "Поставщик": v} for k, v in sorted(disp_by_id.items(), key=lambda t: (t[1], t[0]))]
    )

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(in_path))[0]
    out_path = os.path.join(out_dir, f"{base}_priced.xlsx")

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name="Матрица", index=False)
        offers_df.to_excel(writer, sheet_name="Предложения", index=False)
        suppliers_df.to_excel(writer, sheet_name="Поставщики", index=False)

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Поиск цен по артикулам через ABCP Public API и запись в Excel")
    parser.add_argument("--input", default="input", help="Папка с входными .xlsx")
    parser.add_argument("--output", default="output", help="Папка для результатов")
    parser.add_argument("--concurrency", type=int, default=6, help="Параллельность запросов")
    parser.add_argument("--timeout", type=int, default=30, help="Таймаут запроса (сек)")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(base_dir, ".env"))

    if not os.path.isabs(args.input):
        args.input = os.path.join(base_dir, args.input)
    if not os.path.isabs(args.output):
        args.output = os.path.join(base_dir, args.output)

    base_url = os.getenv("ABCP_BASE_URL", "").strip()
    userlogin = os.getenv("ABCP_LOGIN", "").strip()
    userpsw_md5 = os.getenv("ABCP_PASSWORD_MD5", "").strip()

    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    if not base_url or not userlogin or not userpsw_md5:
        raise SystemExit("Нет ABCP_BASE_URL / ABCP_LOGIN / ABCP_PASSWORD_MD5 в .env")

    files = sorted(glob.glob(os.path.join(args.input, "*.xlsx")))
    if not files:
        raise SystemExit(f"В папке '{args.input}' нет .xlsx файлов")

    async def runner():
        for f in files:
            out = await process_one_excel(
                in_path=f,
                out_dir=args.output,
                base_url=base_url,
                userlogin=userlogin,
                userpsw_md5=userpsw_md5,
                concurrency=max(1, args.concurrency),
                timeout_s=max(5, args.timeout),
            )
            if out:
                print("OK:", out)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
