<?php

namespace App\Http\Controllers;

use App\Models\AuditLog;
use App\Models\BotInstance;
use App\Services\BotControlService;
use App\Services\ControlActor;
use Illuminate\Http\RedirectResponse;
use Illuminate\Http\Request;

class DashboardController extends Controller
{
    public function __construct(private readonly BotControlService $control) {}

    private function bot(): BotInstance
    {
        return BotInstance::forName((string) config('trading.instance_id'));
    }

    public function index()
    {
        $b = $this->bot();
        $staleAfter = now()->subSeconds((int) config('trading.heartbeat_stale_seconds', 180));
        return view('dashboard', [
            'bot' => $b,
            'tradingStale' => ! $b->trading_heartbeat_at || $b->trading_heartbeat_at->lt($staleAfter),
            'reconStale' => $b->mode !== 'PAPER' && (! $b->reconciliation_heartbeat_at || $b->reconciliation_heartbeat_at->lt($staleAfter)),
            'positions' => $b->positions()->orderBy('symbol')->get(),
            'orders' => $b->orders()->latest('id')->limit(25)->get(),
            'fills' => $b->fills()->latest('execution_time')->limit(25)->get(),
            'signals' => $b->signals()->latest('occurred_at')->limit(15)->get(),
            'runs' => $b->reconciliationRuns()->with('mismatches')->latest('started_at')->limit(10)->get(),
            'events' => $b->events()->latest('occurred_at')->limit(25)->get(),
            'audit' => AuditLog::where('bot_instance_id', $b->id)->latest('created_at')->limit(15)->get(),
        ]);
    }

    public function pause(Request $r): RedirectResponse
    {
        return back()->with('status', $this->control->pause($this->bot(), ControlActor::web($r)));
    }

    public function resume(Request $r): RedirectResponse
    {
        return back()->with('status', $this->control->resume($this->bot(), ControlActor::web($r)));
    }

    public function emergencyStop(Request $r): RedirectResponse
    {
        return back()->with('status', $this->control->emergencyStop($this->bot(), ControlActor::web($r)));
    }

    public function clearEmergencyStop(Request $r): RedirectResponse
    {
        return back()->with('status', $this->control->clearEmergencyStop($this->bot(), ControlActor::web($r)));
    }

    public function acknowledgeReconciliation(Request $r): RedirectResponse
    {
        return back()->with('status', $this->control->acknowledgeReconciliation($this->bot(), ControlActor::web($r)));
    }
}
