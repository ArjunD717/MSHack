"""Portfolio Decision Centre dashboard.

Runs the deterministic allocation engine and presents the evidence in the
InsureVest-style dark interface.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import os
import sys
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import analyze, build_payload
from portfolio import DEFAULT_FACTS_PATH, DEFAULT_POLICY_PATH, ClientPolicy, load_facts, load_policy, run_validation
from reporting import build_figures, comparison_table, focus_candidate, format_check_value, save_outputs
from yfinance_source import DEFAULT_DATA_DIR, prepare_market_data


DATA_DIR = Path(os.environ.get("MSHACK_DATA_DIR", DEFAULT_DATA_DIR))
FACTS_PATH = Path(os.environ.get("MSHACK_FACTS_PATH", DEFAULT_FACTS_PATH))
POLICY_PATH = Path(os.environ.get("MSHACK_POLICY_PATH", DEFAULT_POLICY_PATH))

TEAL = "#2dd4bf"
CYAN = "#22d3ee"
BLUE = "#4f7cff"
PURPLE = "#a78bfa"
AMBER = "#fbbf24"
RED = "#fb7185"
SLATE = "#94a3b8"
MUTED = "#8ea3bd"

ASSETS = ["CASH", "BIL", "SHY", "IEF"]
ASSET_META = {
    "CASH": ("Cash", "Immediate liquidity", "#94a3b8"),
    "BIL": ("BIL", "1–3m T-bills", TEAL),
    "SHY": ("SHY", "1–3y Treasuries", BLUE),
    "IEF": ("IEF", "7–10y Treasuries", PURPLE),
}
ROLES = {
    "BIL": "Liquidity reserve",
    "SHY": "Short-duration income",
    "IEF": "Liability-horizon test",
}
NAV = ["Portfolio Overview", "Historical Analysis", "Risk & Evidence", "Alternatives & Export"]
PAGE_COPY = {
    "Portfolio Overview": ("Portfolio Decision Centre", "Claims-aware investment analytics and allocation evidence."),
    "Historical Analysis": ("Historical Analysis", "Buy-and-hold replay, stress windows and rate-shock outcomes."),
    "Risk & Evidence": ("Risk & Evidence", "Issuer facts, policy checks and the audit trail behind the allocation."),
    "Alternatives & Export": ("Alternatives & Export", "Other passing allocations, search diagnostics and portable evidence."),
}

st.set_page_config(
    page_title="InsureVest Analytics · Portfolio Decision Centre",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

:root{
  --bg:#060b14; --bg2:#0a1322; --card:rgba(13,24,41,.88); --card2:rgba(8,15,27,.94);
  --line:rgba(148,163,184,.14); --line-soft:rgba(148,163,184,.075);
  --text:#e9f0fa; --muted:#8ea3bd; --muted2:#6f849e;
  --teal:#2dd4bf; --cyan:#22d3ee; --blue:#4f7cff; --purple:#a78bfa;
  --amber:#fbbf24; --red:#fb7185;
}
html, body, [data-testid="stAppViewContainer"]{background:var(--bg); color:var(--text);}
.stApp{
  background:
    radial-gradient(1100px 620px at 7% -10%, rgba(45,212,191,.10), transparent 55%),
    radial-gradient(920px 540px at 102% -4%, rgba(79,124,255,.13), transparent 52%),
    #060b14;
}
[data-testid="stHeader"]{background:transparent; height:0;}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"], #MainMenu, footer{display:none !important;}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"]{display:none !important;}
.block-container{max-width:1500px; padding:.55rem 2.25rem 2.4rem;}
*{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}
h1,h2,h3,h4{font-family:'Space Grotesk','Inter',sans-serif; color:var(--text);}
p{color:inherit;}

/* Top bar ------------------------------------------------------------- */
.topbar-brand{display:flex; align-items:center; gap:11px; padding-top:8px;}
.brand-mark{
  width:33px; height:33px; border-radius:10px; display:grid; place-items:center;
  background:linear-gradient(135deg, rgba(45,212,191,.20), rgba(34,211,238,.05));
  border:1px solid rgba(45,212,191,.40); color:var(--teal);
  box-shadow:0 0 26px rgba(45,212,191,.12);
}
.brand-mark svg{width:19px;height:19px;}
.brand-name{font-family:'Space Grotesk'; font-weight:600; font-size:15.5px; letter-spacing:-.025em; color:#f2f7ff;}
.brand-name em{font-style:normal; color:var(--teal); font-weight:500;}
.meta-bar{display:flex; justify-content:flex-end; align-items:center; gap:6px; padding-top:8px;}
.top-rule{height:1px; background:linear-gradient(90deg, rgba(148,163,184,.18), rgba(148,163,184,.05) 70%, transparent); margin:6px 0 0;}

[data-testid="stRadio"]{margin:0;}
[data-testid="stRadioGroup"]{display:flex; gap:2px; flex-wrap:nowrap; align-items:center;}
[data-testid="stRadioOption"]{
  position:relative; padding:7px 12px !important; border-radius:7px; background:transparent !important;
  transition:background .15s ease;
}
[data-testid="stRadioOption"] > div > div:first-child{display:none !important;}
[data-testid="stRadioOption"] p{
  font-size:12.4px !important; color:#8ea3bd !important; font-weight:500 !important;
  white-space:nowrap; margin:0; line-height:1.2;
}
[data-testid="stRadioOption"]:hover p{color:#dbe7f5 !important;}
[data-testid="stRadioOption"]:has(input:checked)::after{
  content:""; position:absolute; left:12px; right:12px; bottom:-5px; height:2px;
  background:linear-gradient(90deg,var(--teal),var(--cyan)); border-radius:2px;
}
[data-testid="stRadioOption"]:has(input:checked) p{color:#d7fff8 !important; font-weight:600 !important;}

/* Page head ----------------------------------------------------------- */
.pagehead{padding:18px 0 13px;}
.title-line{display:flex; align-items:center; gap:10px; flex-wrap:wrap;}
.pagehead h1{font-size:31px; font-weight:600; letter-spacing:-.04em; margin:0; line-height:1.08;}
.pagehead .sub{color:var(--muted); font-size:13px; margin:5px 0 0; line-height:1.45;}
.asof{text-align:right; color:#647a94; font-size:10.5px; margin-top:7px; line-height:1.5;}
.asof b{color:#a9bdd6; font-weight:500;}

/* Pills, buttons ------------------------------------------------------ */
.pill{
  display:inline-flex; align-items:center; justify-content:center; gap:5px;
  font-size:9.5px; line-height:1; padding:5px 8px; border-radius:999px;
  border:1px solid var(--line); color:var(--muted); background:rgba(148,163,184,.05);
  white-space:nowrap; font-weight:600; letter-spacing:.025em;
}
.pill.ok{color:#8ff7e8; background:rgba(45,212,191,.085); border-color:rgba(45,212,191,.25);}
.pill.warn{color:#fcd34d; background:rgba(251,191,36,.075); border-color:rgba(251,191,36,.24);}
.pill.bad{color:#fda4af; background:rgba(251,113,133,.085); border-color:rgba(251,113,133,.26);}
.pill.soft{color:#a9bdd6; background:rgba(148,163,184,.055);}
.pill.blue{color:#c0d1ff; background:rgba(79,124,255,.11); border-color:rgba(79,124,255,.26);}

div[data-testid="stPopover"] > button, [data-testid="stButton"] > button{
  background:rgba(13,24,41,.72) !important; color:#c7d5e8 !important;
  border:1px solid var(--line) !important; border-radius:9px !important;
  font-size:11.5px !important; font-weight:500 !important; min-height:34px;
  box-shadow:none !important;
}
div[data-testid="stPopover"] > button:hover, [data-testid="stButton"] > button:hover{
  border-color:rgba(45,212,191,.42) !important; color:#eafffb !important; background:rgba(45,212,191,.06) !important;
}
div[data-testid="stPopover"] > button:focus, [data-testid="stButton"] > button:focus{box-shadow:none !important;}

/* KPI cards ----------------------------------------------------------- */
.kpi{
  position:relative; overflow:hidden; min-height:152px;
  border:1px solid var(--line); border-radius:16px; padding:15px 17px 13px;
  background:linear-gradient(180deg, rgba(15,28,47,.98), rgba(8,16,29,.96));
  box-shadow:0 18px 42px rgba(0,0,0,.20);
}
.kpi::before{
  content:""; position:absolute; right:-55px; top:-70px; width:190px; height:190px;
  background:radial-gradient(circle, rgba(45,212,191,.15), transparent 66%);
  pointer-events:none;
}
.kpi-head{display:flex; align-items:center; gap:9px; position:relative; z-index:1;}
.kpi-icon{
  width:29px; height:29px; border-radius:8px; display:grid; place-items:center;
  color:var(--teal); background:rgba(45,212,191,.075); border:1px solid rgba(45,212,191,.17);
}
.kpi-icon svg{width:16px; height:16px;}
.kpi-label{font-size:9.8px; letter-spacing:.075em; text-transform:uppercase; color:var(--muted); font-weight:600;}
.kpi-head .pill{margin-left:auto;}
.kpi-value{
  position:relative; z-index:1; font-family:'Space Grotesk'; font-size:31px; font-weight:600;
  letter-spacing:-.04em; line-height:1.05; margin:14px 0 4px; color:#f2f7ff;
}
.kpi-value.neg{color:#ff9aac;}
.kpi-sub{position:relative; z-index:1; font-size:10.6px; color:#70859e; line-height:1.45;}
.kpi-spark{position:absolute; right:13px; bottom:12px; width:118px; height:34px; opacity:.95; z-index:0;}
.kpi-meter{position:absolute; right:16px; bottom:14px; width:122px; z-index:1;}
.meter{height:5px; border-radius:4px; background:rgba(148,163,184,.14); overflow:hidden;}
.meter span{display:block; height:100%; border-radius:4px; background:linear-gradient(90deg,var(--teal),var(--cyan));}
.meter-caption{font-size:9.2px; color:#647a94; margin-top:5px; text-align:right; white-space:nowrap;}

/* Cards --------------------------------------------------------------- */
[data-testid="stVerticalBlockBorderWrapper"]{
  background:linear-gradient(180deg, rgba(13,24,41,.88), rgba(8,15,27,.94));
  border:1px solid var(--line); border-radius:18px; padding:14px 16px 20px;
  box-shadow:0 24px 55px rgba(0,0,0,.22);
}
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"]{gap:.42rem;}
.card-head{display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin:0 0 2px;}
.card-title{font-family:'Space Grotesk'; font-size:14px; font-weight:600; letter-spacing:-.015em; color:#eaf2fc; line-height:1.25;}
.card-sub{font-size:10.2px; color:#6f849e; margin-top:3px; line-height:1.4;}
.card-foot{font-size:9.8px; color:#5f738c; line-height:1.5; margin-top:3px;}
[data-testid="stMarkdown"]:has(.card-foot), [data-testid="stMarkdown"] > div:has(.card-foot), [data-testid="stElementContainer"]:has(.card-foot){height:auto !important;}

/* Resilience ---------------------------------------------------------- */
.gauge-wrap{display:flex; align-items:center; gap:16px; padding:6px 0 10px;}
.gauge{position:relative; width:148px; height:148px; flex:0 0 148px;}
.gauge svg{width:148px; height:148px; transform:rotate(-90deg);}
.gauge-track{fill:none; stroke:#142236; stroke-width:11;}
.gauge-fill{fill:none; stroke:url(#coverageGrad); stroke-width:11; stroke-linecap:round; filter:drop-shadow(0 0 7px rgba(45,212,191,.28));}
.gauge-center{position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center;}
.gauge-center strong{font-family:'Space Grotesk'; font-size:28px; font-weight:600; color:#f2f7ff; letter-spacing:-.04em; line-height:1;}
.gauge-center span{font-size:8.2px; color:#70859e; text-transform:uppercase; letter-spacing:.09em; margin-top:5px;}
.gauge-copy p{font-size:10.7px; color:#9fb3ca; line-height:1.55; margin:0 0 8px;}
.srow{display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0; border-top:1px solid var(--line-soft); font-size:11.5px; color:#c7d5e8;}
.insight{
  margin-top:9px; padding:10px 11px; border-radius:10px;
  background:rgba(45,212,191,.045); border:1px solid rgba(45,212,191,.13);
  font-size:10.1px; line-height:1.55; color:#9fb3ca;
}
.insight b{color:#c9fdf6; font-weight:600;}

/* Allocation ---------------------------------------------------------- */
.alloc-wrap{display:flex; align-items:center; gap:18px; padding:7px 0 5px;}
.donut{
  position:relative; width:150px; height:150px; border-radius:50%; flex:0 0 150px;
  box-shadow:0 0 0 1px rgba(148,163,184,.07), 0 14px 34px rgba(0,0,0,.28);
}
.donut::after{
  content:""; position:absolute; inset:17px; border-radius:50%;
  background:#0a1422; border:1px solid rgba(148,163,184,.08);
}
.donut-center{position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; z-index:1;}
.donut-center strong{font-family:'Space Grotesk'; font-size:21px; font-weight:600; color:#f2f7ff; letter-spacing:-.04em; line-height:1;}
.donut-center span{font-size:8px; text-transform:uppercase; letter-spacing:.09em; color:#70859e; margin-top:4px;}
.alloc-legend{width:100%;}
.alloc-row{display:grid; grid-template-columns:8px 1fr auto auto; gap:8px; align-items:center; padding:7px 0; border-bottom:1px solid var(--line-soft);}
.alloc-row:last-child{border-bottom:none;}
.alloc-dot{width:7px; height:7px; border-radius:50%;}
.alloc-name b{display:block; font-size:11px; color:#dbe7f5; font-weight:600; line-height:1.2;}
.alloc-name span{display:block; font-size:8.8px; color:#647a94; margin-top:2px;}
.alloc-w{font-size:10.5px; color:#c7d5e8; text-align:right; min-width:34px;}
.alloc-usd{font-size:10.5px; color:#eaf2fc; text-align:right; min-width:55px; font-weight:600;}
.alloc-row.muted{opacity:.38;}

/* Checks / brief ------------------------------------------------------ */
.check-row{display:grid; grid-template-columns:1.18fr 1fr auto; gap:9px; align-items:center; padding:7px 0; border-top:1px solid var(--line-soft);}
.check-row:first-of-type{border-top:none;}
.check-name{font-size:10.8px; color:#c7d5e8; line-height:1.3;}
.check-measure{font-size:10px; color:#9fb3ca; text-align:right; line-height:1.25;}
.check-measure em{display:block; font-style:normal; color:#5f738c; font-size:8.8px; margin-top:1px;}
.check-row .pill{min-width:42px;}
.brief-list{display:flex; flex-direction:column; gap:9px; margin-top:5px;}
.brief-item{display:flex; gap:9px; align-items:flex-start;}
.brief-num{
  width:19px; height:19px; border-radius:6px; flex:0 0 19px; display:grid; place-items:center;
  background:rgba(45,212,191,.075); border:1px solid rgba(45,212,191,.18);
  color:var(--teal); font-size:8.8px; font-weight:700;
}
.brief-item p{margin:0; font-size:10.5px; line-height:1.55; color:#b9c9dd;}
.brief-item b{color:#eaf2fc; font-weight:600;}
.brief-foot{margin-top:10px; padding-top:8px; border-top:1px solid var(--line-soft); font-size:9px; color:#5f738c; line-height:1.5;}

/* Tables -------------------------------------------------------------- */
.etf-wrap{overflow-x:auto;}
.etf{width:100%; border-collapse:collapse; min-width:900px;}
.etf th{
  font-size:9px; text-transform:uppercase; letter-spacing:.085em; color:#647a94; font-weight:600;
  text-align:right; padding:7px 9px; border-bottom:1px solid var(--line); white-space:nowrap;
}
.etf th:first-child{text-align:left;}
.etf td{padding:10px 9px; border-bottom:1px solid var(--line-soft); font-size:11px; color:#c7d5e8; text-align:right; white-space:nowrap;}
.etf td:first-child{text-align:left;}
.etf tr.held{background:rgba(45,212,191,.032);}
.etf tr.held td:first-child{box-shadow:inset 2px 0 0 var(--teal);}
.etf tr:last-child td{border-bottom:none;}
.etf a.ticker{color:#eaf2fc; font-weight:700; text-decoration:none; letter-spacing:.01em;}
.etf a.ticker:hover{color:var(--teal);}
.ticker-name{display:block; font-size:8.8px; color:#647a94; max-width:230px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-top:2px;}
.data-table{width:100%; border-collapse:collapse;}
.data-table th{font-size:9px; text-transform:uppercase; letter-spacing:.08em; color:#647a94; font-weight:600; text-align:right; padding:7px 8px; border-bottom:1px solid var(--line);}
.data-table th:first-child{text-align:left;}
.data-table td{padding:8px; border-bottom:1px solid var(--line-soft); font-size:10.8px; color:#c7d5e8; text-align:right;}
.data-table td:first-child{text-align:left;}
.data-table tr:last-child td{border-bottom:none;}

/* Evidence ------------------------------------------------------------ */
.fund-card{
  height:100%; border:1px solid var(--line); border-radius:14px; padding:13px 14px;
  background:linear-gradient(180deg, rgba(12,23,40,.82), rgba(8,16,29,.9));
}
.fund-top{display:flex; align-items:center; justify-content:space-between; gap:8px;}
.fund-ticker{font-family:'Space Grotesk'; font-size:16px; font-weight:600; color:#f2f7ff;}
.fund-name{font-size:9.5px; color:#6f849e; margin-top:2px; min-height:26px; line-height:1.35;}
.fund-stats{display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:11px 0; padding:9px 0; border-top:1px solid var(--line-soft); border-bottom:1px solid var(--line-soft);}
.fund-stat span{display:block; font-size:8.5px; color:#647a94; text-transform:uppercase; letter-spacing:.06em;}
.fund-stat b{display:block; font-size:12px; color:#eaf2fc; font-weight:600; margin-top:3px;}
.fund-checks .check-row{grid-template-columns:1fr auto; padding:5px 0;}
.fund-checks .check-measure{display:none;}
.fund-source{margin-top:9px; font-size:9.5px;}
.fund-source a{color:#7dd3fc; text-decoration:none;}

/* Expander / inputs / AI --------------------------------------------- */
[data-testid="stExpander"]{border:1px solid var(--line); border-radius:12px; background:rgba(9,18,32,.45);}
[data-testid="stExpander"] summary{font-size:11.5px; color:#c7d5e8;}
[data-testid="stExpander"] p, [data-testid="stExpander"] li{font-size:10.8px; color:#9fb3ca;}
[data-testid="stPopoverBody"]{
  width:780px !important; max-width:96vw; max-height:calc(100vh - 190px) !important;
  background:#0b1524 !important; border:1px solid var(--line) !important; border-radius:16px !important;
  box-shadow:0 28px 80px rgba(0,0,0,.55) !important; padding:12px 16px !important;
}
[data-testid="stPopoverBody"] [data-testid="stVerticalBlock"]{gap:.24rem;}
[data-testid="stPopoverBody"] [data-testid="stForm"]{gap:.24rem;}
[data-testid="stPopoverBody"] [data-testid="stWidgetLabel"]{margin-bottom:0;}
[data-testid="stPopoverBody"] [data-testid="stNumberInput"] input{height:35px !important; padding:0 .55rem !important;}
[data-testid="stPopoverBody"] [data-testid="stExpander"] summary{min-height:1.85rem; padding:4px 8px;}
[data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"] button{min-height:34px !important;}
[data-testid="stPopoverBody"] [data-testid="stForm"]{border:none; padding:0; background:transparent;}
[data-testid="stPopoverBody"] label p{font-size:10.5px; color:#8ea3bd;}
[data-testid="stNumberInput"] input, [data-testid="stTextInput"] input{
  background:#081120 !important; color:#eaf2fc !important;
  border:1px solid var(--line) !important; border-radius:8px !important; font-size:11.5px !important;
}
[data-testid="stNumberInput"] > div, [data-testid="stTextInput"] > div{border:none !important;}
[data-testid="stNumberInput"] button{background:#0f1c2e !important; color:#8ea3bd !important; border:none !important;}
[data-testid="stSelectbox"] > div > div{background:#081120 !important; color:#eaf2fc !important; border:1px solid var(--line) !important;}
[data-baseweb="select"] [data-testid="stMarkdownContainer"] p{font-size:11px;}
[data-baseweb="popover"]{background:#0b1524 !important;}
[data-testid="stFormSubmitButton"] button{
  background:linear-gradient(135deg,#2dd4bf,#22d3ee) !important; color:#04131c !important;
  border:none !important; font-weight:700 !important; border-radius:9px !important;
}
[data-testid="stFormSubmitButton"] button[kind="secondaryFormSubmit"],
[data-testid="stFormSubmitButton"] button[kind="secondary"]{
  background:rgba(13,24,41,.72) !important; color:#c7d5e8 !important;
  border:1px solid var(--line) !important; font-weight:500 !important;
}
[data-testid="stAlert"]{background:rgba(13,24,41,.85); border:1px solid var(--line); color:#c7d5e8; border-radius:12px;}
[data-testid="stCaptionContainer"]{color:#647a94;}
[data-testid="stSpinner"] p{color:#8ea3bd; font-size:12px;}
.footer{margin-top:24px; padding-top:13px; border-top:1px solid var(--line-soft); color:#5f738c; font-size:9.5px; line-height:1.65;}
.footer b{color:#8ea3bd; font-weight:500;}
.mini-stats{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:4px 0 8px;}
.mini-stat{border:1px solid var(--line); border-radius:12px; padding:10px 12px; background:rgba(10,20,34,.55);}
.mini-stat span{display:block; font-size:8.8px; color:#647a94; text-transform:uppercase; letter-spacing:.07em;}
.mini-stat b{display:block; font-family:'Space Grotesk'; font-size:17px; color:#eaf2fc; font-weight:600; margin-top:4px;}
.mini-stat em{display:block; font-style:normal; font-size:8.8px; color:#647a94; margin-top:2px;}
.sel-bar{display:flex; height:8px; border-radius:5px; overflow:hidden; background:#142236; margin:8px 0 10px;}
.sel-bar i{display:block; height:100%;}
.sel-row{display:grid; grid-template-columns:1fr auto auto; gap:10px; align-items:center; padding:6px 0; border-top:1px solid var(--line-soft); font-size:10.8px; color:#c7d5e8;}
.sel-row span{display:flex; align-items:center; gap:7px;}
.sel-row i{width:7px; height:7px; border-radius:50%; display:inline-block;}
.sel-row b{color:#eaf2fc; font-weight:600;}
.sel-row em{font-style:normal; color:#647a94; min-width:34px; text-align:right;}
.sel-note{display:flex; justify-content:space-between; gap:8px; margin-top:8px; padding-top:8px; border-top:1px solid var(--line-soft); font-size:9.4px; color:#647a94;}
.search-hero{display:flex; align-items:flex-end; gap:10px; padding:4px 0 10px;}
.search-hero b{font-family:'Space Grotesk'; font-size:42px; line-height:1; letter-spacing:-.05em; color:#eaf2fc; font-weight:600;}
.search-hero span{font-size:10.5px; color:#6f849e; line-height:1.4; padding-bottom:3px;}
.fail-row{display:grid; grid-template-columns:1fr auto; gap:10px; align-items:center; padding:7px 0; border-top:1px solid var(--line-soft);}
.fail-row span{font-size:10.5px; color:#b9c9dd;}
.fail-row b{font-size:10.5px; color:#eaf2fc; font-weight:600;}
.fail-bar{grid-column:1 / -1; height:3px; border-radius:3px; background:rgba(148,163,184,.12); overflow:hidden; margin-top:-3px;}
.fail-bar i{display:block; height:100%; background:linear-gradient(90deg,#fb7185,#fbbf24); border-radius:3px;}

/* Readability pass: one deliberate step up from the dense financial layout */
.brand-name{font-size:16.5px;}
[data-testid="stRadioOption"] p{font-size:13.4px !important;}
.pill{font-size:10.4px; padding:5px 9px;}
div[data-testid="stPopover"] > button, [data-testid="stButton"] > button{font-size:12.4px !important; min-height:36px;}
.pagehead h1{font-size:33px;}
.pagehead .sub{font-size:14px;}
.asof{font-size:11.4px;}
.kpi{min-height:164px; padding:16px 18px 15px;}
.kpi-label{font-size:10.8px;}
.kpi-value{font-size:34px;}
.kpi-sub{font-size:11.8px;}
.kpi-head .pill{font-size:10px;}
.meter-caption{font-size:10px;}
.card-title{font-size:15.4px;}
.card-sub{font-size:11.3px;}
.card-foot{font-size:10.7px;}
.gauge-center strong{font-size:31px;}
.gauge-center span{font-size:9.2px;}
.gauge-copy p{font-size:11.8px;}
.srow{font-size:12.4px; padding:9px 0;}
.insight{font-size:11.1px; line-height:1.6;}
.alloc-name b{font-size:12px;}
.alloc-name span{font-size:9.7px;}
.alloc-w, .alloc-usd{font-size:11.4px;}
.alloc-usd{min-width:62px;}
.donut-center strong{font-size:23px;}
.donut-center span{font-size:8.9px;}
.check-name{font-size:11.8px;}
.check-measure{font-size:11px;}
.check-measure em{font-size:9.5px;}
.check-row .pill{font-size:10.2px;}
.brief-item p{font-size:11.4px;}
.brief-num{font-size:9.6px;}
.brief-foot{font-size:9.8px;}
.etf th{font-size:9.9px;}
.etf td{font-size:11.9px;}
.ticker-name{font-size:9.6px;}
.data-table th{font-size:9.9px;}
.data-table td{font-size:11.7px;}
.fund-ticker{font-size:17.4px;}
.fund-name{font-size:10.4px;}
.fund-stat span{font-size:9.3px;}
.fund-stat b{font-size:13px;}
.fund-source{font-size:10.4px;}
[data-testid="stExpander"] summary{font-size:12.5px;}
[data-testid="stExpander"] p, [data-testid="stExpander"] li{font-size:11.7px;}
[data-testid="stPopoverBody"] label p{font-size:11.4px;}
[data-testid="stNumberInput"] input, [data-testid="stTextInput"] input{font-size:12.4px !important;}
.footer{font-size:10.6px;}
.mini-stat span{font-size:9.5px;}
.mini-stat b{font-size:18px;}
.mini-stat em{font-size:9.5px;}
.search-hero b{font-size:46px;}
.search-hero span{font-size:11.4px;}
.fail-row span, .fail-row b{font-size:11.4px;}
.sel-row{font-size:11.7px;}
.sel-note{font-size:10.2px;}

/* Assumptions panel: larger type and a larger panel, same two-column logic */
[data-testid="stPopoverBody"]{
  width:880px !important; max-width:94vw !important;
  max-height:calc(100vh - 170px) !important;
  padding:14px 18px !important;
}
[data-testid="stPopoverBody"] .card-title{font-size:17px !important;}
[data-testid="stPopoverBody"] .card-sub{font-size:12.4px !important;}
[data-testid="stPopoverBody"] label p{font-size:12.6px !important;}
[data-testid="stPopoverBody"] [data-testid="stNumberInput"] input{font-size:13.6px !important; height:38px !important;}
[data-testid="stPopoverBody"] [data-baseweb="select"],
[data-testid="stPopoverBody"] [data-baseweb="select"] *{font-size:13.6px !important;}
[data-testid="stPopoverBody"] [data-testid="stExpander"] summary{font-size:13.6px !important;}
[data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"] button{font-size:13.6px !important; min-height:38px !important;}
[data-testid="stPopoverBody"] [data-testid="stSlider"]{font-size:12.6px;}
"""


