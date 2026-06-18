#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 ALOCADOR  v11  —  três baldes (Caixa · Brasil · S&P), dois sinais independentes
================================================================================

Reescrita conceitual pedida pelo Théo. Em vez de timar sleeves específicos (e o
erro de desplegar SPXR11 pelo breadth do IBOV), agora:

  • Universo = 3 baldes:  CAIXA (CDI) · BRASIL (Organon+Ártica) · S&P (SPXR11).
    Single-names (Nu, ALOS) e cripto ficam FORA — são discricionários.
  • DOIS composites de barateza, cada mercado dirigido pelo SEU próprio sinal:
      score_BR : ERP real BR, P/L IBOV, DY, DD IBOV, DD dos fundos, breadth BR, F&G BR
      score_US : ERP real US, P/E S&P, DD S&P, breadth S&P (mesmas regras), F&G CNN
  • CAIXA: banda min/max desejada (parâmetros). O piso-alvo cai conforme a MELHOR
    oportunidade entre os dois mercados. Deploy = λ·(caixa%−piso)·total (gap
    geométrico — nunca zera munição).
  • SPLIT Brasil×S&P por atratividade = (quão abaixo do alvo) × (quão barato).

Server-side: junta tudo num docs/allocation.json e manda o e-mail. A página
docs/alocador.html recalcula ao vivo no navegador a partir desses inputs.

Fontes. Override em config.json só deve existir quando preenchido manualmente
pelo usuário pela tela/config — não como substituto silencioso de um fetch
funcional:
  BR  breadth.json, data.json, ibov_price.json [repo] · Selic/IPCA BCB SGS
      · P/L IBOV: MANUAL (pl_br_override) — a Oceans14 (fonte do gráfico real)
        protege o endpoint de dados com JWT de sessão logada, expira em horas;
        não automatizável sem guardar login como secret. Confira manualmente
        em oceans14.com.br/acoes/historico-pl-bovespa.
      · F&G Brasil: MANUAL (fg_br) — não existe índice oficial público para o
        Brasil (só o da CNN, que é dos EUA). Use breadth/DD ao lado (já na
        tela) pra formar sua própria leitura de sentimento, em vez de um
        proxy sintético que reembalharia o mesmo dado de forma menos clara.
      · DY IBOV: MANUAL (dy_br_override) — sem API gratuita/sem-chave
        confiável para DY agregado do índice.
  US  breadth_us.json [fetch_breadth_us.py, rodando no CI como job próprio —
      se aparecer REVISAR, confira se esse job já rodou pelo menos uma vez no
      GitHub Actions desde o deploy] · P/E e DY: multpl.com primeiro (mesma
      fonte/função que já funciona pro CAPE), SPY/yfinance .info como
      fallback (esse endpoint específico do yfinance é o mais sujeito a
      rate-limit; .download(), usado no breadth, é mais robusto) · CAPE
      multpl.com (Shiller) · juro real FRED DFII10 · Fed FRED DFF · CPI FRED
      CPIAUCSL · F&G CNN dataviz · drawdown ^GSPC
