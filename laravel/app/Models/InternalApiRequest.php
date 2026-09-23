<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class InternalApiRequest extends Model
{
    public const UPDATED_AT = null;

    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['response_body' => 'array', 'created_at' => 'datetime'];
    }
}
