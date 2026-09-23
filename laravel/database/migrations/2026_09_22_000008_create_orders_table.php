<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('orders', function (Blueprint $t) {
            $t->id();
            $t->foreignId('bot_instance_id')->constrained()->cascadeOnDelete();
            $t->foreignId('signal_id')->nullable()->constrained()->nullOnDelete();
            $t->string('exchange');
            $t->string('symbol');
            $t->string('side');
            $t->string('type');
            $t->string('purpose');
            $t->string('client_order_id');
            $t->string('client_intent_id');
            $t->string('exchange_order_id')->nullable();
            $t->decimal('requested_quantity', 36, 18);
            $t->decimal('executed_quantity', 36, 18)->default(0);
            $t->decimal('cumulative_quote_quantity', 36, 18)->default(0);
            $t->decimal('average_fill_price', 36, 18)->nullable();
            $t->decimal('limit_price', 36, 18)->nullable();
            $t->decimal('stop_price', 36, 18)->nullable();
            $t->string('status');
            $t->boolean('submission_ambiguous')->default(false);
            $t->text('reject_reason')->nullable();
            $t->timestamp('submitted_at')->nullable();
            $t->timestamp('acknowledged_at')->nullable();
            $t->timestamp('last_exchange_update_at')->nullable();
            $t->timestamp('terminal_at')->nullable();
            $t->unsignedBigInteger('engine_version')->default(0); // monotonic mirror guard
            $t->timestamps();
            $t->unique(['bot_instance_id', 'client_order_id']);
            $t->unique(['bot_instance_id', 'client_intent_id']);
            $t->index(['bot_instance_id', 'status']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('orders');
    }
};
