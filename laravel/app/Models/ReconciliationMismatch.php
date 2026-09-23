<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class ReconciliationMismatch extends Model
{
    protected $guarded = ['id'];

    protected function casts(): array
    {
        return ['detail' => 'array', 'resolved' => 'boolean'];
    }

    public function run() { return $this->belongsTo(ReconciliationRun::class, 'reconciliation_run_id'); }
}
