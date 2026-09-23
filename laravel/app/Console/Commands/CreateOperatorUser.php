<?php

namespace App\Console\Commands;

use App\Models\User;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\Validator;

/** Creates dashboard users. Passwords are prompted (never passed as CLI args) and hashed by the model cast. */
class CreateOperatorUser extends Command
{
    protected $signature = 'bot:create-user {email} {--name=} {--role=viewer : admin|operator|viewer}';

    protected $description = 'Create a dashboard user (password prompted securely).';

    public function handle(): int
    {
        $email = (string) $this->argument('email');
        $role = (string) $this->option('role');
        $password = (string) $this->secret('Password (min 12 characters)');
        $v = Validator::make(compact('email', 'role', 'password'), [
            'email' => ['required', 'email', 'unique:users,email'],
            'role' => ['required', 'in:'.implode(',', User::ROLES)],
            'password' => ['required', 'string', 'min:12'],
        ]);
        if ($v->fails()) {
            foreach ($v->errors()->all() as $e) {
                $this->error($e);
            }
            return self::FAILURE;
        }
        User::create(['name' => $this->option('name') ?: $email, 'email' => $email, 'role' => $role, 'password' => $password]);
        $this->info("Created {$role} user {$email}.");
        return self::SUCCESS;
    }
}
