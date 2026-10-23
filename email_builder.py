"""Build the daily digest email (HTML + plain text) with matched offers.

Design: dense, scannable one-liner rows — title (linked), match badge,
company · location · salary on a single meta line, skills inline, LLM verdict
inline. Inline CSS only (Gmail strips <style> blocks).
"""

from __future__ import annotations

import html as html_mod
import re

INK = "#16213e"
MUTED = "#5f6772"
FAINT = "#9aa1ab"
LINE = "#e6e9ee"
ACCENT = "#2563eb"
GREEN = "#0e7a3d"
AMBER = "#b45309"


def _g(o, key, default=""):
    if isinstance(o, dict):
        return o.get(key, default)
    return getattr(o, key, default)


def _badge(match: int) -> str:
    color = GREEN if match >= 80 else ACCENT if match >= 60 else AMBER
    return (
        f'<span style="display:inline-block;min-width:44px;padding:2px 6px;'
        f'border-radius:11px;background:{color};color:#fff;font-size:12px;'
        f'font-weight:700;text-align:center">{match}%</span>'
    )


def _loc_short(location: str, work_mode: str) -> str:
    if re.search(r"zdaln|remote", f"{location} {work_mode}", re.I):
        return "Zdalnie"
    if re.search(r"warszawa|warsaw", location or "", re.I):
        return "Wwa" + (" · hybr" if re.search(r"hybryd|hybrid", work_mode or "", re.I) else "")
    return (location or "—")[:18]


def _row(m: dict, idx: int, dream: bool = False, is_new: bool = False) -> str:
    o = m["offer"]
    title = html_mod.escape(_g(o, "title"))
    company = html_mod.escape(_g(o, "company") or "")
    url = html_mod.escape(_g(o, "url"))
    source = html_mod.escape(_g(o, "source") or "")
    salary = _g(o, "salary") or ""
    sal_h = f' · <b style="color:{INK}">{html_mod.escape(salary)}</b>' if salary else ""
    skills = " · ".join((_g(o, "skills") or [])[:5])
    skills_h = f' · <span style="color:{MUTED}">{html_mod.escape(skills)}</span>' if skills else ""
    reason = html_mod.escape(m.get("llm_reason") or "")
    title_html = (
        f'<a href="{url}" style="color:{INK};font-weight:700;text-decoration:none">{title}</a>'
        if url else f'<span style="color:{INK};font-weight:700">{title}</span>'
    )
    if dream:
        bg, bar = "background:#fdf6ec;", "border-left:3px solid #d4a017;"
    elif is_new:
        bg, bar = "background:#eef7ef;", "border-left:3px solid #0e7a3d;"
    else:
        bg, bar = "", "border-left:3px solid transparent;"
    return f"""<tr style="border-bottom:1px solid {LINE};{bg}">
<td style="padding:7px 10px;vertical-align:top;width:34px;color:{FAINT};font-size:12px;font-weight:700;{bar}">{idx}</td>
<td style="padding:7px 4px;vertical-align:top">
  {title_html}<span style="color:{MUTED};font-size:12px"> · {company} · {_loc_short(_g(o,'location'), _g(o,'work_mode'))}{sal_h}{skills_h}</span>
  {f'<div style="color:{GREEN};font-size:11px">🤖 {reason}</div>' if reason else ''}
</td>
<td style="padding:7px 10px;vertical-align:top;text-align:right">{_badge(m['match'])}</td>
</tr>"""


def build_email(matches: list[dict], board_status: dict[str, str], date_str: str) -> tuple[str, str, str]:
    """Return (subject, html_body, plain_text)."""
    from matcher import is_dream_company
    top = matches[:25]
    # dream companies pinned at the top (each group by match desc), then the rest
    dream = [m for m in top if is_dream_company(_g(m["offer"], "company"))]
    normal = [m for m in top if not is_dream_company(_g(m["offer"], "company"))]
    new_normal = [m for m in normal if m.get("is_new")]
    seen_normal = [m for m in normal if not m.get("is_new")]
    rows = "".join(
        _row(m, i + 1, dream=True, is_new=bool(m.get("is_new"))) for i, m in enumerate(dream)
    ) + "".join(
        _row(m, i + 1 + len(dream), dream=False, is_new=True) for i, m in enumerate(new_normal)
    ) + "".join(
        _row(m, i + 1 + len(dream) + len(new_normal), dream=False, is_new=False) for i, m in enumerate(seen_normal)
    )
    n = len(matches)
    remote_n = sum(
        1 for m in matches
        if re.search(r"zdaln|remote", f"{_g(m['offer'], 'location')} {_g(m['offer'], 'work_mode')}", re.I)
    )
    strong_n = sum(1 for m in matches if m["match"] >= 80)
    status_line = " · ".join(
        f"{html_mod.escape(b.split('.')[0])} {html_mod.escape(s.split(' ')[1]) if ' ' in s else ''}".strip()
        for b, s in board_status.items()
    )

    new_n = sum(1 for m in matches if m.get("is_new"))
    subject = f"💼 {n} ofert IT — {date_str}" + (f" ({new_n} NEW)" if new_n else "") + (f" (best {matches[0]['match']}%)" if matches else "")

    empty = (
        f'<tr><td colspan="3" style="padding:24px;text-align:center;color:{MUTED}">'
        f"Brak ofert ≥ progu dopasowania. Do jutra!</td></tr>"
    ) if not top else rows

    html_body = f"""<div style="background:#f4f6f9;padding:16px 8px;font-family:Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:700px;margin:0 auto;background:#fff;border-radius:10px;border:1px solid {LINE}">
<tr><td style="padding:16px 20px 10px 20px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:18px;font-weight:800;color:{INK}">Job Alerts <span style="color:{FAINT};font-size:13px;font-weight:400">{date_str}</span></td>
    <td align="right" style="font-size:12px;color:{MUTED};white-space:nowrap">{strong_n}× ≥80% · {remote_n} zdalnych{f' · <b style="color:{GREEN}">{new_n} nowych</b>' if new_n else ''}</td>
  </tr></table>
</td></tr>
<tr><td style="padding:0 12px 12px 12px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{empty}</table>
</td></tr>
<tr><td style="padding:6px 20px 12px 20px;font-size:10.5px;color:{FAINT};border-top:1px solid {LINE}">
  {status_line} · scraper_app (regex+LLM vs profil kompetencji)
</td></tr>
</table>
</div>"""

    plain = []
    for i, m in enumerate(top, 1):
        o = m["offer"]
        sal = f" | {_g(o, 'salary')}" if _g(o, "salary") else ""
        plain.append(f"{i}. [{m['match']}%] {_g(o, 'title')} — {_g(o, 'company')} ({_g(o, 'location')}{sal}) {_g(o, 'url')}")
    plain_text = f"Job Alerts — {date_str}\n{n} matches.\n\n" + "\n".join(plain)
    return subject, html_body, plain_text
