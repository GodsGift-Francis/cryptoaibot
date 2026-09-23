<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('bot_instances', function (Blueprint $t) {
            $t->string('reconciliation_state')->default('CONNECTING');
            $t->text('reconciliation_reason')->nullable();
            $t->string('reconciliation_ack_token')->nullable();
            $t->timestamp('last_reconciled_at')->nullable();
            $t->boolean('ws_connected')->default(false);
            $t->timestamp('last_ws_event_at')->nullable();
            $t->timestamp('last_market_data_at')->nullable();
            $t->timestamp('last_cycle_at')->nullable();
            $t->timestamp('trading_heartbeat_at')->nullable();
            $t->timestamp('reconciliation_heartbeat_at')->nullable();
            $t->unsignedInteger('non_terminal_orders')->default(0);
            $t->unsignedInteger('open_mismatches')->default(0);
            $t->json('health')->nullable();
        });
    }

    public function down(): void
    {
        Schema::table('bot_instances', function (Blueprint $t) {
            $t->dropColumn(['reconciliation_state', 'reconciliation_reason', 'reconciliation_ack_token',
                'last_reconciled_at', 'ws_connected', 'last_ws_event_at', 'last_market_data_at', 'last_cycle_at',
                'trading_heartbeat_at', 'reconciliation_heartbeat_at', 'non_terminal_orders', 'open_mismatches', 'health']);
        });
    }
};
