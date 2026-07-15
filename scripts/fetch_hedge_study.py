#!/usr/bin/env python3
"""
Estudo permanente: S&P 500 hedgeado (SPXR11) vs dolarizado (SPXI11) para o
investidor brasileiro, século XXI. Gera docs/hedge_study.json.

A pergunta: o carry do diferencial de juros (Selic − Fed) compensa abrir mão da
valorização do dólar? Constrói dois índices mensais em BRL desde 2000:

    SPXI_BRL(t) = S&P_TR_USD(t) × PTAX(t)                 (exposição cambial)
    SPXR_BRL(t) = S&P_TR_USD(t) × CDI_acum(t) / Fed_acum(t) (hedge + carry)

S&P com dividendos reinvestidos (retorno TOTAL). Fontes, na mesma linha do
allocator.py: Shiller (multpl/GitHub), PTAX (BCB SGS 1), Selic (BCB SGS 432),
Fed Funds (FRED DFF). Cada fonte tem fallback embutido para não quebrar o cron.

Saída: matriz de janelas (terminando hoje, fixas históricas, todas as móveis),
distribuição de CAGR, drawdown e win-rate por horizonte.
"""
import os, json, csv, io, datetime, urllib.request, statistics, bisect
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 40

def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")

# ── Selic/Fed policy fallbacks (séries mensais oficiais; exatas por serem passos)
SELIC_CHANGES = [
 (2000,1,19.0),(2000,3,18.5),(2000,6,17.5),(2000,7,16.5),(2001,1,15.25),(2001,3,15.75),
 (2001,4,16.25),(2001,5,16.75),(2001,7,19.0),(2002,2,18.75),(2002,3,18.5),(2002,7,18.0),
 (2002,10,21.0),(2002,11,22.0),(2003,1,25.5),(2003,3,26.5),(2003,6,26.0),(2003,7,24.5),
 (2003,8,22.0),(2003,9,20.0),(2003,10,19.0),(2003,11,17.5),(2003,12,16.5),(2004,4,16.0),
 (2004,9,16.25),(2004,10,16.75),(2004,11,17.25),(2004,12,17.75),(2005,1,18.25),(2005,2,18.75),
 (2005,3,19.25),(2005,5,19.75),(2005,9,19.5),(2005,10,19.0),(2005,11,18.5),(2005,12,18.0),
 (2006,1,17.25),(2006,3,16.5),(2006,4,15.75),(2006,6,15.25),(2006,7,14.75),(2006,9,14.25),
 (2006,10,13.75),(2006,11,13.25),(2007,1,13.0),(2007,4,12.5),(2007,6,12.0),(2007,9,11.25),
 (2008,4,11.75),(2008,6,12.25),(2008,7,13.0),(2008,9,13.75),(2009,1,12.75),(2009,3,11.25),
 (2009,4,10.25),(2009,6,9.25),(2009,7,8.75),(2010,4,9.5),(2010,6,10.25),(2010,7,10.75),
 (2011,1,11.25),(2011,3,11.75),(2011,4,12.0),(2011,6,12.25),(2011,7,12.5),(2011,9,12.0),
 (2011,10,11.5),(2011,11,11.0),(2012,1,10.5),(2012,3,9.75),(2012,4,9.0),(2012,5,8.5),
 (2012,7,8.0),(2012,8,7.5),(2012,10,7.25),(2013,4,7.5),(2013,5,8.0),(2013,7,8.5),(2013,8,9.0),
 (2013,10,9.5),(2013,11,10.0),(2014,1,10.5),(2014,4,11.0),(2014,10,11.25),(2014,12,11.75),
 (2015,1,12.25),(2015,3,12.75),(2015,4,13.25),(2015,6,13.75),(2015,7,14.25),(2016,10,14.0),
 (2016,11,13.75),(2017,1,13.0),(2017,4,11.25),(2017,7,9.25),(2017,9,8.25),(2017,10,7.5),
 (2017,12,7.0),(2018,1,6.75),(2018,3,6.5),(2019,7,6.0),(2019,9,5.5),(2019,10,5.0),(2019,12,4.5),
 (2020,2,4.25),(2020,3,3.75),(2020,5,3.0),(2020,6,2.25),(2020,8,2.0),(2021,3,2.75),(2021,5,3.5),
 (2021,6,4.25),(2021,8,5.25),(2021,9,6.25),(2021,10,7.75),(2021,12,9.25),(2022,2,10.75),
 (2022,3,11.75),(2022,5,12.75),(2022,6,13.25),(2022,8,13.75),(2023,8,13.25),(2023,9,12.75),
 (2023,11,12.25),(2023,12,11.75),(2024,3,10.75),(2024,5,10.5),(2024,9,10.75),(2024,11,11.25),
 (2024,12,12.25),(2025,1,12.25),(2025,3,14.25),(2025,5,14.75),(2025,6,15.0),(2026,1,15.0),
]
FED_CHANGES = [
 (2000,1,5.5),(2000,2,5.75),(2000,3,6.0),(2000,5,6.5),(2001,1,5.5),(2001,3,5.0),(2001,4,4.5),
 (2001,5,4.0),(2001,6,3.75),(2001,8,3.5),(2001,9,3.0),(2001,10,2.5),(2001,11,2.0),(2001,12,1.75),
 (2002,11,1.25),(2003,6,1.0),(2004,6,1.25),(2004,8,1.5),(2004,9,1.75),(2004,11,2.0),(2004,12,2.25),
 (2005,2,2.5),(2005,3,2.75),(2005,5,3.0),(2005,6,3.25),(2005,8,3.5),(2005,9,3.75),(2005,11,4.0),
 (2005,12,4.25),(2006,1,4.5),(2006,3,4.75),(2006,5,5.0),(2006,6,5.25),(2007,9,4.75),(2007,10,4.5),
 (2007,12,4.25),(2008,1,3.0),(2008,3,2.25),(2008,4,2.0),(2008,10,1.0),(2008,12,0.25),(2015,12,0.5),
 (2016,12,0.75),(2017,3,1.0),(2017,6,1.25),(2017,12,1.5),(2018,3,1.75),(2018,6,2.0),(2018,9,2.25),
 (2018,12,2.5),(2019,8,2.25),(2019,9,2.0),(2019,10,1.75),(2020,3,0.25),(2022,3,0.5),(2022,5,1.0),
 (2022,6,1.75),(2022,7,2.5),(2022,9,3.25),(2022,11,4.0),(2022,12,4.5),(2023,2,4.75),(2023,3,5.0),
 (2023,5,5.25),(2023,7,5.5),(2024,9,5.0),(2024,11,4.75),(2024,12,4.5),(2025,1,4.5),
]

