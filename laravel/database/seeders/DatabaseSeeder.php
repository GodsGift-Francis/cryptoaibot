<?php

namespace Database\Seeders;

use App\Models\BotInstance;
use Illuminate\Database\Seeder;

class DatabaseSeeder extends Seeder
{
    public function run(): void
    {
        // New bots start PAUSED: an operator must deliberately resume from the dashboard.
        BotInstance::firstOrCreate(['name' => config('trading.instance_id')], [
            'mode' => 'PAPER', 'enabled' => false, 'emergency_stop' => false, 'status' => 'PAUSED',
            'symbol' => 'BTC/USDT', 'timeframe' => '1h',
        ]);
    }
}
