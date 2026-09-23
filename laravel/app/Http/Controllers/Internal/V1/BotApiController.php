<?php

namespace App\Http\Controllers\Internal\V1;

use App\Actions\Engine\RecordFill;
use App\Actions\Engine\RecordOrder;
use App\Actions\Engine\RecordPosition;
use App\Actions\Engine\RecordReconciliationRun;
use App\Http\Controllers\Controller;
use App\Http\Requests\Internal\EventRequest;
use App\Http\Requests\Internal\FillRequest;
use App\Http\Requests\Internal\HeartbeatRequest;
use App\Http\Requests\Internal\OrderRequest;
use App\Http\Requests\Internal\PositionRequest;
use App\Http\Requests\Internal\ReconciliationRequest;
use App\Http\Requests\Internal\SignalRequest;
use App\Models\BotEvent;
use App\Models\BotInstance;
use App\Models\Signal;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Carbon;

/**
 * Internal engine API (/api/internal/v1/bots/{instance}/...). Not reachable from browser
 * routes; authenticated by the bot.internal middleware and replay-protected by bot.idempotent.
 */
class BotApiController extends Controller
{
    private function bot(Request $r): BotInstance
    {
        return $r->attributes->get('bot');
    }

    public function control(Request $r): JsonResponse
    {
        $b = $this->bot($r)->refresh();
        return response()->json([
            'enabled' => (bool) $b->enabled,
            'emergency_stop' => (bool) $b->emergency_stop,
            'mode' => $b->mode,
            'symbol' => $b->symbol,
            'timeframe' => $b->timeframe,
            'reconciliation_ack_token' => $b->reconciliation_ack_token,
            'server_time' => now()->toIso8601String(),
        ]);
    }

    public function heartbeat(HeartbeatRequest $r): JsonResponse
    {
        $d = $r->validated();
        $b = $this->bot($r);
        $update = [
            'status' => $d['status'] === 'RUNNING' && $b->emergency_stop ? 'EMERGENCY_STOP' : ($b->enabled ? $d['status'] : 'PAUSED'),
            'mode' => $d['mode'], 'symbol' => $d['symbol'], 'last_heartbeat_at' => now(),
            'last_error' => $d['error'] ?? null, 'ws_connected' => $d['ws_connected'],
            'reconciliation_state' => $d['reconciliation_state'],
            'reconciliation_reason' => $d['reconciliation_reason'] ?? null,
            'non_terminal_orders' => $d['non_terminal_orders'], 'open_mismatches' => $d['open_mismatches'],
            'health' => $d,
        ];
        foreach (['last_market_data_at', 'last_cycle_at', 'last_ws_event_at', 'last_reconciled_at'] as $k) {
            if (! empty($d[$k])) {
                $update[$k] = Carbon::parse($d[$k]);
            }
        }
        if (! empty($d['timeframe'])) {
            $update['timeframe'] = $d['timeframe'];
        }
        $update[$d['worker'] === 'reconciliation' ? 'reconciliation_heartbeat_at' : 'trading_heartbeat_at'] = now();
        $b->update($update);
        return response()->json(['ok' => true]);
    }

    public function signals(SignalRequest $r): JsonResponse
    {
        $d = $r->validated();
        $candle = null;
        try {
            $candle = Carbon::parse(explode('#', (string) ($d['candle_open_time'] ?? ''))[0] ?: null);
        } catch (\Throwable) {
        }
        $signal = Signal::firstOrCreate(['client_signal_id' => $d['client_signal_id']], [
            'bot_instance_id' => $this->bot($r)->id, 'symbol' => $d['symbol'], 'action' => $d['action'],
            'score' => $d['score'], 'price' => $d['price'] ?? null, 'reasons' => $d['reasons'], 'mode' => $d['mode'],
            'strategy_name' => $d['strategy_name'], 'strategy_version' => $d['strategy_version'],
            'candle_open_time' => $candle, 'occurred_at' => Carbon::parse($d['occurred_at']),
        ]);
        return response()->json(['ok' => true, 'signal_id' => $signal->id]);
    }

    public function orders(OrderRequest $r, RecordOrder $action): JsonResponse
    {
        return response()->json($action($this->bot($r), $r->validated()));
    }

    public function fills(FillRequest $r, RecordFill $action): JsonResponse
    {
        return response()->json($action($this->bot($r), $r->validated()));
    }

    public function positions(PositionRequest $r, RecordPosition $action): JsonResponse
    {
        return response()->json($action($this->bot($r), $r->validated()));
    }

    public function reconciliation(ReconciliationRequest $r, RecordReconciliationRun $action): JsonResponse
    {
        return response()->json($action($this->bot($r), $r->validated()));
    }

    public function events(EventRequest $r): JsonResponse
    {
        $d = $r->validated();
        $event = BotEvent::firstOrCreate(['idempotency_key' => $r->header('X-Request-Id')], [
            'bot_instance_id' => $this->bot($r)->id, 'event_type' => $d['event_type'], 'severity' => $d['severity'],
            'message' => $d['message'], 'payload' => $d['payload'], 'occurred_at' => Carbon::parse($d['occurred_at']),
        ]);
        return response()->json(['ok' => true, 'event_id' => $event->id]);
    }
}
