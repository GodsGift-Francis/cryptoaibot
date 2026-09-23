<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('signals', function (Blueprint $t) {
            $t->string('client_signal_id')->nullable()->unique();
            $t->string('strategy_name')->nullable();
            $t->string('strategy_version')->nullable();
            $t->timestamp('candle_open_time')->nullable();
        });
        Schema::table('bot_events', function (Blueprint $t) {
            $t->string('idempotency_key')->nullable()->unique();
            $t->string('severity')->default('INFO');
            $t->index(['bot_instance_id', 'severity']);
        });
        Schema::table('trades', function (Blueprint $t) {
            // trades is kept only as a legacy V1 summary table; raw data lives in orders/fills.
            $t->string('client_order_id')->nullable()->index();
            $t->string('exchange_order_id')->nullable();
        });
    }

    public function down(): void
    {
        Schema::table('trades', fn (Blueprint $t) => $t->dropColumn(['client_order_id', 'exchange_order_id']));
        Schema::table('bot_events', function (Blueprint $t) {
            $t->dropIndex(['bot_instance_id', 'severity']);
            $t->dropColumn(['idempotency_key', 'severity']);
        });
        Schema::table('signals', fn (Blueprint $t) => $t->dropColumn(['client_signal_id', 'strategy_name', 'strategy_version', 'candle_open_time']));
    }
};
