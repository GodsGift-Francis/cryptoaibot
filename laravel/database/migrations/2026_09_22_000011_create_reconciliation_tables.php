<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('reconciliation_runs', function (Blueprint $t) {
            $t->id();
            $t->foreignId('bot_instance_id')->constrained()->cascadeOnDelete();
            $t->string('run_key')->unique();
            $t->string('reason');
            $t->string('status');
            $t->text('detail')->nullable();
            $t->json('summary')->nullable();
            $t->timestamp('started_at');
            $t->timestamp('finished_at')->nullable();
            $t->timestamps();
            $t->index(['bot_instance_id', 'started_at']);
        });
        Schema::create('reconciliation_mismatches', function (Blueprint $t) {
            $t->id();
            $t->foreignId('reconciliation_run_id')->constrained()->cascadeOnDelete();
            $t->string('kind');
            $t->string('severity');
            $t->string('client_order_id')->nullable();
            $t->json('detail')->nullable();
            $t->boolean('resolved')->default(false);
            $t->timestamps();
            $t->index(['severity', 'resolved']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('reconciliation_mismatches');
        Schema::dropIfExists('reconciliation_runs');
    }
};
