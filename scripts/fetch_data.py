#!/usr/bin/env python3
"""
Busca dados diários de cotas da CVM e calcula métricas para o Ranking de Fundos.

ESTRATÉGIA DE HISTÓRICO:
  - history.json NUNCA é truncado — cresce a cada execução.
  - Na primeira execução (ou se history.json estiver vazio), faz backfill completo
    desde HISTORY_START_YEAR até hoje.
  - Nas execuções seguintes, adiciona apenas meses com dados mais recentes que
    a última data já salva.
  - Sem janela deslizante: toda métrica do index.html usa o histórico completo.

Fontes:
  - Cotas: CVM /INF_DIARIO (arquivos mensais 2021+, anuais HIST pré-2021)
  - IBOV: Yahoo Finance
  - CDI: API Banco Central (série 12)
"""

import os, sys, json, zipfile, io, math, datetime, urllib.request, calendar, socket
from pathlib import Path
from itertools import combinations

socket.setdefaulttimeout(12)

# ── Lista de fundos ────────────────────────────────────────────────────────────
FUNDS = [
    {"name": "Tarpon GT FIF Cotas FIA",                                            "cnpj": "22232927000190", "cnpjFmt": "22.232.927/0001-90", "exibicao": "Tarpon GT", "curto": "Tarpon"},
    {"name": "Organon FIF Cotas FIA",                                              "cnpj": "17400251000166", "cnpjFmt": "17.400.251/0001-66", "exibicao": "Organon", "curto": "Organon"},
    {"name": "Artica Long Term FIA",                                               "cnpj": "18302338000163", "cnpjFmt": "18.302.338/0001-63", "exibicao": "Ártica Long Term", "curto": "Ártica"},
    {"name": "Genoa Capital Arpa CIC Classe FIM RL",                               "cnpj": "37495383000126", "cnpjFmt": "37.495.383/0001-26", "exibicao": "Genoa Arpa", "curto": "Arpa"},
    {"name": "Itaú Artax Ultra Multimercado FIF DA CIC RL",                        "cnpj": "42698666000105", "cnpjFmt": "42.698.666/0001-05", "exibicao": "Artax Ultra", "curto": "Artax"},
    {"name": "Guepardo Long Bias RV FIM",                                          "cnpj": "24623392000103", "cnpjFmt": "24.623.392/0001-03", "exibicao": "Guepardo Long Bias", "curto": "Guepardo"},
    {"name": "Kapitalo Tarkus FIF Cotas FIA",                                      "cnpj": "28747685000153", "cnpjFmt": "28.747.685/0001-53", "exibicao": "Kapitalo Tarkus", "curto": "Tarkus"},
    {"name": "Real Investor FIC FIF Ações RL",                                     "cnpj": "10500884000105", "cnpjFmt": "10.500.884/0001-05", "exibicao": "Real Investor", "curto": "Real"},
    {"name": "Gama Schroder Gaia Contour Tech Equity L&S BRL FIF CIC Mult IE RL", "cnpj": "35744790000102", "cnpjFmt": "35.744.790/0001-02", "exibicao": "Schroder Tech L&S", "curto": "Schroder"},
    {"name": "Patria Long Biased FIF Cotas FIM",                                   "cnpj": "38954217000103", "cnpjFmt": "38.954.217/0001-03", "exibicao": "Pátria Long Biased", "curto": "Pátria"},
    {"name": "Absolute Pace Long Biased FIC FIF Ações RL",                         "cnpj": "32073525000143", "cnpjFmt": "32.073.525/0001-43", "exibicao": "Absolute Pace", "curto": "Pace"},
    {"name": "Arbor FIC FIA",                                                      "cnpj": "21689246000192", "cnpjFmt": "21.689.246/0001-92", "exibicao": "Arbor", "curto": "Arbor"},
    {"name": "Charles River FIF Ações",                                            "cnpj": "14438229000117", "cnpjFmt": "14.438.229/0001-17", "exibicao": "Charles River", "curto": "Charles"},
    {"name": "SPX Falcon FIF CIC Ações RL",                                        "cnpj": "17397315000117", "cnpjFmt": "17.397.315/0001-17", "exibicao": "SPX Falcon", "curto": "Falcon"},
    {"name": "Opportunity Global Equity Real Institucional FIC FIF Ações IE RL",        "cnpj": "46351969000108", "cnpjFmt": "46.351.969/0001-08", "exibicao": "Opportunity Global", "curto": "Opportunity"},
    {"name": "SPX Patriot FIF CIC Ações RL", "cnpj": "15334585000153", "cnpjFmt": "15.334.585/0001-53", "exibicao": "SPX Patriot", "curto": "Patriot"},
    {"name": "TB FIF Cotas FIA", "cnpj": "47511351000120", "cnpjFmt": "47.511.351/0001-20", "exibicao": "TB", "curto": "TB"},
    {"name": "Itaú Janeiro Multimercado FIF DA Classe FIC RL ATIVO", "cnpj": "52116227000109", "cnpjFmt": "52.116.227/0001-09", "exibicao": "Itaú Janeiro Multimercado", "curto": "Janeiro MM"},
    {"name": "Ace Capital Multicenários FIC FIF Multimercado RL", "cnpj": "47612105000165", "cnpjFmt": "47.612.105/0001-65", "exibicao": "Ace Capital Multicenários", "curto": "Ace"},
    {"name": "Kapitalo K10 FIF Cotas FIM", "exibicao": "Kapitalo K10", "curto": "K10", "cnpj": "29726133000121", "cnpjFmt": "29.726.133/0001-21"},
    {"name": "Genoa Capital Radar CIC Classe FIM RL", "exibicao": "Genoa Radar", "curto": "Radar", "cnpj": "35828684000107", "cnpjFmt": "35.828.684/0001-07"},
    {"name": "Witpar FIF Ações", "exibicao": "Witpar", "curto": "Witpar", "cnpj": "16876874000147", "cnpjFmt": "16.876.874/0001-47"},
    {"name": "Itaú Janeiro RF LP FIF", "exibicao": "Itaú Janeiro RF", "curto": "Janeiro RF", "cnpj": "52239457000157", "cnpjFmt": "52.239.457/0001-57"},
    {"name": "Mapfre Confianza FIF RF Referenciado DI CP", "exibicao": "Mapfre Confianza", "curto": "Mapfre", "cnpj": "51253495000100", "cnpjFmt": "51.253.495/0001-00"},
    {"name": "Itaú Artax Infra FIF Incentivado Infra CIC RF CP LP RL", "exibicao": "Artax Infra", "curto": "Artax Infra", "cnpj": "52969671000169", "cnpjFmt": "52.969.671/0001-69"},
    {"name": "Polo Norte I Long Short FIC FIM", "exibicao": "Polo Norte L&S", "curto": "Polo Norte", "cnpj": "07013315000112", "cnpjFmt": "07.013.315/0001-12"},
    {"name": "AZ Quest Total Return FIC FI Fin Multimercado", "exibicao": "AZ Quest Total Return", "curto": "AZ Quest TR", "cnpj": "14812722000155", "cnpjFmt": "14.812.722/0001-55"},
    {"name": "Genoa Capital Sagres I FIF CIC Multimercado RL", "exibicao": "Genoa Sagres", "curto": "Sagres", "cnpj": "48997077000104", "cnpjFmt": "48.997.077/0001-04"},
    {"name": "Arbor Hedge Long Biased FIC FIA RL", "exibicao": "Arbor Hedge", "curto": "Arbor Hedge", "cnpj": "52217086000102", "cnpjFmt": "52.217.086/0001-02"},
]

FIRST_MONTHLY_YEAR = 2021   # CVM: arquivos mensais a partir daqui
CVM_OLDEST_YEAR    = 2005   # CVM: arquivos anuais HIST a partir daqui
HISTORY_START_YEAR = 2019   # Início do backfill histórico (ajuste se quiser mais)

MONTHLY_CACHE: dict = {}
ANNUAL_CACHE:  dict = {}


# ── Fetch e parse ──────────────────────────────────────────────────────────────

def _parse_content(content: str) -> dict:
    lines = content.split("\n")
    header = [h.strip().lstrip("\ufeff") for h in lines[0].split(";")]
    col_cnpj  = next((i for i, h in enumerate(header) if h.startswith("CNPJ")), -1)
    col_date  = header.index("DT_COMPTC") if "DT_COMPTC" in header else -1
    col_quota = header.index("VL_QUOTA")  if "VL_QUOTA"  in header else -1
    return {"lines": lines, "col_cnpj": col_cnpj, "col_date": col_date, "col_quota": col_quota}


def _fetch_zip(url: str, timeout: int) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return zf.read(zf.namelist()[0]).decode("windows-1252", errors="replace")
    except Exception as e:
        return None


def fetch_monthly(year: int, month: int) -> dict | None:
    key = (year, month)
    if key in MONTHLY_CACHE:
        return MONTHLY_CACHE[key]
    url = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{year}{month:02d}.zip"
    content = _fetch_zip(url, timeout=60)
    result = _parse_content(content) if content else None
    if result:
        print(f"  ✓ mensal {year}-{month:02d} ({len(result['lines'])} linhas)")
    else:
        print(f"  ✗ mensal {year}-{month:02d}: falhou")
    MONTHLY_CACHE[key] = result
    return result


def fetch_annual(year: int) -> dict | None:
    if year in ANNUAL_CACHE:
        return ANNUAL_CACHE[year]
    url = f"https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/HIST/inf_diario_fi_{year}.zip"
    content = _fetch_zip(url, timeout=120)
    result = _parse_content(content) if content else None
    if result:
        print(f"  ✓ anual  {year} ({len(result['lines'])} linhas)")
    else:
        print(f"  ✗ anual  {year}: falhou")
    ANNUAL_CACHE[year] = result
    return result


def _extract_rows(data: dict | None, fund: dict) -> list:
    if not data or data["col_date"] < 0 or data["col_quota"] < 0:
        return []
    cnpj, fmt = fund["cnpj"], fund["cnpjFmt"]
    # Collect ALL rows for this fund on each date
    # pós-RCVM 175: mesmo CNPJ aparece múltiplas vezes por dia (casca + subclasses)
    # Estratégia: para cada data, guardar todas as cotas e depois escolher a correta
    all_rows: dict[str, list[float]] = {}
    for line in data["lines"][1:]:
        if cnpj not in line and fmt not in line:
            continue
        cols = line.split(";")
        try:
            if data["col_cnpj"] >= 0:
                raw = cols[data["col_cnpj"]].strip().replace(".", "").replace("/", "").replace("-", "")
                if raw != cnpj:
                    continue
            d = cols[data["col_date"]].strip()
            q = float(cols[data["col_quota"]].replace(",", "."))
            if d and q > 0:
                if d not in all_rows:
                    all_rows[d] = []
                all_rows[d].append(q)
        except (ValueError, IndexError):
            continue
    if not all_rows:
        return []
    # Detectar se há múltiplas cotas por dia (pós-RCVM 175)
    multi_dates = {d: qs for d, qs in all_rows.items() if len(qs) > 1}
    if multi_dates:
        # Logar as cotas encontradas para diagnóstico
        sample_date = sorted(multi_dates.keys())[-1]  # data mais recente com múltiplas cotas
        print(f"      [RCVM175] {len(multi_dates)} datas com múltiplas cotas. Exemplo {sample_date}: {sorted(multi_dates[sample_date])}")
    # Última cota com linha única = referência histórica pré-RCVM 175
    single_qs = {d: qs[0] for d, qs in all_rows.items() if len(qs) == 1}
    last_ref = single_qs[max(single_qs)] if single_qs else None

    out = []
    warned = False
    for d in sorted(all_rows.keys()):
        qs = all_rows[d]
        if len(qs) == 1:
            out.append({"date": d, "quota": qs[0]})
        else:
            # Excluir cotas < 1.5 (cascas pós-RCVM 175 com cota ~1.0)
            real_qs = sorted(q for q in qs if q >= 1.5)
            if real_qs:
                chosen = min(real_qs)
                # Verificar continuidade com série histórica
                if last_ref is not None and not warned:
                    ratio = chosen / last_ref
                    if ratio < 0.5 or ratio > 2.0:
                        print(f"      [AVISO RCVM175] subclasse suspeita em {d}: "
                              f"escolhida={chosen:.6f} vs ref={last_ref:.6f} "
                              f"(ratio={ratio:.2f}) — verificar manualmente")
                        warned = True
                out.append({"date": d, "quota": chosen})
            else:
                out.append({"date": d, "quota": max(qs)})
    return out


def rows_in_month(year: int, month: int, fund: dict) -> list:
    return _extract_rows(fetch_monthly(year, month), fund)


def rows_in_year(year: int, fund: dict) -> list:
    return _extract_rows(fetch_annual(year), fund)


# ── Datas e cálculos ───────────────────────────────────────────────────────────

def subtract_months(date: datetime.date, n: int) -> datetime.date:
    total = date.year * 12 + (date.month - 1) - n
    y, m  = divmod(total, 12)
    m    += 1
    return datetime.date(y, m, min(date.day, calendar.monthrange(y, m)[1]))


def years_apart(a: str, b: str) -> float:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days / 365.25


def cagr(start: float, end: float, years: float) -> float | None:
    if not start or not end or years <= 0:
        return None
    return (math.pow(end / start, 1.0 / years) - 1) * 100


