<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('fills', function (Blueprint $t) {
            $t->id();
            $t->foreignId('bot_instance_id')->constrained()->cascadeOnDelete();
            $t->foreignId('order_id')->nullable()->constrained()->nullOnDelete();
            $t->string('client_order_id');
            $t->string('exchange_trade_id')->nullable();
            $t->string('symbol');
            $t->string('side');
            $t->decimal('quantity', 36, 18);
            $t->decimal('price', 36, 18);
            $t->decimal('quote_quantity', 36, 18);
            $t->decimal('commission', 36, 18)->default(0);
            $t->string('commission_asset')->nullable();
            $t->timestamp('execution_time');
            $t->string('event_key')->unique(); // idempotency: one row per exchange execution
            $t->timestamps();
            $t->index(['bot_instance_id', 'execution_time']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('fills');
    }
};
