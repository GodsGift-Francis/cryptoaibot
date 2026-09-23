<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class BotInstance extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return [
            'enabled' => 'boolean', 'emergency_stop' => 'boolean', 'ws_connected' => 'boolean',
            'metadata' => 'array', 'health' => 'array',
            'last_heartbeat_at' => 'datetime', 'trading_heartbeat_at' => 'datetime',
            'reconciliation_heartbeat_at' => 'datetime', 'last_reconciled_at' => 'datetime',
            'last_ws_event_at' => 'datetime', 'last_market_data_at' => 'datetime', 'last_cycle_at' => 'datetime',
        ];
    }

    public static function forName(string $name): self
    {
        return static::firstOrCreate(['name' => $name], [
            'mode' => 'PAPER', 'enabled' => false, 'emergency_stop' => false, 'status' => 'OFFLINE',
            'symbol' => 'BTC/USDT', 'timeframe' => '1h',
        ]);
    }

    public function signals() { return $this->hasMany(Signal::class); }
    public function trades() { return $this->hasMany(Trade::class); }
    public function orders() { return $this->hasMany(Order::class); }
    public function fills() { return $this->hasMany(Fill::class); }
    public function positions() { return $this->hasMany(Position::class); }
    public function reconciliationRuns() { return $this->hasMany(ReconciliationRun::class); }
    public function events() { return $this->hasMany(BotEvent::class); }
    public function auditLogs() { return $this->hasMany(AuditLog::class); }
}
