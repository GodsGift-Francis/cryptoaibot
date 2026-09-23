<?php

namespace App\Http\Requests\Internal;

class OrderRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'client_order_id' => ['required', 'string', 'max:64'],
            'client_intent_id' => ['required', 'string', 'max:191'],
            'signal_id' => ['nullable', 'string', 'max:191'],
            'exchange_order_id' => ['nullable', 'string', 'max:64'],
            'exchange' => ['required', 'string', 'max:32'],
            'symbol' => ['required', 'string', 'max:32'],
            'side' => ['required', 'in:BUY,SELL'],
            'type' => ['required', 'in:MARKET,LIMIT,STOP_LOSS_LIMIT'],
            'purpose' => ['required', 'in:ENTRY,EXIT,STOP_EXIT,PROTECTIVE_STOP'],
            'status' => ['required', self::STATUS],
            'requested_quantity' => self::DECIMAL,
            'executed_quantity' => self::DECIMAL,
            'cumulative_quote_quantity' => self::DECIMAL,
            'average_fill_price' => self::NULLABLE_DECIMAL,
            'limit_price' => self::NULLABLE_DECIMAL,
            'stop_price' => self::NULLABLE_DECIMAL,
            'submission_ambiguous' => ['required', 'boolean'],
            'reject_reason' => ['nullable', 'string', 'max:1000'],
            'submitted_at' => ['nullable', 'date'],
            'acknowledged_at' => ['nullable', 'date'],
            'last_exchange_update_at' => ['nullable', 'date'],
            'terminal_at' => ['nullable', 'date'],
            'version' => ['required', 'integer', 'min:0'],
        ];
    }
}
