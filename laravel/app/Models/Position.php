<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Position extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['opened_at' => 'datetime', 'engine_updated_at' => 'datetime'];
    }

    public function botInstance() { return $this->belongsTo(BotInstance::class); }
}
