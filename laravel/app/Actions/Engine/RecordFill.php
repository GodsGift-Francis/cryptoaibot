<?php

namespace App\Actions\Engine;

use App\Models\BotInstance;
use App\Models\Fill;
use App\Models\Order;

/** One row per exchange execution (unique event_key): duplicates are ignored, never double counted. */
class RecordFill
{
    public function __invoke(BotInstance $bot, array $d): array
    {
        $orderId = Order::where('bot_instance_id', $bot->id)->where('client_order_id', $d['client_order_id'])->value('id');
        $inserted = Fill::query()->insertOrIgnore([
            'bot_instance_id' => $bot->id, 'order_id' => $orderId, 'client_order_id' => $d['client_order_id'],
            'exchange_trade_id' => $d['exchange_trade_id'] ?? null, 'symbol' => $d['symbol'], 'side' => $d['side'],
            'quantity' => $d['quantity'], 'price' => $d['price'], 'quote_quantity' => $d['quote_quantity'],
            'commission' => $d['commission'], 'commission_asset' => $d['commission_asset'] ?? null,
            'execution_time' => \Illuminate\Support\Carbon::parse($d['execution_time']), 'event_key' => $d['event_key'],
            'created_at' => now(), 'updated_at' => now(),
        ]);
        return ['ok' => true, 'inserted' => $inserted === 1];
    }
}