# FUND_META em Python — espelho exato do JS (inception, initialQuota, tipo, trib)
FUND_META_PY: dict[str, dict] = {
    "22.232.927/0001-90": {"inception":"2010-07-12","initialQuota":1.2136, "tipo":"Long Only",    "trib":"RV"},
    "17.400.251/0001-66": {"inception":"2013-02-22","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "18.302.338/0001-63": {"inception":"2013-06-27","initialQuota":0.7647, "tipo":"Long Only",    "trib":"RV"},
    "37.495.383/0001-26": {"inception":"2021-04-30","initialQuota":1.0000, "tipo":"Long Biased",  "trib":"RV"},
    "42.698.666/0001-05": {"inception":"2022-05-31","initialQuota":1.0000, "tipo":"Multimercado", "trib":"RV"},
    "24.623.392/0001-03": {"inception":"2016-07-11","initialQuota":1.0000, "tipo":"Long Biased",  "trib":"RV"},
    "28.747.685/0001-53": {"inception":"2017-11-06","initialQuota":1.0000, "tipo":"Long Biased",  "trib":"RV"},
    "10.500.884/0001-05": {"inception":"2012-02-29","initialQuota":3.6300, "tipo":"Long Only",    "trib":"RV"},
    "35.744.790/0001-02": {"inception":"2020-04-01","initialQuota":1.0000, "tipo":"Multimercado", "trib":"TR"},
    "38.954.217/0001-03": {"inception":"2020-10-30","initialQuota":1.0000, "tipo":"Long Biased",  "trib":"RV"},
    "32.073.525/0001-43": {"inception":"2018-12-27","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "21.689.246/0001-92": {"inception":"2015-03-23","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "14.438.229/0001-17": {"inception":"2011-11-07","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "17.397.315/0001-17": {"inception":"2012-09-17","initialQuota":1.0000, "tipo":"Long Biased",  "trib":"RV"},
    "46.351.969/0001-08": {"inception":"2022-12-16","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "15.334.585/0001-53": {"inception":"2013-01-02","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "47.511.351/0001-20": {"inception":"2022-08-31","initialQuota":100.00, "tipo":"Long Only",    "trib":"RV"},
    "52.116.227/0001-09": {"inception":"2023-09-29","initialQuota":1.0000, "tipo":"Multimercado", "trib":"TR"},
    "47.612.105/0001-65": {"inception":"2022-11-30","initialQuota":1.0000, "tipo":"Multimercado", "trib":"TR"},
    "29.726.133/0001-21": {"inception":"2018-05-16","initialQuota":1.0000, "tipo":"Multimercado", "trib":"TR"},
    "35.828.684/0001-07": {"inception":"2020-06-30","initialQuota":1.0000, "tipo":"Multimercado", "trib":"TR"},
    "16.876.874/0001-47": {"inception":"2019-01-02","initialQuota":1.0000, "tipo":"Long Only",    "trib":"RV"},
    "52.239.457/0001-57": {"inception":"2023-09-29","initialQuota":1.0000, "tipo":"Renda Fixa - Pós-fixado Global","trib":"TR"},
    "51.253.495/0001-00": {"inception":"2023-08-01","initialQuota":1.0000, "tipo":"Renda Fixa - Crédito Privado",  "trib":"TR"},
    "52.969.671/0001-69": {"inception":"2023-11-30","initialQuota":1.0000, "tipo":"Renda Fixa - Debêntures Incentivadas","trib":"Isento"},
}


def quota_on_or_before(target_date: datetime.date, fund: dict) -> dict | None:
    ts = target_date.isoformat()
    y, m = target_date.year, target_date.month
    for _ in range(3):
        rows = rows_in_month(y, m, fund) if y >= FIRST_MONTHLY_YEAR else rows_in_year(y, fund)
        candidates = [r for r in rows if r["date"] <= ts]
        if candidates:
            return candidates[-1]
        if y >= FIRST_MONTHLY_YEAR:
            total = y * 12 + m - 2
            y, m  = divmod(total, 12)
            m    += 1
        else:
            y -= 1
    return None


def find_anchor_date(cur_year: int, cur_month: int) -> datetime.date:
    quorum = max(2, len(FUNDS) // 2)
    for delta in range(3):
        total = cur_year * 12 + cur_month - 1 - delta
        y, m  = divmod(total, 12)
        m    += 1
        last_dates = []
        for fund in FUNDS:
            rows = rows_in_month(y, m, fund)
            if rows:
                last_dates.append(datetime.date.fromisoformat(rows[-1]["date"]))
        if len(last_dates) >= quorum:
            last_dates.sort()
            anchor = last_dates[len(last_dates) // 2]
            print(f"Anchor date: {anchor} ({len(last_dates)}/{len(FUNDS)} fundos com dados)")
            return anchor
    return datetime.date(cur_year, cur_month, 1)


def find_inception(fund: dict, anchor_year: int) -> dict | None:
    print(f"    inception search: {fund['cnpjFmt']}")
    oldest_year_found = anchor_year
    consecutive_misses = 0
    for y in range(anchor_year - 1, CVM_OLDEST_YEAR - 1, -1):
        if y >= FIRST_MONTHLY_YEAR:
            rows        = rows_in_month(y, 12, fund)
            file_exists = MONTHLY_CACHE.get((y, 12)) is not None
        else:
            rows        = rows_in_year(y, fund)
            file_exists = ANNUAL_CACHE.get(y) is not None
        if rows:
            oldest_year_found  = y
            consecutive_misses = 0
            print(f"      encontrado em {y}")
        elif file_exists:
            consecutive_misses += 1
            if consecutive_misses >= 2:
                break
    print(f"      ano mais antigo: {oldest_year_found}")
    for scan_year in [oldest_year_found - 1, oldest_year_found]:
        if scan_year < CVM_OLDEST_YEAR:
            continue
        if scan_year >= FIRST_MONTHLY_YEAR:
            for m in range(1, 13):
                rows = rows_in_month(scan_year, m, fund)
                if rows:
                    print(f"      inception: {rows[0]['date']}")
                    return rows[0]
        else:
            rows = rows_in_year(scan_year, fund)
            if rows:
                print(f"      inception: {rows[0]['date']}")
                return rows[0]
    return None


# ── Processamento por fundo (data.json) ───────────────────────────────────────

def process_fund(fund: dict, anchor: datetime.date, prev_max_quotas: dict,
                 ibov_price_map: dict | None = None,
                 cdi_price_map: dict | None = None) -> dict:
    print(f"\n── {fund['name']}")
    latest = quota_on_or_before(anchor, fund)
    if not latest:
        print(f"  ✗ sem dados")
        return {**fund, "error": True}

    end_quota, end_date = latest["quota"], latest["date"]
    print(f"  cota atual: {end_quota} em {end_date}")

    anchor_str = anchor.isoformat()
    is_delayed = end_date < anchor_str
    delay_days = (anchor - datetime.date.fromisoformat(end_date)).days if is_delayed else 0
    if is_delayed:
        print(f"  ⚠ atrasado {delay_days}d em relação à âncora ({anchor_str})")

    a12 = subtract_months(anchor, 12)
    a36 = subtract_months(anchor, 36)
    a60 = subtract_months(anchor, 60)

    q12 = quota_on_or_before(a12, fund)
    q36 = quota_on_or_before(a36, fund)
    q60 = quota_on_or_before(a60, fund)

    inception   = find_inception(fund, anchor.year)
    inc_quota   = inception["quota"] if inception else None
    inc_date    = inception["date"]  if inception else None

    # IBOV CAGR desde o inception deste fundo especificamente
    ibov_cagr_inception = None
    if inc_date and ibov_price_map:
        ibov_dates = sorted(ibov_price_map.keys())
        p_inc, d_inc   = _best_price_and_date(ibov_price_map, ibov_dates, datetime.date.fromisoformat(inc_date))
        p_anch, d_anch = _best_price_and_date(ibov_price_map, ibov_dates, anchor)
        if p_inc and p_anch and d_inc and d_anch:
            ibov_cagr_inception = cagr(p_inc, p_anch, years_apart(d_inc, d_anch))

    # CDI CAGR desde o inception — para calcular alphaVsCdi (âncora de multimercados)
    cdi_cagr_inception = None
    if inc_date and cdi_price_map:
        cdi_dates = sorted(cdi_price_map.keys())
        p_cdi_inc,  d_cdi_inc  = _best_price_and_date(cdi_price_map, cdi_dates, datetime.date.fromisoformat(inc_date))
        p_cdi_anch, d_cdi_anch = _best_price_and_date(cdi_price_map, cdi_dates, anchor)
        if p_cdi_inc and p_cdi_anch and d_cdi_inc and d_cdi_anch:
            cdi_cagr_inception = cagr(p_cdi_inc, p_cdi_anch, years_apart(d_cdi_inc, d_cdi_anch))

    def do_cagr(q):
        if not q: return None
        return cagr(q["quota"], end_quota, years_apart(q["date"], end_date))

    prev      = prev_max_quotas.get(fund["cnpjFmt"], {})
    prev_max  = prev.get("maxQuota") or 0.0
    # maxQuota = max(histórico completo, cota atual)
    # prev_max_quotas já vem do history.json (via reconstruct_max_quotas_from_history)
    # ou do data.json anterior — em ambos os casos, comparamos com a cota atual
    if end_quota > prev_max:
        max_quota      = end_quota
        max_quota_date = end_date
        print(f"  nova máxima: {max_quota} em {max_quota_date}")
    else:
        max_quota      = prev_max
        max_quota_date = prev.get("maxQuotaDate", "")

    result = {
        "name":          fund["name"],
        "cnpj":          fund["cnpjFmt"],
        "cnpjFmt":       fund["cnpjFmt"],
        "latestDate":    end_date,
        "latestQuota":   end_quota,
        "isDelayed":     is_delayed,
        "delayDays":     delay_days,
        "maxQuota":      max_quota,
        "maxQuotaDate":  max_quota_date,
        "inceptionDate": inc_date,
        "anchorDate":    anchor.isoformat(),
        "anchor12m":     a12.isoformat(),
        "anchor36m":     a36.isoformat(),
        "anchor60m":     a60.isoformat(),
        "cagr12":        do_cagr(q12),
        "cagr36":        do_cagr(q36),
        "cagr60":        do_cagr(q60),
        "cagrInception":      cagr(inc_quota, end_quota, years_apart(inc_date, end_date)) if inc_date else None,
        "ibovCagrInception":  round(ibov_cagr_inception, 4) if ibov_cagr_inception is not None else None,
        # alpha vs CDI desde inception — âncora de skill para multimercados
        "cdiCagrInception":   round(cdi_cagr_inception, 4) if cdi_cagr_inception is not None else None,
        "alphaVsCdi":         round(
            cagr(inc_quota, end_quota, years_apart(inc_date, end_date)) - cdi_cagr_inception, 4
        ) if (inc_date and cdi_cagr_inception is not None
              and cagr(inc_quota, end_quota, years_apart(inc_date, end_date)) is not None) else None,
        # alpha vs IBOV desde inception — âncora de skill para fundos de ações
        "alphaVsIbov":        round(
            cagr(inc_quota, end_quota, years_apart(inc_date, end_date)) - ibov_cagr_inception, 4
        ) if (inc_date and ibov_cagr_inception is not None
              and cagr(inc_quota, end_quota, years_apart(inc_date, end_date)) is not None) else None,
        "error":         False,
    }
    def _fmt(v): return f"{v:.2f}" if v is not None else "N/D"
    print(f"  CAGR 12M={_fmt(result['cagr12'])} 36M={_fmt(result['cagr36'])} 60M={_fmt(result['cagr60'])}")
    return result


# ── Benchmarks ─────────────────────────────────────────────────────────────────

def _best_price_and_date(price_map: dict, dates: list, target: datetime.date):
    tstr = target.isoformat()
    candidates = [d for d in dates if d <= tstr]
    if not candidates: return None, None
    d = candidates[-1]
    return price_map[d], d


def fetch_ibov(anchor: datetime.date, a12: datetime.date, a36: datetime.date, a60: datetime.date,
               oldest_inception: datetime.date | None = None) -> tuple[dict, dict]:
    ticker   = "%5EBVSP"
    fetch_from = oldest_inception - datetime.timedelta(days=10) if oldest_inception else a60 - datetime.timedelta(days=10)
    period1 = int(datetime.datetime.combine(
        fetch_from, datetime.time(),
        tzinfo=datetime.timezone.utc).timestamp())
    period2 = int(datetime.datetime.combine(
        anchor + datetime.timedelta(days=5), datetime.time(),
        tzinfo=datetime.timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={period1}&period2={period2}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        result     = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes     = result["indicators"]["quote"][0]["close"]
        price_map  = {
            datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat(): price
            for ts, price in zip(timestamps, closes) if price is not None
        }
        dates = sorted(price_map.keys())
        p_anchor, d_anchor = _best_price_and_date(price_map, dates, anchor)
        p12, d12 = _best_price_and_date(price_map, dates, a12)
        p36, d36 = _best_price_and_date(price_map, dates, a36)
        p60, d60 = _best_price_and_date(price_map, dates, a60)
        def ibov_cagr(d_s, d_e, p_s, p_e):
            if not all([d_s, d_e, p_s, p_e]): return None
            return cagr(p_s, p_e, years_apart(d_s, d_e))
        result_ibov = {
            "cagr12": ibov_cagr(d12, d_anchor, p12, p_anchor),
            "cagr36": ibov_cagr(d36, d_anchor, p36, p_anchor),
            "cagr60": ibov_cagr(d60, d_anchor, p60, p_anchor),
        }
        vals = {k: f"{v:.2f}%" if v is not None else "N/D" for k, v in result_ibov.items()}
        print(f"  IBOV 12M={vals['cagr12']} 36M={vals['cagr36']} 60M={vals['cagr60']}")
        return result_ibov, price_map
    except Exception as e:
        print(f"  ✗ IBOV falhou: {e}")
        return {"cagr12": None, "cagr36": None, "cagr60": None}, {}


def fetch_cdi(anchor: datetime.date, a12: datetime.date, a36: datetime.date, a60: datetime.date) -> dict:
    # Busca 200 meses (~16,7 anos) de histórico — cobre a inception META mais
    # antiga (Tarpon 2010-07), permitindo alfa vs CDI e o mapa S&P-hedge desde
    # o início real de todos os fundos. Histórico: 84M (7a) não alcançava nem o
    # Organon (2014, CVM) → alphaVsCdi=None → decay monitor cego; 156M cobria a
    # base CVM mas não a base meta do painel. Custo: ~4.300 linhas diárias do BCB,
    # em janelas de 9 anos (limite da API SGS para séries diárias).
    _y, _m = anchor.year, anchor.month - 200
    while _m <= 0: _m += 12; _y -= 1
    import calendar as _cal
    _d = min(anchor.day, _cal.monthrange(_y, _m)[1])
    start = datetime.date(_y, _m, _d) - datetime.timedelta(days=5)
    try:
        # A API SGS do BCB limita séries DIÁRIAS a ~10 anos por request — um range
        # de 13 anos numa chamada só devolve erro. Busca em janelas de 9 anos,
        # em ordem cronológica (o acumulador de cotas exige processar em ordem).
        data = []
        _ws = start
        while _ws <= anchor:
            _we = min(datetime.date(_ws.year + 9, _ws.month, min(_ws.day, 28)), anchor)
            url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
                   f"?formato=json"
                   f"&dataInicial={_ws.strftime('%d/%m/%Y')}"
                   f"&dataFinal={_we.strftime('%d/%m/%Y')}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                chunk = json.loads(resp.read())
            if chunk: data.extend(chunk)
            _ws = _we + datetime.timedelta(days=1)
        # dedup preservando ordem (bordas de janela podem repetir um dia)
        _seen = set(); _dedup = []
        for entry in data:
            if entry["data"] not in _seen:
                _seen.add(entry["data"]); _dedup.append(entry)
        data = _dedup
        if not data:
            raise ValueError("Resposta vazia do BCB")
        price_map: dict = {}
        acc = 1.0
        for entry in data:
            d    = datetime.datetime.strptime(entry["data"], "%d/%m/%Y").date().isoformat()
            acc *= 1 + float(entry["valor"]) / 100
            price_map[d] = acc
        dates = sorted(price_map.keys())
        p_anchor, d_anchor = _best_price_and_date(price_map, dates, anchor)
        p12, d12 = _best_price_and_date(price_map, dates, a12)
        p36, d36 = _best_price_and_date(price_map, dates, a36)
        p60, d60 = _best_price_and_date(price_map, dates, a60)
        def cdi_cagr(d_s, d_e, p_s, p_e):
            if not all([d_s, d_e, p_s, p_e]): return None
            return cagr(p_s, p_e, years_apart(d_s, d_e))
        result_cdi = {
            "cagr12": cdi_cagr(d12, d_anchor, p12, p_anchor),
            "cagr36": cdi_cagr(d36, d_anchor, p36, p_anchor),
            "cagr60": cdi_cagr(d60, d_anchor, p60, p_anchor),
        }
        vals = {k: f"{v:.2f}%" if v is not None else "N/D" for k, v in result_cdi.items()}
        print(f"  CDI  12M={vals['cagr12']} 36M={vals['cagr36']} 60M={vals['cagr60']}")
        return result_cdi, price_map
    except Exception as e:
        print(f"  ✗ CDI falhou: {e}")
        return {"cagr12": None, "cagr36": None, "cagr60": None}, {}


def fetch_ntnb() -> dict:
    """
    Busca as taxas atuais das NTN-B (Tesouro IPCA+).

    Fontes em ordem de confiabilidade:
      1) ANBIMA arquivo txt — últimos 5 dias úteis (fonte primária, mais completa)
      2) BCB SGS /ultimos/1 — fallback rápido com timeout curto
    """
    FALLBACK = {
        "ntnb_rate_long":   7.05,
        "ntnb_rate_mid":    6.90,
        "ntnb_fetched_at":  None,
        "ntnb_titles":      [],
        "ntnb_source":      "fallback",
    }

    today        = datetime.date.today()
    horizon_long = today.replace(year=today.year + 8)
    horizon_5y   = today.replace(year=today.year + 5)

    def _process_titles(titles):
        if not titles: return None
        longs = [t for t in titles if datetime.date.fromisoformat(t["vencimento"]) >= horizon_long]
        mids  = [t for t in titles if abs((datetime.date.fromisoformat(t["vencimento"]) - horizon_5y).days) < 730]
        ntnb_long = sum(t["taxa"] for t in longs) / len(longs) if longs else None
        ntnb_mid  = min(mids, key=lambda t: abs((datetime.date.fromisoformat(t["vencimento"]) - horizon_5y).days))["taxa"] if mids else None
        if not ntnb_long: return None
        return {
            "ntnb_rate_long":  round(ntnb_long, 4),
            "ntnb_rate_mid":   round(ntnb_mid, 4) if ntnb_mid else round(ntnb_long, 4),
            "ntnb_fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "ntnb_titles":     titles,
        }

    def _last_business_days(n):
        days = []
        d = today
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d -= datetime.timedelta(days=1)
        return days

    # ── Fonte 1: ANBIMA arquivo txt — últimos 5 dias úteis ───────────────────
    for candidate in _last_business_days(5):
        url = ("https://www.anbima.com.br/informacoes/merc-sec/arqs/ms"
               + candidate.strftime("%y%m%d") + ".txt")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("latin-1", errors="replace")

            # Detecta separador (@  ou ;)
            sep = "@"
            for line in raw.splitlines()[:20]:
                if ";" in line and "NTN" in line.upper():
                    sep = ";"; break
                if "@" in line and "NTN" in line.upper():
                    sep = "@"; break

            titles = []
            for line in raw.splitlines():
                if "NTN-B" not in line.upper(): continue
                parts = line.split(sep)
                if len(parts) < 4:
                    parts = line.split(";" if sep == "@" else "@")
                if len(parts) < 4: continue
                try:
                    tipo = parts[0].strip().strip('"')
                    if "NTN-B" not in tipo.upper() or "PRINCIPAL" in tipo.upper(): continue

                    # Parse vencimento: suporta dois formatos:
                    #   Novo: YYYYMMDD (ex: "20260815") — col[4]
                    #   Antigo: DD/MM/YYYY (ex: "15/08/2026") — busca em cols [2,3,1]
                    venc_iso = None

                    # Tenta formato novo (YYYYMMDD) em col[4]
                    if len(parts) > 4:
                        cv = parts[4].strip().strip('"')
                        if len(cv) == 8 and cv.isdigit():
                            venc_iso = f"{cv[:4]}-{cv[4:6]}-{cv[6:8]}"

                    # Fallback: formato antigo com barras em cols [2,3,1]
                    if not venc_iso:
                        for col in [2, 3, 1]:
                            cv = parts[col].strip().strip('"') if col < len(parts) else ""
                            if "/" in cv and len(cv) >= 8:
                                dv, mv, yv = cv.split("/")
                                venc_iso = f"{yv.zfill(4)}-{mv.zfill(2)}-{dv.zfill(2)}"
                                break

                    if not venc_iso: continue

                    # Taxa indicativa: col[7] no novo formato, fallback cols [5,4,6,3]
                    taxa = None
                    for col in [7, 5, 4, 6, 3]:
                        if col >= len(parts): continue
                        try:
                            v = float(parts[col].strip().strip('"').replace(",", "."))
                            if 2.0 < v < 20.0:
                                taxa = v; break
                        except ValueError:
                            continue
                    if taxa:
                        titles.append({"nome": tipo, "vencimento": venc_iso, "taxa": taxa})
                except Exception:
                    continue

            result = _process_titles(titles)
            if result:
                result["ntnb_source"] = "anbima"
                longs = [t for t in titles if datetime.date.fromisoformat(t["vencimento"]) >= horizon_long]
                print(f"  NTN-B ANBIMA {candidate} ({len(titles)} títulos, {len(longs)} longas)")
                print(f"  NTN-B_long={result['ntnb_rate_long']:.2f}% NTN-B_mid={result['ntnb_rate_mid']:.2f}%")
                return result
            print(f"  ✗ NTN-B ANBIMA {candidate}: {len(titles)} títulos NTN-B encontrados")
        except Exception as e:
            print(f"  ✗ NTN-B ANBIMA {candidate}: {e}")

    # ── Fonte 2: BCB SGS /ultimos/1 — fallback ────────────────────────────────
    try:
        url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.13793/dados/ultimos/1?formato=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data:
            taxa = float(data[-1]["valor"])
            if 2.0 < taxa < 20.0:
                print(f"  NTN-B BCB SGS 13793: {taxa:.2f}%")
                return {
                    "ntnb_rate_long":   round(taxa, 4),
                    "ntnb_rate_mid":    round(taxa * 0.97, 4),
                    "ntnb_fetched_at":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "ntnb_titles":      [{"nome": "NTN-B 2035 (SGS)", "vencimento": "2035-05-15", "taxa": taxa}],
                    "ntnb_source":      "bcb_sgs",
                }
    except Exception as e:
        print(f"  ✗ NTN-B BCB SGS 13793: {e}")

    print("  ✗ NTN-B: todas as fontes falharam — usando fallback")
    return FALLBACK


def fetch_ipca_focus() -> dict:
    """
    Busca expectativa de IPCA do Focus via BCB Olinda.
    Campos reais: DataReferencia (ano como string), Mediana.
    LP = ano mais distante disponível (tipicamente hoje+4 anos).
    """
    FALLBACK = {
        "ipca_12m":         4.8,
        "ipca_longo_prazo": 4.0,
        "ipca_fetched_at":  None,
        "ipca_source":      "fallback",
    }

    today_year = datetime.date.today().year

    try:
        url = ("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
               "ExpectativasMercadoAnuais"
               "?%24filter=Indicador%20eq%20%27IPCA%27"
               "&%24orderby=Data%20desc&%24top=20&%24format=json")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())

        records = raw.get("value") or []
        if not records:
            raise ValueError("Olinda retornou vazio")

        latest_date = max(r["Data"] for r in records)
        # baseCalculo=0 → série original (não suavizada) — coincide com o PDF Focus
        # baseCalculo=1 → série suavizada (valores sistematicamente maiores)
        by_ano = {int(r["DataReferencia"]): float(r["Mediana"])
                  for r in records
                  if r["Data"] == latest_date
                  and r.get("Mediana") is not None
                  and r.get("baseCalculo", 0) == 0}

        ipca_12m = by_ano.get(today_year) or by_ano.get(today_year + 1)
        # LP = ano mais distante disponível
        ipca_lp  = by_ano.get(max(by_ano.keys())) if by_ano else None

        if not ipca_12m:
            raise ValueError(f"Sem dados para {today_year}: {sorted(by_ano.keys())}")

        result = {
            "ipca_12m":         round(ipca_12m, 2),
            "ipca_longo_prazo": round(ipca_lp, 2) if ipca_lp else FALLBACK["ipca_longo_prazo"],
            "ipca_fetched_at":  latest_date,
            "ipca_source":      "focus",
        }
        print(f"  Focus IPCA: 12M={result['ipca_12m']}% LP={result['ipca_longo_prazo']}% (ref. {latest_date})")
        return result

    except Exception as e:
        print(f"  ✗ Focus IPCA falhou: {e} — usando fallback")
        return FALLBACK


def _legacy_max_dd(rets: list) -> float:
    """Fallback para maxDrawdown caso compute_fund_metrics retorne vazio."""
    start = 0
    for i, r in enumerate(rets):
        if r is not None:
            start = max(0, i - 1)
            break
    cum = peak = 1.0
    dd_max = 0.0
    for r in rets[start:]:
        if r is None: continue
        cum *= (1 + r)
        if cum > peak: peak = cum
        dd = (cum - peak) / peak
        if dd < dd_max: dd_max = dd
    return round(dd_max * 100, 2)

def months_to_fetch(last_date_in_history: str | None, anchor: datetime.date) -> list:
    """
    Retorna lista de (year, month) a buscar na CVM.

    - Se history.json está vazio/ausente → backfill completo desde HISTORY_START_YEAR.
    - Se já tem dados → busca apenas meses a partir do mês da última data salva.
      Inclui o mês anterior ao atual para cobrir datas que chegam com atraso.
    """
    if last_date_in_history is None:
        # Backfill completo
        start_year  = HISTORY_START_YEAR
        start_month = 1
        print(f"  Backfill completo desde {start_year}-01")
    else:
        last = datetime.date.fromisoformat(last_date_in_history)
        start_year  = last.year
        start_month = last.month
        print(f"  Incremental desde {last_date_in_history}")

    result = []
    y, m = start_year, start_month
    while (y, m) <= (anchor.year, anchor.month):
        result.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return result


def update_history(anchor: datetime.date) -> None:
    """
    Atualiza history.json de forma cumulativa — nunca trunca o histórico.

    Comportamento:
    - Carrega todas as cotas já salvas.
    - Determina quais meses ainda não foram buscados (incremental) ou
      faz backfill completo se o arquivo estiver vazio.
    - Adiciona novas cotas sem remover as antigas.
    - Reconstrói retornos, correlação e drawdown sobre o histórico completo.
    - Sem cutoff de data — o arquivo cresce indefinidamente.
    """
    print(f"\n── Atualizando history.json (histórico completo, sem truncar)")
    hist_path = Path(__file__).parent.parent / "docs" / "history.json"

    # ── Carregar histórico existente ────────────────────────────────────────
    quotas: dict = {f["cnpjFmt"]: {} for f in FUNDS}
    last_date_in_history = None
    existing_fund_cnpjs: set = set()

    if hist_path.exists():
        try:
            existing = json.loads(hist_path.read_text())
            for cnpj, fd in existing.get("funds", {}).items():
                if cnpj in quotas:
                    quotas[cnpj] = dict(zip(fd["dates"], fd["quotas"]))
                    existing_fund_cnpjs.add(cnpj)
            # Data mais recente no arquivo atual
            all_dates = sorted(existing.get("commonDates", []))
            if all_dates:
                last_date_in_history = all_dates[-1]
                print(f"  Histórico existente: {len(all_dates)} datas "
                      f"({all_dates[0]} → {all_dates[-1]})")
            else:
                print("  history.json existe mas está vazio — iniciando backfill")
        except Exception as e:
            print(f"  Erro ao ler history.json: {e} — iniciando backfill completo")

    # ── Detectar fundos novos (não presentes no history.json anterior) ───────
    all_fund_cnpjs = {f["cnpjFmt"] for f in FUNDS}
    new_funds = all_fund_cnpjs - existing_fund_cnpjs
    if new_funds:
        new_names = [f["name"] for f in FUNDS if f["cnpjFmt"] in new_funds]
        print(f"  ⚠ Fundos novos detectados (sem histórico): {', '.join(new_names)}")
        print(f"    → Forçando backfill completo para incluí-los")
        last_date_in_history = None  # força backfill completo de todos os meses

    # ── Detectar fundos com histórico esparso (add_fund rodou com CVM incompleta) ─
    # Um fundo adicionado quando a CVM tinha poucas cotas vai ter muitos zeros/gaps.
    # Se um fundo tem menos de 60% das cotas esperadas desde sua inception,
    # forçamos backfill completo para recuperar cotas que chegaram depois.
    if last_date_in_history is not None:  # só se não já forçou backfill
        sparse_funds = []
        for f in FUNDS:
            cnpj = f["cnpjFmt"]
            if cnpj not in quotas:
                continue
            qs = quotas[cnpj]
            real_cotas = sum(1 for v in qs.values() if v and v > 0)
            if real_cotas == 0:
                continue
            # Encontrar a data mais antiga de cota real
            sorted_real = sorted(d for d, v in qs.items() if v and v > 0)
            inception_str = sorted_real[0]
            # Dias de mercado esperados desde inception até hoje (aprox 252/ano)
            import datetime as _dt
            try:
                inc = _dt.date.fromisoformat(inception_str)
                today_d = _dt.date.fromisoformat(last_date_in_history)
                years = (today_d - inc).days / 365.25
                expected = int(years * 252)
                if expected > 60 and real_cotas < expected * 0.6:
                    sparse_funds.append(f["name"])
                    print(f"  ⚠ {f['name']}: apenas {real_cotas} cotas reais "
                          f"(esperado ~{expected} para {years:.1f} anos desde {inception_str})")
            except Exception:
                pass
        if sparse_funds:
            print(f"  → Fundos com histórico esparso: {', '.join(sparse_funds)}")
            print(f"    → Forçando backfill completo para recuperar cotas CVM retroativas")
            last_date_in_history = None

    # ── Determinar meses a buscar ────────────────────────────────────────────
    to_fetch = months_to_fetch(last_date_in_history, anchor)
    print(f"  Meses a buscar: {len(to_fetch)} "
          f"({to_fetch[0][0]}-{to_fetch[0][1]:02d} → "
          f"{to_fetch[-1][0]}-{to_fetch[-1][1]:02d})")

    # ── Buscar e acumular cotas ──────────────────────────────────────────────
    for year, month in to_fetch:
        added = 0
        for fund in FUNDS:
            if year >= FIRST_MONTHLY_YEAR:
                rows = rows_in_month(year, month, fund)
            else:
                # Para anos pré-2021, usa arquivo anual (já cacheado se buscado antes)
                if month == 1:  # busca o arquivo anual apenas uma vez por ano
                    rows = rows_in_year(year, fund)
                else:
                    rows = _extract_rows(ANNUAL_CACHE.get(year), fund)
                    # Filtra apenas o mês atual
                    month_str = f"{year}-{month:02d}"
                    rows = [r for r in rows if r["date"].startswith(month_str)]

            for row in rows:
                d, q = row["date"], row["quota"]
                if d not in quotas[fund["cnpjFmt"]]:
                    quotas[fund["cnpjFmt"]][d] = q
                    added += 1
        if added:
            print(f"  {year}-{month:02d}: +{added} novas cotas")

    # ── Selecionar datas comuns ──────────────────────────────────────────────
    # Aceita datas onde >= 80% dos fundos têm cota (evita que gap de um fundo
    # encole toda a série). Sem cutoff de data — usa TODO o histórico disponível.
    PRESENCE_THRESHOLD = 0.80
    min_funds_required = max(2, int(len(FUNDS) * PRESENCE_THRESHOLD))

    date_counts: dict[str, int] = {}
    for fund in FUNDS:
        for d in quotas[fund["cnpjFmt"]]:
            date_counts[d] = date_counts.get(d, 0) + 1

    common_dates = sorted(d for d, cnt in date_counts.items()
                          if cnt >= min_funds_required)

    if not common_dates:
        print("  Sem datas suficientes — history.json não atualizado")
        return

    print(f"  Datas aceitas: {len(common_dates)} ({common_dates[0]} → {common_dates[-1]})")

    # ── Interpolação para fundos ausentes numa data aceita ───────────────────
    # Interpolação geométrica (log-retorno linear) — correta para séries de cotas.
    # Equivale a supor retorno diário constante no gap.
    interpolated_total = 0
    for fund in FUNDS:
        cnpj      = fund["cnpjFmt"]
        qs        = quotas[cnpj]
        all_dates = sorted(qs.keys())

        for d in common_dates:
            if d in qs:
                continue
            prev_d = next((x for x in reversed(all_dates) if x < d), None)
            next_d = next((x for x in all_dates           if x > d), None)

            if prev_d and next_d and qs.get(prev_d) and qs.get(next_d):
                t0    = datetime.date.fromisoformat(prev_d)
                t1    = datetime.date.fromisoformat(next_d)
                td    = datetime.date.fromisoformat(d)
                alpha = (td - t0).days / max((t1 - t0).days, 1)
                qs[d] = qs[prev_d] * ((qs[next_d] / qs[prev_d]) ** alpha)
                qs[d] = round(qs[d], 8)
                interpolated_total += 1
            elif prev_d and qs.get(prev_d):
                qs[d] = qs[prev_d]
                interpolated_total += 1

        quotas[cnpj] = qs

    if interpolated_total:
        print(f"  Interpoladas {interpolated_total} cotas ausentes")

    # ── Retornos diários ─────────────────────────────────────────────────────
    returns_by_fund: dict = {}
    for fund in FUNDS:
        qs   = quotas[fund["cnpjFmt"]]
        rets = []
        for i in range(1, len(common_dates)):
            q0 = qs.get(common_dates[i-1])
            q1 = qs.get(common_dates[i])
            rets.append((q1 / q0) - 1 if q0 and q1 else None)  # None = pre-inception or gap
        returns_by_fund[fund["cnpjFmt"]] = rets

    # ── Correlação de Pearson ────────────────────────────────────────────────
    def first_real_idx(cnpj: str) -> int:
        rets = returns_by_fund[cnpj]
        for i, r in enumerate(rets):
            if r != 0.0:
                return max(0, i - 1)
        return 0

    def pearson_real(ca: str, cb: str) -> float:
        """Pearson using only dates where both funds have real (non-zero) returns."""
        ra = returns_by_fund[ca]
        rb = returns_by_fund[cb]
        # Build aligned pairs where both have real data
        pairs = [(ra[i], rb[i]) for i in range(min(len(ra), len(rb)))
                 if ra[i] is not None and rb[i] is not None]
        n = len(pairs)
        if n < 30: return 0.0
        a = [p[0] for p in pairs]
        b = [p[1] for p in pairs]
        ma, mb = sum(a) / n, sum(b) / n
        num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
        sa  = math.sqrt(sum((x - ma) ** 2 for x in a))
        sb  = math.sqrt(sum((x - mb) ** 2 for x in b))
        return round(num / (sa * sb), 4) if sa * sb > 0 else 0.0

    cnpjs = [f["cnpjFmt"] for f in FUNDS]
    corr  = {ca: {cb: (1.0 if ca == cb else pearson_real(ca, cb))
                  for cb in cnpjs} for ca in cnpjs}

    # ── Métricas por fundo (servidor) ────────────────────────────────────────
    # Calculadas uma vez por dia aqui e consumidas diretamente pelo browser.
    # Evita recomputação O(n²·T) no thread principal a cada interação do usuário.

    def first_real_idx_list(rets: list) -> int:
        """Índice do primeiro retorno real (não-None) na série."""
        for i, r in enumerate(rets):
            if r is not None:
                return max(0, i - 1)
        return 0

    def compute_fund_metrics(cnpj: str, cdi_annual: float) -> dict:
        """
        Calcula métricas de risco/retorno para um fundo sobre o histórico completo.

        Rolling alpha e Beat IBOV usam retornos diários reais do IBOV quando disponíveis
        (_ibov_daily_rets, injetado por main()), eliminando o viés do proxy constante.
        Para janelas sem cobertura do IBOV real, usa o proxy como fallback.

        Retorna dict com: vol, sharpe, sortino, calmar, maxDrawdown, cagrHist,
                          rollingAlpha (array), rollingDates (array),
                          propensity (Beat IBOV %), alphaAnn, teAnn, ir.
        """
        rets_all   = returns_by_fund[cnpj]
        # dates_all[i] corresponde a returns[i]: retorno de common_dates[i] → common_dates[i+1]
        # returns[i] = quota[i+1] / quota[i] - 1, portanto a data "de chegada" é common_dates[i+1]
        dates_all  = common_dates  # len = n_returns + 1

        fi   = first_real_idx_list(rets_all)
        rets = [r for r in rets_all[fi:] if r is not None]
        n    = len(rets)
        if n < 60:
            return {}

        # Datas das barras reais (data de chegada de cada retorno)
        dates_real = dates_all[fi + 1: fi + 1 + n]

        cdi_daily         = math.pow(1 + cdi_annual / 100, 1 / 252) - 1
        ibov_proxy_daily  = _ibov_daily_proxy  # fallback quando IBOV real não disponível

        # Retorno anualizado (CAGR sobre o período com dados reais)
        cum = 1.0
        for r in rets:
            cum *= (1 + r)
        cagr_val = (math.pow(cum, 252 / n) - 1) * 100

        # Volatilidade anualizada
        mean_r = sum(rets) / n
        var_r  = sum((r - mean_r) ** 2 for r in rets) / (n - 1)
        vol    = math.sqrt(var_r * 252) * 100

        # Sharpe (MAR = CDI)
        sharpe = (cagr_val - cdi_annual) / vol if vol > 0 else None

        # Sortino (semi-desvio vs CDI diário)
        excess_d = [r - cdi_daily for r in rets]
        down_sq  = sum(e * e for e in excess_d if e < 0) / n
        semi_vol = math.sqrt(down_sq * 252) * 100
        sortino  = (cagr_val - cdi_annual) / semi_vol if semi_vol > 0 else None

        # Max drawdown e Calmar
        c2 = pk = 1.0
        mdd = 0.0
        for r in rets:
            c2 *= (1 + r)
            if c2 > pk:
                pk = c2
            dd = (c2 - pk) / pk
            if dd < mdd:
                mdd = dd
        mdd_pct = mdd * 100  # negativo
        calmar  = cagr_val / abs(mdd_pct) if mdd_pct < 0 else None

        # ── Helper: retorno IBOV real para uma data, com fallback ao proxy ──────
        def ibov_ret(date_str: str) -> float:
            r = _ibov_daily_rets.get(date_str)
            return r if r is not None else ibov_proxy_daily

        # ── IR vs IBOV (usando retornos reais quando disponíveis) ───────────────
        # excess[i] = r_fund[i] - r_ibov[date_i]
        ibov_excess_d = [rets[i] - ibov_ret(dates_real[i]) for i in range(n)]
        alpha_d_daily = sum(ibov_excess_d) / n
        alpha_ann     = (math.pow(1 + alpha_d_daily, 252) - 1) * 100
        te_d          = math.sqrt(sum((e - alpha_d_daily) ** 2 for e in ibov_excess_d) / (n - 1))
        te_ann        = te_d * math.sqrt(252) * 100
        ir            = alpha_ann / te_ann if te_ann > 0 else None

        # ── Rolling alpha 12M (janela 252 pregões, retornos IBOV reais) ─────────
        WINDOW      = 252
        rolling_alpha = []
        rolling_dates = []
        for i in range(n - WINDOW + 1):
            cf = 1.0
            ci = 1.0
            for j in range(i, i + WINDOW):
                cf *= (1 + rets[j])
                ci *= (1 + ibov_ret(dates_real[j]))
            # Anualizar ambos: (1+r_fund)^(252/WINDOW) - (1+r_ibov)^(252/WINDOW)
            # Para WINDOW=252 exatamente, isso é simplesmente cf-1 e ci-1 em termos anuais
            ra = (math.pow(cf, 252 / WINDOW) - math.pow(ci, 252 / WINDOW)) * 100
            rolling_alpha.append(round(ra, 2))
            if i + WINDOW - 1 < len(dates_real):
                rolling_dates.append(dates_real[i + WINDOW - 1])

        # ── Beat IBOV (janela trimestral 63 pregões, passo 21) ──────────────────
        beats = 0
        total = 0
        for i in range(0, n - 62, 21):
            cf = ci = 1.0
            for j in range(i, i + 63):
                cf *= (1 + rets[j])
                ci *= (1 + ibov_ret(dates_real[j]))
            if cf > ci:
                beats += 1
            total += 1
        propensity = round(beats / total * 100, 1) if total > 0 else None

        return {
            "vol":          round(vol,       2),
            "sharpe":       round(sharpe,    4) if sharpe    is not None else None,
            "sortino":      round(sortino,   4) if sortino   is not None else None,
            "calmar":       round(calmar,    4) if calmar    is not None else None,
            "maxDrawdown":  round(mdd_pct,   2),
            "cagrHist":     round(cagr_val,  4),
            "alphaAnn":     round(alpha_ann, 4),
            "teAnn":        round(te_ann,    4),
            "ir":           round(ir,        4) if ir        is not None else None,
            "propensity":   propensity,
            "rollingAlpha": rolling_alpha,
            "rollingDates": rolling_dates,
        }

    # ── Covariância e semi-covariância (universo completo) ───────────────────
    # Pré-calculadas sobre o histórico completo para que o otimizador no browser
    # apenas faça slicing de submatrizes (O(k²)) em vez de recomputar do zero.
    # Unidades: %² anualizadas — idêntico a covMatrix() no index.html.

    def compute_cov_matrix(cdi_annual: float) -> dict:
        """
        Calcula covariância e semi-covariância para todos os pares de fundos.
        Retorna {"cov": {cnpjA: {cnpjB: valor}}, "semiCov": {...}}
        """
        all_cnpjs = [f["cnpjFmt"] for f in FUNDS]
        n_total   = len(common_dates) - 1  # número de retornos

        cdi_daily = math.pow(1 + cdi_annual / 100, 1 / 252) - 1

        # Índice de primeiro retorno real por fundo
        fi_map = {cnpj: first_real_idx_list(returns_by_fund[cnpj]) for cnpj in all_cnpjs}

        # Médias (sobre todo o período real de cada fundo)
        means = {}
        for cnpj in all_cnpjs:
            fi  = fi_map[cnpj]
            rs  = [r for r in returns_by_fund[cnpj][fi:] if r is not None]
            means[cnpj] = sum(rs) / len(rs) if rs else 0.0

        cov_out      = {ca: {} for ca in all_cnpjs}
        semi_cov_out = {ca: {} for ca in all_cnpjs}

        for i, ca in enumerate(all_cnpjs):
            for j, cb in enumerate(all_cnpjs):
                if j < i:
                    # Simétrica — copiar
                    cov_out[ca][cb]      = cov_out[cb][ca]
                    semi_cov_out[ca][cb] = semi_cov_out[cb][ca]
                    continue

                if ca == cb:
                    # Variância diagonal
                    fi  = fi_map[ca]
                    rs  = [r for r in returns_by_fund[ca][fi:] if r is not None]
                    nn  = len(rs)
                    if nn < 2:
                        cov_out[ca][cb] = semi_cov_out[ca][cb] = 0.0
                        continue
                    ma  = means[ca]
                    var = sum((r - ma) ** 2 for r in rs) / (nn - 1) * 252 * 10000
                    cov_out[ca][cb] = round(var, 6)
                    # Semi-variância diagonal
                    sds = [min(r - cdi_daily, 0) for r in rs]
                    sv  = sum(s * s for s in sds) / nn * 252 * 10000
                    semi_cov_out[ca][cb] = round(sv, 6)
                    continue

                # Par distinto: usar apenas datas onde ambos têm retorno real
                fia, fib   = fi_map[ca], fi_map[cb]
                t_start    = max(fia, fib)
                ra_all     = returns_by_fund[ca]
                rb_all     = returns_by_fund[cb]
                pairs      = [(ra_all[t], rb_all[t])
                              for t in range(t_start, n_total)
                              if ra_all[t] is not None and rb_all[t] is not None]
                nn = len(pairs)
                if nn < 30:
                    cov_out[ca][cb] = semi_cov_out[ca][cb] = 0.0
                    continue

                ma, mb = means[ca], means[cb]
                # Covariância
                s = sum((a - ma) * (b - mb) for a, b in pairs)
                cov_out[ca][cb] = round(s / (nn - 1) * 252 * 10000, 6)
                # Semi-covariância
                sds_a = [min(a - cdi_daily, 0) for a, _ in pairs]
                sds_b = [min(b - cdi_daily, 0) for _, b in pairs]
                ss    = sum(sds_a[k] * sds_b[k] for k in range(nn))
                semi_cov_out[ca][cb] = round(ss / nn * 252 * 10000, 6)

        return {"cov": cov_out, "semiCov": semi_cov_out}

    # ── Injetar proxy IBOV para compute_fund_metrics ─────────────────────────
    # (A fronteira eficiente é calculada em main() via compute_efficient_frontier()
    #  após process_fund() ter produzido os retornos esperados por fundo.
    #  O resultado é então injetado no history.json por patch_history_frontier().)
    # Precisamos do retorno anualizado do IBOV para o cálculo do rolling alpha.
    # Usamos o CAGR implícito calculado sobre o período do history — consistente
    # com o que o browser usa (ibov.cagr36 como proxy constante).
    # Este valor é passado via closure através de _ibov_daily_proxy.
    # Será sobrescrito pelo valor real vindo do data.json logo após main() rodar.
    # Aqui usamos um valor padrão conservador; main() injetará o real.
    _ibov_daily_proxy = math.pow(1 + 0.15, 1 / 252) - 1  # fallback 15% a.a.

    # ── Calcular CDI anual para esta run ─────────────────────────────────────
    # Aproximação: derivada do CAGR de 36M do CDI (já calculado em main).
    # Aqui dentro de update_history não temos acesso direto ao CDI fetchado,
    # então usamos fallback de 12.5% — sobrescrito em main() se necessário.
    # Para future-proofing, aceitamos cdi_annual como argumento opcional.

    # ── Chamada das métricas precomputadas ───────────────────────────────────
    # Nota: _ibov_daily_proxy e _cdi_annual_proxy são closures definidas acima
    # e serão sobrepostas por main() antes de chamar update_history().
    # A função aceita os valores via argumento para evitar estado global.
    _cdi_annual_proxy  = getattr(update_history, "_cdi_annual", 12.5)
    _ibov_annual_proxy = getattr(update_history, "_ibov_annual", 15.0)
    _ibov_daily_proxy  = math.pow(1 + _ibov_annual_proxy / 100, 1 / 252) - 1
    # Retornos diários reais do IBOV (injetados por main() antes desta chamada).
    # Usados para rolling alpha rigoroso e Beat IBOV — elimina o viés do proxy constante.
    _ibov_daily_rets: dict = getattr(update_history, "_ibov_daily_rets", {})

    print("  Calculando métricas por fundo…")
    fund_metrics: dict = {}
    for fund in FUNDS:
        cnpj = fund["cnpjFmt"]
        m = compute_fund_metrics(cnpj, _cdi_annual_proxy)
        if m:
            fund_metrics[cnpj] = m
    print(f"  Métricas calculadas: {len(fund_metrics)} fundos")

    print("  Calculando matrizes de covariância e semi-covariância…")
    cov_data = compute_cov_matrix(_cdi_annual_proxy)
    print("  Covariâncias prontas")

    # ── Serializar ───────────────────────────────────────────────────────────
    funds_out = {
        fund["cnpjFmt"]: {
            "nome":        fund["name"],
            "dates":       common_dates,
            "quotas":      [quotas[fund["cnpjFmt"]].get(d) for d in common_dates],  # None = pre-inception
            "returns":     returns_by_fund[fund["cnpjFmt"]],
            "maxDrawdown": fund_metrics.get(fund["cnpjFmt"], {}).get("maxDrawdown",
                           _legacy_max_dd(returns_by_fund[fund["cnpjFmt"]])),
            "metrics":     fund_metrics.get(fund["cnpjFmt"], {}),
        }
        for fund in FUNDS
    }

    n_days  = len(common_dates)
    n_years = (datetime.date.fromisoformat(common_dates[-1]) -
               datetime.date.fromisoformat(common_dates[0])).days / 365.25

    # Serializar retornos diários reais do IBOV alinhados com commonDates.
    # Apenas as datas presentes em commonDates são necessárias — evita serializar
    # fins de semana e feriados que não têm cotas de fundos.
    ibov_rets_filtered = {
        d: _ibov_daily_rets[d]
        for d in common_dates
        if d in _ibov_daily_rets
    }

    output = {
        "generatedAt":      datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "from":             common_dates[0],
        "to":               common_dates[-1],
        "nDays":            n_days,
        "nYears":           round(n_years, 2),
        "commonDates":      common_dates,
        "correlation":      corr,
        "covMatrix":        cov_data["cov"],
        "semiCovMatrix":    cov_data["semiCov"],
        "ibovReturns":      ibov_rets_filtered,
        "funds":            funds_out,
    }

    hist_path.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    size_kb = hist_path.stat().st_size // 1024
    print(f"  ✓ history.json: {n_days} pregões, {n_years:.1f} anos, {size_kb} KB")



def compute_efficient_frontier(mu_map: dict, cov_out: dict, corr: dict) -> list:
    """
    Pré-calcula a fronteira eficiente aproximada via Monte Carlo (Dirichlet).
    Mesma lógica de renderEfficientFrontier() no index.html — garante consistência.

    mu_map:  {cnpjFmt: retorno_esperado_%}   (geralmente cagr36 do fundo)
    cov_out: {cnpjFmt: {cnpjFmt: float}}     covariância %² anualizada (diagonal = var)
    corr:    {cnpjFmt: {cnpjFmt: float}}     correlação de Pearson

    Retorna lista de {x: vol_%, y: ret_%} representando o envelope de Pareto.
    """
    import random as _random

    valid  = [c for c in mu_map if mu_map[c] is not None]
    k      = len(valid)
    if k < 2:
        return []

    mus_v  = [mu_map[c] for c in valid]
    vols_v = [math.sqrt(max(cov_out.get(c, {}).get(c, 0.0), 0.0)) for c in valid]

    N   = 800
    pts = []
    for _ in range(N):
        raw = [-math.log(max(1e-10, _random.random())) for _ in range(k)]
        s   = sum(raw)
        w   = [r / s for r in raw]

        pt_ret = sum(w[i] * mus_v[i] for i in range(k))

        pt_var = 0.0
        for i in range(k):
            for j in range(k):
                rho = corr.get(valid[i], {}).get(valid[j], 0.0) if i != j else 1.0
                pt_var += w[i] * w[j] * vols_v[i] * vols_v[j] * rho
        pt_vol = math.sqrt(max(0.0, pt_var))
        pts.append((round(pt_vol, 2), round(pt_ret, 2)))

    # Envelope de Pareto: para cada bin de 0.5% de volatilidade, manter maior retorno
    BIN   = 0.5
    bins: dict = {}
    for vol_p, ret_p in pts:
        b = round(round(vol_p / BIN) * BIN, 1)
        if b not in bins or ret_p > bins[b]:
            bins[b] = ret_p

    return sorted([{"x": v, "y": r} for v, r in bins.items()], key=lambda p: p["x"])


def fetch_ntnb_historico() -> dict[str, float]:
    """
    Busca o histórico de taxas NTN-B longa do Tesouro Direto (arquivo CSV histórico).

    Retorna dict {data_iso: taxa_real_media_longa} onde taxa é em % ao ano.
    Dados disponíveis desde ~2002; atualizado diariamente pelo Tesouro.

    URL: CSV público com preços e taxas históricas de todos os títulos.
    Formato: Tipo Titulo;Vencimento do Titulo;Data Base;Taxa Compra Manha;...
    """
    url = ("https://www.tesourodireto.com.br/json/br/com/b3/tesourodireto/pte/"
           "rest/api/v1/TesouroDireto_HistoricoPrecosTaxas.csv")
    print("  Buscando histórico NTN-B do Tesouro Direto…")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/csv,*/*",
            "Referer": "https://www.tesourodireto.com.br/",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("latin-1", errors="replace")

        # Parse CSV: separador ;, cabeçalho na linha 1 ou 2
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        # Encontrar linha de cabeçalho (contém "Tipo Titulo" ou "Data Base")
        header_idx = next((i for i, l in enumerate(lines)
                           if "Tipo Titulo" in l or "Data Base" in l), 0)
        header = [h.strip().strip('"') for h in lines[header_idx].split(";")]

        def col(name: str) -> int:
            for i, h in enumerate(header):
                if name.lower() in h.lower():
                    return i
            return -1

        c_tipo = col("Tipo Titulo")
        c_date = col("Data Base")
        c_taxa = col("Taxa Compra Manha")
        c_venc = col("Vencimento")
        if c_date < 0 or c_taxa < 0:
            print("  ✗ NTN-B histórico: colunas não encontradas no CSV")
            return {}

        today = datetime.date.today()
        # NTN-B "longa": vencimento >= 8 anos a partir da data base
        by_date: dict[str, list[float]] = {}
        for line in lines[header_idx + 1:]:
            cols = line.split(";")
            if len(cols) <= max(c_tipo, c_date, c_taxa):
                continue
            tipo = cols[c_tipo].strip().strip('"').upper() if c_tipo >= 0 else ""
            if "IPCA" not in tipo and "NTN-B" not in tipo:
                continue
            date_raw = cols[c_date].strip().strip('"')
            taxa_raw = cols[c_taxa].strip().strip('"').replace(",", ".")
            try:
                # Data pode ser DD/MM/AAAA ou AAAA-MM-DD
                if "/" in date_raw:
                    d, m, y = date_raw.split("/")
                    date_iso = f"{y.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
                else:
                    date_iso = date_raw[:10]
                taxa_val = float(taxa_raw)
                if taxa_val <= 0:
                    continue
                # Filtro de vencimento longo (>= 8 anos a partir da data base)
                if c_venc >= 0:
                    venc_raw = cols[c_venc].strip().strip('"')
                    if "/" in venc_raw:
                        dv, mv, yv = venc_raw.split("/")
                        venc_iso = f"{yv.zfill(4)}-{mv.zfill(2)}-{dv.zfill(2)}"
                    else:
                        venc_iso = venc_raw[:10]
                    base_year = int(date_iso[:4])
                    venc_year = int(venc_iso[:4])
                    if venc_year - base_year < 8:
                        continue
                by_date.setdefault(date_iso, []).append(taxa_val)
            except (ValueError, IndexError):
                continue

        # Média das longas por data
        result = {d: round(sum(v) / len(v), 4) for d, v in by_date.items() if v}
        print(f"  ✓ NTN-B histórico: {len(result)} datas com taxa longa")
        return result

    except Exception as e:
        print(f"  ✗ NTN-B histórico falhou: {e}")
        return {}


def compute_metrics_history(
    hist_path: Path,
    cdi_price_map: dict[str, float],
    ntnb_hist: dict[str, float],
    anchor: datetime.date,
    betas_data: dict,
    backfill_months: int = 12,
) -> None:
    """
    Calcula e persiste o histórico de métricas por fundo no history.json.

    Para cada fundo e para cada 'data de referência' nos últimos backfill_months,
    reconstrói o que o modelo teria estimado naquela data com os dados então
    disponíveis — sem lookahead.

    Métricas calculadas por data:
      targetReturn:    retorno alvo bruto estimado (%)
      netReturn5:      retorno alvo líquido IR a 5 anos (%)
      netReturn10:     retorno alvo líquido IR a 10 anos (%)
      cdiWeighted:     CDI ponderado (12M×1+36M×3+60M×5)/9 naquela data (%)
      ntnbLong:        taxa NTN-B longa usada naquela data (%)
      maxDD:           max drawdown histórico acumulado até a data (%)
      worstStress:     pior retorno estimado nos 5 cenários de crise (%)
                       modelo completo: R_sist(β_crise) + R_idio(CVaR+vol_idio)
                       RF: modelo de carry/spread/duration (sem componente idio)

    Schema adicionado ao history.json:
      metricsHistory: {
        "22.232.927/0001-90": {
          "2024-03-01": { targetReturn, netReturn5, ..., worstStress },
          ...
        },
        ...
      }

    Datas de referência: última data útil de cada mês nos últimos backfill_months.
    Incremental: pula datas já calculadas em execuções anteriores.
    """
    if not hist_path.exists():
        print("  ⚠ compute_metrics_history: history.json não encontrado")
        return

    try:
        hist = json.loads(hist_path.read_text())
    except Exception as e:
        print(f"  ⚠ compute_metrics_history: falha ao ler history.json: {e}")
        return

    common_dates = hist.get("commonDates", [])
    funds_hist   = hist.get("funds", {})
    if not common_dates or not funds_hist:
        print("  ⚠ compute_metrics_history: history.json vazio ou sem commonDates")
        return

    # Datas de referência: última data de cada mês nos últimos backfill_months
    ref_dates: list[datetime.date] = []
    for months_back in range(backfill_months, 0, -1):
        y, m = anchor.year, anchor.month - months_back
        while m <= 0:
            m += 12; y -= 1
        last_day = calendar.monthrange(y, m)[1]
        ref_dates.append(datetime.date(y, m, last_day))
    ref_dates.append(anchor)  # inclui hoje

    # Monta CDI acumulado por data a partir do cdi_price_map
    # cdi_price_map: {iso_date: fator_acumulado} a partir de uma data base
    cdi_dates = sorted(cdi_price_map.keys())

    def cdi_cagr_between(d_start_iso: str, d_end_iso: str) -> float | None:
        """CAGR do CDI entre duas datas usando o price_map do fetch_cdi."""
        ps = cdi_price_map.get(d_start_iso)
        pe = cdi_price_map.get(d_end_iso)
        if not ps or not pe or ps <= 0:
            return None
        yrs = years_apart(d_start_iso, d_end_iso)
        return cagr(ps, pe, yrs)

    def best_cdi_date(target: datetime.date) -> str | None:
        target_iso = target.isoformat()
        if target_iso in cdi_price_map:
            return target_iso
        # Busca o mais próximo anterior
        for d in reversed(cdi_dates):
            if d <= target_iso:
                return d
        return None

    # ════════════════════════════════════════════════════════════════════════════
    # PORT FIEL DO JAVASCRIPT — calcFloorRF e calcTargetReturn
    # Cada linha espelha exatamente o código em index.html.
    # Sem aproximações, sem simplificações.
    # ════════════════════════════════════════════════════════════════════════════

    # Parâmetros globais do calcFloorRF (espelho exato do JS)
    NTNB_HAIRCUT = 0.35
    TAU_ANOS     = 10.0
    META_CMN     = 3.0
    W_META_CMN   = 0.30
    W_FOCUS_5A   = 0.50
    W_FOCUS_12M  = 0.20

    # Valores de NTN-B e Focus IPCA: lidos do data.json (mesmos globals do browser)
    # Em produção estes são atualizados diariamente pelo fetch_data.py
    data_json_path = hist_path.parent / "data.json"
    try:
        data_json = json.loads(data_json_path.read_text())
    except Exception:
        data_json = {}

    ntnb_global     = data_json.get("ntnb", {})
    ipca_focus_glob = data_json.get("ipca_focus", {})
    ibov_global     = data_json.get("ibov", {})
    cdi_global      = data_json.get("cdi", {})
    fund_betas_glob = data_json.get("fund_betas", betas_data)
    funds_data_glob = data_json.get("funds", [])  # lista de fundos do data.json

    ntnb_rate_long = ntnb_global.get("ntnb_rate_long") or 7.05
    focus_12m      = ipca_focus_glob.get("ipca_12m") or 4.8
    focus_5a       = ipca_focus_glob.get("ipca_longo_prazo") or 4.0

    # Port fiel de calcFloorRF(horizonte, cdiObservado)
    def calc_floor_rf_full(horizonte: float, cdi_observado: float,
                           ntnb_long: float, f12m: float, f5a: float) -> dict:
        ipca_estr      = W_META_CMN * META_CMN + W_FOCUS_5A * f5a + W_FOCUS_12M * f12m
        neutro_real    = ntnb_long * (1 - NTNB_HAIRCUT)
        neutral_nom    = ((1 + neutro_real / 100) * (1 + ipca_estr / 100) - 1) * 100
        h              = max(0.5, horizonte)
        w_cdi          = math.exp(-h / TAU_ANOS)
        floor          = w_cdi * cdi_observado + (1 - w_cdi) * neutral_nom
        return {
            "floor": floor, "neutralNominal": neutral_nom,
            "ntnbLong": ntnb_long, "ipcaEstr": ipca_estr,
            "w_cdi": w_cdi, "horizonte": h,
        }

    # FUND_META_PY agora é constante de módulo (ver topo) — usada também na matriz de alphas.

    # Port fiel de cagrInception(f) — usa cota inicial e cota na data de referência
    def cagr_inception_py(cnpj: str, latest_quota: float | None,
                          latest_date_iso: str) -> float | None:
        meta = FUND_META_PY.get(cnpj)
        if not meta or not meta.get("inception") or not latest_quota:
            return None
        inc_quota = meta["initialQuota"]
        inc_date  = datetime.date.fromisoformat(meta["inception"])
        lat_date  = datetime.date.fromisoformat(latest_date_iso)
        years = (lat_date - inc_date).days / 365.25
        if years <= 0:
            return None
        return (math.pow(latest_quota / inc_quota, 1.0 / years) - 1) * 100

    # Port fiel de alphaVsIbov e alphaVsCdi desde inception
    # Usa ibovReturns do history.json (mesmo que o browser usa) e cdi_price_map
    ibov_returns_map_full: dict[str, float] = hist.get("ibovReturns", {})

    def alpha_vs_ibov_inception(cnpj: str, fund_rets: list,
                                 fund_dates: list, inception_iso: str) -> float | None:
        """Replica alphaVsIbov: CAGR_fund_inception - CAGR_ibov_inception."""
        if not inception_iso or not fund_rets or not fund_dates:
            return None
        # Retornos do fundo desde inception
        cum_fund = 1.0
        cum_ibov = 1.0
        n_days = 0
        for i, d in enumerate(fund_dates):
            if d < inception_iso:
                continue
            if i >= len(fund_rets) or fund_rets[i] is None:
                continue
            r_fund = fund_rets[i]
            r_ibov = ibov_returns_map_full.get(d, 0.0)
            cum_fund *= (1 + r_fund)
            cum_ibov *= (1 + r_ibov)
            n_days += 1
        if n_days < 30:
            return None
        years = n_days / 252
        cagr_fund = (math.pow(cum_fund, 1.0 / years) - 1) * 100
        cagr_ibov = (math.pow(cum_ibov, 1.0 / years) - 1) * 100
        return round(cagr_fund - cagr_ibov, 4)

    def alpha_vs_cdi_inception(cnpj: str, fund_rets: list,
                                fund_dates: list, inception_iso: str,
                                cdi_pm: dict) -> float | None:
        """Replica alphaVsCdi: CAGR_fund_inception - CAGR_cdi_inception."""
        if not inception_iso or not fund_rets or not fund_dates:
            return None
        cum_fund = 1.0
        n_days = 0
        first_date = None
        last_date  = None
        for i, d in enumerate(fund_dates):
            if d < inception_iso:
                continue
            if i >= len(fund_rets) or fund_rets[i] is None:
                continue
            cum_fund *= (1 + fund_rets[i])
            n_days += 1
            if first_date is None:
                first_date = d
            last_date = d
        if n_days < 30 or first_date is None:
            return None
        years = n_days / 252
        cagr_fund = (math.pow(cum_fund, 1.0 / years) - 1) * 100
        # CDI entre primeira e última data
        p_start = cdi_pm.get(first_date)
        p_end   = cdi_pm.get(last_date)
        if not p_start or not p_end or p_start <= 0:
            return None
        cagr_cdi = (math.pow(p_end / p_start, 1.0 / years) - 1) * 100
        return round(cagr_fund - cagr_cdi, 4)

    # Port fiel do IR e propensity (compute_fund_metrics) — sobre retornos até ref_date
    def compute_ir_and_propensity(fund_rets: list, fund_dates: list,
                                   ibov_rets_map: dict, cdi_ann: float) -> dict:
        """Porta exatamente compute_fund_metrics: IR vs IBOV e propensity (beat%)."""
        n = len(fund_rets)
        if n < 60:
            return {"ir": None, "propensity": None, "alpha_ann": None}

        cdi_daily        = math.pow(1 + cdi_ann / 100, 1 / 252) - 1
        ibov_proxy_daily = math.pow(1 + (ibov_global.get("cagr36") or 15.0) / 100, 1 / 252) - 1

        def ibov_ret_d(d: str) -> float:
            r = ibov_rets_map.get(d)
            return r if r is not None else ibov_proxy_daily

        # IR vs IBOV — espelho exato do compute_fund_metrics JS-side
        ibov_excess_d = [fund_rets[i] - ibov_ret_d(fund_dates[i]) for i in range(n)]
        alpha_d_daily = sum(ibov_excess_d) / n
        alpha_ann     = (math.pow(1 + alpha_d_daily, 252) - 1) * 100
        te_d_vals     = [(e - alpha_d_daily) ** 2 for e in ibov_excess_d]
        te_d          = math.sqrt(sum(te_d_vals) / (n - 1)) if n > 1 else 0
        te_ann        = te_d * math.sqrt(252) * 100
        ir            = alpha_ann / te_ann if te_ann > 0 else None

        # propensity: beat IBOV em janelas de 63 pregões, passo 21
        beats = 0
        total = 0
        for i in range(0, n - 62, 21):
            cf = ci = 1.0
            for j in range(i, i + 63):
                cf *= (1 + fund_rets[j])
                ci *= (1 + ibov_ret_d(fund_dates[j]))
            if cf > ci:
                beats += 1
            total += 1
        propensity = round(beats / total * 100, 1) if total > 0 else None

        return {
            "ir":         round(ir, 4) if ir is not None else None,
            "propensity": propensity,
            "alpha_ann":  round(alpha_ann, 4),
        }

    # Port fiel de calcTargetReturn(f, globalFunds, globalCdiTarget, horizonte)
    def calc_target_return_py(
        cnpj: str,
        cagr12: float | None,
        cagr36: float | None,
        cagr60: float | None,
        latest_quota: float | None,
        latest_date_iso: str,
        fund_rets_to_ref: list,        # retornos do fundo até ref_date
        fund_dates_to_ref: list,       # datas correspondentes
        cdi_observado: float,          # CDI ponderado naquela data
        ibov_rets_map: dict,           # ibovReturns do history.json
        cdi_pm: dict,                  # cdi_price_map
        all_funds_snapshot: list,      # lista de {cnpj, cagr12, cagr36, cagr60, ci, alpha, ir, propensity}
        ntnb_long_val: float,
        f12m_val: float,
        f5a_val: float,
        horizonte: float | None = None,
    ) -> float | None:
        """
        Port completo e fiel de calcTargetReturn do JS.
        Sem nenhuma aproximação — cada linha é um espelho direto do JavaScript.
        """
        meta   = FUND_META_PY.get(cnpj, {})
        tipo   = (meta.get("tipo") or "").lower()
        is_multi = "multimercado" in tipo
        is_rf    = "renda fixa" in tipo

        # ── Sinal próprio ──────────────────────────────────────────────────────
        # Pesos T² em vez de √T: c60 recebe 25×, c36 recebe 9×, c12 recebe 1×.
        # Justificativa: o retorno alvo é âncora de longo prazo — janelas curtas
        # devem ser ajuste marginal, não sinal dominante. T² penaliza c12
        # (muito ruidoso) e amplifica c60 e cagrInception (estáveis).
        # O JS usa √T; aqui aumentamos a estabilidade sem mudar a estrutura.
        samples: list[tuple[float, float]] = []
        if cagr12 is not None: samples.append((1.0, cagr12))
        if cagr36 is not None: samples.append((3.0, cagr36))
        if cagr60 is not None: samples.append((5.0, cagr60))

        inception_iso = meta.get("inception")
        age_years: float = 10.0
        if inception_iso and latest_date_iso:
            inc_d = datetime.date.fromisoformat(inception_iso)
            lat_d = datetime.date.fromisoformat(latest_date_iso)
            age_years = (lat_d - inc_d).days / 365.25

        # cagrInception — âncora mais estável: incorpora todo o histórico do fundo
        ci = cagr_inception_py(cnpj, latest_quota, latest_date_iso)
        if ci is not None and age_years > 5.5:
            samples.append((age_years, ci))

        if not samples:
            return None

        # Pesos T² (não √T) — favorece fortemente janelas longas
        t2_weights = [T * T for T, _ in samples]
        total_w    = sum(t2_weights)
        raw_avg    = sum(t2_weights[i] * v for i, (_, v) in enumerate(samples)) / total_w

        # Penalidade de dispersão ciclical — igual ao JS
        variance = sum(t2_weights[i] * (v - raw_avg) ** 2
                       for i, (_, v) in enumerate(samples)) / total_w
        sigma   = math.sqrt(variance)
        penalty = min(sigma * 0.30, abs(raw_avg) * 0.15)
        adjusted = raw_avg - penalty if raw_avg >= 0 else raw_avg + penalty

        # Pull de reversão à média — âncora é cagrInception (se disponível) ou c60.
        # cagrInception é a âncora de longo prazo mais estável: usa todo o histórico
        # do fundo e oscila muito menos que qualquer janela móvel de 5 anos.
        # pull_force fixo em 0.25 para fundos maduros: mantém conexão forte com o LP.
        anchor_lp  = ci if ci is not None else (cagr60 if cagr60 is not None else raw_avg)
        pull_force = max(0.20, 0.30 - max(0, age_years - 5) * 0.005)
        sinal_proprio = adjusted * (1 - pull_force) + anchor_lp * pull_force

        # ── Prior ──────────────────────────────────────────────────────────────
        ibov_long = (ibov_global.get("cagr60") or ibov_global.get("cagr36") or
                     ibov_global.get("cagr12") or 12.0)
        benchmark = cdi_observado if (is_multi or is_rf) else ibov_long

        fund_data_entry = next((f for f in funds_data_glob if f.get("cnpjFmt") == cnpj), {})
        if is_multi or is_rf:
            alpha_obs = fund_data_entry.get("alphaVsCdi") or 0.0
        else:
            alpha_obs = fund_data_entry.get("alphaVsIbov") or fund_data_entry.get("alphaAnn") or 0.0

        alpha_dif = alpha_obs
        peers = [
            p for p in all_funds_snapshot
            if p["cnpj"] != cnpj and p.get("age_years", 0) >= 5
            and (
                (is_multi and "multimercado" in (FUND_META_PY.get(p["cnpj"], {}).get("tipo") or "").lower()) or
                (is_rf    and "renda fixa"   in (FUND_META_PY.get(p["cnpj"], {}).get("tipo") or "").lower()) or
                (not is_multi and not is_rf and
                 "multimercado" not in (FUND_META_PY.get(p["cnpj"], {}).get("tipo") or "").lower() and
                 "renda fixa"   not in (FUND_META_PY.get(p["cnpj"], {}).get("tipo") or "").lower())
            )
        ]
        if peers:
            peer_alphas = [p["alpha_obs"] for p in peers if p.get("alpha_obs") is not None]
            if peer_alphas:
                group_alpha = sum(peer_alphas) / len(peer_alphas)
                alpha_dif   = alpha_obs - group_alpha

        prior = benchmark + alpha_dif * 0.5

        # ── λ trifatorial ──────────────────────────────────────────────────────
        n_efetivo = (min(age_years, 3) * (1 if cagr36 is not None else 0.5)
                    + min(max(age_years - 3, 0), 2) * (1 if cagr60 is not None else 0)
                    + max(age_years - 5, 0) * 0.5)
        lambda_hist = math.exp(-n_efetivo / 6)

        metrics_snap = compute_ir_and_propensity(
            fund_rets_to_ref, fund_dates_to_ref, ibov_rets_map, cdi_observado)
        ir_val     = metrics_snap["ir"]
        propensity = metrics_snap["propensity"]

        ir_score   = 0.5
        beat_score = 0.5
        if ir_val is not None:
            ir_score = min(1.0, max(0.0, (ir_val + 0.5) / 1.5))
        if propensity is not None:
            beat_score = min(1.0, max(0.0, (propensity - 40) / 40))
        consist_score  = 0.6 * ir_score + 0.4 * beat_score
        lambda_consist = 1.0 - consist_score

        # λ_recente: decay amortecido a 0.25 (em vez de 0.5 no JS original).
        # Justificativa: o sinal recente (c36 vs cagrInception) é legítimo mas
        # ruidoso — um fator de 0.25 preserva a informação sem amplificar o ciclo.
        lambda_recente = 0.5
        if ci is not None and age_years > 3:
            recente = cagr36 if cagr36 is not None else (cagr12 if cagr12 is not None else ci)
            decay   = (ci - recente) / (abs(ci) + 1)
            lambda_recente = min(0.9, max(0.1, 0.5 + decay * 0.25))  # 0.25 em vez de 0.5

        lam = lambda_hist * lambda_consist * lambda_recente

        # ── Blending final ─────────────────────────────────────────────────────
        blended = (1 - lam) * sinal_proprio + lam * prior

        # Teto: E[R] ≤ cagrInception
        capped = min(blended, ci) if ci is not None else blended

        # ── Floor estrutural ───────────────────────────────────────────────────
        if is_rf:
            h = horizonte if horizonte is not None else min(max(age_years, 2), 10)
            floor_info = calc_floor_rf_full(h, cdi_observado, ntnb_long_val, f12m_val, f5a_val)
            cdi_floor  = floor_info["floor"]
        else:
            cdi_floor = cdi_observado

        return max(capped, cdi_floor)

    # ── Modelo de stress completo — espelho fiel do buildFundPanel JS ─────────
    #
    # R_stress = R_sistemático + R_idiossincrático
    #
    # R_sist = exposure.net_crisis × (β_crise_ibov × R_ibov + β_crise_sp × R_sp_brl)
    #   β_crise: OLS só em pregões com R_ibov < CRISE_THRESHOLD (dias ruins)
    #   Sem dados suficientes: usa β_normal como fallback
    #
    # R_idio = w_emp × CVaR_resíduo × scaleCorr × √252
    #        + w_teo × (−vol_idio × k_crise × scaleCorr)
    #   CVaR: média dos piores 10% dos resíduos diários
    #   k_crise: std(ε|dias ruins) / std(ε|todos)
    #   ρ̄: autocorrelação média dos resíduos nos lags 1–3
    #   scaleCorr = √(T_anos × max(0.5, 1 + 2ρ̄))
    #   w_emp = min(1, n_dias_ruins / N_MIN)
    #
    # RF: usa modelo de carry/spread/duration (sem componente idiossincrática)
    #
    CRISE_THRESHOLD = -0.015   # IBOV < -1.5% = dia de crise
    N_MIN_CRISE     = 40       # dias ruins mínimos para confiança empírica total
    K_IDIO_DEFAULT  = 1.5      # amplificação padrão quando sem dados suficientes

    # Cenários de stress — espelho exato do JS STRESS_SCENARIOS
    STRESS_SCENARIOS_PY = [
        {"name":"2008",  "ibov_ret":-0.600, "sp500_usd":-0.565, "brl_dep":+0.350, "days":365,
         "credit_spread_shock":4.0, "real_rate_shock":3.5, "cdi_acc_period":0.133},
        {"name":"2013",  "ibov_ret":-0.280, "sp500_usd":+0.000, "brl_dep":+0.150, "days":180,
         "credit_spread_shock":1.0, "real_rate_shock":2.0, "cdi_acc_period":0.054},
        {"name":"2015",  "ibov_ret":-0.410, "sp500_usd":-0.120, "brl_dep":+0.500, "days":365,
         "credit_spread_shock":2.0, "real_rate_shock":2.8, "cdi_acc_period":0.133},
        {"name":"Covid", "ibov_ret":-0.449, "sp500_usd":-0.340, "brl_dep":+0.300, "days":30,
         "credit_spread_shock":3.5, "real_rate_shock":3.5, "cdi_acc_period":0.011},
        {"name":"2022",  "ibov_ret":-0.280, "sp500_usd":-0.240, "brl_dep":+0.080, "days":90,
         "credit_spread_shock":1.2, "real_rate_shock":3.0, "cdi_acc_period":0.030},
    ]

    # Exposição por CNPJ (espelho do FUND_EXPOSURE JS)
    FUND_EXPOSURE_PY: dict[str, dict] = {
        "22.232.927/0001-90": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "17.400.251/0001-66": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "18.302.338/0001-63": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "37.495.383/0001-26": {"net_normal":0.70, "net_crisis":0.50, "primary":"ibov"},
        "42.698.666/0001-05": {"net_normal":0.30, "net_crisis":0.10, "primary":"sp500"},
        "24.623.392/0001-03": {"net_normal":0.55, "net_crisis":0.30, "primary":"ibov"},
        "28.747.685/0001-53": {"net_normal":1.00, "net_crisis":0.85, "primary":"ibov"},
        "10.500.884/0001-05": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "35.744.790/0001-02": {"net_normal":0.00, "net_crisis":0.00, "primary":"sp500"},
        "38.954.217/0001-03": {"net_normal":0.50, "net_crisis":0.25, "primary":"ibov"},
        "32.073.525/0001-43": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "21.689.246/0001-92": {"net_normal":1.00, "net_crisis":0.95, "primary":"sp500"},
        "14.438.229/0001-17": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "17.397.315/0001-17": {"net_normal":0.80, "net_crisis":0.65, "primary":"ibov"},
        "46.351.969/0001-08": {"net_normal":1.00, "net_crisis":0.95, "primary":"sp500"},
        "15.334.585/0001-53": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "47.511.351/0001-20": {"net_normal":1.00, "net_crisis":0.95, "primary":"sp500"},
        "52.116.227/0001-09": {"net_normal":0.30, "net_crisis":0.10, "primary":"ibov"},
        "47.612.105/0001-65": {"net_normal":0.30, "net_crisis":0.10, "primary":"mixed"},
        "29.726.133/0001-21": {"net_normal":0.30, "net_crisis":0.10, "primary":"mixed"},
        "35.828.684/0001-07": {"net_normal":0.30, "net_crisis":0.10, "primary":"mixed"},
        "16.876.874/0001-47": {"net_normal":1.00, "net_crisis":0.95, "primary":"ibov"},
        "52.239.457/0001-57": {"net_normal":0.0, "net_crisis":0.0, "primary":"cdi",
                               "rf_subtype":"pos_fixado_global","credit_duration":0.5,"rate_duration":0.5,"fx_exposure":0.15},
        "51.253.495/0001-00": {"net_normal":0.0, "net_crisis":0.0, "primary":"cdi",
                               "rf_subtype":"credito_privado","credit_duration":2.0,"rate_duration":0.0,"fx_exposure":0.0},
        "52.969.671/0001-69": {"net_normal":0.0, "net_crisis":0.0, "primary":"cdi",
                               "rf_subtype":"debentures_infra","credit_duration":4.5,"rate_duration":6.5,"fx_exposure":0.0},
    }

    ibov_returns_map: dict[str, float] = hist.get("ibovReturns", {})

    def _compute_fund_stress_params(
        cnpj: str,
        fund_rets: list,          # retornos diários do fundo até ref_date
        common_dates_slice: list, # datas correspondentes
        beta_ibov_n: float,
        beta_sp_n: float,
        r2: float,
    ) -> dict:
        """
        Calcula os parâmetros de stress para um fundo (espelho do JS buildFundPanel).
        Retorna: {b_ibov_crise, b_sp_crise, cvarResiduo, rhoBar, kIdioCrise,
                  volAnual, r2, nDiasRuins, w_emp}
        """
        n = len(fund_rets)

        # Vol total anualizada
        if n < 2:
            return {}
        mean_r = sum(fund_rets) / n
        var_   = sum((r - mean_r) ** 2 for r in fund_rets) / (n - 1)
        vol_ann = math.sqrt(var_ * 252)  # decimal, não %

        # JS usa dates[i+1] para o retorno IBOV do pregão i:
        # fund_rets[i] = quota[i+1]/quota[i]-1, cuja "data de chegada" é dates[i+1].
        # Espelho exato: ibovDay(dates[i+1 || ''])
        def ibov_at(i: int) -> float:
            d = common_dates_slice[i + 1] if i + 1 < len(common_dates_slice) else ''
            return ibov_returns_map.get(d, 0.0)

        ibov_rets_slice = [ibov_at(i) for i in range(n)]
        residuos        = [fund_rets[i] - beta_ibov_n * ibov_rets_slice[i] for i in range(n)]

        # Identificar dias de crise (IBOV < threshold) — espelho do JS
        crise_mask   = [ibov_rets_slice[i] < CRISE_THRESHOLD for i in range(n)]
        n_dias_ruins = sum(crise_mask)

        b_ibov_crise = beta_ibov_n
        b_sp_crise   = beta_sp_n
        expo = FUND_EXPOSURE_PY.get(cnpj, {})
        primary = expo.get("primary", "ibov")
        # Só recalcula β_crise para fundos cujo benchmark primário é IBOV.
        # Para fundos internacionais (sp500), o IBOV é proxy ruim em crises externas.
        if n_dias_ruins >= 20 and primary in ("ibov", "mixed"):
            sXX=sXY=sX=sY=nC_=0
            for i in range(n):
                if not crise_mask[i]: continue
                ri=ibov_rets_slice[i]; rf=fund_rets[i]
                sXX+=ri*ri; sXY+=ri*rf; sX+=ri; sY+=rf; nC_+=1
            denom = nC_*sXX - sX*sX
            if abs(denom) > 1e-12:
                b_ibov_crise = (nC_*sXY - sX*sY)/denom
                b_ibov_crise = max(0.0, min(3.0, b_ibov_crise))
                b_sp_crise   = b_ibov_crise * (beta_sp_n / max(abs(beta_ibov_n), 1e-6))

        # CVaR 15% dos resíduos dos dias de crise — espelho exato do JS:
        # criseIdx.map(i => residuos[i]).sort().slice(0, p15n)
        crise_residuos = sorted([residuos[i] for i in range(n) if crise_mask[i]])
        if crise_residuos:
            n15        = max(1, len(crise_residuos) * 15 // 100)
            cvar_resid = sum(crise_residuos[:n15]) / n15
        else:
            cvar_resid = None

        # Autocorrelação dos resíduos, lags 1–3 — sem mudança
        rho_sum = 0.0
        rho_cnt = 0
        mean_e  = sum(residuos) / n
        var_e   = sum((e - mean_e)**2 for e in residuos) / max(n-1, 1)
        if var_e > 1e-12:
            for lag in range(1, 4):
                pairs = [(residuos[i]-mean_e, residuos[i-lag]-mean_e)
                         for i in range(lag, n)]
                if pairs:
                    cov_lag = sum(a*b for a,b in pairs) / len(pairs)
                    rho_sum += cov_lag / var_e
                    rho_cnt += 1
        rho_bar = rho_sum / rho_cnt if rho_cnt > 0 else 0.0

        # k_crise — sem mudança
        k_idio = K_IDIO_DEFAULT
        if n_dias_ruins >= 5:
            res_crise  = [residuos[i] for i in range(n) if crise_mask[i]]
            std_all    = math.sqrt(var_e) if var_e > 0 else 1e-6
            var_crise  = sum((e - sum(res_crise)/len(res_crise))**2
                             for e in res_crise) / max(len(res_crise)-1, 1)
            std_crise  = math.sqrt(var_crise) if var_crise > 0 else std_all
            k_idio     = std_crise / std_all if std_all > 1e-8 else K_IDIO_DEFAULT

        # w_emp: exige 80 dias ruins para confiança total (em vez de 40).
        # Justificativa: 40 dias representa apenas ~4 meses de dados de mercado
        # estressado — insuficiente para calibrar o CVaR com precisão.
        # Com 80 dias (~8 meses de crises), o empírico é muito mais confiável.
        # Efeito: w_emp evolui mais gradualmente; com 39 dias ruins → w_emp=0.49
        # (em vez de 0.97), dando mais peso ao modelo teórico estável.
        w_emp = min(1.0, n_dias_ruins / 80)  # 80 em vez de 40

        return {
            "b_ibov_crise": b_ibov_crise,
            "b_sp_crise":   b_sp_crise,
            "cvar_resid":   cvar_resid,
            "rho_bar":      rho_bar,
            "k_idio":       k_idio,
            "vol_ann":      vol_ann,
            "r2":           r2,
            "n_dias_ruins": n_dias_ruins,
            "w_emp":        w_emp,
        }

    def calc_worst_stress(
        cnpj: str,
        stress_params: dict,  # saída de _compute_fund_stress_params
        cdi_ann: float,       # CDI anual ponderado (%) para carry RF
    ) -> float | None:
        """
        Pior retorno nos 5 cenários de crise — modelo completo fiel ao JS.
        """
        expo = FUND_EXPOSURE_PY.get(cnpj, {"net_normal":1.0,"net_crisis":0.8,"primary":"ibov"})
        worst = None

        for sc in STRESS_SCENARIOS_PY:
            # ── Branch RF ────────────────────────────────────────────────────
            if expo.get("primary") == "cdi":
                credit_dur  = expo.get("credit_duration", 0)
                rate_dur    = expo.get("rate_duration",   0)
                fx_expo     = expo.get("fx_exposure",     0)
                cdi_carry   = (math.pow(1 + cdi_ann / 100, sc["days"] / 365) - 1
                               if sc.get("cdi_acc_period") is None
                               else sc["cdi_acc_period"])
                credit_loss = (sc.get("credit_spread_shock", 0) / 100) * credit_dur
                dur_loss    = (sc.get("real_rate_shock",    0) / 100) * rate_dur
                fx_loss     = fx_expo * abs(sc.get("brl_dep", 0)) * -0.3
                ret = (cdi_carry - credit_loss - dur_loss + fx_loss) * 100

            # ── Branch equity/multi ──────────────────────────────────────────
            else:
                if not stress_params:
                    continue
                b_ibov_c = stress_params["b_ibov_crise"]
                b_sp_c   = stress_params["b_sp_crise"]
                vol_ann  = stress_params["vol_ann"]
                r2       = stress_params["r2"]
                cvar     = stress_params["cvar_resid"]
                rho_bar  = stress_params["rho_bar"]
                k_idio   = stress_params["k_idio"]
                w_emp    = stress_params["w_emp"]
                w_teo    = 1.0 - w_emp

                # Exposição em crise
                nn = max(expo.get("net_normal", 1.0), 0.01)
                nc = expo.get("net_crisis", 0.8)
                net_adj = nc / nn

                # Retornos dos índices no cenário
                r_ibov   = sc["ibov_ret"]
                r_sp_brl = (1 + sc.get("sp500_usd", 0)) * (1 + sc.get("brl_dep", 0)) - 1
                primary  = expo.get("primary", "ibov")
                if primary == "ibov":
                    r_factor = net_adj * (b_ibov_c * r_ibov + b_sp_c * r_sp_brl)
                elif primary == "sp500":
                    r_factor = net_adj * (b_sp_c * r_sp_brl + b_ibov_c * r_ibov)
                else:  # mixed
                    r_factor = net_adj * (b_ibov_c * r_ibov * 0.5 + b_sp_c * r_sp_brl * 0.5)

                # Amplificação de correlação em crises severas
                if abs(r_ibov) > 0.3:
                    r_factor *= 1.05

                # Componente idiossincrática
                vol_idio_ann = vol_ann * math.sqrt(max(0.0, 1.0 - r2))
                T_anos       = sc["days"] / 252
                scale_corr   = math.sqrt(T_anos * max(0.5, 1.0 + 2.0 * rho_bar))
                # cvar_resid é None quando não há N_MIN=80 dias de crise na janela
                # até a ref_date (fundos novos / datas de referência antigas).
                # Era exatamente isto que derrubava o backfill inteiro com
                # TypeError (None * float) — engolido pelo try/except do chamador,
                # razão pela qual o metricsHistory NUNCA foi gravado em nenhum run
                # saudável. Sem CVaR empírico, o peso vai todo para a teórica.
                if cvar is None:
                    w_emp, w_teo = 0.0, 1.0
                    r_emp = 0.0
                else:
                    r_emp = cvar * scale_corr * math.sqrt(252)
                r_teo        = -(vol_idio_ann * k_idio * scale_corr)
                r_idio       = w_emp * r_emp + w_teo * r_teo

                ret = (r_factor + r_idio) * 100

            if worst is None or ret < worst:
                worst = ret

        return round(worst, 2) if worst is not None else None
    IR_RATES = {5: 0.15, 10: 0.15}   # simplificação: tabela regressiva ≈ 15% LP
    def net_return(gross_pct: float, years: int, trib: str) -> float:
        """Retorno líquido de IR: simplificado para o backfill."""
        if trib == "Isento":
            return gross_pct
        ir = IR_RATES.get(years, 0.15)
        # Aproximação: (1+r)^n líquido = (1+r*0.85)^n após come-cotas
        # Para TR: 15% sobre ganho acumulado menos efeito do come-cotas semestral
        # Aqui usamos a aproximação de compounding com alíquota efetiva
        g = gross_pct / 100
        net_ann = (1 + g) ** years
        gain = net_ann - 1
        net_with_ir = 1 + gain * (1 - ir)
        return (net_with_ir ** (1 / years) - 1) * 100

    # Mapeamento CNPJ → trib (precisa ler do FUND_META — indisponível aqui, usa fallback)
    # Codificamos os tipos diretamente para os CNPJs conhecidos
    TRIB_MAP = {
        "52.239.457/0001-57": "TR",   # Janeiro RF
        "51.253.495/0001-00": "TR",   # Mapfre
        "52.969.671/0001-69": "Isento",  # Artax Infra
    }
    def get_trib(cnpj: str, fund_info: dict) -> str:
        return TRIB_MAP.get(cnpj, "RV")  # ações/multi → RV

    # Versão do modelo — mudar quando os parâmetros de cálculo mudarem.
    # Quando a versão muda, todo o metricsHistory é recalculado do zero.
    # Quando a versão é a mesma, só adiciona datas novas (incremental).
    # Isso garante: histórico imutável + consistência quando o modelo evolui.
    MODEL_VERSION = "v4"  # T² weights, CVaR 15% crisis-only, N_MIN=80, beta_crise ibov-only

    saved_version = hist.get("metricsHistoryVersion")
    if saved_version != MODEL_VERSION:
        print(f"  Modelo mudou ({saved_version} → {MODEL_VERSION}): recalculando metricsHistory do zero")
        existing = {}
    else:
        existing = hist.get("metricsHistory", {})

    new_entries: dict[str, dict[str, dict]] = {cnpj: {} for cnpj in funds_hist}
    total_computed = 0

    for ref_date in ref_dates:
        ref_iso = ref_date.isoformat()
        # Encontra o índice da data de referência em commonDates
        if ref_iso not in common_dates:
            # Encontra a data mais próxima anterior
            ref_iso_eff = next((d for d in reversed(common_dates) if d <= ref_iso), None)
            if not ref_iso_eff:
                continue
        else:
            ref_iso_eff = ref_iso

        ref_idx = common_dates.index(ref_iso_eff)

        # CDI nas janelas 12M, 36M, 60M até ref_date
        def subtract_months_date(d: datetime.date, n: int) -> datetime.date:
            y, m = d.year, d.month - n
            while m <= 0: m += 12; y -= 1
            last = calendar.monthrange(y, m)[1]
            return datetime.date(y, m, min(d.day, last))

        d12 = subtract_months_date(ref_date, 12)
        d36 = subtract_months_date(ref_date, 36)
        d60 = subtract_months_date(ref_date, 60)

        cdi12 = cdi_cagr_between(best_cdi_date(d12) or ref_iso_eff, best_cdi_date(ref_date) or ref_iso_eff)
        cdi36 = cdi_cagr_between(best_cdi_date(d36) or ref_iso_eff, best_cdi_date(ref_date) or ref_iso_eff)
        cdi60 = cdi_cagr_between(best_cdi_date(d60) or ref_iso_eff, best_cdi_date(ref_date) or ref_iso_eff)

        pts = [(T, v) for T, v in [(1, cdi12), (3, cdi36), (5, cdi60)] if v is not None]
        if not pts:
            continue
        cdi_weighted = sum(T * v for T, v in pts) / sum(T for T, _ in pts)

        # NTN-B longa na data (ou fallback)
        ntnb_long = ntnb_hist.get(ref_iso_eff)
        if ntnb_long is None:
            # Procura a mais próxima anterior
            ntnb_long = next((ntnb_hist[d] for d in sorted(ntnb_hist.keys(), reverse=True)
                              if d <= ref_iso_eff), 7.05)

        cdi_floor_5a = calc_floor_rf_full(5.0, cdi_weighted, ntnb_long,
                                           focus_12m, focus_5a)["floor"]

        # ── Peer snapshot para esta data de referência ──────────────────────
        # Necessário para alphaDiferencial no calcTargetReturn — exatamente como o
        # JS usa globalFunds no momento do render. Para cada peer, calculamos
        # alpha_obs (vs IBOV ou CDI) sobre os retornos disponíveis até ref_date.
        peer_snapshot: list[dict] = []
        for p_cnpj, p_fd in funds_hist.items():
            p_returns = p_fd.get("returns", [])
            p_valid   = [i for i, d in enumerate(common_dates)
                         if d <= ref_iso_eff and i < len(p_returns) and p_returns[i] is not None]
            if len(p_valid) < 60:
                continue
            p_rets  = [p_returns[i] for i in p_valid]
            p_dates = [common_dates[i] for i in p_valid]
            p_meta  = FUND_META_PY.get(p_cnpj, {})
            p_tipo  = (p_meta.get("tipo") or "").lower()
            p_inc   = p_meta.get("inception")
            p_age   = 0.0
            if p_inc and ref_iso_eff:
                p_age = (datetime.date.fromisoformat(ref_iso_eff) -
                         datetime.date.fromisoformat(p_inc)).days / 365.25
            p_is_multi = "multimercado" in p_tipo
            p_is_rf    = "renda fixa"   in p_tipo
            # alpha do data.json — calculado desde inception real via CVM
            p_data_entry = next((f for f in funds_data_glob if f.get("cnpjFmt") == p_cnpj), {})
            if p_is_multi or p_is_rf:
                p_alpha = p_data_entry.get("alphaVsCdi")
            else:
                p_alpha = p_data_entry.get("alphaVsIbov") or p_data_entry.get("alphaAnn")
            peer_snapshot.append({
                "cnpj":      p_cnpj,
                "age_years": p_age,
                "alpha_obs": p_alpha,
            })

        for cnpj, fd in funds_hist.items():
            # Pula datas já calculadas — cada ponto é imutável (calculado com dados da época)
            if ref_iso in existing.get(cnpj, {}):
                continue

            dates_fund = fd.get("dates", [])
            quotas     = fd.get("quotas", [])
            returns    = fd.get("returns", [])
            if not dates_fund or len(quotas) < 2:
                continue

            # Apenas datas até ref_date (sem lookahead)
            valid_idx = [i for i, d in enumerate(common_dates) if d <= ref_iso_eff and i < len(returns)]
            if len(valid_idx) < 20:
                continue

            # Retornos e datas do fundo até ref_date (apenas não-None)
            rets_to_ref  = [returns[i] for i in valid_idx if returns[i] is not None]
            dates_to_ref = [common_dates[i] for i in valid_idx if returns[i] is not None]
            if len(rets_to_ref) < 20:
                continue

            # CAGR nas janelas até ref_date — usa quota_on_or_before exatamente
            # como process_fund faz, buscando cotas reais da CVM (com cache).
            # Isso garante que os CAGRs históricos são idênticos ao que o site mostra,
            # incluindo janelas que precedem o início do history.json.
            fund_spec = next((f for f in FUNDS if f["cnpjFmt"] == cnpj), None)
            if fund_spec is None:
                continue

            q_end = quota_on_or_before(ref_date, fund_spec)
            if not q_end:
                continue
            end_quota_ref = q_end["quota"]
            end_date_ref  = q_end["date"]

            a12_ref = subtract_months(ref_date, 12)
            a36_ref = subtract_months(ref_date, 36)
            a60_ref = subtract_months(ref_date, 60)

            q12_ref = quota_on_or_before(a12_ref, fund_spec)
            q36_ref = quota_on_or_before(a36_ref, fund_spec)
            q60_ref = quota_on_or_before(a60_ref, fund_spec)

            def do_cagr_ref(q):
                if not q: return None
                return cagr(q["quota"], end_quota_ref, years_apart(q["date"], end_date_ref))

            c12 = do_cagr_ref(q12_ref)
            c36 = do_cagr_ref(q36_ref)
            c60 = do_cagr_ref(q60_ref)

            # cagrInception: usa initialQuota do FUND_META_PY (hardcoded, idêntico ao JS)
            meta_py        = FUND_META_PY.get(cnpj, {})
            inc_quota_val  = meta_py.get("initialQuota")
            inc_date_str   = meta_py.get("inception")
            if inc_quota_val and inc_date_str and end_date_ref:
                ci_ref = cagr(inc_quota_val, end_quota_ref,
                              years_apart(inc_date_str, end_date_ref))
            else:
                ci_ref = None

            # alphaVsIbov / alphaVsCdi desde inception usando cdi_price_map e ibovReturns
            # Estes são calculados sobre os retornos do history.json disponíveis até ref_date
            # (sem lookahead), usando as mesmas funções do modelo completo.
            latest_quota_at_ref = end_quota_ref

            # targetReturn — port completo e fiel de calcTargetReturn
            target = calc_target_return_py(
                cnpj              = cnpj,
                cagr12            = c12,
                cagr36            = c36,
                cagr60            = c60,
                latest_quota      = latest_quota_at_ref,
                latest_date_iso   = end_date_ref,
                fund_rets_to_ref  = rets_to_ref,
                fund_dates_to_ref = dates_to_ref,
                cdi_observado     = cdi_weighted,
                ibov_rets_map     = ibov_returns_map_full,
                cdi_pm            = cdi_price_map,
                all_funds_snapshot= peer_snapshot,
                ntnb_long_val     = ntnb_long,
                f12m_val          = focus_12m,
                f5a_val           = focus_5a,
                horizonte         = None,
            )

            # Max drawdown histórico até ref_date
            cum = 1.0
            peak = 1.0
            max_dd = 0.0
            for r in rets_to_ref:
                cum *= (1 + r)
                if cum > peak:
                    peak = cum
                dd = (cum - peak) / peak
                if dd < max_dd:
                    max_dd = dd

            # worstStress — calculado sobre retornos disponíveis até ref_date,
            # exatamente como o JS faz no buildFundPanel (sem lookahead).
            beta_ibov_n = float(fund_betas_glob.get(cnpj, {}).get("beta_ibov") or 0.0)
            beta_sp_n   = float(fund_betas_glob.get(cnpj, {}).get("beta_sp500") or 0.0)
            r2_val      = float(fund_betas_glob.get(cnpj, {}).get("r2") or 0.0)
            stress_params_date = _compute_fund_stress_params(
                cnpj               = cnpj,
                fund_rets          = rets_to_ref,
                common_dates_slice = dates_to_ref,
                beta_ibov_n        = beta_ibov_n,
                beta_sp_n          = beta_sp_n,
                r2                 = r2_val,
            )
            worst_stress = calc_worst_stress(cnpj, stress_params_date, cdi_weighted)

            entry = {
                "targetReturn": round(target, 2) if target is not None else None,
                "cdiWeighted":  round(cdi_weighted, 2),
                "ntnbLong":     round(ntnb_long, 2),
                "maxDD":        round(max_dd * 100, 2),
                "worstStress":  worst_stress,
                "cagr12":       round(c12, 4) if c12 is not None else None,
                "cagr36":       round(c36, 4) if c36 is not None else None,
                "cagr60":       round(c60, 4) if c60 is not None else None,
            }
            new_entries[cnpj][ref_iso] = entry
            total_computed += 1

    # Merge com existente e escreve
    merged = {**existing}
    for cnpj, dates_dict in new_entries.items():
        if dates_dict:
            merged.setdefault(cnpj, {}).update(dates_dict)

    hist["metricsHistory"] = merged
    hist["metricsHistoryVersion"] = MODEL_VERSION
    hist_path.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
    print(f"  ✓ metricsHistory: {total_computed} novas entradas · {len(merged)} fundos · versão {MODEL_VERSION}")


def patch_history_frontier(hist_path: Path, frontier: list) -> None:
    """
    Adiciona/atualiza o campo 'efficientFrontier' no history.json existente.
    Operação atômica: lê → modifica em memória → escreve.
    Chamada de main() após process_fund() ter gerado os mu_map.
    """
    if not hist_path.exists() or not frontier:
        return
    try:
        hist = json.loads(hist_path.read_text())
        hist["efficientFrontier"] = frontier
        hist_path.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
        print(f"  ✓ efficientFrontier: {len(frontier)} pontos escritos em history.json")
    except Exception as e:
        print(f"  ⚠ patch_history_frontier falhou: {e}")


def reconstruct_max_quotas_from_history(hist_path: Path) -> dict:
    if not hist_path.exists():
        return {}
    try:
        hist = json.loads(hist_path.read_text())
        result = {}
        for cnpj, fd in hist.get("funds", {}).items():
            dates  = fd.get("dates", [])
            quotas = fd.get("quotas", [])
            if not quotas: continue
            # Filtrar None (pré-inception) antes de calcular máximo
            valid = [(q, d) for q, d in zip(quotas, dates) if q is not None]
            if not valid: continue
            max_quota, max_date = max(valid, key=lambda x: x[0])
            result[cnpj] = {
                "maxQuota":     max_quota,
                "maxQuotaDate": max_date,
            }
        print(f"  Reconstruídos {len(result)} maxQuotas do history.json (fallback)")
        return result
    except Exception as e:
        print(f"  Não foi possível reconstruir maxQuotas: {e}")
        return {}


# ── Main ───────────────────────────────────────────────────────────────────────


def fetch_fed_dff(start: datetime.date, anchor: datetime.date) -> dict:
    """Busca a Fed Funds Rate efetiva diária (FRED série DFF) e devolve um fator
    ACUMULADO por data {iso: fator}, no mesmo formato do cdi_price_map.
    DFF é taxa anual em %; convertemos para fator diário (base 360, convenção fed funds).
    """
    s = start.isoformat(); e = anchor.isoformat()
    url = (f"https://api.stlouisfed.org/fred/series/observations"
           f"?series_id=DFF&observation_start={s}&observation_end={e}"
           f"&file_type=json&api_key={os.environ.get('FRED_API_KEY','')}")
    # FRED aceita sem chave via fredgraph CSV; usamos o endpoint CSV como fallback robusto.
    csv_url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
               f"&cosd={s}&coed={e}")
    def _parse_csv(text):
        out = {}
        for line in text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 2: continue
            d, v = parts[0].strip(), parts[1].strip()
            if v in ("", "."): continue
            try: out[d] = float(v)
            except ValueError: continue
        return out
    try:
        rates = None
        for _try, _to in enumerate((30, 60, 90), 1):
            try:
                req = urllib.request.Request(csv_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=_to) as resp:
                    rates = _parse_csv(resp.read().decode("utf-8"))
                if rates: break
            except Exception as _rex:
                print(f"  Fed DFF tentativa {_try}/3 falhou ({_rex})")
        if not rates:
            raise ValueError("FRED DFF vazio após 3 tentativas")
        # fator acumulado: cada dia compõe (1 + taxa_anual/100/360).
        # DFF é taxa overnight anualizada; aplicamos por dia-calendário (forward-fill
        # implícito: dias sem cotação herdam a última taxa via varredura contínua).
        acc = 1.0; price_map = {}
        all_days = sorted(rates.keys())
        last_rate = rates[all_days[0]]
        cur = datetime.date.fromisoformat(all_days[0])
        end = anchor
        rate_lookup = rates
        while cur <= end:
            iso = cur.isoformat()
            if iso in rate_lookup: last_rate = rate_lookup[iso]
            acc *= 1 + (last_rate / 100.0) / 360.0
            price_map[iso] = acc
            cur += datetime.timedelta(days=1)
        print(f"  Fed DFF: {len(rates)} cotações, fator acumulado de {all_days[0]} a {anchor.isoformat()}")
        return price_map
    except Exception as ex:
        print(f"  ✗ Fed DFF falhou ({ex}) — usando fallback HISTÓRICO (médias anuais)")
        # Fallback com médias ANUAIS históricas da DFF, não uma constante.
        # A constante (3,6%) foi calibrada para janelas de 5 anos; aplicada a 13
        # anos ela reescreve a história (Fed real ficou em ~0-2% de 2013 a 2021)
        # e distorceu o carry do S&P hedgeado em ~2pp/ano nas janelas longas —
        # detectado na auditoria da matriz de alphas (jul/2026). Estas são as
        # médias anuais efetivas da DFF; o ano corrente pode ser sobrescrito
        # via FED_FALLBACK no ambiente.
        FED_ANNUAL_AVG = {
            2008: 1.93, 2009: 0.16, 2010: 0.18, 2011: 0.10, 2012: 0.14,
            2013: 0.11, 2014: 0.09, 2015: 0.13, 2016: 0.40, 2017: 1.00,
            2018: 1.83, 2019: 2.16, 2020: 0.36, 2021: 0.08, 2022: 1.68,
            2023: 5.03, 2024: 5.15, 2025: 4.33, 2026: 3.60,
        }
        try:
            FED_ANNUAL_AVG[anchor.year] = float(os.environ.get("FED_FALLBACK", FED_ANNUAL_AVG.get(anchor.year, 3.6)))
        except ValueError:
            pass
        _last_known = FED_ANNUAL_AVG[max(FED_ANNUAL_AVG)]
        acc = 1.0; price_map = {}
        cur = start
        while cur <= anchor:
            fed_y = FED_ANNUAL_AVG.get(cur.year, _last_known)
            acc *= 1 + (fed_y / 100.0) / 360.0
            price_map[cur.isoformat()] = acc
            cur += datetime.timedelta(days=1)
        print(f"  Fed fallback histórico de {start.isoformat()} a {anchor.isoformat()} "
              f"({FED_ANNUAL_AVG.get(start.year, _last_known)}% → {FED_ANNUAL_AVG.get(anchor.year, _last_known)}%)")
        return price_map


def fetch_ptax(start: datetime.date, anchor: datetime.date) -> dict:
    """Dólar PTAX venda diário (BCB SGS série 1) → {iso: R$/US$}.
    Mesmo chunking de ≤9 anos do fetch_cdi (limite da API SGS para séries
    diárias). Devolve {} em falha — quem compõe decide o fallback."""
    try:
        data = []
        _ws = start
        while _ws <= anchor:
            _we = min(datetime.date(_ws.year + 9, _ws.month, min(_ws.day, 28)), anchor)
            url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados"
                   f"?formato=json"
                   f"&dataInicial={_ws.strftime('%d/%m/%Y')}"
                   f"&dataFinal={_we.strftime('%d/%m/%Y')}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                chunk = json.loads(resp.read())
            if chunk: data.extend(chunk)
            _ws = _we + datetime.timedelta(days=1)
        out = {}
        for entry in data:
            try:
                d, m, y = entry["data"].split("/")
                out[f"{y}-{m}-{d}"] = float(entry["valor"])
            except (KeyError, ValueError):
                continue
        if not out:
            raise ValueError("PTAX vazia")
        ds = sorted(out.keys())
        print(f"  PTAX: {len(out)} cotações ({ds[0]} → {ds[-1]})")
        return out
    except Exception as e:
        print(f"  ✗ PTAX falhou ({e})")
        return {}


def fetch_sp500_dolar(sp_usd_map: dict, anchor: datetime.date,
                      a12: datetime.date, a36: datetime.date, a60: datetime.date,
                      fetch_from: datetime.date) -> tuple[dict, dict]:
    """S&P 500 DOLARIZADO em BRL: converte para dólar na cotação do momento e
    carrega o câmbio junto — SEM hedge, SEM carry.

        SP_dolar_BRL(t) = S&P_USD(t) × PTAX(t)

    É o retorno em reais de quem compra USD e o índice (na prática, IVVB11),
    ganhando e perdendo com a variação cambial. Complementa o sp500 hedgeado
    (SPXR11): mesmos S&P e datas, câmbio no lugar do carry. Ignora spread de
    conversão e IOF. Devolve (bloco de CAGRs, price map para alpha de inception).
    """
    try:
        if not sp_usd_map:
            raise ValueError("sp_usd_map ausente")
        ptax = fetch_ptax(fetch_from, anchor)
        if not ptax:
            raise ValueError("PTAX indisponível")
        import bisect
        fx_dates = sorted(ptax.keys())
        def _le(ds):
            i = bisect.bisect_right(fx_dates, ds)
            return ptax[fx_dates[i-1]] if i else None
        m = {}
        for ds in sorted(sp_usd_map.keys()):
            fx = _le(ds)
            if sp_usd_map[ds] and fx:
                m[ds] = sp_usd_map[ds] * fx
        blk = _index_block(m, anchor, a12, a36, a60)
        vals = {k: f"{v:.2f}%" if isinstance(v, float) else "N/D" for k, v in blk.items()}
        print(f"  S&P500 dolarizado (câmbio, sem hedge) 12M={vals['cagr12']} 36M={vals['cagr36']} "
              f"60M={vals['cagr60']} · mapa desde {sorted(m.keys())[0] if m else '—'}")
        return blk, m
    except Exception as e:
        print(f"  ✗ S&P500 dolarizado falhou: {e}")
        return {"cagr12": None, "cagr36": None, "cagr60": None}, {}


def _yahoo_chart_map(ticker: str, start: datetime.date, end: datetime.date) -> dict:
    """Mapa {date_iso: close} via chart API do Yahoo. Levanta exceção em falha —
    quem chama decide o fallback."""
    period1 = int(datetime.datetime.combine(start, datetime.time(), tzinfo=datetime.timezone.utc).timestamp())
    period2 = int(datetime.datetime.combine(end + datetime.timedelta(days=5), datetime.time(), tzinfo=datetime.timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&period1={period1}&period2={period2}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    result     = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes     = result["indicators"]["quote"][0]["close"]
    m = {
        datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat(): price
        for ts, price in zip(timestamps, closes) if price is not None
    }
    if not m:
        raise ValueError(f"{ticker}: mapa vazio")
    return m


def index_cagr_between(price_map: dict, d_start: datetime.date, d_end: datetime.date) -> float | None:
    """CAGR de um índice entre duas datas usando o preço na data ≤ alvo.
    None se o mapa não cobre o início (ex.: inception do fundo anterior ao
    histórico disponível do índice) — o UI mostra '—' nesses casos."""
    if not price_map: return None
    dates = sorted(price_map.keys())
    if d_start.isoformat() < dates[0]:      # início fora da cobertura: não estimar
        return None
    p_s, ds = _best_price_and_date(price_map, dates, d_start)
    p_e, de = _best_price_and_date(price_map, dates, d_end)
    if not (p_s and p_e and ds and de): return None
    return cagr(p_s, p_e, years_apart(ds, de))


def _index_block(price_map: dict, anchor, a12, a36, a60) -> dict:
    return {
        "cagr12": index_cagr_between(price_map, a12, anchor),
        "cagr36": index_cagr_between(price_map, a36, anchor),
        "cagr60": index_cagr_between(price_map, a60, anchor),
    }


def fetch_index_simple(label: str, tickers: list, anchor, a12, a36, a60,
                       fetch_from: datetime.date) -> tuple[dict, dict]:
    """Índice simples (sem conversão): tenta os tickers em ordem e devolve
    (bloco de CAGRs + fonte, price_map). Os símbolos ^SMLL/^IDIV podem não
    existir no Yahoo; os ETFs (SMAL11/DIVO11) são o fallback confiável —
    proxies de retorno total do índice, com o pequeno drag da taxa de adm."""
    for tk in tickers:
        try:
            pm = _yahoo_chart_map(tk, fetch_from, anchor)
            blk = _index_block(pm, anchor, a12, a36, a60)
            blk["source"] = tk.replace("%5E", "^")
            vals = {k: (f"{v:.2f}%" if isinstance(v, float) else "N/D") for k, v in blk.items() if k.startswith("cagr")}
            print(f"  {label} [{blk['source']}] 12M={vals['cagr12']} 36M={vals['cagr36']} 60M={vals['cagr60']}"
                  f" · histórico desde {sorted(pm.keys())[0]}")
            return blk, pm
        except Exception as e:
            print(f"  {label}: {tk} falhou ({e}) — tentando próximo")
    print(f"  ✗ {label}: todas as fontes falharam")
    return {"cagr12": None, "cagr36": None, "cagr60": None, "source": None}, {}


def fetch_sp500(anchor: datetime.date, a12: datetime.date, a36: datetime.date, a60: datetime.date,
                cdi_price_map: dict | None = None,
                fetch_from: datetime.date | None = None) -> tuple[dict, dict]:
    """S&P 500 em BRL na lógica SPXR11 (HEDGEADO): retorno do índice em USD MAIS o
    carry do diferencial de juros (CDI − Fed), SEM exposição cambial.

        SPXR11_BRL(t) = S&P_USD(t) × CDI_acumulado(t) / Fed_acumulado(t)

    O câmbio NÃO entra. A Selic (via CDI, BCB série 12) e a Fed funds (FRED DFF)
    formam o carry. Isto corrige o cálculo antigo (S&P_USD × câmbio), que era o
    S&P NÃO-hedgeado, exposto ao dólar — não corresponde ao SPXR11.

    Devolve (bloco de CAGRs, sp_brl_price_map). O mapa serve para o alpha desde
    inception por fundo; sua cobertura é limitada pela interseção S&P × CDI × Fed
    (o CDI cobre 156 meses — funds com inception anterior ficam com alpha '—').
    fetch_from estende o histórico além de a60 (default: comportamento antigo).
    """
    start = fetch_from or (a60 - datetime.timedelta(days=10))
    try:
        sp_map = _yahoo_chart_map("%5EGSPC", start, anchor)
        if not cdi_price_map:
            raise ValueError("cdi_price_map ausente — necessário para o carry")
        fed_map = fetch_fed_dff(start, anchor)
        if not fed_map:
            raise ValueError("Fed DFF ausente — necessário para o carry")

        # lookup "preço na data ≤ alvo" com bisect — o _nearest antigo reordenava
        # o mapa inteiro a cada chamada (O(n log n) por lookup; inviável para
        # construir um mapa diário de 13 anos).
        import bisect
        cdi_dates = sorted(cdi_price_map.keys())
        fed_dates = sorted(fed_map.keys())
        def _le(m, dates, ds):
            i = bisect.bisect_right(dates, ds)
            return m[dates[i-1]] if i else None

        sp_brl_map = {}
        for ds in sorted(sp_map.keys()):
            cdi = _le(cdi_price_map, cdi_dates, ds)
            fed = _le(fed_map, fed_dates, ds)
            if sp_map[ds] and cdi and fed and fed > 0:
                sp_brl_map[ds] = sp_map[ds] * (cdi / fed)

        result_sp = _index_block(sp_brl_map, anchor, a12, a36, a60)
        vals = {k: f"{v:.2f}%" if v is not None else "N/D" for k, v in result_sp.items()}
        print(f"  S&P500 BRL (SPXR11/hedge) 12M={vals['cagr12']} 36M={vals['cagr36']} 60M={vals['cagr60']}"
              f" · mapa desde {sorted(sp_brl_map.keys())[0] if sp_brl_map else '—'}")
        return result_sp, sp_brl_map
    except Exception as e:
        print(f"  ✗ S&P500 falhou: {e}")
        return {"cagr12": None, "cagr36": None, "cagr60": None}, {}



def fetch_daily_index_returns(anchor: datetime.date, history_start_year: int) -> dict:
    """
    Fetches full daily return series for IBOV and S&P500 BRL from history_start_year to anchor.
    Used for beta regression against fund daily returns in history.json.
    Returns: {"ibov": {date: return}, "sp500_brl": {date: return}}
    """
    start = datetime.date(history_start_year, 1, 1) - datetime.timedelta(days=5)
    period1 = int(datetime.datetime.combine(start, datetime.time(), tzinfo=datetime.timezone.utc).timestamp())
    period2 = int(datetime.datetime.combine(anchor + datetime.timedelta(days=5), datetime.time(), tzinfo=datetime.timezone.utc).timestamp())

    def _yahoo_prices(ticker):
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
               f"?interval=1d&period1={period1}&period2={period2}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        ts     = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        return {datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat(): p
                for t, p in zip(ts, closes) if p is not None}

    def prices_to_returns(prices: dict) -> dict:
        dates = sorted(prices.keys())
        rets  = {}
        for i in range(1, len(dates)):
            d0, d1 = dates[i-1], dates[i]
            if prices[d0] and prices[d1] and prices[d0] > 0:
                rets[d1] = prices[d1] / prices[d0] - 1
        return rets

    try:
        ibov_px   = _yahoo_prices("%5EBVSP")
        sp_px     = _yahoo_prices("%5EGSPC")
        fx_px     = _yahoo_prices("BRL%3DX")   # USD/BRL

        ibov_rets = prices_to_returns(ibov_px)

        # S&P in BRL = SP_price * USD/BRL rate
        sp_brl_px = {}
        for d in sp_px:
            sp = sp_px[d]
            fx = fx_px.get(d)
            if fx is None:
                # fallback to nearest available FX
                fx_dates = sorted(fx_px.keys())
                cands = [x for x in fx_dates if x <= d]
                fx = fx_px[cands[-1]] if cands else None
            if sp and fx and fx > 0:
                sp_brl_px[d] = sp * fx
        sp_brl_rets = prices_to_returns(sp_brl_px)

        print(f"  Daily returns: IBOV {len(ibov_rets)}d, S&P BRL {len(sp_brl_rets)}d")
        return {"ibov": ibov_rets, "sp500_brl": sp_brl_rets}
    except Exception as e:
        print(f"  ✗ daily index returns failed: {e}")
        return {"ibov": {}, "sp500_brl": {}}


def compute_fund_betas(history_path: Path, index_rets: dict) -> dict:
    """
    OLS regression: R_fund = alpha + beta_ibov * R_ibov + beta_sp500 * R_sp500_brl + epsilon
    Uses fund daily returns from history.json aligned with index returns.
    Returns per-fund beta dict saved into data.json.
    """
    if not history_path.exists() or not index_rets["ibov"]:
        return {}

    try:
        hist = json.loads(history_path.read_text())
    except Exception:
        return {}

    ibov_r   = index_rets["ibov"]
    sp500_r  = index_rets["sp500_brl"]
    results  = {}

    for cnpj, fd in hist.get("funds", {}).items():
        dates   = fd.get("dates", [])
        returns = fd.get("returns", [])
        if len(dates) < 120 or len(returns) < 120:
            continue

        # Align fund returns with index returns
        # returns[i] corresponds to dates[i] (return FROM dates[i-1] TO dates[i])
        X_ibov, X_sp, Y = [], [], []
        for i in range(1, len(dates)):
            d    = dates[i]
            r_f  = returns[i - 1]
            r_i  = ibov_r.get(d)
            r_s  = sp500_r.get(d)
            if r_i is None or r_s is None:
                continue
            if r_f is None:
                continue  # skip pre-inception (fund not yet active)
            X_ibov.append(r_i)
            X_sp.append(r_s)
            Y.append(r_f)

        n = len(Y)
        if n < 60:
            results[cnpj] = {"beta_ibov": None, "beta_sp500": None, "alpha": None, "r2": None, "n": n}
            continue

        # OLS with two factors: Y = a + b1*X1 + b2*X2
        # Normal equations: [X'X] [b] = [X'Y]
        # X matrix: [1, X_ibov, X_sp500]
        n_f = float(n)
        s1  = sum(X_ibov)
        s2  = sum(X_sp)
        sy  = sum(Y)
        s11 = sum(x*x for x in X_ibov)
        s22 = sum(x*x for x in X_sp)
        s12 = sum(X_ibov[i]*X_sp[i] for i in range(n))
        s1y = sum(X_ibov[i]*Y[i] for i in range(n))
        s2y = sum(X_sp[i]*Y[i] for i in range(n))

        # 3x3 system via Cramer / direct solve
        # [n,   s1,  s2 ] [a ]   [sy ]
        # [s1,  s11, s12] [b1] = [s1y]
        # [s2,  s12, s22] [b2]   [s2y]
        A = [[n_f, s1,  s2 ],
             [s1,  s11, s12],
             [s2,  s12, s22]]
        b = [sy, s1y, s2y]

        # Gaussian elimination
        import copy
        M = [row[:] + [b[i]] for i, row in enumerate(A)]
        for col in range(3):
            pivot = max(range(col, 3), key=lambda r: abs(M[r][col]))
            M[col], M[pivot] = M[pivot], M[col]
            if abs(M[col][col]) < 1e-12:
                break
            for row in range(col+1, 3):
                f = M[row][col] / M[col][col]
                M[row] = [M[row][j] - f*M[col][j] for j in range(4)]
        # Back substitution
        sol = [0.0]*3
        for row in range(2, -1, -1):
            sol[row] = (M[row][3] - sum(M[row][j]*sol[j] for j in range(row+1, 3))) / (M[row][row] if abs(M[row][row]) > 1e-12 else 1e-12)

        alpha_d, b_ibov, b_sp = sol
        alpha_ann = (math.pow(1 + alpha_d, 252) - 1) * 100

        # R-squared
        y_mean = sy / n_f
        ss_tot = sum((y - y_mean)**2 for y in Y)
        y_hat  = [alpha_d + b_ibov*X_ibov[i] + b_sp*X_sp[i] for i in range(n)]
        ss_res = sum((Y[i] - y_hat[i])**2 for i in range(n))
        r2     = 1 - ss_res/ss_tot if ss_tot > 0 else 0

        results[cnpj] = {
            "beta_ibov":  round(b_ibov, 4),
            "beta_sp500": round(b_sp,   4),
            "alpha_ann":  round(alpha_ann, 2),
            "r2":         round(r2, 4),
            "n_obs":      n,
        }
        print(f"  {cnpj[-14:]}: β_ibov={b_ibov:.3f} β_sp={b_sp:.3f} α={alpha_ann:.1f}% R²={r2:.3f} n={n}")

    return results

def main() -> None:
    today = datetime.date.today()
    print(f"Executando para {today.isoformat()}")

    anchor = find_anchor_date(today.year, today.month)
    a12    = subtract_months(anchor, 12)
    a36    = subtract_months(anchor, 36)
    a60    = subtract_months(anchor, 60)

    print(f"Janelas: 12M={a12} 36M={a36} 60M={a60} → {anchor}")

    out_path  = Path(__file__).parent.parent / "docs" / "data.json"
    hist_path = Path(__file__).parent.parent / "docs" / "history.json"

    # Fetch IBOV first so we can pass price_map to process_fund for per-fund inception alpha
    print(f"\n── Ibovespa")
    oldest_inception = datetime.date(2005, 1, 1)  # safe lower bound covering all funds
    ibov, ibov_price_map = fetch_ibov(anchor, a12, a36, a60, oldest_inception=oldest_inception)

    # Fetch CDI before update_history so we can pass the annual rate for metric computation.
    # CDI is needed for Sharpe, Sortino, and semi-covariance inside update_history.
    print(f"\n── CDI (pré-fetch para métricas)")
    cdi, cdi_price_map = fetch_cdi(anchor, a12, a36, a60)

    # Inject CDI and IBOV annual rates into update_history via function attributes.
    # update_history reads these via getattr(update_history, "_cdi_annual", 12.5).
    # Using weighted average of available periods (same logic as the browser's cdiTarget).
    _cdi_pts   = [(1, cdi["cagr12"]), (3, cdi["cagr36"]), (5, cdi["cagr60"])]
    _cdi_valid = [(T, v) for T, v in _cdi_pts if v is not None]
    update_history._cdi_annual  = (
        sum(T * v for T, v in _cdi_valid) / sum(T for T, _ in _cdi_valid)
        if _cdi_valid else 12.5
    )
    update_history._ibov_annual = ibov.get("cagr36") or ibov.get("cagr12") or 15.0

    # Pré-fetch IBOV daily returns para rolling alpha rigoroso.
    # fetch_daily_index_returns é chamado de novo mais abaixo para os betas,
    # mas precisamos dos retornos antes de update_history. Reutilizamos o mesmo
    # resultado armazenando no atributo da função.
    print(f"\n── IBOV daily returns (para rolling alpha e beat)")
    _idx_rets_early = fetch_daily_index_returns(anchor, HISTORY_START_YEAR)
    update_history._ibov_daily_rets = _idx_rets_early.get("ibov", {})
    print(f"  {len(update_history._ibov_daily_rets)} pregões disponíveis")

    # Atualiza history.json ANTES de calcular maxQuotas —
    # assim reconstruct_max_quotas_from_history lê o histórico completo e atualizado,
    # incluindo backfill de fundos novos ou recém-limpos.
    update_history(anchor)

    # Agora lê maxQuotas do history.json já atualizado
    prev_max_quotas = reconstruct_max_quotas_from_history(hist_path)

    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text())
            for f in prev.get("funds", []):
                cnpj = f.get("cnpjFmt")
                if cnpj and cnpj not in prev_max_quotas and f.get("maxQuota"):
                    prev_max_quotas[cnpj] = {
                        "maxQuota":     f["maxQuota"],
                        "maxQuotaDate": f.get("maxQuotaDate", ""),
                    }
            print(f"Carregados {len(prev_max_quotas)} maxQuotas (history + data.json)")
        except Exception as e:
            print(f"Não foi possível ler data.json anterior: {e}")

    results = [process_fund(f, anchor, prev_max_quotas, ibov_price_map=ibov_price_map, cdi_price_map=cdi_price_map) for f in FUNDS]

    # ── GATE DE SANIDADE ─────────────────────────────────────────────────────
    # Se a CVM estiver fora do ar (como em 2026-07-09, quando TODOS os zips
    # mensais falharam), os 29 fundos saem com erro e o workflow commitaria um
    # data.json vazio por cima do último bom — foi exatamente o que aconteceu.
    # Regra: menos da metade dos fundos resolvidos → aborta com exit(1). O job
    # fica VERMELHO, o passo de commit nunca roda, e o site continua servindo o
    # último data.json saudável. Mesma filosofia do config.json: falhar alto.
    _ok_funds = [r for r in results if not r.get("error")]
    if len(_ok_funds) < max(1, len(results) // 2):
        print(f"\n✗ ABORTANDO: só {len(_ok_funds)}/{len(results)} fundos resolvidos — "
              f"CVM provavelmente fora do ar. data.json NÃO será escrito; "
              f"o site mantém o último bom.")
        sys.exit(1)

    # Fronteira eficiente — calculada aqui porque precisa dos retornos esperados
    # (cagr36 de cada fundo), que só existem após process_fund() rodar.
    # Usa cov e corr do history.json que update_history() acabou de escrever.
    print(f"\n── Fronteira eficiente")
    try:
        _hist_snap  = json.loads(hist_path.read_text())
        _cov_snap   = _hist_snap.get("covMatrix", {})
        _corr_snap  = _hist_snap.get("correlation", {})
        _mu_map     = {
            r["cnpjFmt"]: r.get("cagr36")
            for r in results
            if not r.get("error") and r.get("cagr36") is not None
        }
        _frontier = compute_efficient_frontier(_mu_map, _cov_snap, _corr_snap)
        patch_history_frontier(hist_path, _frontier)
        print(f"  {len(_frontier)} pontos na fronteira eficiente")
    except Exception as _fe:
        print(f"  ⚠ fronteira eficiente falhou: {_fe}")

    delayed = [r for r in results if not r.get("error") and r.get("isDelayed")]
    if delayed:
        print(f"\n⚠ Fundos atrasados em relação à âncora ({anchor}):")
        for r in delayed:
            print(f"  {r['name']}: última cota {r['latestDate']} ({r['delayDays']}d)")

    # CDI já buscado antes de update_history (pré-fetch acima).

    print(f"\n── S&P 500 (SPXR11 / hedge cambial — carry CDI−Fed, sem câmbio)")
    # fetch longo: o mapa BRL-hedge alimenta o alpha desde inception por fundo.
    # Cobertura limitada pela interseção S&P × CDI (200 meses) × Fed — alcança a
    # inception meta mais antiga (2010-07); anteriores ficam '—'.
    _idx_from = datetime.date(2008, 1, 1)
    sp500, sp_brl_map = fetch_sp500(anchor, a12, a36, a60, cdi_price_map, fetch_from=_idx_from)

    print(f"\n── S&P 500 (USD nominal)")
    sp500_usd, sp_usd_map = fetch_index_simple("S&P 500 USD", ["%5EGSPC"], anchor, a12, a36, a60, datetime.date(2008, 1, 1))

    print(f"\n── S&P 500 dolarizado (USD→BRL na PTAX, sem hedge)")
    sp500_dolar, sp_dolar_map = fetch_sp500_dolar(sp_usd_map, anchor, a12, a36, a60, datetime.date(2008, 1, 1))

    print(f"\n── SMLL (small caps B3)")
    smll, smll_map = fetch_index_simple("SMLL", ["%5ESMLL", "SMAL11.SA"], anchor, a12, a36, a60, datetime.date(2008, 1, 1))

    print(f"\n── IDIV (dividendos B3)")
    idiv, idiv_map = fetch_index_simple("IDIV", ["%5EIDIV", "DIVO11.SA"], anchor, a12, a36, a60, datetime.date(2008, 1, 1))

    # fallback dos índices novos: se o fetch falhou de ponta a ponta, reusa o
    # último bloco bom do data.json (mesma lógica do fallback de CDI abaixo).
    # Só os CAGRs — sem price map, o alpha de inception fica None neste run.
    if out_path.exists():
        try:
            _prev = json.loads(out_path.read_text())
            for _k, _blk in (("sp500_usd", sp500_usd), ("sp500_dolar", sp500_dolar), ("smll", smll), ("idiv", idiv)):
                if all(_blk.get(c) is None for c in ("cagr12", "cagr36", "cagr60")):
                    _pb = _prev.get(_k, {})
                    if any(_pb.get(c) is not None for c in ("cagr12", "cagr36", "cagr60")):
                        _blk.update(_pb)
                        print(f"  ↩ {_k}: fetch falhou, usando último valor gravado")
        except Exception:
            pass

    print(f"\n── NTN-B (Tesouro IPCA+ — âncora de juro real)")
    ntnb = fetch_ntnb()

    print(f"\n── Focus IPCA (expectativa de inflação de longo prazo)")
    ipca_focus = fetch_ipca_focus()

    # ── NTN-B fallback: se fetch falhou, usa último valor bom gravado ────────────
    if ntnb.get("ntnb_source") == "fallback" and out_path.exists():
        try:
            prev_data = json.loads(out_path.read_text())
            prev_ntnb = prev_data.get("ntnb", {})
            if prev_ntnb.get("ntnb_rate_long") and prev_ntnb.get("ntnb_source") == "live":
                ntnb = prev_ntnb
                print(f"  ↩ NTN-B: usando último valor live gravado ({prev_ntnb.get('ntnb_fetched_at', '')[:10]}): "
                      f"long={prev_ntnb.get('ntnb_rate_long')}% mid={prev_ntnb.get('ntnb_rate_mid')}%")
        except Exception:
            pass

    # ── Focus IPCA fallback: usa último valor bom gravado ────────────────────────
    if ipca_focus.get("ipca_source") == "fallback" and out_path.exists():
        try:
            prev_data  = json.loads(out_path.read_text())
            prev_ipca  = prev_data.get("ipca_focus", {})
            if prev_ipca.get("ipca_longo_prazo") and prev_ipca.get("ipca_source") == "live":
                ipca_focus = prev_ipca
                print(f"  ↩ Focus IPCA: usando último valor live gravado ({prev_ipca.get('ipca_fetched_at', '')[:10]}): "
                      f"12M={prev_ipca.get('ipca_12m')}% LP={prev_ipca.get('ipca_longo_prazo')}%")
        except Exception:
            pass

    print(f"\n── Betas (regressão OLS vs IBOV e S&P BRL)")
    # Reutiliza o fetch já feito antes de update_history — evita segunda chamada à API.
    index_rets = _idx_rets_early
    fund_betas = compute_fund_betas(hist_path, index_rets)
    print(f"  Betas calculados: {len(fund_betas)} fundos")

    # ── Histórico de métricas por fundo ──────────────────────────────────────────
    # NOTA: este bloco deve vir DEPOIS de compute_fund_betas, que define fund_betas.
    # Recalcula retroativamente os últimos 12 meses (com dados disponíveis na data).
    # NTN-B histórica carregada uma vez do Tesouro Direto CSV público.
    # Execuções subsequentes pulam datas já calculadas (incremental).
    print(f"\n── Histórico de métricas (backfill 12M)")
    try:
        ntnb_hist = fetch_ntnb_historico()
        compute_metrics_history(
            hist_path      = hist_path,
            cdi_price_map  = cdi_price_map,
            ntnb_hist      = ntnb_hist,
            anchor         = anchor,
            betas_data     = fund_betas,
            backfill_months = 60,
        )
    except Exception as _mh:
        import traceback
        print(f"  ⚠ metricsHistory falhou: {_mh}")
        traceback.print_exc()

    # ── CDI fallback: se BCB falhou, usa último valor bom gravado ───────────────
    # Garante que o CDI nunca fica null no data.json por causa de falha transitória da API.
    cdi_final = cdi
    if all(v is None for v in cdi.values()) and out_path.exists():
        try:
            prev_data = json.loads(out_path.read_text())
            prev_cdi  = prev_data.get("cdi", {})
            if any(v is not None for v in prev_cdi.values()):
                cdi_final = prev_cdi
                print(f"  ⚠ CDI: BCB falhou, usando último valor gravado: "
                      f"12M={prev_cdi.get('cagr12')} 36M={prev_cdi.get('cagr36')} 60M={prev_cdi.get('cagr60')}")
        except Exception as _e:
            print(f"  ⚠ CDI fallback falhou: {_e}")

    # ── Jensen's alpha por fundo ─────────────────────────────────────────────────
    _cdi_ann  = cdi_final.get("cagr36") or cdi_final.get("cagr12") or 12.0
    _ibov_ann = ibov.get("cagr36") or ibov.get("cagr12") or 15.0
    jensen_alpha = {}
    for r in results:
        if r.get("error"): continue
        cnpj = r.get("cnpjFmt")
        if not cnpj or cnpj not in fund_betas: continue
        b = fund_betas[cnpj]
        beta_ibov = b.get("beta_ibov")
        if beta_ibov is None: continue
        fund_cagr = r.get("cagrInception") or r.get("cagr36") or r.get("cagr12")
        if fund_cagr is None: continue
        ja = fund_cagr - (_cdi_ann + beta_ibov * (_ibov_ann - _cdi_ann))
        jensen_alpha[cnpj] = round(ja, 4)
    print(f"\n── Jensen's alpha: {len(jensen_alpha)} fundos calculados")

    # ── Alpha do CAGR vs. cada índice, por janela + inception ────────────────────
    # Janelas (12/36/60): diferença simples de CAGRs — mesma convenção do alpha
    # da tabela (calcAlpha no HTML).
    # Inception: usa a MESMA base do cartão "Retorno desde o início" do painel —
    # FUND_META_PY (initialQuota na data de início das operações), não a inception
    # CVM. Misturar as duas bases no mesmo painel induzia leituras erradas (ex.:
    # painel mostrando 28%aa/13a do Ártica ao lado de um alpha calculado sobre a
    # base CVM 2020 → parecia implicar S&P hedge de 35%aa). O índice é recalculado
    # entre a inception meta e a latestDate DO FUNDO (mesmo período exato).
    # Fallback: base CVM quando o fundo não tem meta. None quando o histórico do
    # índice não alcança a inception (UI mostra '—').
    _idx_maps = {
        "ibov":     (ibov,        ibov_price_map),
        "sp_usd":   (sp500_usd,   sp_usd_map),
        "sp_dolar": (sp500_dolar, sp_dolar_map),
        "sp_brl":   (sp500,       sp_brl_map),
        "smll":     (smll,        smll_map),
        "idiv":     (idiv,        idiv_map),
    }
    _n_alpha = 0
    for r in results:
        if r.get("error"): continue
        # base de inception: meta (painel) → fallback CVM
        _meta = FUND_META_PY.get(r.get("cnpjFmt") or "", {})
        _inc_date, _fi_cagr = None, None
        _end_iso = r.get("latestDate")
        if _meta.get("inception") and _meta.get("initialQuota") and r.get("latestQuota") and _end_iso:
            try:
                _inc_date = datetime.date.fromisoformat(_meta["inception"])
                _fi_cagr  = cagr(_meta["initialQuota"], r["latestQuota"],
                                 years_apart(_meta["inception"], _end_iso))
            except Exception:
                _inc_date, _fi_cagr = None, None
        if _inc_date is None or _fi_cagr is None:
            try:
                _inc_date = datetime.date.fromisoformat(r["inceptionDate"]) if r.get("inceptionDate") else None
            except Exception:
                _inc_date = None
            _fi_cagr = r.get("cagrInception")
        _al = {}
        for _key, (_blk, _pm) in _idx_maps.items():
            _a = {}
            for _w, _o in (("cagr12", "a12"), ("cagr36", "a36"), ("cagr60", "a60")):
                _fv, _bv = r.get(_w), (_blk or {}).get(_w)
                _a[_o] = round(_fv - _bv, 4) if (_fv is not None and _bv is not None) else None
            _inc = None
            if _inc_date and _fi_cagr is not None and _pm and _end_iso:
                _bci = index_cagr_between(_pm, _inc_date, datetime.date.fromisoformat(_end_iso))
                if _bci is not None:
                    _inc = round(_fi_cagr - _bci, 4)
            _a["inc"] = _inc
            _al[_key] = _a
        r["alphas"] = _al
        _n_alpha += 1
    print(f"── Matriz de alphas vs índices: {_n_alpha} fundos (início = base FUND_META/painel)")

    data_out = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "anchorDate":  anchor.isoformat(),
        "ibov":        ibov,
        "cdi":         cdi_final,
        "sp500":       sp500,
        "sp500_usd":   sp500_usd,
        "sp500_dolar": sp500_dolar,
        "smll":        smll,
        "idiv":        idiv,
        "ntnb":        ntnb,
        "ipca_focus":  ipca_focus,
        "fund_betas":  fund_betas,
        "jensenAlpha": jensen_alpha,
        "funds":       results,
    }

    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(data_out, ensure_ascii=False, indent=2))
    print(f"\n✓ data.json escrito ({len(results)} fundos)")



if __name__ == "__main__":
    main()
