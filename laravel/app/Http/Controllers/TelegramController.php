<?php

namespace App\Http\Controllers;

use App\Models\BotInstance;
use App\Services\BotControlService;
use App\Services\ControlActor;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Http;

/**
 * Telegram operator channel. Allow-listed chats may pause / resume / emergency-stop.
 * Clearing an emergency stop and acknowledging a reconciliation halt are deliberately
 * dashboard-only (admin role). Redelivered updates are ignored by update_id.
 */
class TelegramController extends Controller
{
    public function __construct(private readonly BotControlService $control) {}

    public function webhook(Request $r, string $secret): JsonResponse
    {
        $expected = (string) config('trading.telegram_webhook_secret');
        if (strlen($expected) < 16 || ! hash_equals($expected, $secret)) {
            return response()->json(['ok' => false], 403);
        }
        $updateId = (string) data_get($r->all(), 'update_id', '');
        if ($updateId !== '' && ! Cache::add("tg_update:{$updateId}", 1, now()->addDay())) {
            return response()->json(['ok' => true]);   // duplicate delivery
        }
        $chat = (string) data_get($r->all(), 'message.chat.id', '');
        $text = strtolower(trim((string) data_get($r->all(), 'message.text', '')));
        if (! in_array($chat, config('trading.telegram_allowed_chat_ids'), true)) {
            return response()->json(['ok' => true]);   // silently ignore non-allow-listed chats
        }
        $b = BotInstance::forName((string) config('trading.instance_id'));
        $actor = ControlActor::telegram($chat);
        $reply = match (strtok($text, ' @')) {
            '/start', '/help' => "Commands: /status /signals /fills /pause /resume /emergency",
            '/status' => $this->status($b),
            '/signals' => $this->signals($b),
            '/fills', '/trades' => $this->fills($b),
            '/pause' => $this->control->pause($b, $actor),
            '/resume' => $this->control->resume($b, $actor),
            '/emergency' => $this->control->emergencyStop($b, $actor),
            default => 'Unknown command. Try /help',
        };
        $this->send($chat, $reply);
        return response()->json(['ok' => true]);
    }

    private function status(BotInstance $b): string
    {
        return "STATUS\nMode: {$b->mode}\nStatus: {$b->status}\nEnabled: ".($b->enabled ? 'YES' : 'NO')
            ."\nEmergency: ".($b->emergency_stop ? 'YES' : 'NO')
            ."\nReconciliation: {$b->reconciliation_state}".($b->reconciliation_reason ? " ({$b->reconciliation_reason})" : '')
            ."\nWebSocket: ".($b->ws_connected ? 'connected' : 'disconnected')
            ."\nOpen orders: {$b->non_terminal_orders}  Mismatches: {$b->open_mismatches}"
            ."\nLast heartbeat: ".($b->last_heartbeat_at?->diffForHumans() ?? 'never');
    }

    private function signals(BotInstance $b): string
    {
        $rows = $b->signals()->latest('occurred_at')->limit(5)->get();
        return $rows->isEmpty() ? 'No signals yet.' : "LATEST SIGNALS\n".$rows->map(fn ($s) => "{$s->occurred_at}: {$s->action} {$s->score} @ {$s->price}")->join("\n");
    }

    private function fills(BotInstance $b): string
    {
        $rows = $b->fills()->latest('execution_time')->limit(5)->get();
        return $rows->isEmpty() ? 'No fills yet.' : "LATEST FILLS\n".$rows->map(fn ($f) => "{$f->execution_time}: {$f->side} {$f->quantity} @ {$f->price}")->join("\n");
    }

    private function send(string $chat, string $text): void
    {
        $token = (string) config('trading.telegram_token');
        if ($token === '') {
            return;
        }
        Http::timeout(10)->post("https://api.telegram.org/bot{$token}/sendMessage", ['chat_id' => $chat, 'text' => $text]);
    }
}