# ---------------------------------------------------------------------------
# Helpers


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def usd(value, decimals=0) -> str:
    if value is None:
        return "—"
    return f"${value:,.{decimals}f}"


def pct(value, decimals=2) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}%"


def short_date(value) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%b %Y")
    except (ValueError, TypeError):
        return str(value)


def status_chip(status: str, ok="Pass", warn="Review", bad="Fail") -> str:
    kind = "ok" if status == "pass" else ("warn" if status == "unverified" else "bad")
    label = ok if status == "pass" else (warn if status == "unverified" else bad)
    return f'<span class="pill {kind}">{esc(label)}</span>'


def check_chip(status: str) -> str:
    if status == "pass":
        return '<span class="pill ok">Pass</span>'
    if status == "unverified":
        return '<span class="pill warn">Review</span>'
    return '<span class="pill bad">Fail</span>'


def icon(name: str) -> str:
    paths = {
        "wallet": '<rect x="3" y="6" width="18" height="13" rx="3"/><path d="M3 10h18"/><path d="M16 14h2"/>',
        "trend": '<path d="M4 16l5-5 3 3 7-8"/><path d="M14 6h5v5"/>',
        "shield": '<path d="M12 3l7 3v5c0 4.4-3 8.1-7 9-4-.9-7-4.6-7-9V6l7-3z"/><path d="M9.2 12l2 2 3.8-4"/>',
        "pulse": '<path d="M3 12h4l2.2-5 3.2 10 2.4-5H21"/>',
        "spark": '<path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6l2.1 2.1m0-12.8l-2.1 2.1m-8.6 8.6l-2.1 2.1"/><circle cx="12" cy="12" r="3"/>',
    }
    body = paths.get(name, paths["spark"])
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def sparkline(values, color: str, uid: str) -> str:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(vals) < 2:
        return ""
    if len(vals) > 90:
        picked = [round(i * (len(vals) - 1) / 89) for i in range(90)]
        vals = [vals[i] for i in picked]
    low, high = min(vals), max(vals)
    span = high - low or 1
    points = []
    for i, value in enumerate(vals):
        x = i * 118 / (len(vals) - 1)
        y = 33 - ((value - low) / span) * 27
        points.append(f"{x:.2f},{y:.2f}")
    line = "M" + " L".join(points)
    area = f"{line} L118,36 L0,36 Z"
    return (
        f'<svg class="kpi-spark" viewBox="0 0 118 36" preserveAspectRatio="none" aria-hidden="true">'
        f'<defs><linearGradient id="spark{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity=".28"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
        f'<path d="{area}" fill="url(#spark{uid})"/>'
        f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.5"/>'
        f"</svg>"
    )


