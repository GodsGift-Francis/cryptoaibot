<?php

use Illuminate\Support\Facades\Schedule;

Schedule::command('bot:health')->everyMinute()->withoutOverlapping();
Schedule::command('bot:prune-internal-requests')->daily();
