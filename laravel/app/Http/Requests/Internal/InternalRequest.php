<?php

namespace App\Http\Requests\Internal;

use Illuminate\Contracts\Validation\Validator;
use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Http\Exceptions\HttpResponseException;
use Illuminate\Validation\Rule;

/** Base for engine payloads. Authentication is done by the bot.internal middleware. */
abstract class InternalRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    protected function baseRules(): array
    {
        return ['bot_instance' => ['required', 'string', Rule::in([(string) $this->route('instance')])]];
    }

    protected function failedValidation(Validator $validator): void
    {
        throw new HttpResponseException(response()->json(['message' => 'Invalid payload', 'errors' => $validator->errors()], 422));
    }

    protected const DECIMAL = ['required', 'numeric'];
    protected const NULLABLE_DECIMAL = ['nullable', 'numeric'];
    protected const STATUS = 'in:INTENT_CREATED,SUBMITTING,NEW,PARTIALLY_FILLED,FILLED,CANCELED,REJECTED,EXPIRED';
}
