<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class ReconciliationRun extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['summary' => 'array', 'started_at' => 'datetime', 'finished_at' => 'datetime'];
    }

    public function botInstance() { return $this->belongsTo(BotInstance::class); }
    public function mismatches() { return $this->hasMany(ReconciliationMismatch::class); }
}