def kpi_card(label, value, sub, chip_text, chip_kind="", icon_name="spark", value_class="", visual="") -> str:
    return (
        '<div class="kpi">'
        f'<div class="kpi-head"><span class="kpi-icon">{icon(icon_name)}</span>'
        f'<span class="kpi-label">{esc(label)}</span>'
        f'<span class="pill {chip_kind}">{esc(chip_text)}</span></div>'
        f'<div class="kpi-value {value_class}">{value}</div>'
        f'<div class="kpi-sub">{sub}</div>'
        f"{visual}</div>"
    )


def card_head(title, subtitle="", pill_text="", pill_kind="soft") -> None:
    pill = f'<span class="pill {pill_kind}">{esc(pill_text)}</span>' if pill_text else ""
    sub = f'<div class="card-sub">{esc(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="card-head"><div><div class="card-title">{esc(title)}</div>{sub}</div>{pill}</div>',
        unsafe_allow_html=True,
    )


def series(rows: pd.DataFrame, prefix: str):
    for column in rows.columns:
        if str(column).startswith(prefix) and str(column).endswith("value USD"):
            return rows[column].astype(float).tolist()
    return []


# ---------------------------------------------------------------------------
# Data


@st.cache_data(show_spinner=False)
def calculate(policy_json, prices_csv, metadata_json, facts_json, analysis_date):
    return run_validation(
        pd.read_csv(io.StringIO(prices_csv)),
        json.loads(metadata_json),
        ClientPolicy(**json.loads(policy_json)),
        json.loads(facts_json),
        as_of=datetime.fromisoformat(analysis_date).date(),
    )


