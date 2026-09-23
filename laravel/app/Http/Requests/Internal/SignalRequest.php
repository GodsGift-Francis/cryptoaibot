<?php

namespace App\Http\Requests\Internal;

class SignalRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'client_signal_id' => ['required', 'string', 'max:191'],
            'symbol' => ['required', 'string', 'max:32'],
            'action' => ['required', 'in:BUY,SELL,HOLD'],
            'score' => ['required', 'numeric'],
            'price' => self::NULLABLE_DECIMAL,
            'reasons' => ['present', 'array'],
            'reasons.*' => ['string', 'max:500'],
            'candle_open_time' => ['nullable', 'string', 'max:64'],
            'strategy_name' => ['required', 'string', 'max:64'],
            'strategy_version' => ['required', 'string', 'max:32'],
            'occurred_at' => ['required', 'date'],
            'mode' => ['required', 'in:PAPER,TESTNET,LIVE'],
        ];
    }
}
