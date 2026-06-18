#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_breadth_us.py — breadth do S&P 500 com AS MESMAS REGRAS do breadth do IBOV.

Replica a lógica do app/engine.py (MA20/50/200, composite 0,15/0,35/0,50, regimes
≥0,80/0,60/0,40/0,20) para os constituintes do S&P 500, e ainda devolve o
drawdown do índice (^GSPC). Saída: docs/breadth_us.json no mesmo shape do breadth.json.

Fontes (todas fetchables, free):
  - constituintes: raw.githubusercontent.com/datasets/s-and-p-500-companies (CSV)
  - preços: yfinance (^GSPC + constituintes)

Roda no GitHub Actions (job próprio, ~1x/dia). Mantém um cache em data/.
"""
import json, logging, sys
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np, pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("breadth_us")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"; DATA = ROOT / "data"
OUT = DOCS / "breadth_us.json"
CACHE = DATA / "prices_us.parquet"
CONSTITUENTS_CSV = ("https://raw.githubusercontent.com/datasets/"
                    "s-and-p-500-companies/main/data/constituents.csv")
MA = [20, 50, 200]

# composite e regime — IDÊNTICOS ao breadth do IBOV (app/engine.py + fetch_breadth.py)
def composite(b20, b50, b200):
    return round(0.15*(b20 or 0) + 0.35*(b50 or 0) + 0.50*(b200 or 0), 4)

def classify_regime(b200):
    if b200 is None or (isinstance(b200, float) and np.isnan(b200)): return "unknown"
    if b200 >= 0.80: return "overbought"
    if b200 >= 0.60: return "bull"
    if b200 >= 0.40: return "neutral"
    if b200 >= 0.20: return "bear"
    return "capitulation"


def get_constituents():
    r = _requests().get(CONSTITUENTS_CSV, timeout=30)
    import io, csv
    rows = list(csv.DictReader(io.StringIO(r.text)))
    syms = [row["Symbol"].strip().replace(".", "-") for row in rows if row.get("Symbol")]
    log.info(f"{len(syms)} constituintes do S&P 500")
    return syms

def _requests():
    import requests
    return requests


def fetch_prices(symbols):
    import yfinance as yf
    start = (datetime.today() - timedelta(days=420)).strftime("%Y-%m-%d")  # ~400d p/ MA200+warmup
    raw = yf.download(symbols + ["^GSPC"], start=start, auto_adjust=True,
                      progress=False, threads=True)
    close = raw["Close"] if "Close" in raw else raw
    close.index = pd.to_datetime(close.index).normalize()
    return close.dropna(how="all")


def fetch_breadth_indices():
    """Tenta as séries OFICIAIS de breadth do S&P via Yahoo (índices calculados pelo
    provedor, com composição correta e sem survivorship bias):
        ^S5TW = % > MM20 · ^S5FI = % > MM50 · ^S5TH = % > MM200
    Retorna {20,50,200} em fração [0..1], ou None se Yahoo não servir esses símbolos."""
    import yfinance as yf
    syms = {20: "^S5TW", 50: "^S5FI", 200: "^S5TH"}
    out = {}
    try:
        raw = yf.download(list(syms.values()), period="10d", progress=False, threads=False)
        close = raw["Close"] if "Close" in raw else raw
        for w, s in syms.items():
            col = close[s].dropna() if s in close else None
            if col is None or not len(col):
                return None
            out[w] = round(float(col.iloc[-1]) / 100.0, 4)  # série vem em %
        log.info(f"breadth via índices oficiais S5TW/S5FI/S5TH: {out}")
        return out
    except Exception as e:
        log.warning(f"índices oficiais indisponíveis ({e}) — caio p/ cálculo próprio")
        return None


def compute_from_constituents():
    """Fallback: baixa os ~500 constituintes e computa o breadth (mais pesado/frágil).
    Salva um cache em CACHE (data/prices_us.parquet) — a variável já existia no
    módulo mas nunca era escrita, o que também quebrava o workflow (git add
    falhava com pathspec error porque o arquivo nunca existia; corrigido também
    no update.yml). O cache não é lido de volta hoje (cada chamada baixa fresco),
    mas fica disponível pra debugging/auditoria e para uma futura otimização de
    leitura incremental."""
    syms = get_constituents()
    px = fetch_prices(syms)
    try:
        DATA.mkdir(exist_ok=True)
        px.to_parquet(CACHE)
        log.info(f"cache salvo em {CACHE} ({px.shape[0]} datas x {px.shape[1]} símbolos)")
    except Exception as e:
        log.warning(f"não consegui salvar cache em {CACHE} ({e}) — seguindo sem cache")
    cols = [c for c in px.columns if c != "^GSPC"]
    stk = px[cols]; last = stk.index[-1]; out = {}
    for w in MA:
        ma = stk.rolling(window=w, min_periods=w).mean()
        p, m = stk.loc[last], ma.loc[last]
        valid = p.notna() & m.notna(); n = int(valid.sum())
        out[w] = round(float((p[valid] > m[valid]).sum()) / n, 4) if n >= 5 else None
    out["n"] = len(cols)
    return out





def build():
    # 1) caminho preferido: índices oficiais (leve, composição correta, sem survivorship)
    b = fetch_breadth_indices()
    src = "indices_oficiais"
    n = None
    # 2) fallback: cálculo próprio a partir dos constituintes
    if b is None:
        c = compute_from_constituents()
        b = {20: c[20], 50: c[50], 200: c[200]}; n = c["n"]; src = "calculo_proprio"

    row = {"date": datetime.today().strftime("%Y-%m-%d"),
           "breadth_20": b[20], "breadth_50": b[50], "breadth_200": b[200],
           "n_constituents": n, "source": src}
    row["composite"] = composite(b[20], b[50], b[200])
    row["regime"] = classify_regime(b[200])
    # NOTA: sp_dd foi removido daqui — agora é calculado no allocator.py via ATH
    # persistido em docs/sp_ath.json, independente deste job.

    out = {"latest": row, "count": 1}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"breadth_us.json [{src}]: composite={row['composite']} regime={row['regime']}")
    return out


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        log.error(f"falhou: {e}")
        sys.exit(0)  # nunca derruba o pipeline