def load_market(policy: ClientPolicy, refresh: bool):
    summary = prepare_market_data(data_dir=DATA_DIR, refresh=refresh)
    prices_csv = (DATA_DIR / "prices.csv").read_text(encoding="utf-8")
    prices = pd.read_csv(io.StringIO(prices_csv))
    facts = load_facts(FACTS_PATH)
    analysis_date = datetime.now(timezone.utc).date().isoformat()
    policy_json = json.dumps(asdict(policy), sort_keys=True)
    result = calculate(policy_json, prices_csv, json.dumps(summary["source"]), json.dumps(facts), analysis_date)
    figures, history_rows = build_figures(result, prices)
    fingerprint = json.dumps(
        [policy_json, summary["source"], facts, hashlib.sha256(prices_csv.encode("utf-8")).hexdigest(), analysis_date],
        sort_keys=True,
    )
    if st.session_state.get("result_fingerprint") != fingerprint:
        save_outputs(result, prices, DATA_DIR)
        (DATA_DIR / "model_input.json").write_text(
            json.dumps(build_payload(summary, policy.budget_usd, result), indent=2, allow_nan=False),
            encoding="utf-8",
        )
        st.session_state.result_fingerprint = fingerprint
        st.session_state.pop("sb_ai_report", None)
    return summary, prices, facts, result, figures, history_rows


# ---------------------------------------------------------------------------
# Charts


def style_figure(fig, height, legend=True, margin=None):
    fig.update_layout(
        template=None,
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, sans-serif", "color": "#a9bdd6", "size": 11},
        margin=margin or {"l": 54, "r": 16, "t": 34, "b": 36},
        showlegend=legend,
        legend={
            "orientation": "h", "yanchor": "bottom", "y": 1.015, "x": 0,
            "bgcolor": "rgba(0,0,0,0)", "font": {"color": "#8ea3bd", "size": 10.5},
            "itemsizing": "constant",
        },
        hoverlabel={
            "bgcolor": "#0c1728", "bordercolor": "rgba(45,212,191,.24)",
            "font": {"color": "#eaf2fc", "size": 11, "family": "Inter, sans-serif"},
        },
    )
    fig.update_xaxes(
        gridcolor="rgba(148,163,184,.055)", zerolinecolor="rgba(148,163,184,.10)",
        tickfont={"color": "#6f849e", "size": 10.5}, title_font={"color": "#6f849e", "size": 10.5},
        linecolor="rgba(148,163,184,.10)",
    )
    fig.update_yaxes(
        gridcolor="rgba(148,163,184,.055)", zerolinecolor="rgba(148,163,184,.10)",
        tickfont={"color": "#6f849e", "size": 10.5}, title_font={"color": "#6f849e", "size": 10.5},
        linecolor="rgba(148,163,184,.10)",
    )
    for area in fig.layout.annotations:
        if area.text and not area.text.startswith("<b>"):
            area.font = {"color": "#6f849e", "size": 10, "family": "Inter, sans-serif"}
    return fig


def history_figure(rows: pd.DataFrame, height: int = 326):
    fig = go.Figure()
    selected_values = series(rows, "Selected")
    baseline_values = series(rows, "Baseline")
    if not selected_values:
        selected_values = series(rows, "Unverified illustration")
    specs = []
    if selected_values:
        specs.append(("Selected", selected_values, TEAL, "rgba(45,212,191,.10)", "solid"))
    if baseline_values:
        specs.append(("Baseline · cash + BIL", baseline_values, SLATE, "rgba(148,163,184,.035)", "dash"))
    for name, values, color, fill, dash in specs:
        base = values[0] or 1
        returns = [(value / base - 1) * 100 for value in values]
        fig.add_trace(
            go.Scatter(
                x=rows.index, y=returns, name=name, mode="lines",
                line={"color": color, "width": 2.1, "dash": dash},
                fill="tozeroy" if name == "Selected" else None,
                fillcolor=fill,
                hovertemplate="%{y:+.2f}%<extra>" + name + "</extra>",
            )
        )
        fig.add_annotation(
            x=rows.index[-1], y=returns[-1], text=f"{returns[-1]:+.1f}%",
            showarrow=False, xanchor="left", xshift=7,
            font={"color": color, "size": 10.5, "family": "Inter"},
        )
    fig.add_hline(y=0, line_color="rgba(148,163,184,.18)", line_width=1)
    for start, end, label, color in (
        ("2020-02-19", "2020-03-23", "2020 stress", "rgba(251,113,133,.055)"),
        ("2022-01-01", "2022-10-31", "2022 rate shock", "rgba(251,191,36,.045)"),
    ):
        if pd.Timestamp(start) < rows.index[-1]:
            fig.add_vrect(x0=start, x1=end, fillcolor=color, line_width=0, layer="below")
            fig.add_annotation(
                x=pd.Timestamp(start), y=1.0, yref="paper", text=label,
                showarrow=False, xanchor="left", xshift=3,
                font={"color": "#7f93ad", "size": 9.5, "family": "Inter"},
            )
    fig.update_yaxes(ticksuffix="%")
    fig.update_xaxes(ticksuffix="", tickformat="%Y", range=[rows.index[0], rows.index[-1] + pd.Timedelta(days=45)])
    fig.update_layout(hovermode="x unified")
    style_figure(fig, height, legend=True, margin={"l": 54, "r": 58, "t": 42, "b": 34})
    return fig