def _expand(changes, upto):
    out={}; cur=None; ci=0; y,m=2000,1
    while (y,m)<=upto:
        while ci<len(changes) and (changes[ci][0],changes[ci][1])<=(y,m):
            cur=changes[ci][2]; ci+=1
        out[f"{y:04d}-{m:02d}"]=cur
        m+=1
        if m>12: m=1; y+=1
    return out

# ── S&P 500 total return (Shiller: price + monthly dividend reinvested) ──────────
def fetch_sp_tr():
    """Retorna {(y,m): tr_index_level}, base 100 no primeiro mês de 2000."""
    src="GitHub/Shiller"
    try:
        raw=_get("https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv")
        rows=list(csv.DictReader(io.StringIO(raw)))
        recs=[]
        for r in rows:
            try:
                y,m,_=r["Date"].split("-")
                recs.append((int(y),int(m),float(r["SP500"]),float(r["Dividend"] or 0)))
            except: pass
        recs=sorted([x for x in recs if x[0]>=1999], key=lambda x:(x[0],x[1]))
        if len(recs)<200: raise ValueError("shiller curto")
    except Exception as e:
        return None, f"FALHOU ({e})"
    tr={}; lvl=100.0; prev=None
    for (y,m,p,d) in recs:
        if prev is not None:
            lvl*=(p+d/12.0)/prev
        tr[f"{y:04d}-{m:02d}"]=lvl
        prev=p
    return tr, src

# ── PTAX USD/BRL mensal (fim de mês) ────────────────────────────────────────────
def fetch_fx():
    # tenta BCB SGS 1 (PTAX venda); fallback Fed H.10 (GitHub datasets)
    try:
        end=datetime.date.today()
        out={}
        ws=datetime.date(1999,12,1)
        while ws<=end:
            we=min(datetime.date(ws.year+9,ws.month,28),end)
            url=(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json"
                 f"&dataInicial={ws.strftime('%d/%m/%Y')}&dataFinal={we.strftime('%d/%m/%Y')}")
            for entry in json.loads(_get(url)):
                d,mo,y=entry["data"].split("/"); out[f"{y}-{mo}-{d}"]=float(entry["valor"])
            ws=we+datetime.timedelta(days=1)
        if len(out)<1000: raise ValueError("ptax curto")
        return _monthly_last(out), "BCB SGS 1 (PTAX)"
    except Exception:
        try:
            raw=_get("https://raw.githubusercontent.com/datasets/exchange-rates/main/data/daily.csv")
            out={}
            for ln in raw.splitlines()[1:]:
                p=ln.split(",")
                if len(p)>=3 and p[1]=="Brazil":
                    try: out[p[0]]=float(p[2])
                    except: pass
            return _monthly_last(out), "Fed H.10 (fallback)"
        except Exception as e:
            return None, f"FALHOU ({e})"

