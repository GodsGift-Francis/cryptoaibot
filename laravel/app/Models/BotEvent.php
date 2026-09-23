<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class BotEvent extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['payload' => 'array', 'occurred_at' => 'datetime'];
    }

    public function botInstance() { return $this->belongsTo(BotInstance::class); }
}
