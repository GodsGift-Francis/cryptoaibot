<?php

namespace App\Http\Requests\Internal;

class HeartbeatRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'worker' => ['required', 'in:trading,reconciliation,test,legacy-run-once'],
            'status' => ['required', 'string', 'max:32'],
            'mode' => ['required', 'in:PAPER,TESTNET,LIVE'],
            'symbol' => ['required', 'string', 'max:32'],
            'timeframe' => ['nullable', 'string', 'max:8'],
            'time' => ['required', 'date'],
            'last_market_data_at' => ['nullable', 'date'],
            'last_cycle_at' => ['nullable', 'date'],
            'ws_connected' => ['required', 'boolean'],
            'last_ws_event_at' => ['nullable', 'date'],
            'reconciliation_state' => ['required', 'in:CONNECTING,RECONCILING,HEALTHY,DEGRADED,HALTED'],
            'reconciliation_reason' => ['nullable', 'string', 'max:2000'],
            'last_reconciled_at' => ['nullable', 'date'],
            'non_terminal_orders' => ['required', 'integer', 'min:0'],
            'open_mismatches' => ['required', 'integer', 'min:0'],
            'outbox_backlog' => ['required', 'integer', 'min:0'],
            'position_quantity' => ['nullable', 'numeric'],
            'realized_pnl' => ['nullable', 'numeric'],
            'error' => ['nullable', 'string', 'max:2000'],
        ];
    }
}