def _monthly_last(daily):
    dates=sorted(daily); out={}
    for ds in dates:
        out[ds[:7]]=daily[ds]   # last write wins = last day of month
    return out

# ── Fed Funds (FRED DFF) e Selic (BCB SGS 432) mensais ──────────────────────────
def fetch_fed():
    try:
        raw=_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")
        rows=list(csv.reader(io.StringIO(raw)))[1:]
        by_m={}
        for d,v in rows:
            try: by_m[d[:7]]=float(v)
            except: pass
        if len(by_m)<200: raise ValueError("dff curto")
        return by_m, "FRED DFF"
    except Exception:
        return _expand(FED_CHANGES, _now_ym_tuple()), "fallback FOMC (passos oficiais)"

def fetch_selic():
    try:
        end=datetime.date.today()
        url=(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json"
             f"&dataInicial=01/01/2000&dataFinal={end.strftime('%d/%m/%Y')}")
        by_m={}
        for e in json.loads(_get(url)):
            d,mo,y=e["data"].split("/"); by_m[f"{y}-{mo}"]=float(e["valor"])
        if len(by_m)<200: raise ValueError("selic curto")
        return by_m, "BCB SGS 432 (Selic meta)"
    except Exception:
        return _expand(SELIC_CHANGES, _now_ym_tuple()), "fallback Copom (passos oficiais)"

def _now_ym_tuple():
    t=datetime.date.today(); return (t.year, t.month)

# ── Helpers de janela ───────────────────────────────────────────────────────────
def ym_add(ym,k):
    y,m=map(int,ym.split("-")); t=y*12+(m-1)+k; return f"{t//12:04d}-{t%12+1:02d}"
def yrs_between(a,b):
    ya,ma=map(int,a.split("-")); yb,mb=map(int,b.split("-")); return ((yb*12+mb)-(ya*12+ma))/12.0
def cagr(s,a,b):
    if a not in s or b not in s: return None
    y=yrs_between(a,b);  return (s[b]/s[a])**(1/y)-1 if y>0 else None
def total(s,a,b):
    return (s[b]/s[a]-1) if (a in s and b in s) else None
def maxdd(s,months):
    peak=-1; mdd=0.0
    for m in months:
        peak=max(peak,s[m]); mdd=min(mdd,s[m]/peak-1)
    return mdd

