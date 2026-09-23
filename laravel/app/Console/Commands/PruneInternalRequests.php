<?php

namespace App\Console\Commands;

use App\Models\InternalApiRequest;
use Illuminate\Console\Command;

class PruneInternalRequests extends Command
{
    protected $signature = 'bot:prune-internal-requests';

    protected $description = 'Delete old internal API idempotency records.';

    public function handle(): int
    {
        $days = (int) config('trading.internal_request_retention_days', 14);
        $n = InternalApiRequest::where('created_at', '<', now()->subDays($days))->delete();
        $this->info("Pruned {$n} internal request records older than {$days} days.");
        return self::SUCCESS;
    }
}
