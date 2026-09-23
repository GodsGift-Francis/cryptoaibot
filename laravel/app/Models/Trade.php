<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

/** @deprecated V1 summary table. Orders and fills are the source of truth in V1.1. */
class Trade extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['metadata' => 'array', 'occurred_at' => 'datetime'];
    }
}
