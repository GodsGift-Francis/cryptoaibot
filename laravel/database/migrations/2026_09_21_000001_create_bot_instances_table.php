<?php
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;
return new class extends Migration { public function up(): void { Schema::create('bot_instances', function(Blueprint $t){$t->id();$t->string('name')->unique();$t->string('mode')->default('PAPER');$t->boolean('enabled')->default(true);$t->boolean('emergency_stop')->default(false);$t->string('status')->default('OFFLINE');$t->string('symbol')->default('BTC/USDT');$t->string('timeframe')->default('1h');$t->timestamp('last_heartbeat_at')->nullable();$t->text('last_error')->nullable();$t->json('metadata')->nullable();$t->timestamps();}); }
public function down(): void { Schema::dropIfExists('bot_instances'); }};
