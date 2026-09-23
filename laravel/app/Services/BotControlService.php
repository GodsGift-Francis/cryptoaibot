<?php

namespace App\Services;

use App\Models\AuditLog;
use App\Models\BotEvent;
use App\Models\BotInstance;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;

/**
 * The only place operator control state changes. Every change is audited in the same
 * transaction. The engine reads this state on every cycle and fails closed if it cannot.
 */
class BotControlService
{
    private const FIELDS = ['enabled', 'emergency_stop', 'status', 'reconciliation_ack_token'];

    public function pause(BotInstance $bot, ControlActor $actor): string
    {
        return $this->change($bot, $actor, 'pause', ['enabled' => false, 'status' => 'PAUSED'], 'Trading paused: no new entries.');
    }

    public function resume(BotInstance $bot, ControlActor $actor): string
    {
        if ($bot->emergency_stop) {
            return 'Emergency stop is engaged. An admin must clear it from the dashboard first.';
        }
        return $this->change($bot, $actor, 'resume', ['enabled' => true, 'status' => 'RUNNING'], 'Trading resumed.');
    }

    public function emergencyStop(BotInstance $bot, ControlActor $actor): string
    {
        return $this->change($bot, $actor, 'emergency_stop',
            ['enabled' => false, 'emergency_stop' => true, 'status' => 'EMERGENCY_STOP'],
            'Emergency stop engaged: no entries, no strategy exits (stop-loss protection stays active).');
    }

    public function clearEmergencyStop(BotInstance $bot, ControlActor $actor): string
    {
        // Clearing does NOT resume trading; resume is a separate, deliberate action.
        return $this->change($bot, $actor, 'clear_emergency_stop',
            ['emergency_stop' => false, 'enabled' => false, 'status' => 'PAUSED'], 'Emergency stop cleared; bot remains paused.');
    }

    public function acknowledgeReconciliation(BotInstance $bot, ControlActor $actor): string
    {
        return $this->change($bot, $actor, 'acknowledge_reconciliation_halt',
            ['reconciliation_ack_token' => (string) Str::uuid()],
            'Acknowledged. The engine will re-run reconciliation and only resume if it is clean.');
    }

    private function change(BotInstance $bot, ControlActor $actor, string $action, array $changes, string $message): string
    {
        DB::transaction(function () use ($bot, $actor, $action, $changes, $message) {
            $bot->refresh();
            $before = $bot->only(self::FIELDS);
            $bot->update($changes);
            AuditLog::create([
                'user_id' => $actor->user?->id, 'bot_instance_id' => $bot->id, 'actor' => $actor->actor,
                'channel' => $actor->channel, 'action' => $action, 'before' => $before,
                'after' => $bot->only(self::FIELDS), 'ip_address' => $actor->ip, 'user_agent' => $actor->userAgent,
            ]);
            BotEvent::create([
                'bot_instance_id' => $bot->id, 'event_type' => 'CONTROL_'.strtoupper($action), 'severity' => 'WARNING',
                'message' => "{$message} (by {$actor->actor} via {$actor->channel})", 'payload' => $changes,
                'occurred_at' => now(),
            ]);
        });
        return $message;
    }
}
