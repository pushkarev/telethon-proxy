#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SESSIONS_DIR = Path('/home/ubuntu/.openclaw/agents/main/sessions')
HOST = '127.0.0.1'
PORT = int(os.environ.get('USAGE_DASHBOARD_PORT', '8789'))
BASE_PATH = os.environ.get('USAGE_DASHBOARD_BASE_PATH', '/usage-dashboard').rstrip('/') or '/usage-dashboard'


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except Exception:
        return None


def day_key(dt: datetime | None) -> str | None:
    return dt.strftime('%Y-%m-%d') if dt else None


def normalize_period(period: str | None) -> str:
    period = (period or 'all').lower()
    return period if period in {'day', 'week', 'month', 'quarter', 'year', 'all'} else 'all'


def period_start(period: str, now: datetime) -> datetime | None:
    if period == 'day':
        return now - timedelta(days=1)
    if period == 'week':
        return now - timedelta(days=7)
    if period == 'month':
        return now - timedelta(days=30)
    if period == 'quarter':
        return now - timedelta(days=90)
    if period == 'year':
        return now - timedelta(days=365)
    return None


def load_usage_data(period: str = 'all') -> dict:
    now = datetime.now(timezone.utc)
    period = normalize_period(period)
    start = period_start(period, now)

    openai_daily = defaultdict(lambda: {'cost': 0.0, 'tokens': 0, 'messages': 0})
    perplexity_daily = defaultdict(lambda: {'requests': 0, 'tookMs': 0, 'results': 0})
    model_breakdown = defaultdict(lambda: {'cost': 0.0, 'tokens': 0, 'messages': 0})
    search_queries = []
    sessions_seen = 0
    openai_all_time_cost = 0.0
    perplexity_all_time_requests = 0

    for path in sorted(SESSIONS_DIR.glob('*.jsonl')):
        sessions_seen += 1
        try:
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue

                    if row.get('type') != 'message':
                        continue
                    message = row.get('message') or {}
                    timestamp = parse_ts(row.get('timestamp')) or parse_ts(message.get('timestamp'))
                    if not timestamp:
                        continue
                    day = day_key(timestamp)

                    usage = message.get('usage') or {}
                    cost_total = ((usage.get('cost') or {}).get('total'))
                    total_tokens = usage.get('totalTokens')
                    provider = message.get('provider')
                    model = message.get('model') or 'unknown'
                    api = message.get('api') or ''

                    if isinstance(cost_total, (int, float)) and cost_total > 0 and (
                        provider in {'openai-codex', 'openai', 'openai-responses'} or 'openai' in api
                    ):
                        openai_all_time_cost += float(cost_total)
                        if start is None or timestamp >= start:
                            openai_daily[day]['cost'] += float(cost_total)
                            openai_daily[day]['tokens'] += int(total_tokens or 0)
                            openai_daily[day]['messages'] += 1
                            model_breakdown[model]['cost'] += float(cost_total)
                            model_breakdown[model]['tokens'] += int(total_tokens or 0)
                            model_breakdown[model]['messages'] += 1

                    if message.get('role') == 'toolResult' and message.get('toolName') == 'web_search':
                        details = message.get('details') or {}
                        if details.get('provider') == 'perplexity' and day:
                            results = details.get('results') or []
                            perplexity_all_time_requests += 1
                            if start is None or timestamp >= start:
                                perplexity_daily[day]['requests'] += 1
                                perplexity_daily[day]['tookMs'] += int(details.get('tookMs') or 0)
                                perplexity_daily[day]['results'] += len(results)
                                search_queries.append({
                                    'timestamp': timestamp.isoformat().replace('+00:00', 'Z'),
                                    'query': details.get('query') or '',
                                    'count': int(details.get('count') or 0),
                                    'results': len(results),
                                    'tookMs': int(details.get('tookMs') or 0),
                                    'sessionFile': path.name,
                                })
        except FileNotFoundError:
            continue

    search_queries.sort(key=lambda item: item.get('timestamp') or '', reverse=True)

    openai_series = [
        {
            'date': day,
            'cost': round(values['cost'], 6),
            'tokens': values['tokens'],
            'messages': values['messages'],
        }
        for day, values in sorted(openai_daily.items())
    ]
    perplexity_series = [
        {
            'date': day,
            'requests': values['requests'],
            'avgTookMs': round(values['tookMs'] / values['requests'], 2) if values['requests'] else 0,
            'results': values['results'],
        }
        for day, values in sorted(perplexity_daily.items())
    ]
    model_series = [
        {
            'model': model,
            'cost': round(values['cost'], 6),
            'tokens': values['tokens'],
            'messages': values['messages'],
        }
        for model, values in sorted(model_breakdown.items(), key=lambda kv: kv[1]['cost'], reverse=True)
    ]

    total_openai_cost = round(sum(item['cost'] for item in openai_series), 6)
    total_openai_tokens = sum(item['tokens'] for item in openai_series)
    total_perplexity_requests = sum(item['requests'] for item in perplexity_series)

    return {
        'generatedAt': now.isoformat().replace('+00:00', 'Z'),
        'basePath': BASE_PATH,
        'period': period,
        'periodStart': start.isoformat().replace('+00:00', 'Z') if start else None,
        'sessionsScanned': sessions_seen,
        'summary': {
            'openaiCostUsd': total_openai_cost,
            'openaiTokens': total_openai_tokens,
            'perplexityRequests': total_perplexity_requests,
            'openaiAllTimeCostUsd': round(openai_all_time_cost, 6),
            'perplexityAllTimeRequests': perplexity_all_time_requests,
            'perplexityBillingKnown': False,
        },
        'openaiDaily': openai_series,
        'perplexityDaily': perplexity_series,
        'modelBreakdown': model_series,
        'recentPerplexityQueries': search_queries[:25],
        'availablePeriods': ['day', 'week', 'month', 'quarter', 'year', 'all'],
        'notes': [
            'OpenAI totals are reconstructed from saved OpenClaw session transcript usage.cost.total fields.',
            'Perplexity billing credits are still not available locally, so the graph uses Perplexity request count as the second series.',
            'Time period filters apply to both graphing and summary cards. All-time totals remain shown in the summary for reference.',
            'This is a host-local history view based on retained sessions, not an authoritative provider billing export.',
        ],
    }


HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>OpenClaw Usage Dashboard</title>
  <script src=\"https://cdn.plot.ly/plotly-2.35.2.min.js\"></script>
  <style>
    :root {
      --bg: #0b1020;
      --panel: rgba(18,25,51,0.92);
      --text: #edf2ff;
      --muted: #9aa6cf;
      --accent1: #7dd3fc;
      --accent2: #a78bfa;
      --accent3: #34d399;
      --border: rgba(255,255,255,0.08);
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, system-ui, sans-serif; background: linear-gradient(180deg, #0b1020, #101933); color: var(--text); }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0 0 8px; font-size: 32px; }
    .sub { color: var(--muted); margin-bottom: 24px; line-height: 1.5; }
    .toolbar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
    .toolbar button { background:#182244; color:var(--text); border:1px solid var(--border); border-radius:999px; padding:8px 14px; cursor:pointer; }
    .toolbar button.active { background:#2d4ea2; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 16px; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 18px; box-shadow: 0 10px 30px rgba(0,0,0,0.22); }
    .label { font-size: 13px; color: var(--muted); margin-bottom: 8px; }
    .value { font-size: 28px; font-weight: 700; }
    .small { font-size: 13px; color: var(--muted); margin-top: 6px; line-height: 1.4; }
    .charts { display: grid; gap: 16px; grid-template-columns: 1fr; }
    .chart { min-height: 380px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); font-size: 14px; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #dbeafe; }
    .footer { color: var(--muted); font-size: 13px; margin-top: 16px; line-height: 1.6; }
    a { color: #93c5fd; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>OpenClaw usage dashboard</h1>
    <div class=\"sub\">OpenAI spend and Perplexity search activity reconstructed from saved OpenClaw session transcripts on this host.</div>

    <div class=\"toolbar\" id=\"periodButtons\"></div>
    <div class=\"grid\" id=\"cards\"></div>

    <div class=\"charts\">
      <div class=\"card\"><div id=\"combinedChart\" class=\"chart\"></div></div>
      <div class=\"card\"><div id=\"modelChart\" class=\"chart\"></div></div>
    </div>

    <div class=\"grid\" style=\"margin-top:16px; grid-template-columns: 1.1fr 1.9fr;\">
      <div class=\"card\">
        <div class=\"label\">Notes</div>
        <div id=\"notes\"></div>
      </div>
      <div class=\"card\">
        <div class=\"label\">Recent Perplexity searches in selected range</div>
        <div style=\"overflow:auto\"><table><thead><tr><th>Time</th><th>Query</th><th>Results</th><th>Latency</th></tr></thead><tbody id=\"queries\"></tbody></table></div>
      </div>
    </div>

    <div class=\"footer\" id=\"footer\"></div>
  </div>

  <script>
    const periodNames = { day:'Last 24h', week:'Last 7d', month:'Last 30d', quarter:'Last 90d', year:'Last 365d', all:'All time' };
    function usd(v) { return '$' + Number(v || 0).toFixed(4); }
    function num(v) { return new Intl.NumberFormat('en-US').format(v || 0); }
    function esc(s) { return String(s || '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'); }
    function currentPeriod() {
      const p = new URLSearchParams(location.search).get('period') || 'all';
      return periodNames[p] ? p : 'all';
    }
    async function load(period = currentPeriod()) {
      const resp = await fetch(`./data.json?period=${encodeURIComponent(period)}`, { cache: 'no-store' });
      const data = await resp.json();

      document.getElementById('periodButtons').innerHTML = data.availablePeriods.map(p =>
        `<button class="${p === data.period ? 'active' : ''}" data-period="${p}">${periodNames[p] || p}</button>`
      ).join('');
      for (const btn of document.querySelectorAll('#periodButtons button')) {
        btn.onclick = () => {
          const p = btn.getAttribute('data-period');
          const url = new URL(location.href);
          url.searchParams.set('period', p);
          history.replaceState({}, '', url);
          load(p);
        };
      }

      const cards = [
        { label: 'OpenAI spend in range', value: usd(data.summary.openaiCostUsd), small: `all-time ${usd(data.summary.openaiAllTimeCostUsd)}` },
        { label: 'Perplexity requests in range', value: num(data.summary.perplexityRequests), small: `all-time ${num(data.summary.perplexityAllTimeRequests)}` },
        { label: 'OpenAI tokens in range', value: num(data.summary.openaiTokens), small: data.periodStart ? `since ${new Date(data.periodStart).toLocaleString()}` : 'full retained history' },
        { label: 'Sessions scanned', value: num(data.sessionsScanned), small: `${periodNames[data.period]} · generated ${new Date(data.generatedAt).toLocaleString()}` },
      ];
      document.getElementById('cards').innerHTML = cards.map(c => `
        <div class="card">
          <div class="label">${c.label}</div>
          <div class="value">${c.value}</div>
          <div class="small">${c.small}</div>
        </div>
      `).join('');

      const dates = Array.from(new Set([
        ...data.openaiDaily.map(d => d.date),
        ...data.perplexityDaily.map(d => d.date),
      ])).sort();
      const openaiMap = Object.fromEntries(data.openaiDaily.map(d => [d.date, d]));
      const perplexityMap = Object.fromEntries(data.perplexityDaily.map(d => [d.date, d]));
      const openaiCost = dates.map(d => (openaiMap[d] || {}).cost || 0);
      const perplexityReq = dates.map(d => (perplexityMap[d] || {}).requests || 0);

      Plotly.newPlot('combinedChart', [
        {
          x: dates,
          y: openaiCost,
          type: 'bar',
          name: 'OpenAI spend (USD)',
          marker: { color: '#7dd3fc' },
          yaxis: 'y1',
          hovertemplate: '%{x}<br>OpenAI $%{y:.4f}<extra></extra>'
        },
        {
          x: dates,
          y: perplexityReq,
          type: 'scatter',
          mode: 'lines+markers',
          name: 'Perplexity requests',
          line: { color: '#a78bfa', width: 3 },
          marker: { size: 8 },
          yaxis: 'y2',
          hovertemplate: '%{x}<br>Perplexity %{y} requests<extra></extra>'
        }
      ], {
        title: `OpenAI spend + Perplexity usage — ${periodNames[data.period]}`,
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#edf2ff' },
        xaxis: { gridcolor: 'rgba(255,255,255,0.08)' },
        yaxis: { title: 'USD', gridcolor: 'rgba(255,255,255,0.08)' },
        yaxis2: { title: 'Requests', overlaying: 'y', side: 'right' },
        legend: { orientation: 'h' },
        margin: { t: 60, r: 60, b: 50, l: 60 }
      }, { responsive: true });

      Plotly.newPlot('modelChart', [{
        x: data.modelBreakdown.map(d => d.model),
        y: data.modelBreakdown.map(d => d.cost),
        type: 'bar',
        marker: { color: '#34d399' },
        hovertemplate: '%{x}<br>$%{y:.4f}<extra></extra>'
      }], {
        title: 'OpenAI spend by model in selected range',
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#edf2ff' },
        xaxis: { gridcolor: 'rgba(255,255,255,0.08)' },
        yaxis: { gridcolor: 'rgba(255,255,255,0.08)' },
        margin: { t: 50, r: 20, b: 80, l: 60 }
      }, { responsive: true });

      document.getElementById('notes').innerHTML = '<ul>' + data.notes.map(n => `<li>${esc(n)}</li>`).join('') + '</ul>';
      document.getElementById('queries').innerHTML = data.recentPerplexityQueries.map(q => `
        <tr>
          <td>${q.timestamp ? new Date(q.timestamp).toLocaleString() : ''}</td>
          <td><code>${esc(q.query)}</code></td>
          <td>${q.results}</td>
          <td>${q.tookMs} ms</td>
        </tr>
      `).join('');
      document.getElementById('footer').innerHTML = `Generated ${new Date(data.generatedAt).toLocaleString()} · Range ${periodNames[data.period]} · Data endpoint ${data.basePath}/data.json?period=${data.period}`;
    }
    load();
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        query = parse_qs(parsed.query)
        period = normalize_period((query.get('period') or ['all'])[0])

        if path in {'/', BASE_PATH}:
            return self._send_html(HTML)
        if path in {BASE_PATH + '/data.json', '/data.json'}:
            return self._send_json(load_usage_data(period))
        if path in {'/healthz', BASE_PATH + '/healthz'}:
            return self._send_json({'ok': True, 'generatedAt': datetime.now(timezone.utc).isoformat(), 'period': period})
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'not found')

    def log_message(self, fmt, *args):
        return

    def _send_html(self, html: str):
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Usage dashboard serving on http://{HOST}:{PORT}{BASE_PATH}/')
    server.serve_forever()
