<?php

namespace App\Http\Requests\Internal;

class EventRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'event_type' => ['required', 'string', 'max:64', 'regex:/^[A-Z0-9_]+$/'],
            'severity' => ['required', 'in:INFO,WARNING,CRITICAL'],
            'message' => ['required', 'string', 'max:4000'],
            'payload' => ['present', 'array'],
            'occurred_at' => ['required', 'date'],
        ];
    }
}
