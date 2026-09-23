<?php

namespace App\Console\Commands;

use App\Models\BotEvent;
use App\Models\BotInstance;
use Illuminate\Console\Command;

/** Marks bots whose workers stopped heart-beating. The engine itself fails closed independently. */
class BotHealth extends Command
{
    protected $signature = 'bot:health';

    protected $description = 'Flag stale trading / reconciliation worker heartbeats.';

    public function handle(): int
    {
        $stale = now()->subSeconds((int) config('trading.heartbeat_stale_seconds', 180));
        foreach (BotInstance::all() as $bot) {
            $tradingDead = $bot->trading_heartbeat_at === null || $bot->trading_heartbeat_at->lt($stale);
            $reconDead = $bot->mode !== 'PAPER' && ($bot->reconciliation_heartbeat_at === null || $bot->reconciliation_heartbeat_at->lt($stale));
            if (($tradingDead || $reconDead) && ! in_array($bot->status, ['OFFLINE', 'STALE'], true) && $bot->last_heartbeat_at) {
                $bot->update(['status' => 'STALE']);
                BotEvent::create(['bot_instance_id' => $bot->id, 'event_type' => 'WORKER_HEARTBEAT_STALE', 'severity' => 'CRITICAL',
                    'message' => ($tradingDead ? 'trading ' : '').($reconDead ? 'reconciliation ' : '').'worker heartbeat stale',
                    'payload' => [], 'occurred_at' => now()]);
            }
        }
        $this->info('Bot health checked.');
        return self::SUCCESS;
    }
}
