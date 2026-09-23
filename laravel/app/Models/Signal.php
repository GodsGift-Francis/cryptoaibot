<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Signal extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['reasons' => 'array', 'occurred_at' => 'datetime', 'candle_open_time' => 'datetime'];
    }

    public function botInstance() { return $this->belongsTo(BotInstance::class); }
}
