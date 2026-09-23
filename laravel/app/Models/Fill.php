<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Fill extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['execution_time' => 'datetime'];
    }

    public function order() { return $this->belongsTo(Order::class); }
    public function botInstance() { return $this->belongsTo(BotInstance::class); }
}
