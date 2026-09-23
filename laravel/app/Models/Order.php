<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class Order extends Model
{
    public const TERMINAL = ['FILLED', 'CANCELED', 'REJECTED', 'EXPIRED'];

    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['submission_ambiguous' => 'boolean', 'submitted_at' => 'datetime', 'acknowledged_at' => 'datetime',
            'last_exchange_update_at' => 'datetime', 'terminal_at' => 'datetime'];
    }

    public function botInstance() { return $this->belongsTo(BotInstance::class); }
    public function signal() { return $this->belongsTo(Signal::class); }
    public function fills() { return $this->hasMany(Fill::class); }

    public function isTerminal(): bool
    {
        return in_array($this->status, self::TERMINAL, true);
    }
}
