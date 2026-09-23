<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        // Idempotency ledger for the internal engine API (X-Request-Id replay protection).
        Schema::create('internal_api_requests', function (Blueprint $t) {
            $t->id();
            $t->string('bot_instance');
            $t->string('request_id', 191);
            $t->string('endpoint');
            $t->unsignedSmallInteger('response_status');
            $t->json('response_body')->nullable();
            $t->timestamp('created_at')->useCurrent();
            $t->unique(['bot_instance', 'request_id']);
            $t->index('created_at');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('internal_api_requests');
    }
};
