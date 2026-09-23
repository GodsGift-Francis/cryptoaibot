@extends('layout')
@section('content')
@php
  $recon = $bot->reconciliation_state;
  $reconClass = $recon === 'HEALTHY' ? 'good' : ($recon === 'HALTED' ? 'bad' : 'warn');
  $statusClass = $bot->status === 'RUNNING' ? 'good' : (in_array($bot->status, ['EMERGENCY_STOP','STALE','OFFLINE']) ? 'bad' : 'warn');
@endphp
<header>
  <div><h1 style="margin:0">Crypto Trading Control Plane</h1>
  <div class="muted">Instance <strong>{{ $bot->name }}</strong> · {{ $bot->symbol }} · {{ $bot->timeframe }}</div></div>
  <form method="post" action="{{ route('logout') }}">@csrf<span class="muted">{{ auth()->user()->email }} ({{ auth()->user()->role }})</span> <button class="normal">Sign out</button></form>
</header>
@if (session('status'))<div class="flash" style="margin-top:14px">{{ session('status') }}</div>@endif
@if ($bot->mode === 'LIVE')<div class="error" style="margin-top:14px"><strong>LIVE MODE</strong> — real funds at risk.</div>@endif

<div class="grid" style="margin-top:14px">
  <div class="card"><div class="label">Mode</div><div class="value">{{ $bot->mode }}</div></div>
  <div class="card"><div class="label">Control state</div><div class="value {{ $statusClass }}">{{ $bot->status }}</div>
    <div class="muted">enabled: {{ $bot->enabled ? 'yes' : 'no' }} · emergency: {{ $bot->emergency_stop ? 'YES' : 'no' }}</div></div>
  <div class="card"><div class="label">Reconciliation</div><div class="value {{ $reconClass }}">{{ $recon }}</div>
    <div class="muted" style="white-space:normal">{{ $bot->reconciliation_reason }}</div></div>
  <div class="card"><div class="label">User data stream</div><div class="value {{ $bot->ws_connected ? 'good' : ($bot->mode === 'PAPER' ? 'muted' : 'bad') }}">{{ $bot->mode === 'PAPER' ? 'n/a (paper)' : ($bot->ws_connected ? 'connected' : 'disconnected') }}</div>
    <div class="muted">last event: {{ $bot->last_ws_event_at?->diffForHumans() ?? '—' }}</div></div>
  <div class="card"><div class="label">Trading worker</div><div class="value {{ $tradingStale ? 'bad' : 'good' }}">{{ $tradingStale ? 'STALE' : 'alive' }}</div>
    <div class="muted">{{ $bot->trading_heartbeat_at?->diffForHumans() ?? 'never' }}</div></div>
  <div class="card"><div class="label">Reconciliation worker</div><div class="value {{ $reconStale ? 'bad' : 'good' }}">{{ $bot->mode === 'PAPER' ? 'n/a (paper)' : ($reconStale ? 'STALE' : 'alive') }}</div>
    <div class="muted">{{ $bot->reconciliation_heartbeat_at?->diffForHumans() ?? 'never' }}</div></div>
  <div class="card"><div class="label">Last reconciled</div><div class="value">{{ $bot->last_reconciled_at?->diffForHumans() ?? 'never' }}</div></div>
  <div class="card"><div class="label">Market data / cycle</div><div class="value" style="font-size:15px">{{ $bot->last_market_data_at?->diffForHumans() ?? '—' }}</div>
    <div class="muted">cycle {{ $bot->last_cycle_at?->diffForHumans() ?? '—' }}</div></div>
  <div class="card"><div class="label">Open orders / mismatches</div><div class="value {{ $bot->open_mismatches ? 'bad' : '' }}">{{ $bot->non_terminal_orders }} / {{ $bot->open_mismatches }}</div></div>
</div>

@can('operate-bot')
<div class="card">
  <div class="label">Controls (audited)</div>
  <form method="post" action="{{ route('bot.pause') }}" style="display:inline">@csrf<button class="normal">Pause</button></form>
  <form method="post" action="{{ route('bot.resume') }}" style="display:inline">@csrf<button class="normal">Resume</button></form>
  <form method="post" action="{{ route('bot.emergency') }}" style="display:inline" onsubmit="return confirm('Engage EMERGENCY STOP?')">@csrf<button class="danger">Emergency stop</button></form>
  @can('administer-bot')
    @if ($bot->emergency_stop)
    <form method="post" action="{{ route('bot.clear-emergency') }}" style="display:inline" onsubmit="return confirm('Clear emergency stop? The bot stays paused.')">@csrf<button class="admin">Clear emergency stop</button></form>
    @endif
    @if ($recon === 'HALTED')
    <form method="post" action="{{ route('bot.ack-reconciliation') }}" style="display:inline" onsubmit="return confirm('Acknowledge the halt? Only do this after investigating the mismatch.')">@csrf<button class="admin">Acknowledge reconciliation halt</button></form>
    @endif
  @endcan
</div>
@endcan

