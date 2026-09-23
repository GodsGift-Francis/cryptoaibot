<?php

namespace App\Actions\Engine;

use App\Models\BotInstance;
use App\Models\Position;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

/** Last-writer-wins by the engine's own updated_at: older snapshots never overwrite newer ones. */
class RecordPosition
{
    public function __invoke(BotInstance $bot, array $d): array
    {
        return DB::transaction(function () use ($bot, $d) {
            $updatedAt = Carbon::parse($d['updated_at']);
            $pos = Position::where('bot_instance_id', $bot->id)->where('symbol', $d['symbol'])->lockForUpdate()->first();
            if ($pos && $pos->engine_updated_at && $pos->engine_updated_at->gt($updatedAt)) {
                return ['ok' => true, 'applied' => false];
            }
            Position::updateOrCreate(['bot_instance_id' => $bot->id, 'symbol' => $d['symbol']], [
                'side' => $d['side'], 'quantity' => $d['quantity'], 'average_entry_price' => $d['average_entry_price'],
                'realized_pnl' => $d['realized_pnl'], 'unrealized_pnl' => $d['unrealized_pnl'],
                'last_mark_price' => $d['last_mark_price'] ?? null, 'stop_price' => $d['stop_price'] ?? null,
                'opened_at' => $d['opened_at'] ?? null, 'engine_updated_at' => $updatedAt,
            ]);
            return ['ok' => true, 'applied' => true];
        });
    }
}