"""
import json, logging, os, re, smtplib, ssl, sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("alocador")
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# ── CONFIG (defaults; sobrescritos por config.json + env) ─────────────────────
DEFAULT_CONFIG = {
    "caixa": 90000.0,
    "brasil": 300000.0,            # valor de mercado hoje em Organon+Ártica (R$)
    "sp": 60000.0,                 # valor de mercado hoje em SPXR11 (R$)
    # total = caixa+brasil+sp (calculado); ou informe "carteira_total" p/ sobrescrever

    # alvos do RISCO (somam 1): default da nossa carteira (~75% Brasil / 25% S&P)
    "alvo_brasil": 0.75, "alvo_sp": 0.25,

    # banda de caixa desejada
    "caixa_max": 0.20,             # quando tudo caro
    "caixa_min": 0.05,             # quando algo barato
    "lambda_deploy": 0.22,
    "municao_minima": 0.05,

    # monitor de decay (só Brasil — S&P é passivo)
    "organon_aum_teto": 1_000_000_000.0,

    "email_to": "theo.fernandes10@gmail.com",

    # overrides (em branco = usa o fetch)
    "pl_br_override": None, "dy_br_override": None,
    "selic_override": None, "ipca_override": None, "fg_br": None, "cape_br": None,
    "pe_us_override": None, "dy_us_override": None, "real_us_override": None,
    "fg_us_override": None, "cape_us_override": None, "fed_override": None,
    "us_cpi_override": None, "override_sinal": None,
}
CNPJ = {"Organon": "49.984.812/0001-08", "Artica": "18.302.338/0001-63"}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    p = ROOT / "config.json"
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
            log.info("config.json carregado")
        except Exception as e:
            log.warning(f"config.json inválido ({e})")
    for env, key in [("ALOC_CAIXA", "caixa"), ("ALOC_BRASIL", "brasil"), ("ALOC_SP", "sp")]:
        if os.getenv(env):
            try: cfg[key] = float(os.getenv(env))
            except Exception: pass
    cfg["carteira_total"] = cfg.get("carteira_total") or (cfg["caixa"] + cfg["brasil"] + cfg["sp"])
    return cfg


# ── leitura dos JSONs do repo ─────────────────────────────────────────────────
def rj(name):
    try: return json.loads((DOCS / name).read_text(encoding="utf-8"))
    except Exception as e: log.warning(f"{name} indisponível ({e})"); return None

def _requests():
    import requests; return requests

def _pick(override, fetch_fn, fetch_label):
    """(valor, fonte) com override (config) tendo precedência sobre o fetch.
    'manual (override)' é classificado como MANUAL na UI — é um valor seu, não um fetch."""
    if override is not None: return override, "manual (override)"
    try: v = fetch_fn()
    except Exception: v = None
    return (v, fetch_label) if v is not None else (None, "indisponível")

def _pick2(override, fetch_fn):
    """Como _pick, mas para fetchers que já retornam (valor, fonte) — evita chamar
    o fetcher duas vezes (1x p/ valor, 1x p/ label) como _pick exigiria."""
    if override is not None: return override, "manual (override)"
    try: v, src = fetch_fn()
    except Exception: v, src = None, "indisponível"
    return (v, src) if v is not None else (None, "indisponível")


# ── FETCHERS (cada um degrada com graça) ──────────────────────────────────────
def fetch_pl_ibov(cfg):
    """P/L do IBOV: GENUINAMENTE MANUAL, sem fetch.
    Histórico: o scraping de oceans14.com.br/acoes/historico-pl-bovespa via
    requests+regex pegava qualquer número 4-40 do HTML/JS de configuração da
    página (o gráfico real é renderizado via JS), gerando leituras erradas
    (ex.: 34.1x quando o P/L real era ~11x). A página tem um endpoint interno
    real (gHistoricoPlBovespa.aspx) que devolve o dado certo, mas exige um JWT
    de sessão logada (sessionId + userAgent + expiração em horas) — não dá pra
    automatizar isso no GitHub Actions sem guardar login/senha como secret e
    ficar refém de qualquer mudança no fluxo de auth deles. Não vale o risco.
    Preencha manualmente em config.json (pl_br_override) ou pela tela; veja o
    P/L atual em oceans14.com.br/acoes/historico-pl-bovespa (à mão)."""
    if cfg.get("pl_br_override"): return float(cfg["pl_br_override"]), "manual (override)"
    return None, "MANUAL — sem fetch (confira em oceans14.com.br/acoes/historico-pl-bovespa)"

def fetch_bcb(series, default=None):
    try:
        r = _requests().get(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series}/dados/ultimos/1?formato=json", timeout=20)
        return float(r.json()[-1]["valor"].replace(",", "."))
    except Exception as e: log.warning(f"BCB {series} ({e})"); return default

def fetch_multpl(slug, default=None):
    """valor corrente de multpl.com via meta-description (o gráfico é JS; o HTML
    estático tem o dado em <meta name='description' content='... is 31.98 ...'>.
    Mantém fallback para o regex antigo caso o formato mude."""
    try:
        r = _requests().get(f"https://www.multpl.com/{slug}",
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code != 200:
            log.warning(f"multpl {slug}: HTTP {r.status_code}"); return default
        # 1) meta description: "... is 31.98, a change of ..."
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'][^"\']*?is\s+([\d]+\.[\d]+)', r.text)
        if m: return float(m.group(1))
        # 2) fallback: qualquer "Current ... número" no body
        m = re.search(r"Current\s*[\d\w\s\.:]*?([\d]+\.[\d]{1,2})", r.text)
        if m: return float(m.group(1))
        log.warning(f"multpl {slug}: HTTP 200 mas nenhum regex bateu (layout pode ter mudado)")
    except Exception as e: log.warning(f"multpl {slug} ({e})")
    return default

def fetch_fred(series, default=None):
    """último valor de uma série FRED via CSV (sem chave). ex.: DFII10, DFF."""
    try:
        r = _requests().get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=20)
        rows = [l for l in r.text.strip().splitlines()[1:] if l.split(",")[-1] not in ("", ".")]
        return float(rows[-1].split(",")[-1])
    except Exception as e: log.warning(f"FRED {series} ({e})"); return default

def fetch_fred_yoy(series, default=None):
    """variação 12m de uma série de nível FRED (ex.: CPIAUCSL → inflação YoY US)."""
    try:
        r = _requests().get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=20)
        vals = [float(l.split(",")[-1]) for l in r.text.strip().splitlines()[1:]
                if l.split(",")[-1] not in ("", ".")]
        if len(vals) >= 13: return round((vals[-1]/vals[-13] - 1) * 100, 2)
    except Exception as e: log.warning(f"FRED yoy {series} ({e})")
    return default

# Tabela de slugs multpl.com + série FRED de fallback para cada indicador US
# Formato: (slug_multpl, serie_fred, usa_yoy_fred)
_US_SOURCES = {
    "pe":       ("s-p-500-pe-ratio",          None,       False),  # sem série FRED de trailing PE
    "cape":     ("shiller-pe",                None,       False),
    "dy":       ("s-p-500-dividend-yield",    None,       False),
    "real":     ("10-year-real-interest-rate", "DFII10",  False),
    "fed":      ("fed-funds-rate",             "DFF",     False),
    "cpi":      ("inflation",                  "CPIAUCSL", True),  # multpl.com/inflation = YoY%
}

def _fetch_us(key, cfg_override=None):
    """Busca um indicador US: multpl primeiro, FRED como fallback.
    Retorna (valor, fonte_label)."""
    if cfg_override is not None: return cfg_override, "manual (override)"
    slug, fred_series, fred_yoy = _US_SOURCES[key]
    v = fetch_multpl(slug)
    if v is not None: return v, "multpl.com"
    if fred_series:
        v = (fetch_fred_yoy(fred_series) if fred_yoy else fetch_fred(fred_series))
        if v is not None: return v, f"FRED {fred_series} (fallback)"
    return None, "indisponível"

def fetch_cnn_fng(default=None):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://edition.cnn.com/markets/fear-and-greed",
            "Origin": "https://edition.cnn.com",
        }
        r = _requests().get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                            headers=headers, timeout=20)
        if r.status_code != 200:
            log.warning(f"CNN F&G: HTTP {r.status_code}"); return default
        return round(float(r.json()["fear_and_greed"]["score"]))
    except Exception as e: log.warning(f"CNN F&G ({e})"); return default

# ── DD do S&P 500 — ATH persistido ────────────────────────────────────────────
# Lógica: sp_ath.json guarda o pico histórico conhecido. A cada execução:
#   1) busca o preço atual do ^GSPC via yfinance (tempo real)
#   2) se preço atual > ATH salvo → atualiza ATH
#   3) calcula DD = (atual / ATH - 1) * 100
# O arquivo sp_ath.json fica no repo (commitado junto com breadth_us.json).
# "Para todo o sempre" = esse arquivo só cresce, nunca é apagado.
SP_ATH_FILE = DOCS / "sp_ath.json"

def fetch_sp_price():
    """Preço mais recente do ^GSPC via yfinance download (mais robusto que .info)."""
    import yfinance as yf
    try:
        g = yf.download("^GSPC", period="5d", auto_adjust=True, progress=False, threads=False)
        c = (g["Close"] if "Close" in g else g).dropna().squeeze()
        return round(float(c.iloc[-1]), 2)
    except Exception as e:
        log.warning(f"^GSPC preço ({e})"); return None

def fetch_sp_ath_and_dd():
    """Calcula DD do S&P com ATH persistido em sp_ath.json.
    Inicializa o ATH com 6 meses de histórico na primeira execução (garante que
    o pico real dos últimos 6m está capturado, sem precisar ir mais longe).
    Retorna (preco_atual, ath, dd_pct, fonte)."""
    import yfinance as yf
    # 1) carrega ATH salvo (ou inicializa)
    ath_data = {}
    if SP_ATH_FILE.exists():
        try: ath_data = json.loads(SP_ATH_FILE.read_text())
        except Exception: pass
    saved_ath = ath_data.get("ath")

    # 2) se não há ATH salvo, inicializa com 6 meses de histórico
    if saved_ath is None:
        log.info("sp_ath.json não existe — inicializando ATH com 6 meses de histórico")
        try:
            g = yf.download("^GSPC", period="6mo", auto_adjust=True, progress=False, threads=False)
            c = (g["Close"] if "Close" in g else g).dropna().squeeze()
            saved_ath = round(float(c.max()), 2)
            log.info(f"ATH inicializado: {saved_ath} (pico dos últimos 6 meses)")
        except Exception as e:
            log.warning(f"falha ao inicializar ATH ({e})"); return None, None, None, "indisponível"

    # 3) preço atual
    cur = fetch_sp_price()
    if cur is None: return None, None, None, "indisponível"

    # 4) atualiza ATH se necessário
    ath = saved_ath
    if cur > ath:
        log.info(f"novo ATH do S&P: {cur} (anterior: {ath})")
        ath = cur

    # 5) persiste
    try:
        SP_ATH_FILE.write_text(json.dumps(
            {"ath": ath, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"não consegui salvar sp_ath.json ({e})")

    dd = round((cur / ath - 1) * 100, 2)
    return cur, ath, dd, "yfinance ^GSPC + ATH persistido"

def ibov_dd():
    j = rj("ibov_price.json") or {}; d = j.get("data", [])
    cl = [r["close"] for r in d if r.get("close")]
    return ((cl[-1]/max(cl)-1)*100, cl[-1], max(cl)) if cl else (None, None, None)

# ── DD dos fundos ─────────────────────────────────────────────────────────────
# Fonte preferida: a SUA planilha de cotas diárias (mesma lógica do seu Apps Script —
# ATH sempre sobre cota_cvm; cota atual = cota_cvm, com fallback p/ cota_site quando a
# CVM ainda não publicou). Fonte de fallback: data.json (só CVM, atrasa 2-3 d.u.).
COTAS_SHEET_ID = "1PT-cCVZsmLzbqm_B6BxoFDWzeqeXm543OT_ZbuIfOgE"

def _gviz_csv(sheet_name):
    url = (f"https://docs.google.com/spreadsheets/d/{COTAS_SHEET_ID}"
           f"/gviz/tq?tqx=out:csv&sheet={sheet_name}")
    r = _requests().get(url, timeout=20)
    import io, csv
    return list(csv.reader(io.StringIO(r.text)))

def _dd_from_rows(rows, cota_idx, fallback_idx=None):
    """ATH = máx(cota base, col cota_idx); cota atual = última linha com cota_idx,
    ou fallback_idx se vazia. Se a atual superar o ATH histórico, ATH := atual (novo topo).
    Replica _calcMetrics() do seu Apps Script."""
    ath = 0.0; last = None
    for row in rows[1:]:
        if not row or cota_idx >= len(row): continue
        try: base = float(row[cota_idx])
        except Exception: base = None
        if base and base > ath: ath = base
        cur = base
        if cur is None and fallback_idx is not None and fallback_idx < len(row):
            try: cur = float(row[fallback_idx])
            except Exception: cur = None
        if cur: last = cur
    if last and last > ath: ath = last
    return (last/ath - 1.0) * 100.0 if (last and ath) else None

def fetch_fund_dd_from_sheet():
    """DD médio (Organon + Ártica) a partir da planilha de cotas diárias."""
    try:
        org = _gviz_csv("organon")  # date, cota_cvm, cota_site
        art = _gviz_csv("artica")   # date, cota, cdi, ibov
        dd_org = _dd_from_rows(org, 1, 2)
        dd_art = _dd_from_rows(art, 1, None)
        dds = [d for d in (dd_org, dd_art) if d is not None]
        if dds:
            return sum(dds)/len(dds), {"organon": dd_org, "artica": dd_art}, "planilha"
    except Exception as e:
        log.warning(f"planilha de cotas ({e})")
    return None, {}, "indisponível"

def fund_dd_avg():
    """Fallback: DD via data.json (CVM, defasado)."""
    d = rj("data.json") or {}; dds = []
    for f in d.get("funds", []):
        nm = (f.get("name") or "").lower()
        if "organon" in nm or "artica" in nm or "ártica" in nm:
            lq, mq = f.get("latestQuota"), f.get("maxQuota")
            if lq and mq: dds.append((lq/mq-1)*100)
    return sum(dds)/len(dds) if dds else None

def fund_alpha():
    d = rj("data.json") or {}; out = {}
    for f in d.get("funds", []):
        nm = (f.get("name") or "").lower()
        k = "Organon" if "organon" in nm else "Artica" if "artica" in nm or "ártica" in nm else None
        if k: out[k] = f.get("alphaVsCdi")
    return out


# ── SCORERS (tier; 0=caro/ruim p/ comprar .. 10=barato/ótimo) ─────────────────
def tier(x, t):
    for lim, s, lab in t:
        if x < lim: return s, lab
    return t[-1][1], t[-1][2]

def s_erp(ey, real):
    if ey is None or real is None: return None, "ERP n/d", None
    e = ey - real
    s, l = tier(e, [(-6,.5,"juro real muito superior"),(-4,2,"juro real bem superior"),
                    (-2,3.5,"juro real claram. superior"),(0,5.5,"juro real levem. superior"),
                    (2,7,"bolsa levem. acima"),(4,8,"bolsa acima do juro real"),
                    (6,9,"bolsa claram. acima"),(1e9,10,"bolsa supera muito")])
    return s, f"ERP real {e:+.1f}pp — {l}", e

def s_pl_br(pl):
    if pl is None: return None, "P/L n/d", None
    s, l = tier(pl, [(7,10,"extrem. barato"),(9,8.5,"muito barato"),(11,7,"barato"),
                     (12,6,"levem. barato"),(13,5.5,"abaixo do justo"),(14,5,"justo"),
                     (15,4,"levem. caro"),(17,2.5,"caro"),(19,1.5,"muito caro"),(1e9,.5,"caro/trough")])
    return s, f"{pl:.1f}x — {l}", pl

def s_pe_us(pe):
    if pe is None: return None, "P/E n/d", None
    s, l = tier(pe, [(13,10,"muito barato"),(16,8.5,"barato"),(18,7,"abaixo da média"),
                     (20,6,"razoável"),(22,5,"justo (~média)"),(25,3.5,"acima da média"),
                     (28,2,"caro"),(32,1,"muito caro"),(1e9,.5,"extremo")])
    return s, f"{pe:.1f}x — {l}", pe

def s_erp_us(ey, real):
    """ERP do S&P sobre o juro real (TIPS), calibrado à história DOS EUA (não do IBOV).
    Para o SPXR11 hedgeado é a barateza relevante vs CDI (a Selic cancela no hedge)."""
    if ey is None or real is None: return None, "ERP n/d", None
    e = ey - real
    s, l = tier(e, [(-1,1.5,"caríssima vs juros (tipo 1999-00)"),(1,3,"cara vs juros"),
                    (2.5,4.5,"abaixo da média (richish)"),(4,6,"~média histórica"),
                    (5.5,7.5,"atrativa"),(7,9,"barata"),(1e9,10,"muito barata (tipo 2009)")])
    return s, f"ERP real {e:+.1f}pp — {l}", e

def s_cape_us(cape):
    """CAPE de Shiller, parâmetros históricos do S&P (média ~17; >30 caro; >35 extremo)."""
    if cape is None: return None, "CAPE n/d", None
    s, l = tier(cape, [(10,10,"muito barato (raro)"),(15,8.5,"barato"),(20,6.5,"na média"),
                       (25,5,"acima da média"),(30,3,"caro"),(35,1.5,"muito caro"),
                       (1e9,0.5,"extremo (tipo 1999/2021)")])
    return s, f"CAPE {cape:.0f}x — {l}", cape

def s_dd_us(dd):
    """drawdown do S&P com parâmetros históricos DO S&P (covid −34%, GFC −57%, 2022 −25%)."""
    if dd is None: return None, "DD n/d", None
    s, l = tier(abs(dd), [(4,1,"no topo/ATH"),(8,2.5,"recuo pequeno"),(12,4,"pullback"),
                          (19,6,"correção"),(28,8,"quase bear/bear"),(40,9.5,"bear severo (tipo 2020)"),
                          (1e9,10,"crash histórico (tipo 2008)")])
    return s, f"{dd:.1f}% — {l}", dd

def s_dy(dy):
    if dy is None: return None, "DY n/d", None
    s, l = tier(-dy, [(-9,10,"altíssimo"),(-7,8.5,"muito atrativo"),(-5.5,7,"atrativo"),
                      (-4,5,"razoável"),(-3,3,"justo"),(-2,1.5,"baixo"),(1e9,0,"muito baixo")])
    return s, f"{dy:.1f}% — {l}", dy

def s_dd(dd):
    if dd is None: return None, "DD n/d", None
    s, l = tier(abs(dd), [(5,1,"próx. ATH"),(10,2.5,"perto do topo"),(17,4.5,"correção leve"),
                          (25,6,"correção relevante"),(35,7.5,"correção forte"),
                          (45,9,"queda severa"),(1e9,10,"crise histórica")])
    return s, f"{dd:.1f}% — {l}", dd

def s_fund_dd(a):
    if a is None: return None, "DD fundos n/d", None
    s, l = tier(abs(a), [(3,1.5,"em/acima do ATH"),(7,2.5,"próx. ATH"),(14,4.5,"correção leve"),
                         (22,6,"correção relevante"),(30,7.5,"correção forte"),
                         (40,9,"queda muito forte"),(1e9,10,"queda severa")])
    return s, f"DD médio {a:.1f}% — {l}", a

def s_breadth(p):
    if p is None: return None, "breadth n/d", None
    pct = p*100
    s, l = tier(pct, [(20,10,"extrema oportunidade"),(35,8.5,"boa oportunidade"),(50,6,"abaixo do meio"),
                      (65,4,"neutro"),(80,2,"momentum positivo"),(1e9,.5,"esticado")])
    return s, f"{pct:.0f}% composto — {l}", pct

def s_mm200(p):
    if p is None: return None, "MM200 n/d", None
    pct = p*100
    s, l = tier(pct, [(10,10,"muito oversold"),(20,8.5,"oversold"),(35,7,"fraqueza técnica"),
                      (50,5,"abaixo da média"),(65,3,"neutro"),(80,1.5,"positivo"),(1e9,.5,"esticado")])
    return s, f"{pct:.0f}% > MM200 — {l}", pct

def s_fg(fg):
    if fg is None: return None, "F&G n/d", None
    s, l = tier(fg, [(10,10,"medo extremo"),(20,9,"medo muito alto"),(30,8,"medo alto"),
                     (40,7,"medo moderado"),(50,6,"leve medo"),(60,5,"neutro"),(70,4,"leve ganância"),
                     (80,3,"ganância"),(90,2,"ganância alta"),(1e9,.5,"ganância extrema")])
    return s, f"{fg:.0f}/100 — {l}", fg

WEIGHTS_BR = {"erp":.22,"pl":.12,"dy":.06,"ibov_dd":.16,"fund_dd":.16,"breadth":.12,"mm200":.06,"fg":.10}
WEIGHTS_US = {"erp":.22,"pe":.14,"cape":.16,"sp_dd":.22,"fg":.26}
GROUPS_BR = {"Valuation":["erp","pl","dy"],"Drawdown":["ibov_dd","fund_dd"],"Breadth":["breadth","mm200"],"Sentimento":["fg"]}
GROUPS_US = {"Valuation":["erp","pe","cape"],"Drawdown":["sp_dd"],"Sentimento":["fg"]}

def composite(scores, weights):
    num = den = 0.0; missing = []
    for k, w in weights.items():
        s = scores.get(k, (None,))[0]
        if s is None: missing.append(k); continue
        num += s*w; den += w
    if den == 0: return None, missing, "BAIXA"
    conf = "ALTA" if den >= .75 else "MÉDIA" if den >= .5 else "BAIXA"
    return round(num/den, 2), missing, conf


# ── DEPLOY + SPLIT ────────────────────────────────────────────────────────────
def cheap_gate(score):
    """f(score): peso de barateza no split. Zera mercado caro (<~neutro), 1.0 quando barato.
    Honra a sua regra: 'S&P caro leva pouco mesmo se sub-alocado'."""
    if score is None: return 0.0
    return max(0.0, min(1.0, (score - 4.5) / 5.5))

def decide(cfg, sBR, sUS):
    T = max(1.0, cfg["carteira_total"]); C = cfg["caixa"]
    B = cfg["brasil"]; S = cfg["sp"]; cashpct = C/T
    if cfg.get("override_sinal") == "HOLD":
        return dict(tranche=0, piso=cashpct, cashpct=cashpct, gap=0, aloc_brasil=0, aloc_sp=0,
                    tgt_brasil=cfg["alvo_brasil"], tgt_sp=cfg["alvo_sp"], cur_brasil=B/T, cur_sp=S/T,
                    desc="override manual: HOLD")
    chBest = (max([x for x in [sBR, sUS] if x is not None] or [0]))/10.0
    piso = cfg["caixa_max"] - (cfg["caixa_max"]-cfg["caixa_min"])*chBest
    piso = max(cfg["municao_minima"], piso)
    gap = cashpct - piso
    # quanto o caixa "quer" desplegar nesta rodada
    want = max(0.0, cfg["lambda_deploy"]*gap*T)
    want = min(want, max(0.0, C - cfg["municao_minima"]*T))
    # alvos em R$ do risco e atratividade COM gate de barateza
    risk_at_target = T*(1-piso)
    tgtB = cfg["alvo_brasil"]*risk_at_target; tgtS = cfg["alvo_sp"]*risk_at_target
    underB = max(0.0, tgtB - B); underS = max(0.0, tgtS - S)
    attrB = underB*cheap_gate(sBR); attrS = underS*cheap_gate(sUS)
    tot = attrB + attrS
    aB = aS = 0.0
    if want > 0 and tot > 0:
        aB = min(want*attrB/tot, underB)   # nunca passa do alvo (disciplina)
        aS = min(want*attrS/tot, underS)
        aB = round(aB, -2); aS = round(aS, -2)
    tranche = aB + aS  # deploy efetivo = só o que tem destino barato+sub-alocado
    rodadas = round(1/cfg["lambda_deploy"]) if cfg["lambda_deploy"] > 0 else None
    if tranche == 0 and want > 0:
        desc = "caixa acima do piso, mas nada barato E abaixo do alvo agora — segura munição"
    else:
        desc = (f"piso-alvo de caixa {piso*100:.0f}% · deploy roteado p/ o mercado barato e "
                f"abaixo do alvo (mercado caro é vetado mesmo se sub-alocado)")
    return dict(tranche=round(tranche, -2), piso=piso, cashpct=cashpct, gap=gap,
                want=round(want, -2), aloc_brasil=aB, aloc_sp=aS, rodadas=rodadas,
                tgt_brasil=tgtB/T, tgt_sp=tgtS/T, cur_brasil=B/T, cur_sp=S/T, desc=desc)

def signal_label(score):
    if score is None: return "INCOMPLETO", "#7a7870"
    if score < 3.5: return "HOLD", "#b4322a"
    if score < 5.5: return "CAUTELOSO", "#c2541f"
    if score < 6.5: return "NEUTRO", "#b07d0a"
    if score < 7.5: return "BOM", "#1a7a52"
    if score < 8.5: return "ATRATIVO", "#15803d"
    if score < 9.5: return "EXCELENTE", "#1d4ed8"
    return "CRISE/RARO", "#6d28d9"


def decay_monitor(cfg):
    flags = []; al = fund_alpha()
    for k, a in al.items():
        if a is not None and a < 0:
            flags.append(("medio", f"{k}: alfa vs CDI 60m {a:+.1f}pp — janela de revisão (ciclo × erosão)."))
    nucleo = cfg["brasil"]/max(1.0, cfg["carteira_total"])
    if nucleo > 0.55:
        flags.append(("medio", f"Brasil (Organon+Ártica) em {nucleo*100:.0f}% — concentração pessoa-chave; "
                               f"direcione aportes ao S&P/caixa se passar do seu conforto."))
    if not flags: flags.append(("ok", "Sem alertas de capacity/concentração."))
    return flags


# ── MAIN ──────────────────────────────────────────────────────────────────────
def build():
    cfg = load_config()
    bBR = (rj("breadth.json") or {}).get("latest", {})
    d = rj("data.json") or {}

    # ── Brasil ──
    pl, pl_src = fetch_pl_ibov(cfg)
    selic, selic_src = _pick(cfg.get("selic_override"), lambda: fetch_bcb(432), "BCB SGS 432")
    ip = d.get("ipca_focus"); ip = ip.get("ipca_12m") if isinstance(ip, dict) else ip
    ipca, ipca_src = _pick(cfg.get("ipca_override"), lambda: fetch_bcb(13522), "BCB SGS 13522")
    if ipca is None and ip is not None: ipca, ipca_src = ip, "data.json (Focus)"
    real_cdi = (selic*0.98 - ipca) if (selic is not None and ipca is not None) else None
    ey_br = (100/pl) if pl else None
    dy_br = cfg.get("dy_br_override")
    dy_br_src = "manual (override)" if dy_br is not None else "MANUAL — sem fetch (use statusinvest)"
    dd_ibov, ibov_cur, ibov_ath = ibov_dd()
    fdd, fdd_detail, fdd_src = fetch_fund_dd_from_sheet()
    if fdd is None:
        fdd = fund_dd_avg(); fdd_src = "data.json (CVM, defasado)" if fdd is not None else "indisponível"
    br_breadth_ok = bBR.get("composite") is not None
    fg_br = cfg.get("fg_br")
    fg_br_src = "manual (override)" if fg_br is not None else "MANUAL — sem fetch (não existe F&G oficial p/ Brasil; veja breadth/DD ao lado p/ formar sua leitura)"
    src_br = {
        "pl": pl_src, "dy": dy_br_src, "selic": selic_src, "ipca": ipca_src,
        "ibov_dd": "ibov_price.json" if dd_ibov is not None else "indisponível",
        "fund_dd": fdd_src,
        "breadth": "breadth.json" if br_breadth_ok else "SEED — revisar",
        "mm200": "breadth.json" if bBR.get("breadth_200") is not None else "SEED — revisar",
        "fg": fg_br_src,
    }
    scBR = {"erp": s_erp(ey_br, real_cdi), "pl": s_pl_br(pl), "dy": s_dy(dy_br),
            "ibov_dd": s_dd(dd_ibov), "fund_dd": s_fund_dd(fdd),
            "breadth": s_breadth(bBR.get("composite")), "mm200": s_mm200(bBR.get("breadth_200")),
            "fg": s_fg(fg_br)}
    score_br, miss_br, conf_br = composite(scBR, WEIGHTS_BR)

    # ── S&P ──
    pe,      pe_src      = _fetch_us("pe",   cfg.get("pe_us_override"))
    cape,    cape_src    = _fetch_us("cape", cfg.get("cape_us_override"))
    dy_us,   dy_us_src   = _fetch_us("dy",   cfg.get("dy_us_override"))
    real_us, real_us_src = _fetch_us("real", cfg.get("real_us_override"))
    fed,     fed_src     = _fetch_us("fed",  cfg.get("fed_override"))
    us_cpi,  us_cpi_src  = _fetch_us("cpi",  cfg.get("us_cpi_override"))
    fg_us_raw = cfg.get("fg_us_override")
    if fg_us_raw is not None:
        fg_us, fg_us_src = fg_us_raw, "manual (override)"
    else:
        fg_us = fetch_cnn_fng()
        fg_us_src = "CNN" if fg_us is not None else "MANUAL — CNN indisponível; preencha você"
    sp_cur, sp_ath, sp_dd, sp_dd_src = fetch_sp_ath_and_dd()
    ey_us = (100/pe) if pe else None
    carry = (selic - fed) if (selic is not None and fed is not None) else None
    src_us = {
        "pe": pe_src, "cape": cape_src, "dy": dy_us_src, "real_us": real_us_src,
        "sp_dd": sp_dd_src, "fg": fg_us_src, "fed": fed_src, "us_cpi": us_cpi_src,
    }
    scUS = {"erp": s_erp_us(ey_us, real_us), "pe": s_pe_us(pe), "cape": s_cape_us(cape),
            "sp_dd": s_dd_us(sp_dd), "fg": s_fg(fg_us)}
    score_us, miss_us, conf_us = composite(scUS, WEIGHTS_US)

    dec = decide(cfg, score_br, score_us)
    sigBR = signal_label(score_br); sigUS = signal_label(score_us)
    flags = decay_monitor(cfg)

    def grp(groups, scores, weights):
        return {g: {"weight": round(sum(weights[k] for k in ks), 4),
                    "items": [{"key": k, "score": scores[k][0], "read": scores[k][1],
                               "weight": weights[k]} for k in ks]} for g, ks in groups.items()}

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "asof": bBR.get("date") or d.get("anchorDate"),
        "carteira_total": cfg["carteira_total"], "caixa": cfg["caixa"],
        "brasil": cfg["brasil"], "sp": cfg["sp"],
        "banda": {"caixa_min": cfg["caixa_min"], "caixa_max": cfg["caixa_max"],
                  "lambda": cfg["lambda_deploy"], "municao_minima": cfg["municao_minima"],
                  "alvo_brasil": cfg["alvo_brasil"], "alvo_sp": cfg["alvo_sp"]},
        "brasil_signal": {"score": score_br, "conf": conf_br, "label": sigBR[0], "cor": sigBR[1],
                          "regime": bBR.get("regime"), "groups": grp(GROUPS_BR, scBR, WEIGHTS_BR)},
        "sp_signal": {"score": score_us, "conf": conf_us, "label": sigUS[0], "cor": sigUS[1],
                      "groups": grp(GROUPS_US, scUS, WEIGHTS_US)},
        "decision": dec,
        # bloco cru p/ a página recalcular client-side
        "inputs": {
            "br_src": src_br, "us_src": src_us,
            "br": {"pl": pl, "ey": ey_br, "dy": dy_br, "selic": selic, "ipca": ipca,
                   "real_cdi": real_cdi, "ibov_dd": dd_ibov, "fund_dd": fdd,
                   "breadth_pct": (bBR.get("composite") or 0)*100, "mm200_pct": (bBR.get("breadth_200") or 0)*100,
                   "fg": cfg.get("fg_br"), "ibov": ibov_cur, "regime": bBR.get("regime"), "pl_src": pl_src,
                   "fund_dd_src": fdd_src, "fund_dd_detail": fdd_detail},
            "us": {"pe": pe, "cape": cape, "ey": ey_us, "dy": dy_us, "real_us": real_us, "sp_dd": sp_dd,
                   "fg": fg_us, "fed": fed, "us_cpi": us_cpi, "carry": carry,
                   "sp_cur": sp_cur, "sp_ath": sp_ath},
        },
        "carry": {"selic": selic, "fed": fed, "diff": carry,
                  "nota": ("Carry do hedge do SPXR11 ≈ Selic − Fed. Em BRL, SPXR11 ≈ retorno do S&P (USD) "
                           "+ carry. Vs CDI o carry quase se anula (excesso ≈ S&P USD − Fed), por isso "
                           "não entra no score — é exibido como contexto da posição.")},
        "weights": {"br": WEIGHTS_BR, "us": WEIGHTS_US},
        "groups_map": {"br": GROUPS_BR, "us": GROUPS_US},
        "decay_flags": [{"level": lv, "msg": m} for lv, m in flags],
    }
    (DOCS / "allocation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"BR score={score_br} {sigBR[0]} · US score={score_us} {sigUS[0]} · "
             f"tranche R${dec['tranche']:,.0f} (BR {dec['aloc_brasil']:,.0f} / S&P {dec['aloc_sp']:,.0f})")
    return out, cfg


def send_email(out, cfg):
    user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    if not (user and pw and cfg.get("email_to")):
        log.info("SMTP não configurado — pulando e-mail."); return
    sys.path.insert(0, str(Path(__file__).parent))
    from email_render import render_email
    html = render_email(out, cfg)
    msg = MIMEMultipart("alternative")
    dB, dU = out["brasil_signal"], out["sp_signal"]; dec = out["decision"]
    msg["Subject"] = (f"[Alocador] {out.get('asof','')} · BR {dB['score']}/{dB['label']} · "
                      f"S&P {dU['score']}/{dU['label']} · deploy R${dec['tranche']:,.0f}")
    msg["From"] = user; msg["To"] = cfg["email_to"]
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw); s.send_message(msg)
    log.info("e-mail enviado")


if __name__ == "__main__":
    out, cfg = build()
    if "--email" in sys.argv:
        try: send_email(out, cfg)
        except Exception as e: log.warning(f"e-mail falhou: {e}")
