<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Event;

use WissenBeratung\LicenseClient\Contracts\EventDispatcher;

/** Default: Events verpuffen. Der Dienst hängt sein Monitoring selbst dran. */
final class NullEventDispatcher implements EventDispatcher
{
    public function dispatch(object $event): void {}
}
