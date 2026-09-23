<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('positions', function (Blueprint $t) {
            $t->id();
            $t->foreignId('bot_instance_id')->constrained()->cascadeOnDelete();
            $t->string('symbol');
            $t->string('side')->default('LONG');
            $t->decimal('quantity', 36, 18)->default(0);
            $t->decimal('average_entry_price', 36, 18)->default(0);
            $t->decimal('realized_pnl', 36, 18)->default(0);
            $t->decimal('unrealized_pnl', 36, 18)->default(0);
            $t->decimal('last_mark_price', 36, 18)->nullable();
            $t->decimal('stop_price', 36, 18)->nullable();
            $t->timestamp('opened_at')->nullable();
            $t->timestamp('engine_updated_at')->nullable(); // last-writer-wins guard
            $t->timestamps();
            $t->unique(['bot_instance_id', 'symbol']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('positions');
    }
};
