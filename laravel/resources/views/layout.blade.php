<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{{ $title ?? 'Crypto Trading Control Plane' }}</title>
<style>
:root{--bg:#0b1020;--card:#121a2d;--line:#26324d;--muted:#94a3b8;--text:#e5e7eb;--good:#34d399;--warn:#fbbf24;--bad:#fb7185}
*{box-sizing:border-box}body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--text);margin:0}
.wrap{max-width:1280px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-top:14px}
.label{color:var(--muted);font-size:12px;letter-spacing:.04em;text-transform:uppercase}.value{font-size:20px;font-weight:600;margin-top:4px}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
button{padding:9px 14px;border:0;border-radius:8px;margin:4px 4px 0 0;cursor:pointer;font-weight:600}
.danger{background:#7f1d1d;color:#fff}.normal{background:#e5e7eb;color:#111827}.admin{background:#1e3a8a;color:#fff}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:7px;border-bottom:1px solid var(--line);white-space:nowrap}
input{width:100%;padding:10px;border-radius:8px;border:1px solid var(--line);background:#0f1628;color:var(--text);margin:6px 0 12px}
.flash{background:#14532d;border:1px solid #166534;padding:10px;border-radius:8px}.error{background:#4c0519;border:1px solid #881337;padding:10px;border-radius:8px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
</style>
</head>
<body><div class="wrap">@yield('content')</div></body>
</html>
