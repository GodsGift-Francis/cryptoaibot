<?php
use Illuminate\Database\Migrations\Migration;use Illuminate\Database\Schema\Blueprint;use Illuminate\Support\Facades\Schema;
return new class extends Migration {public function up():void{Schema::create('bot_events',function(Blueprint $t){$t->id();$t->foreignId('bot_instance_id')->constrained()->cascadeOnDelete();$t->string('event_type');$t->text('message')->nullable();$t->json('payload')->nullable();$t->timestamp('occurred_at')->useCurrent();$t->timestamps();});}public function down():void{Schema::dropIfExists('bot_events');}};
