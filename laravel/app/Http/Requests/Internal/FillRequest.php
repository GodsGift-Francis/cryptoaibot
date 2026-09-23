<?php

namespace App\Http\Requests\Internal;

class FillRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'event_key' => ['required', 'string', 'max:191'],
            'client_order_id' => ['required', 'string', 'max:64'],
            'exchange_trade_id' => ['nullable', 'string', 'max:64'],
            'symbol' => ['required', 'string', 'max:32'],
            'side' => ['required', 'in:BUY,SELL'],
            'quantity' => self::DECIMAL,
            'price' => self::DECIMAL,
            'quote_quantity' => self::DECIMAL,
            'commission' => self::DECIMAL,
            'commission_asset' => ['nullable', 'string', 'max:16'],
            'execution_time' => ['required', 'date'],
        ];
    }
}
