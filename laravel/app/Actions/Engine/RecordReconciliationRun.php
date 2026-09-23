<?php

namespace App\Actions\Engine;

use App\Models\BotInstance;
use App\Models\ReconciliationRun;
use Illuminate\Support\Facades\DB;

class RecordReconciliationRun
{
    public function __invoke(BotInstance $bot, array $d): array
    {
        return DB::transaction(function () use ($bot, $d) {
            $run = ReconciliationRun::firstOrCreate(['run_key' => $d['run_key']], [
                'bot_instance_id' => $bot->id, 'reason' => $d['reason'], 'status' => $d['status'],
                'detail' => $d['detail'] ?? null, 'summary' => $d['summary'],
                'started_at' => $d['started_at'], 'finished_at' => $d['finished_at'],
            ]);
            if ($run->wasRecentlyCreated) {
                foreach ($d['mismatches'] as $m) {
                    $run->mismatches()->create([
                        'kind' => $m['kind'], 'severity' => $m['severity'], 'client_order_id' => $m['client_order_id'] ?? null,
                        'detail' => $m['detail'], 'resolved' => $m['resolved'],
                    ]);
                }
                $critical = collect($d['mismatches'])->where('severity', 'CRITICAL')->where('resolved', false)->count();
                $update = ['reconciliation_state' => $d['status'], 'reconciliation_reason' => $d['detail'] ?? null, 'open_mismatches' => $critical];
                if ($d['status'] === 'HEALTHY') {
                    $update['last_reconciled_at'] = $d['finished_at'];
                }
                $bot->update($update);
            }
            return ['ok' => true, 'run_id' => $run->id];
        });
    }
}
