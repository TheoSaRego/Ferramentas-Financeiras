#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""email_render.py — HTML do e-mail semanal do Alocador (shape v9: 2 sinais + 3 baldes)."""

def _money(n):
    try: return "R$ " + format(int(round(n)), ",d").replace(",", ".")
    except Exception: return "—"

def _sig_block(title, sig):
    if sig.get("score") is None:
        return f"<td style='vertical-align:top;width:50%;padding:8px'><div style='font:11px monospace;color:#7a7870'>{title}</div><div style='font:28px Georgia'>—</div></td>"
    rows = ""
    for g, gd in sig["groups"].items():
        rows += f"<div style='font:11px monospace;color:#1a1916;margin-top:6px'>{g}</div>"
        for it in gd["items"]:
            sc = "—" if it["score"] is None else it["score"]
            rows += (f"<div style='font:10px monospace;color:#7a7870;display:flex;"
                     f"justify-content:space-between'><span>{it['key']} · {it['read']}</span><b style='color:#1a1916'>{sc}</b></div>")
    return (f"<td style='vertical-align:top;width:50%;padding:8px'>"
            f"<div style='font:11px monospace;color:#7a7870;text-transform:uppercase;letter-spacing:.1em'>{title}</div>"
            f"<div style='font:30px Georgia;color:{sig['cor']}'>{sig['score']}<span style='font-size:13px;color:#7a7870'>/10</span></div>"
            f"<div style='font:12px monospace;font-weight:bold;color:{sig['cor']}'>{sig['label']}</div>"
            f"<div style='font:10px monospace;color:#7a7870'>confiança {sig['conf']} · regime {sig.get('regime','—')}</div>"
            f"{rows}</td>")

def render_email(out, cfg):
    d = out["decision"]; B = out["brasil_signal"]; U = out["sp_signal"]
    alerts = "".join(
        f"<div style='font:11px monospace;color:{'#e05c2a' if a['level']=='medio' else '#7a7870'};margin:2px 0'>• {a['msg']}</div>"
        for a in out.get("decay_flags", []))
    return f"""<div style="max-width:640px;margin:0 auto;font-family:Georgia,serif;color:#1a1916;background:#f7f6f2;padding:24px">
  <div style="font:11px monospace;color:#7a7870;text-transform:uppercase;letter-spacing:.1em">alocador tático · {out.get('asof','')}</div>
  <h1 style="font-weight:normal;font-size:22px;margin:6px 0 16px">Decisão de deploy: {_money(d['tranche'])}</h1>
  <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid rgba(0,0,0,.08);border-radius:10px">
    <tr>{_sig_block('Brasil · Organon+Ártica', B)}{_sig_block('S&P 500 · SPXR11', U)}</tr>
  </table>
  <div style="background:#fff;border:1px solid rgba(0,0,0,.16);border-radius:10px;padding:16px;margin-top:12px">
    <div style="font:11px monospace;color:#7a7870">caixa atual {round(d['cashpct']*100)}% → piso-alvo {round(d['piso']*100)}%</div>
    <table style="width:100%;margin-top:10px"><tr>
      <td style="width:50%;padding:8px;background:#f7f6f2;border-radius:8px">
        <div style="font:10px monospace;color:#7a7870">→ BRASIL</div><div style="font:20px Georgia">{_money(d['aloc_brasil'])}</div>
        <div style="font:10px monospace;color:#7a7870">alvo {round(d['tgt_brasil']*100)}% · atual {round(d['cur_brasil']*100)}%</div></td>
      <td style="width:50%;padding:8px;background:#f7f6f2;border-radius:8px">
        <div style="font:10px monospace;color:#7a7870">→ S&P</div><div style="font:20px Georgia">{_money(d['aloc_sp'])}</div>
        <div style="font:10px monospace;color:#7a7870">alvo {round(d['tgt_sp']*100)}% · atual {round(d['cur_sp']*100)}%</div></td>
    </tr></table>
    <div style="font:11px monospace;color:#7a7870;margin-top:10px;line-height:1.6">{d['desc']}. Gap geométrico — nunca zera a munição.</div>
  </div>
  <div style="margin-top:12px">{alerts}</div>
  <div style="font:10px monospace;color:#7a7870;font-style:italic;margin-top:14px">
    Leitura do seu próprio material, não recomendação — a decisão é sua.</div>
</div>"""
