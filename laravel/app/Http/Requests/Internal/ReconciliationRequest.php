<?php

namespace App\Http\Requests\Internal;

class ReconciliationRequest extends InternalRequest
{
    public function rules(): array
    {
        return $this->baseRules() + [
            'run_key' => ['required', 'string', 'max:191'],
            'reason' => ['required', 'string', 'max:255'],
            'status' => ['required', 'in:CONNECTING,RECONCILING,HEALTHY,DEGRADED,HALTED'],
            'detail' => ['nullable', 'string', 'max:2000'],
            'started_at' => ['required', 'date'],
            'finished_at' => ['required', 'date'],
            'summary' => ['present', 'array'],
            'mismatches' => ['present', 'array', 'max:500'],
            'mismatches.*.kind' => ['required', 'string', 'max:64'],
            'mismatches.*.severity' => ['required', 'in:INFO,WARNING,CRITICAL'],
            'mismatches.*.client_order_id' => ['nullable', 'string', 'max:64'],
            'mismatches.*.detail' => ['present', 'array'],
            'mismatches.*.resolved' => ['required', 'boolean'],
        ];
    }
}