def scenario_figure(result, height=330):
    portfolios = [("Selected", focus_candidate(result)), ("Baseline", result.get("baseline"))]
    fig = go.Figure()
    for label, candidate in portfolios:
        if not candidate:
            continue
        loss = candidate["metrics"].get("rate_up_loss_usd")
        if loss is None:
            continue
        color = TEAL if label == "Selected" else SLATE
        losses = [-loss, loss]
        fig.add_bar(
            x=["Rates rise +100 bps", "Rates fall −100 bps"], y=losses, name=label,
            marker={"color": color, "line": {"width": 0}},
            text=[("−" + usd(abs(value))) if value < 0 else usd(value) for value in losses] if label == "Selected" else None,
            textposition="outside" if label == "Selected" else None,
            textfont={"color": "#9fb3ca", "size": 10},
            hovertemplate="%{x}<br>$%{y:,.2f}<extra>" + label + "</extra>",
        )
    limit = result["policy"]["maximum_rate_loss_usd"]
    fig.add_hline(
        y=-limit, line_color=RED, line_dash="dot", line_width=1.2,
        annotation_text=f"Loss cap {usd(limit)}", annotation_position="bottom left",
        annotation_font={"color": "#fda4af", "size": 9.5},
    )
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    fig.update_layout(barmode="group", bargap=0.52, bargroupgap=0.14)
    return style_figure(fig, height, legend=True, margin={"l": 58, "r": 16, "t": 40, "b": 42})


def frontier_figure(figures, height=390):
    fig = figures["frontier"]
    fig.update_layout(title_text="")
    for trace in fig.data:
        if trace.name == "Pass":
            trace.marker.color = TEAL
            trace.marker.size = 4.5
            trace.marker.opacity = 0.62
        elif trace.name == "Fail":
            trace.marker.color = "#334155"
            trace.marker.size = 4
            trace.marker.opacity = 0.5
        elif trace.name == "Unverified":
            trace.marker.color = AMBER
        elif trace.name == "Shown allocation":
            trace.marker.color = "#f8fafc"
            trace.marker.size = 15
            trace.marker.line = {"color": TEAL, "width": 2}
    return style_figure(fig, height, legend=True, margin={"l": 62, "r": 16, "t": 44, "b": 44})


# ---------------------------------------------------------------------------
# Header / controls


def _request_refresh():
    st.session_state.force_refresh = True


def assumptions_popover(policy: ClientPolicy):
    with st.popover("⚙ Assumptions"):
        st.markdown(
            '<div class="card-title" style="font-size:17px;margin-bottom:0;">Client assumptions</div>'
            '<div class="card-sub" style="margin-bottom:8px;">Editable demo policy; none of these are regulatory rules.</div>',
            unsafe_allow_html=True,
        )
        with st.form("sandbox_policy_form"):
            left, right = st.columns(2)
            with left:
                budget = st.number_input("Budget (USD)", min_value=1_000, value=int(policy.budget_usd), step=5_000, key="sb_budget")
                reserve = st.number_input("Accessible cash (USD)", min_value=0, value=int(policy.minimum_cash_usd), step=5_000, key="sb_reserve")
                horizon = st.number_input("Horizon (months)", min_value=1, max_value=120, value=policy.horizon_months, key="sb_horizon")
                volatility = st.number_input("Max volatility (%)", min_value=0.0, max_value=50.0, value=policy.maximum_volatility_pct, step=0.25, key="sb_vol")
                drawdown = st.number_input("Max drawdown (%)", min_value=0.0, max_value=100.0, value=policy.maximum_drawdown_pct, step=0.5, key="sb_dd")
            with right:
                claim = st.number_input("Stressed claim (USD)", min_value=0, value=int(policy.stressed_claim_usd), step=5_000, key="sb_claim")
                due = st.number_input("Claim due (days)", min_value=0, max_value=365, value=policy.claim_due_days, key="sb_due")
                rate_loss = st.number_input("Rate-loss cap (USD)", min_value=0, value=int(policy.maximum_rate_loss_usd), step=250, key="sb_rate")
                shock = st.slider("Rate shock (bps)", 0, 300, int(policy.rate_shock_bps), 25, key="sb_shock")
            with st.expander("Evidence and operating assumptions"):
                adv_left, adv_right = st.columns(2)
                with adv_left:
                    delay = st.number_input("ETF sale access (days)", min_value=1, max_value=30, value=policy.assumed_sale_delay_days, key="sb_delay")
                    cash_rate = st.number_input("Cash rate (%)", min_value=0.0, max_value=20.0, value=policy.cash_annual_rate_pct, step=0.25, key="sb_cash")
                    spread = st.number_input("Max spread (%)", min_value=0.0, max_value=10.0, value=policy.maximum_spread_pct, step=0.01, key="sb_spread")
                with adv_right:
                    price_age = st.number_input("Max price age (days)", min_value=0, max_value=30, value=policy.maximum_price_age_days, key="sb_pa")
                    fact_age = st.number_input("Max fact age (days)", min_value=0, max_value=365, value=policy.maximum_fact_age_days, key="sb_fa")
                    grid_choices = sorted({5, 10, 20, 25, 50, policy.grid_step_pct})
                    grid_step = st.selectbox("Allocation step (%)", grid_choices, index=grid_choices.index(policy.grid_step_pct), key="sb_grid")
            action_left, action_right = st.columns([2.2, 1], vertical_alignment="center")
            with action_left:
                applied = st.form_submit_button("Apply & recalculate", type="primary", width="stretch")
            with action_right:
                reset = st.form_submit_button("Reset defaults", type="secondary", width="stretch")
        if reset:
            for key in [key for key in st.session_state if str(key).startswith("sb_")]:
                del st.session_state[key]
            st.session_state.policy_state = asdict(load_policy(POLICY_PATH))
            st.rerun()
        if applied:
            proposed = replace(
                policy,
                budget_usd=float(budget), minimum_cash_usd=float(reserve), horizon_months=int(horizon),
                maximum_volatility_pct=float(volatility), maximum_drawdown_pct=float(drawdown),
                maximum_rate_loss_usd=float(rate_loss), rate_shock_bps=float(shock),
                stressed_claim_usd=float(claim), claim_due_days=int(due),
                assumed_sale_delay_days=int(delay), cash_annual_rate_pct=float(cash_rate),
                maximum_spread_pct=float(spread), maximum_price_age_days=int(price_age),
                maximum_fact_age_days=int(fact_age), grid_step_pct=int(grid_step),
            )
            st.session_state.policy_state = asdict(proposed)
            st.rerun()


def topbar(policy: ClientPolicy) -> str:
    brand_col, nav_col, meta_col = st.columns([1.14, 2.38, 1.3], vertical_alignment="center")
    with brand_col:
        st.markdown(
            '<div class="topbar-brand"><div class="brand-mark">'
            + icon("shield")
            + '</div><div class="brand-name">InsureVest <em>Analytics</em></div></div>',
            unsafe_allow_html=True,
        )
    with nav_col:
        page = st.radio("Section", NAV, horizontal=True, label_visibility="collapsed", key="nav_section")
    with meta_col:
        st.markdown(
            '<div class="meta-bar">'
            '<span class="pill soft">Insurance portfolio</span>'
            '<span class="pill soft">USD</span>'
            f'<span class="pill blue">{usd(policy.budget_usd)}</span>'
            "</div>",
            unsafe_allow_html=True,
        )
    st.markdown('<div class="top-rule"></div>', unsafe_allow_html=True)
    return page


