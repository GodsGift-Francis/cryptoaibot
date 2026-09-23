<?php

namespace App\Actions\Engine;

use App\Models\BotInstance;
use App\Models\Order;
use App\Models\Signal;
use Illuminate\Support\Facades\DB;

/**
 * Mirrors engine order state. The engine is authoritative; Laravel only accepts a
 * payload whose engine `version` is newer than what it already holds, so replayed or
 * out-of-order deliveries can never move an order backwards.
 */
class RecordOrder
{
    public function __invoke(BotInstance $bot, array $d): array
    {
        return DB::transaction(function () use ($bot, $d) {
            $order = Order::where('bot_instance_id', $bot->id)->where('client_order_id', $d['client_order_id'])->lockForUpdate()->first();
            if ($order && $order->engine_version >= $d['version']) {
                return ['ok' => true, 'applied' => false, 'order_id' => $order->id];
            }
            $attrs = [
                'client_intent_id' => $d['client_intent_id'], 'exchange' => $d['exchange'], 'symbol' => $d['symbol'],
                'side' => $d['side'], 'type' => $d['type'], 'purpose' => $d['purpose'], 'status' => $d['status'],
                'exchange_order_id' => $d['exchange_order_id'] ?? null,
                'requested_quantity' => $d['requested_quantity'], 'executed_quantity' => $d['executed_quantity'],
                'cumulative_quote_quantity' => $d['cumulative_quote_quantity'],
                'average_fill_price' => $d['average_fill_price'] ?? null, 'limit_price' => $d['limit_price'] ?? null,
                'stop_price' => $d['stop_price'] ?? null, 'submission_ambiguous' => $d['submission_ambiguous'],
                'reject_reason' => $d['reject_reason'] ?? null, 'submitted_at' => $d['submitted_at'] ?? null,
                'acknowledged_at' => $d['acknowledged_at'] ?? null,
                'last_exchange_update_at' => $d['last_exchange_update_at'] ?? null,
                'terminal_at' => $d['terminal_at'] ?? null, 'engine_version' => $d['version'],
            ];
            if (! $order) {
                $signalId = Signal::where('bot_instance_id', $bot->id)->where('client_signal_id', $d['signal_id'] ?? '')->value('id');
                $order = Order::create($attrs + ['bot_instance_id' => $bot->id, 'client_order_id' => $d['client_order_id'], 'signal_id' => $signalId]);
            } else {
                $order->update($attrs);
            }
            return ['ok' => true, 'applied' => true, 'order_id' => $order->id];
        });
    }
}