<div class="card scroll"><h2>Positions</h2><table><tr><th>Symbol</th><th>Qty</th><th>Avg entry</th><th>Stop</th><th>Mark</th><th>Unrealized</th><th>Realized</th><th>Opened</th></tr>
@forelse($positions as $p)<tr><td>{{ $p->symbol }}</td><td>{{ $p->quantity + 0 }}</td><td>{{ $p->average_entry_price + 0 }}</td><td>{{ $p->stop_price !== null ? $p->stop_price + 0 : '—' }}</td><td>{{ $p->last_mark_price !== null ? $p->last_mark_price + 0 : '—' }}</td><td>{{ round($p->unrealized_pnl, 2) }}</td><td>{{ round($p->realized_pnl, 2) }}</td><td>{{ $p->opened_at ?? '—' }}</td></tr>
@empty<tr><td colspan="8" class="muted">No positions reported yet.</td></tr>@endforelse</table></div>

<div class="card scroll"><h2>Orders</h2><table><tr><th>ID</th><th>Purpose</th><th>Side</th><th>Type</th><th>Status</th><th>Req</th><th>Exec</th><th>Avg</th><th>Client ID</th><th>Exchange ID</th><th>Submitted</th><th>Note</th></tr>
@forelse($orders as $o)<tr><td>{{ $o->id }}</td><td>{{ $o->purpose }}</td><td>{{ $o->side }}</td><td>{{ $o->type }}</td>
<td class="{{ $o->status === 'FILLED' ? 'good' : (in_array($o->status, ['REJECTED','EXPIRED']) ? 'bad' : 'warn') }}">{{ $o->status }}</td>
<td>{{ $o->requested_quantity + 0 }}</td><td>{{ $o->executed_quantity + 0 }}</td><td>{{ $o->average_fill_price !== null ? $o->average_fill_price + 0 : '—' }}</td>
<td>{{ $o->client_order_id }}</td><td>{{ $o->exchange_order_id ?? '—' }}</td><td>{{ $o->submitted_at }}</td>
<td>{{ $o->submission_ambiguous ? 'ambiguous→queried ' : '' }}{{ $o->reject_reason }}</td></tr>
@empty<tr><td colspan="12" class="muted">No orders yet.</td></tr>@endforelse</table></div>

<div class="card scroll"><h2>Fills</h2><table><tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>Quote</th><th>Fee</th><th>Order</th><th>Trade ID</th></tr>
@forelse($fills as $f)<tr><td>{{ $f->execution_time }}</td><td>{{ $f->side }}</td><td>{{ $f->quantity + 0 }}</td><td>{{ $f->price + 0 }}</td><td>{{ round($f->quote_quantity, 2) }}</td><td>{{ $f->commission + 0 }} {{ $f->commission_asset }}</td><td>{{ $f->client_order_id }}</td><td>{{ $f->exchange_trade_id }}</td></tr>
@empty<tr><td colspan="8" class="muted">No fills yet.</td></tr>@endforelse</table></div>

<div class="card scroll"><h2>Reconciliation runs</h2><table><tr><th>Started</th><th>Reason</th><th>Status</th><th>Detail</th><th>Mismatches</th></tr>
@forelse($runs as $run)<tr><td>{{ $run->started_at }}</td><td>{{ $run->reason }}</td><td class="{{ $run->status === 'HEALTHY' ? 'good' : ($run->status === 'HALTED' ? 'bad' : 'warn') }}">{{ $run->status }}</td><td style="white-space:normal">{{ $run->detail }}</td>
<td style="white-space:normal">@foreach($run->mismatches as $m)<span class="{{ $m->severity === 'CRITICAL' ? 'bad' : 'warn' }}">{{ $m->severity }} {{ $m->kind }}</span>{{ $m->client_order_id ? ' ('.$m->client_order_id.')' : '' }}<br>@endforeach</td></tr>
@empty<tr><td colspan="5" class="muted">No reconciliation runs yet.</td></tr>@endforelse</table></div>

<div class="card scroll"><h2>Recent signals</h2><table><tr><th>Time</th><th>Action</th><th>Score</th><th>Price</th><th>Reasons</th></tr>
@forelse($signals as $s)<tr><td>{{ $s->occurred_at }}</td><td>{{ $s->action }}</td><td>{{ $s->score + 0 }}</td><td>{{ $s->price !== null ? $s->price + 0 : '—' }}</td><td style="white-space:normal">{{ implode(' | ', $s->reasons ?? []) }}</td></tr>
@empty<tr><td colspan="5" class="muted">No signals yet.</td></tr>@endforelse</table></div>

<div class="card scroll"><h2>Events</h2><table><tr><th>Time</th><th>Severity</th><th>Type</th><th>Message</th></tr>
@forelse($events as $e)<tr><td>{{ $e->occurred_at }}</td><td class="{{ $e->severity === 'CRITICAL' ? 'bad' : ($e->severity === 'WARNING' ? 'warn' : '') }}">{{ $e->severity }}</td><td>{{ $e->event_type }}</td><td style="white-space:normal">{{ $e->message }}</td></tr>
@empty<tr><td colspan="4" class="muted">No events yet.</td></tr>@endforelse</table></div>

<div class="card scroll"><h2>Audit log</h2><table><tr><th>Time</th><th>Actor</th><th>Channel</th><th>Action</th><th>Before → After</th></tr>
@forelse($audit as $a)<tr><td>{{ $a->created_at }}</td><td>{{ $a->actor }}</td><td>{{ $a->channel }}</td><td>{{ $a->action }}</td><td style="white-space:normal">{{ json_encode($a->before) }} → {{ json_encode($a->after) }}</td></tr>
@empty<tr><td colspan="5" class="muted">No control actions yet.</td></tr>@endforelse</table></div>
@endsection
