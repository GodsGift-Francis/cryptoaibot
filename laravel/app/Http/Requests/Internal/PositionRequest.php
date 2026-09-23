<?php

namespace App\Http\Requests\Internal;

class PositionRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'symbol' => ['required', 'string', 'max:32'],
            'side' => ['required', 'in:LONG'],
            'quantity' => self::DECIMAL,
            'average_entry_price' => self::DECIMAL,
            'realized_pnl' => self::DECIMAL,
            'unrealized_pnl' => self::DECIMAL,
            'last_mark_price' => self::NULLABLE_DECIMAL,
            'stop_price' => self::NULLABLE_DECIMAL,
            'opened_at' => ['nullable', 'date'],
            'updated_at' => ['required', 'date'],
        ];
    }
}