def build():
    log=[]
    tr, tr_src = fetch_sp_tr();       log.append(f"S&P TR: {tr_src}")
    fx, fx_src = fetch_fx();          log.append(f"PTAX: {fx_src}")
    fed, fed_src = fetch_fed();       log.append(f"Fed: {fed_src}")
    selic, sel_src = fetch_selic();   log.append(f"Selic: {sel_src}")
    if not tr or not fx:
        raise SystemExit("Fontes essenciais indisponíveis: " + " | ".join(log))

    ann=lambda a:(1.0+a/100.0)**(1.0/12.0)
    months=[m for m in sorted(tr) if m>="2000-01" and m in fx and m in fed and m in selic]
    base=months[0]; tr0=tr[base]; fx0=fx[base]
    SPXI={}; SPXR={}; cdi=1.0; fedacc=1.0
    for i,m in enumerate(months):
        SPXI[m]=(tr[m]/tr0)*(fx[m]/fx0)*100.0
        if i>0:
            cdi*=ann(selic[m]); fedacc*=ann(fed[m])
        SPXR[m]=(tr[m]/tr0)*(cdi/fedacc)*100.0
    END=months[-1]

    # BLOCO A — terminando hoje
    ending=[]
    for n in [1,2,3,5,7,10,15,20,26]:
        st=ym_add(END,-12*n)
        if st<base: continue
        ci=cagr(SPXI,st,END); cr=cagr(SPXR,st,END)
        if ci is None: continue
        ending.append({"n":n,"inicio":st,"spxi":round(ci*100,2),"spxr":round(cr*100,2),
                       "delta":round((cr-ci)*100,2),"win":"SPXR" if cr>ci else "SPXI"})

    # BLOCO B — fixas históricas
    fixed_spec=[("2000-01","2002-12"),("2003-01","2007-12"),("2006-01","2012-12"),
        ("2008-01","2008-12"),("2009-01","2012-12"),("2011-01","2015-12"),
        ("2016-01","2019-12"),("2020-01","2021-12"),("2022-01","2024-12"),
        ("2003-01","2010-12"),("2010-01","2020-12"),("2000-01","2010-12"),
        ("2015-01","2020-12"),("2018-01","2022-12")]
    fixed=[]
    for a,b in fixed_spec:
        if a<base or b>END: continue
        ci=cagr(SPXI,a,b); cr=cagr(SPXR,a,b)
        fixed.append({"a":a,"b":b,"anos":round(yrs_between(a,b)),
            "spxi_tot":round(total(SPXI,a,b)*100),"spxr_tot":round(total(SPXR,a,b)*100),
            "spxi_aa":round(ci*100,1),"spxr_aa":round(cr*100,1),"win":"SPXR" if cr>ci else "SPXI"})

    # BLOCO C — todas as móveis: win-rate + Δ, e distribuição
    rolling=[]; dist={}
    for n in [1,3,5,7,10,15,20]:
        diffs=[]; wins=0; ci_l=[]; cr_l=[]
        for i in range(len(months)):
            b=ym_add(months[i],12*n)
            if b not in SPXR: break
            ci=cagr(SPXI,months[i],b); cr=cagr(SPXR,months[i],b)
            if ci is None or cr is None: continue
            diffs.append((cr-ci)*100); ci_l.append(ci*100); cr_l.append(cr*100)
            if cr>ci: wins+=1
        if not diffs: continue
        rolling.append({"n":n,"janelas":len(diffs),"spxr_vence":wins,
            "pct":round(100*wins/len(diffs)),"delta_medio":round(statistics.mean(diffs),2),
            "delta_mediana":round(statistics.median(diffs),2)})
        def pctl(l,p): l=sorted(l); return round(l[int(p*(len(l)-1))],1)
        dist[n]={"spxi":{"min":pctl(ci_l,0),"p10":pctl(ci_l,.1),"p25":pctl(ci_l,.25),
                  "mediana":pctl(ci_l,.5),"p75":pctl(ci_l,.75),"p90":pctl(ci_l,.9),"max":pctl(ci_l,1)},
                 "spxr":{"min":pctl(cr_l,0),"p10":pctl(cr_l,.1),"p25":pctl(cr_l,.25),
                  "mediana":pctl(cr_l,.5),"p75":pctl(cr_l,.75),"p90":pctl(cr_l,.9),"max":pctl(cr_l,1)}}

    carry_tot=cdi/fedacc-1
    yrs=(len(months)-1)/12
    out={
      "meta":{"gerado":datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"inicio":base,"fim":END,
              "meses":len(months),"fontes":log,
              "carry_acum_pct":round(carry_tot*100),"carry_aa_pct":round(((1+carry_tot)**(1/yrs)-1)*100,2),
              "fx_ini":round(fx0,3),"fx_fim":round(fx[END],3),
              "fx_desval_aa_pct":round(((fx[END]/fx0)**(1/yrs)-1)*100,2)},
      "ending":ending,"fixed":fixed,"rolling":rolling,"dist":dist,
      "drawdown":{"spxi":round(maxdd(SPXI,months)*100,1),"spxr":round(maxdd(SPXR,months)*100,1)},
      "series":{"months":months,"SPXI":{m:round(SPXI[m],2) for m in months},
                "SPXR":{m:round(SPXR[m],2) for m in months},
                "TR_USD":{m:round(tr[m]/tr0*100,2) for m in months},
                "FX":{m:round(fx[m],3) for m in months}},
    }
    DOCS.mkdir(exist_ok=True)
    (DOCS/"hedge_study.json").write_text(json.dumps(out,ensure_ascii=False,separators=(",",":")))
    print("✓ hedge_study.json —", " | ".join(log))
    print(f"  {base}→{END} · carry {out['meta']['carry_aa_pct']}%aa · real -{out['meta']['fx_desval_aa_pct']}%aa")
    print(f"  20a rolling: SPXR vence {[r for r in rolling if r['n']==20]}")

if __name__=="__main__":
    build()