def page_header(page: str, policy: ClientPolicy, result, summary) -> None:
    title, subtitle = PAGE_COPY[page]
    common = result["common_history"]
    last_price = datetime.fromisoformat(summary["instruments"][0]["last_price_date"]).strftime("%d %b %Y")
    as_of = datetime.now(timezone.utc).strftime("%d %b %Y")
    fetched = datetime.fromisoformat(summary["source"]["fetched_at_utc"]).astimezone(timezone.utc)
    title_col, control_col = st.columns([2.85, 1.15], vertical_alignment="bottom")
    with title_col:
        st.markdown(
            '<div class="pagehead"><div class="title-line">'
            f"<h1>{esc(title)}</h1>"
            '<span class="pill soft">Illustrative portfolio</span>'
            f'<span class="pill soft">{esc(short_date(common["start"]))} – {esc(short_date(common["end"]))}</span>'
            "</div>"
            f'<p class="sub">{esc(subtitle)}</p></div>',
            unsafe_allow_html=True,
        )
    with control_col:
        left, right = st.columns([1.12, 1], vertical_alignment="center")
        with left:
            assumptions_popover(policy)
        with right:
            st.button(
                "↻ Refresh",
                on_click=_request_refresh,
                width="stretch",
                help="Download fresh Yahoo Finance prices. Issuer facts are not refreshed.",
            )
        st.markdown(
            f'<div class="asof">As of <b>{esc(as_of)}</b> · prices to {esc(last_price)}<br>'
            f"fetched {fetched.strftime('%d %b %Y, %H:%M')} UTC</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Shared blocks


def coverage_gauge(ratio: float | None, target: float = 1.0) -> str:
    if ratio is None:
        pct_fill = 0.0
        center = "—"
    else:
        pct_fill = max(0.0, min(ratio / 3.0, 1.0)) * 100
        center = f"{ratio:.1f}×"
    return (
        '<div class="gauge-wrap"><div class="gauge">'
        '<svg viewBox="0 0 160 160" aria-hidden="true">'
        '<defs><linearGradient id="coverageGrad" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{TEAL}"/><stop offset="1" stop-color="{CYAN}"/>'
        "</linearGradient></defs>"
        '<circle class="gauge-track" cx="80" cy="80" r="62"/>'
        f'<circle class="gauge-fill" cx="80" cy="80" r="62" pathLength="100" stroke-dasharray="{pct_fill:.2f} 100"/>'
        "</svg>"
        f'<div class="gauge-center"><strong>{center}</strong><span>claims coverage</span></div></div>'
        f'<div class="gauge-copy"><p>Coverage of modelled 30-day claims outflow by liquid holdings.</p>'
        f'<span class="pill {"ok" if ratio is not None and ratio >= target else "warn"}">Target ≥ {target:.1f}×</span></div>'
        "</div>"
    )


def resilience_block(result, focus) -> str:
    policy = ClientPolicy(**result["policy"])
    metrics = focus["metrics"]
    available = metrics.get("stressed_available_usd")
    claim = policy.stressed_claim_usd
    ratio = (available / claim) if available is not None and claim else None
    check_map = {c["name"]: c for c in focus["checks"]}
    cash_ok = check_map.get("Immediately accessible cash", {}).get("status") == "pass"
    rate_ok = check_map.get("Rate shock loss (USD)", {}).get("status") == "pass"
    coverage_ok = check_map.get("Stressed claims coverage (USD)", {}).get("status") == "pass"
    loss = metrics.get("rate_up_loss_usd")
    rows = [
        ("Claims reserve", "Covered" if cash_ok else "Below floor", "ok" if cash_ok else "bad"),
        ("Liquidity buffer", "Adequate" if cash_ok else "Review", "ok" if cash_ok else "warn"),
        (f"Rate shock +{policy.rate_shock_bps:g} bps", "Within limit" if rate_ok else "Breach", "ok" if rate_ok else "bad"),
        ("Severe claims scenario", "Funded" if coverage_ok else "Shortfall", "ok" if coverage_ok else "bad"),
    ]
    rows_html = "".join(
        f'<div class="srow"><span>{esc(name)}</span><span class="pill {kind}">{esc(label)}</span></div>'
        for name, label, kind in rows
    )
    insight = (
        f"<b>Stress summary:</b> {pct(-loss / policy.budget_usd * 100 if loss is not None else None)} "
        f"under a +{policy.rate_shock_bps:g} bps shock; {usd(available)} is available by day "
        f"{policy.claim_due_days} against a {usd(claim)} claim."
    )
    return (
        coverage_gauge(ratio)
        + rows_html
        + f'<div class="insight">{insight}</div>'
    )


def allocation_block(policy: ClientPolicy, focus) -> str:
    weights = focus["weights"]
    amounts = focus["amounts_usd"]
    segments = []
    cursor = 0.0
    for asset in ASSETS:
        weight = float(weights.get(asset, 0) or 0) * 100
        if weight <= 0:
            continue
        end = cursor + weight
        gap = min(0.55, weight * 0.12)
        segments.append(f"{ASSET_META[asset][2]} {cursor:.3f}% {max(cursor, end - gap):.3f}%")
        segments.append(f"#0a1422 {max(cursor, end - gap):.3f}% {end:.3f}%")
        cursor = end
    gradient = ", ".join(segments) if segments else "#142236 0% 100%"
    legend = []
    for asset in ASSETS:
        name, meta, color = ASSET_META[asset]
        weight = float(weights.get(asset, 0) or 0)
        amount = amounts.get(asset, 0) or 0
        muted = " muted" if weight <= 0 else ""
        legend.append(
            f'<div class="alloc-row{muted}"><i class="alloc-dot" style="background:{color}"></i>'
            f'<span class="alloc-name"><b>{esc(name)}</b><span>{esc(meta)}</span></span>'
            f'<span class="alloc-w">{weight * 100:.0f}%</span>'
            f'<span class="alloc-usd">{usd(amount)}</span></div>'
        )
    return (
        '<div class="alloc-wrap">'
        f'<div class="donut" style="background: conic-gradient(from -90deg, {gradient});">'
        f'<div class="donut-center"><strong>{usd(policy.budget_usd)}</strong><span>allocated</span></div></div>'
        f'<div class="alloc-legend">{"".join(legend)}</div></div>'
    )


def checks_block(focus) -> str:
    wanted = [
        "Immediately accessible cash",
        "Historical volatility (%)",
        "Historical maximum drawdown (%)",
        "Rate shock loss (USD)",
        "Stressed claims coverage (USD)",
        "Common window freshness",
    ]
    short = {
        "Immediately accessible cash": "Cash floor",
        "Historical volatility (%)": "Volatility",
        "Historical maximum drawdown (%)": "Max drawdown",
        "Rate shock loss (USD)": "Rate-shock loss",
        "Stressed claims coverage (USD)": "Claims coverage",
        "Common window freshness": "Price freshness",
    }
    by_name = {c["name"]: c for c in focus["checks"]}
    rows = []
    for name in wanted:
        check = by_name.get(name)
        if not check:
            continue
        measured = format_check_value(name, check["measured"])
        limit = format_check_value(name, check["limit"])
        rows.append(
            '<div class="check-row">'
            f'<span class="check-name">{esc(short[name])}</span>'
            f'<span class="check-measure">{esc(measured)}<em>{esc(limit)}</em></span>'
            f'{check_chip(check["status"])}</div>'
        )
    return "".join(rows)


def brief_block(result, focus) -> str:
    policy = ClientPolicy(**result["policy"])
    metrics = focus["metrics"]
    search = result["search"]
    largest = max(search["failure_counts"].items(), key=lambda item: item[1]) if search["failure_counts"] else ("drawdown", 0)
    items = [
        (
            "Liquidity first",
            f"<b>{usd(focus['amounts_usd'].get('CASH'))} cash</b> meets the "
            f"{usd(policy.minimum_cash_usd)} floor exactly; "
            f"{pct(focus['weights'].get('SHY', 0) * 100, 0)} sits in short-duration Treasuries.",
        ),
        (
            "Risk inside limits",
            f"Volatility {pct(metrics.get('annualized_volatility_pct'))} vs "
            f"{pct(policy.maximum_volatility_pct)}; drawdown {pct(metrics.get('max_drawdown_pct'))} vs "
            f"{pct(policy.maximum_drawdown_pct)}; rate loss {usd(metrics.get('rate_up_loss_usd'))} vs "
            f"{usd(policy.maximum_rate_loss_usd)}.",
        ),
        (
            "Search evidence",
            f"<b>{search['passing']:,} of {search['total']:,}</b> allocations pass. "
            f"The largest exclusion is <b>{esc(largest[0].lower())}</b> ({largest[1]:,} candidates).",
        ),
    ]
    body = "".join(
        f'<div class="brief-item"><div class="brief-num">{i + 1}</div><p><b>{title}.</b> {text}</p></div>'
        for i, (title, text) in enumerate(items)
    )
    return (
        f'<div class="brief-list">{body}</div>'
        '<div class="brief-foot">Deterministic summary generated from the validation result. '
        "Not investment advice; evidence checks cover only the modelled prototype constraints.</div>"
    )


def etf_table(result, focus, facts, summary) -> str:
    funds = {fund["ticker"]: fund for fund in result["fund_evidence"]}
    instruments = {item["ticker"]: item for item in summary["instruments"]}
    rows = []
    for ticker in ["BIL", "SHY", "IEF"]:
        fund = funds.get(ticker, {})
        facts_fund = facts.get("funds", {}).get(ticker, {})
        instrument = instruments.get(ticker, {})
        weight = float(focus["weights"].get(ticker, 0) or 0)
        held = " held" if weight > 0 else ""
        status = fund.get("status", "unverified")
        chip = status_chip(status, ok="Eligible", warn="Review", bad="Not eligible")
        yield_pct = fund.get("sec_yield_pct")
        duration = fund.get("duration_years")
        vol = instrument.get("annualized_volatility_last_252_sessions_pct")
        impact = -float(duration) * 1.0 if duration is not None else None
        source = fund.get("source_url") or facts_fund.get("source_url") or "#"
        rows.append(
            f'<tr class="{held.strip()}">'
            f'<td><a class="ticker" href="{esc(source)}" target="_blank" rel="noopener">{esc(ticker)}</a>'
            f'<span class="ticker-name">{esc(fund.get("name", ""))}</span></td>'
            f"<td>{'—' if yield_pct is None else pct(yield_pct)}</td>"
            f"<td>{'—' if duration is None else f'{duration:.2f} yrs'}</td>"
            f"<td>{'—' if vol is None else pct(vol)}</td>"
            f"<td>{'—' if impact is None else f'{impact:+.2f}%'}</td>"
            f"<td>{'—' if weight <= 0 else pct(weight * 100, 0)}</td>"
            f"<td>{chip}</td>"
            f"<td>{esc(ROLES.get(ticker, '—'))}</td>"
            "</tr>"
        )
    return (
        '<div class="etf-wrap"><table class="etf"><thead><tr>'
        "<th>ETF</th><th>Indicative yield</th><th>Duration</th><th>Hist. volatility</th>"
        "<th>+100 bps NAV impact</th><th>Allocation</th><th>Eligibility</th><th>Portfolio role</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def data_table(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, classes="data-table", escape=True)


# ---------------------------------------------------------------------------
# Pages


def render_overview(ctx) -> None:
    policy, result = ctx["policy"], ctx["result"]
    focus, summary = ctx["focus"], ctx["summary"]
    metrics = focus["metrics"]
    claim = policy.stressed_claim_usd
    available = metrics.get("stressed_available_usd")
    ratio = available / claim if available is not None and claim else None
    loss = metrics.get("rate_up_loss_usd")
    loss_pct = loss / policy.budget_usd * 100 if loss is not None and policy.budget_usd else None
    values = series(ctx["rows"], "Selected") or series(ctx["rows"], "Baseline")

    cap_used = max(0.0, min((loss or 0) / policy.maximum_rate_loss_usd, 1.0)) * 100 if policy.maximum_rate_loss_usd else 0
    coverage_used = max(0.0, min((ratio or 0) / 3.0, 1.0)) * 100

    cards = st.columns(4, gap="small")
    with cards[0]:
        st.markdown(
            kpi_card(
                "Portfolio value", usd(policy.budget_usd), "Deployable reserve · 12-month horizon",
                "Demo", "soft", "wallet", visual=sparkline(values, TEAL, "1"),
            ),
            unsafe_allow_html=True,
        )
    with cards[1]:
        st.markdown(
            kpi_card(
                "Estimated annual income", usd(metrics.get("income_run_rate_usd")),
                f"{pct(metrics.get('income_run_rate_pct'))} current run-rate · not a forecast",
                "Indicative", "soft", "trend",
            ),
            unsafe_allow_html=True,
        )
    with cards[2]:
        st.markdown(
            kpi_card(
                "30-day claims coverage", f"{ratio:.1f}×" if ratio is not None else "—",
                f"{usd(available)} available by day {policy.claim_due_days}",
                "Above target" if ratio is not None and ratio >= 1 else "Review",
                "ok" if ratio is not None and ratio >= 1 else "warn", "shield",
                visual=(
                    '<div class="kpi-meter"><div class="meter"><span style="width:'
                    f'{coverage_used:.1f}%"></span></div><div class="meter-caption">Target ≥ 1.0×</div></div>'
                ),
            ),
            unsafe_allow_html=True,
        )
    with cards[3]:
        st.markdown(
            kpi_card(
                f"Rate-shock loss",
                f"−{pct(abs(loss_pct) if loss_pct is not None else None)}",
                f"+{policy.rate_shock_bps:g} bps shock · first-order approximation",
                "Within limit", "ok", "pulse", "neg",
                visual=(
                    '<div class="kpi-meter"><div class="meter"><span style="width:'
                    f'{cap_used:.1f}%"></span></div><div class="meter-caption">'
                    f"{usd(loss)} of {usd(policy.maximum_rate_loss_usd)} cap</div></div>"
                ),
            ),
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    left, right = st.columns([2.02, 1.0], gap="medium")
    with left:
        with st.container(border=True):
            card_head(
                "Historical performance",
                "Cumulative return, % · buy-and-hold replay with drifting weights",
                "No claims paid",
            )
            st.plotly_chart(
                history_figure(ctx["rows"]), width="stretch", theme=None,
                config={"displayModeBar": False},
            )
            st.markdown(
                f'<div class="card-foot">Common window {esc(short_date(result["common_history"]["start"]))} – '
                f'{esc(short_date(result["common_history"]["end"]))} · '
                f'{result["common_history"]["return_observations"]:,} return observations. '
                "Historical results are not a forward test.</div>",
                unsafe_allow_html=True,
            )
    with right:
        with st.container(border=True):
            card_head("Portfolio resilience", "Modelled coverage and policy status", "Modelled output", "blue")
            st.markdown(resilience_block(result, focus), unsafe_allow_html=True)

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    col_a, col_b, col_c = st.columns([1.2, 1.3, 1.12], gap="medium")
    with col_a:
        with st.container(border=True):
            card_head("Asset allocation", "Selected weights and dollar amounts", "Selected", "ok")
            st.markdown(allocation_block(policy, focus), unsafe_allow_html=True)
            st.markdown(
                '<div class="card-foot">Illustrative allocation, subject to eligibility, '
                "claims requirements and risk constraints.</div>",
                unsafe_allow_html=True,
            )
    with col_b:
        with st.container(border=True):
            all_pass = all(check["status"] == "pass" for check in focus["checks"])
            card_head("Policy checks", "Key constraints applied to the selected allocation", "All pass" if all_pass else "Review", "ok" if all_pass else "warn")
            st.markdown(checks_block(focus), unsafe_allow_html=True)
            st.markdown(
                f'<div class="card-foot">{len(focus["checks"])} modelled checks. A pass is not a suitability '
                "determination.</div>",
                unsafe_allow_html=True,
            )
    with col_c:
        with st.container(border=True):
            card_head("Desk brief", "What the result says in three lines", "Evidence-backed", "soft")
            st.markdown(brief_block(result, focus), unsafe_allow_html=True)

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    with st.container(border=True):
        card_head("ETF comparison", "Indicative issuer facts and price-derived risk measures", "Source dated", "soft")
        st.markdown(etf_table(result, focus, ctx["facts"], summary), unsafe_allow_html=True)
        st.markdown(
            '<div class="card-foot">Yields and duration come from dated issuer facts; volatility is from adjusted '
            f"closes to {esc(summary['instruments'][0]['last_price_date'])}. The +100 bps NAV impact is a first-order "
            "duration approximation, not a loss bound.</div>",
            unsafe_allow_html=True,
        )


def render_history(ctx) -> None:
    policy, result, focus = ctx["policy"], ctx["result"], ctx["focus"]
    with st.container(border=True):
        card_head(
            "Historical return path",
            "Cumulative return, % · buy-and-hold replay with drifting weights",
            "Historical replay",
        )
        st.plotly_chart(
            history_figure(ctx["rows"], height=420), width="stretch", theme=None,
            config={"displayModeBar": False},
        )
        st.markdown(
            f'<div class="card-foot">Common window: {esc(result["common_history"]["start"])} to '
            f'{esc(result["common_history"]["end"])}. The replay rebalances by holding shares, so weights drift.</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.25, 1], gap="medium")
    with left:
        with st.container(border=True):
            card_head("Rate-shock scenarios", "First-order mark-to-market change for ±100 bps", "Approximation", "soft")
            st.plotly_chart(
                scenario_figure(result, height=360), width="stretch", theme=None,
                config={"displayModeBar": False},
            )
            st.markdown(
                '<div class="card-foot">The rate-loss cap applies to the selected allocation. '
                f'For comparison, the cash + BIL baseline loses about '
                f'{usd(result["baseline"]["metrics"]["rate_up_loss_usd"]) if result.get("baseline") else "—"} '
                "under the same +100 bps shock. Bars are an approximation, not a loss bound.</div>",
                unsafe_allow_html=True,
            )
    with right:
        with st.container(border=True):
            card_head("Stress windows", "Observed history, not scenario forecasts", "Observed", "soft")
            periods = pd.DataFrame(focus["historical_periods"]).rename(
                columns={
                    "label": "Window", "start": "Start", "end": "End",
                    "return_pct": "Return (%)", "drawdown_pct": "Max drawdown (%)",
                }
            )
            periods["Return (%)"] = periods["Return (%)"].map(lambda value: f"{value:+.2f}%")
            periods["Max drawdown (%)"] = periods["Max drawdown (%)"].map(lambda value: f"{value:.2f}%")
            st.markdown(data_table(periods), unsafe_allow_html=True)
            available = focus["metrics"].get("stressed_available_usd")
            st.markdown(
                f'<div class="insight"><b>Claims access:</b> {usd(available)} modelled accessible by day '
                f"{policy.claim_due_days} after the upward rate shock and sale haircut, against a "
                f"{usd(policy.stressed_claim_usd)} claim.</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="card-foot">Cash is accessible immediately; ETF proceeds are assumed accessible after '
                f"{policy.assumed_sale_delay_days} calendar days. This does not guarantee settlement or liquidity.</div>",
                unsafe_allow_html=True,
            )


def render_evidence(ctx) -> None:
    result, focus = ctx["result"], ctx["focus"]
    with st.container(border=True):
        card_head("Fund evidence", "Issuer facts and per-fund checks used by the search", "Issuer sourced", "soft")
        columns = st.columns(3, gap="small")
        for column, fund in zip(columns, result["fund_evidence"]):
            checks = "".join(
                '<div class="check-row">'
                f'<span class="check-name">{esc(check["name"])}</span>'
                f'{check_chip(check["status"])}</div>'
                for check in fund["checks"][:5]
            )
            source = fund.get("source_url") or "#"
            with column:
                st.markdown(
                    '<div class="fund-card">'
                    '<div class="fund-top">'
                    f'<div><div class="fund-ticker">{esc(fund["ticker"])}</div>'
                    f'<div class="fund-name">{esc(fund["name"])}</div></div>'
                    f'{status_chip(fund["status"], ok="Eligible", warn="Review", bad="Not eligible")}</div>'
                    '<div class="fund-stats">'
                    f'<div class="fund-stat"><span>SEC yield</span><b>{pct(fund.get("sec_yield_pct"))}</b></div>'
                    f'<div class="fund-stat"><span>Duration</span><b>{"—" if fund.get("duration_years") is None else f"{fund["duration_years"]:.2f} yrs"}</b></div>'
                    f'<div class="fund-stat"><span>Spread</span><b>{pct(fund.get("spread_pct"))}</b></div>'
                    "</div>"
                    f'<div class="fund-checks">{checks}</div>'
                    f'<div class="fund-source"><a href="{esc(source)}" target="_blank" rel="noopener">Issuer source ↗</a></div>'
                    "</div>",
                    unsafe_allow_html=True,
                )

    methodology = list(result["methodology"].values()) + list(result["limitations"])
    with st.expander("Methodology, assumptions and limitations"):
        items = "".join(f"<li>{esc(note)}</li>" for note in methodology)
        st.markdown(f'<ul style="margin:4px 0 2px 18px; padding:0;">{items}</ul>', unsafe_allow_html=True)
        st.caption(
            "Editable policy assumptions, not regulatory rules. A pass covers only the explicit prototype checks."
        )

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    with st.container(border=True):
        card_head("AI investment brief", "Optional model explanation of the computed result", "Optional", "blue")
        st.caption(
            "The model receives the computed allocation and checks and is instructed to preserve them. "
            "It does not change the deterministic result."
        )
        key = st.text_input(
            "Azure API key (current session)", type="password", key="sb_ai_key",
            help="Used only for the Azure request; not written to reports or project files.",
        )
        credential = (key or os.environ.get("AZURE_OPENAI_API_KEY", "")).strip()
        if st.button("Generate AI brief", disabled=not bool(credential), key="sb_ai_run"):
            try:
                with st.spinner("Explaining the evidence..."):
                    payload = build_payload(ctx["summary"], ctx["policy"].budget_usd, result)
                    report = analyze(payload, api_key=credential)
                    st.session_state.sb_ai_report = report
            except Exception as exc:
                st.error(f"AI explanation failed ({type(exc).__name__}). The deterministic result remains available.")
        if not credential:
            st.caption("Paste a key above or set AZURE_OPENAI_API_KEY before starting the dashboard.")
        if "sb_ai_report" in st.session_state:
            st.markdown(st.session_state.sb_ai_report.replace("$", "\\$"))


def render_alternatives(ctx) -> None:
    result, focus = ctx["result"], ctx["focus"]
    left, right = st.columns([1.9, 1], gap="medium")
    with left:
        with st.container(border=True):
            card_head(
                "Candidate frontier", "Income run-rate versus historical variability",
                "Search space", "soft",
            )
            st.plotly_chart(
                frontier_figure(ctx["figures"]), width="stretch", theme=None,
                config={"displayModeBar": False},
            )
            st.markdown(
                '<div class="card-foot">Each dot is one allocation on the 5% grid; the star is the shown allocation. '
                "Position is historical, not a forecast.</div>",
                unsafe_allow_html=True,
            )
    with right:
        with st.container(border=True):
            search = result["search"]
            card_head("Search diagnostics", "Why the other candidates fail", "Modelled", "soft")
            st.markdown(
                f'<div class="search-hero"><b>{search["passing"]:,}</b>'
                f'<span>of {search["total"]:,} allocations<br>pass every modelled check</span></div>',
                unsafe_allow_html=True,
            )
            rows = []
            for name, count in sorted(search["failure_counts"].items(), key=lambda item: item[1], reverse=True):
                share = count / search["total"] * 100 if search["total"] else 0
                rows.append(
                    f'<div class="fail-row"><span>{esc(name)}</span><b>{count:,}</b>'
                    f'<div class="fail-bar"><i style="width:{share:.1f}%"></i></div></div>'
                )
            st.markdown("".join(rows), unsafe_allow_html=True)
            st.markdown(
                '<div class="card-foot">Counts overlap: one candidate can fail several checks. '
                f'{search["unverified"]:,} unverified inputs in this run.</div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        with st.container(border=True):
            weight = float(focus["weights"].get("SHY", 0) or 0)
            card_head("Selected allocation", "Shown portfolio at a glance", f"{weight * 100:.0f}% SHY", "blue")
            cash = focus["amounts_usd"].get("CASH", 0) or 0
            shy = focus["amounts_usd"].get("SHY", 0) or 0
            metrics = focus["metrics"]
            ratio = (
                metrics["stressed_available_usd"] / result["policy"]["stressed_claim_usd"]
                if metrics.get("stressed_available_usd") is not None and result["policy"]["stressed_claim_usd"] else None
            )
            st.markdown(
                '<div class="sel-bar">'
                f'<i style="width:{float(focus["weights"].get("CASH", 0) or 0) * 100:.1f}%;background:{ASSET_META["CASH"][2]}"></i>'
                f'<i style="width:{weight * 100:.1f}%;background:{ASSET_META["SHY"][2]}"></i></div>'
                f'<div class="sel-row"><span><i style="background:{ASSET_META["CASH"][2]}"></i>Cash</span>'
                f"<b>{usd(cash)}</b><em>{float(focus['weights'].get('CASH', 0) or 0) * 100:.0f}%</em></div>"
                f'<div class="sel-row"><span><i style="background:{ASSET_META["SHY"][2]}"></i>SHY</span>'
                f"<b>{usd(shy)}</b><em>{weight * 100:.0f}%</em></div>"
                '<div class="sel-note"><span>Claims coverage</span>'
                f'<span><b style="color:#8ff7e8">{ratio:.1f}×</b></span></div>'
                '<div class="sel-note"><span>Rate-shock loss</span>'
                f'<span><b style="color:#eaf2fc">{usd(metrics.get("rate_up_loss_usd"))}</b> / '
                f'{usd(result["policy"]["maximum_rate_loss_usd"])}</span></div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    with st.container(border=True):
        card_head("Next passing alternatives", "Same checks, different trade-offs", "Validated", "ok")
        alternatives = result["alternatives"]
        if alternatives:
            df = comparison_table([(f"Alternative {i + 1}", candidate) for i, candidate in enumerate(alternatives)])
            st.markdown(data_table(df), unsafe_allow_html=True)
            st.markdown(
                '<div class="card-foot">Exact weights and dollar amounts are included in the validation export.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No additional passing alternatives under these assumptions.")

    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
    with st.container(border=True):
        card_head("Export evidence", "Portable copies of the deterministic run", "Offline", "soft")
        specs = [
            ("Download chart report", "report.html", "text/html"),
            ("Download candidate allocations", "candidate_allocations.csv", "text/csv"),
            ("Download validation evidence", "validation.json", "application/json"),
        ]
        columns = st.columns(3, gap="small")
        for column, (label, name, mime) in zip(columns, specs):
            path = DATA_DIR / name
            with column:
                if path.exists():
                    st.download_button(
                        label, path.read_bytes(), name, mime=mime, key=f"sb_dl_{name}", width="stretch",
                    )
                else:
                    st.caption(f"{name} is not available yet.")


def render_footer() -> None:
    st.markdown(
        '<div class="footer"><b>Illustrative prototype.</b> Historical performance does not guarantee future results. '
        "Scenario output depends on assumptions and is not investment guidance. Cash access, sale delay and the cash "
        "rate are assumptions; trading spread is only a liquidity proxy. Rate stress is a first-order approximation, "
        "not a loss bound.</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main


def inject_css() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def main() -> None:
    inject_css()
    defaults = load_policy(POLICY_PATH)
    if "policy_state" not in st.session_state:
        st.session_state.policy_state = asdict(defaults)
    policy = ClientPolicy(**st.session_state.policy_state)

    refresh = bool(st.session_state.pop("force_refresh", False))
    try:
        spinner = st.spinner("Refreshing prices from Yahoo Finance…") if refresh else nullcontext()
        with spinner:
            summary, prices, facts, result, figures, history_rows = load_market(policy, refresh)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error(f"Unable to evaluate this request: {exc}")
        st.info("Check the policy, issuer facts and saved price files. No constraint has been relaxed.")
        return

    focus = focus_candidate(result)
    page = topbar(policy)
    page_header(page, policy, result, summary)

    if focus is None:
        with st.container(border=True):
            card_head("No passing allocation", "The engine kept every constraint unchanged.", "Review", "warn")
            st.markdown(
                f'<div class="insight">{esc(result["status"].title())}. No allocation passed all modelled checks. '
                "Open Assumptions to change the policy or review the evidence.</div>",
                unsafe_allow_html=True,
            )
        render_footer()
        return

    ctx = {
        "policy": policy,
        "summary": summary,
        "prices": prices,
        "facts": facts,
        "result": result,
        "figures": figures,
        "rows": history_rows,
        "focus": focus,
    }
    if page == NAV[0]:
        render_overview(ctx)
    elif page == NAV[1]:
        render_history(ctx)
    elif page == NAV[2]:
        render_evidence(ctx)
    else:
        render_alternatives(ctx)
    render_footer()


if __name__ == "__main__":
    main()
